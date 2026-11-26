"""
檢索品質評估：比較嵌入模型與檢索方法，找出病人口語問題能否找到正確的衛教單張。

測試集：backend/retrieval_eval.json（開發者撰寫的開發集，不是臨床驗證集）
指標（單張層級）：
  Hit@1   第一個檢索到的段落屬於正確單張
  Hit@3   前 5 個段落中，前 3 個不同單張包含正確單張（≈ 模型拿得到正確資料）
  MRR     正確單張在單張排名中的倒數排名平均
  OOS     範圍外問題與範圍內問題的最高相似度能否分開（AUC），用來校準「無來源」門檻

執行：python -X utf8 scripts/eval_retrieval.py [模型名稱[@段落token上限] ...]
      結論與歷次結果：docs/knowledge/檢索評估.md
      模型名稱見 MODELS；首次執行會下載模型到 models/
"""
from __future__ import annotations

import json
import math
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))
import index_vault as iv  # noqa: E402

EVAL = json.loads((ROOT / "backend" / "retrieval_eval.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class ModelSpec:
    name: str                 # models/<name>/ 目錄名稱
    repo: str                 # Hugging Face repo（ONNX 版）
    onnx_file: str            # repo 內的 onnx 檔路徑
    max_tokens: int           # 段落 token 上限（含前綴）
    pooling: str = "mean"     # mean | cls
    query_prefix: str = ""
    passage_prefix: str = ""


MODELS = {
    "minilm": ModelSpec("paraphrase-multilingual-MiniLM-L12-v2", "Xenova/paraphrase-multilingual-MiniLM-L12-v2",
                        "onnx/model.onnx", 128),
    "e5-small": ModelSpec("multilingual-e5-small", "Xenova/multilingual-e5-small", "onnx/model.onnx", 512,
                          query_prefix="query: ", passage_prefix="passage: "),
    "e5-small-int8": ModelSpec("multilingual-e5-small", "Xenova/multilingual-e5-small", "onnx/model_quantized.onnx",
                               512, query_prefix="query: ", passage_prefix="passage: "),
    "e5-base-int8": ModelSpec("multilingual-e5-base", "Xenova/multilingual-e5-base", "onnx/model_quantized.onnx",
                              512, query_prefix="query: ", passage_prefix="passage: "),
    "bge-m3-int8": ModelSpec("bge-m3", "Xenova/bge-m3", "onnx/model_quantized.onnx", 512,
                             pooling="cls"),
}


class OnnxEmbedder:
    def __init__(self, spec: ModelSpec):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.np, self.spec = np, spec
        d = ROOT / "models" / spec.name
        d.mkdir(parents=True, exist_ok=True)
        for remote in ("tokenizer.json", spec.onnx_file):
            dest = d / Path(remote).name
            if not dest.exists():
                print(f"  下載 {spec.repo}/{remote} …")
                urllib.request.urlretrieve(f"https://huggingface.co/{spec.repo}/resolve/main/{remote}", dest)
        self.tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        self.tok.enable_truncation(max_length=spec.max_tokens)
        self.tok.enable_padding()
        self.count_tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        self.count_tok.no_truncation()
        self.count_tok.no_padding()
        self.sess = ort.InferenceSession(str(d / Path(spec.onnx_file).name), providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}

    def count_tokens(self, text: str) -> int:
        return len(self.count_tok.encode(text).ids)

    def embed(self, texts: list[str], batch: int = 16):
        np = self.np
        out = []
        for i in range(0, len(texts), batch):
            enc = self.tok.encode_batch(texts[i:i + batch])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.sess.run(None, feed)[0]
            if self.spec.pooling == "cls":
                vec = hidden[:, 0]
            else:
                m = mask[..., None].astype(np.float32)
                vec = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
            out.append(vec)
        v = np.concatenate(out)
        return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)   # 一律正規化後用 cosine


def chunks_for(emb: OnnxEmbedder):
    """以該模型的 tokenizer 與長度上限切段（長上下文模型可放進較完整的段落）。"""
    prefix_tokens = emb.count_tokens(emb.spec.passage_prefix) - 2 if emb.spec.passage_prefix else 0  # 扣掉 <s> </s>
    iv.MODEL_MAX_TOKENS = emb.spec.max_tokens - prefix_tokens
    iv.get_token_counter = lambda: emb.count_tokens
    chunks, _ = iv.build_chunks()
    return [c for c in chunks if c.dept == "hemato-oncology"]


# ── 關鍵字檢索：中文字元 bigram 的 BM25（不需斷詞套件）──────────────
def bigrams(text: str) -> list[str]:
    s = re.sub(r"[^一-鿿A-Za-z0-9]", "", text)
    return [s[i:i + 2] for i in range(len(s) - 1)] or list(s)


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.toks = [bigrams(d) for d in docs]
        self.k1, self.b = k1, b
        self.avg = sum(map(len, self.toks)) / len(self.toks)
        df = Counter(t for ts in self.toks for t in set(ts))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(ts) for ts in self.toks]

    def scores(self, q: str) -> list[float]:
        qt = bigrams(q)
        out = []
        for tf, ts in zip(self.tf, self.toks):
            s = 0.0
            for t in qt:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(ts) / self.avg))
            out.append(s)
        return out


def rank(scores) -> list[int]:
    return sorted(range(len(scores)), key=lambda i: -scores[i])


