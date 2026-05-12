import os
from datetime import datetime
from pathlib import Path

from src.secure_storage import (
    SecureStorageDecodeError,
    load_json,
    save_json,
)

# Path to the shared neural memory file.
# In exe mode, MLMAE_DATA_DIR is set by launcher.py to the prismara folder.
# In dev mode, fall back to a local prismara folder at the project root.
_data_dir = os.environ.get(
    "MLMAE_DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "prismara")
)
os.makedirs(_data_dir, exist_ok=True)
MEMORY_FILE = Path(_data_dir) / "neural_memory.json"

REQUIRED_KEYS: tuple[str, ...] = (
    "session_start",
    "global_context",
    "event_log",
    "shared_artifacts",
)


class MemoryDecodeError(Exception):
    """neural_memory.json exists but is unreadable or schema-invalid.

    Typical causes:
      - The file was written on a different machine (XOR key derived from the
        machine fingerprint no longer matches).
      - Manual edit corrupted the wrapped JSON or invalidated the checksum.
      - Older format from before required keys were enforced.

    Callers (e.g. /run-stream) should surface this to the user with a
    recovery hint instead of crashing on a KeyError. Recovery options:
      - Approve a transfer window (admin → settings) to re-bind to this
        machine.
      - Reset memory: delete or rename prismara/neural_memory.json so the next
        read creates a blank slate.
    """


def _blank_state() -> dict:
    return {
        "session_start": str(datetime.now()),
        "global_context": "Prismara AI initialized. Agents should read this context before execution.",
        "event_log": [],
        "shared_artifacts": {},
    }


def initialize_memory(force: bool = False) -> None:
    """Create a blank neural memory file when none exists.

    Pass force=True to overwrite an existing (possibly corrupt) file with a
    fresh blank state. Used by the admin recovery action.
    """
    if force or not MEMORY_FILE.exists():
        save_json(MEMORY_FILE, _blank_state(), encode_at_rest=True)
        print("🧠 [MEMORY CORE]: Wrote fresh neural_memory.json")


def read_memory() -> dict:
    """Read neural memory, raising MemoryDecodeError on unreadable/invalid files.

    Never silently returns an empty dict for a corrupt or undecodable file —
    that previously masked machine-fingerprint mismatches and surfaced as
    confusing KeyErrors deep inside write_to_memory.
    """
    if not MEMORY_FILE.exists():
        initialize_memory()

    try:
        memory = load_json(MEMORY_FILE, default=None, strict=True)
    except SecureStorageDecodeError as e:
        raise MemoryDecodeError(str(e)) from e

    if not isinstance(memory, dict):
        raise MemoryDecodeError(
            f"neural_memory.json did not decode to a JSON object "
            f"(got {type(memory).__name__})."
        )

    missing = [k for k in REQUIRED_KEYS if k not in memory]
    if missing:
        raise MemoryDecodeError(
            f"neural_memory.json is missing required keys: {', '.join(missing)}. "
            f"Reset memory from the admin panel to recover."
        )

    return memory


def write_to_memory(agent_name: str, action_summary: str, artifacts: dict | None = None) -> None:
    """Append an event (and optional artifacts) to neural memory."""
    memory = read_memory()

    memory["event_log"].append({
        "timestamp": str(datetime.now()),
        "agent": agent_name,
        "action": action_summary,
    })

    if artifacts:
        memory["shared_artifacts"].update(artifacts)

    save_json(MEMORY_FILE, memory, encode_at_rest=True)
    print(f"💾 [MEMORY CORE]: '{agent_name}' updated the neural memory.")


def get_shared_artifact(key: str):
    """Return a single shared artifact by key, or None if absent or unreadable."""
    try:
        return read_memory()["shared_artifacts"].get(key)
    except MemoryDecodeError:
        return None
