# 评测 (P6)

`sample_dataset.jsonl` — 样例集 (3 条, 扩充中)。
运行: `python scripts/eval_ragas.py --dataset eval/sample_dataset.jsonl --project mae --api http://127.0.0.1:8081`

## 基线 (2026-09-04, 3 样本)

| 指标 | 值 | 说明 |
|---|---|---|
| faithfulness | 0.517 | 低分主因是评测口径: 枚举型问题无 conclusion 字段/冲突对峙不合并 → 空答案记 0 分; 样本集扩到 20+ 并按 Q 类型分流后才有可比性 |
| answer_relevancy | 0.935 | 答案与问题相关性良好 |

## 依赖注意

- ragas 0.3.1 + langchain 全家桶 0.3.x (ragas 0.4.x 与 langchain-community
  0.4.x 上游不兼容, 会炸 vertexai 导入)
- DeepSeek 无 embeddings API → 必须显式传本地 bge 嵌入, 且脚本内强制
  HF 离线 (HF 直连被墙, 曾实测卡死 28 分钟)
