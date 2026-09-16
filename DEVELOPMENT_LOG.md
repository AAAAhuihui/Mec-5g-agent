# MEC/5G RAG Agent 开发日志

本文按开发时间保留历史设计与验证记录，不代表所有描述均适用于当前版本。当前入口、功能边界和可复现测评见 [README](README.md)。

## 2026-06-17

### 一、项目目标

本项目目标是实现一个面向 MEC/5G 信令场景的智能问答 Agent。系统具备文档入库、混合检索、检索质量评估、答案生成、自检、多轮会话和命令行交互能力。

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

初始设计优先保证核心流程可运行，因此提供以下兜底逻辑：

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

这样做的目的是在早期开发阶段，即使依赖不完整，也能验证 ingest 和 chat 流程。

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

该阶段的历史记录只保存在内存中，服务重启后会丢失；后续版本已引入 MySQL 持久化。

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

在某次端到端测试中，Python 无法写入 `data/chroma_db/fallback_store.json`。为支持文件写入受限的开发环境，在 `VectorStore` 中增加进程内存兜底。

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

该开发阶段仍有以下限制：

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

## 2026-06-19

### 二十三、工作流迁移到 LangGraph

本次将原有手写顺序流程迁移为 LangGraph 状态图，核心文件为：

```text
app/agent/workflow.py
app/agent/state.py
```

新的 `RAGState` 在原有字段基础上增加了工具选择相关状态：

```text
selected_tool
tool_args
tool_selection_source
tool_selection_reason
```

LangGraph 节点包括：

```text
classify
select_tool
tool_selection_failed
general_answer
code_analysis
rewrite_queries
retrieve
compress_evidence
generate_answer
self_check
revise_answer
add_self_check_query
finalize
```

这样后续可以更清晰地表达分支、循环、重试和工具调用流程，也为 checkpoint、ReAct、多工具编排留下扩展空间。

### 二十四、引入 OpenAI-compatible Tool Calling

新增 `DeepSeekClient.tool_call()`，使用 OpenAI SDK 兼容的 `tools/tool_calls` 方式让大模型选择工具。

工具定义统一迁移到：

```text
app/tools/definitions.py
```

当前工具包括：

```text
code_search
rag_retrieve
general_answer
```

设计原则：

1. 工具选择必须由 LLM 完成。
2. 不再使用规则兜底强行选择工具。
3. 如果 LLM 未返回合法 tool_calls，则进入 `tool_selection_failed`，本轮不执行工具。

这样可以更接近真实 Agent 的工具路由模式，也方便后续扩展日志检索、K8s 查询、OpenAPI Schema 等工具。

### 二十五、代码搜索工具真实化

完善 `app/tools/code_search_tool.py`，将代码定位相关逻辑从 workflow 中抽离，避免 workflow 文件过度臃肿。

当前支持：

```text
run_code_search()
search_code()
extract_code_path()
extract_code_query()
code_results_to_docs()
code_results_to_facts()
build_code_search_answer()
```

已验证的问题形式包括：

```text
在 app/agent/workflow.py 中搜索 RAGWorkflow
在 D:\HiAI\Hello.py 中搜索 print
在"D:\HiAI\Hello.py"中搜索print
```

代码搜索命中后会被转换成 RAG 统一使用的 `docs/facts` 结构，因此可以复用后续证据展示、trace 和答案输出格式。

### 二十六、流程图文档更新

新增 LangGraph 流程图文档：

```text
docs/langgraph_workflow.mmd
docs/langgraph_workflow.svg
docs/langgraph_workflow_simple.mmd
docs/langgraph_workflow_simple.svg
```

其中详细图描述完整 LangGraph 节点和分支，精简图用于说明核心链路：

```text
用户问题
-> 上下文改写
-> LangGraph
-> LLM 选择工具
-> code_search / rag_retrieve / general_answer
-> CRAG / Self-RAG
-> 最终答案
```

### 二十七、代码注释与讲解补充

对 `app/agent/workflow.py` 增加中文注释，说明各节点职责：

