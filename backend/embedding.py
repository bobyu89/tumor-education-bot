"""
共用嵌入層：模型註冊表 + ONNX 推論

兩種用途，各自固定模型：
  檢索（rag.py 查詢、scripts/index_vault.py 建庫）→ EMBED_MODEL，預設 multilingual-e5-small
  紅旗向量補充（redflag.py）                      → REDFLAG_MODEL，固定 paraphrase-multilingual-MiniLM-L12-v2

為什麼檢索換成 e5-small：scripts/eval_retrieval.py 以 79 題病人口語問題比較，
  MiniLM   Hit@1 0.66、Hit@3 0.84
  e5-small Hit@1 0.82、Hit@3 0.91（McNemar p = 0.004）
提升來自模型本身（同樣 128 token 段落也是 0.82），檔案大小與查詢速度和 MiniLM 相同。
為什麼紅旗不跟著換：紅旗向量門檻 0.85 是在 MiniLM 上校準的（test_redflag.py 的誤報檢查），
換模型等於換一套安全規則，必須重新校準驗證；這裡刻意分開。

模型一律用 onnxruntime 推論（向量與 sentence-transformers 相同：同權重、同 pooling），
不依賴 torch —— Windows 11 的 Smart App Control 會封鎖 torch，Docker 映像也不裝 torch。

換檢索模型：設環境變數 EMBED_MODEL（見 MODELS），再執行 scripts/index_vault.py；
索引會記錄建庫用的模型，模型不同時自動整個重建。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_MODELS_ROOT = Path(__file__).resolve().parent.parent / "models"


@dataclass(frozen=True)
class ModelSpec:
    name: str                  # models/<name>/，也記錄在向量索引的 metadata
    repo: str                  # Hugging Face 上的 ONNX 版本
    onnx_file: str             # repo 內的 ONNX 權重路徑
    max_seq: int               # 模型能讀的 token 上限（超過會被截斷）
    chunk_tokens: int          # 建庫時每段的 token 上限（含主題前綴與 passage 前綴）
    pooling: str = "mean"      # mean | cls
    query_prefix: str = ""     # e5 系列要求查詢與段落加不同前綴
    passage_prefix: str = ""
    min_similarity: float = 0.2  # 低於此 cosine 相似度的段落視為不相關（見各模型註解）


MODELS: dict[str, ModelSpec] = {
    # 舊檢索模型；現在只給紅旗向量補充用。相似度範圍很寬，0.2 幾乎不過濾。
    "paraphrase-multilingual-MiniLM-L12-v2": ModelSpec(
        name="paraphrase-multilingual-MiniLM-L12-v2",
        repo="Xenova/paraphrase-multilingual-MiniLM-L12-v2",
        onnx_file="onnx/model.onnx", max_seq=128, chunk_tokens=128,
        min_similarity=0.2,
    ),
    # 目前的檢索模型。e5 的相似度集中在 0.75–0.95；開發集範圍內問題的最高相似度最低 0.838，
    # 範圍外問題最高 0.879 —— 兩者重疊，門檻分不開，「知識庫沒有」靠模型依單張作答的規則判斷。
    # 0.80 只濾掉明顯無關的段落，不會擋到範圍內問題。
    "multilingual-e5-small": ModelSpec(
        name="multilingual-e5-small",
        repo="Xenova/multilingual-e5-small",
        onnx_file="onnx/model.onnx", max_seq=512, chunk_tokens=256,
        query_prefix="query: ", passage_prefix="passage: ",
        min_similarity=0.80,
    ),
}

DEFAULT_EMBED_MODEL = "multilingual-e5-small"
REDFLAG_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


def _configured_model() -> str:
    name = os.environ.get("EMBED_MODEL", "").strip() or DEFAULT_EMBED_MODEL
    if name not in MODELS:
        raise ValueError(f"EMBED_MODEL={name!r} 不在支援清單：{', '.join(MODELS)}")
    return name


EMBED_MODEL = _configured_model()


def get_spec(name: str | None = None) -> ModelSpec:
    return MODELS[name or EMBED_MODEL]


def _ensure_files(spec: ModelSpec, files: tuple[str, ...]) -> Path:
    import urllib.request
    d = _MODELS_ROOT / spec.name
    d.mkdir(parents=True, exist_ok=True)
    for remote in files:
        dest = d / Path(remote).name
        if not dest.exists():
            print(f"[embedding] 下載 {spec.name}/{dest.name} …")
            tmp = dest.with_suffix(dest.suffix + ".part")
            urllib.request.urlretrieve(f"https://huggingface.co/{spec.repo}/resolve/main/{remote}?download=true", tmp)
            tmp.replace(dest)   # 下載中斷不會留下半個檔案被當成完整模型
    return d


class OnnxEmbedder:
    """以 onnxruntime 執行 ModelSpec 指定的模型，輸出 L2 正規化向量（cosine 用）。

    embed_documents 加段落前綴、embed_query 加查詢前綴（e5 系列必須；MiniLM 前綴為空）。
    __call__ 等同 embed_documents，供 chromadb 與紅旗向量層直接呼叫。
    """

    def __init__(self, spec: ModelSpec) -> None:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.np, self.spec = np, spec
        d = _ensure_files(spec, ("tokenizer.json", spec.onnx_file))
        self.tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        self.tok.enable_truncation(max_length=spec.max_seq)
        pad_id = self.tok.token_to_id("<pad>")
        self.tok.enable_padding(pad_id=pad_id if pad_id is not None else 0, pad_token="<pad>")
        self.sess = ort.InferenceSession(str(d / Path(spec.onnx_file).name),
                                         providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self.sess.get_inputs()}

    # ── chromadb EmbeddingFunction 介面 ──
    def name(self) -> str:
        return f"onnx-{self.spec.name}"

    def is_legacy(self) -> bool:
        return True

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self._embed([self.spec.passage_prefix + t for t in input])

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self._embed([self.spec.query_prefix + t for t in input])

    def __call__(self, input: list[str]) -> list[list[float]]:
        return self.embed_documents(input)

    def _embed(self, texts: list[str], batch: int = 16) -> list[list[float]]:
        np = self.np
        out = []
        for i in range(0, len(texts), batch):
            encs = self.tok.encode_batch(texts[i:i + batch])
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.sess.run(None, feed)[0]
            if self.spec.pooling == "cls":
                vec = hidden[:, 0]
            else:   # mean pooling，只算真實 token
                m = mask[..., None].astype(np.float32)
                vec = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
            out.append(vec / (np.linalg.norm(vec, axis=1, keepdims=True) + 1e-9))
        return np.concatenate(out).tolist() if out else []


_cache: dict[str, OnnxEmbedder] = {}


def make_embedding_function(name: str | None = None) -> OnnxEmbedder:
    """取得模型的嵌入函式（每個模型只載入一次）。未指定 = 檢索模型 EMBED_MODEL。"""
    spec = get_spec(name)
    if spec.name not in _cache:
        _cache[spec.name] = OnnxEmbedder(spec)
        print(f"[embedding] 已載入 {spec.name}（ONNX）")
    return _cache[spec.name]


def make_token_counter(name: str | None = None):
    """回傳 count_tokens(text) -> int，用該模型真正的 tokenizer、不截斷（切段時量真實長度）。"""
    from tokenizers import Tokenizer
    spec = get_spec(name)
    d = _ensure_files(spec, ("tokenizer.json",))
    tok = Tokenizer.from_file(str(d / "tokenizer.json"))
    tok.no_truncation()
    tok.no_padding()
    return lambda text: len(tok.encode(text).ids)
