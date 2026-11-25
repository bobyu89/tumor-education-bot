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
    python -X utf8 scripts/index_vault.py            # 同步（預設）：新增新段落、刪除舊版與停用單張的段落
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
sys.path.insert(0, str(ROOT / "backend"))
import departments  # noqa: E402  科別註冊表（只用標準函式庫）
import embedding  # noqa: E402  嵌入模型註冊表（與 rag.py 查詢共用）

VAULT_DIR = ROOT / "Obsidian_Vault" / "衛教知識庫"   # 血液腫瘤科單張；各科來源以 manifest 的 source_dir 為準
CHROMA_PATH = ROOT / "chroma_db"
COLLECTION_NAME = "patient_education"
REPORT_PATH = ROOT / "Obsidian_Vault" / "專案管理" / "索引報告.md"

# 這些章節不進向量庫
EXCLUDED_SECTIONS = {"參考資料", "護理指導評值"}

# 切段時避免產生的碎片長度（字元）
MIN_CHUNK = 40

# 每段的 token 上限（含主題前綴），依檢索模型而定（backend/embedding.py 的 MODELS）。
# 一律用模型真正的 tokenizer 量，不用字元數猜：中文的數字、英文與標點 token 密度差很多。
# 舊版用 400 字元切、模型只讀 128 token → 長段落有一半以上的內容從未進入向量。
SPEC = embedding.get_spec()
EMBED_MODEL = SPEC.name
MODEL_MAX_TOKENS = SPEC.chunk_tokens - (
    embedding.make_token_counter()(SPEC.passage_prefix) - 2 if SPEC.passage_prefix else 0)


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
    dept: str = ""


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
    # 短章節整段保留：例如一句「發燒請立即就醫」的「何時通知醫護人員」章節絕不能被丟掉。
    # （舊版把少於 MIN_CHUNK 字的整個章節直接捨棄；MIN_CHUNK 只該用在切割時避免碎片。）
    if not content:
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

    # 不丟任何文字：短於 MIN_CHUNK 的零頭先嘗試併入前一段，放不下就自成一段。
    # （舊版直接捨棄零頭，實測 ONC-23「年齡因素」與 ONC-35「內關穴位置」共 62 字從未進入知識庫。）
    out: list[str] = []
    buf = ""

    def flush(piece: str):
        if not piece:
            return
        if out and len(piece) < MIN_CHUNK and fits(f"{out[-1]}\n{piece}"):
            out[-1] = f"{out[-1]}\n{piece}"
        else:
            out.append(piece)

    for unit in split_units(content):
        cand = f"{buf}\n{unit}" if buf else unit
        if fits(cand):
            buf = cand
        else:
            flush(buf)
            buf = unit
    flush(buf)
    return out


# ── 嵌入函式：與 rag.py 查詢共用 backend/embedding.py，確保建庫與查詢是同一個模型 ──
def make_embedding_function():
    ef = embedding.make_embedding_function()
    print(f"嵌入模型：{ef.spec.name}（段落上限 {MODEL_MAX_TOKENS} tokens）")
    return ef


def get_token_counter():
    """回傳 count_tokens(text) -> int，用檢索模型真正的 tokenizer。"""
    return embedding.make_token_counter()


# ── 同步：讓向量庫與單張目前的狀態完全一致 ────────────────────────
def sync_collection(col, chunks: list[Chunk], batch_size: int = 32, embed=None) -> dict:
    """把集合同步成「剛好等於這次切出的 chunks」。

    v1 的增量模式只新增、從不刪除：單張改版後，舊版段落仍留在向量庫，可能被拿來回答。
    現在以 chunk ID（含單張代碼、版本、章節與內容）比對：
      - 這次沒有、集合裡有 → 刪除（單張改版的舊段落、已移除或 rag_index 改為 false 的單張）
      - 這次有、集合裡沒有 → 新增
    embed：段落嵌入函式（加段落前綴）；未提供時由集合自己的 embedding_function 處理（測試用）。
    """
    if not chunks:
        # 防呆：沒有任何可索引的單張時，不要把整個向量庫清空
        raise SystemExit("這次沒有切出任何 chunk（全部單張都被略過？）—— 為避免清空向量庫，停止同步")
    wanted = {c.id: c for c in chunks}
    existing = set(col.get(include=[])["ids"]) if col.count() else set()
    stale = sorted(existing - wanted.keys())
    new = [c for cid, c in wanted.items() if cid not in existing]

    for i in range(0, len(stale), batch_size):
        col.delete(ids=stale[i : i + batch_size])
    for i in range(0, len(new), batch_size):
        batch = new[i : i + batch_size]
        col.add(
            ids=[c.id for c in batch],
            documents=[c.text for c in batch],
            metadatas=[{
                "code": c.code, "topic": c.topic, "category": c.category,
                "section": c.section, "source": c.source,
                "doc_version": c.doc_version, "dept": c.dept,
            } for c in batch],
            **({"embeddings": embed([c.text for c in batch])} if embed else {}),
        )
        print(f"  嵌入 {min(i + batch_size, len(new))}/{len(new)}")
    return {"added": len(new), "removed": len(stale), "unchanged": len(wanted) - len(new)}


