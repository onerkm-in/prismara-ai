"""Prismara AI launcher entry point."""

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from src.machine_identity import get_machine_fingerprint, get_machine_fingerprint_candidates
from src.secure_storage import load_json, save_json

# ── Path Resolution ────────────────────────────────────────────────────────────

IS_FROZEN = getattr(sys, "frozen", False)


def get_exe_dir() -> Path:
    """Directory containing the .exe (or the script during development)."""
    if IS_FROZEN:
        return Path(sys.executable).parent
    return Path(__file__).parent


def get_bundle_dir() -> Path:
    """PyInstaller temp-unpacked dir, or project root during development."""
    if IS_FROZEN:
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).parent


EXE_DIR = get_exe_dir()
BUNDLE_DIR = get_bundle_dir()

# ── Single Folder Layout ──────────────────────────────────────────────────────

DATA_DIR         = EXE_DIR / "prismara"
MACHINE_ID_FILE  = DATA_DIR / ".machine_id"
CONFIG_FILE      = DATA_DIR / "config.json"
CREDENTIALS_FILE = DATA_DIR / "credentials.json"
CACHE_DIR        = DATA_DIR / "cache"
LOGS_DIR         = DATA_DIR / "logs"
BACKUPS_DIR      = DATA_DIR / "backups"
CUSTOM_MODELS_FILE = DATA_DIR / "custom_models.json"
SSO_TOKENS_FILE    = CACHE_DIR / "sso_tokens.json"
OLLAMA_MODELS_DIR  = DATA_DIR / "ollama" / "models"
CHAT_HISTORY_FILE  = DATA_DIR / "chat_history.json"

PORT = 7432  # Fixed local port — unlikely to conflict
MAX_TRANSFER_WINDOW_MINUTES = 1440

# ── Logging Setup ─────────────────────────────────────────────────────────────

def _setup_logging():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / "prismara.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )

# ── Machine Fingerprint ───────────────────────────────────────────────────────

def _get_machine_fingerprint() -> str:
    return get_machine_fingerprint()


def _with_storage_fingerprint(fingerprint: str):
    """Temporarily read machine-bound files using a stored fingerprint."""
    class _Override:
        def __enter__(self):
            self.previous = os.environ.get("MLMAE_STORAGE_KEY_FINGERPRINT_OVERRIDE")
            os.environ["MLMAE_STORAGE_KEY_FINGERPRINT_OVERRIDE"] = fingerprint

        def __exit__(self, exc_type, exc, tb):
            if self.previous is None:
                os.environ.pop("MLMAE_STORAGE_KEY_FINGERPRINT_OVERRIDE", None)
            else:
                os.environ["MLMAE_STORAGE_KEY_FINGERPRINT_OVERRIDE"] = self.previous
            return False

    return _Override()


def _load_transfer_payloads(stored_fp: str) -> dict[Path, object]:
    """Read live machine-bound files with the old key before rebinding."""
    payloads: dict[Path, object] = {}
    for path in (CONFIG_FILE, CREDENTIALS_FILE, DATA_DIR / "neural_memory.json", CUSTOM_MODELS_FILE, SSO_TOKENS_FILE, CHAT_HISTORY_FILE):
        if not path.exists():
            continue
        payloads[path] = load_json(path, default=None, strict=True)
    return payloads


def _write_rebound_payloads(payloads: dict[Path, object], current_fp: str, cfg: dict) -> None:
    MACHINE_ID_FILE.write_text(current_fp, encoding="utf-8")
    for path, payload in payloads.items():
        save_json(path, cfg if path == CONFIG_FILE else payload, encode_at_rest=True)

    if CONFIG_FILE not in payloads:
        save_json(CONFIG_FILE, cfg, encode_at_rest=True)


