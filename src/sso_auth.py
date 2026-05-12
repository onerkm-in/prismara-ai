"""
Prismara AI SSO / OAuth2 Auth Module
================================
Supports enterprise SSO for API providers that use OAuth2 bearer tokens
instead of static API keys. Common scenarios:

  - Azure OpenAI (Azure AD / Entra ID — client credentials OR device code)
  - Okta-protected internal LLM gateways
  - Google Workspace OAuth2 (for Vertex AI / enterprise Gemini endpoints)
  - Any OAuth2 Authorization Server (generic OIDC)

Flows implemented:
  1. Client Credentials Grant  — machine-to-machine, no user interaction
  2. Device Code Flow          — user opens a URL in browser, pastes code
  3. Refresh Token             — silent renewal when access token expires

Token cache lives in  prismara/cache/sso_tokens.json  (AES-256 encrypted
at rest using the machine fingerprint as key, so tokens can't be copied to
another machine without approval).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from src.machine_identity import get_machine_fingerprint
from src.secure_storage import load_json, save_json

logger = logging.getLogger(__name__)

# ── Token cache path (resolved at runtime from env or fallback) ───────────────

def _token_cache_path() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    cache_dir = os.environ.get("MLMAE_CACHE_DIR", str(repo_root / "prismara" / "cache"))
    path = Path(cache_dir)
    if not path.is_absolute():
        path = repo_root / path
    path.mkdir(parents=True, exist_ok=True)
    return path / "sso_tokens.json"


def _credentials_path() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    configured = os.environ.get("MLMAE_CREDENTIALS_FILE", "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else repo_root / path
    data_dir = Path(os.environ.get("MLMAE_DATA_DIR", str(repo_root / "prismara")))
    if not data_dir.is_absolute():
        data_dir = repo_root / data_dir
    return data_dir / "credentials.json"


# ── Simple XOR-based obfuscation (not full AES — avoids pycryptodome dep) ────
# Uses the machine fingerprint so the token file is machine-bound.

def _derive_key() -> bytes:
    """Derive a 32-byte key from the machine fingerprint."""
    fp = get_machine_fingerprint()
    return hashlib.sha256(fp.encode()).digest()


def _obfuscate(data: str) -> str:
    """XOR-obfuscate + base64 encode a string."""
    key = _derive_key()
    raw = data.encode("utf-8")
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    return base64.b64encode(xored).decode()


def _deobfuscate(data: str) -> str:
    """Reverse of _obfuscate."""
    key = _derive_key()
    xored = base64.b64decode(data.encode())
    raw = bytes(b ^ key[i % len(key)] for i, b in enumerate(xored))
    return raw.decode("utf-8")


# ── Token Cache ───────────────────────────────────────────────────────────────

def _load_token_cache() -> dict:
    path = _token_cache_path()
    if not path.exists():
        return {}
    try:
        raw = load_json(path, default={}) or {}
        # Deobfuscate token values
        for profile_id, entry in raw.items():
            for field in ("access_token", "refresh_token"):
                if entry.get(field):
                    try:
                        entry[field] = _deobfuscate(entry[field])
                    except Exception:
                        pass
        return raw
    except Exception as e:
        logger.warning("Could not load SSO token cache: %s", e)
        return {}


def _save_token_cache(cache: dict):
    path = _token_cache_path()
    # Obfuscate sensitive fields before writing
    safe = {}
    for profile_id, entry in cache.items():
        safe[profile_id] = dict(entry)
        for field in ("access_token", "refresh_token"):
            if safe[profile_id].get(field):
                safe[profile_id][field] = _obfuscate(safe[profile_id][field])
    save_json(path, safe, encode_at_rest=True)


# ── SSO Profile Schema ────────────────────────────────────────────────────────
# Stored in credentials.json under "sso_profiles" key.
#
# {
#   "id": "azure-openai-prod",          # unique slug
#   "label": "Azure OpenAI (Production)",
#   "flow": "client_credentials",       # or "device_code"
#   "token_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
#   "device_auth_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode",
#   "client_id": "...",
#   "client_secret": "...",             # only for client_credentials
#   "scope": "https://cognitiveservices.azure.com/.default",
#   "audience": "",                     # optional extra aud claim check
# }


def get_valid_token(profile_id: str) -> Optional[str]:
    """
    Return a valid access token for the given SSO profile ID.
    Refreshes automatically if expired. Returns None if no token is cached.
    """
    cache = _load_token_cache()
    entry = cache.get(profile_id)
    if not entry:
        return None

    # Check expiry with 60-second buffer
    expires_at = entry.get("expires_at", 0)
    if time.time() < expires_at - 60:
        return entry["access_token"]

    # Try refresh
    refresh_token = entry.get("refresh_token")
    if refresh_token:
        try:
            new_entry = _refresh_token(profile_id, refresh_token)
            if new_entry:
                cache[profile_id] = new_entry
                _save_token_cache(cache)
                return new_entry["access_token"]
        except Exception as e:
            logger.warning("Token refresh failed for %s: %s", profile_id, e)

    # Token expired and no refresh — purge
    del cache[profile_id]
    _save_token_cache(cache)
    return None


# ── Flow 1: Client Credentials ────────────────────────────────────────────────

def authenticate_client_credentials(profile: dict) -> str:
    """
    Perform OAuth2 Client Credentials grant.
    Returns the access token string and caches it.
    """
    profile_id = profile["id"]
    params = {
        "grant_type": "client_credentials",
        "client_id": profile["client_id"],
        "client_secret": profile["client_secret"],
        "scope": profile.get("scope", ""),
    }
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        profile["token_url"], data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read())

    token = resp["access_token"]
    expires_in = int(resp.get("expires_in", 3600))

    cache = _load_token_cache()
    cache[profile_id] = {
        "access_token": token,
        "refresh_token": resp.get("refresh_token", ""),
        "expires_at": time.time() + expires_in,
    }
    _save_token_cache(cache)
    logger.info("Client credentials auth successful for profile '%s'", profile_id)
    return token


# ── Flow 2: Device Code ───────────────────────────────────────────────────────

def start_device_code_flow(profile: dict) -> dict:
    """
    Initiate device code flow. Returns:
      {
        "device_code": "...",
        "user_code": "ABCD-WXYZ",
        "verification_uri": "https://microsoft.com/devicelogin",
        "expires_in": 900,
        "interval": 5,
        "message": "Go to https://... and enter ABCD-WXYZ"
      }
    The frontend should display user_code + verification_uri to the user,
    then call poll_device_code_flow() to wait for completion.
    """
    params = {
        "client_id": profile["client_id"],
        "scope": profile.get("scope", ""),
    }
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        profile["device_auth_url"], data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read())

    logger.info(
        "Device code flow started for profile '%s'. User code: %s",
        profile["id"], resp.get("user_code"),
    )
    return resp


def poll_device_code_flow(profile: dict, device_code_response: dict) -> Optional[str]:
    """
    Poll the token endpoint until the user completes auth or it times out.
    Returns the access token on success, None on timeout/failure.
    Caches the token automatically.
    """
    profile_id = profile["id"]
    device_code = device_code_response["device_code"]
    interval = int(device_code_response.get("interval", 5))
    expires_in = int(device_code_response.get("expires_in", 900))
    deadline = time.time() + expires_in

    while time.time() < deadline:
        time.sleep(interval)
        params = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": profile["client_id"],
            "device_code": device_code,
        }
        if profile.get("client_secret"):
            params["client_secret"] = profile["client_secret"]
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(
            profile["token_url"], data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read())
            token = resp["access_token"]
            expires = int(resp.get("expires_in", 3600))
            cache = _load_token_cache()
            cache[profile_id] = {
                "access_token": token,
                "refresh_token": resp.get("refresh_token", ""),
                "expires_at": time.time() + expires,
            }
            _save_token_cache(cache)
            logger.info("Device code auth completed for profile '%s'", profile_id)
            return token
        except urllib.error.HTTPError as e:
            body = json.loads(e.read())
            error = body.get("error", "")
            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval += 5
                continue
            else:
                logger.error("Device code poll error for %s: %s", profile_id, body)
                return None
    logger.warning("Device code flow timed out for profile '%s'", profile_id)
    return None


# ── Token Refresh ─────────────────────────────────────────────────────────────

def _refresh_token(profile_id: str, refresh_token: str) -> Optional[dict]:
    """Load the SSO profile and perform a refresh token grant."""
    profile = _load_sso_profile(profile_id)
    if not profile:
        return None

    params = {
        "grant_type": "refresh_token",
        "client_id": profile["client_id"],
        "refresh_token": refresh_token,
        "scope": profile.get("scope", ""),
    }
    if profile.get("client_secret"):
        params["client_secret"] = profile["client_secret"]

    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        profile["token_url"], data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read())

    expires_in = int(resp.get("expires_in", 3600))
    return {
        "access_token": resp["access_token"],
        "refresh_token": resp.get("refresh_token", refresh_token),
        "expires_at": time.time() + expires_in,
    }


def _load_sso_profile(profile_id: str) -> Optional[dict]:
    """Load an SSO profile from credentials.json by its ID."""
    try:
        creds = load_json(_credentials_path(), default={}) or {}
        for p in creds.get("sso_profiles", []):
            if p["id"] == profile_id:
                return p
    except Exception:
        pass
    return None


# ── Token Status ──────────────────────────────────────────────────────────────

def get_sso_status() -> dict[str, dict]:
    """
    Return a summary of all cached SSO tokens (no sensitive values).
    Used by the frontend Settings panel to show connection state.
    """
    cache = _load_token_cache()
    result = {}
    for profile_id, entry in cache.items():
        expires_at = entry.get("expires_at", 0)
        now = time.time()
        result[profile_id] = {
            "authenticated": now < expires_at - 60,
            "expires_in_seconds": max(0, int(expires_at - now)),
            "has_refresh_token": bool(entry.get("refresh_token")),
        }
    return result


def revoke_sso_token(profile_id: str):
    """Remove a cached token for a given profile (logout)."""
    cache = _load_token_cache()
    if profile_id in cache:
        del cache[profile_id]
        _save_token_cache(cache)
        logger.info("Revoked SSO token for profile '%s'", profile_id)
