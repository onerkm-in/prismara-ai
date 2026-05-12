import json
import hashlib
import secrets
import urllib.request
import urllib.error
import zipfile
import io
import re
import threading
from functools import wraps
from datetime import datetime, timezone

from flask import Flask, request, jsonify, session, Response, stream_with_context, g
import sys
import os
import time
from pathlib import Path

try:
    from flask_cors import CORS
except Exception:
    CORS = None

# Add the project root to the Python path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.orchestrator import orchestrate
from src.secure_storage import load_json, save_json
from src.llm_client import load_saved_credentials_into_env
from src.chat_history import CHAT_HISTORY_LIMIT, append_chat_turn, clear_chat_history, load_chat_history

app = Flask(__name__)
app.secret_key = os.environ.get("MLMAE_FLASK_SECRET", secrets.token_hex(32))
if CORS is not None:
    CORS(app, origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ])

# ── Config helpers ────────────────────────────────────────────────────────────

def _data_dir() -> Path:
    configured = os.environ.get("MLMAE_DATA_DIR", "").strip()
    d = Path(configured) if configured else PROJECT_ROOT / "prismara"
    if not d.is_absolute():
        d = PROJECT_ROOT / d
    d.mkdir(parents=True, exist_ok=True)
    return d

def _load_json(path: Path, default=None):
    return load_json(path, default=default if default is not None else {})

def _save_json(path: Path, data):
    save_json(path, data, encode_at_rest=True)

def _creds_path() -> Path:
    configured = os.environ.get("MLMAE_CREDENTIALS_FILE", "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else PROJECT_ROOT / path
    return _data_dir() / "credentials.json"

def _backups_dir() -> Path:
    path = Path(os.environ.get("MLMAE_BACKUP_DIR", str(_data_dir() / "backups")))
    return path if path.is_absolute() else PROJECT_ROOT / path


# ── Production Hardening: Rate Limits + Audit Logs ───────────────────────────

_RATE_BUCKETS: dict[str, tuple[float, int]] = {}
_RATE_LOCK = threading.Lock()


def _logs_dir() -> Path:
    p = Path(os.environ.get("MLMAE_LOGS_DIR", str(_data_dir() / "logs")))
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def _audit_log(event: str, status: str = "ok", details: dict | None = None):
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "status": status,
            "actor": session.get("admin_email", "anonymous"),
            "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
            "method": request.method,
            "path": request.path,
            "details": details or {},
        }
        with (_logs_dir() / "audit.log").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except Exception:
        pass


def _remember_chat_turn(*args, **kwargs):
    try:
        append_chat_turn(*args, **kwargs)
    except Exception as e:
        _audit_log("chat_history_append", "error", {"error": str(e)[:300]})


def _client_key() -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    actor = session.get("admin_email", "anon")
    return f"{ip}|{actor}"


def _rate_limit(scope: str, limit: int, window_seconds: int) -> bool:
    now = time.time()
    key = f"{scope}:{_client_key()}"
    with _RATE_LOCK:
        start, count = _RATE_BUCKETS.get(key, (now, 0))
        if now - start >= window_seconds:
            start, count = now, 0
        if count >= limit:
            return False
        _RATE_BUCKETS[key] = (start, count + 1)
    return True


def _limit_for_path(path: str, method: str) -> tuple[str, int, int] | None:
    if path in ("/run", "/run-stream") and method == "POST":
        return ("run", 30, 60)
    if path == "/chat-history" and method == "DELETE":
        return ("settings_mutation", 60, 3600)
    if path.startswith("/admin/auth") or path == "/admin/register-local":
        return ("admin_auth", 20, 300)
    if path.startswith("/admin/backup-now"):
        return ("backup_now", 12, 3600)
    if path.startswith("/admin"):
        return ("admin_general", 240, 3600)
    if path.startswith("/settings/") and method in ("POST", "PUT", "DELETE"):
        return ("settings_mutation", 60, 3600)
    return None


@app.before_request
def _security_before_request():
    g._req_start = time.time()
    rule = _limit_for_path(request.path, request.method)
    if not rule:
        return None
    scope, limit, window = rule
    if not _rate_limit(scope, limit, window):
        _audit_log("rate_limit", "blocked", {"scope": scope, "limit": limit, "window_s": window})
        return jsonify({"error": "Too many requests. Please retry later.", "scope": scope}), 429
    return None


@app.after_request
def _security_after_request(response):
    try:
        path = request.path
        if path.startswith("/admin") or path.startswith("/settings") or path.startswith("/custom-models") or path in ("/run", "/run-stream", "/chat-history"):
            dur_ms = int((time.time() - float(getattr(g, "_req_start", time.time()))) * 1000)
            status = "ok" if response.status_code < 400 else "error"
            _audit_log("request", status, {"status_code": response.status_code, "duration_ms": dur_ms})
    except Exception:
        pass
    return response

def _config_path() -> Path:
    return _data_dir() / "config.json"


def _valid_env_key(key: str) -> bool:
    return bool(key) and all(c.isupper() or c.isdigit() or c == "_" for c in key)


load_saved_credentials_into_env()

def _admin_email_allowlist() -> list[str]:
    cfg = _load_json(_config_path(), {}) or {}
    emails = cfg.get("admin_allowlist", [])
    if isinstance(emails, list):
        return [str(e).strip().lower() for e in emails if str(e).strip()]
    return []

def _set_first_admin_if_empty(email: str):
    cfg = _load_json(_config_path(), {}) or {}
    allow = cfg.get("admin_allowlist", [])
    if not allow:
        cfg["admin_allowlist"] = [email.lower()]
        _save_json(_config_path(), cfg)