def _rebind_same_machine_data_folder(stored_fp: str, current_fp: str) -> bool:
    """Rebind data after a local fingerprint algorithm migration/drift."""
    with _with_storage_fingerprint(stored_fp):
        cfg = load_json(CONFIG_FILE, default=None, strict=True)
        if not isinstance(cfg, dict):
            logging.warning("Fingerprint migration skipped — config did not decode to an object.")
            return False
        payloads = _load_transfer_payloads(stored_fp)

    cfg = dict(payloads.get(CONFIG_FILE, cfg))
    cfg["last_fingerprint_migrated_at"] = int(time.time())
    _write_rebound_payloads(payloads, current_fp, cfg)
    logging.info("Same-machine fingerprint drift detected — data folder rebound.")
    return True


def _rebind_data_folder(stored_fp: str, current_fp: str) -> bool:
    """Accept an approved transfer and re-encrypt live data for this machine."""
    with _with_storage_fingerprint(stored_fp):
        cfg = load_json(CONFIG_FILE, default=None, strict=True)
        if not isinstance(cfg, dict):
            logging.warning("Transfer denied — config did not decode to an object.")
            return False

        now = int(time.time())
        unlock_until = int(cfg.get("transfer_unlock_until", 0) or 0)
        approved = bool(cfg.get("transfer_approved", False))
        if not approved or unlock_until <= now:
            logging.warning("Transfer denied — approval missing or window expired.")
            return False

        payloads = _load_transfer_payloads(stored_fp)

    cfg = dict(payloads.get(CONFIG_FILE, cfg))
    cfg["transfer_approved"] = False
    cfg["transfer_unlock_until"] = 0
    cfg["last_transfer_accepted_at"] = int(time.time())

    _write_rebound_payloads(payloads, current_fp, cfg)

    logging.info("Transfer approved within window — data folder rebound to this machine.")
    return True


def _init_data_dir() -> bool:
    """
    Creates the prismara folder structure on first run.
    Returns True  → machine check passed (or first run).
    Returns False → different machine detected, no valid transfer window.
    """
    for d in [DATA_DIR, CACHE_DIR, LOGS_DIR, BACKUPS_DIR, OLLAMA_MODELS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    current_fp = _get_machine_fingerprint()

    # ── First run ──────────────────────────────────────────────────────────
    if not MACHINE_ID_FILE.exists():
        MACHINE_ID_FILE.write_text(current_fp, encoding="utf-8")

        if not CONFIG_FILE.exists():
            save_json(
                CONFIG_FILE,
                {
                    "version": "1.0.0",
                    "port": PORT,
                    "first_run": True,
                    "transfer_approved": False,
                    "transfer_unlock_until": 0,
                    "storage_folder": "prismara",
                    "encode_at_rest": True,
                    "data_guard_mode": "auto",
                    "hardware_acceleration_consent": False,
                    "hardware_acceleration_consent_at": 0,
                },
                encode_at_rest=True,
            )

        if not CREDENTIALS_FILE.exists():
            save_json(
                CREDENTIALS_FILE,
                {
                    "OPENAI_API_KEY": "",
                    "OPENAI_ORG_ID": "",
                    "OPENAI_PROJECT_ID": "",
                    "ANTHROPIC_API_KEY": "",
                    "ANTHROPIC_SSO_PROFILE_ID": "",
                    "GOOGLE_API_KEY": "",
                    "GOOGLE_SSO_PROFILE_ID": "",
                    "GOOGLE_CLOUD_PROJECT_ID": "",
                    "OPENROUTER_API_KEY": "",
                    "GROQ_API_KEY": "",
                    "DEEPSEEK_API_KEY": "",
                    "COHERE_API_KEY": "",
                    "SARVAM_API_KEY": "",
                    "sso_profiles": [],
                },
                encode_at_rest=True,
            )

        if not CHAT_HISTORY_FILE.exists():
            save_json(
                CHAT_HISTORY_FILE,
                {
                    "version": 1,
                    "updated_at": int(time.time()),
                    "limit": 200,
                    "messages": [],
                },
                encode_at_rest=True,
            )

        logging.info("First run - prismara folder initialized at %s", DATA_DIR)
        return True

    # ── Subsequent run: verify fingerprint ────────────────────────────────
    stored_fp = MACHINE_ID_FILE.read_text(encoding="utf-8").strip()

    if stored_fp == current_fp:
        return True

    try:
        if stored_fp in set(get_machine_fingerprint_candidates()):
            if _rebind_same_machine_data_folder(stored_fp, current_fp):
                return True
    except Exception as e:
        logging.warning("Same-machine fingerprint migration failed: %s", e)

    # ── Fingerprint mismatch: check for approved transfer window ──────────
    try:
        if _rebind_data_folder(stored_fp, current_fp):
            return True
    except Exception as e:
        logging.warning("Transfer check failed: %s", e)

    logging.warning("Machine fingerprint mismatch — no valid transfer window.")
    return False


# ── Chrome Detection ──────────────────────────────────────────────────────────

def _find_chrome() -> Optional[str]:
    """Return path to Chrome executable, or None if not found."""
    candidates: list[str] = []

    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        ]
    else:
        for name in ("google-chrome", "chromium-browser", "chromium"):
            found = shutil.which(name)
            if found:
                candidates.append(found)

    for path in candidates:
        if path and os.path.isfile(path):
            return path

    return shutil.which("chrome") or shutil.which("google-chrome")


