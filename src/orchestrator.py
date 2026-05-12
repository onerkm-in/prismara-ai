"""
Prismara AI Orchestrator
==================
Local-first, role-typed pipeline. Dependency stages run sequentially; the
independent per-intent processors and final safety checks can run with bounded
parallelism. Each stage streams its own progress events. Stage outputs that
exceed the in-memory threshold spill to prismara/cache/jobs/{request_id}/ on
local disk, gated by available free space.

Pipeline:
    intake     → channeliser  (split multi-intent)
                 refiner      (clean / disambiguate)
                 augmenter    (inject workspace + memory context)
    routing    → elector      (per-intent capability → agent)
    process    → processor(s) (code | reason | write | general | vision)
    synthesis  → summariser   (compress per-intent outputs)
                 synthesiser  (merge multi-intent into one)
    safety     → validator    (output vs. original intent)
                 security     (vuln/secret patterns in generated code)
    finalise   → finaliser    (emit final_answer/metrics/result/done)

Online providers are NEVER invoked from this module. The Researcher role
is gated behind explicit per-request user consent and lives in a separate
module (added later).

Usage from a Flask handler:

    for event in orchestrate(prompt, codebase_context):
        yield json.dumps(event) + "\n"
"""
from __future__ import annotations

import json
import os
import queue
import re
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Optional

from src.llm_client import (
    AGENT_REGISTRY,
    call_llm,
    detect_available_agents,
)
from src.memory_core import MemoryDecodeError, write_to_memory


# ── Disk spillover thresholds ────────────────────────────────────────────────
# Policy: prefer SSD over RAM. We spill aggressively so stage outputs don't
# accumulate in process memory while Ollama is loading/unloading large models.
# The job dir is cleaned up in `finally`, so disk use is bounded per-request.

IN_MEMORY_LIMIT_BYTES = int(os.environ.get("MLMAE_INMEM_LIMIT_BYTES", "2048"))   # 2 KB → spill almost everything
MIN_FREE_DISK_GB = float(os.environ.get("MLMAE_MIN_FREE_DISK_GB", "1.0"))        # refuse to spill below this
MAX_PER_JOB_MB = int(os.environ.get("MLMAE_MAX_PER_JOB_MB", "2048"))             # 2 GB per request (was 200 MB)


def _parallel_mode() -> str:
    return os.environ.get("MLMAE_PARALLEL_MODE", "auto").strip().lower()


def _parallel_env_limit() -> int:
    try:
        return max(0, int(os.environ.get("MLMAE_MAX_PARALLEL_STAGES", "0") or 0))
    except ValueError:
        return 0


# ── Role pools (priority ordered, lightest first for low-RAM defaults) ───────
# Picker walks left-to-right and returns the first agent whose availability
# check passes. Pools list registry keys defined in src/llm_client.py.

ROLE_POOLS: dict[str, list[str]] = {
    "channeliser": [
        "TinyLlama (Local)",
        "Qwen 3 0.6B (Local)",
        "Qwen 3 1.7B (Local)",
        "Phi-3.5 Mini (Local)",
        "Llama 3.2 (Local)",
    ],
    "refiner": [
        "TinyLlama (Local)",
        "Qwen 3 1.7B (Local)",
        "Phi-3.5 Mini (Local)",
        "Qwen 3 4B (Local)",
        "Llama 3.2 (Local)",
        "Gemma 3 (Local)",
    ],
    "augmenter": [
        "Qwen 3 4B (Local)",
        "Qwen 3 8B (Local)",
        "Llama 3.1 8B (Local)",
        "Gemma 3 (Local)",
        "Mistral 7B (Local)",
    ],
    "processor_code": [
        "DeepSeek Coder (Local)",
        "Qwen 2.5 Coder (Local)",
        "CodeGemma 7B (Local)",
        "StarCoder2 7B (Local)",
        "Devstral 24B (Local)",
    ],
    "processor_reason": [
        "DeepSeek R1 1.5B (Local)",
        "DeepSeek R1 7B (Local)",
        "DeepSeek R1 8B (Local)",
        "Qwen 3 8B (Local)",
        "Phi-4 (Local)",
    ],
    "processor_write": [
        "Gemma 3 (Local)",
        "Llama 3.1 8B (Local)",
        "Mistral Nemo (Local)",
        "Mistral 7B (Local)",
        "Gemma 3 12B (Local)",
    ],
    "processor_general": [
        "Llama 3.2 (Local)",
        "Phi-3.5 Mini (Local)",
        "Qwen 3 4B (Local)",
        "Mistral 7B (Local)",
        "Gemma 3 (Local)",
    ],
    "processor_vision": [
        "Llama 3.2 Vision (Local)",
        "Mistral Small 3.1 24B (Local)",
        "Llama 4 Scout (Local)",
    ],
    "summariser": [
        "Phi-3.5 Mini (Local)",
        "Qwen 3 4B (Local)",
        "Llama 3.2 (Local)",
        "Phi-4 (Local)",
    ],
    "synthesiser": [
        "Llama 3.1 8B (Local)",
        "Mistral Nemo (Local)",
        "Gemma 3 12B (Local)",
        "Qwen 3 8B (Local)",
    ],
    "validator": [
        "DeepSeek R1 1.5B (Local)",
        "Phi-3.5 Mini (Local)",
        "Qwen 3 4B (Local)",
        "Llama 3.2 (Local)",
    ],
    "security_scanner": [
        "DeepSeek Coder (Local)",
        "Qwen 2.5 Coder (Local)",
        "StarCoder2 7B (Local)",
        "CodeGemma 7B (Local)",
    ],
}

# Online (paid) fallback pools — used ONLY when the user has granted explicit
# per-request online consent AND a local agent could not satisfy the intent.
# Picker walks left-to-right; an entry is eligible only when its env_key is
# set in the environment. Order matters: the first viable provider wins.
RESEARCHER_FALLBACK_POOL: dict[str, list[str]] = {
    "code":    ["ChatGPT (GPT-4o)", "Claude 3.5 Sonnet", "Gemini 1.5 Pro"],
    "reason":  ["Claude 3.5 Sonnet", "ChatGPT (GPT-4o)", "Gemini 1.5 Pro"],
    "write":   ["Claude 3.5 Sonnet", "ChatGPT (GPT-4o)", "Gemini 1.5 Pro"],
    "vision":  ["Gemini 1.5 Pro", "ChatGPT (GPT-4o)"],
    "general": ["ChatGPT (GPT-4o)", "Claude 3.5 Sonnet", "Gemini 1.5 Pro"],
}


# ── Errors ───────────────────────────────────────────────────────────────────

