"""
Prismara AI Data Guard - End-to-End Code Anonymisation
==================================================
Protects intellectual property and sensitive data when prompts containing
code or file contents are sent to external LLM APIs (especially high-risk
providers like DeepSeek that are subject to foreign data laws).

How it works
------------
Before sending to an external model:
  1. Scan the prompt for identifiers, string literals, comments, file paths,
     credentials, and IP/email/URL patterns.
  2. Replace each unique token with a neutral placeholder (VAR_001, FUNC_001,
     STR_001, PATH_001, SECRET_001, …).
  3. Store the forward mapping (real → placeholder) in a short-lived session dict.
  4. Send the anonymised prompt to the LLM.
  5. After the response arrives, reverse-map every placeholder back to the
     original token so the caller receives readable output.

Provider risk levels (used by the auto-guard mode)
---------------------------------------------------
  NONE   — Ollama, local servers: data never leaves the machine.
  LOW    — OpenAI, Anthropic, Google: enterprise privacy / DPA commitments.
  MEDIUM — Groq, OpenRouter, Cohere, Sarvam: legitimate but third-party/cloud APIs.
  HIGH   — DeepSeek API, unknown custom endpoints: guard always on in AUTO mode.

Guard modes (stored in prismara/config.json under "data_guard_mode"):
  "off"    — No anonymisation, send raw prompts everywhere.
  "auto"   — Anonymise only HIGH-risk providers automatically.
  "always" — Anonymise every external provider (MEDIUM + HIGH).
  "strict" — Anonymise everything including LOW-risk cloud providers.
             (NONE / Ollama is never anonymised — pointless for local.)

Toggle via:
  • Frontend Settings → Privacy & Security → Data Guard toggle
  • POST /api/settings/data-guard  {"mode": "auto"}
  • Environment variable  MLMAE_DATA_GUARD_MODE=auto
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Optional

from src.secure_storage import load_json, save_json

# ── Provider risk classification ──────────────────────────────────────────────

PROVIDER_RISK: dict[str, str] = {
    "ollama":               "none",    # local — data stays on machine
    "openai":               "low",
    "anthropic":            "low",
    "google":               "low",
    "groq":                 "medium",
    "openrouter":           "medium",
    "cohere":               "medium",
    "sarvam":               "medium",
    "deepseek_api":         "high",    # subject to PRC data laws
    "custom_openai_compat": "high",    # unknown endpoint — treat as high by default
}

RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}

# Guard mode → minimum risk level that triggers anonymisation
_MODE_THRESHOLD: dict[str, str] = {
    "off":    "___never___",   # special sentinel — never match
    "auto":   "high",
    "always": "medium",
    "strict": "low",
}


# ── Config / mode loading ─────────────────────────────────────────────────────

def _config_path() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    data_dir = Path(os.environ.get("MLMAE_DATA_DIR", str(repo_root / "prismara")))
    if not data_dir.is_absolute():
        data_dir = repo_root / data_dir
    return data_dir / "config.json"


def get_guard_mode() -> str:
    """Return the current data guard mode (off/auto/always/strict)."""
    # Env var takes priority (useful for CI / testing)
    env = os.environ.get("MLMAE_DATA_GUARD_MODE", "").strip().lower()
    if env in _MODE_THRESHOLD:
        return env
    try:
        cfg = load_json(_config_path(), default={}) or {}
        mode = cfg.get("data_guard_mode", "auto").lower()
        return mode if mode in _MODE_THRESHOLD else "auto"
    except Exception:
        return "auto"


def set_guard_mode(mode: str):
    """Persist the guard mode to config.json."""
    if mode not in _MODE_THRESHOLD:
        raise ValueError(f"Invalid guard mode '{mode}'. Choose: off, auto, always, strict")
    path = _config_path()
    cfg = load_json(path, default={}) if path.exists() else {}
    cfg["data_guard_mode"] = mode
    save_json(path, cfg, encode_at_rest=True)


def should_guard(provider: str) -> bool:
    """Return True if the current mode + provider requires anonymisation."""
    mode = get_guard_mode()
    threshold = _MODE_THRESHOLD.get(mode, "___never___")
    if threshold == "___never___":
        return False
    risk = PROVIDER_RISK.get(provider, "high")   # unknown providers → high
    return RISK_ORDER.get(risk, 3) >= RISK_ORDER.get(threshold, 3)


# ── Session-scoped token maps ─────────────────────────────────────────────────
# Each anonymisation call gets a unique session_id (UUID). The mapping lives
# only in memory and is discarded when the response is de-anonymised.
# This means no mapping file is ever written to disk.

_sessions: dict[str, "_TokenMap"] = {}
_sessions_lock = threading.Lock()


class _TokenMap:
    """Bidirectional token mapping for one anonymisation session."""

    def __init__(self):
        self.forward: dict[str, str] = {}   # real → placeholder
        self.reverse: dict[str, str] = {}   # placeholder → real
        self._counters: dict[str, int] = {}

    def _next(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}_{n:03d}"

    def placeholder_for(self, real: str, prefix: str) -> str:
        if real in self.forward:
            return self.forward[real]
        ph = self._next(prefix)
        self.forward[real] = ph
        self.reverse[ph] = real
        return ph

    def restore(self, text: str) -> str:
        # Replace longest placeholders first to avoid partial matches
        for ph in sorted(self.reverse, key=len, reverse=True):
            text = text.replace(ph, self.reverse[ph])
        return text


def _new_session(session_id: str) -> _TokenMap:
    tm = _TokenMap()
    with _sessions_lock:
        _sessions[session_id] = tm
    return tm


def _get_session(session_id: str) -> Optional[_TokenMap]:
    with _sessions_lock:
        return _sessions.get(session_id)


def _drop_session(session_id: str):
    with _sessions_lock:
        _sessions.pop(session_id, None)


# ── Regex patterns ────────────────────────────────────────────────────────────
# We process the text in layers, from most-specific to least-specific,
# so that e.g. string literals are replaced before identifiers inside them.

# Potential secret values: long hex/base64 strings that look like API keys/tokens
_RE_SECRET = re.compile(
    r'(?<![A-Za-z0-9_])'              # not preceded by alnum/_
    r'[A-Za-z0-9\-_]{32,}'            # 32+ char token
    r'(?![A-Za-z0-9_])',              # not followed by alnum/_
)

# File path patterns (Unix and Windows)
_RE_PATH_UNIX  = re.compile(r'/(?:[A-Za-z0-9_.\-]+/)+[A-Za-z0-9_.\-]*')
_RE_PATH_WIN   = re.compile(r'[A-Za-z]:\\(?:[^\\/:*?"<>|\r\n]+\\)*[^\\/:*?"<>|\r\n]*')

# Email addresses
_RE_EMAIL = re.compile(r'[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}')

# IPv4 addresses
_RE_IP = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')

# URLs
_RE_URL = re.compile(r'https?://[^\s\'"<>]+')

# Python / JS / TS / Java single-line comments
_RE_COMMENT_SL = re.compile(r'(?m)(#[^\n]*|//[^\n]*)')

# Python multiline docstrings / block strings
_RE_DOCSTRING = re.compile(r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')', re.DOTALL)

# C-style /* */ block comments
_RE_COMMENT_ML = re.compile(r'/\*[\s\S]*?\*/', re.DOTALL)

# String literals (double and single quoted, non-greedy, no newline for single-line)
_RE_STRING_DQ = re.compile(r'"(?:[^"\\]|\\.)*"')
_RE_STRING_SQ = re.compile(r"'(?:[^'\\]|\\.)*'")

# Identifiers: camelCase, snake_case, PascalCase — exclude keywords
_PYTHON_KEYWORDS = frozenset({
    "False","None","True","and","as","assert","async","await","break","class",
    "continue","def","del","elif","else","except","finally","for","from","global",
    "if","import","in","is","lambda","nonlocal","not","or","pass","raise","return",
    "try","while","with","yield","print","self","cls","super","int","str","float",
    "bool","list","dict","set","tuple","type","len","range","open","input","exit",
})
_JS_KEYWORDS = frozenset({
    "const","let","var","function","return","if","else","for","while","do","break",
    "continue","switch","case","default","class","extends","import","export","from",
    "async","await","try","catch","finally","throw","new","this","typeof","instanceof",
    "null","undefined","true","false","void","delete","in","of","yield",
})
_ALL_KEYWORDS = _PYTHON_KEYWORDS | _JS_KEYWORDS

_RE_IDENT = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]{2,})\b')


def _replace_pattern(text: str, pattern: re.Pattern, tm: _TokenMap, prefix: str) -> str:
    def _repl(m: re.Match) -> str:
        return tm.placeholder_for(m.group(0), prefix)
    return pattern.sub(_repl, text)


def _replace_identifiers(text: str, tm: _TokenMap) -> str:
    def _repl(m: re.Match) -> str:
        word = m.group(1)
        if word in _ALL_KEYWORDS:
            return word
        # Skip ALL-CAPS constants shorter than 6 chars (likely language tokens)
        if word.isupper() and len(word) < 6:
            return word
        # Skip placeholder tokens themselves
        if re.match(r'^[A-Z]+_\d{3}$', word):
            return word
        return tm.placeholder_for(word, "ID")
    return _RE_IDENT.sub(_repl, text)


# ── Public API ────────────────────────────────────────────────────────────────

def anonymise(text: str, session_id: str) -> str:
    """
    Anonymise code/text for safe external transmission.
    The session_id is used to retrieve the reverse map for de-anonymisation.
    Call de_anonymise(response, session_id) after the LLM responds.
    """
    tm = _new_session(session_id)

    # Layer 1: URLs (before path patterns eat them)
    text = _replace_pattern(text, _RE_URL, tm, "URL")

    # Layer 2: Emails
    text = _replace_pattern(text, _RE_EMAIL, tm, "EMAIL")

    # Layer 3: IPs
    text = _replace_pattern(text, _RE_IP, tm, "IP")

    # Layer 4: File paths (Windows then Unix)
    text = _replace_pattern(text, _RE_PATH_WIN, tm, "PATH")
    text = _replace_pattern(text, _RE_PATH_UNIX, tm, "PATH")

    # Layer 5: Multiline docstrings / block strings (before single-line strings)
    text = _replace_pattern(text, _RE_DOCSTRING, tm, "DOC")

    # Layer 6: Block comments
    text = _replace_pattern(text, _RE_COMMENT_ML, tm, "CMT")

    # Layer 7: Single-line comments
    text = _replace_pattern(text, _RE_COMMENT_SL, tm, "CMT")

    # Layer 8: String literals (double then single quoted)
    text = _replace_pattern(text, _RE_STRING_DQ, tm, "STR")
    text = _replace_pattern(text, _RE_STRING_SQ, tm, "STR")

    # Layer 9: Secret-looking tokens (long hex/base64)
    text = _replace_pattern(text, _RE_SECRET, tm, "SECRET")

    # Layer 10: Identifiers (last — everything else already replaced)
    text = _replace_identifiers(text, tm)

    return text


def de_anonymise(text: str, session_id: str) -> str:
    """
    Reverse the anonymisation applied to session_id.
    Replaces all placeholders in the LLM response with the original tokens.
    Drops the session map from memory afterwards.
    """
    tm = _get_session(session_id)
    if tm is None:
        return text   # no session — return as-is
    result = tm.restore(text)
    _drop_session(session_id)
    return result


def _anonymise_with_session(text: str, session_id: str, tm: "_TokenMap") -> str:
    """Anonymise additional text using an already-created session map."""
    if not text:
        return text

    text = _replace_pattern(text, _RE_URL, tm, "URL")
    text = _replace_pattern(text, _RE_EMAIL, tm, "EMAIL")
    text = _replace_pattern(text, _RE_IP, tm, "IP")
    text = _replace_pattern(text, _RE_PATH_WIN, tm, "PATH")
    text = _replace_pattern(text, _RE_PATH_UNIX, tm, "PATH")
    text = _replace_pattern(text, _RE_DOCSTRING, tm, "DOC")
    text = _replace_pattern(text, _RE_COMMENT_ML, tm, "CMT")
    text = _replace_pattern(text, _RE_COMMENT_SL, tm, "CMT")
    text = _replace_pattern(text, _RE_STRING_DQ, tm, "STR")
    text = _replace_pattern(text, _RE_STRING_SQ, tm, "STR")
    text = _replace_pattern(text, _RE_SECRET, tm, "SECRET")
    text = _replace_identifiers(text, tm)
    return text

def guard_prompt(prompt: str, system: str, provider: str) -> tuple[str, str, Optional[str]]:
    """
    Convenience wrapper used by llm_client.call_llm().
    Returns (anonymised_prompt, anonymised_system, session_id).
    session_id is None if guarding was not applied.
    """
    if not should_guard(provider):
        return prompt, system, None

    import uuid
    session_id = uuid.uuid4().hex
    tm = _new_session(session_id)

    safe_prompt = _anonymise_with_session(prompt, session_id, tm)
    safe_system = _anonymise_with_session(system, session_id, tm)

    return safe_prompt, safe_system, session_id


def guard_response(response: str, session_id: Optional[str]) -> str:
    """
    De-anonymise the LLM response if a session_id was used.
    Pass the session_id returned by guard_prompt().
    """
    if not session_id:
        return response
    return de_anonymise(response, session_id)


# ── Status / info ─────────────────────────────────────────────────────────────

def get_guard_info() -> dict:
    """Return current guard status for the Settings panel."""
    mode = get_guard_mode()
    return {
        "mode": mode,
        "mode_description": {
            "off":    "Disabled — all prompts sent as-is to every provider.",
            "auto":   "Auto — anonymises only HIGH-risk providers (e.g. DeepSeek).",
            "always": "Always — anonymises MEDIUM + HIGH risk providers (Groq, OpenRouter, DeepSeek, …).",
            "strict": "Strict — anonymises all external cloud providers including OpenAI / Anthropic / Google.",
        }.get(mode, ""),
        "provider_risks": PROVIDER_RISK,
        "active_sessions": len(_sessions),
    }
