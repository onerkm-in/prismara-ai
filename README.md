# Prismara AI

![Prismara AI logo](frontend/public/prismara-logo.svg)

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Status: early alpha](https://img.shields.io/badge/status-early%20alpha-f59e0b.svg)](#project-status)
[![Local-first](https://img.shields.io/badge/local--first-privacy%20by%20default-14b8a6.svg)](#trust-model)

Prismara AI is a local-first multi-model agent workspace for people who want AI help without giving up control of their data.

It is being built as both:

- A portable desktop app for private local use.
- A future hosted web option for teams, remote access, and managed deployment.

## Why Prismara AI?

- Local-first by default: state, credentials, memory, logs, and local models stay on the user's machine.
- Multi-model orchestration: Prismara AI routes work through a role-based agent pipeline instead of depending on one model.
- Explicit online consent: cloud providers are used only when enabled for a request or configured in admin integrations.
- Portable Windows packaging: the end-user app is intended to run as a self-contained `PrismaraAI.exe`.
- Open roadmap: desktop and web deployment paths are both planned.

## Project Status

Prismara AI is in early public alpha. The desktop-first local runtime is the current focus. Hosted web deployment, pricing, and platform options are being evaluated next.

Helpful early contributions include setup testing, Windows packaging feedback, Ollama/runtime detection improvements, UI polish, documentation, and hosting architecture ideas.

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

## Quick Start For Contributors

Clone the repo, install dependencies, then build the frontend:

```bat
python -m pip install -r requirements.txt
cd frontend
npm install
npm run build
```

For full packaging on Windows:

```bat
build.bat
```

## Contributing

Contributions are welcome through issues and pull requests. Please read `CONTRIBUTING.md`, use the GitHub issue templates, and keep secrets, runtime data, logs, model weights, and local state out of commits.

Good first places to help:

- Try the app on a Windows machine and report setup friction.
- Test local model recommendations with different RAM/GPU configurations.
- Improve copy, screenshots, docs, or onboarding.
- Help shape the hosted web architecture.
- Pick an item from `ROADMAP.md`.

## Visibility And Feedback

If Prismara AI is useful or interesting, please star the repo, open an issue with feedback, or share a setup report. Even small notes like "this worked on my machine" are useful while the project is early.

For launch copy, outreach ideas, and metrics to track, see `LAUNCH_KIT.md`.

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
