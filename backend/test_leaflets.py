"""
單張管理後台測試：上傳（Markdown／Word／PDF）→ 預覽 → 上架／停用／退回，版本紀錄與索引同步。
用臨時資料庫與臨時向量集合（假嵌入），不動到開發用的索引；不需 API key。

執行：cd backend && python -X utf8 test_leaflets.py
"""
import testutil  # noqa: F401  必須最先
import base64
import io
import tempfile
import types
import zipfile
from pathlib import Path

import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings

import db
import leaflets  # 會把 scripts/ 加進 sys.path
import index_vault

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1; print(f"  ✓ {name}")
    else:
        _fail += 1; print(f"  ✗ {name}  ← 失敗")


class StubEF(EmbeddingFunction):
    def __init__(self):
        pass

    def __call__(self, input: Documents) -> Embeddings:
        return [[float((hash(t) >> s) & 0xFF) / 255 for s in range(0, 64, 8)] for t in input]

    @staticmethod
    def name() -> str:
        return "stub"


# ── 測試檔案產生器 ────────────────────────────────────────────────
def make_docx(paragraphs: list[tuple[str, str]], table: list[list[str]] | None = None) -> bytes:
    """paragraphs：[(樣式, 文字)]，樣式 Title／Heading1／List／Normal。"""
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    styles = (f'<w:styles {W}>'
              '<w:style w:styleId="Title"><w:name w:val="Title"/></w:style>'
              '<w:style w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
              '</w:styles>')
    body = []
    for style, text in paragraphs:
        ppr = ""
        if style in ("Title", "Heading1"):
            ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
        elif style == "List":
            ppr = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        body.append(f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>")
    if table:
        rows = "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in r) + "</w:tr>"
                       for r in table)
        body.append(f"<w:tbl>{rows}</w:tbl>")
    doc = f'<w:document {W}><w:body>{"".join(body)}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
    return buf.getvalue()


def make_pdf(pages: list[list[str]]) -> bytes:
    """最小的文字 PDF（Helvetica，ASCII）。"""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        esc = [ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for ln in lines]
        stream = "BT /F1 11 Tf 14 TL 50 780 Td " + " ".join(f"({t}) Tj T*" for t in esc) + " ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        content_id = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


b64 = lambda b: base64.b64encode(b).decode()

# ── 1. 格式轉換 ──────────────────────────────────────────────────
print("=== 格式轉換 ===")
md = leaflets.docx_to_markdown(make_docx([
    ("Title", "測試衛教單張"), ("Heading1", "概述"), ("Normal", "這是概述。"),
    ("Heading1", "照顧方式"), ("List", "多喝水"), ("List", "按時服藥"), ("Normal", "其他說明。"),
], table=[["症狀", "處理"], ["發燒", "立即就醫"]]))
check("Word：唯一的最上層標題成為單張標題（#）", "# 測試衛教單張" in md)
check("Word：標題樣式成為章節（##）", "## 概述" in md and "## 照顧方式" in md)
check("Word：清單項目加「- 」", "- 多喝水" in md and "- 按時服藥" in md)
check("Word：表格每列一行", "症狀｜處理" in md and "發燒｜立即就醫" in md)
try:
    leaflets.docx_to_markdown(b"not a zip")
    check("Word：壞檔給清楚的錯誤", False)
except leaflets.LeafletError as e:
    check("Word：壞檔給清楚的錯誤", ".docx" in str(e))

pdf_md = leaflets.pdf_to_markdown(make_pdf([
    ["CHEMO CARE LEAFLET", "Drink plenty of water and rest", "when you feel tired.", "1. Call the nurse if", "you have a fever.", "1"],
    ["CHEMO CARE LEAFLET", "Wash your hands often.", "2"],
    ["CHEMO CARE LEAFLET", "Avoid crowds.", "3"],
]))
check("PDF：每頁重複的頁首被移除", "CHEMO CARE LEAFLET" not in pdf_md)
check("PDF：頁碼被移除", "\n1\n" not in f"\n{pdf_md}\n" and not pdf_md.strip().endswith("3"))
check("PDF：句子中間的斷行接回同一段", "Drink plenty of water and rest when you feel tired." in pdf_md)
check("PDF：編號項目另起一段", "\n\n1. Call the nurse if you have a fever." in pdf_md)
try:
    leaflets.convert("x.doc", b"abc")
    check("舊版 .doc 不接受", False)