class NoLocalAgentAvailable(RuntimeError):
    """Raised when zero local agents are available — orchestration cannot proceed.

    The cold-start policy is fail-loud: we never silently escalate to paid
    cloud. The user must pull at least one Ollama model (or wire a custom
    OpenAI-compatible local endpoint) before orchestration runs.
    """


SYNTHESIS_NO_OUTPUT = "No local processor produced output for this request."


# ── Job context: per-request state with disk-aware spillover ─────────────────

@dataclass
class JobContext:
    request_id: str
    spill_root: Path
    max_disk_bytes: int = MAX_PER_JOB_MB * 1024 * 1024
    bytes_on_disk: int = 0
    in_memory: dict[str, str] = field(default_factory=dict)
    on_disk: dict[str, Path] = field(default_factory=dict)
    online_consent: bool = False
    online_pipeline_mode: bool = False
    hardware_snapshot: dict = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.spill_root.mkdir(parents=True, exist_ok=True)

    def put(self, stage: str, content: str) -> None:
        """Store stage output. Spill to disk if it exceeds the in-memory threshold."""
        if content is None:
            content = ""
        size = len(content.encode("utf-8"))

        with self.lock:
            if size <= IN_MEMORY_LIMIT_BYTES or not self._can_spill(size):
                self.in_memory[stage] = content
                return

            path = self.spill_root / f"{stage}.txt"
            path.write_text(content, encoding="utf-8")
            self.on_disk[stage] = path
            self.bytes_on_disk += size
            self.in_memory.pop(stage, None)

    def get(self, stage: str, default: str = "") -> str:
        with self.lock:
            if stage in self.in_memory:
                return self.in_memory[stage]
            if stage in self.on_disk:
                try:
                    return self.on_disk[stage].read_text(encoding="utf-8")
                except Exception:
                    return default
        return default

    def free(self, stage: str) -> None:
        """Drop a stage output we no longer need, freeing RAM and disk."""
        with self.lock:
            self.in_memory.pop(stage, None)
            path = self.on_disk.pop(stage, None)
            if path and path.exists():
                try:
                    size = path.stat().st_size
                    path.unlink()
                    self.bytes_on_disk = max(0, self.bytes_on_disk - size)
                except Exception:
                    pass

    def _can_spill(self, additional_bytes: int) -> bool:
        if self.bytes_on_disk + additional_bytes > self.max_disk_bytes:
            return False
        try:
            free = shutil.disk_usage(self.spill_root).free
        except Exception:
            return False
        return free >= int(MIN_FREE_DISK_GB * 1024 * 1024 * 1024) + additional_bytes

    def cleanup(self) -> None:
        with self.lock:
            try:
                shutil.rmtree(self.spill_root, ignore_errors=True)
            except Exception:
                pass


# ── Capability picker ────────────────────────────────────────────────────────

# Map orchestrator roles to the Researcher fallback pool's capability key.
# Used when local pool is empty AND the user granted per-request online
# consent: the picker falls through to RESEARCHER_FALLBACK_POOL[capability].
_ROLE_TO_CAPABILITY: dict[str, str] = {
    "channeliser":      "general",
    "refiner":          "general",
    "augmenter":        "general",
    "processor_code":   "code",
    "processor_reason": "reason",
    "processor_write":  "write",
    "processor_general": "general",
    "processor_vision": "vision",
    "summariser":       "general",
    "synthesiser":      "general",
    "validator":        "reason",
    "security_scanner": "code",
}


def _known_broken_ollama_models() -> set[str]:
    raw = os.environ.get("MLMAE_KNOWN_BROKEN_MODELS", "deepseek-r1:1.5b")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _agent_is_known_broken(name: str, entry: Optional[dict]) -> bool:
    if not entry or entry.get("provider") != "ollama":
        return False
    model = str(entry.get("model") or AGENT_REGISTRY.get(name, {}).get("model") or "").strip()
    return bool(model and model in _known_broken_ollama_models())


def _agent_is_usable(name: str, entry: Optional[dict], *, allow_online: bool) -> bool:
    if not entry or not entry.get("available"):
        return False
    tier = entry.get("tier")
    if tier == "local":
        return not _agent_is_known_broken(name, entry)
    if allow_online and tier in ("paid", "free_cloud"):
        return True
    return False


def _pick_role_agent(
    role: str,
    available: dict[str, dict],
    ctx: Optional["JobContext"] = None,
) -> Optional[str]:
    """Walk the role's local pool, then (if consent granted) fall through to online.

    Local-first is unchanged: every role tries its local pool first, in
    priority order. The online fall-through only fires when the JobContext
    indicates the user opted in for this request *and* a paid provider for
    the equivalent capability is configured + reachable. If either is
    missing, the role returns None and its stage degrades gracefully.
    """
    for name in ROLE_POOLS.get(role, []):
        entry = available.get(name)
        if _agent_is_usable(name, entry, allow_online=False):
            return name

    if ctx is not None and ctx.online_consent:
        cap = _ROLE_TO_CAPABILITY.get(role, "general")
        for name in RESEARCHER_FALLBACK_POOL.get(cap, RESEARCHER_FALLBACK_POOL["general"]):
            entry = available.get(name)
            if _agent_is_usable(name, entry, allow_online=True):
                return name

    return None


def _pick_researcher(intent: str, available: dict[str, dict]) -> Optional[str]:
    """Pick a paid online agent for the Researcher fallback. Returns None if
    no online provider has both an entry and an available API key."""
    for name in RESEARCHER_FALLBACK_POOL.get(intent, RESEARCHER_FALLBACK_POOL["general"]):
        entry = available.get(name)
        if _agent_is_usable(name, entry, allow_online=True):
            return name
    return None


def _any_online_available(available: dict[str, dict]) -> bool:
    """True if any paid or free-cloud agent is currently usable (key set)."""
    return any(
        e.get("available") and e.get("tier") in ("paid", "free_cloud")
        for e in available.values()
    )


def _iter_role_agents(
    role: str,
    available: dict[str, dict],
    ctx: "JobContext",
) -> Iterator[str]:
    """Yield all agents available for a role in priority order.

    Local pool first (in the order defined by ROLE_POOLS). If the JobContext
    granted online consent, the matching capability slice of
    RESEARCHER_FALLBACK_POOL is yielded afterwards. The caller can iterate
    with break on first success — failed agents simply move to the next.
    """
    seen: set[str] = set()
    for name in ROLE_POOLS.get(role, []):
        if name in seen:
            continue
        entry = available.get(name)
        if _agent_is_usable(name, entry, allow_online=False):
            seen.add(name)
            yield name

    if ctx is not None and ctx.online_consent:
        cap = _ROLE_TO_CAPABILITY.get(role, "general")
        for name in RESEARCHER_FALLBACK_POOL.get(cap, RESEARCHER_FALLBACK_POOL["general"]):
            if name in seen:
                continue
            entry = available.get(name)
            if _agent_is_usable(name, entry, allow_online=True):
                seen.add(name)
                yield name


