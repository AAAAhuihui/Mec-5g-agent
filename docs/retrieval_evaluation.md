# 检索测评与复现

## 数据与指标口径

当前结构化切分使用 `data/raw_docs/29522-gh0.pdf`，对应标注为 `data/evals/ts29522_structured_cases_200.json`，保存的建库清单为 `data/evals/chroma_structured_v2_manifest.json`。默认切分预算 112 Token、重叠预算 16 Token；现有报告包含 1,671 个 Chunk。

每个问题可关联多个有效 Gold Chunk。最终 Top-K 的每个结果是一个由命中块及相邻块组成的窗口：

- `window_recall_at_k`：至少一个返回窗口包含任意 Gold Chunk 的问题占比，属于问题级 Hit@K 口径。
- `window_mrr_at_k`：第一个包含 Gold 的窗口排名倒数的平均值；未命中记 0。
- `evidence_usable_rate`：返回证据是否覆盖标注的必要术语，是规则代理指标，不是答案正确率。
- 同时记录向量、BM25、两路并集、RRF 截断及窗口扩展阶段的命中情况，用于定位召回损失。

`ts29522_chunk_cases_200.json` 对应旧字符切分索引，不可直接拿旧 Gold 序号验证新切分。更换 PDF、切分参数或 Tokenizer 后，应重新映射并人工核验 Gold。

## 运行

先按 README 准备环境与模型，确认没有降级到 Hashing Embedding。以下命令从项目根目录执行，不调用生成式 LLM，但会加载 Embedding；Cross-Encoder 测评还会加载重排模型。

200 条轻量重排：

```bash
python scripts/evaluate_rag.py --source-dir data/raw_docs --cases data/evals/ts29522_structured_cases_200.json --top-k 6 --reranker-backend lightweight --output reports/retrieval_lightweight_new.json
```

同一 50 条等距抽样分别运行两种重排：

```bash
python scripts/evaluate_rag.py --source-dir data/raw_docs --cases data/evals/ts29522_structured_cases_200.json --top-k 6 --limit 50 --sample even --reranker-backend lightweight --output reports/retrieval_lightweight_50_new.json
python scripts/evaluate_rag.py --source-dir data/raw_docs --cases data/evals/ts29522_structured_cases_200.json --top-k 6 --limit 50 --sample even --reranker-backend cross-encoder --output reports/retrieval_cross_encoder_50_new.json
```

Cross-Encoder 需设置 `RERANKER_MODEL`、`RERANKER_ALLOW_DOWNLOAD=false` 和 `RERANKER_FAIL_OPEN=false`；路径填自己的模型目录。向量 Top-40、BM25 Top-40、RRF Top-30、邻接半径 1 使用 `.env.example` 默认值。不要覆盖仓库原始报告。

报告需要核对实际 `embedding_backend`、`reranker_backend`、Chunk 数、样本 ID 和逐题结果。模型加载、推理耗时与检索效果应分别记录。

## 如何解释对比

- 200 条轻量结果与 50 条 Cross-Encoder 结果不能直接构成消融对比；应比较同一 50 条的两个版本。
- 切分前后窗口包含的文本量不同，Gold 映射也可能不同，变化不一定完全来自切分算法。旧固定长度基线是字符窗口，不应改称精确 Token 窗口。
- 离线评测使用内存精确向量排序，在线使用 Chroma，需另做索引一致性与延迟测试。
- 此脚本不测 ReAct 工具选取、CRAG 后续纠正、最终自检或回答正确率。
- 现有题目和 Gold 存在模型辅助构造及规则映射，应补充人工复核、独立留出集和错误案例。没有报告支持的数字不作为项目成绩。
