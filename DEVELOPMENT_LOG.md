# MEC/5G RAG Agent 开发日志

## 2026-06-17

### 一、项目目标

本项目目标是实现一个面向 MEC/5G 信令场景的智能问答 Agent MVP。系统不是普通聊天机器人，而是一个具备文档入库、混合检索、检索质量评估、答案生成、自检、多轮会话和命令行交互能力的 RAG Agent。

主要面向的问题包括：

- NEF、NEF_NBI、Traffic Influence 相关流程
- SMF、UPF、PDR/FAR、N3/N6 分流链路
- MEP、MEPM、MEC APP 的组件关系
- Traffic Influence 下发成功但流量未到 MEC APP 的故障排查
- 后续扩展到 K8s、日志、代码搜索、OpenAPI Schema 工具调用

### 二、初始项目搭建

根据需求从零创建项目目录 `mec_5g_rag_agent/`，采用 FastAPI 作为后端服务框架，主要目录如下：

```text
mec_5g_rag_agent/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── schemas.py
│   ├── llm/
│   ├── rag/
│   ├── agent/
│   ├── tools/
│   └── routers/
├── data/
│   ├── raw_docs/
│   └── chroma_db/
├── examples/
├── scripts/
├── tests/
├── cli.py
├── requirements.txt
├── README.md
└── DEVELOPMENT_LOG.md
```

设计时优先保证 MVP 可运行，因此所有核心链路均提供兜底逻辑：

- DeepSeek API 不可用时，答案生成、CRAG、自检走规则兜底。
- Chroma 不可用时，向量库降级到本地 JSON 存储。
- 文件写入受限时，向量数据降级到进程内存存储。
- sentence-transformers 不可用时，Embedding 降级为 Hashing Embedding。

### 三、配置与 DeepSeek 接入