# ── Browser Launch Dialog ─────────────────────────────────────────────────────

def _show_browser_dialog(machine_ok: bool) -> dict:
    """
    Show a tkinter dialog so the user can choose:
      - Chrome Incognito / Normal
      - Default Browser Private / Normal
    Returns {'browser': 'chrome'|'default', 'incognito': bool, 'cancelled': bool}
    """
    try:
        import tkinter as tk
    except Exception as exc:
        logging.warning("tkinter not available — falling back to default browser: %s", exc)
        return {"browser": "default", "incognito": False, "cancelled": False}

    result = {"browser": "default", "incognito": False, "cancelled": False}
    chrome_path = _find_chrome()

    try:
        root = tk.Tk()
    except Exception as exc:
        logging.warning("Browser dialog unavailable — falling back to default browser: %s", exc)
        return {"browser": "default", "incognito": False, "cancelled": False}
    root.title("Prismara AI")
    root.resizable(False, False)
    root.configure(bg="#0f172a")

    # Window sizing and centering
    h = 380 if (not machine_ok) else 300
    w = 460
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    # ── Title ──────────────────────────────────────────────────────────────
    tk.Label(
        root, text="Prismara AI",
        font=("Segoe UI", 20, "bold"), bg="#0f172a", fg="#60a5fa"
    ).pack(pady=(22, 2))
    tk.Label(
        root, text="Private multi-model agent workspace",
        font=("Segoe UI", 10), bg="#0f172a", fg="#475569"
    ).pack()

    # ── Machine warning ────────────────────────────────────────────────────
    if not machine_ok:
        frm = tk.Frame(root, bg="#431407", bd=0)
        frm.pack(padx=22, fill="x", pady=(14, 4))
        tk.Label(
            frm,
            text="⚠  Different machine detected. This data folder is locked.",
            font=("Segoe UI", 9, "bold"), bg="#431407", fg="#fdba74",
            wraplength=400, justify="left",
        ).pack(padx=12, pady=(8, 2))
        tk.Label(
            frm,
            text="This folder cannot open here. On the original machine, open Settings → Approve Transfer, choose a window, then copy the folder and run again before it expires.",
            font=("Segoe UI", 8), bg="#431407", fg="#fbbf24",
            wraplength=400, justify="left",
        ).pack(padx=12, pady=(0, 8))

        tk.Button(
            root, text="Close",
            bg="#1e293b", fg="#e2e8f0", activebackground="#334155",
            activeforeground="white",
            command=lambda: (result.update({"cancelled": True}), root.destroy()),
            font=("Segoe UI", 10, "bold"), relief="flat",
            cursor="hand2", padx=14, pady=10, bd=0,
        ).pack(padx=22, fill="x", pady=(18, 0))
        root.mainloop()
        return result

    tk.Label(
        root, text="How would you like to open the app?",
        font=("Segoe UI", 11), bg="#0f172a", fg="#e2e8f0"
    ).pack(pady=(16, 10))

    # ── Buttons ────────────────────────────────────────────────────────────
    btn_frame = tk.Frame(root, bg="#0f172a")
    btn_frame.pack(padx=22, fill="x")

    btn_cfg = dict(
        font=("Segoe UI", 10, "bold"), relief="flat",
        cursor="hand2", padx=14, pady=10, bd=0,
    )

    def launch(browser: str, incognito: bool):
        result["browser"] = browser
        result["incognito"] = incognito
        root.destroy()

    if chrome_path:
        tk.Button(
            btn_frame, text="🔒  Chrome — Incognito",
            bg="#1e3a5f", fg="#93c5fd", activebackground="#1e40af",
            activeforeground="white",
            command=lambda: launch("chrome", True), **btn_cfg,
        ).pack(fill="x", pady=3)
        tk.Button(
            btn_frame, text="🌐  Chrome — Normal",
            bg="#1e293b", fg="#94a3b8", activebackground="#334155",
            activeforeground="white",
            command=lambda: launch("chrome", False), **btn_cfg,
        ).pack(fill="x", pady=3)
        tk.Label(
            btn_frame, text="─── or use your default browser ───",
            font=("Segoe UI", 8), bg="#0f172a", fg="#334155"
        ).pack(pady=(6, 0))

    tk.Button(
        btn_frame, text="🔒  Default Browser — Private / Incognito",
        bg="#1e3a5f", fg="#93c5fd", activebackground="#1e40af",
        activeforeground="white",
        command=lambda: launch("default", True), **btn_cfg,
    ).pack(fill="x", pady=3)
    tk.Button(
        btn_frame, text="🌐  Default Browser — Normal",
        bg="#1e293b", fg="#94a3b8", activebackground="#334155",
        activeforeground="white",
        command=lambda: launch("default", False), **btn_cfg,
    ).pack(fill="x", pady=3)

    if not chrome_path:
        tk.Label(
            btn_frame, text="Google Chrome not detected on this machine.",
            font=("Segoe UI", 8, "italic"), bg="#0f172a", fg="#475569"
        ).pack(pady=(4, 0))

    tk.Button(
        btn_frame, text="✕  Cancel",
        bg="#0f172a", fg="#475569", activebackground="#1e293b",
        activeforeground="#64748b",
        command=lambda: (result.update({"cancelled": True}), root.destroy()),
        **btn_cfg,
    ).pack(fill="x", pady=(10, 0))

    root.mainloop()
    return result


