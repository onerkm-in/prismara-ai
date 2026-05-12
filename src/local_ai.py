"""
Prismara AI Local AI manager
======================
Backs the Setup Local AI admin card. Detects Ollama state, downloads the
installer on demand, runs it silently, streams model pull progress, removes
models, and probes for OpenAI-compatible servers on common local ports.

Endpoint mapping (consumed by server/app.py and server/app_standalone.py):
    GET    /admin/local-ai/status         -> status() dict
    POST   /admin/local-ai/install        -> install_ollama() (Windows)
    POST   /admin/local-ai/pull-model     -> pull_model() NDJSON generator
    POST   /admin/local-ai/update-models  -> update_installed_models_stream()
    DELETE /admin/local-ai/model/<name>   -> delete_model(name)
    POST   /admin/local-ai/detect-custom  -> probe_custom_endpoints()
    GET    /settings/hardware             -> hardware_policy()
    POST   /settings/hardware             -> set_hardware_acceleration_consent()

Online providers are NEVER touched by this module — it manages only local
runtimes (Ollama + custom OpenAI-compatible endpoints). The orchestrator's
local-first policy and per-request consent flow remain unchanged.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator, Optional

from src.secure_storage import load_json, save_json

# ── Ollama daemon endpoint ────────────────────────────────────────────────────

def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except Exception:
        return default


OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
LOW_VRAM_GPU_GB = _float_env("MLMAE_MIN_USEFUL_GPU_VRAM_GB", 6.0)
MIN_USEFUL_GPU_MODEL_GB = 1.5

ROLE_WEIGHTS = {
    "channeliser": 1.0,
    "refiner": 1.1,
    "augmenter": 0.8,
    "processor_general": 1.4,
    "processor_code": 1.4,
    "processor_write": 1.1,
    "processor_reason": 1.0,
    "processor_vision": 0.35,
    "summariser": 1.0,
    "synthesiser": 1.2,
    "validator": 1.0,
    "security_scanner": 1.0,
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    configured = os.environ.get("MLMAE_DATA_DIR", "").strip()
    path = Path(configured) if configured else _repo_root() / "prismara"
    if not path.is_absolute():
        path = _repo_root() / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _config_path() -> Path:
    return _data_dir() / "config.json"


def _load_config() -> dict:
    return load_json(_config_path(), default={}) or {}


# ── Curated model catalog ────────────────────────────────────────────────────
# Each entry: ollama tag (what `ollama pull` accepts), the registry name in
# src.llm_client.AGENT_REGISTRY that matches it, approximate disk size, and
# which role pools the model fills. The frontend picker renders this list
# with role coverage so users can choose a meaningful starter set.

MODEL_CATALOG: list[dict] = [
    {
        "tag": "tinyllama",
        "registry_name": "TinyLlama (Local)",
        "size_gb": 0.64,
        "ram_gb": 2,
        "roles": ["channeliser"],
        "summary": "Ultra-light 1.1B. CPU-friendly. Fills the channeliser pool.",
    },
    {
        "tag": "qwen3:1.7b",
        "registry_name": "Qwen 3 1.7B (Local)",
        "size_gb": 1.4,
        "ram_gb": 2,
        "roles": ["channeliser", "refiner"],
        "summary": "Tiny Qwen3 — fast routing + prompt refinement.",
    },
    {
        "tag": "deepseek-r1:1.5b",
        "registry_name": "DeepSeek R1 1.5B (Local)",
        "size_gb": 1.1,
        "ram_gb": 2,
        "roles": ["processor_reason", "validator"],
        "summary": "Small reasoning model. Currently disabled by default on this Ollama build due to runner crashes.",
        "known_broken": True,
    },
    {
        "tag": "phi3.5",
        "registry_name": "Phi-3.5 Mini (Local)",
        "size_gb": 2.0,
        "ram_gb": 4,
        "roles": ["channeliser", "refiner", "processor_general", "summariser", "validator"],
        "summary": "Recommended starter — fills 5 role pools.",
        "recommended": True,
    },
    {
        "tag": "qwen3:4b",
        "registry_name": "Qwen 3 4B (Local)",
        "size_gb": 2.5,
        "ram_gb": 4,
        "roles": ["refiner", "augmenter", "processor_general", "summariser", "validator"],
        "summary": "Balanced 4B all-rounder.",
    },
    {
        "tag": "llama3.2",
        "registry_name": "Llama 3.2 (Local)",
        "size_gb": 2.0,
        "ram_gb": 4,
        "roles": ["channeliser", "refiner", "processor_general", "summariser", "validator"],
        "summary": "Meta's small Llama 3.2 — solid general fallback.",
    },
    {
        "tag": "gemma3",
        "registry_name": "Gemma 3 (Local)",
        "size_gb": 3.3,
        "ram_gb": 4,
        "roles": ["refiner", "augmenter", "processor_write", "processor_general"],
        "summary": "Google Gemma 3 4B — strong at writing tasks.",
    },
    {
        "tag": "qwen2.5-coder",
        "registry_name": "Qwen 2.5 Coder (Local)",
        "size_gb": 4.7,
        "ram_gb": 6,
        "roles": ["processor_code", "security_scanner"],
        "summary": "Code-focused 7B. Powers both code processor and security scanner.",
    },
    {
        "tag": "llama3.1:8b",
        "registry_name": "Llama 3.1 8B (Local)",
        "size_gb": 4.9,
        "ram_gb": 6,
        "roles": ["augmenter", "processor_write", "synthesiser"],
        "summary": "Meta Llama 3.1 8B — write + synthesise.",
    },
    {
        "tag": "mistral-nemo",
        "registry_name": "Mistral Nemo (Local)",
        "size_gb": 7.1,
        "ram_gb": 10,
        "roles": ["processor_write", "synthesiser", "processor_general"],
        "summary": "Mistral Nemo 12B — strong synthesis alternative.",
    },
    {
        "tag": "deepseek-coder",
        "registry_name": "DeepSeek Coder (Local)",
        "size_gb": 3.8,
        "ram_gb": 6,
        "roles": ["processor_code", "security_scanner"],
        "summary": "DeepSeek Coder 6.7B — code specialist alternative.",
    },
    {
        "tag": "llama3.2-vision",
        "registry_name": "Llama 3.2 Vision (Local)",
        "size_gb": 7.9,
        "ram_gb": 8,
        "roles": ["processor_vision"],
        "summary": "Multimodal — fills processor_vision. Only pull if you send images.",
    },
]


# ── Binary + daemon detection ────────────────────────────────────────────────

def _candidate_ollama_paths() -> list[Path]:
    paths: list[str] = []
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", "C:/Program Files")
        paths = [
            f"{local_app}/Programs/Ollama/ollama.exe",
            f"{program_files}/Ollama/ollama.exe",
        ]
    elif sys.platform == "darwin":
        paths = ["/usr/local/bin/ollama", "/opt/homebrew/bin/ollama"]
    else:
        paths = ["/usr/local/bin/ollama", "/usr/bin/ollama"]

    return [Path(p) for p in paths if p]


def detect_ollama_binary() -> Optional[str]:
    """Return the absolute path to the ollama binary, or None if not found."""
    on_path = shutil.which("ollama")
    if on_path:
        return on_path
    for cand in _candidate_ollama_paths():
        if cand.is_file():
            return str(cand)
    return None


def detect_ollama_daemon(timeout: float = 1.5) -> dict:
    """Probe the Ollama daemon. Returns running/version/models when up."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
        models = body.get("models", []) or []
        return {
            "running": True,
            "host": OLLAMA_HOST,
            "model_count": len(models),
            "models": [
                {
                    "name": m.get("name", ""),
                    "size_bytes": int(m.get("size", 0) or 0),
                    "modified_at": m.get("modified_at", ""),
                    "digest": (m.get("digest", "") or "")[:12],
                }
                for m in models
            ],
        }
    except Exception as e:
        return {"running": False, "host": OLLAMA_HOST, "error": str(e)}


