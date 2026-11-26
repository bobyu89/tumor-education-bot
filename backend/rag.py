"""
向量檢索：語意相似度（ChromaDB cosine）+ 相關度過濾。

改動（Phase 1）：
- 改用 backend/embedding.py 的共用嵌入層（torch → ONNX 自動 fallback），
  修好 Smart App Control 封鎖 torch 導致無法啟動的問題。
- metadata 鍵名對齊 scripts/index_vault.py：
  舊 index 用 source_file / disease_category；新 index 用 source / category / code / section。
  兩者都相容（get 時同時嘗試）。
"""
from embedding import get_spec, make_embedding_function
import logging

import chromadb
from config import settings


class RAGRetriever:
    def __init__(self):
        self.client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)
        self.spec = get_spec()
        self.embedding_fn = make_embedding_function()
        # 不把嵌入函式掛在集合上：查詢向量一律自己算（加查詢前綴），
        # 也避免 chromadb 因集合記錄的嵌入函式與現在不同而拒絕開啟
        self.collection = self.client.get_or_create_collection(
            name=settings.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine", "embed_model": self.spec.name},
        )
        # 用 A 模型建的索引拿 B 模型查詢，相似度沒有意義、會檢索到錯的單張。
        # 不一致時拒絕檢索（回傳空結果 → 系統轉介，不會亂答），並大聲提醒同步。
        self.model_mismatch = (self.collection.count() > 0
                               and (self.collection.metadata or {}).get("embed_model") != self.spec.name)
        if self.model_mismatch:
            logging.error("[rag] 向量索引是用 %s 建立的，目前檢索模型是 %s —— 停止檢索。"
                          "請執行 python -X utf8 scripts/index_vault.py 重建（Docker 版啟動時會自動處理）。",
                          (self.collection.metadata or {}).get("embed_model", "舊版 MiniLM"), self.spec.name)
        self._warn_if_untagged()

    def _warn_if_untagged(self):
        """舊版索引的段落沒有科別標記，依科別檢索會查不到任何東西 —— 啟動時提醒同步。"""
        if self.collection.count() == 0:
            return
        sample = self.collection.get(limit=20, include=["metadatas"])["metadatas"]
        if any(not (m or {}).get("dept") for m in sample):
            logging.warning("[rag] 向量索引缺少科別標記（舊版索引）。請執行 python -X utf8 scripts/index_vault.py 同步，"
                            "否則依科別檢索會查不到資料。Docker 版啟動時會自動同步。")

    def query(self, query_text: str, n_results: int = None,
              category_filter: str = None, code_filter: str = None,
              dept: str | None = None) -> list[dict]:
        """語意檢索。code_filter（如 "ONC-22"）可把檢索限制在單一衛教單張，
        供症狀評估後的衛教使用：症狀 → 對應單張是確定的，不該靠語意猜。"""
        k = n_results or settings.RAG_TOP_K
        total = self.collection.count()
        if total == 0 or self.model_mismatch:
            return []

        k = min(k, total)
        # 新舊 metadata 皆支援分類過濾；code 只有新 index（index_vault.py）才有
        conds = []
        if dept:
            conds.append({"dept": dept})          # 只檢索病患所屬科別的單張
        if category_filter:
            conds.append({"category": category_filter})
        if code_filter:
            conds.append({"code": code_filter})
        where = conds[0] if len(conds) == 1 else ({"$and": conds} if conds else None)

        results = self.collection.query(
            query_embeddings=self.embedding_fn.embed_query([query_text]),
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
            if 1 - dist >= self.spec.min_similarity:   # 門檻依模型而定，見 embedding.MODELS
                code = meta.get("code", "")
                # 顯示用來源標籤：新 index 用「ONC-17 口腔黏膜炎」（frontmatter 的 source 是發行單位，
                # 13 份全相同，不能拿來當來源標籤）；舊 index 退回 source_file。
                label = (f"{code} {meta.get('topic', '')}".strip() if code
                         else meta.get("source") or meta.get("source_file", "unknown"))
                output.append({
                    "content": doc,
                    "source": label,
                    "publisher": meta.get("source", ""),
                    "code": meta.get("code", ""),
                    "section": meta.get("section", ""),
                    "category": meta.get("category") or meta.get("disease_category", ""),
                    "topic": meta.get("topic", ""),
                    "doc_version": meta.get("doc_version", ""),
                    "score": round(1 - dist, 3),
                })
        return output

    def known_codes(self) -> frozenset[str]:
        """知識庫中所有科別的單張代碼（引用核對用：不假設代碼格式，新增科別或單張時自動生效）。
        引用其他科別的單張同樣算 citation_mismatch，因為那份單張沒有提供給模型。
        每次呼叫讀一次集合 metadata；單張數量級在數千段以內，成本可忽略，且保證與索引同步。"""
        if self.collection.count() == 0:
            return frozenset()
        metas = self.collection.get(include=["metadatas"])["metadatas"]
        return frozenset(m["code"] for m in metas if m and m.get("code"))


retriever = RAGRetriever()