def _google_oauth_client_id() -> str:
    # Priority: env -> credentials.json
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    if cid:
        return cid
    creds = _load_json(_creds_path(), {}) or {}
    cid = str(creds.get("GOOGLE_OAUTH_CLIENT_ID", "")).strip()
    if cid:
        return cid
    return ""

def _hash_password(password: str, salt_hex: str) -> str:
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 200_000)
    return dk.hex()

def _find_local_admin(username: str):
    cfg = _load_json(_config_path(), {}) or {}
    users = cfg.get('local_admin_accounts', [])
    for u in users:
        if str(u.get('username', '')).lower() == username.lower():
            return u
    return None

def _can_bootstrap_local_admin() -> bool:
    cfg = _load_json(_config_path(), {}) or {}
    users = cfg.get('local_admin_accounts', [])
    return len(users) == 0

def _verify_google_id_token(id_token_value: str) -> dict:
    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as grequests
    except Exception as e:
        raise RuntimeError("google-auth package is required for Google SSO verification") from e

    client_id = _google_oauth_client_id()
    if not client_id:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID is not configured")

    info = id_token.verify_oauth2_token(id_token_value, grequests.Request(), client_id)
    email = str(info.get("email", "")).strip().lower()
    if not email:
        raise RuntimeError("Google token missing email")
    if not info.get("email_verified", False):
        raise RuntimeError("Google email is not verified")

    _set_first_admin_if_empty(email)
    allow = _admin_email_allowlist()
    if email not in allow:
        raise RuntimeError("This Google account is not authorized for Admin access")

    return {"email": email, "name": info.get("name", ""), "picture": info.get("picture", "")}

def admin_required(fn):
    @wraps(fn)
    def _wrapped(*args, **kwargs):
        if not session.get("admin_email"):
            return jsonify({"error": "Admin login required"}), 401
        return fn(*args, **kwargs)
    return _wrapped

# Directories to skip when reading a workspace folder
SKIP_DIRS = {
    '.git', 'node_modules', '__pycache__', '.venv', 'venv',
    'dist', 'build', '.next', '.idea', '.vscode', 'coverage', '.mypy_cache',
    '.pytest_cache', 'mlmae', 'prismara', 'MLMAE_Data', 'PrismaraAI_Data', 'logs',
}

# File extensions to read as text
TEXT_EXTENSIONS = {
    '.py', '.js', '.jsx', '.ts', '.tsx', '.json', '.md', '.txt',
    '.html', '.css', '.scss', '.yml', '.yaml', '.toml', '.cfg',
    '.ini', '.sh', '.bat', '.env', '.sql', '.xml', '.go', '.rs',
    '.java', '.c', '.cpp', '.h', '.cs', '.php', '.rb', '.swift', '.kt'
}

MAX_FILE_SIZE_BYTES = 100 * 1024  # 100 KB per file cap
MAX_FOLDER_FILES = int(os.environ.get("MLMAE_MAX_FOLDER_FILES", "1000"))
MAX_FOLDER_BYTES = int(os.environ.get("MLMAE_MAX_FOLDER_BYTES", str(2 * 1024 * 1024)))


def _read_folder_contents(folder_path: str) -> dict:
    """Walk a local folder and return text file contents, skipping binaries and noise."""
    file_tree = []
    file_contents = {}
    bytes_read = 0

    for root, dirs, files in os.walk(folder_path):
        dirs[:] = sorted([d for d in dirs if d not in SKIP_DIRS])
        rel_root = os.path.relpath(root, folder_path).replace('\\', '/')
        if rel_root == '.':
            rel_root = ''

        for file in sorted(files):
            filepath = os.path.join(root, file)
            rel_path = os.path.join(rel_root, file).replace('\\', '/').lstrip('/')
            ext = Path(file).suffix.lower()

            if ext in TEXT_EXTENSIONS:
                file_tree.append(rel_path)
                if len(file_tree) >= MAX_FOLDER_FILES:
                    dirs[:] = []
                    break
                try:
                    size = os.path.getsize(filepath)
                    if size <= MAX_FILE_SIZE_BYTES and bytes_read + size <= MAX_FOLDER_BYTES:
                        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                            file_contents[rel_path] = f.read()
                        bytes_read += size
                except Exception:
                    pass
        if len(file_tree) >= MAX_FOLDER_FILES:
            break

    return {
        "folder": folder_path,
        "file_count": len(file_tree),
        "file_tree": file_tree,
        "file_contents": file_contents,
        "bytes_read": bytes_read,
        "truncated": len(file_tree) >= MAX_FOLDER_FILES,
    }


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _direct_answer_if_simple(user_prompt: str) -> str | None:
    text = (user_prompt or "").strip().lower()
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


@app.route('/load-folder', methods=['POST'])
def load_folder():
    """Validate a local folder path and return its file tree."""
    data = request.json or {}
    folder_path = data.get('folder_path', '').strip()

    if not folder_path:
        return jsonify({"error": "No folder path provided"}), 400

    if not os.path.isdir(folder_path):
        return jsonify({"error": f"Folder not found: {folder_path}"}), 404

    result = _read_folder_contents(folder_path)
    # Return tree and metadata only (not file contents) to keep payload small
    return jsonify({
        "file_tree": result["file_tree"],
        "file_count": result["file_count"],
        "folder": result["folder"]
    })


@app.route('/chat-history', methods=['GET', 'DELETE'])
def chat_history():
    if request.method == 'DELETE':
        clear_chat_history()
        return jsonify({"ok": True, "messages": []})
    return jsonify({"messages": load_chat_history(), "limit": CHAT_HISTORY_LIMIT})