def get_disk_free_gb(path: str | os.PathLike = "/") -> float:
    """Return free disk space (GB) on the volume containing path."""
    try:
        usage = shutil.disk_usage(str(path))
        return round(usage.free / (1024**3), 1)
    except Exception:
        return 0.0


def get_system_ram_gb() -> float:
    """Return installed RAM in GB using only the standard library."""
    try:
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return round(stat.ullTotalPhys / (1024**3), 1)
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            return round((pages * page_size) / (1024**3), 1)
    except Exception:
        pass
    return 0.0


def detect_cpu_profile() -> dict:
    """Return a compact CPU profile for the System Doctor."""
    name = (
        platform.processor()
        or os.environ.get("PROCESSOR_IDENTIFIER", "")
        or platform.machine()
        or "Unknown CPU"
    )
    return {
        "name": name,
        "architecture": platform.machine(),
        "logical_cores": os.cpu_count() or 0,
    }


def detect_gpu_profile() -> dict:
    """Detect GPU VRAM and whether Prismara AI should treat it as useful for Ollama."""
    gpus: list[dict] = []
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 2:
                    continue
                try:
                    vram_mb = int(float(parts[1]))
                except Exception:
                    vram_mb = 0
                gpus.append({
                    "name": parts[0],
                    "vram_mb": vram_mb,
                    "vram_gb": round(vram_mb / 1024, 1),
                    "driver": parts[2] if len(parts) > 2 else "",
                })
    except Exception:
        pass

    max_vram_gb = max((gpu.get("vram_gb", 0.0) for gpu in gpus), default=0.0)
    low_vram = bool(max_vram_gb and max_vram_gb < LOW_VRAM_GPU_GB)
    useful_for_inference = bool(max_vram_gb >= LOW_VRAM_GPU_GB)
    max_gpu_model_gb = max(0.0, max_vram_gb - 1.0) if useful_for_inference else 0.0
    ignored_for_inference = bool(gpus and not useful_for_inference)
    ignore_reason = ""
    if low_vram:
        ignore_reason = (
            f"Detected GPU VRAM is {max_vram_gb:g} GB, below the "
            f"{LOW_VRAM_GPU_GB:g} GB Prismara AI minimum. GPU is ignored and Ollama "
            "requests are forced to CPU with num_gpu=0."
        )
        note = (
            f"{ignore_reason} Optimize CPU, RAM, and SSD path."
        )
    elif useful_for_inference:
        note = f"GPU VRAM is {max_vram_gb:g} GB; some Ollama GPU offload may be useful."
    else:
        note = "No NVIDIA GPU was detected through nvidia-smi; using CPU-oriented recommendations."

    return {
        "gpus": gpus,
        "usable_gpus": gpus if useful_for_inference else [],
        "max_vram_gb": max_vram_gb,
        "useful_for_inference": useful_for_inference,
        "low_vram": low_vram,
        "ignored_for_inference": ignored_for_inference,
        "ignore_reason": ignore_reason,
        "max_gpu_model_gb": round(max_gpu_model_gb, 1),
        "note": note,
        "min_useful_vram_gb": LOW_VRAM_GPU_GB,
        "model_gpu_threshold_gb": MIN_USEFUL_GPU_MODEL_GB,
    }


