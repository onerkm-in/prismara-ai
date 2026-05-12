"""DEPRECATED — superseded by src/orchestrator.py.

The single-supervisor election model has been replaced by a role-typed
pipeline (channeliser → refiner → augmenter → processor(s) → summariser →
synthesiser → validator → security_scanner → finaliser). This module is
kept only so that any stray import does not break; new code should call
src.orchestrator.orchestrate() directly.
"""

from __future__ import annotations


def elect_supervisor(prompt: str, codebase_context: dict | None = None) -> str:  # noqa: ARG001
    """Compatibility shim — never used by the new pipeline."""
    return "orchestrator (role pipeline)"