# ── Browser Opener ────────────────────────────────────────────────────────────

def _open_browser(url: str, browser: str, incognito: bool):
    chrome_path = _find_chrome()

    if browser == "chrome" and chrome_path:
        args = [chrome_path]
        if incognito:
            args.append("--incognito")
        args.append(url)
        subprocess.Popen(args)
        logging.info("Opened Chrome (%s): %s", "incognito" if incognito else "normal", url)
        return

    # Default browser path
    if incognito:
        _open_default_private(url)
    else:
        webbrowser.open(url)
        logging.info("Opened default browser (normal): %s", url)


def _open_default_private(url: str):
    """Best-effort: open the default browser in private mode."""
    if sys.platform == "win32":
        # Try Edge (common Windows default)
        edge_paths = [
            shutil.which("msedge"),
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        for ep in edge_paths:
            if ep and os.path.isfile(ep):
                subprocess.Popen([ep, "--inprivate", url])
                logging.info("Opened Edge (InPrivate): %s", url)
                return

        # Try Firefox
        ff = shutil.which("firefox")
        if ff:
            subprocess.Popen([ff, "--private-window", url])
            logging.info("Opened Firefox (private): %s", url)
            return

    elif sys.platform == "darwin":
        # Try Safari private (AppleScript) or Firefox
        ff = shutil.which("firefox")
        if ff:
            subprocess.Popen([ff, "--private-window", url])
            return

    # Final fallback — open normally
    logging.warning("Could not open private browser; opening normally.")
    webbrowser.open(url)


def _disable_low_config_gpu_for_children():
    """Keep child runtimes on CPU when the detected NVIDIA GPU is too small."""
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return
        vram_gb = []
        for line in proc.stdout.splitlines():
            try:
                vram_gb.append(float(line.strip()) / 1024.0)
            except Exception:
                continue
        min_vram = float(os.environ.get("MLMAE_MIN_USEFUL_GPU_VRAM_GB", "6.0"))
        if vram_gb and max(vram_gb) < min_vram:
            os.environ["MLMAE_FORCE_OLLAMA_CPU"] = "1"
            os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
            os.environ.setdefault("HIP_VISIBLE_DEVICES", "-1")
            os.environ.setdefault("ROCR_VISIBLE_DEVICES", "-1")
            logging.info("Low-VRAM GPU detected; forcing Prismara AI child runtimes to CPU.")
    except Exception as e:
        logging.debug("GPU CPU-only probe skipped: %s", e)


# ── Server Startup ────────────────────────────────────────────────────────────

def _configure_environment():
    """Set environment variables so all modules use the single prismara folder."""
    os.environ["MLMAE_DATA_DIR"]         = str(DATA_DIR)
    os.environ["MLMAE_CACHE_DIR"]        = str(CACHE_DIR)
    os.environ["MLMAE_CREDENTIALS_FILE"] = str(CREDENTIALS_FILE)
    os.environ["MLMAE_LOGS_DIR"]         = str(LOGS_DIR)
    os.environ["MLMAE_BACKUP_DIR"]       = str(BACKUPS_DIR)
    os.environ.setdefault("OLLAMA_MODELS", str(OLLAMA_MODELS_DIR))
    os.environ.setdefault("MLMAE_DATA_GUARD_MODE", "auto")
    os.environ.setdefault("MLMAE_MIN_USEFUL_GPU_VRAM_GB", "6.0")
    _disable_low_config_gpu_for_children()

    # Inject saved credentials into the process environment
    try:
        creds = load_json(CREDENTIALS_FILE, default={}) or {}
        for key, value in creds.items():
            if not isinstance(key, str) or not key.isupper():
                continue
            if isinstance(value, (dict, list, tuple, set)) or value is None:
                continue
            text = str(value).strip()
            if text and not os.environ.get(key):
                os.environ[key] = text
                logging.info("Loaded credential: %s", key)
    except Exception as e:
        logging.warning("Could not load credentials: %s", e)


def _start_server():
    """Start the standalone Flask app (serves static + API)."""
    sys.path.insert(0, str(BUNDLE_DIR))
    _configure_environment()

    from server.app_standalone import create_app  # type: ignore

    app = create_app()
    logging.info("Starting Prismara AI server on http://127.0.0.1:%d", PORT)
    try:
        from waitress import serve

        logging.info("Serving Prismara AI with Waitress.")
        serve(app, host="127.0.0.1", port=PORT, threads=8)
    except Exception as e:
        logging.warning("Waitress unavailable; falling back to Flask server: %s", e)
        app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)