新增 `app/config.py`，统一读取环境变量：

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`
- `EMBEDDING_MODEL`
- `CHROMA_DIR`
- `RAW_DOCS_DIR`
- `RAG_TOP_K`

新增 `.env.example`，避免在代码中硬编码 API Key。

新增 `app/llm/deepseek_client.py`，使用 OpenAI SDK 兼容方式调用 DeepSeek：

- `chat()`：普通文本调用
- `json_chat()`：用于 CRAG、自检等 JSON 结构化判断

如果没有配置 API Key 或调用失败，客户端返回 `None`，由上层模块自动走规则兜底。

### 四、RAG 文档入库能力

最初支持 Markdown 和 txt 文档，后续扩展到 PDF 和 docx。

当前 `app/rag/document_loader.py` 支持：

- `.md`
- `.markdown`
- `.txt`
- `.pdf`
- `.docx`

PDF 使用 `pypdf` 解析，docx 使用 `python-docx` 解析。若依赖缺失，会给出清晰错误提示：

```text
读取 PDF 需要安装依赖：pip install pypdf
读取 docx 需要安装依赖：pip install python-docx
```

新增 `app/rag/text_splitter.py`，将文档切分成片段。切分策略为固定长度加 overlap，并优先在段落或句号附近截断，尽量减少语义破坏。

新增示例文档：

- `data/raw_docs/mec_5g_intro.md`
- `data/raw_docs/traffic_influence.md`
- `data/raw_docs/troubleshooting.md`

这些文档覆盖了 MEC/5G 组件关系、Traffic Influence 流程、分流失败排查路径。

### 五、Embedding 与向量库实现

新增 `app/rag/embedding.py`：

- 优先使用 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- 不可用时降级为 Hashing Embedding

新增 `app/rag/vector_store.py`：

- 优先使用 Chroma `PersistentClient`
- Chroma 不可用时降级到 `data/chroma_db/fallback_store.json`
- 若当前运行环境不允许 Python 写文件，再降级为进程内存 `_MEMORY_RECORDS`

这样做的目的是保证即使依赖不完整，也能在 MVP 阶段跑通 ingest 和 chat 流程。

### 六、混合检索与重排

新增 `app/rag/keyword_search.py`，实现轻量关键词检索。关键词包括：

- MEC、5G、5GC
- NEF、NEF_NBI、Traffic Influence
- SMF、UPF、PDR、FAR
- MEP、MEPM、MEC APP
- N3、N6、分流、信令、核心网、边缘计算等

新增 `app/rag/retriever.py`，实现 Hybrid Retriever：

1. 对多路 query 执行向量检索
2. 同时执行关键词检索
3. 合并结果
4. 按 source + chunk_index 去重
5. 返回 top_k 文档片段

新增 `app/rag/reranker.py`，使用向量分数和关键词重叠度进行简单重排。

新增 `app/rag/evidence_compressor.py`，将检索片段压缩成结构化 facts，供答案生成和自检使用。

### 七、Agent 状态设计

新增 `app/agent/state.py`，定义 `RAGState`，字段包括：

- `question`
- `intent`
- `domain_entities`
- `need_retrieval`
- `queries`
- `retrieved_docs`
- `reranked_docs`
- `retrieval_grade`
- `retrieval_score`
- `retrieval_retry_count`
- `evidence_facts`
- `draft_answer`
- `self_check_result`
- `answer_rewrite_count`
- `final_answer`

该状态结构为后续迁移到 LangGraph 留出了空间。

### 八、问题分类与 Query 改写

新增 `app/agent/classifier.py`，基于规则判断问题意图：

- `concept_qa`
- `signaling_flow`
- `api_generation`
- `code_analysis`
- `troubleshooting`
- `general`

只要问题包含 MEC/5G 领域关键词，就设置 `need_retrieval=True`。

新增 `app/agent/query_rewriter.py`，根据原始问题生成多路检索 query。例如针对：

```text
NEF 返回成功，但流量没有进入 MEC APP，可能是什么原因？
```

会生成：

```text
NEF_NBI Traffic Influence 分流策略 下发 流程
NEF 返回成功 UPF 未生效 原因
SMF UPF PDR FAR 策略安装
UPF N6 本地分流 MEC APP
MEP Traffic Rule MEC APP 流量规则
```

### 九、CRAG 检索质量评估

新增 `app/agent/crag_evaluator.py`，用于判断检索结果是否足以回答问题。

输出字段：

- `retrieval_grade`
- `score`
- `reason`
- `missing_evidence`
- `next_queries`

支持四类结果：

- `correct`
- `ambiguous`
- `incorrect`
- `empty`

实现策略：

1. 优先执行规则评估，计算问题实体和证据实体覆盖度。
2. 如果 DeepSeek 可用，再调用 LLM 进行辅助判断。
3. 若 LLM 评估失败，使用规则评估结果。
4. 对“规则明确 correct，LLM 仅保守 ambiguous 且无缺失证据”的情况，合并为 `correct`，避免接口显示 `ambiguous` 但自检又 `supported` 的矛盾体验。

### 十、答案生成与 Self-RAG 自检

新增 `app/agent/answer_generator.py`，固定答案格式：

```text
结论：
...

依据：
1. ...

详细解释：
...

涉及组件：
...

