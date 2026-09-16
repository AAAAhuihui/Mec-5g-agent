# 项目指标验证

> 本文保留早期字符切分阶段的验证命令与结果。旧 Chunk 索引不适用于当前结构化切分；当前复现入口与指标口径请看 [检索测评说明](retrieval_evaluation.md)。

运行以下命令，验证项目中可由代码确定的边界与安全行为：

```powershell
.\.venv\Scripts\python.exe scripts\verify_project_metrics.py
```

如需让 CI 或其他工具解析结果：

```powershell
.\.venv\Scripts\python.exe scripts\verify_project_metrics.py --json
```

验证范围包括：

- 文档切分的 800 字符窗口和 120 字符重叠；
- RAG Top-K、ReAct 步数、工具调用、审批和无进展上限；
- 同一命令连续两次取得相同结果后，第三次调用被拦截；
- 会话记忆的短期消息、操作记录、摘要和重要事件边界；
- K8s MCP 的日志、时间窗口和 Event 限额；
- K8s MCP Role 的只读最小 RBAC 策略。

该工具不输出 RAG 准确率、故障诊断成功率、延迟、吞吐量或成本。这些指标需要额外准备带标准答案的数据集、可复现的故障环境或已部署服务的运行压测数据。

## 离线 RAG 检索评测

项目提供了 20 条带标签的 MEC/5G 检索问题，位于 `data/evals/mec_5g_cases.json`。每条问题指定期望来源文档和必须出现在 Top-K 证据中的领域术语。

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_rag.py --source-dir examples
```

脚本会输出：

- `Recall@6`：期望来源是否出现在 Top-6 检索结果中；
- `Evidence usable rate`：Top-6 证据是否覆盖该问题标注的全部必要术语。

该评测不调用 LLM API，只衡量当前混合检索与重排序链路；不可将其表述为“回答准确率”或“诊断成功率”。如需更完整报告，可追加 `--json` 参数保存每个问题的命中结果。

`Recall@K` 只有在“知识库 chunk 数量大于 K”时才有区分度。当前 `examples/` 中只有 3 个 chunk，因此使用默认 `Top-K=6` 时，脚本会提示该结果不适合写入简历。应以完整知识库运行评测，或先用 `--top-k 1` 做小样本冒烟验证。

## TS 29.522 的 200 条 Chunk 级评测

`data/evals/ts29522_chunk_cases_200.json` 包含 200 条基于
`data/raw_docs/29522-gh0.pdf` 生成的问题。每条问题标注一个可直接支持答案的
`expected_chunk_indexes`，因此即使知识库只有一个来源文件，也可以在 853 个
Chunk 中计算有区分度的 Chunk 级 `Recall@K` 和 `MRR@K`。

```powershell
python scripts/evaluate_rag.py `
  --source-dir data/raw_docs `
  --cases data/evals/ts29522_chunk_cases_200.json `
  --top-k 6 `
  --vector-candidate-k 40 `
  --bm25-candidate-k 40 `
  --fused-candidate-k 30 `
  --reranker-backend lightweight
```

评测会分别输出向量、BM25、两路原始并集以及 RRF 融合后的候选 Recall，
用来区分单路召回、融合截断和最终重排问题。当前 200 条数据的结果为：
`Vector Recall@40=67.5%`、`BM25 Recall@40=74.5%`、
`Raw Union Recall=86.5%`、`Candidate Recall@30=79.0%`、
`Recall@6=64.5%`、`MRR@6=0.4208`。

启用本地 Cross-Encoder：

```powershell
$env:RERANKER_MODEL='D:\实习\Agent\models\bge-reranker-v2-m3'
$env:RERANKER_DEVICE='cpu'
$env:RERANKER_ALLOW_DOWNLOAD='false'
python scripts/evaluate_rag.py `
  --source-dir data/raw_docs `
  --cases data/evals/ts29522_chunk_cases_200.json `
  --top-k 6 `
  --vector-candidate-k 40 `
  --bm25-candidate-k 40 `
  --fused-candidate-k 30 `
  --reranker-backend cross-encoder
```

建议分别执行 `--top-k 1`、`--top-k 3` 和 `--top-k 6`。评测只衡量
检索与重排，不代表最终答案正确率。题目由模型辅助生成并通过结构化规则
校验，正式用于简历前仍应人工抽查题意和 Gold Chunk，尤其是重复度较高的
HTTP 状态码与 OpenAPI Schema 题目。

如PDF版本或切分参数发生变化，Chunk序号也会变化，需要重新生成并人工复核：

```powershell
python scripts/generate_pdf_chunk_eval_cases.py `
  --source-dir data/raw_docs `
  --output data/evals/ts29522_chunk_cases_200.json `
  --count 200
```
