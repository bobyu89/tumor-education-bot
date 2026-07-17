"""
向量檢索：語意相似度（ChromaDB cosine）+ 相關度過濾。

改動（Phase 1）：
- 改用 backend/embedding.py 的共用嵌入層（torch → ONNX 自動 fallback），
  修好 Smart App Control 封鎖 torch 導致無法啟動的問題。
- metadata 鍵名對齊 scripts/index_vault.py：
  舊 index 用 source_file / disease_category；新 index 用 source / category / code / section。
  兩者都相容（get 時同時嘗試）。
"""
from embedding import make_embedding_function
import chromadb
from config import settings


class RAGRetriever:
    def __init__(self):
        self.client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)
        self.embedding_fn = make_embedding_function()
        try:
            self.collection = self.client.get_collection(
                name=settings.COLLECTION_NAME,
                embedding_function=self.embedding_fn,
            )
        except Exception:
            self.collection = self.client.get_or_create_collection(
                name=settings.COLLECTION_NAME,
                embedding_function=self.embedding_fn,
                metadata={"hnsw:space": "cosine"},
            )

    def query(self, query_text: str, n_results: int = None,
              category_filter: str = None) -> list[dict]:
        k = n_results or settings.RAG_TOP_K
        total = self.collection.count()
        if total == 0:
            return []

        k = min(k, total)
        # 新舊 metadata 皆支援分類過濾
        where = None
        if category_filter:
            where = {"category": category_filter}

        results = self.collection.query(
            query_texts=[query_text],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        output = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            if dist < 0.8:   # cosine distance < 0.8 = 相關
                output.append({
                    "content": doc,
                    # 新 index：source / code / category / section；舊 index：source_file / disease_category
                    "source": meta.get("source") or meta.get("source_file", "unknown"),
                    "code": meta.get("code", ""),
                    "section": meta.get("section", ""),
                    "category": meta.get("category") or meta.get("disease_category", ""),
                    "topic": meta.get("topic", ""),
                    "score": round(1 - dist, 3),
                })
        return output


retriever = RAGRetriever()
