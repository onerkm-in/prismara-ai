"""Prismara AI CLI entry point - thin wrapper around src.orchestrator.

For interactive use, run the Flask servers (server/app.py for dev,
server/app_standalone.py for packaged). This module just lets you drive a
single request from the shell for smoke-testing.
"""

from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from src.orchestrator import orchestrate


def run_prismara_ai(user_prompt: str, codebase_context: dict | None = None) -> str:
    """Run one request through the orchestrator and return the textual report.

    Stage events are printed to stdout as they arrive so progress is visible
    even from the CLI.
    """
    final_report = ""
    for event in orchestrate(user_prompt, codebase_context=codebase_context):
        kind = event.get("type")
        if kind == "stage":
            agent = event.get("agent", "—")
            status = event.get("status")
            name = event.get("name")
            preview = event.get("output_preview", "")
            note = event.get("note", "")
            line = f"[{status:<7}] {name:<18} {agent}"
            if preview:
                line += f"\n    ↳ {preview}"
            if note:
                line += f"\n    note: {note}"
            print(line)
        elif kind == "result":
            final_report = event.get("content", "")
        elif kind == "error":
            print(f"[ERROR] {event.get('message')}", file=sys.stderr)
        elif kind == "warning":
            print(f"[WARN]  {event.get('message')}", file=sys.stderr)
        elif kind == "intents":
            print(f"[intents] {', '.join(event.get('intents', []))} ({event.get('source')})")
        elif kind == "metrics":
            print(f"[metrics] {json.dumps(event, separators=(',', ':'))}")
    return final_report


def run_mlmae(user_prompt: str, codebase_context: dict | None = None) -> str:
    """Backward-compatible alias for older local scripts."""
    return run_prismara_ai(user_prompt, codebase_context=codebase_context)


if __name__ == "__main__":
    print("Prismara AI - CLI smoke test")
    print("=" * 50)
    prompt = " ".join(sys.argv[1:]) or (
        "Review the codebase, find any bugs or improvements in the Python files, "
        "and suggest fixes."
    )
    report = run_prismara_ai(prompt)
    print()
    print("=" * 50)
    print(report)
