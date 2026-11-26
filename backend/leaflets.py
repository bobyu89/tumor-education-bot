"""
單張管理：管理者上傳 → 轉換預覽 → 上架／停用／退回舊版。

- 上架中的單張寫在 data/leaflets/<科別>/<代碼>.md（覆蓋層）：同代碼時取代 repo 裡的原始單張，
  frontmatter rag_index: false 表示停用。scripts/index_vault.py 啟動時也讀這裡，重啟後狀態不變。
- 每一版（含草稿）都記在 leaflet_versions，原始上傳檔存在 data/leaflets/_files/，可下載核對。
- 上架、停用、退回後立刻同步向量索引（舊版段落刪除），下一則病人提問就用新版。

單張內容照院內核可版本：系統只做格式轉換（Word、PDF → Markdown），不改寫。
預覽會比對「原檔轉出的文字」與「最後要上架的文字」，管理者在預覽裡調整了章節標記以外的內容時會提醒。
PDF 轉文字常有斷行與表格錯亂，必須勾選「已逐段核對」才能上架。
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import io
import re
import sys
import threading
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import db
import departments

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import index_vault  # noqa: E402

MAX_BYTES = 10 * 1024 * 1024
FORMATS = {".md": "md", ".markdown": "md", ".txt": "md", ".docx": "docx", ".pdf": "pdf"}
# 與 quality._PAREN_CODE 一致：回答中的（代碼）才偵測得到
CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}-\d{1,4}$")
META_FIELDS = ("code", "title", "topic", "category", "doc_version", "source")

_lock = threading.Lock()   # 上架／停用／退回與索引同步一次只做一件


class LeafletError(ValueError):
    """管理者可以修正的問題（回 400）。"""


# ── 存放位置（跟著 db.DB_PATH，測試換資料庫時一起換）──────────────
def store_dir() -> Path:
    return db.DB_PATH.parent / "leaflets"


def _overlay_path(dept: str, code: str) -> Path:
    return store_dir() / dept / f"{code}.md"


def _original_path(version_id: int, fmt: str) -> Path:
    return store_dir() / "_files" / f"{version_id}.{fmt}"


# ── 格式轉換 ──────────────────────────────────────────────────────
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_to_markdown(data: bytes) -> str:
    """Word → Markdown（只用標準函式庫）。標題樣式 → ## 章節，清單 → 「- 」，表格每列一行。
    全文只有一個最上層標題時當成單張標題（#）。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        doc = ET.fromstring(zf.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise LeafletError(f"無法讀取 Word 檔（請確認是 .docx，不是舊版 .doc）：{e}") from e

    style_level: dict[str, int] = {}
    if "word/styles.xml" in zf.namelist():
        for st in ET.fromstring(zf.read("word/styles.xml")).iter(f"{_W}style"):
            sid = st.get(f"{_W}styleId", "")
            name = (st.find(f"{_W}name").get(f"{_W}val", "") if st.find(f"{_W}name") is not None else "").lower()
            lvl = st.find(f"{_W}pPr/{_W}outlineLvl")
            m = re.search(r"(?:heading|標題)\s*(\d)", name)
            if lvl is not None:
                style_level[sid] = int(lvl.get(f"{_W}val", "9"))
            elif m:
                style_level[sid] = int(m.group(1)) - 1
            elif name == "title":
                style_level[sid] = -1

    def para_text(p) -> str:
        out = []
        for node in p.iter():
            if node.tag == f"{_W}t":
                out.append(node.text or "")
            elif node.tag == f"{_W}tab":
                out.append(" ")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                out.append("\n")
        return "".join(out).strip()

    items: list[tuple[str, int | None, str]] = []   # (種類, 標題層級, 文字)
    body = doc.find(f"{_W}body")
    for el in list(body) if body is not None else []:
        if el.tag == f"{_W}p":
            text = para_text(el)
            if not text:
                continue
            ppr = el.find(f"{_W}pPr")
            level = None
            is_list = False
            if ppr is not None:
                ps = ppr.find(f"{_W}pStyle")
                if ps is not None:
                    level = style_level.get(ps.get(f"{_W}val", ""))
                ol = ppr.find(f"{_W}outlineLvl")
                if ol is not None:
                    level = int(ol.get(f"{_W}val", "9"))
                is_list = ppr.find(f"{_W}numPr") is not None
            if level is not None and level < 9:
                items.append(("h", level, text))
            else:
                items.append(("li" if is_list else "p", None, text))
        elif el.tag == f"{_W}tbl":
            for tr in el.iter(f"{_W}tr"):
                cells = [" ".join(para_text(p) for p in tc.iter(f"{_W}p")).strip() for tc in tr.iter(f"{_W}tc")]
                cells = [c for c in cells if c]
                if cells:
                    items.append(("p", None, "｜".join(cells)))

    levels = sorted({lv for kind, lv, _ in items if kind == "h"})
    title_level = section_level = None
    if levels:
        top = levels[0]
        tops = [i for i, (k, lv, _) in enumerate(items) if k == "h" and lv == top]
        if len(tops) == 1 and len(levels) > 1:
            title_level, section_level = top, levels[1]
        else:
            section_level = top

    lines: list[str] = []
    for kind, lv, text in items:
        text = text.replace("\n", "  \n")
        if kind == "h" and lv == title_level:
            lines += [f"# {text}", ""]
        elif kind == "h" and lv == section_level:
            lines += [f"## {text}", ""]
        elif kind == "li":
            lines.append(f"- {text}")
        else:
            if lines and lines[-1].startswith("- "):
                lines.append("")
            lines += [text, ""]
    return "\n".join(lines).strip() + "\n"