except leaflets.LeafletError:
    check("舊版 .doc 不接受", True)

print("\n=== 內容比對（只允許調整格式）===")
check("只改空白與章節符號 → 視為未改內容", leaflets.text_changes("概述內容一\n\n照顧", "## 概述內容一\n照顧")["added"] == 0)
ch = leaflets.text_changes("每天喝水兩千毫升", "每天喝水三千毫升")
check("改了文字 → 回報增刪字數", ch["added"] == 1 and ch["removed"] == 1)

# ── 2. API：上傳 → 預覽 → 上架 ──────────────────────────────────
TMP = Path(tempfile.mkdtemp(prefix="leaflets_"))
db.DB_PATH = TMP / "t.sqlite3"
col = chromadb.EphemeralClient().get_or_create_collection(
    "leaflet_test", embedding_function=StubEF(), metadata={"hnsw:space": "cosine"})
leaflets._index_target = lambda: (col, None)
index_vault.sync_collection(col, index_vault.build_chunks(overlay_root=None)[0])
BASE = col.count()

import main  # noqa: E402
main.retriever = types.SimpleNamespace(collection=col)


def codes_in_index(code=None):
    metas = col.get(include=["metadatas"])["metadatas"]
    return [m for m in metas if code is None or m["code"] == code]


from fastapi.testclient import TestClient  # noqa: E402

LEAFLET_MD = """---
code: ONC-99
title: 測試單張
category: 測試
doc_version: 2026.01 一版
---
# 測試單張

## 概述
化療期間要注意手部清潔，外出時戴口罩。

## 何時通知醫護人員
發燒超過 38 度請立即告知醫護人員。
"""