@app.route('/run', methods=['POST'])
def run_task():
    data = request.json or {}
    user_prompt = data.get('prompt')
    folder_path = data.get('folder_path', '').strip()

    if not user_prompt:
        return jsonify({"error": "No prompt provided"}), 400

    direct_answer = _direct_answer_if_simple(user_prompt)
    if direct_answer:
        _remember_chat_turn(
            user_prompt,
            direct_answer,
            raw_output=direct_answer,
            metrics={"models": ["System (Deterministic)"]},
            workspace=folder_path,
        )
        return jsonify({
            "result": direct_answer,
            "final_answer": direct_answer,
            "models": ["System (Deterministic)"]
        })

    codebase_context = None
    if folder_path:
        if not os.path.isdir(folder_path):
            return jsonify({"error": f"Workspace folder not found: {folder_path}"}), 400
        codebase_context = _read_folder_contents(folder_path)

    online_consent = bool(data.get("online_consent", False))

    final_answer = ""
    result_text = ""
    metrics: dict = {}
    error: str | None = None
    for event in orchestrate(user_prompt, codebase_context=codebase_context,
                             online_consent=online_consent):
        kind = event.get("type")
        if kind == "final_answer":
            final_answer = event.get("content", "")
        elif kind == "result":
            result_text = event.get("content", "")
        elif kind == "metrics":
            metrics = event
        elif kind == "error":
            error = event.get("message", "orchestrator error")

    if error and not final_answer:
        _remember_chat_turn(
            user_prompt,
            "",
            raw_output=result_text,
            metrics=metrics,
            workspace=folder_path,
            status="error",
            error=error,
        )
        return jsonify({"error": error}), 500

    _remember_chat_turn(
        user_prompt,
        final_answer or result_text,
        raw_output=result_text,
        metrics=metrics,
        workspace=folder_path,
        status="ok",
    )

    return jsonify({
        "result": result_text or final_answer,
        "final_answer": final_answer,
        "models": metrics.get("models", []),
        "metrics": metrics,
    })


@app.route('/run-stream', methods=['POST'])
def run_task_stream():
    """Stream pipeline progress as NDJSON. Each stage emits its own event."""
    data = request.json or {}
    user_prompt = data.get('prompt', '').strip()
    folder_path = data.get('folder_path', '').strip()

    if not user_prompt:
        return jsonify({"error": "No prompt provided"}), 400

    direct_answer = _direct_answer_if_simple(user_prompt)
    if direct_answer:
        started_at = time.perf_counter()
        process_ms = round((time.perf_counter() - started_at) * 1000)
        metrics = {
            "request_id": secrets.token_hex(8),
            "process_ms": process_ms,
            "models": ["System (Deterministic)"],
            "model_footprint": {"count": 1, "models": ["System (Deterministic)"]},
        }
        _remember_chat_turn(
            user_prompt,
            direct_answer,
            raw_output=direct_answer,
            metrics=metrics,
            workspace=folder_path,
        )

        def quick_generate():
            payload_result = f"[USER REQUEST]: {user_prompt}\n\n[FINAL ANSWER]:\n{direct_answer}"
            yield json.dumps({"type": "final_answer", "content": direct_answer}) + "\n"
            yield json.dumps({
                "type": "metrics",
                **metrics,
            }) + "\n"
            yield json.dumps({"type": "result", "content": payload_result,
                              "final_answer": direct_answer}) + "\n"
            yield json.dumps({"type": "done"}) + "\n"

        return Response(stream_with_context(quick_generate()), mimetype='application/x-ndjson')

    codebase_context = None
    if folder_path:
        if not os.path.isdir(folder_path):
            return jsonify({"error": f"Workspace folder not found: {folder_path}"}), 400
        codebase_context = _read_folder_contents(folder_path)

    online_consent = bool(data.get("online_consent", False))

    @stream_with_context
    def generate():
        final_answer = ""
        result_text = ""
        metrics: dict = {}
        error = ""
        try:
            for event in orchestrate(user_prompt, codebase_context=codebase_context,
                                     online_consent=online_consent):
                kind = event.get("type")
                if kind == "final_answer":
                    final_answer = event.get("content", "")
                elif kind == "result":
                    result_text = event.get("content", "")
                elif kind == "metrics":
                    metrics = event
                elif kind == "error":
                    error = event.get("message", "orchestrator error")
                yield json.dumps(event, ensure_ascii=True) + "\n"
        finally:
            _remember_chat_turn(
                user_prompt,
                final_answer or result_text,
                raw_output=result_text,
                metrics=metrics,
                workspace=folder_path,
                status="error" if error and not (final_answer or result_text) else "ok",
                error=error,
            )

    return Response(generate(), mimetype='application/x-ndjson')


# ── Admin / Google SSO (MVP1) ───────────────────────────────────────────────

@app.route('/admin/bootstrap', methods=['GET'])
def admin_bootstrap():
    return jsonify({
        "google_oauth_client_id": _google_oauth_client_id(),
        "sso_provider": "google",
        "mvp": "1",
        "local_auth_enabled": True,
        "can_bootstrap_local_admin": _can_bootstrap_local_admin(),
    })

@app.route('/admin/auth/google', methods=['POST'])
def admin_auth_google():
    token = (request.json or {}).get("id_token", "")
    if not token:
        return jsonify({"error": "id_token is required"}), 400
    try:
        user = _verify_google_id_token(token)
        session["admin_email"] = user["email"]
        session["admin_name"] = user.get("name", "")
        return jsonify({"ok": True, "user": user})
    except Exception as e:
        return jsonify({"error": str(e)}), 401

