"""
從 Obsidian_Vault/衛教知識庫/*.md 建立 ChromaDB 向量索引。

與舊版 scripts/indexer.py 的差異：
  1. 來源改為 Markdown（逐字轉換自 PDF，已清除頁首頁尾樣板），不再直接吃 PDF。
  2. category 取自 frontmatter，不再用關鍵字推斷器官系統
     （舊版對 13 份有 6 份誤判，見 Review 問題清單 #5）。
  3. 排除 `## 參考資料` 與 `## 護理指導評值` 兩個章節
     —— 英文文獻與考題不該被病患問句檢索到。
  4. 依 Markdown 的 `##` 章節邊界切割，而非固定字元數硬切，
     讓每個 chunk 是語意完整的一個衛教面向。
  5. chunk 附帶 section 欄位，回答時可標示「出自 ONC-17 的『預防與治療』」。

用法：
    python -X utf8 scripts/index_vault.py            # 增量（預設）
    python -X utf8 scripts/index_vault.py --rebuild  # 砍掉重建
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

# ── 設定 ──────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
VAULT_DIR = ROOT / "Obsidian_Vault" / "衛教知識庫"
CHROMA_PATH = ROOT / "chroma_db"
COLLECTION_NAME = "patient_education"
EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
REPORT_PATH = ROOT / "Obsidian_Vault" / "專案管理" / "索引報告.md"

# 這些章節不進向量庫
EXCLUDED_SECTIONS = {"參考資料", "護理指導評值"}

# chunk 大小上限（字元）。超過的章節會再依段落切分。
#
# ⚠️ 這個數字不是隨便選的：paraphrase-multilingual-MiniLM-L12-v2 的 max_seq_length
# 只有 128 tokens，超過的部分會被 tokenizer 靜默截斷。實測本知識庫的中文約
# 1.35 字/token，128 tokens ≈ 173 字。
#
# 舊版 indexer.py 用 400 字元切 ≈ 295 tokens → 每個長 chunk 有約 57% 的內容
# 從未進入向量、搜不到（但 document 仍存完整 400 字，一旦命中又會整段餵給 LLM）。
MAX_CHUNK = 170
MIN_CHUNK = 40

# 模型真實上限，用於索引後的驗證
MODEL_MAX_TOKENS = 128


# ── 資料結構 ──────────────────────────────────────────────────────
@dataclass
class Chunk:
    id: str
    text: str
    code: str
    topic: str
    category: str
    section: str
    source: str
    doc_version: str


# ── Frontmatter 解析（不依賴 PyYAML）────────────────────────────────
def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """回傳 (frontmatter dict, body)。只處理本專案用到的簡單 YAML 子集。"""
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end == -1:
        return {}, raw
    fm_text = raw[3:end]
    body = raw[end + 4 :]

    fm: dict = {}
    current_list_key: str | None = None
    for line in fm_text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        # list item
        if line.lstrip().startswith("- ") and current_list_key:
            fm[current_list_key].append(line.lstrip()[2:].strip())
            continue
        m = re.match(r"^(\w[\w_]*):\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val == "":
            fm[key] = []
            current_list_key = key
        else:
            current_list_key = None
            if val.lower() in ("true", "false"):
                fm[key] = val.lower() == "true"
            else:
                fm[key] = val.strip('"').strip("'")
    return fm, body


# ── 章節切割 ──────────────────────────────────────────────────────
def split_sections(body: str) -> list[tuple[str, str]]:
    """依 `## ` 切成 [(章節名, 內容), ...]。

    第一個 `##` 之前的內容（通常是 `> [!abstract]` 一句話重點）歸為「重點摘要」，
    刻意與文件自己的 `## 概述` 區隔 —— 否則會出現兩個同名的「概述」chunk。
    """
    sections: list[tuple[str, str]] = []
    current_name = "重點摘要"
    buf: list[str] = []

    for line in body.splitlines():
        if line.startswith("## "):
            if buf:
                sections.append((current_name, "\n".join(buf).strip()))
            current_name = line[3:].strip()
            buf = []
        elif line.startswith("# "):
            continue  # H1 標題已在 frontmatter 的 title
        else:
            buf.append(line)
    if buf:
        sections.append((current_name, "\n".join(buf).strip()))
    return [(n, c) for n, c in sections if c]


def clean_markdown(text: str) -> str:
    """把 Markdown 語法降級成給嵌入模型看的純文字。"""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)      # HTML 註解
    text = re.sub(r"%%.*?%%", "", text, flags=re.S)          # Obsidian 註解
    # callout 標頭整行移除（含「一句話重點」這類標題文字，那是排版標籤不是衛教內容）
    text = re.sub(r"^>\s?\[!\w+\][-+]?.*$\n?", "", text, flags=re.M)
    text = re.sub(r"^>\s?", "", text, flags=re.M)            # 引用符號
    text = re.sub(r"==(.+?)==", r"\1", text)                 # 高亮
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)             # 粗體
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)  # wikilink 別名
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)          # wikilink
    text = re.sub(r"^[-*]\s+", "", text, flags=re.M)         # 清單符號
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.M)     # 編號清單
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_section(name: str, content: str, fits) -> list[str]:
    """把章節切成「加上前綴後仍在模型 token 上限內」的片段。

    fits(text) -> bool 由呼叫端提供，實際用 tokenizer 量測，不用字元數猜。
    用字元數逼近 token 數在中文不可靠：數字、英文與標點的 token 密度差很多，
    實測同樣 170 字的 chunk，token 數可以從 120 到 165 不等。

    切割優先序：段落（空行）→ 句子（。！？；）→ 硬切。
    """
    content = clean_markdown(content)
    if len(content) < MIN_CHUNK:
        return []
    if fits(content):
        return [content]

    def split_units(text: str) -> list[str]:
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        units: list[str] = []
        for p in paras:
            if fits(p):
                units.append(p)
                continue
            # 段落自己就超長 → 依句尾標點再切
            sents = [s for s in re.split(r"(?<=[。！？；])", p) if s.strip()]
            for s in sents:
                if fits(s):
                    units.append(s)
                else:
                    # 單句超長（罕見）→ 二分硬切直到塞得下
                    stack = [s]
                    while stack:
                        cur = stack.pop()
                        if fits(cur) or len(cur) <= MIN_CHUNK:
                            units.append(cur)
                        else:
                            mid = len(cur) // 2
                            stack.extend([cur[mid:], cur[:mid]])
        return units

    out: list[str] = []
    buf = ""
    for unit in split_units(content):
        cand = f"{buf}\n{unit}" if buf else unit
        if fits(cand):
            buf = cand
        else:
            if len(buf) >= MIN_CHUNK:
                out.append(buf)
            buf = unit
    if len(buf) >= MIN_CHUNK:
        out.append(buf)
    return out


# ── 嵌入函式 ──────────────────────────────────────────────────────
#
# 優先用 sentence-transformers（架構書指定的做法）。
# 若 torch 無法載入（例如 Windows 11 的 Smart App Control 會封鎖 torch 未簽章的
# DLL，拋 WinError 4551），改用 onnxruntime 跑同一個模型的 ONNX 權重。
# 兩條路徑用的是相同的權重與相同的 mean-pooling，產生的向量可互換。

ONNX_DIR = ROOT / "models" / "paraphrase-multilingual-MiniLM-L12-v2"
ONNX_REPO = "https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/resolve/main"


class OnnxMiniLMEmbedder:
    """用 onnxruntime 重現 SentenceTransformer(paraphrase-multilingual-MiniLM-L12-v2)。

    pooling = mean（依 attention_mask 加權），與該模型 1_Pooling/config.json 一致；
    不做 L2 normalize，與 chromadb SentenceTransformerEmbeddingFunction 的預設一致。
    集合使用 cosine 距離，正規化與否不影響排序。
    """

    def __init__(self) -> None:
        import urllib.request
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.np = np
        ONNX_DIR.mkdir(parents=True, exist_ok=True)
        for remote, local in [("tokenizer.json", "tokenizer.json"),
                              ("onnx/model.onnx", "model.onnx")]:
            dest = ONNX_DIR / local
            if not dest.exists():
                print(f"  下載 {local} …")
                urllib.request.urlretrieve(f"{ONNX_REPO}/{remote}?download=true", dest)

        self.tok = Tokenizer.from_file(str(ONNX_DIR / "tokenizer.json"))
        self.tok.enable_truncation(max_length=MODEL_MAX_TOKENS)
        self.tok.enable_padding(pad_id=1, pad_token="<pad>")  # XLM-R pad id = 1
        self.sess = ort.InferenceSession(
            str(ONNX_DIR / "model.onnx"), providers=["CPUExecutionProvider"]
        )

    def name(self) -> str:                      # chromadb EmbeddingFunction 介面
        return "onnx-paraphrase-multilingual-MiniLM-L12-v2"

    # chromadb >= 1.5 除了 __call__ 還會呼叫這兩個。
    # 本模型 query 與 document 用同一種編碼（不像 e5 需要 query:/passage: 前綴），
    # 所以兩者都直接轉呼叫 __call__。
    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def __call__(self, input: list[str]) -> list[list[float]]:
        np = self.np
        encs = self.tok.encode_batch(input)
        ids = np.array([e.ids for e in encs], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
        out = self.sess.run(
            ["last_hidden_state"],
            {"input_ids": ids, "attention_mask": mask,
             "token_type_ids": np.zeros_like(ids)},
        )[0]
        # mean pooling，只算真實 token
        m = mask[..., None].astype(np.float32)
        summed = (out * m).sum(axis=1)
        counts = np.clip(m.sum(axis=1), 1e-9, None)
        return (summed / counts).tolist()


def make_embedding_function():
    try:
        from chromadb.utils import embedding_functions
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
        print(f"嵌入後端：sentence-transformers（{EMBED_MODEL}）")
        return ef
    except Exception as e:
        print(f"嵌入後端：改用 ONNX —— sentence-transformers 無法載入")
        print(f"  原因：{type(e).__name__}: {str(e)[:120]}")
        ef = OnnxMiniLMEmbedder()
        print(f"  已載入 {ef.name()}")
        return ef


def get_token_counter():
    """回傳 count_tokens(text) -> int，用真正的模型 tokenizer。"""
    import urllib.request
    from tokenizers import Tokenizer

    ONNX_DIR.mkdir(parents=True, exist_ok=True)
    tok_path = ONNX_DIR / "tokenizer.json"
    if not tok_path.exists():
        print("  下載 tokenizer.json（切割需要精確 token 量測）…")
        urllib.request.urlretrieve(f"{ONNX_REPO}/tokenizer.json?download=true", tok_path)

    tok = Tokenizer.from_file(str(tok_path))
    tok.no_truncation()   # 要量真實長度，不能讓它先截斷
    tok.no_padding()
    return lambda text: len(tok.encode(text).ids)


# ── 主流程 ────────────────────────────────────────────────────────
def build_chunks() -> tuple[list[Chunk], list[dict]]:
    count_tokens = get_token_counter()
    chunks: list[Chunk] = []
    files_report: list[dict] = []

    md_files = sorted(VAULT_DIR.glob("*.md"))
    if not md_files:
        sys.exit(f"找不到任何 .md：{VAULT_DIR}")

    for path in md_files:
        raw = path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(raw)

        if not fm.get("rag_index", False):
            files_report.append({"file": path.name, "chunks": 0, "skipped": "rag_index 非 true"})
            continue

        code = fm.get("code", path.stem.split()[0])
        topic = fm.get("topic", "")
        category = fm.get("category", "")
        source = fm.get("source", "")
        doc_version = fm.get("doc_version", "")

        if not category:
            sys.exit(f"{path.name} 缺 category —— 拒絕索引，請先補 frontmatter")

        n_before = len(chunks)
        excluded_hits: list[str] = []

        for sec_name, sec_body in split_sections(body):
            if sec_name in EXCLUDED_SECTIONS:
                excluded_hits.append(sec_name)
                continue

            prefix = f"【{topic}｜{sec_name}】\n"
            # fits() 判斷的是「加上前綴後」的完整 embed_text，前綴本身也佔 token
            budget = MODEL_MAX_TOKENS - count_tokens(prefix)
            if budget < MIN_CHUNK // 2:
                sys.exit(f"{path.name} 的章節標題「{sec_name}」過長，前綴就吃掉 "
                         f"{count_tokens(prefix)}/{MODEL_MAX_TOKENS} tokens")
            fits = lambda t, b=budget: count_tokens(t) <= b

            for i, text in enumerate(chunk_section(sec_name, sec_body, fits)):
                # 前置 topic 與章節名，讓嵌入向量帶有主題語境
                embed_text = prefix + text
                cid = hashlib.md5(
                    f"{code}|{sec_name}|{i}|{text}".encode("utf-8")
                ).hexdigest()
                chunks.append(
                    Chunk(
                        id=cid, text=embed_text, code=code, topic=topic,
                        category=category, section=sec_name,
                        source=source, doc_version=doc_version,
                    )
                )

        files_report.append({
            "file": path.name,
            "code": code,
            "topic": topic,
            "category": category,
            "chunks": len(chunks) - n_before,
            "excluded_sections": excluded_hits,
        })

    return chunks, files_report


def write_report(files_report: list[dict], total: int, mode: str) -> None:
    lines = [
        "---",
        "title: 索引報告",
        "rag_index: false",
        "tags:",
        "  - 專案管理/索引",
        "---",
        "",
        "# 索引報告",
        "",
        f"> [!success] 完成",
        f"> 模式：`{mode}`　來源：`Obsidian_Vault/衛教知識庫/`　"
        f"集合：`{COLLECTION_NAME}`　嵌入模型：`{EMBED_MODEL}`",
        f"> **{len(files_report)} 份文件 → {total} 個 chunks**",
        "",
        "| 編號 | 主題 | 分類 | Chunks | 已排除章節 |",
        "|---|---|---|---:|---|",
    ]
    for r in files_report:
        if r.get("skipped"):
            lines.append(f"| — | {r['file']} | — | 0 | 跳過：{r['skipped']} |")
        else:
            ex = "、".join(r["excluded_sections"]) or "—"
            lines.append(
                f"| {r['code']} | [[{Path(r['file']).stem}]] | {r['category']} "
                f"| {r['chunks']} | {ex} |"
            )
    lines += [
        f"| | | **合計** | **{total}** | |",
        "",
        "## 與舊版索引的差異",
        "",
        "| | 舊版 `indexer.py` | 本版 `index_vault.py` |",
        "|---|---|---|",
        "| 來源 | 直接讀 PDF | Markdown（已清除頁首頁尾樣板） |",
        "| 分類 | 關鍵字推斷器官系統（**6/13 誤判**） | 取自 frontmatter，全部「腫瘤」 |",
        "| 切割 | 固定 400 字元硬切 | 依 `##` 章節邊界，語意完整 |",
        "| 參考資料 | ✅ 一起索引 | ❌ 排除 |",
        "| 護理指導評值（考題） | ✅ 一起索引 | ❌ 排除 |",
        "| chunk metadata | file / category / topic | 多了 `section`、`doc_version` |",
        "",
        "> [!note] chunk 數變少是正常的",
        "> 舊版 108 chunks 含英文參考文獻與是非題／選擇題。那些被病患問句檢索到只會是雜訊。",
        "",
        "詳見 [[Review 問題清單#5. 索引分類有 46% 是錯的]]。",
    ]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="刪除既有集合後重建")
    ap.add_argument("--dry-run", action="store_true", help="只切割不嵌入，印出統計")
    args = ap.parse_args()

    chunks, files_report = build_chunks()
    total = len(chunks)
    print(f"切出 {total} 個 chunks，來自 {len(files_report)} 份文件")

    if args.dry_run:
        for r in files_report:
            print(f"  {r.get('code','—'):<8} {r['chunks']:>3} chunks  "
                  f"排除：{'、'.join(r.get('excluded_sections', [])) or '—'}")
        write_report(files_report, total, "dry-run")
        print(f"\n報告 → {REPORT_PATH}")
        return

    import chromadb

    ef = make_embedding_function()

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))

    if args.rebuild:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"已刪除舊集合 {COLLECTION_NAME}")
        except Exception:
            pass

    col = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    existing = set(col.get(include=[])["ids"]) if col.count() else set()
    new = [c for c in chunks if c.id not in existing]
    print(f"既有 {len(existing)} 個 chunks，本次新增 {len(new)} 個")

    if new:
        B = 32
        for i in range(0, len(new), B):
            batch = new[i : i + B]
            col.add(
                ids=[c.id for c in batch],
                documents=[c.text for c in batch],
                metadatas=[{
                    "code": c.code, "topic": c.topic, "category": c.category,
                    "section": c.section, "source": c.source,
                    "doc_version": c.doc_version,
                } for c in batch],
            )
            print(f"  嵌入 {min(i+B, len(new))}/{len(new)}")

    print(f"\n集合 {COLLECTION_NAME} 現有 {col.count()} 個 chunks")
    write_report(files_report, total, "rebuild" if args.rebuild else "incremental")
    print(f"報告 → {REPORT_PATH}")

    # 煙霧測試：確認排除章節真的沒進去、且檢索得到正確文件
    print("\n── 煙霧測試 ──")
    for q in ["化療後嘴巴破了怎麼辦", "一直拉肚子", "白血球低要注意什麼"]:
        r = col.query(query_texts=[q], n_results=2)
        hits = [f"{m['code']}／{m['section']}" for m in r["metadatas"][0]]
        print(f"  「{q}」→ {', '.join(hits)}")


if __name__ == "__main__":
    main()
