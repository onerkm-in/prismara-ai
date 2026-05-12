"""DEPRECATED — superseded by src/orchestrator.py.

The single-supervisor delegation model has been replaced by a sequential
role pipeline. See src/orchestrator.py. This module is kept only so any
stray import does not break.
"""

from __future__ import annotations


def delegate_task(supervisor_name: str, task: str, codebase_context: dict | None = None) -> str:  # noqa: ARG001
    """Compatibility shim — never used by the new pipeline."""
    return (
        "Legacy delegate_task() called. The orchestrator pipeline supersedes "
        "this entry point. See src/orchestrator.py.orchestrate()."
    )
