#!/usr/bin/env python3
"""RAGAS 评测 (P6): 对融合查询结果做 faithfulness / answer relevancy /
context precision / context recall 评估。

数据集: JSONL, 每行 {"question": ..., "reference": ...}
评测 LLM: 统一 .env 里的 LLM (EVAL_LLM_BINDING_*)。

用法:
  python scripts/eval_ragas.py --dataset eval/sample_dataset.jsonl \
      --project mae --api http://127.0.0.1:8080
依赖: pip install ragas (tuna 源)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx


def load_dataset(path: str) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


async def run_fusion_queries(api: str, project: str, dataset: list[dict]) -> list[dict]:
    results = []
    async with httpx.AsyncClient(timeout=600.0, trust_env=False) as c:
        for row in dataset:
            resp = await c.post(
                f"{api}/fusion/query",
                json={"query": row["question"], "project_id": project},
            )
            data = resp.json()
            conclusion = next(
                (r for r in data.get("results", []) if r.get("kind") == "conclusion"), None
            )
            answer = conclusion.get("conclusion", "") if conclusion else ""
            citations = conclusion.get("citations", []) if conclusion else []
            contexts = [c.get("excerpt", "") for c in citations if c.get("excerpt")]
            results.append({
                "question": row["question"],
                "answer": answer,
                "contexts": contexts,
                "reference": row["reference"],
            })
    return results


def evaluate_with_ragas(
    results: list[dict], llm_base: str, llm_key: str, llm_model: str,
    with_context: bool = False,
) -> dict:
    # 嵌入模型走本地缓存 (HF 直连被墙, 与 rag_engine 同约定)
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    import huggingface_hub.constants as _hc
    _hc.HF_HUB_OFFLINE = True
    from datasets import Dataset
    from langchain_openai import ChatOpenAI
    from ragas import evaluate
    from ragas.metrics import answer_relevancy, faithfulness

    llm = ChatOpenAI(
        model=llm_model, api_key=llm_key, base_url=llm_base, temperature=0,
    )
    metrics = [faithfulness, answer_relevancy]
    if with_context:
        from ragas.metrics import context_precision, context_recall

        metrics += [context_precision, context_recall]
    # ragas 0.3 的 evaluate 总会构造默认 OpenAIEmbeddings (DeepSeek 无
    # embeddings API 会炸), 必须显式传本地 bge 嵌入
    from langchain_huggingface import HuggingFaceEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper

    embedding = LangchainEmbeddingsWrapper(
        HuggingFaceEmbeddings(
            model_name="BAAI/bge-small-zh-v1.5",
            encode_kwargs={"normalize_embeddings": True},
        )
    )
    ds = Dataset.from_list(results)
    scores = evaluate(ds, metrics=metrics, llm=llm, embeddings=embedding)
    return scores.to_pandas().mean(numeric_only=True).to_dict()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--project", default="mae")
    parser.add_argument("--api", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--with-context", action="store_true",
        help="启用 context_precision/context_recall (需本地 bge 嵌入)",
    )
    args = parser.parse_args()

    llm_base = os.environ.get("EVAL_LLM_BINDING_HOST", os.environ.get("CRUCIBLE_LLM_BASE", "https://api.deepseek.com/v1"))
    llm_key = os.environ.get("EVAL_LLM_BINDING_API_KEY", os.environ.get("CRUCIBLE_LLM_API_KEY", ""))
    llm_model = os.environ.get("EVAL_LLM_MODEL", os.environ.get("CRUCIBLE_LLM_MODEL", "deepseek-chat"))

    dataset = load_dataset(args.dataset)
    results = asyncio.run(run_fusion_queries(args.api, args.project, dataset))
    print(f"样本数: {len(results)}; 有引用的样本: {sum(1 for r in results if r['contexts'])}")
    scores = evaluate_with_ragas(
        results, llm_base, llm_key, llm_model, with_context=args.with_context
    )
    print("RAGAS 平均分:")
    print(json.dumps(scores, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