def rrf(*rankings, k: int = 60) -> list[int]:
    agg = defaultdict(float)
    for r in rankings:
        for pos, i in enumerate(r):
            agg[i] += 1 / (k + pos + 1)
    return sorted(agg, key=lambda i: -agg[i])


def leaflet_order(chunk_rank: list[int], codes: list[str]) -> list[str]:
    seen = []
    for i in chunk_rank:
        if codes[i] not in seen:
            seen.append(codes[i])
    return seen


def metrics(rankings: list[list[int]], codes: list[str]) -> dict:
    h1 = h3 = mrr = 0
    misses = []
    for item, r in zip(EVAL["in_scope"], rankings):
        ok = {item["target"], *item.get("alt", [])}
        order = leaflet_order(r, codes)
        top5_leaflets = leaflet_order(r[:5], codes)
        h1 += codes[r[0]] in ok
        h3 += any(c in ok for c in top5_leaflets[:3])
        pos = next((i for i, c in enumerate(order) if c in ok), None)
        mrr += 1 / (pos + 1) if pos is not None else 0
        if codes[r[0]] not in ok:
            misses.append(f"{item['q']}→{codes[r[0]]}")
    n = len(EVAL["in_scope"])
    return {"hit1": h1 / n, "hit3": h3 / n, "mrr": mrr / n, "misses": misses}


def auc(pos: list[float], neg: list[float]) -> float:
    """範圍內最高相似度（pos）高於範圍外（neg）的機率。"""
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return wins / (len(pos) * len(neg))


def resolve(model_key: str) -> ModelSpec:
    """「e5-small@128」= 同一模型，但段落上限改為 128 token（用來分開模型與段落長度的影響）。"""
    from dataclasses import replace
    name, _, cap = model_key.partition("@")
    spec = MODELS[name]
    return replace(spec, max_tokens=int(cap)) if cap else spec


def mcnemar_p(a: list[bool], b: list[bool]) -> float:
    """成對比較兩種方法的 Hit@1（精確二項檢定，雙尾）。"""
    n01 = sum(x and not y for x, y in zip(a, b))
    n10 = sum(y and not x for x, y in zip(a, b))
    n = n01 + n10
    if n == 0:
        return 1.0
    k = min(n01, n10)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def evaluate(model_key: str) -> dict:
    import time
    spec = resolve(model_key)
    emb = OnnxEmbedder(spec)
    chunks = chunks_for(emb)
    codes = [c.code for c in chunks]
    np = emb.np
    P = emb.embed([spec.passage_prefix + c.text for c in chunks])
    qs = [x["q"] for x in EVAL["in_scope"]]
    t0 = time.perf_counter()
    Q = emb.embed([spec.query_prefix + q for q in qs], batch=1)
    ms_per_query = (time.perf_counter() - t0) * 1000 / len(qs)
    O = emb.embed([spec.query_prefix + q for q in EVAL["out_of_scope"]])
    S, SO = Q @ P.T, O @ P.T
    bm = BM25([c.text for c in chunks])
    dense = [rank(s) for s in S]
    kw = [rank(bm.scores(q)) for q in qs]
    hybrid = [rrf(d, k) for d, k in zip(dense, kw)]
    hits = [codes[r[0]] in {x["target"], *x.get("alt", [])} for x, r in zip(EVAL["in_scope"], dense)]
    res = {"model": model_key, "chunks": len(chunks), "ms_per_query": ms_per_query, "dense_hits": hits,
           "dense": metrics(dense, codes), "hybrid": metrics(hybrid, codes),
           "keyword": metrics(kw, codes),
           "oos_auc": auc(S.max(1).tolist(), SO.max(1).tolist()),
           "in_min": float(S.max(1).min()), "in_p10": float(np.percentile(S.max(1), 10)),
           "oos_max": float(SO.max(1).max()), "oos_p90": float(np.percentile(SO.max(1), 90))}
    return res


def main():
    keys = sys.argv[1:] or ["minilm", "e5-small@256"]   # 預設：原本 vs 目前正式設定
    rows = []
    for k in keys:
        print(f"\n=== {k} ===")
        r = evaluate(k)
        rows.append(r)
        for m in ("dense", "keyword", "hybrid"):
            x = r[m]
            print(f"  {m:8s} Hit@1 {x['hit1']:.2f}  Hit@3 {x['hit3']:.2f}  MRR {x['mrr']:.2f}")
        print(f"  單句查詢 {r['ms_per_query']:.0f} ms；段落數 {r['chunks']}；範圍內外可分性 AUC {r['oos_auc']:.2f}"
              f"（範圍內最高相似度 p10 {r['in_p10']:.3f}，範圍外 p90 {r['oos_p90']:.3f}）")
        print("  dense 未命中：", "；".join(r["dense"]["misses"][:12]))
        print("  hybrid 未命中：", "；".join(r["hybrid"]["misses"][:12]))
    if len(rows) > 1:
        base = rows[0]
        print(f"\n成對比較（dense Hit@1，以 {base['model']} 為基準，McNemar 精確檢定）")
        for r in rows[1:]:
            print(f"  {r['model']:16s} {r['dense']['hit1']:.2f} vs {base['dense']['hit1']:.2f}"
                  f"  p = {mcnemar_p(r['dense_hits'], base['dense_hits']):.3f}")
    out = ROOT / "backend" / "retrieval_eval_results.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n結果 → {out}")


if __name__ == "__main__":
    main()
