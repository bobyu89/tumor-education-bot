"""
共用嵌入層 —— paraphrase-multilingual-MiniLM-L12-v2

兩條路徑，回傳的向量可互換：
  1. sentence-transformers（架構書指定，需要 torch）
  2. onnxruntime 跑同一個模型的 ONNX 權重（torch 無法載入時）

為什麼需要 fallback：Windows 11 的 Smart App Control（強制模式）會封鎖
torch 未簽章的 DLL，import torch 拋 WinError 4551「應用程式控制原則已封鎖此檔案」。
此時 sentence-transformers 完全無法載入，改用 ONNX 走同一份權重、同樣 mean-pooling。

rag.py（查詢）與 scripts/index_vault.py（建庫）都應該用這裡的 make_embedding_function()，
確保建庫與查詢用的是完全相同的嵌入函式。

⚠️ 模型 max_seq_length = 128 tokens，超過會被靜默截斷。切 chunk 時務必以此為上限
   （中文實測約 1.35 字/token）。詳見 index_vault.py 的切割邏輯。
"""
from __future__ import annotations

from pathlib import Path

EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
MODEL_MAX_TOKENS = 128

# ONNX 權重與 tokenizer 的本機路徑與下載來源
_MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / EMBED_MODEL
_ONNX_REPO = f"https://huggingface.co/Xenova/{EMBED_MODEL}/resolve/main"


class OnnxMiniLMEmbedder:
    """用 onnxruntime 重現 SentenceTransformer(paraphrase-multilingual-MiniLM-L12-v2)。

    pooling = mean（依 attention_mask 加權），與該模型 1_Pooling/config.json 一致；
    不做 L2 normalize，與 chromadb 的 SentenceTransformerEmbeddingFunction 預設一致。
    集合使用 cosine 距離，是否正規化不影響排序。
    """

    def __init__(self) -> None:
        import urllib.request
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.np = np
        _MODEL_DIR.mkdir(parents=True, exist_ok=True)
        for remote, local in [("tokenizer.json", "tokenizer.json"),
                              ("onnx/model.onnx", "model.onnx")]:
            dest = _MODEL_DIR / local
            if not dest.exists():
                print(f"[embedding] 下載 {local} …")
                urllib.request.urlretrieve(f"{_ONNX_REPO}/{remote}?download=true", dest)

        self.tok = Tokenizer.from_file(str(_MODEL_DIR / "tokenizer.json"))
        self.tok.enable_truncation(max_length=MODEL_MAX_TOKENS)
        self.tok.enable_padding(pad_id=1, pad_token="<pad>")  # XLM-R pad id = 1
        self.sess = ort.InferenceSession(
            str(_MODEL_DIR / "model.onnx"), providers=["CPUExecutionProvider"]
        )

    # ── chromadb EmbeddingFunction 介面 ──
    def name(self) -> str:
        return f"onnx-{EMBED_MODEL}"

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
        m = mask[..., None].astype(np.float32)
        summed = (out * m).sum(axis=1)
        counts = np.clip(m.sum(axis=1), 1e-9, None)
        return (summed / counts).tolist()


_cached_ef = None


def make_embedding_function():
    """優先 sentence-transformers，載入失敗（如 torch 被封鎖）則退回 ONNX。

    單例快取：rag.py（查詢）與 redflag.py（向量補充）共用同一份，
    避免 470MB 模型載入兩次（省 RAM 與啟動時間）。
    """
    global _cached_ef
    if _cached_ef is not None:
        return _cached_ef
    _cached_ef = _build_embedding_function()
    return _cached_ef


def _build_embedding_function():
    try:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        ef = SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
        print(f"[embedding] 後端：sentence-transformers（{EMBED_MODEL}）")
        return ef
    except Exception as e:
        print(f"[embedding] sentence-transformers 無法載入，改用 ONNX")
        print(f"[embedding]   原因：{type(e).__name__}: {str(e)[:100]}")
        ef = OnnxMiniLMEmbedder()
        print(f"[embedding]   已載入 {ef.name()}")
        return ef


# 供切割時精確量測 token 數（要真實長度，關掉截斷）
def make_token_counter():
    """回傳 count_tokens(text) -> int，用真正的模型 tokenizer。"""
    import urllib.request
    from tokenizers import Tokenizer

    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tok_path = _MODEL_DIR / "tokenizer.json"
    if not tok_path.exists():
        import urllib.request
        urllib.request.urlretrieve(f"{_ONNX_REPO}/tokenizer.json?download=true", tok_path)

    tok = Tokenizer.from_file(str(tok_path))
    tok.no_truncation()
    tok.no_padding()
    return lambda text: len(tok.encode(text).ids)
