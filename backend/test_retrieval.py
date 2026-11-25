"""
檢索品質回歸測試：走正式的 rag.query 路徑（不是評估腳本自己的實作），確認換模型或改切段後沒有退步。
需要嵌入模型（首次執行自動下載）與已同步的向量索引（scripts/index_vault.py），不需 API key。

  - 開發集 79 題病人口語問題：第一順位單張正確率（Hit@1）與前 3 份單張內含正確單張（Hit@3）
  - 檢索模型記錄在索引 metadata；索引與目前模型不一致時拒絕檢索（不會用錯的向量亂答）
  - 查詢與段落使用各自的前綴（e5 系列）
  - 紅旗向量層固定使用門檻校準時的模型，不跟著檢索模型換

門檻刻意設得比目前結果（Hit@1 0.82、Hit@3 0.91）低一些，只抓明顯退步；
比較模型請用 scripts/eval_retrieval.py。

執行：cd backend && python -X utf8 test_retrieval.py
"""
import testutil  # noqa: F401  必須最先
import json
import logging
import tempfile
from pathlib import Path

import embedding
import rag
import redflag
from config import settings

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1; print(f"  ✓ {name}")
    else:
        _fail += 1; print(f"  ✗ {name}  ← 失敗")


EVAL = json.loads((Path(__file__).parent / "retrieval_eval.json").read_text(encoding="utf-8"))
DEPT = "hemato-oncology"
r = rag.retriever

print(f"=== 開發集檢索（{r.spec.name}，正式 rag.query 路徑）===")
check("索引記錄的模型與目前檢索模型一致", (r.collection.metadata or {}).get("embed_model") == r.spec.name)
check("索引與模型一致時可以檢索", not r.model_mismatch)

h1 = h3 = 0
misses = []
for item in EVAL["in_scope"]:
    ok = {item["target"], *item.get("alt", [])}
    docs = r.query(item["q"], dept=DEPT)
    leaflets = list(dict.fromkeys(d["code"] for d in docs))
    h1 += bool(leaflets) and leaflets[0] in ok
    h3 += any(c in ok for c in leaflets[:3])
    if not leaflets or leaflets[0] not in ok:
        misses.append(f"{item['q']}→{leaflets[0] if leaflets else '（無）'}")
n = len(EVAL["in_scope"])
print(f"  Hit@1 {h1 / n:.2f}　Hit@3 {h3 / n:.2f}（{n} 題）")
check("Hit@1 ≥ 0.75（MiniLM 為 0.66）", h1 / n >= 0.75)
check("Hit@3 ≥ 0.85（MiniLM 為 0.84）", h3 / n >= 0.85)
if misses:
    print("  未命中：", "；".join(misses))

no_source = [q for q in (x["q"] for x in EVAL["in_scope"]) if not r.query(q, dept=DEPT)]
check("範圍內問題都不會被相似度門檻擋掉（避免誤判為無來源）", not no_source)

print("\n=== 查詢與段落前綴 ===")
ef = embedding.make_embedding_function()
if r.spec.query_prefix != r.spec.passage_prefix:
    q, d = ef.embed_query(["口腔潰瘍"])[0], ef.embed_documents(["口腔潰瘍"])[0]
    check("e5：查詢與段落的向量不同（前綴有加上去）", q != d)
else:
    check("此模型查詢與段落不需前綴", True)

print("\n=== 索引模型不一致時拒絕檢索 ===")
orig_path = settings.CHROMA_DB_PATH
with tempfile.TemporaryDirectory() as tmp:
    import chromadb
    col = chromadb.PersistentClient(path=tmp).get_or_create_collection(
        settings.COLLECTION_NAME, metadata={"hnsw:space": "cosine", "embed_model": "some-other-model"})
    dim = len(ef.embed_query(["x"])[0])
    col.add(ids=["a"], documents=["口腔照護"], embeddings=[[1.0] + [0.0] * (dim - 1)],
            metadatas=[{"code": "ONC-17", "dept": DEPT}])
    settings.CHROMA_DB_PATH = tmp
    logging.disable(logging.ERROR)
    try:
        other = rag.RAGRetriever()
    finally:
        logging.disable(logging.NOTSET)
        settings.CHROMA_DB_PATH = orig_path
    check("偵測到索引是用其他模型建立的", other.model_mismatch)
    check("不一致時回傳空結果（系統轉介，不用錯的向量作答）", other.query("嘴巴破", dept=DEPT) == [])
    del other, col

print("\n=== 紅旗向量層的模型 ===")
redflag._load_vectors()
check(f"紅旗向量固定用 {embedding.REDFLAG_MODEL}（門檻 0.85 在此模型上校準）",
      bool(redflag._embedder) and redflag._embedder.spec.name == embedding.REDFLAG_MODEL)

print(f"\n通過 {_pass} / {_pass + _fail}")
if _fail:
    raise SystemExit(1)