下一步建议：
...
```

答案生成优先调用 DeepSeek，失败时使用规则模板兜底。

后续发现概念类问题，例如：

```text
MEPM 在 MEC 中的作用是什么？
```

不应该套用故障排查模板，因此将 fallback 答案拆成：

- 概念解释模板
- 故障排查模板
- 通用领域模板

新增 `app/agent/self_checker.py`，实现 Self-RAG 风格自检：

- 判断答案是否有证据支持
- 检查是否出现“一定”“必然”“肯定”等强结论
- 判断是否需要补充检索
- 输出 `supported`、`partial`、`unsupported`

### 十一、Workflow 编排

新增 `app/agent/workflow.py`，串联完整流程：

1. classify question
2. generate queries
3. retrieve docs
4. rerank docs
5. CRAG evaluate
6. incorrect/ambiguous 时最多重试 2 次
7. compress evidence
8. generate answer
9. self check
10. partial 时最多重写 1 次
11. unsupported 时补充检索
12. 输出 final answer

核心常量：

```python
MAX_RETRIEVAL_RETRY = 2
MAX_ANSWER_REWRITE = 1
```

所有循环均有次数限制，避免死循环。

### 十二、FastAPI 接口

新增两个接口：

#### POST `/ingest`

导入 `data/raw_docs/` 下的文档。

请求：

```json
{
  "source_dir": "data/raw_docs"
}
```

返回：

```json
{
  "success": true,
  "message": "ingested 3 documents",
  "chunks": 3
}
```

#### POST `/chat`

执行 RAG Agent 问答。

当前请求支持：

```json
{
  "conversation_id": "可选",
  "question": "用户问题",
  "include_trace": false
}
```

返回包括：

- `conversation_id`
- `standalone_question`
- `answer`
- `intent`
- `retrieval_grade`
- `self_check`
- `evidence`
- `trace`

其中 `trace` 默认不返回内容，只有 `include_trace=true` 时返回可审计轨迹。

### 十三、多轮会话能力

新增 `app/agent/memory.py`，实现进程内会话记忆：

```python
_CONVERSATIONS: dict[str, ConversationMemory] = {}
```

每个会话保存：

- conversation_id
- messages
- summary
- confirmed_facts

当前为 MVP 设计，历史记录只保存在内存中，服务重启后会丢失。

新增 `app/agent/contextualizer.py`，用于将追问改写成独立问题。例如：

第一轮：

```text
NEF 返回成功但流量没到 MEC APP，为什么？
```

第二轮：

```text
那应该先查哪里？
```

会被改写为：

```text
在上一轮问题“NEF 返回成功但流量没到 MEC APP，为什么？”的上下文中，那应该先查哪里？
```

如果 DeepSeek 可用，优先调用模型改写；否则走规则兜底。

### 十四、CLI 命令行会话

新增 `cli.py`，支持命令行会话：

```bash
python cli.py chat
```

进入后显示：

```text
/chat>
```

支持命令：

```text
/help
/history
/new
/debug
/trace
/exit
```

CLI 默认只显示 Agent 的最终回复，不显示调试信息。这样更接近正常聊天体验。

可通过 `/debug` 显示：

- conversation_id
- standalone_question
- intent
- retrieval_grade
- faithfulness

可通过 `/trace` 显示可审计推理轨迹。

### 十五、可审计推理轨迹

用户希望查看“思维链”。考虑到不应暴露模型原始隐藏推理，最终实现为可审计执行轨迹，而不是原始 chain-of-thought。

开启方式：

```text
/trace
```

或 API 请求：

```json
{
  "question": "NEF 返回成功但流量没到 MEC APP，为什么？",
  "include_trace": true
}
```

trace 包含：

- 原始问题
- 独立问题
- 分类结果
- 检索 query
- 检索评分
- 检索重试次数
- 证据来源
- 证据事实
- 自检结果
- 答案重写次数

trace 中明确说明：

```text
这是可审计执行轨迹，不是模型原始隐藏思维链。
```

### 十六、工具模块骨架

根据需求新增工具接口骨架：

#### `app/tools/api_schema_tool.py`

- `get_api_schema(api_name: str)`
- `validate_payload(api_name: str, payload: dict)`
- `generate_payload(api_name: str, requirement: str)`

#### `app/tools/code_search_tool.py`

- `search_code(query: str, repo_path: str)`

#### `app/tools/log_search_tool.py`

- `search_logs(query: str, log_dir: str)`

#### `app/tools/k8s_tool.py`

- `query_pods(namespace: str)`
- `query_logs(namespace: str, pod_name: str)`

目前这些工具返回 mock/TODO 数据，保证系统能运行。后续可以接入真实 K8s、日志系统、代码索引和 OpenAPI Schema 仓库。

### 十七、调试与问题修复记录

#### 1. 附件需求乱码

最初读取需求文件时，PowerShell 按错误编码输出了乱码。后续改用 UTF-8 方式读取文件，确认了完整中文需求。

#### 2. Python 写 `__pycache__` 权限受限

运行 `compileall` 时，当前环境拒绝写 `__pycache__`。改为设置：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
```

并使用 AST 解析方式做语法检查。

#### 3. Chroma/JSON 写入权限问题

在某次端到端测试中，Python 无法写入 `data/chroma_db/fallback_store.json`。为保证 MVP 可运行，在 `VectorStore` 中增加进程内存兜底。

#### 4. uvicorn 后台启动不稳定