- classify：问题分类
- select_tool：LLM 工具选择
- rewrite_queries：生成多路检索 query
- retrieve：混合检索和 CRAG 评估
- compress_evidence：证据压缩
- generate_answer：答案生成
- self_check：Self-RAG 自检
- code_analysis：代码定位工具链路

同时补充说明：

1. `DOMAIN_KEYWORDS` 用于判断是否属于 MEC/5G 领域。
2. `query_rewriter.py` 用于扩展检索 query，提高召回。
3. `vector_score / keyword_score / rerank_score` 分别对应向量相似度、关键词重叠度和最终重排分数。
4. 代码定位类任务不适合强行走 CRAG/Self-RAG 的文档检索链路，应优先使用代码搜索工具。

## 2026-06-20

### 二十八、MySQL 多会话持久化

本次将原先的进程内 memory 升级为本地 MySQL 持久化存储。

新增配置项：

```text
MYSQL_HOST
MYSQL_PORT
MYSQL_USER
MYSQL_PASSWORD
MYSQL_DATABASE
```

新增文件：

```text
app/agent/mysql_memory_store.py
app/routers/sessions.py
```

依赖新增：

```text
pymysql
```

当前 MySQL 会自动创建数据库和表结构，核心表包括：

```text
chat_sessions
chat_messages
chat_operations
```

#### `chat_sessions`

用于保存会话元信息和长期摘要：

```text
id
title
summary
created_at
updated_at
```

#### `chat_messages`

用于保存普通聊天记录：

```text
id
session_id
role
content
intent
evidence_sources
created_at
```

每轮 `/chat` 会写入：

```text
user 原始问题
assistant 最终答案
```

#### `chat_operations`

用于保存 Agent 每轮操作记录：

```text
standalone_question
intent
selected_tool
tool_args
queries
evidence_sources
retrieval_grade
retrieval_score
self_check_result
created_at
```

该表用于支持“刚才那个文件”“继续上次的工具”“刚才的 chunk/证据”等多轮追问。

### 二十九、Session API

新增会话管理接口：

```text
POST   /sessions
GET    /sessions
GET    /sessions/{conversation_id}/messages
DELETE /sessions/{conversation_id}
```

支持能力：

1. 创建新会话。
2. 查看历史会话列表。
3. 查看指定会话历史消息。
4. 删除指定会话。

其中 `chat_messages` 和 `chat_operations` 均通过外键关联 `chat_sessions`，并设置：

```text
ON DELETE CASCADE
```

因此删除 session 时，对应聊天记录和操作记录会自动删除。

### 三十、CLI 入口升级为 `/mecagent`

CLI 从单纯 `chat` 模式升级为系统入口模式。

启动方式：

```powershell
.\.venv\Scripts\python.exe cli.py mecagent
```

或进入 CLI 首页后输入：

```text
/mecagent
```

进入系统后支持：

```text
/chat
/chat <session_id>
/session
/session <session_id>
/session delete <session_id>
/use <session_id>
/history
/debug
/trace
/help
/exit
```

其中：

- `/chat` 创建新会话。
- `/chat <session_id>` 进入历史会话继续聊天。
- `/session` 查看 MySQL 中的历史会话。
- `/session delete <session_id>` 删除会话及其历史。

同时新增启动 banner，执行 `cli.py mecagent` 后会显示 MEC Agent 标志图形和启动时间，用于明确记录系统开始使用。

### 三十一、多轮记忆增强

当前多轮上下文由三部分组成：

```text
历史 summary
+ 最近 5 轮详细聊天
+ 最近 3 轮操作记录
```

实现位置：

```text
app/agent/memory.py
app/agent/contextualizer.py
app/agent/mysql_memory_store.py
```

#### 1. 长期摘要

`chat_sessions.summary` 保存长期摘要。

每轮助手回答写入后，会自动更新 summary：

```text
旧 summary + 最近 5 轮对话 -> 新 summary
```

如果 DeepSeek 可用，优先调用 LLM 总结；如果不可用，则使用规则方式拼接并截断。

