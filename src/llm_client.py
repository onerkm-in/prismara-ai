"""
Prismara AI Unified LLM Client
========================
Supports — in cost order (cheapest first):

  LOCAL (FREE, no internet):
    Ollama  — llama3.2, mistral, deepseek-coder, qwen2.5-coder, phi3, gemma3/4, ...
              Requires Ollama installed: https://ollama.com
              Detected automatically if running on http://localhost:11434
    LM Studio, Llamafile, Jan, Koboldcpp — any OpenAI-compatible local server
              Add as Custom Model pointing to http://localhost:{port}/v1

  FREE CLOUD:
    Groq    — llama-3.3-70b, mixtral-8x7b, gemma2-9b
              Free tier, very generous limits. Needs GROQ_API_KEY (free signup).
    OpenRouter (free models) — llama-3.1-8b:free, deepseek-r1:free, gemma-4:free
              Needs OPENROUTER_API_KEY (free tier available).
    Sarvam  — sarvam-m
              Free chat model. Needs SARVAM_API_KEY (free credits / limits apply).

  PAID CLOUD:
    OpenAI   — gpt-4o, gpt-4o-mini        (OPENAI_API_KEY)
    Anthropic — claude-3-5-sonnet          (ANTHROPIC_API_KEY)
    Google   — gemini-2.5-pro, gemma-4     (GOOGLE_API_KEY)
    DeepSeek — deepseek-chat, deepseek-r1  (DEEPSEEK_API_KEY)
    Cohere   — command-r-plus              (COHERE_API_KEY)
    Sarvam   — sarvam-30b, sarvam-105b     (SARVAM_API_KEY)

  CUSTOM (user-defined):
    Any OpenAI-compatible endpoint — e.g. Azure OpenAI, Together AI, Fireworks AI,
    Perplexity, Anyscale, a corporate LLM gateway, or any self-hosted API.
    Supports static API keys OR SSO OAuth2 bearer tokens (Azure AD, Okta, etc.).
    Add via the Settings → Custom Models panel in the UI.
    Custom models are stored in prismara/custom_models.json.

Usage:
    from src.llm_client import call_llm, detect_available_agents, AGENT_REGISTRY

    response = call_llm(agent_name="Llama 3.2 (Local)", prompt="Hello!")
    agents   = detect_available_agents()
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
import urllib.error
from typing import Optional

from pathlib import Path

from src.secure_storage import load_json, save_json

logger = logging.getLogger(__name__)


def _credentials_path() -> Path:
    """Resolve the shared credentials file in dev and packaged modes."""
    repo_root = Path(__file__).resolve().parent.parent
    explicit = os.environ.get("MLMAE_CREDENTIALS_FILE", "").strip()
    if explicit:
        path = Path(explicit)
        return path if path.is_absolute() else repo_root / path
    data_dir = os.environ.get("MLMAE_DATA_DIR", "").strip()
    if data_dir:
        path = Path(data_dir)
        if not path.is_absolute():
            path = repo_root / path
        return path / "credentials.json"
    return repo_root / "prismara" / "credentials.json"


def load_saved_credentials_into_env(*, overwrite: bool = False) -> int:
    """Load scalar saved credentials into os.environ for provider SDKs.

    Admin settings persist provider keys in credentials.json, while most SDK
    calls read from environment variables. Loading them here keeps dev,
    packaged, and CLI entry points consistent without exposing values in API
    responses or logs.
    """
    path = _credentials_path()
    if not path.exists():
        return 0
    try:
        creds = load_json(path, default={}) or {}
    except Exception as exc:
        logger.warning("Could not load saved credentials: %s", exc)
        return 0
    loaded = 0
    for key, value in creds.items():
        if not isinstance(key, str) or not key.isupper():
            continue
        if isinstance(value, (dict, list, tuple, set)) or value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if overwrite or not os.environ.get(key):
            os.environ[key] = text
            loaded += 1
    return loaded


def _known_broken_ollama_models() -> set[str]:
    raw = os.environ.get("MLMAE_KNOWN_BROKEN_MODELS", "deepseek-r1:1.5b")
    return {item.strip() for item in raw.split(",") if item.strip()}

# ── Agent Registry ────────────────────────────────────────────────────────────
# Each entry describes one available agent and how to call it.
# order = priority when multiple agents satisfy the same capability tier.

AGENT_REGISTRY: dict[str, dict] = {

    # ── Local / Ollama ────────────────────────────────────────────────────
    "Llama 3.2 (Local)": {
        "provider": "ollama",
        "model": "llama3.2",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Meta Llama 3.2 running locally via Ollama. No internet required.",
    },
    "Mistral 7B (Local)": {
        "provider": "ollama",
        "model": "mistral",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Mistral 7B running locally via Ollama.",
    },
    "DeepSeek Coder (Local)": {
        "provider": "ollama",
        "model": "deepseek-coder",
        "tier": "local",
        "capabilities": ["code"],
        "cost": "free",
        "description": "DeepSeek Coder running locally via Ollama. Best for code tasks.",
    },
    "Qwen 2.5 Coder (Local)": {
        "provider": "ollama",
        "model": "qwen2.5-coder",
        "tier": "local",
        "capabilities": ["code"],
        "cost": "free",
        "description": "Qwen 2.5 Coder running locally via Ollama.",
    },
    "Phi-3 (Local)": {
        "provider": "ollama",
        "model": "phi3",
        "tier": "local",
        "capabilities": ["general", "code"],
        "cost": "free",
        "description": "Microsoft Phi-3 running locally via Ollama. Fast & lightweight.",
    },
    "Gemma 3 (Local)": {
        "provider": "ollama",
        "model": "gemma3",
        "tier": "local",
        "capabilities": ["general", "write"],
        "cost": "free",
        "description": "Google Gemma 3 4B running locally via Ollama. Great all-rounder.",
        "vram_gb": 4,
    },
    "Gemma 3 12B (Local)": {
        "provider": "ollama",
        "model": "gemma3:12b",
        "tier": "local",
        "capabilities": ["general", "write", "code"],
        "cost": "free",
        "description": "Google Gemma 3 12B via Ollama. Smarter, needs ~8 GB VRAM.",
        "vram_gb": 8,
    },
    "Gemma 3 27B (Local)": {
        "provider": "ollama",
        "model": "gemma3:27b",
        "tier": "local",
        "capabilities": ["general", "write", "code"],
        "cost": "free",
        "description": "Google Gemma 3 27B via Ollama. Near-GPT-4 quality locally. Needs ~16 GB VRAM.",
        "vram_gb": 16,
    },
    # Gemma 4 — Google's latest (2025). Pull: ollama pull gemma4
    "Gemma 4 (Local)": {
        "provider": "ollama",
        "model": "gemma4",
        "tier": "local",
        "capabilities": ["general", "write", "code"],
        "cost": "free",
        "description": "Google Gemma 4 running locally via Ollama. Best-in-class local Google model.",
        "vram_gb": 6,
    },
    "Gemma 4 27B (Local)": {
        "provider": "ollama",
        "model": "gemma4:27b",
        "tier": "local",
        "capabilities": ["general", "write", "code"],
        "cost": "free",
        "description": "Google Gemma 4 27B via Ollama. Flagship local Google model. Needs ~20 GB VRAM.",
        "vram_gb": 20,
    },
    # CodeGemma — code-specialized Gemma variant
    "CodeGemma 7B (Local)": {
        "provider": "ollama",
        "model": "codegemma",
        "tier": "local",
        "capabilities": ["code"],
        "cost": "free",
        "description": "Google CodeGemma 7B via Ollama. Specialised for code completion and generation.",
        "vram_gb": 5,
    },
    # DeepSeek V3 & R1 local
    "DeepSeek V3 (Local)": {
        "provider": "ollama",
        "model": "deepseek-v3",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "DeepSeek V3 via Ollama. State-of-the-art open model, needs high-end GPU.",
        "vram_gb": 24,
    },
    "DeepSeek R1 14B (Local)": {
        "provider": "ollama",
        "model": "deepseek-r1:14b",
        "tier": "local",
        "capabilities": ["general", "code"],
        "cost": "free",
        "description": "DeepSeek R1 14B reasoning model via Ollama. Great for step-by-step reasoning.",
        "vram_gb": 10,
    },
    "DeepSeek R1 7B (Local)": {
        "provider": "ollama",
        "model": "deepseek-r1:7b",
        "tier": "local",
        "capabilities": ["general", "code"],
        "cost": "free",
        "description": "DeepSeek R1 7B reasoning model via Ollama. Runs on most consumer GPUs.",
        "vram_gb": 5,
    },
    "DeepSeek R1 1.5B (Local)": {
        "provider": "ollama",
        "model": "deepseek-r1:1.5b",
        "tier": "local",
        "capabilities": ["general", "code"],
        "cost": "free",
        "description": "DeepSeek R1 distilled 1.5B reasoning model via Ollama. Tiny CPU-friendly option.",
        "vram_gb": 2,
    },
    "DeepSeek R1 8B (Local)": {
        "provider": "ollama",
        "model": "deepseek-r1:8b",
        "tier": "local",
        "capabilities": ["general", "code"],
        "cost": "free",
        "description": "DeepSeek R1 8B reasoning model via Ollama. Strong compact reasoning option.",
        "vram_gb": 6,
    },
    "DeepSeek R1 32B (Local)": {
        "provider": "ollama",
        "model": "deepseek-r1:32b",
        "tier": "local",
        "capabilities": ["general", "code"],
        "cost": "free",
        "description": "DeepSeek R1 32B reasoning model via Ollama. Higher quality local reasoning.",
        "vram_gb": 24,
    },
    "DeepSeek R1 70B (Local)": {
        "provider": "ollama",
        "model": "deepseek-r1:70b",
        "tier": "local",
        "capabilities": ["general", "code"],
        "cost": "free",
        "description": "DeepSeek R1 70B distilled model via Ollama. Large local reasoning model.",
        "vram_gb": 40,
    },
    # Llama variants
    "Llama 3.1 8B (Local)": {
        "provider": "ollama",
        "model": "llama3.1:8b",
        "tier": "local",
        "capabilities": ["general", "write"],
        "cost": "free",
        "description": "Meta Llama 3.1 8B via Ollama. Solid all-round local model.",
        "vram_gb": 5,
    },
    "Llama 3.1 70B (Local)": {
        "provider": "ollama",
        "model": "llama3.1:70b",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Meta Llama 3.1 70B via Ollama. Near GPT-4 quality. Needs 40+ GB VRAM or CPU offload.",
        "vram_gb": 40,
    },
    "Llama 3.2 Vision (Local)": {
        "provider": "ollama",
        "model": "llama3.2-vision",
        "tier": "local",
        "capabilities": ["general", "vision"],
        "cost": "free",
        "description": "Meta Llama 3.2 Vision via Ollama. Multimodal: can describe images.",
        "vram_gb": 6,
    },
    "Llama 4 Scout (Local)": {
        "provider": "ollama",
        "model": "llama4:scout",
        "tier": "local",
        "capabilities": ["general", "code", "write", "vision"],
        "cost": "free",
        "description": "Meta Llama 4 Scout via Ollama. Large multimodal MoE model for long-context tasks.",
        "vram_gb": 48,
    },
    "Llama 4 Maverick (Local)": {
        "provider": "ollama",
        "model": "llama4:maverick",
        "tier": "local",
        "capabilities": ["general", "code", "write", "vision"],
        "cost": "free",
        "description": "Meta Llama 4 Maverick via Ollama. Very large multimodal MoE model.",
        "vram_gb": 80,
    },
    # Qwen family
    "Qwen 2.5 72B (Local)": {
        "provider": "ollama",
        "model": "qwen2.5:72b",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Alibaba Qwen 2.5 72B via Ollama. Excellent multilingual and coding model.",
        "vram_gb": 40,
    },
    "Qwen 2.5 7B (Local)": {
        "provider": "ollama",
        "model": "qwen2.5:7b",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Alibaba Qwen 2.5 7B via Ollama. Very capable small model with multilingual support.",
        "vram_gb": 5,
    },
    "Qwen 3 0.6B (Local)": {
        "provider": "ollama",
        "model": "qwen3:0.6b",
        "tier": "local",
        "capabilities": ["general"],
        "cost": "free",
        "description": "Alibaba Qwen3 0.6B via Ollama. Ultra-small multilingual model.",
        "vram_gb": 1,
    },
    "Qwen 3 1.7B (Local)": {
        "provider": "ollama",
        "model": "qwen3:1.7b",
        "tier": "local",
        "capabilities": ["general", "code"],
        "cost": "free",
        "description": "Alibaba Qwen3 1.7B via Ollama. Tiny local reasoning-capable model.",
        "vram_gb": 2,
    },
    "Qwen 3 4B (Local)": {
        "provider": "ollama",
        "model": "qwen3:4b",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Alibaba Qwen3 4B via Ollama. Small multilingual model with strong reasoning.",
        "vram_gb": 4,
    },
    "Qwen 3 8B (Local)": {
        "provider": "ollama",
        "model": "qwen3:8b",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Alibaba Qwen3 8B via Ollama. Balanced local multilingual and coding model.",
        "vram_gb": 6,
    },
    "Qwen 3 14B (Local)": {
        "provider": "ollama",
        "model": "qwen3:14b",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Alibaba Qwen3 14B via Ollama. Stronger local reasoning and coding model.",
        "vram_gb": 10,
    },
    "Qwen 3 30B A3B (Local)": {
        "provider": "ollama",
        "model": "qwen3:30b",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Alibaba Qwen3 30B A3B MoE via Ollama. High-capability local reasoning model.",
        "vram_gb": 20,
    },
    "Qwen 3 32B (Local)": {
        "provider": "ollama",
        "model": "qwen3:32b",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Alibaba Qwen3 32B via Ollama. Large local multilingual reasoning model.",
        "vram_gb": 24,
    },
    # Command R
    "Command R (Local)": {
        "provider": "ollama",
        "model": "command-r",
        "tier": "local",
        "capabilities": ["general", "write"],
        "cost": "free",
        "description": "Cohere Command R via Ollama. Strong at RAG and retrieval-augmented tasks.",
        "vram_gb": 8,
    },
    # Phi family
    "Phi-4 (Local)": {
        "provider": "ollama",
        "model": "phi4",
        "tier": "local",
        "capabilities": ["general", "code"],
        "cost": "free",
        "description": "Microsoft Phi-4 via Ollama. Punches above its weight for its size.",
        "vram_gb": 8,
    },
    "Phi-3.5 Mini (Local)": {
        "provider": "ollama",
        "model": "phi3.5",
        "tier": "local",
        "capabilities": ["general", "code"],
        "cost": "free",
        "description": "Microsoft Phi-3.5 Mini via Ollama. Ultra-fast on CPU. Good for constrained hardware.",
        "vram_gb": 2,
    },
    # StarCoder2 — code only
    "StarCoder2 7B (Local)": {
        "provider": "ollama",
        "model": "starcoder2:7b",
        "tier": "local",
        "capabilities": ["code"],
        "cost": "free",
        "description": "BigCode StarCoder2 7B via Ollama. Trained on 600+ programming languages.",
        "vram_gb": 5,
    },
    # Mistral family
    "Mistral Small 3.1 24B (Local)": {
        "provider": "ollama",
        "model": "mistral-small3.1:24b",
        "tier": "local",
        "capabilities": ["general", "code", "write", "vision"],
        "cost": "free",
        "description": "Mistral Small 3.1 24B via Ollama. Apache-2.0 model with 128K context and vision.",
        "vram_gb": 16,
    },
    "Devstral 24B (Local)": {
        "provider": "ollama",
        "model": "devstral:24b",
        "tier": "local",
        "capabilities": ["code", "general"],
        "cost": "free",
        "description": "Mistral/All Hands Devstral 24B via Ollama. Agentic coding model.",
        "vram_gb": 16,
    },
    "Magistral 24B (Local)": {
        "provider": "ollama",
        "model": "magistral:24b",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Mistral Magistral 24B via Ollama. Reasoning model for multi-step tasks.",
        "vram_gb": 16,
    },
    "Mistral Nemo (Local)": {
        "provider": "ollama",
        "model": "mistral-nemo",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Mistral Nemo 12B via Ollama. Fast and efficient, built with Nvidia.",
        "vram_gb": 8,
    },
    "Mixtral 8x7B (Local)": {
        "provider": "ollama",
        "model": "mixtral:8x7b",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Mistral Mixtral 8x7B MoE via Ollama. High quality, needs ~32 GB RAM/VRAM.",
        "vram_gb": 32,
    },
    # Open-weight GPT-OSS family via Ollama
    "GPT-OSS 20B (Local)": {
        "provider": "ollama",
        "model": "gpt-oss:20b",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "OpenAI GPT-OSS 20B open-weight model via Ollama. Local reasoning and agentic tasks.",
        "vram_gb": 16,
    },
    "GPT-OSS 120B (Local)": {
        "provider": "ollama",
        "model": "gpt-oss:120b",
        "tier": "local",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "OpenAI GPT-OSS 120B open-weight model via Ollama. Large local reasoning model.",
        "vram_gb": 80,
    },
    # TinyLlama — ultra-lightweight for CPU-only machines
    "TinyLlama (Local)": {
        "provider": "ollama",
        "model": "tinyllama",
        "tier": "local",
        "capabilities": ["general"],
        "cost": "free",
        "description": "TinyLlama 1.1B via Ollama. Runs on any machine including CPU-only. Very fast.",
        "vram_gb": 1,
    },
    # Orca Mini
    "Orca Mini (Local)": {
        "provider": "ollama",
        "model": "orca-mini",
        "tier": "local",
        "capabilities": ["general"],
        "cost": "free",
        "description": "Orca Mini 3B via Ollama. Lightweight reasoning model.",
        "vram_gb": 2,
    },

    # ── Free Cloud / Groq ─────────────────────────────────────────────────
    "Llama 3.3 70B (Groq)": {
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Meta Llama 3.3 70B on Groq. Extremely fast free inference.",
        "env_key": "GROQ_API_KEY",
    },
    "Llama 3.1 8B Instant (Groq)": {
        "provider": "groq",
        "model": "llama-3.1-8b-instant",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Meta Llama 3.1 8B Instant on Groq. Fast low-latency model with large context.",
        "env_key": "GROQ_API_KEY",
    },
    "Mixtral 8x7B (Groq)": {
        "provider": "groq",
        "model": "mixtral-8x7b-32768",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Mixtral 8x7B on Groq free tier.",
        "env_key": "GROQ_API_KEY",
    },
    "Gemma 2 9B (Groq)": {
        "provider": "groq",
        "model": "gemma2-9b-it",
        "tier": "free_cloud",
        "capabilities": ["general", "write"],
        "cost": "free",
        "description": "Google Gemma 2 9B on Groq free tier.",
        "env_key": "GROQ_API_KEY",
    },
    "GPT-OSS 20B (Groq)": {
        "provider": "groq",
        "model": "openai/gpt-oss-20b",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "OpenAI GPT-OSS 20B on Groq. Fast open-weight reasoning model.",
        "env_key": "GROQ_API_KEY",
    },
    "GPT-OSS 120B (Groq)": {
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "OpenAI GPT-OSS 120B on Groq. Large open-weight reasoning model.",
        "env_key": "GROQ_API_KEY",
    },
    "Llama 4 Scout (Groq)": {
        "provider": "groq",
        "model": "meta-llama/llama-4-scout-17b-16e-instruct",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write", "vision"],
        "cost": "free",
        "description": "Meta Llama 4 Scout preview on Groq. Multimodal long-context model.",
        "env_key": "GROQ_API_KEY",
    },
    "Qwen 3 32B (Groq)": {
        "provider": "groq",
        "model": "qwen/qwen3-32b",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Alibaba Qwen3 32B preview on Groq. Strong multilingual reasoning and coding.",
        "env_key": "GROQ_API_KEY",
    },
    "Groq Compound": {
        "provider": "groq",
        "model": "groq/compound",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Groq Compound system. Uses Groq-hosted models and tools for agentic answers.",
        "env_key": "GROQ_API_KEY",
    },
    "Groq Compound Mini": {
        "provider": "groq",
        "model": "groq/compound-mini",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Groq Compound Mini system. Lower-latency Groq agentic system.",
        "env_key": "GROQ_API_KEY",
    },

    # ── Free Cloud / OpenRouter free tier ─────────────────────────────────
    "OpenRouter Free Router": {
        "provider": "openrouter",
        "model": "openrouter/free",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write", "vision"],
        "cost": "free",
        "description": "OpenRouter zero-cost router. Automatically selects an available free model.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Owl Alpha (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "openrouter/owl-alpha",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "OpenRouter Owl Alpha. Free long-context agentic model.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Llama 3.1 8B (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "meta-llama/llama-3.1-8b-instruct:free",
        "tier": "free_cloud",
        "capabilities": ["general", "write"],
        "cost": "free",
        "description": "Meta Llama 3.1 8B via OpenRouter free tier.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Llama 3.2 3B (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "meta-llama/llama-3.2-3b-instruct:free",
        "tier": "free_cloud",
        "capabilities": ["general", "write"],
        "cost": "free",
        "description": "Meta Llama 3.2 3B via OpenRouter free tier.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "DeepSeek R1 (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "deepseek/deepseek-r1:free",
        "tier": "free_cloud",
        "capabilities": ["general", "code"],
        "cost": "free",
        "description": "DeepSeek R1 reasoning model via OpenRouter free tier.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Phi-3 Mini (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "microsoft/phi-3-mini-128k-instruct:free",
        "tier": "free_cloud",
        "capabilities": ["general", "code"],
        "cost": "free",
        "description": "Microsoft Phi-3 Mini 128K via OpenRouter free tier.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Gemma 3 12B (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "google/gemma-3-12b-it:free",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Google Gemma 3 12B via OpenRouter free tier.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Gemma 3 27B (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "google/gemma-3-27b-it:free",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Google Gemma 3 27B via OpenRouter free tier. Near-GPT-4 quality at zero cost.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Gemma 4 (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "google/gemma-4-31b-it:free",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Google Gemma 4 31B via OpenRouter free tier. Multimodal open model, no cost.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Gemma 4 26B A4B (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "google/gemma-4-26b-a4b-it:free",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write", "vision"],
        "cost": "free",
        "description": "Google Gemma 4 26B A4B via OpenRouter free tier. Efficient multimodal MoE model.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Qwen 2.5 72B (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "qwen/qwen-2.5-72b-instruct:free",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Alibaba Qwen 2.5 72B via OpenRouter free tier. Excellent multilingual.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Mistral 7B (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "mistralai/mistral-7b-instruct:free",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Mistral 7B via OpenRouter free tier.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Llama 3.2 11B Vision (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "meta-llama/llama-3.2-11b-vision-instruct:free",
        "tier": "free_cloud",
        "capabilities": ["general", "vision"],
        "cost": "free",
        "description": "Meta Llama 3.2 11B Vision via OpenRouter free tier. Multimodal.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "GPT-OSS 20B (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "openai/gpt-oss-20b:free",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "OpenAI GPT-OSS 20B via OpenRouter free tier.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "GPT-OSS 120B (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "openai/gpt-oss-120b:free",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "OpenAI GPT-OSS 120B via OpenRouter free tier.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Nemotron 3 Super 120B A12B (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "NVIDIA Nemotron 3 Super 120B A12B via OpenRouter free tier.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Nemotron 3 Nano 30B A3B (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "nvidia/nemotron-3-nano-30b-a3b:free",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "NVIDIA Nemotron 3 Nano 30B A3B via OpenRouter free tier.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Nemotron 3 Nano Omni (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "tier": "free_cloud",
        "capabilities": ["general", "vision", "write"],
        "cost": "free",
        "description": "NVIDIA Nemotron 3 Nano Omni via OpenRouter free tier. Multimodal reasoning.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Laguna M.1 (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "poolside/laguna-m.1:free",
        "tier": "free_cloud",
        "capabilities": ["code", "general"],
        "cost": "free",
        "description": "Poolside Laguna M.1 via OpenRouter free tier. Coding-agent model.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Laguna XS.2 (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "poolside/laguna-xs.2:free",
        "tier": "free_cloud",
        "capabilities": ["code", "general"],
        "cost": "free",
        "description": "Poolside Laguna XS.2 via OpenRouter free tier. Efficient coding-agent model.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Ring 2.6 1T (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "inclusionai/ring-2.6-1t:free",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "inclusionAI Ring-2.6-1T via OpenRouter free tier. Large reasoning model.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "GLM 4.5 Air (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "z-ai/glm-4.5-air:free",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Z.ai GLM-4.5-Air via OpenRouter free tier. Agent-focused reasoning model.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "MiniMax M2.5 (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "minimax/minimax-m2.5:free",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "MiniMax M2.5 via OpenRouter free tier. Productivity and coding-agent model.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Solar Pro 3 (OpenRouter Free)": {
        "provider": "openrouter",
        "model": "upstage/solar-pro-3:free",
        "tier": "free_cloud",
        "capabilities": ["general", "write"],
        "cost": "free",
        "description": "Upstage Solar Pro 3 via OpenRouter free tier.",
        "env_key": "OPENROUTER_API_KEY",
    },
    "Sarvam-M (Free API)": {
        "provider": "sarvam",
        "model": "sarvam-m",
        "tier": "free_cloud",
        "capabilities": ["general", "code", "write"],
        "cost": "free",
        "description": "Sarvam-M legacy 24B chat model via Sarvam API. Free chat completion model with Indic language strengths.",
        "env_key": "SARVAM_API_KEY",
    },

    # ── Paid Cloud ────────────────────────────────────────────────────────
    "ChatGPT (GPT-4o)": {
        "provider": "openai",
        "model": "gpt-4o",
        "tier": "paid",
        "capabilities": ["general", "code", "write"],
        "cost": "paid",
        "description": "OpenAI GPT-4o. Best for precise coding and structured output.",
        "env_key": "OPENAI_API_KEY",
    },
    "GPT-4o Mini": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "tier": "paid",
        "capabilities": ["general", "code", "write"],
        "cost": "paid",
        "description": "OpenAI GPT-4o Mini. Cheaper paid option.",
        "env_key": "OPENAI_API_KEY",
    },
    "Claude 3.5 Sonnet": {
        "provider": "anthropic",
        "model": "claude-3-5-sonnet-20241022",
        "tier": "paid",
        "capabilities": ["general", "write"],
        "cost": "paid",
        "description": "Anthropic Claude 3.5 Sonnet. Best for nuanced writing and analysis.",
        "env_key": "ANTHROPIC_API_KEY",
    },
    "Gemini 1.5 Pro": {
        "provider": "google",
        "model": "gemini-1.5-pro",
        "tier": "paid",
        "capabilities": ["general", "code", "write"],
        "cost": "paid",
        "description": "Google Gemini 1.5 Pro. Massive 1M-token context window.",
        "env_key": "GOOGLE_API_KEY",
    },
    "Gemini 2.0 Flash": {
        "provider": "google",
        "model": "gemini-2.0-flash",
        "tier": "paid",
        "capabilities": ["general", "code", "write"],
        "cost": "paid",
        "description": "Google Gemini 2.0 Flash. Fast and cheap, multimodal.",
        "env_key": "GOOGLE_API_KEY",
    },
    "Gemini 2.5 Pro": {
        "provider": "google",
        "model": "gemini-2.5-pro-preview-05-06",
        "tier": "paid",
        "capabilities": ["general", "code", "write"],
        "cost": "paid",
        "description": "Google Gemini 2.5 Pro. Latest flagship with 1M-token context and deep reasoning.",
        "env_key": "GOOGLE_API_KEY",
    },
    "Gemma 4 (Google AI)": {
        "provider": "google",
        "model": "gemma-4-it",
        "tier": "paid",
        "capabilities": ["general", "code", "write"],
        "cost": "paid",
        "description": "Google Gemma 4 via Google AI API. Latest Gemma series — affordable paid option.",
        "env_key": "GOOGLE_API_KEY",
    },
    # DeepSeek cloud API — extremely cheap pay-per-token
    "DeepSeek V3 (API)": {
        "provider": "deepseek_api",
        "model": "deepseek-chat",
        "tier": "paid",
        "capabilities": ["general", "code", "write"],
        "cost": "paid",
        "description": "DeepSeek V3 via DeepSeek API. State-of-the-art at very low cost (~$0.27/1M tokens).",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "DeepSeek R1 (API)": {
        "provider": "deepseek_api",
        "model": "deepseek-reasoner",
        "tier": "paid",
        "capabilities": ["general", "code"],
        "cost": "paid",
        "description": "DeepSeek R1 reasoning model via DeepSeek API. o1-level reasoning at minimal cost.",
        "env_key": "DEEPSEEK_API_KEY",
    },
    # Cohere
    "Command R+ (Cohere)": {
        "provider": "cohere",
        "model": "command-r-plus-08-2024",
        "tier": "paid",
        "capabilities": ["general", "write"],
        "cost": "paid",
        "description": "Cohere Command R+ via Cohere API. Best for RAG and enterprise knowledge tasks.",
        "env_key": "COHERE_API_KEY",
    },
    "Sarvam 30B (API)": {
        "provider": "sarvam",
        "model": "sarvam-30b",
        "tier": "paid",
        "capabilities": ["general", "code", "write"],
        "cost": "paid",
        "description": "Sarvam 30B via Sarvam API. Strong Indic language, reasoning, coding, and 64K-context chat model.",
        "env_key": "SARVAM_API_KEY",
    },
    "Sarvam 105B (API)": {
        "provider": "sarvam",
        "model": "sarvam-105b",
        "tier": "paid",
        "capabilities": ["general", "code", "write"],
        "cost": "paid",
        "description": "Sarvam 105B via Sarvam API. Flagship long-context model for complex reasoning, coding, and generation.",
        "env_key": "SARVAM_API_KEY",
    },
}


# ── Custom Model Registry (persisted to prismara/custom_models.json) ──────────
# Schema for each custom model entry:
# {
#   "id":          "my-azure-gpt4",           # unique slug (auto-generated if blank)
#   "label":       "Azure GPT-4o (Corp)",      # display name shown in UI
#   "base_url":    "https://my.openai.azure.com/openai/deployments/gpt4o",
#   "model":       "gpt-4o",                   # model/deployment name sent in request
#   "tier":        "paid",                     # local | free_cloud | paid | custom
#   "capabilities":["general","code","write"],
#   "auth_type":   "api_key",                  # api_key | sso_client_creds | sso_device_code | none
#   "api_key":     "",                         # plain API key (encrypted at rest in file)
#   "sso_profile_id": "",                     # links to an SSO profile in credentials.json
#   "extra_headers": {},                       # any extra HTTP headers needed
#   "notes":       "Internal corporate gateway — requires VPN"
# }


def _custom_models_path() -> "Path":
    # MLMAE_DATA_DIR is set by launcher.py to the prismara folder next to the exe.
    # In dev mode fall back to a local prismara folder at project root.
    repo_root = Path(__file__).resolve().parent.parent
    data_dir = os.environ.get("MLMAE_DATA_DIR", str(repo_root / "prismara"))
    p = Path(data_dir)
    if not p.is_absolute():
        p = repo_root / p
    p.mkdir(parents=True, exist_ok=True)
    return p / "custom_models.json"


def load_custom_models() -> list[dict]:
    """Load all user-defined custom models from disk."""
    path = _custom_models_path()
    if not path.exists():
        return []
    try:
        return load_json(path, default=[]) or []
    except Exception as e:
        logger.warning("Could not load custom_models.json: %s", e)
        return []


def save_custom_models(models: list[dict]):
    """Persist custom models list to disk."""
    path = _custom_models_path()
    save_json(path, models, encode_at_rest=True)


def add_custom_model(entry: dict) -> dict:
    """
    Add or update a custom model. If entry has no 'id', one is auto-generated.
    Returns the saved entry (with id filled in).
    """
    import uuid as _uuid
    models = load_custom_models()
    if not entry.get("id"):
        entry["id"] = _uuid.uuid4().hex[:8]
    # Replace existing with same id
    models = [m for m in models if m["id"] != entry["id"]]
    models.append(entry)
    save_custom_models(models)
    logger.info("Custom model saved: %s", entry["id"])
    return entry


def delete_custom_model(model_id: str):
    """Remove a custom model by its id."""
    models = [m for m in load_custom_models() if m["id"] != model_id]
    save_custom_models(models)
    logger.info("Custom model deleted: %s", model_id)


def _get_combined_registry() -> dict[str, dict]:
    """
    Return AGENT_REGISTRY merged with all custom models.
    Custom model label becomes the agent name.
    """
    combined = dict(AGENT_REGISTRY)
    for cm in load_custom_models():
        name = cm.get("label") or cm["id"]
        combined[name] = {
            "provider": "custom_openai_compat",
            "model": cm.get("model", ""),
            "tier": cm.get("tier", "custom"),
            "capabilities": cm.get("capabilities", ["general"]),
            "cost": cm.get("cost", "custom"),
            "description": cm.get("notes", "User-defined custom model"),
            "_custom": cm,   # full config attached
        }
    return combined


def _is_ollama_running() -> bool:
    """Check if Ollama server is up on localhost:11434."""
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        return True
    except Exception:
        return False


def _get_ollama_models() -> list[str]:
    """Return list of model names currently pulled in Ollama."""
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as r:
            data = json.loads(r.read())
            names: set[str] = set()
            for m in data.get("models", []):
                name = str(m.get("name", "")).strip()
                if not name:
                    continue
                names.add(name)
                if name.endswith(":latest"):
                    names.add(name.split(":", 1)[0])
            return sorted(names)
    except Exception:
        return []


def _check_custom_model_available(cm: dict) -> bool:
    """Quick availability check for a custom model entry."""
    auth_type = cm.get("auth_type", "api_key")

    if auth_type == "none":
        return True  # unauthenticated local server — assume available

    if auth_type == "api_key":
        return bool(cm.get("api_key", "").strip())

    if auth_type in ("sso_client_creds", "sso_device_code"):
        sso_profile_id = cm.get("sso_profile_id", "")
        if not sso_profile_id:
            return False
        try:
            from src.sso_auth import get_valid_token  # type: ignore
            return get_valid_token(sso_profile_id) is not None
        except Exception:
            return False

    return False


def _is_sso_profile_configured(env_key: str) -> bool:
    profile_id = os.environ.get(env_key, "").strip()
    if not profile_id:
        return False
    try:
        from src.sso_auth import _load_sso_profile, get_sso_status  # type: ignore
        profile = _load_sso_profile(profile_id)
        if not profile:
            return False
        if profile.get("flow") == "client_credentials":
            return True
        return bool(get_sso_status().get(profile_id, {}).get("authenticated"))
    except Exception:
        return False


def detect_available_agents() -> dict[str, dict]:
    """
    Return the full combined registry (built-in + custom) with an 'available'
    flag on each entry based on whether credentials / Ollama models exist.
    """
    load_saved_credentials_into_env()
    available: dict[str, dict] = {}
    registry = _get_combined_registry()

    ollama_running = _is_ollama_running()
    ollama_models = _get_ollama_models() if ollama_running else []

    for name, cfg in registry.items():
        provider = cfg["provider"]
        entry = {**cfg, "name": name, "available": False}
        # Remove internal _custom key from public output
        entry.pop("_custom", None)

        if provider == "ollama":
            model = str(cfg["model"]).strip()
            model_base = model.split(":", 1)[0]
            if model in _known_broken_ollama_models():
                entry["available"] = False
                entry["unavailable_reason"] = "known_broken_model"
            elif ollama_running:
                if ":" in model:
                    entry["available"] = model in ollama_models
                else:
                    entry["available"] = model in ollama_models or f"{model_base}:latest" in ollama_models

        elif provider in ("groq", "openrouter", "openai", "anthropic", "google",
                          "deepseek_api", "cohere", "sarvam"):
            env_key = cfg.get("env_key", "")
            if env_key and os.environ.get(env_key, "").strip():
                entry["available"] = True
            elif provider == "anthropic" and _is_sso_profile_configured("ANTHROPIC_SSO_PROFILE_ID"):
                entry["available"] = True
            elif provider == "google" and _is_sso_profile_configured("GOOGLE_SSO_PROFILE_ID"):
                entry["available"] = True

        elif provider == "custom_openai_compat":
            cm = cfg.get("_custom", {})
            entry["available"] = _check_custom_model_available(cm)

        available[name] = entry

    return available


# ── Unified LLM Call ──────────────────────────────────────────────────────────

def call_llm(agent_name: str, prompt: str, system: str = "") -> str:
    """
    Call the LLM for a given agent_name with the provided prompt.
    Returns the response text, or raises RuntimeError on failure.
    """
    load_saved_credentials_into_env()
    cfg = AGENT_REGISTRY.get(agent_name)
    if not cfg:
        # Check custom models by label
        for cm in load_custom_models():
            if cm.get("label") == agent_name or cm.get("id") == agent_name:
                cfg = {
                    "provider": "custom_openai_compat",
                    "model": cm.get("model", ""),
                    "_custom": cm,
                }
                break
    if not cfg:
        raise ValueError(f"Unknown agent: {agent_name}")

    provider = cfg["provider"]
    model = cfg["model"]
    logger.info("Calling %s via %s (model=%s)", agent_name, provider, model)

    # ── Data Guard: anonymise prompt before sending to external providers ──
    guarded_prompt, guarded_system, guard_session = prompt, system, None
    try:
        from src.data_guard import guard_prompt, guard_response  # type: ignore
        guarded_prompt, guarded_system, guard_session = guard_prompt(prompt, system, provider)
        if guard_session:
            logger.info("DataGuard active for provider '%s' (session %s)", provider, guard_session[:8])
    except Exception as _dg_err:
        logger.warning("DataGuard import failed, sending raw prompt: %s", _dg_err)

    def _restore(raw: str) -> str:
        if not guard_session:
            return raw
        try:
            from src.data_guard import guard_response as _gr  # type: ignore
            return _gr(raw, guard_session)
        except Exception:
            return raw

    if provider == "ollama":
        return _restore(_call_ollama(model, guarded_prompt, guarded_system))
    elif provider == "groq":
        return _restore(_call_groq(model, guarded_prompt, guarded_system))
    elif provider == "openrouter":
        return _restore(_call_openrouter(model, guarded_prompt, guarded_system))
    elif provider == "sarvam":
        return _restore(_call_sarvam(model, guarded_prompt, guarded_system))
    elif provider == "openai":
        return _restore(_call_openai(model, guarded_prompt, guarded_system))
    elif provider == "anthropic":
        return _restore(_call_anthropic(model, guarded_prompt, guarded_system))
    elif provider == "google":
        return _restore(_call_google(model, guarded_prompt, guarded_system))
    elif provider == "deepseek_api":
        return _restore(_call_deepseek_api(model, guarded_prompt, guarded_system))
    elif provider == "cohere":
        return _restore(_call_cohere(model, guarded_prompt, guarded_system))
    elif provider == "custom_openai_compat":
        return _restore(_call_custom(cfg["_custom"], guarded_prompt, guarded_system))
    else:
        raise ValueError(f"Unknown provider: {provider}")


# ── Provider Implementations ──────────────────────────────────────────────────

def _ollama_hardware_options() -> dict:
    """Force CPU unless the local hardware policy explicitly allows GPU use."""
    try:
        from src.local_ai import ollama_runtime_options  # type: ignore
        options = ollama_runtime_options()
        return options if isinstance(options, dict) else {"num_gpu": 0}
    except Exception as e:
        logger.warning("Could not read Ollama hardware policy; forcing CPU: %s", e)
        return {"num_gpu": 0}


def _call_ollama(model: str, prompt: str, system: str) -> str:
    import urllib.request, json
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # Short keep_alive frees RAM between stages — SSD reload is ~1-2 s,
    # better than risking OOM-kill of the llama runner on low-RAM machines.
    # Env knob: MLMAE_OLLAMA_KEEP_ALIVE (default "30s"; "0s" unloads immediately).
    keep_alive = os.environ.get("MLMAE_OLLAMA_KEEP_ALIVE", "30s")
    body = {"model": model, "messages": messages, "stream": False, "keep_alive": keep_alive}
    hardware_options = _ollama_hardware_options()
    if hardware_options:
        body["options"] = hardware_options

    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/") + "/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        timeout = float(os.environ.get("MLMAE_OLLAMA_HTTP_TIMEOUT", "300"))
    except ValueError:
        timeout = 300.0
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return data["message"]["content"].strip()


def _call_groq(model: str, prompt: str, system: str) -> str:
    try:
        from groq import Groq
    except ImportError:
        raise RuntimeError("groq package not installed. Run: pip install groq")

    client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(model=model, messages=messages, max_tokens=4096)
    return response.choices[0].message.content.strip()


def _call_openrouter(model: str, prompt: str, system: str) -> str:
    import urllib.request, json
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = json.dumps({"model": model, "messages": messages}).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": "Prismara AI",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"].strip()


def _call_sarvam(model: str, prompt: str, system: str) -> str:
    api_key = os.environ.get("SARVAM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("SARVAM_API_KEY is not set.")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": 4096,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        "https://api.sarvam.ai/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "api-subscription-key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read())

    content = data["choices"][0]["message"].get("content", "")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()
    return str(content).strip()


def _call_openai(model: str, prompt: str, system: str) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed. Run: pip install openai")

    client = OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        organization=os.environ.get("OPENAI_ORG_ID", "") or None,
        project=os.environ.get("OPENAI_PROJECT_ID", "") or None,
    )
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(model=model, messages=messages, max_tokens=4096)
    return response.choices[0].message.content.strip()


def _get_sso_token_from_env(env_key: str, allow_client_credentials: bool = True) -> Optional[str]:
    profile_id = os.environ.get(env_key, "").strip()
    if not profile_id:
        return None
    try:
        from src.sso_auth import get_valid_token, authenticate_client_credentials, _load_sso_profile  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"{env_key} is set, but SSO support is unavailable: {exc}") from exc

    token = get_valid_token(profile_id)
    if token:
        return token

    if allow_client_credentials:
        profile = _load_sso_profile(profile_id)
        if profile and profile.get("flow") == "client_credentials":
            return authenticate_client_credentials(profile)

    return None


def _call_anthropic(model: str, prompt: str, system: str) -> str:
    sso_token = _get_sso_token_from_env("ANTHROPIC_SSO_PROFILE_ID")
    if sso_token:
        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {sso_token}",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        parts = data.get("content", [])
        return "\n".join(
            str(part.get("text", "")) for part in parts
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()

    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic package not installed. Run: pip install anthropic")

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    kwargs: dict = {"model": model, "max_tokens": 4096, "messages": [{"role": "user", "content": prompt}]}
    if system:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)
    return response.content[0].text.strip()


def _call_google(model: str, prompt: str, system: str) -> str:
    sso_token = _get_sso_token_from_env("GOOGLE_SSO_PROFILE_ID")
    if sso_token:
        return _call_google_with_bearer(model, prompt, system, sso_token)

    try:
        from google import genai  # google-genai (current SDK; replaces google-generativeai)
    except ImportError as e:
        raise RuntimeError(
            "google-genai package not installed. Run: pip install google-genai"
        ) from e

    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", ""))
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    response = client.models.generate_content(model=model, contents=full_prompt)
    return (response.text or "").strip()


def _call_google_with_bearer(model: str, prompt: str, system: str, token: str) -> str:
    model_path = model if model.startswith("models/") else f"models/{model}"
    encoded_model = urllib.parse.quote(model_path, safe="/")
    url = f"https://generativelanguage.googleapis.com/v1beta/{encoded_model}:generateContent"

    payload: dict = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {"maxOutputTokens": 4096},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT_ID", "").strip()
    if project_id:
        headers["x-goog-user-project"] = project_id

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())

    candidate = (data.get("candidates") or [{}])[0]
    parts = candidate.get("content", {}).get("parts", [])
    return "\n".join(
        str(part.get("text", "")) for part in parts
        if isinstance(part, dict) and part.get("text")
    ).strip()


def _call_deepseek_api(model: str, prompt: str, system: str) -> str:
    """DeepSeek API is OpenAI-compatible — just swap the base URL."""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed. Run: pip install openai")

    client = OpenAI(
        api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        base_url="https://api.deepseek.com/v1",
    )
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    response = client.chat.completions.create(model=model, messages=messages, max_tokens=4096)
    return response.choices[0].message.content.strip()


def _call_cohere(model: str, prompt: str, system: str) -> str:
    try:
        import cohere
    except ImportError:
        raise RuntimeError("cohere package not installed. Run: pip install cohere")

    client = cohere.ClientV2(api_key=os.environ.get("COHERE_API_KEY", ""))
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    response = client.chat(model=model, messages=messages, max_tokens=4096)
    return response.message.content[0].text.strip()


def _call_custom(cm: dict, prompt: str, system: str) -> str:
    """
    Call any OpenAI-compatible endpoint defined in a custom model entry.
    Supports static API key or SSO bearer token.
    Works with: Azure OpenAI, LM Studio, Llamafile, Together AI, Fireworks,
    Perplexity, Anyscale, corporate LLM gateways, any OpenAI-compat server.
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed. Run: pip install openai")

    base_url = cm.get("base_url", "").rstrip("/")
    if not base_url:
        raise ValueError("Custom model entry missing 'base_url'")

    auth_type = cm.get("auth_type", "api_key")

    if auth_type == "api_key":
        api_key = cm.get("api_key") or "none"   # some local servers ignore the key
    elif auth_type in ("sso_client_creds", "sso_device_code"):
        sso_profile_id = cm.get("sso_profile_id", "")
        if not sso_profile_id:
            raise ValueError("SSO auth_type set but no sso_profile_id specified")
        try:
            from src.sso_auth import get_valid_token, authenticate_client_credentials, _load_sso_profile  # type: ignore
        except ImportError:
            raise RuntimeError("src.sso_auth module not available")

        token = get_valid_token(sso_profile_id)
        if not token and auth_type == "sso_client_creds":
            profile = _load_sso_profile(sso_profile_id)
            if profile:
                token = authenticate_client_credentials(profile)
        if not token:
            raise RuntimeError(
                f"No valid SSO token for profile '{sso_profile_id}'. "
                "Please authenticate via Settings → SSO Profiles."
            )
        api_key = token   # OpenAI client sends this as Bearer token
    elif auth_type == "none":
        api_key = "none"
    else:
        raise ValueError(f"Unknown auth_type: {auth_type}")

    # Build extra headers (e.g. Azure requires api-version query param via header)
    extra_headers = cm.get("extra_headers", {})

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers=extra_headers if extra_headers else None,
    )

    model = cm.get("model", "")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=cm.get("max_tokens", 4096),
    )
    return response.choices[0].message.content.strip()


# ── Smart Agent Picker ────────────────────────────────────────────────────────

def pick_best_agent(capability: str, prefer_free: bool = True) -> Optional[str]:
    """
    Return the best available agent name for a given capability.
    Preference order when prefer_free=True: local → free_cloud → paid.
    Preference order when prefer_free=False: paid → free_cloud → local.
    """
    available = detect_available_agents()
    tier_order = (
        ["local", "free_cloud", "paid"] if prefer_free
        else ["paid", "free_cloud", "local"]
    )

    for tier in tier_order:
        candidates = [
            name for name, cfg in available.items()
            if cfg["available"] and cfg["tier"] == tier and capability in cfg["capabilities"]
        ]
        if candidates:
            return candidates[0]

    return None