def _any_local_available(available: dict[str, dict]) -> bool:
    return any(
        _agent_is_usable(name, e, allow_online=False)
        for name, e in available.items()
    )


def _available_local_count(available: dict[str, dict]) -> int:
    return sum(
        1 for name, e in available.items()
        if _agent_is_usable(name, e, allow_online=False)
    )


def _safe_hardware_snapshot() -> dict:
    try:
        from src.local_ai import detect_gpu_profile, get_system_ram_gb, hardware_policy

        gpu = detect_gpu_profile()
        return {
            "ram_gb": get_system_ram_gb(),
            "gpu": gpu,
            "policy": hardware_policy(gpu),
        }
    except Exception:
        return {"ram_gb": 0.0, "gpu": {}, "policy": {}}


def _parallel_limit(
    *,
    unit_count: int,
    available: dict[str, dict],
    ctx: JobContext,
    stage_group: str,
) -> tuple[int, dict]:
    mode = _parallel_mode()
    env_limit = _parallel_env_limit()
    if unit_count <= 1:
        return 1, {"mode": mode, "reason": "single unit", "stage_group": stage_group}
    if mode in {"off", "false", "0", "sequential", "single"}:
        return 1, {"mode": mode, "reason": "disabled by MLMAE_PARALLEL_MODE", "stage_group": stage_group}
    if env_limit > 0:
        return min(unit_count, env_limit), {
            "mode": "manual",
            "reason": "MLMAE_MAX_PARALLEL_STAGES override",
            "stage_group": stage_group,
            "env_limit": env_limit,
        }

    if ctx.online_pipeline_mode:
        return min(unit_count, 3), {"mode": mode, "reason": "online pipeline mode", "stage_group": stage_group}

    local_count = _available_local_count(available)
    if local_count <= 1:
        return 1, {
            "mode": mode,
            "reason": "only one local model available; avoiding duplicate Ollama contention",
            "stage_group": stage_group,
            "local_models": local_count,
        }

    if not ctx.hardware_snapshot:
        ctx.hardware_snapshot = _safe_hardware_snapshot()
    hw = ctx.hardware_snapshot
    ram_gb = float(hw.get("ram_gb", 0.0) or 0.0)
    gpu_allowed = bool((hw.get("policy") or {}).get("ollama_gpu_allowed"))
    if gpu_allowed and ram_gb >= 16:
        limit = 3
        reason = "GPU allowed and RAM >= 16 GB"
    elif ram_gb >= 12:
        limit = 2
        reason = "CPU/RAM profile supports two concurrent local stages"
    elif ram_gb == 0:
        limit = 2
        reason = "hardware unknown; conservative two-stage cap"
    else:
        limit = 1
        reason = "RAM below 12 GB; preserving local stability"

    return min(unit_count, limit), {
        "mode": mode,
        "reason": reason,
        "stage_group": stage_group,
        "ram_gb": ram_gb,
        "gpu_allowed": gpu_allowed,
        "local_models": local_count,
    }


def _run_stage_generators_parallel(
    tasks: list[tuple[str, Callable[[], Iterator[dict]]]],
    max_workers: int,
) -> Iterator[dict]:
    if max_workers <= 1 or len(tasks) <= 1:
        for _, factory in tasks:
            yield from factory()
        return

    events: queue.Queue[dict | object] = queue.Queue()
    done_marker = object()

    def _worker(stage_name: str, factory: Callable[[], Iterator[dict]]) -> None:
        try:
            for event in factory():
                events.put(event)
        except Exception as e:
            events.put(_stage_event(stage_name, "error", error=f"parallel worker crashed: {e}"))
        finally:
            events.put(done_marker)

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="prismara-stage") as executor:
        for stage_name, factory in tasks:
            executor.submit(_worker, stage_name, factory)

        completed = 0
        while completed < len(tasks):
            item = events.get()
            if item is done_marker:
                completed += 1
                continue
            yield item  # type: ignore[misc]


# ── Intent detection (deterministic fallback + optional LLM channeliser) ─────

CODE_HINTS = re.compile(
    r"\b(code|python|javascript|typescript|java|c\+\+|golang|rust|"
    r"function|class|method|api|endpoint|bug|fix|refactor|implement|"
    r"unit ?test|regex|sql|json|yaml|html|css|jsx|tsx)\b",
    re.IGNORECASE,
)
REASON_HINTS = re.compile(
    r"\b(why|how|explain|reason|prove|deduce|infer|analyze|analyse|"
    r"compare|trade ?off|step[- ]by[- ]step|plan|strategy)\b",
    re.IGNORECASE,
)
WRITE_HINTS = re.compile(
    r"\b(email|essay|article|blog|letter|story|poem|polite|persuasive|"
    r"summary|abstract|draft|message|copy|tweet|post)\b",
    re.IGNORECASE,
)
VISION_HINTS = re.compile(
    r"\b(image|photo|picture|screenshot|diagram|chart|figure|ocr|describe)\b",
    re.IGNORECASE,
)