def _wait_for_server(timeout: int = 20) -> bool:
    """Poll until Flask responds on /health."""
    import urllib.request
    url = f"http://127.0.0.1:{PORT}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    _setup_logging()
    logging.info("Prismara AI launcher starting - EXE_DIR=%s", EXE_DIR)

    machine_ok = _init_data_dir()

    dialog = _show_browser_dialog(machine_ok)
    if dialog.get("cancelled"):
        logging.info("User cancelled launch dialog — exiting.")
        sys.exit(0)
    if not machine_ok:
        logging.warning("Data folder locked — server will not start.")
        sys.exit(1)

    # Start Flask in a daemon thread
    server_thread = threading.Thread(target=_start_server, daemon=True, name="FlaskServer")
    server_thread.start()

    url = f"http://127.0.0.1:{PORT}"
    if _wait_for_server():
        logging.info("Server ready — opening browser.")
        _open_browser(url, dialog["browser"], dialog["incognito"])
    else:
        logging.error("Server did not start in time.")
        try:
            import tkinter.messagebox as mb
            mb.showerror(
                "Prismara AI - Startup Error",
                f"The local server failed to start.\n\nCheck logs at:\n{LOGS_DIR / 'prismara.log'}",
            )
        except Exception:
            pass
        sys.exit(1)

    # Keep process alive while the Flask daemon thread runs
    server_thread.join()


if __name__ == "__main__":
    main()