#### 2. 最近 5 轮详细聊天

每次构造上下文时，从 MySQL 读取最近 10 条消息：

```text
RECENT_TURN_COUNT = 5
RECENT_MESSAGE_LIMIT = 10
```

用于保留最近追问的具体语言细节。

#### 3. 最近 3 轮操作记录

每轮 workflow 结束后，将关键 state 写入 `chat_operations`。

读取上下文时加载最近 3 条操作记录：

```text
RECENT_OPERATION_LIMIT = 3
```

用于让大模型知道之前：

- 用了哪个 tool
- tool 参数是什么
- 检索 query 是什么
- 命中了哪些证据来源
- CRAG/Self-RAG 判断结果是什么

### 三十二、上下文改写增强

`app/agent/contextualizer.py` 现在不再只看最近几条聊天，而是将以下内容一起交给大模型：

```text
历史摘要
最近5轮详细对话
最近3轮操作记录
当前问题
```

目标是将类似下面的问题改写为可独立执行的问题：

```text
继续查刚才那个文件
上次那个 tool 的参数是什么
刚才的证据来源再解释一下
这个和前面提到的 NEF 有关系吗
```

这样可以显著降低多轮会话中“忘记刚才操作”的概率。

### 三十三、当前设计判断

当前 memory 设计已经从“只保存聊天记录”升级为：

```text
聊天记忆 + 长期摘要 + Agent 操作记忆
```

整体结构为：

```text
chat_sessions    保存会话元信息和长期 summary
chat_messages    保存用户问题和助手最终答案
chat_operations  保存工具调用、检索和自检等操作状态
```

该设计适合当前阶段继续迭代。后续可优化方向：

1. 在 `chat_operations` 中进一步保存 chunk id、page、score、preview 等 evidence item。
2. 将最近操作记录也传给 tool selection prompt，而不只用于 contextualizer。
3. 对 summary 更新做降频，例如每 3 轮更新一次，降低 LLM 调用成本。
4. 将上下文构造拆到独立 `context_builder.py`。
5. 后续引入 LangGraph checkpoint，保存更完整的节点级执行状态。

### 三十四、文件工具与会话产物记忆（2026-07-20）

新增受限于 `FILE_TOOL_ALLOWED_ROOT`（默认 Windows 桌面）的本地文件工具：

```text
file_search  按名称搜索文本文件，只读
file_read    读取 UTF-8 文本文件，只读
file_write   创建或修改文件，必须 y/n 审批
file_delete  删除单个文件，必须 y/n 审批
```

文件写入、删除不会复用 CMD 或 Shell：写入和删除均在 Python 文件工具中执行，并限制路径不能逃逸允许目录。已有文件只有在 `overwrite=true` 时可覆盖。

为解决“刚才创建的俄罗斯方块文件路径在摘要压缩后丢失”的问题，MySQL 新增：

```text
session_artifacts
  session_id
  name
  artifact_type
  file_path
  path_hash
  summary
  content_sha256
  created_at / updated_at
```

成功 `file_write` 后，`/files/approve` 会将文件路径、文件名、写入用途和内容哈希写入该表。后续同一会话加载时，产物列表会注入上下文；例如“给刚才的俄罗斯方块增加变化方块”可以使用已记录的 `tetris.py` 路径。

产物同步规则：

```text
Agent file_delete 审批成功
→ 删除文件
→ 删除对应 session_artifacts 记录

用户在 Agent 外部手动删除文件
→ 下一次加载会话时检查记录路径
→ 确认文件不存在后才删除过期记录
```

这项检查只清理数据库中的过期引用，不会主动删除用户文件。

同时，CMD 最小环境保留 pip/HTTPS 所需的代理、CA 证书、pip 镜像及 Windows 用户配置变量，解决 Agent 子进程与用户终端 TLS 配置不一致的问题。

验证：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

结果：`48 passed`。

### 三十五、双摘要与重要事件 Memory（2026-07-21）