def _catalog_for_hardware(gpu: dict, ram_gb: float) -> list[dict]:
    max_gpu_model_gb = float(gpu.get("max_gpu_model_gb", 0.0) or 0.0)
    low_vram = bool(gpu.get("low_vram"))
    known_broken = _known_broken_tags()
    out: list[dict] = []
    for item in MODEL_CATALOG:
        entry = dict(item)
        size_gb = float(entry.get("size_gb", 0.0) or 0.0)
        required_ram_gb = float(entry.get("ram_gb", 0.0) or 0.0)
        entry["known_broken"] = bool(entry.get("known_broken") or entry.get("tag") in known_broken)
        entry["cpu_feasible"] = bool(not ram_gb or required_ram_gb <= max(1.0, ram_gb - 2.0))
        entry["gpu_feasible"] = bool(not low_vram and max_gpu_model_gb and size_gb <= max_gpu_model_gb)
        entry["gpu_limited"] = bool(low_vram)
        if low_vram:
            entry["runtime_note"] = "CPU-only; low-VRAM GPU ignored"
        elif entry["gpu_feasible"]:
            entry["runtime_note"] = "May fit GPU offload"
        else:
            entry["runtime_note"] = "CPU/RAM path"
        out.append(entry)
    return out


def _known_broken_tags() -> set[str]:
    return {
        item.strip()
        for item in os.environ.get("MLMAE_KNOWN_BROKEN_MODELS", "deepseek-r1:1.5b").split(",")
        if item.strip()
    }


def _is_model_installed(tag: str, installed_names: set[str]) -> bool:
    base = tag.split(":")[0]
    return (
        tag in installed_names
        or f"{tag}:latest" in installed_names
        or base in {name.split(":")[0] for name in installed_names}
        or any(name.startswith(tag + ":") for name in installed_names)
    )


def _recommendation_profile(gpu: dict, ram_gb: float, policy: dict) -> dict:
    if policy.get("ollama_gpu_allowed"):
        return {
            "id": "gpu_assisted",
            "title": "GPU-assisted profile",
            "starter_roles": {
                "channeliser", "refiner", "augmenter", "processor_general",
                "processor_code", "processor_write", "summariser", "validator",
                "security_scanner",
            },
            "coverage_roles": {
                "channeliser", "refiner", "augmenter", "processor_general",
                "processor_code", "processor_write", "summariser", "synthesiser",
                "validator", "security_scanner",
            },
            "starter_budget_gb": 14.0,
            "coverage_budget_gb": 22.0,
            "summary": "GPU offload may help, so the recommendation favors stronger models that fit memory.",
        }
    if ram_gb and ram_gb < 3.5:
        return {
            "id": "minimum_cpu",
            "title": "Minimum CPU profile",
            "starter_roles": {"channeliser", "refiner"},
            "coverage_roles": {"channeliser", "refiner", "processor_general"},
            "starter_budget_gb": 2.2,
            "coverage_budget_gb": 3.5,
            "summary": "Very tight RAM detected. Start with tiny models and keep expectations modest.",
        }
    if ram_gb and ram_gb < 7:
        return {
            "id": "light_cpu",
            "title": "Light CPU profile",
            "starter_roles": {"channeliser", "refiner", "processor_general", "summariser", "validator"},
            "coverage_roles": {"channeliser", "refiner", "augmenter", "processor_general", "summariser", "validator"},
            "starter_budget_gb": 5.5,
            "coverage_budget_gb": 8.0,
            "summary": "CPU-first runtime with limited RAM. Use compact general models.",
        }
    if ram_gb and ram_gb < 11:
        return {
            "id": "balanced_cpu",
            "title": "Balanced CPU profile",
            "starter_roles": {
                "channeliser", "refiner", "processor_general", "processor_code",
                "summariser", "validator", "security_scanner",
            },
            "coverage_roles": {
                "channeliser", "refiner", "augmenter", "processor_general",
                "processor_code", "processor_write", "summariser", "synthesiser",
                "validator", "security_scanner",
            },
            "starter_budget_gb": 10.5,
            "coverage_budget_gb": 15.0,
            "summary": "CPU-first runtime with enough RAM for a practical coding/general set.",
        }

    suffix = ""
    if gpu.get("low_vram"):
        suffix = " The detected GPU has low VRAM, so this stays optimized for CPU, RAM, and SSD."
    elif not gpu.get("gpus"):
        suffix = " No supported GPU was detected, so this stays optimized for CPU, RAM, and SSD."
    return {
        "id": "expanded_cpu",
        "title": "Expanded CPU profile",
        "starter_roles": {
            "channeliser", "refiner", "augmenter", "processor_general",
            "processor_code", "processor_write", "summariser", "validator",
            "security_scanner",
        },
        "coverage_roles": {
            "channeliser", "refiner", "augmenter", "processor_general",
            "processor_code", "processor_write", "summariser", "synthesiser",
            "validator", "security_scanner",
        },
        "starter_budget_gb": 12.0,
        "coverage_budget_gb": 18.0,
        "summary": f"Enough RAM for a stronger local starter set.{suffix}",
    }