前台运行 uvicorn 正常，但通过某些 PowerShell 后台启动方式会立即退出。最终通过 FastAPI `TestClient` 验证应用接口逻辑，确认 `/health`、`/ingest`、`/chat` 路由正常。

#### 5. PowerShell 中文乱码

PowerShell 中中文输出可能出现乱码。建议执行：

```powershell
chcp 65001
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
```

CLI 中也增加了 `sys.stdin/stdout/stderr.reconfigure(encoding="utf-8")`，尽量降低乱码概率。

#### 6. Python 3.9 兼容问题

虽然需求是 Python 3.10+，但当前 `.venv` 实际是 Python 3.9。Pydantic 在解析 `str | None` 时失败，因此将部分模型字段改为 `Optional[str]`，提高当前环境兼容性。

#### 7. 概念问题误用故障模板

早期 fallback 答案对所有领域问题都偏故障排查口吻，导致“MEPM 的作用是什么”这类问题回答不自然。后续将答案模板按 intent 拆分，修复该问题。

#### 8. Swagger 返回内容过长

早期 `/chat` 返回 5 条 evidence，且每条可能是整块文档片段，Swagger 展示过长。后续将 evidence 限制为 3 条，每条最多 600 字。

### 十八、当前验证结果

已完成的验证包括：

```text
parsed 36 python files
```

多轮会话验证：

```text
第一轮：NEF 返回成功但流量没到 MEC APP，为什么？
第二轮：那应该先查哪里？
```

第二轮会被改写为：

```text
在上一轮问题“NEF 返回成功但流量没到 MEC APP，为什么？”的上下文中，那应该先查哪里？
```

并返回：

```text
intent=troubleshooting
retrieval_grade=correct
faithfulness=supported
```

PDF/docx 依赖已在环境中安装：

```text
pypdf 6.13.2
python-docx 1.2.0
```

### 十九、当前限制

当前 MVP 仍有一些限制：

1. 会话历史只保存在进程内存中，服务重启后丢失。
2. Chroma 和 sentence-transformers 如果未安装或模型未下载，会走 fallback，检索效果弱于真实向量模型。
3. CRAG 和 Self-RAG 的规则兜底仍偏简单，复杂问题最好使用 DeepSeek 参与判断。
4. PDF 解析依赖文本层，如果 PDF 是扫描件图片，需要后续接 OCR。
5. docx 当前只读取段落和表格文本，不处理图片、页眉页脚和复杂样式。
6. 工具模块仍是 mock，尚未接入真实 K8s、日志、代码仓库和 OpenAPI Schema。
7. CLI 通过 FastAPI TestClient 在本地进程调用应用，不依赖已启动的 uvicorn 服务。

### 二十、后续计划

建议下一阶段按以下顺序演进：

1. 持久化会话历史
   - 使用 SQLite 保存 conversations 和 messages
   - 支持按 conversation_id 恢复历史

2. 优化检索质量
   - 安装并固定 sentence-transformers 模型
   - 使用 Chroma 持久化向量库
   - 增加 BM25 或 jieba 中文分词关键词检索

3. 增强 CRAG
   - 将 missing evidence 细化为组件、接口、字段、日志四类
   - 对 ambiguous 场景自动生成更精准补充 query

4. 增强文档解析
   - PDF OCR
   - docx 表格结构保留
   - 支持代码文件、YAML、JSON、OpenAPI Schema

5. 接入真实工具
   - K8s Pod 查询
   - K8s 日志查询
   - 本地代码搜索
   - 日志目录检索
   - OpenAPI Schema payload 生成与校验

6. 工作流迁移到 LangGraph
   - 将 classify、rewrite、retrieve、evaluate、generate、check 变成显式节点
   - 更清晰地管理 retry 和 tool call

7. 增加测试
   - 文档加载测试
   - 检索测试
   - workflow 测试
   - API 测试
   - CLI 行为测试

### 二十一、常用命令

安装依赖：

```powershell
pip install -r requirements.txt
```

启动服务：

```powershell
uvicorn app.main:app --reload
```

导入文档：

```powershell
$body = @{ source_dir = "data/raw_docs" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8000/ingest" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body $body
```

命令行会话：

```powershell
python cli.py chat
```

只看最终回复：

```text
/chat> MEPM 在 MEC 中的作用是什么？
```

