"""
混合檢索：語意相似度（ChromaDB cosine）+ 相關度過濾
"""
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from config import settings


class RAGRetriever:
    def __init__(self):
        self.client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)
        self.embedding_fn = SentenceTransformerEmbeddingFunction(
            model_name="paraphrase-multilingual-MiniLM-L12-v2"
        )
        try:
            self.collection = self.client.get_collection(
                name=settings.COLLECTION_NAME,
                embedding_function=self.embedding_fn
            )
        except Exception:
            # 若知識庫尚未建立，建立空 collection
            self.collection = self.client.get_or_create_collection(
                name=settings.COLLECTION_NAME,
                embedding_function=self.embedding_fn,
                metadata={"hnsw:space": "cosine"}
            )

    def query(
        self,
        query_text: str,
        n_results: int = None,
        disease_filter: str = None
    ) -> list[dict]:
        k = n_results or settings.RAG_TOP_K
        total = self.collection.count()
        if total == 0:
            return []

        k = min(k, total)
        where = {"disease_category": disease_filter} if disease_filter else None

        results = self.collection.query(
            query_texts=[query_text],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"]
        )

        output = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0]
        ):
            if dist < 0.8:   # cosine distance < 0.8 = 相關
                output.append({
                    "content": doc,
                    "source": meta.get("source_file", "unknown"),
                    "category": meta.get("disease_category", ""),
                    "topic": meta.get("topic", ""),
                    "score": round(1 - dist, 3)
                })

        return output


retriever = RAGRetriever()