def _pick_models_for_roles(
    catalog: list[dict],
    target_roles: set[str],
    max_total_gb: float,
    seed: Optional[list[dict]] = None,
) -> list[dict]:
    selected: list[dict] = list(seed or [])
    covered: set[str] = {
        role for entry in selected for role in (entry.get("roles") or [])
    }
    total_gb = sum(float(entry.get("size_gb", 0.0) or 0.0) for entry in selected)

    candidates = [
        entry for entry in catalog
        if not entry.get("known_broken") and entry.get("cpu_feasible")
    ]

    while target_roles - covered:
        best: tuple[float, dict] | None = None
        for entry in candidates:
            if entry in selected:
                continue
            size_gb = float(entry.get("size_gb", 0.0) or 0.0)
            if total_gb + size_gb > max_total_gb:
                continue
            roles = set(entry.get("roles") or [])
            new_roles = roles & target_roles - covered
            if not new_roles:
                continue
            score = sum(ROLE_WEIGHTS.get(role, 0.8) for role in new_roles)
            if entry.get("recommended"):
                score += 0.3
            if entry.get("gpu_feasible"):
                score += 0.15
            score -= size_gb * 0.08
            score -= float(entry.get("ram_gb", 0.0) or 0.0) * 0.03
            if best is None or score > best[0]:
                best = (score, entry)
        if not best:
            break
        selected.append(best[1])
        covered.update(best[1].get("roles") or [])
        total_gb += float(best[1].get("size_gb", 0.0) or 0.0)

    return selected


def recommend_model_selection(
    catalog: list[dict],
    gpu: dict,
    ram_gb: float,
    disk_free_gb: float,
    installed_names: Optional[set[str]] = None,
    policy: Optional[dict] = None,
) -> dict:
    """Build a hardware-aware model recommendation plan for the admin UI."""
    installed = installed_names or set()
    active_policy = policy or hardware_policy(gpu)
    profile = _recommendation_profile(gpu, ram_gb, active_policy)
    disk_budget = max(0.0, float(disk_free_gb or 0.0) - 1.0) or 999.0
    starter_budget = min(float(profile["starter_budget_gb"]), disk_budget)
    coverage_budget = min(float(profile["coverage_budget_gb"]), disk_budget)

    starter = _pick_models_for_roles(catalog, set(profile["starter_roles"]), starter_budget)
    coverage = _pick_models_for_roles(catalog, set(profile["coverage_roles"]), coverage_budget, seed=starter)

    starter_tags = [entry["tag"] for entry in starter]
    coverage_tags = [entry["tag"] for entry in coverage]
    optional_tags: list[str] = []
    if ram_gb >= 8 and disk_budget >= 8:
        optional_tags = [
            entry["tag"] for entry in catalog
            if "processor_vision" in (entry.get("roles") or [])
            and not entry.get("known_broken")
            and entry.get("cpu_feasible")
        ][:1]

    recommendation_by_tag: dict[str, dict] = {}
    for idx, tag in enumerate(coverage_tags):
        tier = "starter" if tag in starter_tags else "coverage"
        recommendation_by_tag[tag] = {
            "tier": tier,
            "rank": idx + 1,
            "label": "Primary" if idx == 0 else ("Starter" if tier == "starter" else "Full coverage"),
            "reason": "Selected from detected RAM, GPU policy, disk budget, and role coverage.",
        }
    for tag in optional_tags:
        recommendation_by_tag.setdefault(tag, {
            "tier": "optional",
            "rank": len(recommendation_by_tag) + 1,
            "label": "Optional",
            "reason": "Useful only for image inputs; not part of the default starter set.",
        })

    covered_roles = sorted({
        role for entry in coverage for role in (entry.get("roles") or [])
        if role in set(profile["coverage_roles"])
    })
    missing_roles = sorted(set(profile["coverage_roles"]) - set(covered_roles))

    primary = starter_tags[0] if starter_tags else (coverage_tags[0] if coverage_tags else "")
    return {
        "profile_id": profile["id"],
        "profile_title": profile["title"],
        "summary": profile["summary"],
        "primary_tag": primary,
        "starter_tags": starter_tags,
        "coverage_tags": coverage_tags,
        "optional_tags": optional_tags,
        "missing_starter_tags": [tag for tag in starter_tags if not _is_model_installed(tag, installed)],
        "missing_coverage_tags": [tag for tag in coverage_tags if not _is_model_installed(tag, installed)],
        "installed_recommended_tags": [tag for tag in coverage_tags if _is_model_installed(tag, installed)],
        "starter_total_gb": round(sum(float(e.get("size_gb", 0.0) or 0.0) for e in starter), 1),
        "coverage_total_gb": round(sum(float(e.get("size_gb", 0.0) or 0.0) for e in coverage), 1),
        "covered_roles": covered_roles,
        "missing_roles": missing_roles,
        "recommendation_by_tag": recommendation_by_tag,
        "disk_budget_gb": round(disk_budget, 1) if disk_budget != 999.0 else 0.0,
    }


