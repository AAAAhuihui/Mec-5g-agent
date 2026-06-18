from __future__ import annotations

from typing import Any


def get_api_schema(api_name: str) -> dict[str, Any]:
    return {
        "api_name": api_name,
        "status": "TODO",
        "message": "OpenAPI Schema 工具骨架已预留，后续可接入真实 Schema 仓库。",
        "schema": {},
    }


def validate_payload(api_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "api_name": api_name,
        "valid": True,
        "warnings": ["当前为 mock 校验，尚未接入真实 OpenAPI Schema。"],
        "payload": payload,
    }


def generate_payload(api_name: str, requirement: str) -> dict[str, Any]:
    return {
        "api_name": api_name,
        "requirement": requirement,
        "payload": {"todo": "replace with generated payload"},
        "message": "当前返回 mock payload，后续可由 DeepSeek + Schema 生成真实请求体。",
    }