# ── 主流程 ────────────────────────────────────────────────────────
def build_chunks() -> tuple[list[Chunk], list[dict]]:
    """所有科別（knowledge/*/manifest.json）的單張切段。同一份單張可被多個科別引用，各自帶科別標記。"""
    count_tokens = get_token_counter()
    chunks: list[Chunk] = []
    files_report: list[dict] = []
    for dept in departments.load_all().values():
        c, r = _build_dept_chunks(dept.id, dept.source_dir, count_tokens)
        chunks += c
        files_report += r
    return chunks, files_report


def _build_dept_chunks(dept_id: str, source_dir: Path, count_tokens) -> tuple[list[Chunk], list[dict]]:
    chunks: list[Chunk] = []
    files_report: list[dict] = []

    md_files = sorted(source_dir.glob("*.md"))
    if not md_files:
        sys.exit(f"科別 {dept_id} 找不到任何 .md：{source_dir}")

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
                # ID 含版本：單張改版（即使文字未變）也會換新 ID，舊版由 sync_collection 刪除
                cid = hashlib.md5(
                    f"{dept_id}|{code}|{doc_version}|{sec_name}|{i}|{text}".encode("utf-8")
                ).hexdigest()
                chunks.append(
                    Chunk(
                        id=cid, text=embed_text, code=code, topic=topic,
                        category=category, section=sec_name,
                        source=source, doc_version=doc_version, dept=dept_id,
                    )
                )

        if len(chunks) == n_before:
            print(f"  ⚠ {dept_id}／{path.name}：rag_index 為 true 卻沒有切出任何段落，請檢查章節格式（## 標題）")
        files_report.append({
            "file": path.name,
            "dept": dept_id,
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

    rebuild = args.rebuild
    if not rebuild:
        try:
            built_with = (client.get_collection(COLLECTION_NAME).metadata or {}).get("embed_model", "舊版 MiniLM")
            if built_with != EMBED_MODEL:
                # 不同模型的向量不能混在同一個索引：整個重建
                print(f"索引是用 {built_with} 建立的，目前檢索模型是 {EMBED_MODEL} → 整個重建")
                rebuild = True
        except Exception:
            pass   # 還沒有集合：下面會建立
    if rebuild:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"已刪除舊集合 {COLLECTION_NAME}")
        except Exception:
            pass

    # 段落與查詢向量都明確由 ef 計算（各自加前綴），集合本身不掛嵌入函式
    col = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine", "embed_model": EMBED_MODEL},
    )

    result = sync_collection(col, chunks, embed=ef.embed_documents)
    print(f"新增 {result['added']}、刪除 {result['removed']}（舊版或已停用單張）、未變 {result['unchanged']} 個 chunks")

    print(f"\n集合 {COLLECTION_NAME} 現有 {col.count()} 個 chunks")
    write_report(files_report, total, "rebuild" if rebuild else "sync")
    print(f"報告 → {REPORT_PATH}")

    # 煙霧測試：確認排除章節真的沒進去、且檢索得到正確文件
    print("\n── 煙霧測試 ──")
    for q in ["化療後嘴巴破了怎麼辦", "一直拉肚子", "白血球低要注意什麼"]:
        r = col.query(query_embeddings=ef.embed_query([q]), n_results=2)
        hits = [f"{m['code']}／{m['section']}" for m in r["metadatas"][0]]
        print(f"  「{q}」→ {', '.join(hits)}")


if __name__ == "__main__":
    main()