@app.route('/admin/register-local', methods=['POST'])
def admin_register_local():
    body = request.json or {}
    username = str(body.get('username', '')).strip()
    password = str(body.get('password', '')).strip()
    if not username or not password:
        return jsonify({'error': 'username and password are required'}), 400
    if len(password) < 8:
        return jsonify({'error': 'password must be at least 8 characters'}), 400

    bootstrap = _can_bootstrap_local_admin()
    if not bootstrap and not session.get('admin_email'):
        return jsonify({'error': 'Admin login required to create additional local accounts'}), 401

    cfg = _load_json(_config_path(), {}) or {}
    users = cfg.get('local_admin_accounts', [])
    if any(str(u.get('username', '')).lower() == username.lower() for u in users):
        return jsonify({'error': 'username already exists'}), 409

    salt = secrets.token_hex(16)
    pwd_hash = _hash_password(password, salt)
    users.append({'username': username, 'salt': salt, 'password_hash': pwd_hash})
    cfg['local_admin_accounts'] = users
    _save_json(_config_path(), cfg)
    return jsonify({'ok': True, 'username': username})

@app.route('/admin/auth/local', methods=['POST'])
def admin_auth_local():
    body = request.json or {}
    username = str(body.get('username', '')).strip()
    password = str(body.get('password', '')).strip()
    if not username or not password:
        return jsonify({'error': 'username and password are required'}), 400

    user = _find_local_admin(username)
    if not user:
        return jsonify({'error': 'Invalid credentials'}), 401

    expected = str(user.get('password_hash', ''))
    actual = _hash_password(password, str(user.get('salt', '')))
    if actual != expected:
        return jsonify({'error': 'Invalid credentials'}), 401

    session['admin_email'] = f"local:{username}"
    session['admin_name'] = username
    return jsonify({'ok': True, 'user': {'email': f'local:{username}', 'name': username}})

@app.route('/admin/session', methods=['GET'])
def admin_session_status():
    email = session.get("admin_email")
    if not email:
        return jsonify({"authenticated": False})
    return jsonify({
        "authenticated": True,
        "email": email,
        "name": session.get("admin_name", ""),
    })

@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop("admin_email", None)
    session.pop("admin_name", None)
    return jsonify({"ok": True})

