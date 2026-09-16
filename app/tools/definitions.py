from __future__ import annotations

from typing import Any


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "code_search",
            "description": "Search a local source file or repository path for an exact keyword and return matching lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Local file or directory path to search, for example D:\\HiAI\\Hello.py.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Keyword, function name, class name, variable, or error text to search for.",
                    },
                },
                "required": ["repo_path", "query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_retrieve",
            "description": "Retrieve MEC/5G domain knowledge from the local Chroma knowledge base.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the public web for current information. Use only when the user explicitly "
                "asks to search online or the question requires up-to-date information. "
                "For MEC/5G knowledge that is not time-sensitive, use rag_retrieve instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A concise web-search query.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_delete",
            "description": "Prepare deletion of one local file below FILE_TOOL_ALLOWED_ROOT. Use only when the user explicitly asks to delete that file. It always requires y/n approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or root-relative file path."},
                    "rationale": {"type": "string", "description": "Short Chinese explanation of the deletion."},
                },
                "required": ["path", "rationale"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_search",
            "description": (
                "Find local text files below FILE_TOOL_ALLOWED_ROOT by a filename/path keyword. "
                "This is the mandatory first tool when the user gives a local directory/project path and asks "
                "to analyze, review, explain, audit, or summarize its code structure. Use it to locate README, "
                "requirements/config files, entry points, and source files before answering. This is read-only. "
                "Do not ask the user to paste code before trying this tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Filename or path keyword, such as tetris."}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": (
                "Read one UTF-8 text file below FILE_TOOL_ALLOWED_ROOT. After file_search, use this to inspect "
                "README, dependency/configuration files, and relevant source files for local project analysis. "
                "Base the analysis only on successful file observations. Do not claim local files are inaccessible "
                "unless this tool returns an actual path, permission, or read error. This is read-only."
            ),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Absolute or root-relative file path."}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Prepare writing one complete UTF-8 text file below FILE_TOOL_ALLOWED_ROOT. Use when the user explicitly asks to create or modify a local file. It always requires y/n approval and never writes before approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or root-relative target file path."},
                    "content": {"type": "string", "description": "Complete UTF-8 file content."},
                    "rationale": {"type": "string", "description": "Short Chinese explanation of the write."},
                    "overwrite": {"type": "boolean", "description": "True only when replacing an existing file is necessary."},
                },
                "required": ["path", "content", "rationale"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_task",
            "description": (
                "Finish the current multi-step task only when observations are sufficient. For local project/code "
                "analysis, do not finish until file_search has succeeded and at least one relevant file has been "
                "successfully read, unless file tools report that no readable file exists. Never claim local files "
                "are inaccessible without an explicit file-tool error. Provide the final Chinese answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": "Final answer based only on available observations.",
                    },
                },
                "required": ["answer"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cmd_execute",
            "description": (
                "Prepare one Windows CMD command only when the user explicitly asks to run it. "
                "Conservatively allowlisted read-only commands run immediately and return their output. Any command "
                "that can modify state, uses command chaining/redirection, or is not clearly read-only requires separate "
                "user approval. Use CMD syntax only; do not use it for automatic investigation or ordinary questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The exact Windows CMD command. Do not add extra commands.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "A short Chinese explanation of what the command will do.",
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Optional directory below the configured allowed work directory.",
                    },
                },
                "required": ["command", "rationale"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_pod_bug",
            "description": (
                "Collect read-only Kubernetes Pod status, warning events, and bounded logs for "
                "bug diagnosis. Use when the user asks to diagnose a Pod, container, restart, "
                "CrashLoopBackOff, OOMKilled, or Kubernetes log issue."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace containing the Pod.",
                    },
                    "pod_name": {
                        "type": "string",
                        "description": "Exact Kubernetes Pod name.",
                    },
                    "container": {
                        "type": "string",
                        "description": "Optional container name for a multi-container Pod.",
                    },
                    "since_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "How far back to fetch logs, in seconds. Default: 3600.",
                    },
                    "tail_lines": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Maximum recent log lines to fetch. Default: 500.",
                    },
                },
                "required": ["namespace", "pod_name"],
                "additionalProperties": False,
            },
        },
    },
]

AVAILABLE_TOOLS = {
    tool["function"]["name"]
    for tool in TOOL_DEFINITIONS
}