在不改动 ReAct 推理和工具执行链的前提下，Memory 层改为以下结构：

```text
chat_sessions.summary           保留，兼容历史会话
chat_sessions.semantic_summary  跨任务稳定信息（偏好、项目、环境）
chat_sessions.task_summary      当前会话任务状态
chat_events                     有筛选的长期重要事件
```

摘要更新只发送最近 6 条消息、当前 ReAct 状态和已筛选的重要事件给模型，模型必须返回
`semantic_summary` 与 `task_summary` 两个 JSON 对象。JSON 不合法或 DeepSeek 不可用时，保留原有双摘要，不再把旧摘要和新对话直接拼接。

每个 assistant 最终回答持久化后，`EventExtractor` 根据用户消息、工具结果和最终回答提取至多一个重要事件；普通聊天不写入 `chat_events`。Context Builder 仅带入最近 6 条消息、最多 10 条重要事件和最近操作/产物。事件排序分数为：

```text
0.5 * importance + 0.3 * relevance + 0.2 * recency
```

旧会话首次启动时自动执行 `summary -> semantic_summary` 迁移，并将 `task_summary` 初始化为 `{}`。

### 三十六、统一 ReAct 最终回答出口与跨轮任务记忆评估（2026-07-22）

#### 1. 背景

在“分析本地 MNIST 项目后继续进行前端优化”的连续任务中，出现了两个不同层面的现象：

1. 已执行 `file_search`、`file_read` 后，最终回答仍可能声称“无法读取本地文件”。
2. 下一轮提出“帮我进行前端体验优化”时，模型没有稳定继承上一轮已读取的前端文件、分析结论和后续修改方向。

第一个问题是 ReAct 结束路径的问题；第二个问题是 Memory 数据建模和上下文选择的问题，不能只依赖提示词修补。

#### 2. 已完成：移除 `general_answer` 快捷分支

此前 `general_answer` 会重新调用通用回答函数，只传入当前问题，未携带本轮已经得到的工具 Observation。因此它可能覆盖文件读取、CMD、RAG、联网搜索或诊断工具产生的真实结果。

本次调整为：

```text
任务计划 → ReAct 工具决策 → Observation → 再决策 → finish_task → 最终回答
```

- 从工具定义中移除 `general_answer`。
- 从任务计划允许工具列表中移除 `general_answer`，使用 `finish_task` 作为计划结束步骤。
- `ReActWorkflow` 不再对“回答详细一点”等追问走通用回答快捷路径；所有问题都进入同一 ReAct 循环。
- 移除 ReAct 达到预算时自动通用回答的兜底；未完成任务应返回结构化阻塞信息，而不是伪装成完成。
- 旧 `RAGWorkflow` 新增显式 `finish_task` 节点，由该节点写入 `draft_answer`，再进入 `finalize`。避免在 LangGraph 条件路由函数中修改 state 而导致最终答案未持久化的问题。
- 删除旧工作流中未再使用的 `_general_answer` / `_answer_general` 实现。

结果：工具 Observation 已成为最终答案的唯一事实来源之一，不会再被通用回答分支丢弃。

#### 3. 验证

新增或更新测试，覆盖：

- 简单问题由 `finish_task` 返回最终答案；
- ReAct 任务计划的结束步骤被正确标记为 `completed`；
- “回答详细一点”仍经过 ReAct，并以 `finish_task` 结束；
- 既有 CMD 恢复、文件工具和工作流测试保持通过。