@app.route('/admin/memory-dump', methods=['GET'])
@admin_required
def admin_memory_dump():
    try:
        from src.memory_core import read_memory
        dump = read_memory()
        payload = json.dumps(dump, indent=2)
        return Response(
            payload,
            mimetype='application/json',
            headers={'Content-Disposition': 'attachment; filename=prismara_neural_memory_dump.json'}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/admin/storage-health', methods=['GET'])
@admin_required
def admin_storage_health():
    """Return quick backup/snapshot health so user can detect corruption risk."""
    bdir = _backups_dir()
    files = [p for p in bdir.rglob('*.bak.json') if p.is_file()]
    return jsonify({
        "backup_root": str(bdir),
        "backup_files": len(files),
        "critical_files": {
            "config_exists": _config_path().exists(),
            "credentials_exists": _creds_path().exists(),
            "memory_exists": (_data_dir() / 'neural_memory.json').exists(),
        }
    })


@app.route('/admin/recovery-pack', methods=['GET'])
@admin_required
def admin_recovery_pack():
    """Export an encrypted-at-rest recovery pack for disaster backup."""
    root = _data_dir()
    keep = [
        root / 'config.json',
        root / 'credentials.json',
        root / 'neural_memory.json',
        root / '.machine_id',
    ]

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        for p in keep:
            if p.exists() and p.is_file():
                zf.writestr(f"prismara/{p.name}", p.read_bytes())

        bdir = _backups_dir()
        if bdir.exists():
            for bf in bdir.rglob('*.bak.json'):
                rel = bf.relative_to(root) if str(bf).startswith(str(root)) else Path('backups') / bf.name
                zf.writestr(f"prismara/{rel.as_posix()}", bf.read_bytes())

    mem.seek(0)
    return Response(
        mem.getvalue(),
        mimetype='application/zip',
        headers={'Content-Disposition': 'attachment; filename=prismara_recovery_pack.zip'}
    )


def _build_recovery_pack_bytes() -> bytes:
    root = _data_dir()
    keep = [
        root / 'config.json',
        root / 'credentials.json',
        root / 'neural_memory.json',
        root / '.machine_id',
    ]

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        for p in keep:
            if p.exists() and p.is_file():
                zf.writestr(f"prismara/{p.name}", p.read_bytes())

        bdir = _backups_dir()
        if bdir.exists():
            for bf in bdir.rglob('*.bak.json'):
                rel = bf.relative_to(root) if str(bf).startswith(str(root)) else Path('backups') / bf.name
                zf.writestr(f"prismara/{rel.as_posix()}", bf.read_bytes())

    mem.seek(0)
    return mem.getvalue()


@app.route('/admin/backup-config', methods=['GET'])
@admin_required
def admin_backup_config_get():
    cfg = _load_json(_config_path(), {}) or {}
    return jsonify({
        'mode': cfg.get('backup_mode', 'local'),
        'gcs_bucket': cfg.get('gcs_bucket', ''),
        'gcs_prefix': cfg.get('gcs_prefix', 'prismara-backups'),
        'last_cloud_backup_at': cfg.get('last_cloud_backup_at', 0),
        'last_cloud_backup_bucket': cfg.get('last_cloud_backup_bucket', ''),
        'last_cloud_backup_blob': cfg.get('last_cloud_backup_blob', ''),
        'last_gcs_test_at': cfg.get('last_gcs_test_at', 0),
        'last_gcs_test_ok': cfg.get('last_gcs_test_ok', None),
    })


@app.route('/admin/backup-config', methods=['POST'])
@admin_required
def admin_backup_config_set():
    body = request.json or {}
    mode = str(body.get('mode', 'local')).strip().lower()
    if mode not in ('local', 'google_cloud', 'both'):
        return jsonify({'error': 'mode must be one of: local, google_cloud, both'}), 400

    cfg = _load_json(_config_path(), {}) or {}
    cfg['backup_mode'] = mode
    cfg['gcs_bucket'] = str(body.get('gcs_bucket', '')).strip()
    cfg['gcs_prefix'] = str(body.get('gcs_prefix', 'prismara-backups')).strip() or 'prismara-backups'
    _save_json(_config_path(), cfg)
    return jsonify({'ok': True, 'mode': mode})


@app.route('/admin/backup-now', methods=['POST'])
@admin_required
def admin_backup_now():
    cfg = _load_json(_config_path(), {}) or {}
    mode = cfg.get('backup_mode', 'local')
    bucket = str(cfg.get('gcs_bucket', '')).strip()
    prefix = str(cfg.get('gcs_prefix', 'prismara-backups')).strip() or 'prismara-backups'

    payload = _build_recovery_pack_bytes()
    ts = int(time.time())
    name = f"prismara_recovery_{ts}.zip"
    result = {'mode': mode, 'local': None, 'google_cloud': None}

    if mode in ('local', 'both'):
        out_dir = _backups_dir() / 'recovery-packs'
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / name
        out_path.write_bytes(payload)
        result['local'] = {'ok': True, 'path': str(out_path)}

    if mode in ('google_cloud', 'both'):
        if not bucket:
            result['google_cloud'] = {'ok': False, 'error': 'gcs_bucket is not configured'}
        else:
            try:
                from google.cloud import storage
                creds = _load_json(_creds_path(), {}) or {}
                service_account_json = str(creds.get('GCS_SERVICE_ACCOUNT_JSON', '')).strip()

                if service_account_json:
                    from google.oauth2 import service_account
                    info = json.loads(service_account_json)
                    sc = service_account.Credentials.from_service_account_info(info)
                    client = storage.Client(credentials=sc, project=sc.project_id)
                else:
                    client = storage.Client()

                blob_name = f"{prefix.rstrip('/')}/{name}"
                bkt = client.bucket(bucket)
                blob = bkt.blob(blob_name)
                blob.upload_from_string(payload, content_type='application/zip')
                result['google_cloud'] = {'ok': True, 'bucket': bucket, 'blob': blob_name}
                cfg['last_cloud_backup_at'] = int(time.time())
                cfg['last_cloud_backup_bucket'] = bucket
                cfg['last_cloud_backup_blob'] = blob_name
            except Exception as e:
                result['google_cloud'] = {'ok': False, 'error': str(e)}

    _save_json(_config_path(), cfg)

    return jsonify({'ok': True, 'result': result})


@app.route('/admin/backup-test-gcs', methods=['POST'])
@admin_required
def admin_backup_test_gcs():
    cfg = _load_json(_config_path(), {}) or {}
    bucket = str(cfg.get('gcs_bucket', '')).strip()
    prefix = str(cfg.get('gcs_prefix', 'prismara-backups')).strip() or 'prismara-backups'
    if not bucket:
        return jsonify({'ok': False, 'error': 'gcs_bucket is not configured'}), 200

    try:
        from google.cloud import storage
        creds = _load_json(_creds_path(), {}) or {}
        service_account_json = str(creds.get('GCS_SERVICE_ACCOUNT_JSON', '')).strip()

        if service_account_json:
            from google.oauth2 import service_account
            info = json.loads(service_account_json)
            sc = service_account.Credentials.from_service_account_info(info)
            client = storage.Client(credentials=sc, project=sc.project_id)
            auth_source = 'service_account_json'
        else:
            client = storage.Client()
            auth_source = 'application_default_credentials'

        bkt = client.bucket(bucket)
        exists = bkt.exists()
        if not exists:
            return jsonify({'ok': False, 'error': f"Bucket '{bucket}' not found or no access"}), 200

        test_blob_name = f"{prefix.rstrip('/')}/.prismara_probe_{int(time.time())}.txt"
        blob = bkt.blob(test_blob_name)
        blob.upload_from_string('prismara probe', content_type='text/plain')
        blob.delete()
        cfg['last_gcs_test_at'] = int(time.time())
        cfg['last_gcs_test_ok'] = True
        _save_json(_config_path(), cfg)
        return jsonify({'ok': True, 'bucket': bucket, 'auth_source': auth_source})
    except Exception as e:
        cfg['last_gcs_test_at'] = int(time.time())
        cfg['last_gcs_test_ok'] = False
        _save_json(_config_path(), cfg)
        return jsonify({'ok': False, 'error': str(e)}), 200


# ── Health ────────────────────────────────────────────────────────────────────

@app.route('/health')
def health():
    return jsonify({"status": "ok", "mode": "source"})


@app.route('/system-doctor', methods=['GET'])
@app.route('/api/system-doctor', methods=['GET'])
def system_doctor_public():
    try:
        from src.local_ai import system_doctor
        return jsonify(system_doctor())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Data Guard ────────────────────────────────────────────────────────────────

@app.route('/settings/data-guard', methods=['GET'])
@admin_required
def get_data_guard():
    try:
        from src.data_guard import get_guard_info
        return jsonify(get_guard_info())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/settings/data-guard', methods=['POST'])
@admin_required
def set_data_guard():
    mode = (request.json or {}).get("mode", "").strip().lower()
    if mode not in ("off", "auto", "always", "strict"):
        return jsonify({"error": "Invalid mode. Use: off, auto, always, strict"}), 400
    try:
        from src.data_guard import set_guard_mode
        set_guard_mode(mode)
        return jsonify({"ok": True, "mode": mode})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/settings/hardware', methods=['GET'])
@admin_required
def get_hardware_settings():
    try:
        from src.local_ai import hardware_policy
        return jsonify(hardware_policy())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/settings/hardware', methods=['POST'])
@admin_required
def set_hardware_settings():
    body = request.json or {}
    raw = body.get("hardware_acceleration_consent", False)
    enabled = raw if isinstance(raw, bool) else str(raw).strip().lower() in ("1", "true", "yes", "on")
    try:
        from src.local_ai import set_hardware_acceleration_consent
        return jsonify(set_hardware_acceleration_consent(bool(enabled)))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Custom Models ─────────────────────────────────────────────────────────────

@app.route('/custom-models', methods=['GET'])
@admin_required
def list_custom_models():
    try:
        from src.llm_client import load_custom_models
        models = load_custom_models()
        safe = []
        for m in models:
            entry = dict(m)
            if entry.get("api_key"):
                entry["api_key"] = "••••" + entry["api_key"][-4:]
            safe.append(entry)
        return jsonify(safe)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/custom-models', methods=['POST'])
@admin_required
def upsert_custom_model():
    entry = request.json or {}
    if not entry.get("label"):
        return jsonify({"error": "label is required"}), 400
    if not entry.get("base_url"):
        return jsonify({"error": "base_url is required"}), 400
    try:
        from src.llm_client import add_custom_model
        saved = add_custom_model(entry)
        return jsonify({"ok": True, "id": saved["id"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/custom-models/<model_id>', methods=['DELETE'])
@admin_required
def remove_custom_model(model_id):
    try:
        from src.llm_client import delete_custom_model
        delete_custom_model(model_id)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Agents ────────────────────────────────────────────────────────────────────

@app.route('/agents', methods=['GET'])
@admin_required
def list_agents():
    try:
        from src.llm_client import detect_available_agents
        return jsonify(list(detect_available_agents().values()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── SSO ───────────────────────────────────────────────────────────────────────

@app.route('/sso/status', methods=['GET'])
@admin_required
def sso_status():
    try:
        from src.sso_auth import get_sso_status
        return jsonify(get_sso_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/sso/profiles', methods=['GET'])
@admin_required
def sso_profiles():
    creds = _load_json(_creds_path(), {})
    profiles = [
        {"id": p.get("id"), "label": p.get("label"), "flow": p.get("flow"),
         "token_url": p.get("token_url"), "scope": p.get("scope")}
        for p in creds.get("sso_profiles", [])
    ]
    return jsonify(profiles)

@app.route('/sso/profiles', methods=['POST'])
@admin_required
def save_sso_profile():
    profile = request.json or {}
    if not profile.get("id") or not profile.get("token_url") or not profile.get("client_id"):
        return jsonify({"error": "id, token_url, client_id are required"}), 400
    creds = _load_json(_creds_path(), {})
    profiles = creds.get("sso_profiles", [])
    profiles = [p for p in profiles if p["id"] != profile["id"]]
    profiles.append(profile)
    creds["sso_profiles"] = profiles
    _save_json(_creds_path(), creds)
    return jsonify({"ok": True})

@app.route('/sso/profiles/<profile_id>', methods=['DELETE'])
@admin_required
def delete_sso_profile_route(profile_id):
    creds = _load_json(_creds_path(), {})
    creds["sso_profiles"] = [p for p in creds.get("sso_profiles", [])
                             if p["id"] != profile_id]
    _save_json(_creds_path(), creds)
    try:
        from src.sso_auth import revoke_sso_token
        revoke_sso_token(profile_id)
    except Exception:
        pass
    return jsonify({"ok": True})

@app.route('/sso/auth/client-credentials', methods=['POST'])
@admin_required
def sso_client_creds():
    profile_id = (request.json or {}).get("profile_id", "")
    try:
        from src.sso_auth import authenticate_client_credentials, _load_sso_profile
        profile = _load_sso_profile(profile_id)
        if not profile:
            return jsonify({"error": f"Profile '{profile_id}' not found"}), 404
        authenticate_client_credentials(profile)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/sso/auth/device-code/start', methods=['POST'])
@admin_required
def sso_device_start():
    profile_id = (request.json or {}).get("profile_id", "")
    try:
        from src.sso_auth import start_device_code_flow, _load_sso_profile
        profile = _load_sso_profile(profile_id)
        if not profile:
            return jsonify({"error": f"Profile '{profile_id}' not found"}), 404
        return jsonify(start_device_code_flow(profile))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/sso/auth/device-code/poll', methods=['POST'])
@admin_required
def sso_device_poll():
    body = request.json or {}
    profile_id = body.get("profile_id", "")
    device_code_response = body.get("device_code_response", {})
    if not profile_id or not device_code_response:
        return jsonify({"error": "profile_id and device_code_response required"}), 400
    try:
        from src.sso_auth import poll_device_code_flow, _load_sso_profile
        profile = _load_sso_profile(profile_id)
        if not profile:
            return jsonify({"error": f"Profile '{profile_id}' not found"}), 404
        token = poll_device_code_flow(profile, device_code_response)
        if token:
            return jsonify({"ok": True, "authenticated": True})
        return jsonify({"ok": False, "authenticated": False,
                        "message": "Timed out or user denied"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/sso/revoke', methods=['POST'])
@admin_required
def sso_revoke():
    profile_id = (request.json or {}).get("profile_id", "")
    try:
        from src.sso_auth import revoke_sso_token
        revoke_sso_token(profile_id)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Settings / Credentials ────────────────────────────────────────────────────

@app.route('/settings/credentials', methods=['GET'])
@admin_required
def get_credentials():
    creds = _load_json(_creds_path(), {})
    safe = {
        k: bool(str(v).strip())
        for k, v in creds.items()
        if k not in ("sso_profiles", "integrations") and not isinstance(v, (dict, list))
    }
    return jsonify({"keys_set": safe})

@app.route('/settings/credentials', methods=['POST'])
@admin_required
def save_credentials():
    updates = request.json or {}
    creds_path = _creds_path()
    creds = _load_json(creds_path, {})
    for key, val in updates.items():
        if key == "sso_profiles":
            continue
        if not _valid_env_key(str(key)):
            return jsonify({"error": f"Invalid credential key: {key}"}), 400
        if val:
            creds[key] = str(val)
            os.environ[key] = str(val)
        else:
            creds.pop(key, None)
            os.environ.pop(key, None)
    _save_json(creds_path, creds)
    return jsonify({"ok": True})


# ── Generic Tool Integrations (N connectors) ─────────────────────────────────

@app.route('/integrations', methods=['GET'])
@admin_required
def list_integrations():
    creds = _load_json(_creds_path(), {}) or {}
    items = creds.get("integrations", [])
    safe = []
    for item in items:
        x = dict(item)
        if x.get("secret"):
            x["secret"] = "••••" + str(x["secret"])[-4:]
        if x.get("api_key"):
            x["api_key"] = "••••" + str(x["api_key"])[-4:]
        safe.append(x)
    return jsonify(safe)

@app.route('/integrations', methods=['POST'])
@admin_required
def upsert_integration():
    body = request.json or {}
    if not body.get("id"):
        return jsonify({"error": "id is required"}), 400
    if not body.get("name"):
        return jsonify({"error": "name is required"}), 400

    creds = _load_json(_creds_path(), {}) or {}
    items = creds.get("integrations", [])
    items = [x for x in items if x.get("id") != body["id"]]
    items.append(body)
    creds["integrations"] = items
    _save_json(_creds_path(), creds)
    return jsonify({"ok": True, "id": body["id"]})

@app.route('/integrations/<integration_id>', methods=['DELETE'])
@admin_required
def delete_integration(integration_id):
    creds = _load_json(_creds_path(), {}) or {}
    items = creds.get("integrations", [])
    creds["integrations"] = [x for x in items if x.get("id") != integration_id]
    _save_json(_creds_path(), creds)
    return jsonify({"ok": True})

@app.route('/integrations/<integration_id>/test', methods=['POST'])
@admin_required
def test_integration(integration_id):
    creds = _load_json(_creds_path(), {}) or {}
    items = creds.get("integrations", [])
    hit = next((x for x in items if x.get("id") == integration_id), None)
    if not hit:
        return jsonify({"error": f"Integration '{integration_id}' not found"}), 404

    endpoint = str(hit.get("endpoint", "")).strip()
    if not endpoint:
        return jsonify({"error": "Integration endpoint is empty"}), 400

    headers = {"User-Agent": "PrismaraAI/1.0"}
    if hit.get("auth_type") == "bearer" and hit.get("secret"):
        headers["Authorization"] = f"Bearer {hit['secret']}"
    if hit.get("auth_type") == "api_key" and hit.get("api_key"):
        headers["X-API-Key"] = str(hit["api_key"])

    try:
        req = urllib.request.Request(endpoint, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return jsonify({"ok": True, "status": resp.status})
    except urllib.error.HTTPError as e:
        return jsonify({"ok": False, "status": e.code, "error": str(e)}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200

@app.route('/settings/approve-transfer', methods=['POST'])
@admin_required
def approve_transfer():
    body = request.json or {}
    try:
        minutes = int(body.get("minutes", 1440))
    except Exception:
        minutes = 1440
    if minutes < 1:
        minutes = 1
    if minutes > 1440:
        minutes = 1440

    cfg_path = _data_dir() / "config.json"
    cfg = _load_json(cfg_path, {})
    cfg["transfer_approved"] = True
    cfg["transfer_unlock_until"] = int(time.time()) + (minutes * 60)
    _save_json(cfg_path, cfg)
    return jsonify({"ok": True, "minutes": minutes, "transfer_unlock_until": cfg["transfer_unlock_until"]})


# ── Local AI (Ollama + custom OpenAI-compatible endpoints) ──────────────────

@app.route('/admin/local-ai/status', methods=['GET'])
@admin_required
def local_ai_status():
    try:
        from src.local_ai import status
        return jsonify(status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/admin/local-ai/install', methods=['POST'])
@admin_required
def local_ai_install():
    try:
        from src.local_ai import install_ollama_stream
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    @stream_with_context
    def generate():
        for event in install_ollama_stream():
            yield json.dumps(event, ensure_ascii=True) + "\n"

    return Response(generate(), mimetype='application/x-ndjson')


@app.route('/admin/local-ai/pull-model', methods=['POST'])
@admin_required
def local_ai_pull_model():
    body = request.json or {}
    name = str(body.get("name", "")).strip()
    if not name:
        return jsonify({"error": "model name is required"}), 400
    try:
        from src.local_ai import pull_model_stream
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    @stream_with_context
    def generate():
        for event in pull_model_stream(name):
            yield json.dumps(event, ensure_ascii=True) + "\n"

    return Response(generate(), mimetype='application/x-ndjson')


@app.route('/admin/local-ai/update-models', methods=['POST'])
@admin_required
def local_ai_update_models():
    body = request.json or {}
    names = body.get("names")
    if names is not None and not isinstance(names, list):
        return jsonify({"error": "names must be a list when provided"}), 400
    try:
        from src.local_ai import update_installed_models_stream
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    @stream_with_context
    def generate():
        for event in update_installed_models_stream(names):
            yield json.dumps(event, ensure_ascii=True) + "\n"

    return Response(generate(), mimetype='application/x-ndjson')


@app.route('/admin/local-ai/model/<path:name>', methods=['DELETE'])
@admin_required
def local_ai_delete_model(name):
    try:
        from src.local_ai import delete_model
        ok, msg = delete_model(name)
        if ok:
            return jsonify({"ok": True, "message": msg})
        return jsonify({"ok": False, "error": msg}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/admin/local-ai/detect-custom', methods=['POST'])
@admin_required
def local_ai_detect_custom():
    try:
        from src.local_ai import probe_custom_endpoints
        return jsonify({"endpoints": probe_custom_endpoints()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Diagnostics: traces + system metrics ─────────────────────────────────────

@app.route('/admin/traces', methods=['GET'])
@admin_required
def list_traces():
    """Return summaries of recent traces, newest first."""
    try:
        from src.orchestrator import _traces_dir
        d = _traces_dir()
        files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:200]
        out = []
        for f in files:
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append({
                "request_id": t.get("request_id"),
                "started_at": t.get("started_at"),
                "completed_at": t.get("completed_at"),
                "duration_ms": t.get("duration_ms"),
                "status": t.get("status"),
                "prompt_preview": (t.get("prompt") or "")[:200],
                "intents": t.get("intents", []),
                "models_used": t.get("models_used", []),
                "stage_count": len(t.get("stages", [])),
                "online_pipeline_mode": t.get("online_pipeline_mode", False),
                "online_consent": t.get("online_consent", False),
                "disk_bytes_used": t.get("disk_bytes_used", 0),
                "has_error": bool(t.get("error")),
            })
        return jsonify({"traces": out, "trace_dir": str(d)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/admin/traces/<request_id>', methods=['GET'])
@admin_required
def get_trace(request_id):
    try:
        from src.orchestrator import _traces_dir
        safe_id = "".join(c for c in request_id if c.isalnum() or c in "-_")
        if safe_id != request_id:
            return jsonify({"error": "invalid id"}), 400
        path = _traces_dir() / f"{safe_id}.json"
        if not path.exists():
            return jsonify({"error": "trace not found"}), 404
        return Response(path.read_text(encoding="utf-8"), mimetype="application/json")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/admin/system-metrics', methods=['GET'])
@admin_required
def system_metrics():
    """One-shot system snapshot used by the Diagnostics modal."""
    import ctypes
    metrics = {}
    # RAM via Windows kernel32 (no third-party dep)
    try:
        if os.name == "nt":
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
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            metrics["ram"] = {
                "total_gb": round(stat.ullTotalPhys / (1024**3), 2),
                "free_gb":  round(stat.ullAvailPhys / (1024**3), 2),
                "used_pct": int(stat.dwMemoryLoad),
            }
    except Exception as e:
        metrics["ram_error"] = str(e)

    # Disk by volume of interest
    try:
        import shutil as _shutil
        from src.local_ai import ollama_data_dir as _odd
        from src.orchestrator import _traces_dir as _td
        cache_dir = os.environ.get("MLMAE_CACHE_DIR", str(_data_dir() / "cache"))
        for label, path_str in (
            ("ollama_models", _odd()),
            ("traces", str(_td())),
            ("cache", cache_dir),
        ):
            try:
                u = _shutil.disk_usage(path_str)
                metrics.setdefault("disks", {})[label] = {
                    "path": str(path_str),
                    "free_gb":  round(u.free / (1024**3), 2),
                    "total_gb": round(u.total / (1024**3), 2),
                }
            except Exception:
                pass
    except Exception as e:
        metrics["disk_error"] = str(e)

    # Ollama daemon + loaded models
    try:
        from src.local_ai import detect_ollama_daemon
        d = detect_ollama_daemon(timeout=1.0)
        metrics["ollama"] = {
            "running": d.get("running"),
            "model_count": d.get("model_count"),
        }
        try:
            import urllib.request as _urlreq
            with _urlreq.urlopen("http://127.0.0.1:11434/api/ps", timeout=1.0) as r:
                ps = json.loads(r.read())
            metrics["ollama"]["loaded"] = [
                {"name": m.get("name"), "vram_bytes": m.get("size_vram", 0)}
                for m in (ps.get("models") or [])
            ]
        except Exception:
            metrics["ollama"]["loaded"] = []
    except Exception as e:
        metrics["ollama_error"] = str(e)

    return jsonify(metrics)


if __name__ == '__main__':
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("MLMAE_DEV_SERVER", "").strip().lower() in ("1", "true", "yes", "on")
    if debug:
        extra_files = [
            str(path)
            for folder in ("src", "server")
            for path in (PROJECT_ROOT / folder).glob("*.py")
        ]
        app.run(host="127.0.0.1", port=port, debug=True, extra_files=extra_files)
    else:
        try:
            from waitress import serve

            serve(app, host="127.0.0.1", port=port, threads=8)
        except Exception:
            app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