def hardware_policy(gpu: Optional[dict] = None) -> dict:
    """Return the current hardware-acceleration consent and runtime policy."""
    cfg = _load_config()
    detected_gpu = gpu or detect_gpu_profile()
    consent = bool(cfg.get("hardware_acceleration_consent", False))
    consent_at = int(cfg.get("hardware_acceleration_consent_at", 0) or 0)
    has_gpu = bool(detected_gpu.get("gpus"))
    gpu_useful = bool(detected_gpu.get("useful_for_inference"))
    low_vram = bool(detected_gpu.get("low_vram"))
    gpu_ignored = bool(low_vram or detected_gpu.get("ignored_for_inference"))
    gpu_allowed = bool(consent and gpu_useful and not gpu_ignored)

    if gpu_ignored:
        reason = (
            f"Detected GPU VRAM is below the {LOW_VRAM_GPU_GB:g} GB Prismara AI minimum. "
            "Prismara AI ignores this GPU and forces Ollama CPU mode to avoid stalls."
        )
    elif not has_gpu:
        reason = "Consent is stored, but no supported NVIDIA GPU was detected; CPU path is active."
    elif not consent:
        reason = "Hardware acceleration is off until an admin gives consent."
    elif not gpu_useful:
        reason = "Consent is stored, but the detected GPU is not useful for this runtime; CPU path is active."
    else:
        reason = "Consent is stored and the GPU looks useful; Ollama may use GPU offload automatically."

    return {
        "autodetect_enabled": True,
        "hardware_acceleration_consent": consent,
        "hardware_acceleration_consent_at": consent_at,
        "gpu_detected": has_gpu,
        "gpu_useful": gpu_useful,
        "gpu_low_vram": low_vram,
        "gpu_ignored": gpu_ignored,
        "min_useful_vram_gb": LOW_VRAM_GPU_GB,
        "consent_required": bool(gpu_useful and not consent),
        "ollama_gpu_allowed": gpu_allowed,
        "ollama_num_gpu": None if gpu_allowed else 0,
        "ollama_runtime": "auto_gpu" if gpu_allowed else "cpu",
        "reason": reason,
    }


def set_hardware_acceleration_consent(enabled: bool) -> dict:
    """Persist the admin's consent choice and return the resulting policy."""
    cfg = _load_config()
    detected_gpu = detect_gpu_profile()
    allow_consent = bool(enabled and not detected_gpu.get("ignored_for_inference"))
    cfg["hardware_acceleration_consent"] = allow_consent
    cfg["hardware_acceleration_consent_at"] = int(time.time()) if allow_consent else 0
    save_json(_config_path(), cfg, encode_at_rest=True)
    return hardware_policy(detected_gpu)


def ollama_runtime_options() -> dict:
    """Options passed to Ollama chat calls so GPU use follows consent policy."""
    try:
        if os.environ.get("MLMAE_FORCE_OLLAMA_CPU", "").strip().lower() in {"1", "true", "yes", "on"}:
            return {"num_gpu": 0}
        policy = hardware_policy()
        if policy.get("ollama_gpu_allowed"):
            return {}
    except Exception:
        pass
    return {"num_gpu": 0}


def ollama_data_dir() -> str:
    """Best-effort location of Ollama's model cache (for the disk gauge)."""
    if sys.platform == "win32":
        return os.environ.get("OLLAMA_MODELS",
                              str(Path.home() / ".ollama" / "models"))
    return os.environ.get("OLLAMA_MODELS",
                          str(Path.home() / ".ollama" / "models"))


# ── Custom OpenAI-compatible endpoint probe ──────────────────────────────────

_CUSTOM_PROBES: list[dict] = [
    {"name": "LM Studio",  "port": 1234, "path": "/v1/models"},
    {"name": "llamafile",  "port": 8080, "path": "/v1/models"},
    {"name": "vLLM",       "port": 8000, "path": "/v1/models"},
    {"name": "TGI",        "port": 3000, "path": "/v1/models"},
    {"name": "Jan",        "port": 1337, "path": "/v1/models"},
    {"name": "Koboldcpp",  "port": 5001, "path": "/v1/models"},
]


def _port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def probe_custom_endpoints(host: str = "127.0.0.1") -> list[dict]:
    """Probe well-known local ports for OpenAI-compatible servers.

    For each port that's open, attempt a GET on /v1/models to confirm it
    really speaks the OpenAI API. Returns the subset that responded.
    """
    found: list[dict] = []
    for probe in _CUSTOM_PROBES:
        if not _port_open(host, probe["port"]):
            continue
        url = f"http://{host}:{probe['port']}{probe['path']}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=1.5) as r:
                body = json.loads(r.read())
            data = body.get("data") or body.get("models") or []
            model_ids = [
                (m.get("id") or m.get("name") or "")
                for m in data
                if isinstance(m, dict)
            ]
            found.append({
                "name": probe["name"],
                "host": host,
                "port": probe["port"],
                "base_url": f"http://{host}:{probe['port']}/v1",
                "models": [mid for mid in model_ids if mid][:20],
            })
        except Exception:
            continue
    return found


# ── Ollama installer (Windows) ────────────────────────────────────────────────

def _installer_cache_path() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    cache_root = Path(os.environ.get("MLMAE_CACHE_DIR", str(repo_root / "prismara" / "cache")))
    if not cache_root.is_absolute():
        cache_root = repo_root / cache_root
    p = cache_root / "ollama-installer"
    p.mkdir(parents=True, exist_ok=True)
    return p / "OllamaSetup.exe"


def _managed_installer_candidates() -> list[Path]:
    """Installer locations Prismara AI can use before downloading from the internet."""
    candidates: list[Path] = []
    env_path = os.environ.get("MLMAE_OLLAMA_INSTALLER", "").strip()
    if env_path:
        candidates.append(Path(env_path))

    cache_path = _installer_cache_path()
    data_dir = _data_dir()
    candidates.extend([
        cache_path,
        data_dir / "OllamaSetup.exe",
        data_dir / "runtime" / "OllamaSetup.exe",
        data_dir.parent / "OllamaSetup.exe",
        _repo_root() / "vendor" / "OllamaSetup.exe",
        _repo_root() / "runtime" / "OllamaSetup.exe",
    ])

    bundle_root = Path(getattr(sys, "_MEIPASS", _repo_root()))
    candidates.extend([
        bundle_root / "vendor" / "OllamaSetup.exe",
        bundle_root / "runtime" / "OllamaSetup.exe",
    ])
    return candidates


