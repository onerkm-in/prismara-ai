# Prismara AI

Prismara AI is a local-first multi-model agent workspace. The packaged Windows app is intended to run as a portable `PrismaraAI.exe`: users place the exe in a folder, launch it, and the app creates a sibling `prismara/` runtime folder for encrypted local state, chat history, logs, backups, credentials, and local model storage.

## Portable Runtime

- Distribute only `release/PrismaraAI.exe`.
- Distribute `release/PrismaraAI.exe.sha256` with it so users can verify the download.
- On first run, the exe creates `prismara/` beside itself.
- End users do not need Python, Node, npm, or PyInstaller.
- The `prismara/` folder is machine-bound and encrypted at rest. It is not intended to be copied directly.
- Transfers must be approved from Settings within the configured transfer window.

## Local AI Behavior

Prismara AI detects local hardware and recommends models based on RAM, disk, GPU VRAM, and local runtime policy. GPU use requires explicit admin consent. Low-VRAM GPUs are ignored entirely for local inference and Prismara AI forces Ollama requests to CPU mode (`num_gpu=0`). The default useful-GPU minimum is 6 GB VRAM, configurable with `MLMAE_MIN_USEFUL_GPU_VRAM_GB`.

The app includes a first-run System Doctor that reports CPU, RAM, GPU, disk, Ollama/runtime status, recommended model packs, and expected speed. The top-level recommended-pack action can install the managed runtime or pull the starter model pack once the runtime is available.

The executable bundles the app runtime. End users do not need Python, Node, npm, or PyInstaller. True offline local inference still needs an Ollama-compatible model engine. Prismara AI can use an offline bundled/adjacent `OllamaSetup.exe` when present, otherwise it downloads the installer once into `prismara/cache/ollama-installer/` and stores model weights under the exe-adjacent `prismara/ollama/models` folder.

## Trust Model

- Local by default: chat history, credentials, workspace memory, logs, backups, and local model weights stay under `prismara/`.
- Encrypted at rest: sensitive Prismara AI state uses machine-bound AES-GCM storage.
- Online consent: online providers are used only when explicitly enabled for a request or configured in admin integrations.
- Transfer mode: a time-limited transfer window can rebind encrypted Prismara AI state on another machine, but it cannot prevent OS-level copying by a device administrator.

## Antivirus And Release Trust

Prismara AI is packaged as a Windows PyInstaller executable, which means it contains an embedded Python runtime. Some antivirus products treat unsigned one-file Python apps as suspicious, especially if the binary is packed or has no publisher reputation. The release build disables UPX packing, includes Windows file metadata, and writes a SHA-256 hash beside the exe.

For public releases, sign `release/PrismaraAI.exe` with a code-signing certificate and publish the matching SHA-256 hash in the GitHub release notes. If a vendor still flags the file, submit the signed exe and source repository link as a false-positive report.

## Build

Build-machine prerequisites are Python, Node.js, and npm. End users do not need these.

```bat
build.bat
```

The build script installs dependencies, builds the React frontend, runs PyInstaller, and copies the clean distributable to:

```text
release\PrismaraAI.exe
```

`dist/` and any `prismara/` or legacy `mlmae/` folders are build/runtime artifacts and must not be published as source.

## Source Layout

- `launcher.py` - portable exe launcher and local server bootstrap.
- `server/app_standalone.py` - packaged Flask API and static frontend host.
- `src/orchestrator.py` - multi-stage agent pipeline and deterministic fast paths.
- `src/local_ai.py` - Ollama, hardware detection, model recommendation, and update flows.
- `src/secure_storage.py` - machine-bound encrypted JSON storage.
- `frontend/src/` - React UI.

## License

Copyright 2026 Rajesh Kumar Mohanty.

Apache License 2.0. See `LICENSE`.