执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q app
```

结果：`61 passed`；仅保留 LangGraph 第三方依赖的弃用提示警告。

#### 4. 发现：当前跨轮上下文的实际边界

同一 `session_id` 的下一轮请求目前会加载并用于问题改写的内容：

```text
semantic_summary   长期稳定偏好、项目、环境信息
task_summary       当前会话的目标、完成项、问题、下一步
recent_messages    最近 3 轮（6 条）用户/助手消息
important_events   按重要性、相关性、时效性排序的最多 10 条事件
recent_operations  最近 3 条操作记录
artifacts          最近 20 个由 Agent 创建或修改的文件产物
```

但完整 ReAct 执行状态虽然已写入 `react_tasks.state_json`，下一轮 `get_or_create_memory()` 不会主动加载它；同时 `chat_operations` 记录的是本轮最终工具，当前通常为 `finish_task`，并不能表达中间的 `file_search`、`file_read`、CMD 等步骤。只读文件也不会进入 `session_artifacts`。

此外，当前完整 Memory Context 主要交给 Contextualizer 生成“独立问题”；后续 ReAct 计划器和工具选择器主要接收改写后的问题，而没有直接收到同一份结构化任务上下文。这会形成信息压缩瓶颈。

#### 5. 后续设计方向（待实现）

不要无限增加聊天消息数量，也不要把所有执行内容拼入 `summary`。应将任务执行记忆独立建模：

```text
react_tasks       任务目标、状态、最终结论、推荐下一步
react_steps       每一步工具、脱敏参数、结果摘要、成功/失败、时间
task_file_refs    已读/创建/修改文件的路径、用途、内容摘要、哈希、更新时间
chat_events       仅保存关键事实、失败、决策、偏好
```

下一轮若判定为跟进任务，应按“当前未完成任务 → 相关的最近已完成任务 → 任务步骤与文件引用 → 最近消息 → 重要事件与长期摘要”的优先级构建 `WORKING_CONTEXT`，并将其直接注入：

1. ReAct 任务计划器；
2. 工具决策模型；
3. 最终 `finish_task` 回答阶段。

对于文件，应保存路径、内容哈希和结构化摘要，而不是完整正文；真正写入或修改前必须重新 `file_read`，以当前磁盘状态为准。这样可同时保留跨轮连续性、控制 Token 成本，并避免基于过期文件内容修改。

#### 6. Redis + MySQL Memory 缓存分层

本项目已接入 Redis 作为 Memory 的可选加速层，采用 Cache-Aside（旁路缓存）模式：

```text
读取：Redis 命中 → 直接返回
读取：Redis 未命中/异常 → 查询 MySQL → 回填 Redis
写入：先写 MySQL（唯一事实来源）→ 更新或失效 Redis
```

MySQL 始终保存持久化数据；Redis 不保存唯一副本，也不承担任务恢复职责。Redis 不可达、密码错误、网络超时或未配置时，缓存操作按未命中处理，Agent 自动回退至 MySQL，不能影响正常对话。

当前 Redis 缓存范围：

```text
agent:memory:summary:<session-hash>:semantic:v1
agent:memory:summary:<session-hash>:task:v1
agent:memory:events:<session-hash>:v<event-version>:<query-fingerprint>
agent:memory:events-version:<session-hash>
```

- 缓存 `semantic_summary` 与 `task_summary`，减少每轮上下文构建时的 MySQL 会话查询。
- 缓存按查询排序后的重要事件列表；创建或删除事件时递增 `event-version`，使旧事件查询缓存自然失效。
- session id 与查询文本均通过哈希构造缓存键，不直接暴露会话标识或用户问题。
- 默认 TTL 为 `REDIS_CONTEXT_TTL_SECONDS=600` 秒；连接和读写超时由 `REDIS_SOCKET_TIMEOUT_SECONDS=1.0` 秒控制，避免 Redis 故障拖慢对话主链路。

相关环境变量：

```dotenv
REDIS_HOST=192.168.29.143
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=                # 按实际 Redis 配置填写；无密码则留空
REDIS_CONTEXT_TTL_SECONDS=600
REDIS_SOCKET_TIMEOUT_SECONDS=1.0
```

需要注意：Redis 缓存只能降低读取延迟，不能解决“下一轮没有读取上一轮 ReAct Observation”的语义连续性问题。后续新增 `react_steps`、`task_file_refs` 和 `WORKING_CONTEXT` 时，MySQL 应继续保存这些结构化事实；Redis 可在其上增加短 TTL 的相关任务上下文缓存，但不应替代持久化任务记忆。
