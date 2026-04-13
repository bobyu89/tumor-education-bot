"""
Oncology Patient Education RAG Indexer
Supports PDF / DOCX, outputs to local ChromaDB
"""
import os
import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime

# Fix Windows stdout encoding
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import fitz  # PyMuPDF
from langchain_text_splitters import RecursiveCharacterTextSplitter
import chromadb

from utils import clean_text, infer_topic_from_filename

# -- Settings --
DOCS_DIR = Path(__file__).parent.parent / "docs"
CHROMA_DIR = Path(__file__).parent.parent / "chroma_db"
COLLECTION_NAME = "patient_education"
CHUNK_SIZE = 400
CHUNK_OVERLAP = 80
USE_LOCAL_EMBEDDING = True

DISEASE_CATEGORIES = {
    "心臟": ["心臟", "心衰", "心肌梗塞", "高血壓", "心律不整", "冠狀動脈"],
    "呼吸": ["肺", "氣喘", "COPD", "呼吸", "支氣管"],
    "泌尿": ["腎", "透析", "洗腎", "泌尿", "膀胱"],
    "腫瘤": ["癌", "腫瘤", "化療", "放療", "標靶", "化學治療", "白血球", "血小板", "紅血球", "ONC"],
    "代謝": ["糖尿病", "高血脂", "甲狀腺", "肥胖"],
    "骨科": ["骨折", "關節", "脊椎", "復健"],
}


def extract_text_from_pdf(filepath: Path) -> str:
    doc = fitz.open(str(filepath))
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text


def extract_text_from_docx(filepath: Path) -> str:
    from docx import Document
    doc = Document(str(filepath))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


def infer_disease_category(text: str, filename: str) -> str:
    combined = text[:500] + filename
    for category, keywords in DISEASE_CATEGORIES.items():
        if any(kw in combined for kw in keywords):
            return category
    return "其他"


def get_embedding_function():
    if USE_LOCAL_EMBEDDING:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        return SentenceTransformerEmbeddingFunction(
            model_name="paraphrase-multilingual-MiniLM-L12-v2"
        )
    else:
        from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
        return OpenAIEmbeddingFunction(
            api_key=os.environ["OPENAI_API_KEY"],
            model_name="text-embedding-3-small"
        )


def index_documents():
    print(f"[INFO] Docs dir: {DOCS_DIR.resolve()}")
    print(f"[INFO] ChromaDB: {CHROMA_DIR.resolve()}")

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    print("[INFO] Loading embedding model (first run will download ~400MB)...")
    embedding_fn = get_embedding_function()

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"}
    )

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "\u3002", "\uff0c", " "]
    )

    report = {"indexed": [], "skipped": [], "errors": []}
    existing_ids = set(collection.get()["ids"])
    print(f"[INFO] Existing chunks: {len(existing_ids)} (incremental mode)\n")

    for filepath in sorted(DOCS_DIR.iterdir()):
        suffix = filepath.suffix.lower()
        if suffix not in [".pdf", ".docx"]:
            report["skipped"].append(str(filepath.name))
            continue

        try:
            if suffix == ".pdf":
                raw_text = extract_text_from_pdf(filepath)
                doc_type = "pdf"
            else:
                raw_text = extract_text_from_docx(filepath)
                doc_type = "docx"

            text = clean_text(raw_text)
            if len(text) < 50:
                report["skipped"].append(f"{filepath.name} (too short)")
                continue

            chunks = splitter.split_text(text)
            disease_cat = infer_disease_category(text, filepath.stem)
            topic = infer_topic_from_filename(filepath.name)

            new_chunks, new_ids, new_metas = [], [], []
            for i, chunk in enumerate(chunks):
                chunk_id = hashlib.md5(f"{filepath.name}_{i}".encode()).hexdigest()
                if chunk_id in existing_ids:
                    continue
                new_chunks.append(chunk)
                new_ids.append(chunk_id)
                new_metas.append({
                    "source_file": filepath.name,
                    "doc_type": doc_type,
                    "topic": topic,
                    "disease_category": disease_cat,
                    "language": "zh-TW",
                    "chunk_index": i,
                    "indexed_at": datetime.now().isoformat()
                })

            if new_chunks:
                collection.add(documents=new_chunks, ids=new_ids, metadatas=new_metas)

            report["indexed"].append({
                "file": filepath.name,
                "chunks": len(new_chunks),
                "category": disease_cat,
                "topic": topic
            })
            print(f"  [OK] {filepath.name} -> {len(new_chunks)} chunks [{disease_cat}/{topic}]")

        except Exception as e:
            report["errors"].append({"file": filepath.name, "error": str(e)})
            print(f"  [ERR] {filepath.name}: {e}")

    report_path = Path(__file__).parent.parent / "index_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    total = collection.count()
    new_total = sum(r['chunks'] for r in report['indexed'])
    print(f"\n{'='*50}")
    print(f"[DONE] Total chunks in DB: {total}")
    print(f"       New chunks added:   {new_total}")
    print(f"       Skipped files:      {len(report['skipped'])}")
    print(f"       Errors:             {len(report['errors'])}")
    print(f"       Report saved:       {report_path}")

    if total > 0:
        print("\n[TEST] Verification query...")
        results = collection.query(
            query_texts=["化療副作用處理"],
            n_results=min(3, total)
        )
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i]
            print(f"  [{i+1}] {meta.get('source_file','?')}")
            print(f"       {doc[:80].strip()}...")


if __name__ == "__main__":
    index_documents()