def _detect_intents_fallback(prompt: str) -> list[str]:
    """Cheap regex-based intent detection. Always returns at least one intent."""
    intents: list[str] = []
    if VISION_HINTS.search(prompt):
        intents.append("vision")
    if CODE_HINTS.search(prompt):
        intents.append("code")
    if REASON_HINTS.search(prompt):
        intents.append("reason")
    if WRITE_HINTS.search(prompt):
        intents.append("write")
    return intents or ["general"]


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _deterministic_answer_if_simple(prompt: str) -> Optional[str]:
    """Fast-path tiny utility/greeting prompts so they never hit LLM stages."""
    text = (prompt or "").strip().lower()
    if not text:
        return None

    normalized = re.sub(r"[^\w\s]", "", text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    simple_greetings = {
        "hi", "hello", "hey", "hello there", "hi there", "hey there",
        "good morning", "good afternoon", "good evening", "namaste",
    }
    if normalized in simple_greetings:
        return "Hello! How can I help you today?"

    now = datetime.now()
    if "date" in text and any(k in text for k in ["today", "current", "what", "what's", "whats"]):
        return f"Today is {now.strftime('%A')} {_ordinal(now.day)} {now.strftime('%B %Y')}."
    if "day" in text and "today" in text:
        return f"Today is {now.strftime('%A')}."
    if "time" in text and any(k in text for k in ["now", "current", "what", "what's", "whats"]):
        return f"Current local time is {now.strftime('%I:%M %p')}."

    return None


# ── Stage helpers ────────────────────────────────────────────────────────────

def _safe_call(agent: str, prompt: str, system: str = "") -> tuple[bool, str, str]:
    """Call an agent, returning (ok, output, error_message). Never raises."""
    try:
        out = call_llm(agent_name=agent, prompt=prompt, system=system)
        return True, (out or "").strip(), ""
    except Exception as e:
        return False, "", str(e)


def _preview(text: str, n: int = 200) -> str:
    text = (text or "").strip().replace("\r", "")
    if len(text) <= n:
        return text
    return text[:n].rstrip() + "…"


def _stage_event(
    name: str,
    status: str,
    *,
    agent: Optional[str] = None,
    duration_ms: Optional[int] = None,
    output_preview: Optional[str] = None,
    error: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    payload: dict = {"type": "stage", "name": name, "status": status}
    if agent is not None:
        payload["agent"] = agent
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if output_preview is not None:
        payload["output_preview"] = _preview(output_preview)
    if error is not None:
        payload["error"] = error
    if note is not None:
        payload["note"] = note
    return payload


def _observe(stage: str, agent: Optional[str], summary: str) -> Optional[str]:
    """Append to neural_memory if writable; return a warning message if not."""
    try:
        write_to_memory(
            agent_name=agent or f"orchestrator/{stage}",
            action_summary=summary[:500],
        )
        return None
    except MemoryDecodeError as e:
        return f"neural memory unwritable ({e}); pipeline continues without observer log"
    except Exception as e:
        return f"observer log skipped: {e}"


# ── Stage runners ────────────────────────────────────────────────────────────
# Each runner yields stage events and returns its output text. Failures are
# caught — the pipeline degrades gracefully rather than aborting.

def _stage_channeliser(prompt: str, available: dict, ctx: JobContext) -> Iterator[dict]:
    name = "channeliser"
    yield _stage_event(name, "queued")
    agent = _pick_role_agent("channeliser", available, ctx)
    intents_fallback = _detect_intents_fallback(prompt)

    if not agent:
        ctx.put(name, json.dumps({"intents": intents_fallback, "source": "regex"}))
        yield _stage_event(name, "done", agent="regex (no LLM available)",
                           output_preview=", ".join(intents_fallback),
                           note="no local channeliser model — using regex fallback")
        return

    yield _stage_event(name, "running", agent=agent)
    started = time.perf_counter()
    sys_msg = (
        "You classify a user request into one or more intents from this fixed "
        "set: code, reason, write, vision, general. Reply with ONLY a "
        "comma-separated list of intent words from that set, nothing else."
    )
    ok, out, err = _safe_call(agent, prompt, sys_msg)
    dur = int((time.perf_counter() - started) * 1000)

    intents: list[str] = []
    if ok and out:
        for tok in re.split(r"[,\s]+", out.lower()):
            tok = tok.strip(" .;:'\"")
            if tok in {"code", "reason", "write", "vision", "general"} and tok not in intents:
                intents.append(tok)
    if not intents:
        intents = intents_fallback

    ctx.put(name, json.dumps({"intents": intents, "source": "llm" if ok else "regex"}))

    if ok:
        yield _stage_event(name, "done", agent=agent, duration_ms=dur,
                           output_preview=", ".join(intents))
    else:
        yield _stage_event(name, "error", agent=agent, duration_ms=dur,
                           error=err, note="fell back to regex intents")


def _stage_refiner(prompt: str, available: dict, ctx: JobContext) -> Iterator[dict]:
    name = "refiner"
    yield _stage_event(name, "queued")

    sys_msg = (
        "Rewrite the user's request to be unambiguous and self-contained. "
        "Keep it the same length or shorter. Do not answer it. Reply with "
        "only the rewritten request, nothing else."
    )

    last_err = ""
    last_agent = None
    attempt_count = 0
    for agent in _iter_role_agents("refiner", available, ctx):
        attempt_count += 1
        last_agent = agent
        yield _stage_event(name, "running", agent=agent)
        started = time.perf_counter()
        ok, out, err = _safe_call(agent, prompt, sys_msg)
        dur = int((time.perf_counter() - started) * 1000)

        if ok and out:
            ctx.put(name, out)
            yield _stage_event(
                name,
                "done",
                agent=agent,
                duration_ms=dur,
                output_preview=out,
                note=f"succeeded on attempt {attempt_count}" if attempt_count > 1 else None,
            )
            return

        last_err = err or "refiner returned empty output"
        yield _stage_event(name, "retry", agent=agent, duration_ms=dur,
                           error=last_err, note="trying next agent in pool")

    if attempt_count == 0:
        ctx.put(name, prompt)
        yield _stage_event(name, "skipped", note="no refiner model available — passthrough")
        return

    ctx.put(name, prompt)
    yield _stage_event(name, "error", agent=last_agent,
                       error=last_err,
                       note=f"all {attempt_count} agents in refiner pool failed; passthrough")


def _stage_augmenter(
    refined: str,
    codebase_context: Optional[dict],
    available: dict,
    ctx: JobContext,
) -> Iterator[dict]:
    name = "augmenter"
    yield _stage_event(name, "queued")

    augmented_input = refined
    if codebase_context:
        files = codebase_context.get("file_tree", [])[:50]
        augmented_input = (
            f"{refined}\n\n"
            f"[WORKSPACE: {codebase_context.get('folder')} "
            f"({codebase_context.get('file_count')} files)]\n"
            f"Tree (first {len(files)}):\n  " + "\n  ".join(files)
        )

    agent = _pick_role_agent("augmenter", available, ctx)
    if not agent or not codebase_context:
        ctx.put(name, augmented_input)
        if not codebase_context:
            yield _stage_event(name, "skipped", note="no workspace loaded — passthrough")
        else:
            yield _stage_event(name, "skipped",
                               note="no local augmenter model — using raw tree")
        return

    yield _stage_event(name, "running", agent=agent)
    started = time.perf_counter()
    sys_msg = (
        "Given the user's request and a workspace file tree, identify the "
        "5-10 files most likely relevant. Append a short '[CONTEXT HINTS]' "
        "section to the request listing those files. Return the request "
        "with the appended section, nothing else."
    )
    ok, out, err = _safe_call(agent, augmented_input, sys_msg)
    dur = int((time.perf_counter() - started) * 1000)

    final_aug = out if ok and out else augmented_input
    ctx.put(name, final_aug)

    if ok:
        yield _stage_event(name, "done", agent=agent, duration_ms=dur,
                           output_preview=final_aug)
    else:
        yield _stage_event(name, "error", agent=agent, duration_ms=dur,
                           error=err, note="passthrough — workspace tree only")


def _stage_processor(
    intent: str,
    augmented: str,
    available: dict,
    ctx: JobContext,
) -> Iterator[dict]:
    name = f"processor_{intent}"
    yield _stage_event(name, "queued")
    role_key = f"processor_{intent}" if f"processor_{intent}" in ROLE_POOLS else "processor_general"

    sys_msg = {
        "code": "You are a careful coding assistant. Produce concrete, runnable code changes or analysis. Be specific and avoid filler.",
        "reason": "Reason step-by-step. State assumptions explicitly. Conclude with a clear answer.",
        "write": "Write clearly and concisely in the requested tone. Match length to the request.",
        "vision": "Describe and analyse images precisely. If no image is provided, say so and answer the textual request.",
        "general": "Answer the user's request directly and concisely.",
    }.get(intent, "Answer the user's request directly and concisely.")

    # Build a deduped iteration: first the role-specific pool, then general as fallback.
    seen: set[str] = set()
    def _candidates():
        for a in _iter_role_agents(role_key, available, ctx):
            if a not in seen:
                seen.add(a); yield a
        if role_key != "processor_general":
            for a in _iter_role_agents("processor_general", available, ctx):
                if a not in seen:
                    seen.add(a); yield a

    last_err = ""
    last_agent = None
    attempt_count = 0
    for agent in _candidates():
        attempt_count += 1
        last_agent = agent
        yield _stage_event(name, "running", agent=agent)
        started = time.perf_counter()
        ok, out, err = _safe_call(agent, augmented, sys_msg)
        dur = int((time.perf_counter() - started) * 1000)
        if ok:
            ctx.put(name, out)
            yield _stage_event(name, "done", agent=agent, duration_ms=dur,
                               output_preview=out,
                               note=f"succeeded on attempt {attempt_count}" if attempt_count > 1 else None)
            return
        last_err = err
        yield _stage_event(name, "retry", agent=agent, duration_ms=dur,
                           error=err, note="trying next agent in pool")

    ctx.put(name, "")
    if attempt_count == 0:
        yield _stage_event(name, "error",
                           error="no local processor available for this intent",
                           note="intent dropped from synthesis")
    else:
        yield _stage_event(name, "error", agent=last_agent,
                           error=last_err,
                           note=f"all {attempt_count} processor candidates failed; intent dropped")


def _stage_researcher(
    intents: list[str],
    augmented: str,
    available: dict,
    ctx: JobContext,
    online_consent: bool,
) -> Iterator[dict]:
    """Online fallback for intents whose local processor produced no output.

    Runs only when the caller granted explicit per-request consent. Even
    with consent, the stage is a no-op for intents that already succeeded
    locally — local-first is the policy, online is the backstop.
    """
    name = "researcher"
    yield _stage_event(name, "queued")

    failed_intents = [i for i in intents if not ctx.get(f"processor_{i}")]
    if not failed_intents:
        yield _stage_event(name, "skipped", note="all intents satisfied locally")
        return

    if not online_consent:
        yield _stage_event(
            name, "skipped",
            note=f"online consent not granted; {len(failed_intents)} intent(s) "
                 f"left empty: {', '.join(failed_intents)}"
        )
        return

    for intent in failed_intents:
        agent = _pick_researcher(intent, available)
        if not agent:
            yield _stage_event(
                name, "skipped",
                note=f"intent={intent}: no online provider configured "
                     "(set an API key in the admin panel)"
            )
            continue

        yield _stage_event(name, "running", agent=agent, note=f"intent={intent}")
        started = time.perf_counter()
        sys_msg = (
            "Local agents could not handle this intent. Provide a concise, "
            "directly useful answer. The user has explicitly granted online "
            "consent for this single request only."
        )
        ok, out, err = _safe_call(agent, augmented, sys_msg)
        dur = int((time.perf_counter() - started) * 1000)

        if ok:
            ctx.put(f"processor_{intent}", out)
            yield {
                "type": "consent_used",
                "agent": agent,
                "intent": intent,
                "duration_ms": dur,
            }
            yield _stage_event(
                name, "done", agent=agent, duration_ms=dur,
                output_preview=out, note=f"intent={intent} (online fallback)"
            )
        else:
            yield _stage_event(
                name, "error", agent=agent, duration_ms=dur,
                error=err, note=f"intent={intent} fallback also failed"
            )


def _stage_summariser(intents: list[str], available: dict, ctx: JobContext) -> Iterator[dict]:
    name = "summariser"
    yield _stage_event(name, "queued")
    agent = _pick_role_agent("summariser", available, ctx)

    invoked = False  # did we actually run an LLM compression on any intent?
    saw_output = False
    for intent in intents:
        src_stage = f"processor_{intent}"
        out = ctx.get(src_stage)
        if not out:
            continue
        saw_output = True
        if len(out) <= 1000 or not agent:
            ctx.put(f"summary_{intent}", out)
            continue
        invoked = True
        yield _stage_event(name, "running", agent=agent, note=f"compressing {intent}")
        started = time.perf_counter()
        sys_msg = "Compress the following content to under 250 words while preserving every concrete fact, code change, and conclusion. Return only the compressed text."
        ok, summary, err = _safe_call(agent, out, sys_msg)
        dur = int((time.perf_counter() - started) * 1000)
        ctx.put(f"summary_{intent}", summary if ok else out[:1500])
        if ok:
            yield _stage_event(name, "done", agent=agent, duration_ms=dur,
                               output_preview=summary, note=f"intent={intent}")
        else:
            yield _stage_event(name, "error", agent=agent, duration_ms=dur,
                               error=err, note=f"intent={intent} truncated instead")
        ctx.free(src_stage)

    if not invoked:
        # Either no local summariser model, or every processor output was
        # already concise (≤1000 chars) so there was nothing to compress.
        # Emit a terminal stage event so the UI doesn't show it as queued.
        if not saw_output:
            yield _stage_event(name, "skipped", note="no processor output to summarise")
        elif not agent:
            yield _stage_event(name, "skipped", note="no local summariser model — passthrough")
        else:
            yield _stage_event(name, "skipped", agent=agent,
                               note="all processor outputs already concise")


def _stage_synthesiser(
    intents: list[str],
    original_prompt: str,
    available: dict,
    ctx: JobContext,
) -> Iterator[dict]:
    name = "synthesiser"
    yield _stage_event(name, "queued")

    parts: list[str] = []
    for intent in intents:
        s = ctx.get(f"summary_{intent}") or ctx.get(f"processor_{intent}")
        if s:
            parts.append(f"[{intent}]\n{s}")

    if not parts:
        ctx.put(name, SYNTHESIS_NO_OUTPUT)
        yield _stage_event(name, "error", error="all processors empty",
                           note="finaliser will surface a setup hint")
        return

    if len(parts) == 1:
        ctx.put(name, parts[0].split("\n", 1)[1] if "\n" in parts[0] else parts[0])
        yield _stage_event(name, "skipped", note="single intent — no merge needed")
        return

    agent = _pick_role_agent("synthesiser", available, ctx)
    if not agent:
        ctx.put(name, "\n\n".join(parts))
        yield _stage_event(name, "skipped",
                           note="no local synthesiser — concatenated outputs")
        return

    yield _stage_event(name, "running", agent=agent)
    started = time.perf_counter()
    sys_msg = (
        "You will receive answers to multiple intents of one user request, "
        "each labelled [intent]. Merge them into a single coherent reply "
        "that addresses the original request below. Do not repeat the labels."
    )
    merge_input = f"ORIGINAL REQUEST:\n{original_prompt}\n\nINTENT ANSWERS:\n" + "\n\n".join(parts)
    ok, out, err = _safe_call(agent, merge_input, sys_msg)
    dur = int((time.perf_counter() - started) * 1000)

    ctx.put(name, out if ok else "\n\n".join(parts))
    if ok:
        yield _stage_event(name, "done", agent=agent, duration_ms=dur,
                           output_preview=out)
    else:
        yield _stage_event(name, "error", agent=agent, duration_ms=dur,
                           error=err, note="returned concatenated outputs")


def _stage_validator(
    original_prompt: str,
    available: dict,
    ctx: JobContext,
) -> Iterator[dict]:
    name = "validator"
    yield _stage_event(name, "queued")
    answer = ctx.get("synthesiser")
    if not answer:
        yield _stage_event(name, "skipped", note="no answer to validate")
        return
    if answer.strip() == SYNTHESIS_NO_OUTPUT:
        yield _stage_event(name, "skipped", note="synthesis produced no answer to validate")
        return

    sys_msg = (
        "Judge whether the answer addresses the user's request. Reply with "
        "exactly one of: PASS, PARTIAL, FAIL — followed by a one-sentence reason."
    )
    judge_input = f"REQUEST:\n{original_prompt}\n\nANSWER:\n{answer}"

    last_err = ""
    last_agent = None
    attempt_count = 0
    for agent in _iter_role_agents("validator", available, ctx):
        attempt_count += 1
        last_agent = agent
        yield _stage_event(name, "running", agent=agent)
        started = time.perf_counter()
        ok, out, err = _safe_call(agent, judge_input, sys_msg)
        dur = int((time.perf_counter() - started) * 1000)
        if ok:
            ctx.put(name, out)
            yield _stage_event(name, "done", agent=agent, duration_ms=dur,
                               output_preview=out,
                               note=f"validated on attempt {attempt_count}" if attempt_count > 1 else None)
            return
        last_err = err
        yield _stage_event(name, "retry", agent=agent, duration_ms=dur,
                           error=err, note="trying next agent in pool")

    if attempt_count == 0:
        yield _stage_event(name, "skipped", note="no validator model available")
    else:
        yield _stage_event(name, "error", agent=last_agent,
                           error=last_err,
                           note=f"all {attempt_count} agents in validator pool failed")


def _stage_security(available: dict, ctx: JobContext) -> Iterator[dict]:
    name = "security_scanner"
    yield _stage_event(name, "queued")
    answer = ctx.get("synthesiser")
    if not answer or "```" not in answer:
        yield _stage_event(name, "skipped", note="no code block in answer")
        return

    quick_findings: list[str] = []
    if re.search(r"(?i)\b(api[_-]?key|secret|token|password)\s*=\s*['\"]\w+", answer):
        quick_findings.append("hardcoded secret literal")
    if re.search(r"(?i)\beval\s*\(", answer):
        quick_findings.append("eval() use")
    if re.search(r"(?i)\bexec\s*\(", answer):
        quick_findings.append("exec() use")
    if re.search(r"(?i)\bshell\s*=\s*True", answer):
        quick_findings.append("subprocess shell=True")

    agent = _pick_role_agent("security_scanner", available, ctx)
    if not agent:
        ctx.put(name, json.dumps({"quick_findings": quick_findings, "llm_review": None}))
        yield _stage_event(name, "skipped",
                           note=f"no security model — quick scan only: {quick_findings or 'clean'}")
        return

    yield _stage_event(name, "running", agent=agent)
    started = time.perf_counter()
    sys_msg = (
        "You are a security-focused code reviewer. Inspect ONLY the fenced "
        "code blocks in the input. List concrete vulnerabilities (one per "
        "line, prefixed with '- '). If none, reply exactly: 'No issues found.'"
    )
    ok, out, err = _safe_call(agent, answer, sys_msg)
    dur = int((time.perf_counter() - started) * 1000)

    ctx.put(name, json.dumps({"quick_findings": quick_findings,
                              "llm_review": out if ok else None}))
    if ok:
        yield _stage_event(name, "done", agent=agent, duration_ms=dur,
                           output_preview=out)
    else:
        yield _stage_event(name, "error", agent=agent, duration_ms=dur,
                           error=err, note="quick scan only")


# ── Public entry point ──────────────────────────────────────────────────────

def _traces_dir() -> Path:
    """Where per-request RunTrace JSON files live."""
    repo_root = Path(__file__).resolve().parent.parent
    logs_env = os.environ.get("MLMAE_LOGS_DIR")
    if logs_env:
        base = Path(logs_env)
    else:
        data_env = os.environ.get("MLMAE_DATA_DIR")
        data_dir = Path(data_env) if data_env else repo_root / "prismara"
        if not data_dir.is_absolute():
            data_dir = repo_root / data_dir
        base = data_dir / "logs"
    p = base / "traces"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_trace(trace: dict) -> Optional[Path]:
    """Persist a trace to disk. Best-effort; never raises."""
    try:
        path = _traces_dir() / f"{trace['request_id']}.json"
        path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except Exception:
        return None


def orchestrate(
    user_prompt: str,
    codebase_context: Optional[dict] = None,
    request_id: Optional[str] = None,
    online_consent: bool = False,
) -> Iterator[dict]:
    """Run the local-first role pipeline for one request, yielding stream events.

    Yields event dicts ready for NDJSON serialisation. Final events are
    `final_answer`, `metrics`, `result`, `done` — matching what the UI
    already consumes. Stage progress is reported as `{type: "stage", ...}`.
    """
    request_id = request_id or secrets.token_hex(8)
    started_at = time.perf_counter()
    started_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
    repo_root = Path(__file__).resolve().parent.parent
    cache_root = Path(os.environ.get("MLMAE_CACHE_DIR", str(repo_root / "prismara" / "cache")))
    if not cache_root.is_absolute():
        cache_root = repo_root / cache_root
    spill_root = cache_root / "jobs" / request_id
    ctx = JobContext(
        request_id=request_id,
        spill_root=spill_root,
        online_consent=online_consent,
    )

    # Trace accumulator. Populated by _track() as events stream by; written
    # to prismara/logs/traces/{request_id}.json in finally so even crashed runs
    # leave evidence behind.
    trace: dict = {
        "request_id": request_id,
        "started_at": started_iso,
        "prompt": user_prompt[:2000],
        "prompt_truncated": len(user_prompt) > 2000,
        "online_consent": online_consent,
        "online_pipeline_mode": False,
        "workspace": (
            {"folder": codebase_context.get("folder"),
             "file_count": codebase_context.get("file_count")}
            if codebase_context else None
        ),
        "stages": [],                   # list of stage records (one per stage name)
        "intents": [],
        "intent_source": "",
        "models_used": [],
        "final_answer_preview": "",
        "disk_bytes_used": 0,
        "parallelism": {},
        "status": "incomplete",
        "error": "",
        "events": [],                   # full event log for deep debugging
    }
    _stages_by_name: dict[str, dict] = {}

    direct_answer = _deterministic_answer_if_simple(user_prompt)
    if direct_answer:
        process_ms = int((time.perf_counter() - started_at) * 1000)
        result_text = f"[USER REQUEST]: {user_prompt}\n\n[FINAL ANSWER]:\n{direct_answer}"
        metrics = {
            "type": "metrics",
            "request_id": request_id,
            "process_ms": process_ms,
            "models": ["System (Deterministic)"],
            "model_footprint": {"count": 1, "models": ["System (Deterministic)"]},
            "intents": ["direct"],
            "disk_bytes_used": 0,
            "parallelism": {},
        }
        direct_events = [
            {"type": "status", "message": f"orchestrator direct answer (request {request_id})"},
            {"type": "intents", "intents": ["direct"], "source": "deterministic"},
            {"type": "final_answer", "content": direct_answer},
            metrics,
            {"type": "result", "content": result_text, "final_answer": direct_answer},
            {"type": "done"},
        ]
        trace["intents"] = ["direct"]
        trace["intent_source"] = "deterministic"
        trace["models_used"] = ["System (Deterministic)"]
        trace["final_answer_preview"] = direct_answer
        trace["disk_bytes_used"] = 0
        trace["status"] = "ok"
        trace["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        trace["duration_ms"] = process_ms
        trace["events"] = direct_events
        _write_trace(trace)
        ctx.cleanup()
        for event in direct_events:
            yield event
        return

    yield {"type": "status", "message": f"orchestrator booting (request {request_id})"}

    # ── Snapshot agent availability once per request (cheap, ≤200 ms) ──
    try:
        available = detect_available_agents()
    except Exception as e:
        yield {"type": "error", "message": f"agent detection failed: {e}"}
        ctx.cleanup()
        return

    has_local = _any_local_available(available)
    has_online = _any_online_available(available)

    if not has_local:
        # Local pool empty. Two paths:
        #   1. Consent granted + at least one paid/free-cloud agent ready →
        #      run the pipeline against online providers (still requires the
        #      explicit per-request opt-in — this is NOT a silent escalation).
        #   2. Otherwise → emit a structured setup_required event so the UI
        #      can render an actionable Setup Required panel instead of a
        #      dead-end error message.
        if online_consent and has_online:
            ctx.online_pipeline_mode = True
            yield {
                "type": "online_pipeline_mode",
                "message": (
                    "No local agents available. Running this request on online "
                    "providers because you granted per-request consent. To run "
                    "fully locally, pull an Ollama model or configure a local "
                    "OpenAI-compatible endpoint via the admin panel."
                ),
            }
        else:
            yield {
                "type": "setup_required",
                "message": (
                    "No local agents are available."
                ),
                "detail": {
                    "has_local": False,
                    "has_online": has_online,
                    "online_consent_granted": bool(online_consent),
                    "options": [
                        {
                            "id": "ollama",
                            "title": "Install Ollama and pull a small model",
                            "steps": [
                                "Install Ollama (winget install Ollama.Ollama or download from ollama.com)",
                                "Pull a small model: `ollama pull phi3.5` (~2.4 GB)",
                                "Retry the request",
                            ],
                        },
                        {
                            "id": "custom_local",
                            "title": "Add a local OpenAI-compatible endpoint",
                            "steps": [
                                "Run LM Studio / vLLM / llamafile locally",
                                "Open Admin Control Panel → Custom Models",
                                "Pick the OpenAI-compatible template, point base_url at your local server",
                                "Save and retry the request",
                            ],
                        },
                        {
                            "id": "consent_online",
                            "title": "Use an online provider for this single request",
                            "steps": [
                                "Configure a paid API key in Admin → Credentials (OpenAI, Anthropic, or Google)" if not has_online else "An online provider is already configured",
                                "Tick 'Allow online assistance for this request only' below the prompt",
                                "Resubmit",
                            ],
                            "ready": bool(has_online),
                        },
                    ],
                },
            }
            ctx.cleanup()
            return

    obs = _observe("start", None, f"Received request: {user_prompt[:200]}")
    if obs:
        yield {"type": "warning", "message": obs}

    models_used: list[str] = []

    def _track(event: dict) -> dict:
        # Track unique models used across the run for the metrics event.
        if event.get("type") == "stage" and event.get("agent") and event["agent"] not in models_used:
            if "regex" not in event["agent"]:
                models_used.append(event["agent"])

        # Trace recording: keep a per-stage record and append every event verbatim.
        trace["events"].append({k: v for k, v in event.items() if k != "type"} | {"type": event.get("type")})
        if event.get("type") == "stage":
            sname = event.get("name") or "?"
            rec = _stages_by_name.get(sname)
            if rec is None:
                rec = {
                    "name": sname,
                    "status": event.get("status", "queued"),
                    "agent": event.get("agent"),
                    "duration_ms": event.get("duration_ms"),
                    "output_preview": event.get("output_preview"),
                    "note": event.get("note"),
                    "error": event.get("error"),
                    "attempts": [],
                }
                _stages_by_name[sname] = rec
                trace["stages"].append(rec)
            else:
                for k in ("status", "agent", "duration_ms", "output_preview", "note", "error"):
                    if event.get(k) is not None:
                        rec[k] = event.get(k)
            if event.get("status") in ("running", "retry", "done", "error"):
                rec["attempts"].append({
                    "agent": event.get("agent"),
                    "status": event.get("status"),
                    "duration_ms": event.get("duration_ms"),
                    "error": event.get("error"),
                })
        elif event.get("type") == "intents":
            trace["intents"] = event.get("intents") or []
            trace["intent_source"] = event.get("source", "")
        elif event.get("type") == "online_pipeline_mode":
            trace["online_pipeline_mode"] = True
        elif event.get("type") == "final_answer":
            trace["final_answer_preview"] = (event.get("content") or "")[:2000]
        elif event.get("type") == "error":
            trace["status"] = "error"
            trace["error"] = event.get("message", "")
        elif event.get("type") == "setup_required":
            trace["status"] = "setup_required"
        elif event.get("type") == "metrics":
            trace["disk_bytes_used"] = event.get("disk_bytes_used", 0)
        return event

    try:
        # ── Channelisation ──
        for ev in _stage_channeliser(user_prompt, available, ctx):
            yield _track(ev)
        intents_payload = json.loads(ctx.get("channeliser") or '{"intents": ["general"]}')
        intents: list[str] = intents_payload.get("intents") or ["general"]

        yield _track({"type": "intents", "intents": intents,
                      "source": intents_payload.get("source", "regex")})

        # ── Refinement ──
        for ev in _stage_refiner(user_prompt, available, ctx):
            yield _track(ev)
        refined = ctx.get("refiner") or user_prompt

        # ── Augmentation ──
        for ev in _stage_augmenter(refined, codebase_context, available, ctx):
            yield _track(ev)
        augmented = ctx.get("augmenter") or refined
        ctx.free("refiner")  # no longer needed

        # ── Processing (bounded parallel fan-out across independent intents) ──
        processor_limit, processor_policy = _parallel_limit(
            unit_count=len(intents),
            available=available,
            ctx=ctx,
            stage_group="processors",
        )
        trace["parallelism"]["processors"] = {"limit": processor_limit, **processor_policy}
        yield _track({
            "type": "status",
            "message": (
                f"processing {len(intents)} intent(s) with parallelism {processor_limit} "
                f"({processor_policy.get('reason')})"
            ),
            "parallelism": {"processors": {"limit": processor_limit, **processor_policy}},
        })
        processor_tasks = [
            (
                f"processor_{intent}",
                lambda intent=intent: _stage_processor(intent, augmented, available, ctx),
            )
            for intent in intents
        ]
        for ev in _run_stage_generators_parallel(processor_tasks, processor_limit):
            yield _track(ev)

        # ── Researcher (only if consent granted AND something failed locally) ──
        for ev in _stage_researcher(intents, augmented, available, ctx, online_consent):
            yield _track(ev)

        ctx.free("augmenter")

        # ── Summarisation (per intent) ──
        for ev in _stage_summariser(intents, available, ctx):
            yield _track(ev)

        # ── Synthesis ──
        for ev in _stage_synthesiser(intents, user_prompt, available, ctx):
            yield _track(ev)

        for intent in intents:
            ctx.free(f"summary_{intent}")

        # ── Validation + security scan (independent after synthesis) ──
        safety_tasks = [
            ("validator", lambda: _stage_validator(user_prompt, available, ctx)),
            ("security_scanner", lambda: _stage_security(available, ctx)),
        ]
        safety_limit, safety_policy = _parallel_limit(
            unit_count=len(safety_tasks),
            available=available,
            ctx=ctx,
            stage_group="safety",
        )
        trace["parallelism"]["safety"] = {"limit": safety_limit, **safety_policy}
        yield _track({
            "type": "status",
            "message": (
                f"running final checks with parallelism {safety_limit} "
                f"({safety_policy.get('reason')})"
            ),
            "parallelism": {"safety": {"limit": safety_limit, **safety_policy}},
        })
        for ev in _run_stage_generators_parallel(safety_tasks, safety_limit):
            yield _track(ev)

        # ── Finalise ──
        final_answer = ctx.get("synthesiser") or "No output produced — see stage errors above."
        validator_verdict = ctx.get("validator") or ""
        sec = ctx.get("security_scanner") or "{}"

        process_ms = int((time.perf_counter() - started_at) * 1000)

        report_lines = [
            f"[USER REQUEST]: {user_prompt}",
            f"[INTENTS]: {', '.join(intents)}",
            "-" * 50,
            "[FINAL ANSWER]:",
            final_answer,
        ]
        if validator_verdict:
            report_lines += ["", f"[VALIDATOR]: {validator_verdict}"]
        try:
            sec_obj = json.loads(sec)
            qf = sec_obj.get("quick_findings") or []
            if qf or sec_obj.get("llm_review"):
                report_lines += ["", "[SECURITY]:"]
                if qf:
                    report_lines += [f"  • {f}" for f in qf]
                if sec_obj.get("llm_review"):
                    report_lines += ["  " + line for line in sec_obj["llm_review"].splitlines()]
        except Exception:
            pass

        result_text = "\n".join(report_lines)

        yield {"type": "final_answer", "content": final_answer}
        yield {
            "type": "metrics",
            "request_id": request_id,
            "process_ms": process_ms,
            "models": models_used,
            "model_footprint": {"count": len(models_used), "models": models_used},
            "intents": intents,
            "disk_bytes_used": ctx.bytes_on_disk,
            "parallelism": trace.get("parallelism", {}),
        }
        yield {"type": "result", "content": result_text, "final_answer": final_answer}

        obs2 = _observe("done", None, f"Completed in {process_ms}ms with models: {', '.join(models_used)}")
        if obs2:
            yield {"type": "warning", "message": obs2}

        trace["status"] = "ok" if trace["status"] == "incomplete" else trace["status"]
        yield {"type": "done"}

    except Exception as e:
        trace["status"] = "error"
        trace["error"] = f"orchestrator crashed: {e}"
        yield {"type": "error", "message": f"orchestrator crashed: {e}"}
    finally:
        # Finalise trace and persist before cleanup so even crashes leave evidence.
        trace["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        trace["duration_ms"] = int((time.perf_counter() - started_at) * 1000)
        trace["models_used"] = list(models_used)
        trace["disk_bytes_used"] = max(trace.get("disk_bytes_used", 0), ctx.bytes_on_disk)
        _write_trace(trace)
        ctx.cleanup()