显示可审计轨迹：

```text
/chat> /trace
/chat> NEF 返回成功但流量没到 MEC APP，为什么？
```

退出：

```text
/chat> /exit
```

## 2026-06-18

### 二十二、独立虚拟环境与真实向量库入库修复

本次将项目从 Anaconda base 环境迁移到项目内独立虚拟环境：

```text
D:\实习\Agent\mec_5g_rag_agent\.venv
```

目的：

1. 避免继续污染 Anaconda base 环境。
2. 固定 Chroma、sentence-transformers、Transformers、NumPy 等依赖版本。
3. 让项目真正使用 `sentence-transformers + Chroma`，而不是 fallback。

#### 1. 依赖版本固定

实际验证后发现：

- `chromadb 1.5.9` 在当前 Windows/Python 3.9 环境中，写入 384 维 sentence-transformers 向量时会出现 native 级直接退出，没有 Python traceback。
- `chromadb 0.5.23` 仍存在批量/连续 upsert 不稳定问题。
- `chromadb 0.4.24` 与 `numpy 2.x` 不兼容，会触发 `np.float_` 被移除的问题。
- `chromadb 0.4.24` 与 `posthog 6.x` 会出现 telemetry 接口不兼容警告。

因此将依赖固定为：

```text
numpy<2.0.0
chromadb==0.4.24
posthog<4.0.0
transformers>=4.41.0,<4.46.0
```

当前验证版本：

```text
chromadb 0.4.24
posthog 3.25.0
transformers 4.45.2
numpy 1.26.4
```

并通过：

```text
pip check
No broken requirements found.
```

#### 2. sentence-transformers 模型加载

默认模型仍为：

```text
sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

由于直接访问 `huggingface.co` 经常超时，模型下载时使用镜像：

```powershell
$env:HF_ENDPOINT='https://hf-mirror.com'
```

下载完成后，运行时默认使用本地缓存，并在代码中设置离线优先，避免每次启动都访问 Hugging Face：

```text
HF_HUB_OFFLINE=1
```

如确实需要重新下载模型，可显式设置：

```powershell
$env:EMBEDDING_ALLOW_DOWNLOAD='1'
$env:HF_ENDPOINT='https://hf-mirror.com'
```

#### 3. CLI ingest 优化

原先 `cli.py ingest` 通过 FastAPI `TestClient` 调 `/ingest`，长文档入库时缺少可见进度，容易误判为超时。

现改为 CLI 直接执行：

1. `load_documents()`
2. `split_documents()`
3. `VectorStore().add_documents()`

并新增：

```powershell
--batch-size
```

导入时会输出：

```text
loading documents from data/raw_docs ...
loaded documents: 1
split chunks: 853
embedding backend: sentence-transformers; vector backend: chroma
ingested chunks: 8/853
...
ingested chunks: 853/853
```

#### 4. Chroma 数据库重建

旧的 `data/chroma_db` 曾由多个 Chroma 版本创建或测试，存在 schema 不兼容和残留 segment 问题。

处理方式：

1. 备份旧 Chroma 目录。
2. 创建干净的 `data/chroma_db`。
3. 使用 `chromadb 0.4.24` 重新入库。

最终验证：

```text
embedding_backend sentence-transformers
vector_backend chroma
load_error None
```

Chroma collection：

```text
mec_5g_docs count 853
```

#### 5. data 目录清理

清理前存在多份测试和备份目录：

```text
chroma_db_backup_20260618_195937
chroma_db_legacy_chroma15
chroma_debug_db
chroma_debug_v05
```

清理后 `data/` 仅保留当前可运行所需内容：

```text
data/chroma_db
data/raw_docs
```

其中：

- `data/raw_docs` 保留原始 PDF。
- `data/chroma_db` 保留已完成入库的 Chroma 向量库。

清理后再次验证：

```text
count 853
```

#### 6. 当前入库状态

当前 `data/raw_docs` 中 PDF 已成功切分并入库：

```text
loaded documents: 1
split chunks: 853
chunks: 853
embedding_backend: sentence-transformers
vector_backend: chroma
```

因此当前 RAG 检索已不再走 JSON fallback，也不再使用 Hashing Embedding。