with TestClient(main.app) as client:
    A = testutil.login_headers(client, "A001", "admin123")
    R = testutil.login_headers(client, "R001", "research123")
    D = "hemato-oncology"

    print("\n=== 權限 ===")
    check("研究者不能看單張管理", client.get("/admin/leaflets", headers=R).status_code == 403)
    check("研究者不能上傳", client.post("/admin/leaflets/drafts", headers=R, json={
        "dept": D, "filename": "a.md", "content_b64": b64(b"x")}).status_code == 403)

    print("\n=== 清單 ===")
    items = client.get("/admin/leaflets", headers=A).json()["items"]
    check("列出 13 份院內原始單張", len(items) == 13 and all(i["origin"] == "builtin" and i["active"] for i in items))
    check("清單有每份單張的段落數", all((i["chunks"] or 0) > 0 for i in items))

    print("\n=== 新單張（Markdown）===")
    r = client.post("/admin/leaflets/drafts", headers=A, json={
        "dept": D, "filename": "ONC-99.md", "content_b64": b64(LEAFLET_MD.encode())})
    d = r.json()
    check("上傳建立草稿（201）", r.status_code == 201 and d["status"] == "draft")
    check("欄位取自檔案 frontmatter", d["meta"]["code"] == "ONC-99" and d["meta"]["topic"] == "測試單張")
    check("預覽列出章節與段落數", [s["name"] for s in d["preview"]["sections"]] == ["概述", "何時通知醫護人員"]
          and d["preview"]["chunk_count"] == 2)
    check("沒有阻擋上架的問題", d["preview"]["errors"] == [])
    check("提醒這是新單張", any("新單張" in w for w in d["preview"]["warnings"]))
    check("草稿還不在索引裡", not codes_in_index("ONC-99"))
    first_id = d["id"]

    r = client.post(f"/admin/leaflets/drafts/{first_id}/publish", headers=A, json={"note": "新增"})
    check("上架成功並同步索引", r.status_code == 200 and r.json()["sync"]["added"] == 2)
    check("新單張進入索引", len(codes_in_index("ONC-99")) == 2)
    check("覆蓋層檔案寫入 data/leaflets/<科別>/<代碼>.md", (leaflets.store_dir() / D / "ONC-99.md").exists())
    item = next(i for i in client.get("/admin/leaflets", headers=A).json()["items"] if i["code"] == "ONC-99")
    check("清單顯示為上傳版本、使用中", item["origin"] == "uploaded" and item["active"] and item["updated_by"] == "A001")
    check("啟動時的索引器也讀得到上架的單張",
          any(c.code == "ONC-99" for c in index_vault.build_chunks(overlay_root=leaflets.store_dir())[0])
          and not any(c.code == "ONC-99" for c in index_vault.build_chunks(overlay_root=None)[0]))

    print("\n=== 改版（Word）===")
    docx = make_docx([("Title", "測試單張"), ("Heading1", "概述"), ("Normal", "化療期間要注意手部清潔。"),
                      ("Heading1", "飲食"), ("Normal", "食物要煮熟。")])
    d = client.post("/admin/leaflets/drafts", headers=A, json={
        "dept": D, "filename": "ONC-99 改版.docx", "content_b64": b64(docx), "code": "onc-99"}).json()
    check("代碼自動轉大寫，其他欄位沿用目前版本", d["meta"]["code"] == "ONC-99" and d["meta"]["category"] == "測試")
    check("版本沒改 → 擋下上架", any("版本" in e for e in d["preview"]["errors"]))
    r = client.post(f"/admin/leaflets/drafts/{d['id']}/publish", headers=A, json={})
    check("版本相同時上架回 400", r.status_code == 400)
    d = client.put(f"/admin/leaflets/drafts/{d['id']}", headers=A, json={"doc_version": "2026.06 二版"}).json()
    check("改了版本後沒有阻擋問題", d["preview"]["errors"] == [])
    d2 = client.put(f"/admin/leaflets/drafts/{d['id']}", headers=A,
                    json={"body": d["body"].replace("食物要煮熟", "食物要煮到全熟")}).json()
    check("預覽裡改了文字 → 提醒與原檔不同", any("與原檔轉出的文字不同" in w for w in d2["preview"]["warnings"]))
    d = client.put(f"/admin/leaflets/drafts/{d['id']}", headers=A, json={"body": d["body"]}).json()
    check("改回原文 → 提醒消失", not any("與原檔" in w for w in d["preview"]["warnings"]))
    r = client.post(f"/admin/leaflets/drafts/{d['id']}/publish", headers=A, json={})
    check("改版上架成功", r.status_code == 200)
    metas = codes_in_index("ONC-99")
    check("索引只剩新版段落（舊版已刪除）", metas and all(m["doc_version"] == "2026.06 二版" for m in metas))
    check("新版的「飲食」章節可被檢索到", any(m["section"] == "飲食" for m in metas))
    second_id = d["id"]

    print("\n=== PDF 改版院內單張 ===")
    pdf = make_pdf([["Mouth care leaflet", "Rinse your mouth after meals", "and before bed."]])
    d = client.post("/admin/leaflets/drafts", headers=A, json={
        "dept": D, "filename": "ONC-17.pdf", "content_b64": b64(pdf), "code": "ONC-17",
        "doc_version": "2026.11 八版"}).json()
    check("PDF 草稿沿用原始單張的分類", d["meta"]["category"] != "" and d["format"] == "pdf")
    check("PDF 提醒要逐段核對", any("逐段" in w for w in d["preview"]["warnings"]))
    r = client.post(f"/admin/leaflets/drafts/{d['id']}/publish", headers=A, json={})
    check("PDF 未勾選核對 → 不能上架", r.status_code == 400 and "核對" in r.json()["detail"])
    r = client.post(f"/admin/leaflets/drafts/{d['id']}/publish", headers=A, json={"reviewed": True})
    check("PDF 勾選核對後可上架", r.status_code == 200)
    check("ONC-17 索引換成新版", all(m["doc_version"] == "2026.11 八版" for m in codes_in_index("ONC-17")))
    orig = client.get(f"/admin/leaflets/versions/{d['id']}/original", headers=A)
    check("可下載原始上傳檔核對", orig.status_code == 200 and orig.content == pdf)

    print("\n=== 退回 ===")
    r = client.post(f"/admin/leaflets/{D}/ONC-17/restore", headers=A, json={"version_id": None})
    check("退回院內原始版", r.status_code == 200 and not (leaflets.store_dir() / D / "ONC-17.md").exists())
    check("ONC-17 索引回到原始版本", all(m["doc_version"] != "2026.11 八版" for m in codes_in_index("ONC-17"))
          and codes_in_index("ONC-17"))
    r = client.post(f"/admin/leaflets/{D}/ONC-99/restore", headers=A, json={"version_id": first_id})
    check("退回指定的舊版", r.status_code == 200
          and all(m["doc_version"] == "2026.01 一版" for m in codes_in_index("ONC-99")))
    r = client.post(f"/admin/leaflets/{D}/ONC-99/restore", headers=A, json={"version_id": None})
    check("新單張沒有院內原始版可退回（400）", r.status_code == 400)
    vs = client.get(f"/admin/leaflets/{D}/ONC-99/versions", headers=A).json()["versions"]
    status = {v["id"]: v["status"] for v in vs}
    check("版本紀錄：退回的版本為上架中、另一版為已取代",
          status[first_id] == "published" and status[second_id] == "superseded")

    print("\n=== 停用 ===")
    r = client.post(f"/admin/leaflets/{D}/ONC-18/retire", headers=A, json={"note": "院內停用"})
    check("停用院內單張", r.status_code == 200 and not codes_in_index("ONC-18"))
    item = next(i for i in client.get("/admin/leaflets", headers=A).json()["items"] if i["code"] == "ONC-18")
    check("清單顯示已停用", item["active"] is False)
    check("重複停用回 400", client.post(f"/admin/leaflets/{D}/ONC-18/retire", headers=A, json={}).status_code == 400)
    r = client.post(f"/admin/leaflets/{D}/ONC-18/restore", headers=A, json={"version_id": None})
    check("恢復後重新進入索引", r.status_code == 200 and codes_in_index("ONC-18"))

    print("\n=== 檢查與防呆 ===")
    bad = client.post("/admin/leaflets/drafts", headers=A, json={
        "dept": D, "filename": "x.md", "content_b64": b64("## 概述\n內容".encode()), "code": "abc",
        "title": "x", "category": "c", "doc_version": "1"}).json()
    check("代碼格式不對 → 擋下", any("代碼格式" in e for e in bad["preview"]["errors"]))
    empty = client.post("/admin/leaflets/drafts", headers=A, json={
        "dept": D, "filename": "x.md", "content_b64": b64(b""), "code": "ONC-98",
        "title": "x", "category": "c", "doc_version": "1"}).json()
    check("沒有內容 → 擋下", any("沒有切出任何段落" in e for e in empty["preview"]["errors"]))
    check("不支援的格式回 400", client.post("/admin/leaflets/drafts", headers=A, json={
        "dept": D, "filename": "x.xlsx", "content_b64": b64(b"x")}).status_code == 400)
    check("未知科別回 400", client.post("/admin/leaflets/drafts", headers=A, json={
        "dept": "nope", "filename": "x.md", "content_b64": b64(b"x")}).status_code == 400)
    r = client.delete(f"/admin/leaflets/drafts/{bad['id']}", headers=A)
    check("捨棄草稿", r.status_code == 200 and client.post(
        f"/admin/leaflets/drafts/{bad['id']}/publish", headers=A, json={}).status_code == 400)
    check("已上架的版本不能再修改", client.put(f"/admin/leaflets/drafts/{second_id}", headers=A,
                                          json={"title": "y"}).status_code == 400)

    print("\n=== 稽核與總量 ===")
    with db.get_conn() as conn:
        ev = [r["event_type"] for r in conn.execute(
            "SELECT event_type FROM events WHERE actor='A001' AND event_type LIKE 'leaflet_%'")]
    check("上架、停用、退回都記入稽核紀錄",
          {"leaflet_draft", "leaflet_publish", "leaflet_retire", "leaflet_restore", "leaflet_discard"} <= set(ev))
    check("其他單張的段落沒有受影響（總數 = 原本 + ONC-99 一版的 2 段）", col.count() == BASE + 2)
    with db.get_conn() as conn:
        check("資料庫 schema 至少 v6", conn.execute("SELECT MAX(version) v FROM schema_version").fetchone()["v"] >= 6)

print(f"\n通過 {_pass} / {_pass + _fail}")
if _fail:
    raise SystemExit(1)
