# MEC/5G RAG Agent MVP

这是一个基于 DeepSeek + RAG + Agent 的 MEC/5G 信令智能问答系统 MVP。系统面向 NEF、NEF_NBI、Traffic Influence、UPF、SMF、PDR/FAR、MEP、MEC APP、N3/N6 分流等场景，支持文档入库、混合检索、CRAG 检索质量评估、答案生成和 Self-RAG 风格自检。

## 目录结构

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
├── tests/
├── .env.example
├── requirements.txt
└── README.md
```

## 安装依赖

```bash
pip install -r requirements.txt
```

`chromadb` 用于本地向量库，`sentence-transformers` 用于默认 Embedding。若这两个依赖暂时不可用，代码会降级到本地 JSON 向量存储和 Hashing Embedding，便于先跑通 MVP；生产或较准检索建议安装完整依赖。

## 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`：

```env
DEEPSEEK_API_KEY=你的 DeepSeek Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

代码不会硬编码 API Key。没有配置 `DEEPSEEK_API_KEY` 时，系统会使用规则兜底完成 CRAG、答案生成和自检。

## 启动服务

```bash
uvicorn app.main:app --reload
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

## 导入文档

默认示例文档已经放在 `data/raw_docs/`。当前支持 `.md`、`.markdown`、`.txt`、`.pdf`、`.docx`。把文件放进该目录后执行导入命令：

```bash
curl -X POST http://127.0.0.1:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"source_dir": "data/raw_docs"}'
```

如果要导入 PDF/docx，请确认已安装依赖：

```bash
pip install pypdf python-docx
```

返回示例：

```json
{
  "success": true,
  "message": "ingested 3 documents",
  "chunks": 3
}
```

## 发送问题

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "NEF 返回成功，但流量没有进入 MEC APP，可能是什么原因？"}'
```

返回包含：

- `answer`：最终答案
- `intent`：问题意图
- `retrieval_grade`：CRAG 检索质量等级
- `self_check`：Self-RAG 自检结果
- `evidence`：引用的检索片段

## 命令行多轮会话

无需浏览器也可以进入会话：

```bash
python cli.py chat
```

进入后直接输入问题即可。支持命令：

```text
/help
/history
/new
/exit
```

如果想在 bash 中用更短的命令：

```bash
source scripts/bash_aliases.sh
chat
```

严格来说，bash 里的 `/chat` 会被解释为根目录下的可执行文件。脚本里也提供了 `/chat` alias，但不同 shell 对带斜杠 alias 的支持可能不一致；最稳的是使用 `chat` 或 `python cli.py chat`。

## 开发说明

- 文档加载当前支持 Markdown、txt、PDF、docx，后续可扩展代码文件、OpenAPI Schema 和日志。
- `app/tools/` 中预留了 OpenAPI、代码搜索、日志搜索和 K8s 工具接口，目前返回 mock/TODO 数据。
- `app/agent/workflow.py` 限制了检索重试和答案重写次数，避免死循环，后续可平滑迁移到 LangGraph。
