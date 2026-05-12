# -*- mode: python ; coding: utf-8 -*-
# prismara.spec - PyInstaller build spec for PrismaraAI.exe
#
# Run with:  pyinstaller prismara.spec
# Or use:    build.bat  (which also runs `npm run build` first)

import sys
from pathlib import Path

ROOT = Path(SPECPATH)   # project root (where this .spec lives)

# Data files bundled into the exe
# Format: (source, dest_inside_bundle)
# dest paths must match what the code references via sys._MEIPASS / _bundle_dir()

datas = [
    # Pre-built React app (must run `npm run build` in frontend/ first)
    (str(ROOT / "frontend" / "dist"),   "frontend/dist"),

    # Python source modules (needed so `from src.xxx import` works when frozen)
    (str(ROOT / "src"),                 "src"),

    # Server module (app_standalone.py + helpers)
    (str(ROOT / "server"),              "server"),

    # Top-level orchestration
    (str(ROOT / "main.py"),             "."),
]

# Optional offline Ollama installer. Keep it out of source unless its
# redistribution terms are reviewed; if vendor/OllamaSetup.exe exists on the
# build machine, the exe can install the managed runtime without downloading it.
OLLAMA_INSTALLER = ROOT / "vendor" / "OllamaSetup.exe"
if OLLAMA_INSTALLER.exists():
    datas.append((str(OLLAMA_INSTALLER), "runtime"))

# Hidden imports
# PyInstaller static analysis misses dynamic imports.  List them all explicitly.

hidden_imports = [
    # Flask & WSGI
    "flask",
    "flask.templating",
    "flask_cors",
    "werkzeug",
    "werkzeug.serving",
    "werkzeug.routing",
    "werkzeug.exceptions",
    "waitress",
    "waitress.server",
    "jinja2",
    "jinja2.ext",
    "click",

    # OpenAI (also used for DeepSeek / custom endpoints)
    "openai",
    "openai.types",
    "openai.types.chat",
    "openai._models",
    "openai._streaming",
    "openai._client",
    "httpx",
    "httpcore",
    "anyio",
    "anyio._backends._asyncio",
    "anyio._backends._trio",
    "sniffio",
    "h11",
    "websockets",
    "websockets.client",
    "websockets.sync",
    "websockets.sync.client",
    "websockets.asyncio",
    "websockets.asyncio.client",
    "websockets.exceptions",
    "websockets.extensions",

    # Anthropic
    "anthropic",
    "anthropic.types",
    "anthropic._streaming",

    # Google AI (current SDK is google-genai; google.generativeai is deprecated)
    "google.genai",
    "google.api_core",
    "google.auth",
    "google.auth.transport.requests",
    "google.protobuf",
    "grpc",

    # Groq
    "groq",
    "groq.types",

    # Cohere
    "cohere",
    "cohere.types",
    "fastavro",

    # Utilities
    "dotenv",
    "requests",
    "requests.adapters",
    "urllib3",
    "certifi",
    "charset_normalizer",
    "idna",

    # Standard library extras PyInstaller sometimes misses
    "uuid",
    "hashlib",
    "threading",
    "socket",
    "json",
    "logging",
    "logging.handlers",
    "pathlib",
    "re",
    "base64",
    "urllib.request",
    "urllib.parse",
    "urllib.error",

    # Prismara AI own modules - must be listed so PyInstaller walks them
    "src.llm_client",
    "src.data_guard",
    "src.sso_auth",
    "src.memory_core",
    "src.orchestrator",
    "src.machine_identity",
    "src.secure_storage",
    "src.chat_history",
    "src.election_engine",      # deprecated shim, kept for legacy imports
    "src.supervisor_agent",     # deprecated shim, kept for legacy imports
    "server.app_standalone",
    "main",
]

# Analysis

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Cut exe size — not needed at runtime
        "matplotlib", "numpy", "pandas", "scipy", "PIL", "cv2",
        "pytest", "setuptools", "pip", "IPython", "jupyter",
        "notebook", "nbformat", "nbconvert",
        # Belt-and-suspenders: the new orchestrator does not use these and
        # they're no longer in requirements.txt; this exclusion stops them
        # being pulled in transitively if they happen to be in site-packages.
        "torch", "torch.distributed", "torchvision", "torchaudio",
        "tensorflow", "tensorflow.compat", "keras", "jax", "flax",
        "langchain", "langchain_core", "langchain_openai",
        "langchain_anthropic", "langchain_google_genai", "langchain_community",
        "langgraph", "langgraph.graph",
        "google.generativeai", "google.ai.generativelanguage_v1beta",
        "transformers", "datasets",
        "sentence_transformers", "sklearn", "scikit-learn",
        "lxml",
        # The launcher treats tkinter as optional. Excluding it avoids a
        # PyInstaller Tk runtime hook crash on machines with broken Tcl/Tk.
        "tkinter", "tkinter.ttk", "tkinter.messagebox", "_tkinter",
    ],
    noarchive=False,
    optimize=1,   # strip docstrings to reduce size
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PrismaraAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # avoid packed-binary AV false positives
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # no console window for normal click-to-run UX
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(ROOT / "version_info.txt"),
    # icon="assets/icon.ico",   # uncomment and add icon file if desired
)