_BULLET = re.compile(r"^([•●○■□◆◇▪\-\*‧・]|\(?[0-9０-９]{1,2}[\.、\)）]|[（(]?[一二三四五六七八九十]{1,3}[、\.）)])")
_SENT_END = "。！？：；」）!?:;"
_PAGE_NO = re.compile(r"^(第?\s*\d+\s*(頁|/\s*\d+)?|\d+\s*/\s*\d+|page\s*\d+)$", re.I)


def pdf_to_markdown(data: bytes) -> str:
    """PDF → 文字段落。去掉每頁重複的頁首頁尾與頁碼，把句子中間的斷行接回去。
    章節標題無法可靠判斷，一律不加 ##：管理者在預覽裡逐段核對時補上。"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except ImportError as e:
        raise LeafletError("伺服器沒有安裝 pypdf，無法讀取 PDF") from e
    except Exception as e:   # pypdf 對損毀檔會丟各種例外
        raise LeafletError(f"無法讀取 PDF：{type(e).__name__}: {e}") from e
    if not any(p.strip() for p in pages):
        raise LeafletError("PDF 裡沒有文字（可能是掃描圖檔），請改上傳 Word 或文字版 PDF")

    page_lines = [[ln.strip() for ln in p.splitlines()] for p in pages]
    repeated: set[str] = set()
    if len(pages) >= 3:   # 出現在六成以上頁面的同一行 = 頁首頁尾
        counts: dict[str, int] = {}
        for lines in page_lines:
            for ln in set(lines):
                if ln:
                    counts[ln] = counts.get(ln, 0) + 1
        repeated = {ln for ln, n in counts.items() if n >= 0.6 * len(pages)}

    paras: list[str] = []
    cur = ""
    for lines in page_lines:
        for ln in lines:
            if not ln or ln in repeated or _PAGE_NO.match(ln):
                if not ln and cur:
                    paras.append(cur); cur = ""
                continue
            if cur and (_BULLET.match(ln) or cur[-1] in _SENT_END):
                paras.append(cur); cur = ln
            elif cur:
                sep = " " if re.match(r"[A-Za-z0-9]", ln[0]) and re.match(r"[A-Za-z0-9,.]", cur[-1]) else ""
                cur += sep + ln
            else:
                cur = ln
    if cur:
        paras.append(cur)
    return "\n\n".join(paras) + "\n"


def convert(filename: str, data: bytes) -> tuple[str, dict, str]:
    """回傳 (格式, 檔案內 frontmatter, 內文 Markdown)。"""
    fmt = FORMATS.get(Path(filename).suffix.lower())
    if not fmt:
        raise LeafletError("只接受 Word（.docx）、Markdown（.md）或 PDF")
    if len(data) > MAX_BYTES:
        raise LeafletError("檔案超過 10 MB")
    if fmt == "md":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise LeafletError("Markdown 檔需為 UTF-8 編碼") from e
        fm, body = index_vault.parse_frontmatter(text)
        return fmt, fm, body.strip() + "\n"
    if fmt == "docx":
        return fmt, {}, docx_to_markdown(data)
    return fmt, {}, pdf_to_markdown(data)


# ── 內文比對：只允許調整格式，不改內容 ───────────────────────────
_FORMAT_CHARS = re.compile(r"[\s#*>\-_=`|｜]+")


def _normalized(md: str) -> str:
    return _FORMAT_CHARS.sub("", md)


def text_changes(original: str, final: str) -> dict:
    """原檔轉出的文字 vs 要上架的文字，忽略空白與 Markdown 符號後的增刪字數。"""
    a, b = _normalized(original), _normalized(final)
    if a == b:
        return {"added": 0, "removed": 0, "samples": []}
    added = removed = 0
    samples = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        removed += i2 - i1
        added += j2 - j1
        if len(samples) < 5:
            samples.append({"removed": a[i1:i2][:40], "added": b[j1:j2][:40]})
    return {"added": added, "removed": removed, "samples": samples}


# ── frontmatter ───────────────────────────────────────────────────
def compose(meta: dict, body: str, rag_index: bool = True) -> str:
    lines = ["---"]
    for k in META_FIELDS:
        v = str(meta.get(k) or "").replace("\n", " ").strip()
        if v:
            lines.append(f"{k}: {v}")
    lines += [f"rag_index: {'true' if rag_index else 'false'}", "---", ""]
    return "\n".join(lines) + body.strip() + "\n"


def _clean_meta(meta: dict) -> dict:
    out = {k: str(meta.get(k) or "").replace("\n", " ").strip() for k in META_FIELDS}
    out["code"] = out["code"].upper()
    if not out["topic"]:
        out["topic"] = out["title"]
    return out


# ── 目前生效的單張 ───────────────────────────────────────────────
def _dept(dept_id: str):
    if not departments.exists(dept_id):
        raise LeafletError(f"未知的科別：{dept_id}")
    return departments.get(dept_id)


def _builtin(dept_id: str) -> dict[str, tuple[dict, str, str]]:
    """repo 裡的原始單張：{代碼: (frontmatter, 內文, 檔名)}。"""
    out = {}
    for path, fm, body, _ in index_vault.collect_documents(_dept(dept_id).source_dir, None):
        out[fm.get("code") or path.stem.split()[0]] = (fm, body, path.name)
    return out


def current(dept_id: str, code: str) -> dict | None:
    """這個代碼目前生效的版本（覆蓋層優先），沒有則 None。"""
    p = _overlay_path(dept_id, code)
    if p.exists():
        fm, body = index_vault.parse_frontmatter(p.read_text(encoding="utf-8"))
        return {"fm": fm, "body": body, "origin": "uploaded"}
    b = _builtin(dept_id).get(code)
    if b:
        return {"fm": b[0], "body": b[1], "origin": "builtin", "file": b[2]}
    return None


def list_leaflets(dept_id: str, chunk_counts: dict[str, int] | None = None) -> list[dict]:
    builtin = _builtin(dept_id)
    codes = set(builtin)
    od = store_dir() / dept_id
    if od.is_dir():
        codes |= {p.stem for p in od.glob("*.md")}
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT code, id, status, published_by, published_at FROM leaflet_versions "
            "WHERE dept=? AND status IN ('published','retired') ORDER BY id", (dept_id,)).fetchall()
        drafts = {r["code"]: r["n"] for r in conn.execute(
            "SELECT code, COUNT(*) n FROM leaflet_versions WHERE dept=? AND status='draft' GROUP BY code",
            (dept_id,))}
    latest = {r["code"]: dict(r) for r in rows}
    out = []
    for code in sorted(codes):
        cur = current(dept_id, code)
        fm = cur["fm"]
        row = latest.get(code)
        out.append({
            "code": code, "title": fm.get("title", ""), "topic": fm.get("topic", ""),
            "category": fm.get("category", ""), "doc_version": fm.get("doc_version", ""),
            "active": bool(fm.get("rag_index", False)),
            "origin": cur["origin"],
            "version_id": row["id"] if row and cur["origin"] == "uploaded" else None,
            "updated_by": row["published_by"] if row and cur["origin"] == "uploaded" else None,
            "updated_at": row["published_at"] if row and cur["origin"] == "uploaded" else None,
            "has_builtin": code in builtin,
            "chunks": (chunk_counts or {}).get(code),
            "drafts": drafts.get(code, 0),
        })
    return out


# ── 預覽 ──────────────────────────────────────────────────────────
_counter = None


def _count_tokens(text: str) -> int:
    global _counter
    if _counter is None:
        _counter = index_vault.get_token_counter()
    return _counter(text)


def preview(dept_id: str, meta: dict, body: str, extracted: str, fmt: str, reviewed: bool) -> dict:
    """章節、段落與阻擋上架的問題（errors）／提醒（warnings）。"""
    errors, warnings = [], []
    if not CODE_RE.match(meta["code"]):
        errors.append("單張代碼格式需為「英文字母開頭＋連字號＋數字」，例如 ONC-41、CV-01")
    for k, label in (("title", "標題"), ("category", "分類"), ("doc_version", "版本")):
        if not meta[k]:
            errors.append(f"請填寫{label}")
    cur = current(dept_id, meta["code"]) if CODE_RE.match(meta["code"]) else None
    if cur and cur["fm"].get("rag_index") and cur["fm"].get("doc_version", "") == meta["doc_version"]:
        errors.append(f"版本「{meta['doc_version']}」與目前上架中的版本相同；改版請更新版本欄位")
    if not cur:
        warnings.append(f"{meta['code']} 是新單張，上架後才會出現在知識庫")

    sections = []
    chunks = []
    if meta["category"]:
        fm = {**meta, "rag_index": True}
        try:
            chunks, excluded = index_vault.document_chunks(dept_id, fm, body, _count_tokens)
        except ValueError as e:
            errors.append(str(e))
            excluded = []
        for name, content in index_vault.split_sections(body):
            n = sum(1 for c in chunks if c.section == name)
            sections.append({"name": name, "chars": len(index_vault.clean_markdown(content)),
                             "chunks": n, "excluded": name in excluded})
    if meta["category"] and not chunks:
        errors.append("沒有切出任何段落：內容是空的，或全部章節都被排除")
    if len([s for s in sections if not s["excluded"]]) <= 1 and len(index_vault.clean_markdown(body)) > 300:
        warnings.append("沒有偵測到章節（## 標題），整份會當成一個章節；建議在預覽裡補上章節標題")
    changes = text_changes(extracted, body)
    if changes["added"] or changes["removed"]:
        warnings.append(f"與原檔轉出的文字不同：新增 {changes['added']} 字、刪除 {changes['removed']} 字。"
                        "單張內容應照院內核可版本，請確認只調整了格式")
    if fmt == "pdf" and not reviewed:
        warnings.append("PDF 轉出的文字需逐段與原檔核對（斷行、表格、項目符號），核對後勾選「已逐段核對」才能上架")
    return {"sections": sections, "chunk_count": len(chunks),
            "chunks": [{"section": c.section, "text": c.text} for c in chunks],
            "errors": errors, "warnings": warnings, "changes": changes}


# ── 草稿 ──────────────────────────────────────────────────────────
def _row(version_id: int) -> dict:
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM leaflet_versions WHERE id=?", (version_id,)).fetchone()
    if not r:
        raise LookupError("查無此版本")
    return dict(r)


def _draft_view(r: dict) -> dict:
    fm, body = index_vault.parse_frontmatter(r["markdown"])
    body = body.lstrip("\n")
    meta = _clean_meta({k: fm.get(k, r.get(k)) for k in META_FIELDS})
    return {"id": r["id"], "dept": r["dept"], "status": r["status"], "format": r["source_format"],
            "original_name": r["original_name"], "meta": meta, "body": body, "reviewed": bool(r["reviewed"]),
            "created_by": r["created_by"], "created_at": r["created_at"],
            "preview": preview(r["dept"], meta, body, r["extracted_text"] or "", r["source_format"],
                               bool(r["reviewed"]))}


def create_draft(dept_id: str, filename: str, content_b64: str, meta: dict, actor: str) -> dict:
    _dept(dept_id)
    try:
        data = base64.b64decode(content_b64, validate=True)
    except ValueError as e:
        raise LeafletError("檔案內容無法解碼") from e
    fmt, file_fm, body = convert(filename, data)
    # 欄位優先序：表單 > 檔案 frontmatter > 同代碼目前的版本
    code = (meta.get("code") or file_fm.get("code") or "").upper().strip()
    cur = current(dept_id, code) if code else None
    merged = {k: meta.get(k) or file_fm.get(k) or (cur["fm"].get(k) if cur else "") for k in META_FIELDS}
    merged["code"] = code
    m = _clean_meta(merged)
    with db.get_conn() as conn:
        vid = conn.execute(
            "INSERT INTO leaflet_versions(dept, code, title, topic, category, doc_version, source, source_format, "
            "original_name, original_sha256, markdown, extracted_text, status, reviewed, created_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'draft',0,?,?)",
            (dept_id, m["code"], m["title"], m["topic"], m["category"], m["doc_version"], m["source"], fmt,
             Path(filename).name, hashlib.sha256(data).hexdigest(), compose(m, body), body, actor, db._now()),
        ).lastrowid
    p = _original_path(vid, fmt)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    db.log_event("leaflet_draft", payload={"dept": dept_id, "code": m["code"], "version_id": vid,
                                           "file": Path(filename).name}, actor=actor)
    return _draft_view(_row(vid))


def get_draft(version_id: int) -> dict:
    return _draft_view(_row(version_id))


def update_draft(version_id: int, meta: dict | None, body: str | None, reviewed: bool | None) -> dict:
    r = _row(version_id)
    if r["status"] != "draft":
        raise LeafletError("只能修改尚未上架的草稿")
    fm, old_body = index_vault.parse_frontmatter(r["markdown"])
    m = _clean_meta({k: (meta or {}).get(k, r[k]) for k in META_FIELDS})
    new_body = old_body if body is None else body.replace("\r\n", "\n")
    rev = r["reviewed"] if reviewed is None else int(bool(reviewed))
    with db.get_conn() as conn:
        conn.execute("UPDATE leaflet_versions SET code=?, title=?, topic=?, category=?, doc_version=?, source=?, "
                     "markdown=?, reviewed=? WHERE id=?",
                     (m["code"], m["title"], m["topic"], m["category"], m["doc_version"], m["source"],
                      compose(m, new_body), rev, version_id))
    return _draft_view(_row(version_id))


def discard_draft(version_id: int, actor: str) -> None:
    r = _row(version_id)
    if r["status"] != "draft":
        raise LeafletError("只能捨棄尚未上架的草稿")
    with db.get_conn() as conn:
        conn.execute("UPDATE leaflet_versions SET status='discarded' WHERE id=?", (version_id,))
    db.log_event("leaflet_discard", payload={"dept": r["dept"], "code": r["code"], "version_id": version_id},
                 actor=actor)


def versions(dept_id: str, code: str) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, doc_version, title, source_format, original_name, status, reviewed, created_by, "
            "created_at, published_by, published_at, note FROM leaflet_versions "
            "WHERE dept=? AND code=? AND status != 'discarded' ORDER BY id DESC", (dept_id, code.upper())).fetchall()
    return [dict(r) for r in rows]


def original_file(version_id: int) -> tuple[Path, str]:
    r = _row(version_id)
    p = _original_path(version_id, r["source_format"])
    if not p.exists():
        raise LookupError("原始檔不存在")
    return p, r["original_name"] or p.name


# ── 上架、停用、退回（會同步索引）──────────────────────────────
def _index_target():
    """要同步的向量集合與段落嵌入函式。測試會替換這個函式。"""
    from rag import retriever
    return retriever.collection, retriever.embedding_fn.embed_documents


def sync_index() -> dict:
    col, embed = _index_target()
    try:
        chunks, _ = index_vault.build_chunks(overlay_root=store_dir())
        return index_vault.sync_collection(col, chunks, embed=embed)
    except SystemExit as e:   # index_vault 對設定錯誤用 sys.exit
        raise LeafletError(f"索引同步失敗：{e}") from e


def _swap_overlay(dept_id: str, code: str, text: str | None) -> dict:
    """寫入（text）或刪除（None）覆蓋層檔案並同步索引；同步失敗時還原檔案。"""
    p = _overlay_path(dept_id, code)
    backup = p.read_text(encoding="utf-8") if p.exists() else None
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        if text is None:
            p.unlink(missing_ok=True)
        else:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(p)
        return sync_index()
    except Exception:
        if backup is None:
            p.unlink(missing_ok=True)
        else:
            p.write_text(backup, encoding="utf-8")
        try:
            sync_index()
        except Exception:
            pass
        raise


def _mark(dept_id: str, code: str, version_id: int | None, status: str, actor: str, note: str) -> None:
    """這個代碼原本上架中的版本改為 superseded，指定版本改為 status。"""
    with db.get_conn() as conn:
        conn.execute("UPDATE leaflet_versions SET status='superseded' WHERE dept=? AND code=? "
                     "AND status IN ('published','retired')", (dept_id, code))
        if version_id is not None:
            conn.execute("UPDATE leaflet_versions SET status=?, published_by=?, published_at=?, note=? WHERE id=?",
                         (status, actor, db._now(), note or None, version_id))


def publish(version_id: int, actor: str, reviewed: bool = False, note: str = "") -> dict:
    with _lock:
        r = _row(version_id)
        if r["status"] != "draft":
            raise LeafletError("這個版本不是草稿（可能已上架或已捨棄）")
        if reviewed and not r["reviewed"]:
            with db.get_conn() as conn:
                conn.execute("UPDATE leaflet_versions SET reviewed=1 WHERE id=?", (version_id,))
            r["reviewed"] = 1
        view = _draft_view(r)
        if view["preview"]["errors"]:
            raise LeafletError("；".join(view["preview"]["errors"]))
        if r["source_format"] == "pdf" and not r["reviewed"]:
            raise LeafletError("PDF 轉出的文字必須逐段核對後才能上架（請勾選「已逐段核對」）")
        dept_id, code = r["dept"], view["meta"]["code"]
        result = _swap_overlay(dept_id, code, compose(view["meta"], view["body"]))
        _mark(dept_id, code, version_id, "published", actor, note)
    db.log_event("leaflet_publish", payload={"dept": dept_id, "code": code, "version_id": version_id,
                                             "doc_version": view["meta"]["doc_version"], "note": note}, actor=actor)
    return {"code": code, "version_id": version_id, "sync": result}


def retire(dept_id: str, code: str, actor: str, note: str = "") -> dict:
    """停用：之後檢索不到這份單張。保留所有版本，可再退回。"""
    code = code.upper()
    with _lock:
        cur = current(dept_id, code)
        if not cur:
            raise LookupError("查無此單張")
        if not cur["fm"].get("rag_index"):
            raise LeafletError("這份單張已經是停用狀態")
        meta = _clean_meta({k: cur["fm"].get(k, "") for k in META_FIELDS} | {"code": code})
        stub = compose(meta, f"（{code} 已於管理後台停用）", rag_index=False)
        result = _swap_overlay(dept_id, code, stub)
        with db.get_conn() as conn:
            vid = conn.execute(
                "INSERT INTO leaflet_versions(dept, code, title, topic, category, doc_version, source, source_format, "
                "markdown, status, created_by, created_at) VALUES (?,?,?,?,?,?,?,'retire',?,'draft',?,?)",
                (dept_id, code, meta["title"], meta["topic"], meta["category"], meta["doc_version"], meta["source"],
                 stub, actor, db._now())).lastrowid
        _mark(dept_id, code, vid, "retired", actor, note)
    db.log_event("leaflet_retire", payload={"dept": dept_id, "code": code, "note": note}, actor=actor)
    return {"code": code, "sync": result}


def restore(dept_id: str, code: str, version_id: int | None, actor: str, note: str = "") -> dict:
    """退回：version_id = 某個曾上架的版本；None = 院內原始版（repo 裡的單張）。"""
    code = code.upper()
    with _lock:
        if version_id is None:
            if code not in _builtin(dept_id):
                raise LeafletError("這份單張沒有院內原始版可以退回")
            result = _swap_overlay(dept_id, code, None)
            _mark(dept_id, code, None, "", actor, note)
        else:
            r = _row(version_id)
            if r["dept"] != dept_id or r["code"] != code or r["source_format"] == "retire":
                raise LeafletError("這個版本不屬於這份單張，或不是可退回的內容版本")
            if r["published_at"] is None:
                raise LeafletError("只能退回曾經上架過的版本")
            result = _swap_overlay(dept_id, code, r["markdown"])
            _mark(dept_id, code, version_id, "published", actor, note)
    db.log_event("leaflet_restore", payload={"dept": dept_id, "code": code, "version_id": version_id,
                                             "note": note}, actor=actor)
    return {"code": code, "version_id": version_id, "sync": result}