def managed_installer_status() -> dict:
    """Return whether an offline/cached Ollama installer is already available."""
    cache_path = _installer_cache_path()
    for candidate in _managed_installer_candidates():
        try:
            path = candidate if candidate.is_absolute() else _repo_root() / candidate
            if path.is_file() and path.stat().st_size > 0:
                return {
                    "available": True,
                    "path": str(path),
                    "source": "cache" if path.resolve() == cache_path.resolve() else "bundled_or_adjacent",
                    "size_mb": round(path.stat().st_size / (1024 * 1024), 1),
                }
        except Exception:
            continue
    return {
        "available": False,
        "path": str(cache_path),
        "source": "download_required",
        "size_mb": 0,
    }


def download_ollama_installer(progress_cb=None) -> Path:
    """Download the Ollama installer to prismara/cache/ollama-installer/.

    Calls progress_cb(downloaded_bytes, total_bytes) periodically if given.
    Returns the local path to the downloaded installer.
    """
    out = _installer_cache_path()
    managed = managed_installer_status()
    if managed.get("available"):
        src = Path(str(managed.get("path", "")))
        if src.is_file():
            if src.resolve() != out.resolve():
                shutil.copy2(src, out)
            size = out.stat().st_size
            if progress_cb:
                progress_cb(size, size)
            return out

    req = urllib.request.Request(OLLAMA_INSTALLER_URL, headers={"User-Agent": "PrismaraAI/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        total = int(resp.headers.get("Content-Length", "0") or 0)
        chunk = 1 << 16  # 64 KB
        downloaded = 0
        with open(out, "wb") as f:
            while True:
                data = resp.read(chunk)
                if not data:
                    break
                f.write(data)
                downloaded += len(data)
                if progress_cb:
                    try:
                        progress_cb(downloaded, total)
                    except Exception:
                        pass
    return out


def install_ollama_silent(installer_path: Path) -> int:
    """Run the Inno Setup installer silently. Per-user install, no UAC.

    Returns the installer exit code (0 = success).
    """
    if sys.platform != "win32":
        raise RuntimeError("Silent Ollama install via this helper is Windows-only.")
    proc = subprocess.run(
        [str(installer_path), "/VERYSILENT", "/NORESTART"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    return proc.returncode


def install_ollama_stream() -> Iterator[dict]:
    """Run the full download + install flow, yielding NDJSON-ready events.

    Yields:
        {"type": "install", "phase": "downloading", "downloaded": int, "total": int}
        {"type": "install", "phase": "running"}
        {"type": "install", "phase": "verifying"}
        {"type": "install", "phase": "done", "ollama_path": str, "version": str}
        {"type": "install", "phase": "error", "message": str}
    """
    if sys.platform != "win32":
        yield {"type": "install", "phase": "error",
               "message": "Automated install is Windows-only. On macOS/Linux, "
                          "use brew install ollama or the curl script from ollama.com."}
        return

    # Phase 1: download
    last_emitted = {"value": 0}
    out_path = None

    def _send_progress(done: int, total: int):
        # Buffer to avoid event storm — emit every 1 MB or 5% jump
        step = max(1, total // 20) if total else 1 << 20
        if done - last_emitted["value"] >= step or (total and done >= total):
            last_emitted["value"] = done

    try:
        yield {"type": "install", "phase": "downloading", "downloaded": 0, "total": 0}
        out_path = download_ollama_installer(progress_cb=_send_progress)
        size_mb = out_path.stat().st_size / (1024 * 1024)
        yield {"type": "install", "phase": "downloaded",
               "downloaded_mb": round(size_mb, 1)}
    except Exception as e:
        yield {"type": "install", "phase": "error",
               "message": f"download failed: {e}"}
        return

    # Phase 2: silent install
    try:
        yield {"type": "install", "phase": "running"}
        rc = install_ollama_silent(out_path)
        if rc != 0:
            yield {"type": "install", "phase": "error",
                   "message": f"installer exit code {rc}"}
            return
    except Exception as e:
        yield {"type": "install", "phase": "error",
               "message": f"installer crashed: {e}"}
        return

    # Phase 3: verify daemon
    yield {"type": "install", "phase": "verifying"}
    deadline = time.time() + 30
    while time.time() < deadline:
        info = detect_ollama_daemon(timeout=1.0)
        if info.get("running"):
            yield {
                "type": "install",
                "phase": "done",
                "ollama_path": detect_ollama_binary() or "",
                "host": info["host"],
            }
            return
        time.sleep(0.5)

    yield {"type": "install", "phase": "error",
           "message": "Daemon did not respond on 11434 within 30 s after install."}


# ── Model pull stream (proxy to Ollama /api/pull) ────────────────────────────

def pull_model_stream(name: str) -> Iterator[dict]:
    """Pull a model via Ollama's streaming API, yielding NDJSON events.

    Each yielded dict is wrapped with type='pull' so the UI can distinguish
    it from other stream types on the same channel.
    """
    if not name or not name.strip():
        yield {"type": "pull", "phase": "error", "message": "missing model name"}
        return

    info = detect_ollama_daemon(timeout=1.0)
    if not info.get("running"):
        yield {"type": "pull", "phase": "error",
               "message": "Ollama daemon is not running. Install or start it first."}
        return

    body = json.dumps({"name": name.strip(), "stream": True}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/pull",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            for raw in r:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    evt = json.loads(raw)
                except Exception:
                    continue
                # Pass-through with our type marker. Ollama's progress events
                # include status, digest, total, completed. We also tag a
                # synthetic phase so the UI can render a state machine.
                status = (evt.get("status") or "").lower()
                phase = "downloading"
                if status.startswith("pulling manifest"):
                    phase = "manifest"
                elif "success" in status or status == "success":
                    phase = "done"
                elif "verifying" in status:
                    phase = "verifying"
                elif status.startswith("writing"):
                    phase = "writing"
                yield {
                    "type": "pull",
                    "phase": phase,
                    "model": name,
                    "status": evt.get("status", ""),
                    "completed": int(evt.get("completed", 0) or 0),
                    "total": int(evt.get("total", 0) or 0),
                    "digest": (evt.get("digest", "") or "")[:12],
                }
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(e)
        yield {"type": "pull", "phase": "error",
               "model": name, "message": f"HTTP {e.code}: {body[:300]}"}
    except Exception as e:
        yield {"type": "pull", "phase": "error",
               "model": name, "message": str(e)}


def update_installed_models_stream(names: Optional[list[str]] = None) -> Iterator[dict]:
    """Re-pull installed Ollama models so Ollama can fetch newer manifests/layers."""
    info = detect_ollama_daemon(timeout=1.0)
    if not info.get("running"):
        yield {"type": "update", "phase": "error",
               "message": "Ollama daemon is not running. Install or start it first."}
        return

    installed = [m.get("name", "") for m in info.get("models", []) if m.get("name")]
    source = names if names is not None else installed
    requested = [str(n).strip() for n in source if str(n).strip()]
    models = [m for m in requested if m in installed] if names is not None else requested
    if not models:
        yield {"type": "update", "phase": "error", "message": "No installed models to update."}
        return

    total_models = len(models)
    for index, model in enumerate(models, start=1):
        yield {
            "type": "update",
            "phase": "model_start",
            "model": model,
            "index": index,
            "total_models": total_models,
            "status": "checking manifest",
        }
        model_failed = False
        for event in pull_model_stream(model):
            if event.get("type") != "pull":
                continue
            if event.get("phase") == "error":
                model_failed = True
            yield {
                **event,
                "type": "update",
                "index": index,
                "total_models": total_models,
            }
        yield {
            "type": "update",
            "phase": "model_error" if model_failed else "model_done",
            "model": model,
            "index": index,
            "total_models": total_models,
        }

    yield {"type": "update", "phase": "done", "total_models": total_models}


def delete_model(name: str) -> tuple[bool, str]:
    """Remove a pulled model via Ollama's /api/delete. Returns (ok, message)."""
    if not name:
        return False, "missing model name"
    body = json.dumps({"name": name}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/delete",
        data=body,
        headers={"Content-Type": "application/json"},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200, "deleted"
    except urllib.error.HTTPError as e:
        try:
            return False, e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


# ── Composite status (drives the admin card's state machine) ─────────────────

def status() -> dict:
    """Return a single dict the admin card can render directly."""
    cpu = detect_cpu_profile()
    binary = detect_ollama_binary()
    daemon = detect_ollama_daemon()
    custom = probe_custom_endpoints()
    gpu = detect_gpu_profile()
    ram_gb = get_system_ram_gb()
    policy = hardware_policy(gpu)

    model_dir = ollama_data_dir()
    disk_free = get_disk_free_gb(model_dir if Path(model_dir).exists() else Path.home())

    # Build a quick role-coverage map from pulled models + catalog.
    pulled_tags = {(m.get("name") or "").split(":")[0] for m in daemon.get("models", [])}
    pulled_tags_full = {(m.get("name") or "") for m in daemon.get("models", [])}
    role_coverage: dict[str, list[str]] = {}
    known_broken = _known_broken_tags()
    for entry in MODEL_CATALOG:
        if entry["tag"] in known_broken:
            continue
        base = entry["tag"].split(":")[0]
        installed = (entry["tag"] in pulled_tags_full
                     or base in pulled_tags
                     or any(t.startswith(entry["tag"] + ":") for t in pulled_tags_full))
        if installed:
            for role in entry["roles"]:
                role_coverage.setdefault(role, []).append(entry["registry_name"])

    catalog = _catalog_for_hardware(gpu, ram_gb)
    recommendation = recommend_model_selection(
        catalog,
        gpu,
        ram_gb,
        disk_free,
        installed_names=pulled_tags_full,
        policy=policy,
    )
    rec_by_tag = recommendation.get("recommendation_by_tag", {})
    for entry in catalog:
        rec = rec_by_tag.get(entry.get("tag"))
        if rec:
            entry["hardware_recommended"] = True
            entry["recommendation_tier"] = rec.get("tier", "")
            entry["recommendation_label"] = rec.get("label", "")
            entry["recommendation_rank"] = rec.get("rank", 0)
            entry["recommendation_reason"] = rec.get("reason", "")

    return {
        "platform": platform.system().lower(),
        "cpu": cpu,
        "binary_path": binary,
        "installed": bool(binary),
        "daemon": daemon,
        "custom_endpoints": custom,
        "disk_free_gb": disk_free,
        "system_ram_gb": ram_gb,
        "gpu": gpu,
        "hardware_policy": policy,
        "model_dir": model_dir,
        "catalog": catalog,
        "model_recommendation": recommendation,
        "role_coverage": role_coverage,
    }


def _expected_speed_for(status_payload: dict) -> dict:
    gpu = status_payload.get("gpu") or {}
    policy = status_payload.get("hardware_policy") or {}
    ram_gb = float(status_payload.get("system_ram_gb") or 0.0)
    daemon_up = bool((status_payload.get("daemon") or {}).get("running"))
    installed = bool(status_payload.get("installed"))

    if policy.get("ollama_gpu_allowed"):
        local_models = "GPU-assisted when Ollama can offload layers; speed still depends on model size."
        label = "GPU-assisted"
        concurrency = "2-3 lightweight local jobs"
    elif gpu.get("low_vram"):
        local_models = "CPU-first. Small local models can take 20-120 seconds on longer prompts."
        label = "CPU-first, low-VRAM GPU ignored"
        concurrency = "1 local LLM job, plus deterministic stages"
    elif ram_gb >= 16:
        local_models = "CPU-first. Small local models usually fit; expect tens of seconds for full pipeline work."
        label = "CPU-capable"
        concurrency = "1-2 local LLM jobs"
    elif ram_gb >= 8:
        local_models = "CPU-first. Prefer compact models; expect longer waits on full pipeline work."
        label = "Compact CPU"
        concurrency = "1 local LLM job"
    else:
        local_models = "Constrained. Prefer TinyLlama or cloud fallback with consent for larger tasks."
        label = "Constrained"
        concurrency = "1 lightweight local job"

    runtime = "Ready" if installed and daemon_up else ("Installed but not running" if installed else "Runtime setup required")
    return {
        "label": label,
        "runtime": runtime,
        "simple_prompts": "Instant deterministic path for greetings, time/date, and simple acknowledgements.",
        "local_models": local_models,
        "recommended_parallelism": concurrency,
        "note": "Prismara AI skips the noisy multi-agent pipeline for simple prompts and uses stages only when the request needs them.",
    }


def system_doctor() -> dict:
    """Product-level first-run health report for the main UI."""
    st = status()
    recommendation = st.get("model_recommendation") or {}
    daemon = st.get("daemon") or {}
    gpu = st.get("gpu") or {}
    policy = st.get("hardware_policy") or {}
    installer = managed_installer_status()

    missing_starter = recommendation.get("missing_starter_tags") or []
    starter_tags = recommendation.get("starter_tags") or []
    runtime_ready = bool(st.get("installed") and daemon.get("running"))
    starter_ready = bool(starter_tags and not missing_starter)

    checklist = [
        {
            "id": "portable_folder",
            "label": "Portable data folder",
            "state": "ok",
            "detail": str(_data_dir()),
        },
        {
            "id": "encrypted_state",
            "label": "Encrypted local state",
            "state": "ok",
            "detail": "Chat history, credentials, config, and memory use machine-bound AES-GCM storage.",
        },
        {
            "id": "runtime",
            "label": "Local model runtime",
            "state": "ok" if runtime_ready else ("warn" if st.get("installed") else "action"),
            "detail": "Ollama is installed and running." if runtime_ready else (
                "Ollama is installed but the daemon is not responding." if st.get("installed")
                else "Install the managed Ollama runtime to run offline local models."
            ),
        },
        {
            "id": "model_pack",
            "label": "Recommended model pack",
            "state": "ok" if starter_ready else "action",
            "detail": (
                "Starter pack is installed."
                if starter_ready
                else f"Missing {len(missing_starter)} recommended starter model(s)."
            ),
        },
        {
            "id": "hardware_consent",
            "label": "Hardware consent",
            "state": "ok" if policy.get("ollama_gpu_allowed") else "info",
            "detail": policy.get("reason", "CPU runtime policy is active."),
        },
    ]

    prereq_note = (
        "The Prismara AI exe bundles the app runtime, so users do not need Python, Node, npm, or PyInstaller. "
        "Offline local inference still needs an Ollama-compatible model engine; Prismara AI can use an offline "
        "bundled/adjacent installer when present, otherwise it downloads the installer once and stores models "
        "inside the exe-adjacent prismara/ folder."
    )

    return {
        "generated_at": int(time.time()),
        "local_ai": st,
        "expected_speed": _expected_speed_for(st),
        "recommended_pack": {
            "title": recommendation.get("profile_title") or "Hardware-aware model pack",
            "summary": recommendation.get("summary", ""),
            "primary_tag": recommendation.get("primary_tag", ""),
            "starter_tags": starter_tags,
            "missing_starter_tags": missing_starter,
            "starter_total_gb": recommendation.get("starter_total_gb", 0),
            "coverage_tags": recommendation.get("coverage_tags", []),
            "missing_coverage_tags": recommendation.get("missing_coverage_tags", []),
            "coverage_total_gb": recommendation.get("coverage_total_gb", 0),
        },
        "runtime_setup": {
            "runtime": "Ollama",
            "app_portable": True,
            "python_node_required_for_users": False,
            "offline_installer_available": bool(installer.get("available")),
            "installer_source": installer.get("source", "download_required"),
            "installer_path": installer.get("path", ""),
            "model_storage": st.get("model_dir", ""),
            "prereq_note": prereq_note,
        },
        "checklist": checklist,
        "trust": {
            "license": "Apache-2.0",
            "copyright": "Copyright 2026 Rajesh Kumar Mohanty",
            "local_only_default": True,
            "stays_local": [
                "Chat history",
                "Credentials",
                "Workspace index and memory",
                "Logs and recovery packs",
                "Ollama model weights",
            ],
            "encrypted": "Machine-bound AES-GCM storage in the exe-adjacent prismara/ folder.",
            "online": "Online providers are used only when the user grants consent for the request or configures cloud integrations.",
            "transfer": (
                "Transfer mode opens a time-limited rebind window for encrypted Prismara AI state. "
                "It does not stop a device administrator or the operating system from copying raw files."
            ),
            "paths": {
                "data": str(_data_dir()),
                "models": st.get("model_dir", ""),
            },
        },
    }
