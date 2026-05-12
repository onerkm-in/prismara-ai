from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from typing import Any

from src.secure_storage import load_json, save_json

CHAT_HISTORY_LIMIT = 200
MAX_CHAT_CONTENT_CHARS = 60000

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    configured = os.environ.get("MLMAE_DATA_DIR", "").strip()
    root = Path(configured) if configured else _PROJECT_ROOT / "prismara"
    if not root.is_absolute():
        root = _PROJECT_ROOT / root
    root.mkdir(parents=True, exist_ok=True)
    return root


def chat_history_path() -> Path:
    return _data_dir() / "chat_history.json"


def _clip_text(value: Any, limit: int = MAX_CHAT_CONTENT_CHARS) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit]


def _json_safe_meta(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_json_safe_meta(v) for v in value[:50]]
    if isinstance(value, dict):
        return {str(k): _json_safe_meta(v) for k, v in list(value.items())[:50]}
    return str(value)


def _normalise_message(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None

    role = str(message.get("role", "")).strip().lower()
    if role not in {"user", "assistant", "system", "error"}:
        return None

    content = _clip_text(message.get("content", ""))
    if not content and role != "error":
        return None

    raw_created = message.get("created_at", int(time.time()))
    try:
        created_at = int(raw_created)
    except Exception:
        created_at = int(time.time())

    msg_id = str(message.get("id") or secrets.token_hex(8))
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}

    return {
        "id": msg_id[:80],
        "role": role,
        "content": content,
        "created_at": created_at,
        "meta": _json_safe_meta(meta),
    }


def load_chat_history() -> list[dict[str, Any]]:
    data = load_json(chat_history_path(), default={}) or {}
    raw_messages = data.get("messages", []) if isinstance(data, dict) else data
    if not isinstance(raw_messages, list):
        return []

    messages: list[dict[str, Any]] = []
    for item in raw_messages:
        msg = _normalise_message(item)
        if msg:
            messages.append(msg)
    return messages[-CHAT_HISTORY_LIMIT:]


def save_chat_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []
    for item in messages:
        msg = _normalise_message(item)
        if msg:
            normalised.append(msg)

    normalised = normalised[-CHAT_HISTORY_LIMIT:]
    save_json(
        chat_history_path(),
        {
            "version": 1,
            "updated_at": int(time.time()),
            "limit": CHAT_HISTORY_LIMIT,
            "messages": normalised,
        },
        encode_at_rest=True,
    )
    return normalised


def clear_chat_history() -> list[dict[str, Any]]:
    return save_chat_history([])


def append_chat_turn(
    user_prompt: str,
    answer: str,
    *,
    raw_output: str = "",
    metrics: dict[str, Any] | None = None,
    workspace: str = "",
    status: str = "ok",
    error: str = "",
) -> list[dict[str, Any]]:
    metrics = metrics if isinstance(metrics, dict) else {}
    request_id = str(metrics.get("request_id") or secrets.token_hex(8))
    now = int(time.time())
    models = metrics.get("models", [])
    if not isinstance(models, list):
        models = []

    messages = load_chat_history()
    common_meta = {
        "request_id": request_id,
        "workspace": _clip_text(workspace, 4096),
        "status": status,
    }

    messages.append({
        "id": f"{request_id}-user",
        "role": "user",
        "content": _clip_text(user_prompt),
        "created_at": now,
        "meta": common_meta,
    })

    assistant_content = answer or raw_output or error
    messages.append({
        "id": f"{request_id}-assistant",
        "role": "assistant" if status == "ok" else "error",
        "content": _clip_text(assistant_content),
        "created_at": now,
        "meta": {
            **common_meta,
            "models": [str(model) for model in models[:20]],
            "error": _clip_text(error, 4096),
        },
    })

    return save_chat_history(messages)
