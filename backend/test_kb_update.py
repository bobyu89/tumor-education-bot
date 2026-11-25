"""
知識庫可持續更新（第 1 階段）測試，不需嵌入模型、不需 API key。

  - 向量庫同步：單張改版後舊段落被刪除；停用或移除的單張整份刪除；未變動的不重嵌；
    沒有任何可索引單張時拒絕清空向量庫
  - 引用核對不寫死 ONC 格式：其他科別代碼（CV-01）可用；模型編出的代碼判為 citation_mismatch
  - 回答記錄所依據的單張版本（messages.rag_doc_versions）

執行：cd backend && python -X utf8 test_kb_update.py
"""
import testutil  # 必須最先
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings

import index_vault
from index_vault import Chunk, sync_collection

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1; print(f"  ✓ {name}")
    else:
        _fail += 1; print(f"  ✗ {name}  ← 失敗")


class StubEF(EmbeddingFunction):
    """固定維度的假嵌入：只測同步邏輯，不需要下載模型。"""
    def __init__(self):
        pass

    def __call__(self, input: Documents) -> Embeddings:
        return [[float((hash(t) >> s) & 0xFF) / 255 for s in range(0, 64, 8)] for t in input]

    @staticmethod
    def name() -> str:
        return "stub"


def chunks_for(code: str, version: str, texts: list[str]) -> list[Chunk]:
    import hashlib
    out = []
    for i, t in enumerate(texts):
        cid = hashlib.md5(f"{code}|{version}|sec|{i}|{t}".encode()).hexdigest()
        out.append(Chunk(id=cid, text=t, code=code, topic="t", category="c",
                         section="sec", source="s", doc_version=version))
    return out


print("=== 向量庫同步（單張改版、停用、移除）===")
client = chromadb.EphemeralClient()
col = client.get_or_create_collection("kb_test", embedding_function=StubEF(), metadata={"hnsw:space": "cosine"})

v1 = chunks_for("ONC-17", "2024.08", ["漱口要用溫水", "化療前三十分鐘含碎冰"]) + chunks_for("ONC-22", "2024.08", ["腹瀉多喝水"])
r = sync_collection(col, v1)
check("第一次同步：全部新增", r == {"added": 3, "removed": 0, "unchanged": 0} and col.count() == 3)

r = sync_collection(col, v1)
check("內容沒變：不重嵌、不刪除", r == {"added": 0, "removed": 0, "unchanged": 3})

v2 = chunks_for("ONC-17", "2025.01", ["漱口要用溫水", "依醫囑使用漱口水"]) + chunks_for("ONC-22", "2024.08", ["腹瀉多喝水"])
r = sync_collection(col, v2)
docs = col.get(include=["documents", "metadatas"])
check("ONC-17 改版：舊版兩段刪除、新版兩段新增", r == {"added": 2, "removed": 2, "unchanged": 1})
check("舊版內容不再存在於向量庫", "化療前三十分鐘含碎冰" not in docs["documents"])
check("留下的 ONC-17 段落都是新版本",
      {m["doc_version"] for m in docs["metadatas"] if m["code"] == "ONC-17"} == {"2025.01"})

r = sync_collection(col, chunks_for("ONC-17", "2025.01", ["漱口要用溫水", "依醫囑使用漱口水"]))
check("ONC-22 停用或移除：整份刪除", r["removed"] == 1 and
      "ONC-22" not in {m["code"] for m in col.get(include=["metadatas"])["metadatas"]})

try:
    sync_collection(col, [])
    check("沒有任何可索引單張 → 拒絕清空向量庫", False)
except SystemExit:
    check("沒有任何可索引單張 → 拒絕清空向量庫", col.count() == 2)

print("\n=== 實際知識庫：chunk ID 含版本 ===")
real_chunks, _ = index_vault.build_chunks()
check("13 份單張都切得出段落", len({c.code for c in real_chunks}) == 13)
check("每個 chunk 都帶版本", all(c.doc_version for c in real_chunks))
check("chunk ID 不重複", len({c.id for c in real_chunks}) == len(real_chunks))

print("\n=== 引用核對不寫死 ONC 格式 ===")
import quality
CV = [{"source": "CV-01 心衰竭居家照護", "code": "CV-01", "content": "每天量體重", "score": 0.8}]
check("其他科別代碼（CV-01）→ grounded", quality.classify("每天早上量體重（CV-01）", CV) == "grounded")
check("模型編出不存在的代碼 → citation_mismatch", quality.classify("多喝水（CV-99）", CV) == "citation_mismatch")
check("引用知識庫中其他未提供的單張 → citation_mismatch",
      quality.classify("要多休息（ONC-37）", CV, frozenset({"CV-01", "ONC-37"})) == "citation_mismatch")
check("沒標出處 → uncited", quality.classify("每天早上量體重", CV) == "uncited")
check("一般文字中的『COVID-19』不誤當成引用", quality.classify("每天量體重（CV-01），COVID-19 疫苗另詢問醫師", CV) == "grounded")
from prompt import build_prompt
check("提示詞的引用範例用實際提供的代碼", "（CV-01）" in build_prompt(None, CV) and "ONC-17" not in build_prompt(None, CV))

print("\n=== 回答記錄所依據的單張版本 ===")
import db
db.DB_PATH = Path(tempfile.gettempdir()) / "tumor_bot_kb_update.sqlite3"
db.DB_PATH.unlink(missing_ok=True)
db.init_db()
db.ensure_session("P910")
import chat_service
import llm_client
import rag
from llm_client import LLMResult

_orig_q, _orig_k = rag.retriever.query, rag.retriever.known_codes
rag.retriever.query = lambda *a, **k: [{"source": "ONC-17 口腔黏膜炎", "code": "ONC-17", "doc_version": "2024.08 七版",
                                        "content": "餐後以軟毛牙刷清潔口腔。", "score": 0.8}]
rag.retriever.known_codes = lambda: frozenset({"ONC-17", "ONC-22"})


class FakeLLM:
    def generate(self, *a, **k):
        return LLMResult("用軟毛牙刷清潔口腔（ONC-17）", "claude-sonnet-5-5", "anthropic", 10, 5)


llm_client._client = FakeLLM()
asyncio.run(chat_service.process_message("P910", "刷牙要注意什麼"))
with db.get_conn() as conn:
    m = conn.execute("SELECT answer_quality, rag_doc_versions FROM messages WHERE patient_code='P910' AND role='bot' "
                     "ORDER BY id DESC").fetchone()
check("回答標記為 grounded", m["answer_quality"] == "grounded")
check("記錄 {代碼: 版本}", json.loads(m["rag_doc_versions"]) == {"ONC-17": "2024.08 七版"})
import stats
check("研究匯出包含單張版本欄位", "rag_doc_versions" in stats.export_csv("messages").splitlines()[0])
with db.get_conn() as conn:
    v = conn.execute("SELECT MAX(version) v FROM schema_version").fetchone()["v"]
check("資料庫 schema 升到 v4", v == 4)
rag.retriever.query, rag.retriever.known_codes = _orig_q, _orig_k
llm_client._client = None
db.DB_PATH.unlink(missing_ok=True)

print(f"\n通過 {_pass} / {_pass + _fail}")
if _fail:
    raise SystemExit(1)
