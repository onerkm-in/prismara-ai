# Prismara AI Handover (Technical + Functional + Operational)

Last updated: 2026-05-11 (end of session)
Workspace root: `D:\Working_Folder\Projects\mlmae`
Brand note: the project is now branded **Prismara AI**; older technical notes may still refer to its original internal name, MLMAE.

## 0) Next-session quickstart

You are picking up a working Prismara AI codebase with a partial-fail observed
at the end of the previous session. Read in this order:

1. **§6 Fix list** — the prioritised next-action list (P0 → P3). Start
   with the P0s; they're scoped, have file-level entry points, and
   each is independently shippable.
2. **§1 Executive Summary** — what this project is and current state.
3. **§3.6 Pool fallthrough** + **§3.7 RunTrace persistence** —
   the most recent architectural additions and how they interact.
4. **§9 Resume Checklist** — concrete commands to verify the dev
   chain is healthy before you make changes.

The Diagnostics modal (top-right Activity icon in the UI) is the
fastest way to inspect what happened on past runs — each click on a
run shows the full retry chain and a Copy/Download for sharing.

Local environment on the machine where this code was last run is
captured in §5.1 (Ollama path, model list on D:, OLLAMA_MODELS env
var). If you're on a different machine, run through §5.4 smoke checks
first.



## 1) Executive Summary

Prismara AI is a multi-agent orchestration app with:
- React + Vite frontend (`frontend/`)
- Flask backend for dev (`server/app.py`)
- Node proxy (`server/server.js`) for local `/api` routing in dev
- Standalone Flask server for packaged exe mode (`server/app_standalone.py`)

**As of 2026-05-10 the supervisor/IC model has been replaced by a local-first
role pipeline** in `src/orchestrator.py`. Every request flows through typed
stages (channeliser → refiner → augmenter → processor(s) → researcher →
summariser → synthesiser → validator → security_scanner). Dependency stages
stay sequential, while independent processor intents and final safety checks
run with bounded hardware-aware parallelism. Each stage emits streaming events
so the UI can render a live progress strip.

Current state:
- Simple deterministic prompts (date / day / time) still bypass the pipeline
  and answer in a single deterministic step. Simple greetings such as
  `Hello There` and `Hey there` also bypass the pipeline and return
  immediately. This guard exists in both HTTP route layers and inside
  `src.orchestrator.orchestrate()` as a defence-in-depth stop before model
  detection or channeliser/refiner stages.
- Non-simple prompts run the full role pipeline against locally available
  Ollama models. If zero local agents are available, the pipeline fails
  loudly with a clear setup hint instead of silently escalating to paid
  cloud. This is the **cold-start = fail-loud** policy.
- Online providers are invoked only when the user has ticked "Allow online
  assistance for this request only" in the form AND a local processor failed
  to satisfy a given intent. This is the **per-request explicit consent**
  policy. See `_stage_researcher` in `src/orchestrator.py`.
- The previously-broken neural memory path (`'event_log'` KeyError on
  fingerprint mismatch) is fixed: `secure_storage.load_json` now distinguishes
  decode-failure from file-missing via a `strict=True` mode, and
  `memory_core.read_memory` raises a typed `MemoryDecodeError` when the
  schema is incomplete instead of silently returning `{}`.
- Stage outputs that exceed 8 KB spill to `prismara/cache/jobs/{request_id}/`
  on local disk. The spill check refuses to write below 1 GB free disk and
  caps any single job at 200 MB. The job directory is removed on completion.
- Free/local model registry remains at ~92 entries (`src/llm_client.py`),
  ~80 of them local/free-tier.
- Admin Control Panel still owns all external configuration: API keys, SSO
  profiles, custom OpenAI-compatible models, integrations, data guard mode,
  backup config, transfer-window approval. No external configuration lives
  outside the admin panel.

## 2) Repository Map

- `main.py`: thin CLI shim around `src.orchestrator.orchestrate()`
- `src/orchestrator.py`: **NEW** — local-first role pipeline,
  per-stage streaming, bounded parallel processor/safety fan-out,
  disk-aware spillover, role pools, online-consent Researcher fallback
- `src/llm_client.py`: agent registry + provider implementations +
  `call_llm()`, `detect_available_agents()`, `pick_best_agent()`. Google
  provider uses the current `google-genai` SDK (the deprecated
  `google.generativeai` has been removed).
- `src/local_ai.py`: **NEW** — Ollama detection, silent installer download
  + run, NDJSON-streamed model pull, model delete, OpenAI-compatible
  endpoint probe (LM Studio / llamafile / vLLM / TGI / Jan / Koboldcpp),
hardware/RAM/GPU fit detection, model update streaming, admin hardware
acceleration consent policy, and a curated `MODEL_CATALOG` of recommended
pulls with size + role coverage hints. Backs the Setup Local AI admin card.
- `src/memory_core.py`: neural memory read/write with schema validation
  and typed `MemoryDecodeError`
- `src/secure_storage.py`: machine-bound XOR-obfuscated JSON + backup,
  `strict=True` mode for unambiguous decode-failure surfacing
- `src/data_guard.py`: provider-risk-tiered prompt anonymisation (DataGuard)
- `src/sso_auth.py`: OAuth2 client-credentials + device-code + refresh
- `src/machine_identity.py`: machine fingerprint (MAC + hostname + WMIC)
- `src/election_engine.py`: **DEPRECATED shim**, kept for compat imports
- `src/supervisor_agent.py`: **DEPRECATED shim**, kept for compat imports

- `server/app.py`: dev Flask API. `/run` and `/run-stream` now consume the
  orchestrator generator directly. New admin endpoints:
  `/admin/local-ai/status` (GET),
  `/admin/local-ai/install` (POST → NDJSON stream),
  `/admin/local-ai/pull-model` (POST → NDJSON stream),
  `/admin/local-ai/model/<name>` (DELETE),
  `/admin/local-ai/detect-custom` (POST). All gated by `@admin_required`.
  Other admin/settings/integrations/sso/backup routes unchanged.
- `server/server.js`: Node proxy — `/api/*` → `http://localhost:5000`
- `server/app_standalone.py`: packaged-mode API + static hosting; same
  orchestrator wiring as dev.

- `frontend/src/App.jsx`: workspace, streaming, admin panel, agreement
  modal, **per-stage Pipeline Progress strip**, per-request online
  consent checkbox, **Setup Required panel** (renders `setup_required`
  event with deep-link buttons), **Setup Local AI admin card** (Ollama
  detection, install streamer, model picker with role coverage, pull
  progress, model remover, custom-endpoint probe), final/metrics/raw
  output panels.
- `frontend/src/App.css`: panel styles plus `.pipeline-*`,
  `.online-consent-row`, `.setup-required-*`, `.online-mode-banner`,
  `.local-ai-*` rules.

- `launcher.py`: exe launcher, folder/bootstrap/machine-lock setup
- `prismara.spec`: PyInstaller spec - `src.orchestrator` added to
  `hidden_imports`; legacy modules retained for safety.
- `build.bat`: build helper script

## 3) Technical Handover

### 3.1 Runtime Architecture

Development chain:
1. Vite frontend (`http://localhost:5173`)
2. Vite proxy forwards `/api/*` → Node (`http://localhost:3000`)
3. Node proxy forwards `/api/*` → Flask dev backend (`http://localhost:5000/*`)

Packaged chain:
1. `launcher.py` starts standalone Flask on port `7432`
2. Flask serves both static frontend assets and `/api/*`
3. No Node proxy needed in packaged mode

### 3.2 Role Pipeline (`src/orchestrator.py`)

| Stage              | Purpose                                              | Default local pool (priority order, lightest first)   |
| ------------------ | ---------------------------------------------------- | ----------------------------------------------------- |
| channeliser        | Detect single vs multi-intent (code/reason/write/vision/general) | TinyLlama, Qwen3 0.6B, Qwen3 1.7B, Phi-3.5 Mini |
| refiner            | Clean / disambiguate the prompt                      | Phi-3.5 Mini, Qwen3 1.7B/4B, Llama 3.2, Gemma 3       |
| augmenter          | Inject workspace-tree context hints                  | Qwen3 4B/8B, Llama 3.1 8B, Gemma 3, Mistral 7B        |
| processor_code     | Code-specific work                                   | DeepSeek Coder, Qwen 2.5 Coder, CodeGemma, StarCoder2 |
| processor_reason   | Step-by-step reasoning                               | DeepSeek R1 1.5B/7B/8B, Qwen3 8B, Phi-4               |
| processor_write    | Writing / tone / nuance                              | Gemma 3, Llama 3.1 8B, Mistral Nemo, Mistral 7B       |
| processor_general  | Catch-all                                            | Llama 3.2, Phi-3.5 Mini, Qwen3 4B, Mistral 7B         |
| processor_vision   | Image-aware                                          | Llama 3.2 Vision, Mistral Small 3.1 24B, Llama 4 Scout|
| researcher         | **Online fallback** — only with consent + key        | (paid) Claude 3.5 Sonnet, GPT-4o, Gemini 1.5 Pro      |
| summariser         | Compress per-intent processor output                 | Phi-3.5 Mini, Qwen3 4B, Llama 3.2, Phi-4              |
| synthesiser        | Merge multi-intent answers into one coherent reply   | Llama 3.1 8B, Mistral Nemo, Gemma 3 12B, Qwen3 8B     |
| validator          | Judge answer vs original intent (PASS/PARTIAL/FAIL)  | DeepSeek R1 1.5B, Phi-3.5 Mini, Qwen3 4B, Llama 3.2   |
| security_scanner   | Vuln/secret patterns in any generated code blocks    | DeepSeek Coder, Qwen 2.5 Coder, StarCoder2, CodeGemma |

Fail modes per stage are graceful: a failed or unavailable stage emits a
typed `error` or `skipped` event and the pipeline continues. The Finaliser
always emits `final_answer`/`metrics`/`result`/`done` even in degraded runs.
Role selection also skips Ollama tags listed in `MLMAE_KNOWN_BROKEN_MODELS`
(default: `deepseek-r1:1.5b`) even if stale availability data reports them
as installed.

### 3.3 Streaming Protocol

`/run-stream` and `/api/run-stream` emit NDJSON events. Event types:

| Type            | Meaning                                                |
| --------------- | ------------------------------------------------------ |
| `status`        | Top-level boot / phase message                         |
| `stage`         | Per-stage event: `name`, `status` (queued/running/done/error/skipped), `agent`, `duration_ms`, `output_preview`, `note`, `error` |
| `intents`       | Detected intents (`["code","reason","write","vision","general"]`) and source (`llm` or `regex`) |
| `consent_used`  | Online provider was actually invoked: `agent`, `intent`, `duration_ms` |
| `warning`       | Non-fatal pipeline note (e.g., neural memory unwritable) |
| `final_answer`  | Human-readable primary answer                          |
| `metrics`       | `request_id`, `process_ms`, `models[]`, `intents[]`, `disk_bytes_used` |
| `result`        | Full structured report                                 |
| `done`          | Stream complete                                        |
| `error`         | Fatal failure                                          |

The `agent` event remains in the protocol for backward compatibility but
is no longer emitted by the new orchestrator — `stage` carries that info.

### 3.4 Disk-aware spillover

Each request gets its own working directory at
`prismara/cache/jobs/{request_id}/`. Stage outputs <= 8 KB stay in RAM; larger
outputs spill to `{stage}.txt`. `JobContext` tracks bytes-on-disk and
refuses to spill if either:

- The free disk on the spill volume drops below `MIN_FREE_DISK_GB` (1 GB), or
- The job's spilled bytes would exceed `MAX_PER_JOB_MB` (200 MB).

When either guard trips, the output stays in RAM (accepting higher RAM use
on that one request) and the metrics event reports `disk_bytes_used` so
the UI / Admin Storage Health panel can surface pressure. The job dir is
removed in a `finally` block on completion or crash.

### 3.5 Online consent gating

The `/run` and `/run-stream` payloads accept `online_consent: bool`.
Front-end exposes this as a per-request checkbox in the prompt form;
default off.

Cold-start guard logic (in `orchestrator.orchestrate()`):
- **No local + consent=false** → emits `setup_required` event with three
  structured options (install Ollama, configure custom local endpoint,
  enable online for this request). UI renders the Setup Required panel.
- **No local + consent=true + paid key present** → emits
  `online_pipeline_mode` event, sets `JobContext.online_consent=True`,
  and `_pick_role_agent` falls through from `ROLE_POOLS` (local) to
  `RESEARCHER_FALLBACK_POOL` (online) for every role. UI shows the
  purple "Running on online providers" banner.
- **Local available + consent=true** → local-first wins per role; the
  Researcher stage uses online only for intents where the local
  processor returned empty (the original 2026-05-10 behavior).

Researcher fires `consent_used` events whenever it actually invokes an
online provider, so the UI can render a per-invocation badge for
transparency.

### 3.6 Pool fallthrough + retry

Validator and Processor stages now iterate through the role pool on
failure instead of giving up on the first agent. `_iter_role_agents`
yields local-pool agents in priority order, then (only if consent
granted) the matching online fallback pool. Each stage emits a `retry`
event when an agent fails, then continues with the next. Trace records
keep every attempt (agent, status, duration, error) so the diagnostics
view shows the full retry chain.

This eliminates the "one bad model breaks the whole pipeline" failure
mode — observed concretely with `deepseek-r1:1.5b` which crashes the
Ollama llama runner with `exit code 2` even on a fresh pull on this
Ollama 0.23.2 build. The validator now sees the 500, retries the next
pool member (Phi-3.5 Mini), and succeeds.

### 3.7 RunTrace persistence and Diagnostics

Every `orchestrate()` invocation builds a `trace` dict as events stream
by; it is written to `{MLMAE_LOGS_DIR or MLMAE_DATA_DIR/logs}/traces/
{request_id}.json` in the generator's `finally` block. That means
crashed and client-disconnected runs still leave a complete artifact on
disk for post-mortem.

Trace fields: `request_id`, `started_at`, `completed_at`, `duration_ms`,
`status` (ok | error | setup_required | incomplete), `prompt` (first
2000 chars), `intents`, `intent_source`, `models_used`, `online_consent`,
`online_pipeline_mode`, `disk_bytes_used`, `final_answer_preview`,
`error`, `stages[]`, `events[]`. Each stage record has `name`, `status`,
`agent`, `duration_ms`, `output_preview`, `note`, `error`, and an
`attempts[]` list capturing every retry pass (with agent, status,
duration, error).

Admin endpoints (gated by `@admin_required`):
- `GET /admin/traces` → list of summaries (last 200), newest first
- `GET /admin/traces/<request_id>` → full trace JSON
- `GET /admin/system-metrics` → snapshot: RAM total/free/used%
  (via Windows kernel32 GlobalMemoryStatusEx — no third-party dep),
  disk free for `ollama_models`/`traces`/`cache` volumes, Ollama
  daemon state, and currently-loaded models (via Ollama's `/api/ps`).

Frontend: top-right activity icon in the header opens the Diagnostics
modal. Modal split-pane: trace list (left) with status pill + duration +
intents/models preview; trace detail (right) with metadata grid, prompt,
per-stage timeline including all retry attempts. Three actions on
detail: **Copy summary** (human-readable redacted format), **Copy JSON**
(full structure), **Download** (saves as `prismara-trace-{id}.json`).

### 3.8 RAM-vs-SSD policy

Bias is "prefer SSD over RAM" on low-RAM machines. Three knobs, all
env-overridable:

- `MLMAE_INMEM_LIMIT_BYTES` (default 2048): stage outputs above this go
  to `prismara/cache/jobs/{request_id}/{stage}.txt` instead of staying in
  Python memory. Lowered from 8192 on 2026-05-11.
- `MLMAE_MAX_PER_JOB_MB` (default 2048): hard cap on disk used per
  request. Raised from 200 MB so larger workspace contexts don't trip
  the spill guard.
- `MLMAE_OLLAMA_KEEP_ALIVE` (default `"30s"`): passed to Ollama's
  `/api/chat`. Shorter keep-alive forces models to unload between
  stages, freeing RAM at the cost of a ~1-2 s SSD reload when the
  next call hits the same model.
- `MLMAE_PARALLEL_MODE` (default `"auto"`): controls bounded pipeline
  parallelism. Use `"off"`/`"sequential"` to force one stage at a time.
- `MLMAE_MAX_PARALLEL_STAGES` (default `0` = auto): manual cap for
  independent processor and safety stages. Auto mode uses one stage when
  only one local model is available, two stages on CPU/RAM profiles with
  at least 12 GB RAM, and up to three stages when GPU offload is allowed
  with enough RAM.

### 3.8.1 Bounded parallelism

Implemented 2026-05-11: processor intents now fan out in parallel when the
request has multiple independent intents and the hardware/model availability
allows it. Validator and security scan also run in parallel after synthesis
because both consume the final answer and do not depend on each other.
Channeliser, refiner, augmenter, researcher, summariser, and synthesiser stay
ordered because each transforms or depends on the previous stage's output.

### 3.9 GPU note

Detected: `NVIDIA GeForce 940MX, 2 GB VRAM` (mobile, 2016). Ollama
attempts GPU offload but falls back to CPU for any model larger than
~1.5 GB at runtime because 2 GB VRAM isn't enough to hold the weights.
Confirmed: during inference, GPU utilization stays at 0% and VRAM
usage is just Ollama's compute context, not the model itself.
Practically the 940MX gives no inference speedup for any model in
`MODEL_CATALOG` except possibly TinyLlama. Treat GPU as unavailable;
optimize the CPU + RAM + SSD path.

Implemented 2026-05-11: `src/local_ai.hardware_policy()` persists an
admin-controlled `hardware_acceleration_consent` flag in encrypted
`prismara/config.json`. `src.llm_client._call_ollama()` now sends
`options: {"num_gpu": 0}` until consent is granted and the detected GPU is
useful for inference. On the verified 940MX system, even with consent stored,
the policy remains `ollama_runtime: "cpu"` because VRAM is below 4 GB.

### 3.10 Setup Local AI admin card

New admin sub-card backed by `src/local_ai.py`. State machine renders
one of five views based on `status()`:

| State                              | UI rendered                          |
| ---------------------------------- | ------------------------------------ |
| `installed: false`                 | "Install Ollama" button + size hint  |
| installed, `daemon.running: false` | "Daemon down" pill, refresh button   |
| installed, daemon up, no models    | Catalog grid only, no pulled table   |
| installed, daemon up, has models   | Pulled table + catalog grid          |
| any state, custom endpoints found  | Probe results with "Adopt" buttons   |

The catalog (`MODEL_CATALOG` in `src/local_ai.py`) is curated to fill
every role pool defined in the orchestrator. Each entry lists ollama tag,
size GB, RAM requirement, the role pools it covers, and a one-line
summary. Click "Pull (X.X GB)" → POST to `/admin/local-ai/pull-model`
returns NDJSON with `{type: "pull", phase, completed, total, status}`
which the UI renders as an inline progress bar.

Installed models can now be refreshed with "Update all" or per-model
"Update" buttons. Both call `/admin/local-ai/update-models`, which re-pulls
installed tags and streams NDJSON progress. The card also displays detected
RAM/GPU fit plus a "Hardware acceleration consent" control backed by
`/settings/hardware` in source mode and `/api/settings/hardware` in packaged
mode.

Implemented 2026-05-11: `status()` now also returns
`model_recommendation`, a hardware-aware plan derived from RAM, GPU policy,
disk headroom, known broken models, and role coverage. The UI shows a primary
model, a starter set, a full-coverage set, and badges catalog cards with
their recommendation tier. On the verified 16 GB RAM + 2 GB 940MX machine,
the plan is `expanded_cpu`, primary `phi3.5`, starter
`phi3.5 + deepseek-coder + gemma3`, full coverage adds `llama3.1:8b`.

Custom-endpoint probe (`probe_custom_endpoints`) tries common ports for
OpenAI-compatible servers: LM Studio (1234), llamafile (8080), vLLM
(8000), TGI (3000), Jan (1337), Koboldcpp (5001). For each open port,
fetches `/v1/models` and offers an "Add as Custom Model" button that
pre-fills the existing Custom Models draft form.

The `setup_required` panel that the orchestrator emits at cold-start
includes a deep-link: clicking "Open Setup Local AI" opens the admin
panel and scrolls to this card (`id="setup-local-ai-card"`).

## 4) Functional Handover

### 4.1 User-Facing Features

- Prompt execution with live per-stage progress strip showing each role's
  status, the agent it picked, duration, output preview, and notes/errors.
- Workspace loading (`/load-folder`) with file tree context.
- Final answer + metrics summary panel (request, process, render, total).
- Per-request "Allow online assistance" checkbox (off by default).
- Admin Control Panel: Google SSO + local admin fallback; data guard mode;
  credentials/API keys; SSO profiles CRUD; integrations CRUD; memory dump;
  storage health; recovery pack; local + GCS backup.

### 4.2 Folder Analysis Agreement

Before first folder analysis, user sees AI-usage agreement modal.
Acceptance is persisted in browser localStorage.

## 5) Operational Handover

### 5.1 Environment Notes

**Host hardware (verified 2026-05-11 via Task Manager):**
- CPU: Intel Core i5-8250U @ 1.60 GHz base / 2.55 GHz turbo, 4 cores / 8 threads. 256 KB L1, 1 MB L2, 6 MB L3.
- RAM: 16 GB total, typically 13-14 GB used in steady state. This is the binding constraint for local inference — Phi-3.5 Mini's first call routinely takes 25-60 s when other models are warm, and the llama runner exits with code 2 if a second model tries to load with <2 GB RAM free.
- Disk 0: SSD SATA hosting **both C: and D:**. Moving Ollama models from C: to D: shifted the path but not the physical disk; IO contention between trace writes (prismara/cache/jobs), model reads, and model pulls all hit the same device. Plan disk-heavy operations sequentially.
- Disk 1: HDD SATA hosting E:/F:/G:/H: — unused by MLMAE, but available if model storage needs to migrate.
- GPU 0: Intel UHD Graphics (integrated). Used for UI/desktop, not LLM inference.
- GPU 1: NVIDIA GeForce 940MX (2 GB VRAM, 2016). Idle during inference — Ollama can't use it because no model in `MODEL_CATALOG` fits in 2 GB VRAM. Treat as unavailable; see §3.9.

Python: `C:/Users/rajes/AppData/Local/Microsoft/WindowsApps/python3.13.exe`
Backend deps: `python -m pip install -r requirements.txt`

Ollama (local LLM runtime):
- Binary on this machine: `C:\Users\rajes\AppData\Local\Programs\Ollama\ollama.exe`
- Daemon endpoint: `http://127.0.0.1:11434`
- Models live on D: drive — `OLLAMA_MODELS=D:\Ollama\models` is set at
  the **User** level via `[Environment]::SetEnvironmentVariable(...,"User")`.
  Any new process the user launches (including `PrismaraAI.exe` from
  Explorer) inherits this. Shells that pre-date the SetX may not — in
  those, `$env:OLLAMA_MODELS = "D:\Ollama\models"` before starting the
  daemon.
- To verify: `Invoke-RestMethod http://127.0.0.1:11434/api/tags`
- Currently pulled (point-in-time, will grow as background pulls finish):
  phi3.5, tinyllama, qwen3:1.7b, qwen3:4b, deepseek-r1:1.5b (plus
  llama3.2, gemma3, qwen2.5-coder, llama3.1:8b, mistral-nemo,
  llama3.2-vision queued/in-progress).

### 5.2 Development Start Commands

From repo root:

1. Flask backend: `python server/app.py`
2. Node proxy: `node server/server.js`
3. Frontend dev server: `npm --prefix frontend run dev`

### 5.3 Quick Health Checks

- UI: `http://localhost:5173/`
- Proxy API: `http://localhost:3000/api/admin/bootstrap`
- Flask health: `http://127.0.0.1:5000/health`

Expected: HTTP 200 on all three.

### 5.4 Smoke checks for the new pipeline

- Run a simple prompt (`what is the date today?`) — must take the
  deterministic fast path, no `stage` events.
- Run a non-simple prompt — verify `intents` event arrives, then a
  `stage` event each for channeliser → refiner → augmenter → processor →
  summariser → synthesiser → validator → (security_scanner only if the
  answer contains a fenced code block).
- Without any Ollama model pulled, expect a single `error` event with the
  cold-start message — **never** a silent escalation to online.
- Tick "Allow online assistance" + run a prompt for a capability the
  local pool can't satisfy — expect a `consent_used` event when the
  paid provider is invoked.

### 5.5 Production / Packaged Mode

- Build frontend first: `npm --prefix frontend run build`
- Build exe via PyInstaller spec or `build.bat`
- Standalone simulation: `python -m server.app_standalone`

## 6) Fix list for next session (observed 2026-05-11)

A real pipeline run produced a cascade of failures. Each item below is
scoped, prioritised, and has the file-level entry point. Order suggested
for next session.

### P0 — Flask reload doesn't pick up `src/*` changes

**Symptom:** After editing `src/orchestrator.py`, the dev server still
served the *old* validator and processor error messages (`note: answer
not judged`, `note: this intent contributes empty output`) instead of
the new retry-aware messages (`all N agents in <pool> failed`).
Werkzeug's reloader should watch imported modules but appears to miss
src/* on this Windows + Git-Bash setup.

**Fix:** In `server/app.py`'s `app.run()`, pass
`extra_files=[*Path("src").glob("*.py"), *Path("server").glob("*.py")]`
so the reloader watches them explicitly. Alternative: document a manual
restart requirement in HANDOVER §5 and add a CLI helper that kills the
prior Flask before starting a new one.

### P0 — Refiner has no retry; one slow model timing out passthroughs the whole stage

**Symptom:** Refiner picked Phi-3.5 Mini, the call took 122 s and was
killed by the 120 s urllib timeout in `src/llm_client.py::_call_ollama`,
refiner fell back to passthrough.

**Fix:**
1. Apply the same retry pattern to `_stage_refiner` that the validator
   and processor stages already use (iterate `_iter_role_agents` until
   one succeeds, emit `retry` events between attempts).
2. Bump `_call_ollama`'s urllib timeout from 120 s → 300 s (or read
   from `MLMAE_OLLAMA_HTTP_TIMEOUT` env var) so slow first-load on a
   cold model doesn't trip when memory pressure causes paging.
3. Reorder the `refiner` pool in `ROLE_POOLS` so tiny models come
   first: `TinyLlama` → `Qwen3 1.7B` → `Phi-3.5 Mini` → rest. Refiner
   doesn't need quality, just clean rewrite of a short prompt.

### P1 — `deepseek-r1:1.5b` is structurally broken on this Ollama build

**Symptom:** Every call to `deepseek-r1:1.5b` fails with `llama runner
process has terminated with exit code 2`. Confirmed after fresh re-pull
on Ollama 0.23.2. Retry fallthrough handles it but wastes ~11-17 s per
attempt on the first stage that picks it.

**Fix:** Either:
1. Remove `"DeepSeek R1 1.5B (Local)"` from the validator and
   `processor_reason` pools in `src/orchestrator.py::ROLE_POOLS` until
   Ollama fixes the runner crash. Other DeepSeek R1 variants
   (7B/8B/14B) may work; pull and test.
2. Add a `known_broken_models: set[str]` filter in
   `src/llm_client.py::detect_available_agents` that marks specific
   model tags as `available=False` even when their blob exists.

### P1 — RAM pressure makes Phi-3.5 Mini take 30-120 s per call

**Symptom:** On 16 GB total RAM with ~3-4 GB free during inference,
Phi-3.5 Mini's first response on a cold cache routinely takes 25-60 s
and occasionally hits the 120 s timeout. Subsequent calls in the same
keep_alive window are fast (4-10 s).

**Fix options (pick one or stack):**
1. **Pre-warm**: at orchestrator boot, fire a 1-token request to the
   model expected to dominate the pipeline (typically Phi-3.5 Mini)
   so it's already resident when channeliser → refiner → … hit it.
   Cost: ~2 s upfront, eliminates 20-60 s first-stage tax.
2. **Raise `MLMAE_OLLAMA_KEEP_ALIVE`** from 30 s to 5 m for runs
   where RAM headroom is available. Trade-off: model stays in RAM
   between requests, faster subsequent calls.
3. **Add RAM-pressure backoff**: before each LLM call, sample free
   RAM via the Windows kernel32 helper already used by
   `/admin/system-metrics`. If below 1.5 GB, force-unload other
   models via `POST /api/generate {"model": ..., "keep_alive": 0}`
   before dispatching the new call.

### P2 — Summariser silent when all processors empty

**Symptom:** When every processor failed (no output), summariser stayed
at `queued` in the UI because the loop body always hits `continue` and
the terminal event was conditional on `invoked` being False AND no
agent being available. With agent present but no input, no terminal
event fires.

**Fix:** In `_stage_summariser`, after the for-loop, emit an
unconditional terminal `skipped` event with note "no processor output
to summarise" when `invoked` is False, regardless of whether `agent` is
set.

### P2 — Trace dir resolution depends on cwd

**Symptom:** Running Flask from `frontend/` writes traces to
`frontend/prismara/logs/traces/` instead of the repo root.
`_traces_dir()` uses `MLMAE_DATA_DIR` env var with default "prismara"
(relative), so cwd determines location.

**Fix:** Resolve the default relative to `Path(__file__).parent.parent`
(repo root) rather than cwd. `launcher.py` already sets
`MLMAE_DATA_DIR` correctly for packaged mode; this only affects dev.

### P2 — Validator runs against synthesiser's fallback message

**Symptom:** When synthesiser fired its "No local processor produced
output" fallback into `ctx`, validator picked it up and tried to judge
it — wasting an LLM call.

**Fix:** In `_stage_validator`, check for the sentinel string OR check
whether synthesiser emitted an `error` event (track that flag in
`JobContext`) and skip validation when synthesis didn't produce a real
answer.

### P3 — UX: pre-flight readiness check before running

**Nice to have:** when the user hits Execute Task, show a one-line
banner that reflects current pressure (RAM free, models in pool, last
trace status). Lets them cancel before they wait 110 s for a likely
failure.

## 7) Backlog

### P1 - Security / Privacy Hardening

1. Lock down local file access on `/load-folder`, `/run`, `/run-stream`.
   Authenticated/admin session OR explicit allowlisted roots. Add total
   byte / file count caps in addition to per-file caps.
2. Bind dev networking to `127.0.0.1` only (currently binds all interfaces
   in `server/server.js` and `server/app.py`). Restrict CORS to the
   expected dev origin.
3. Replace XOR-based "secure storage" with DPAPI (Windows-native) or
   keyring/Fernet/AES-GCM. Until then, never describe the storage as
   "encrypted" — it is machine-bound obfuscation.
4. Add CSRF/session hardening on admin mutation routes.

### P2 - Pipeline polish

1. Memory recovery UX: when a `MemoryDecodeError` is raised, surface a
   one-click "Reset Memory" admin action that does
   `initialize_memory(force=True)` and explains the data loss tradeoff.
2. Cache cleanup: on launcher startup, sweep `prismara/cache/jobs/` for
   directories older than 24 h to reclaim disk after ungraceful exits.
3. Researcher: optionally allow online assistance for *enrichment* on
   intents that locally succeeded (e.g., "fact-check this answer against
   current data"), not just empty-output fallback.
4. Smarter intent detection: use the channeliser output to gate vision
   processors, etc., rather than running every intent's processor when
   the channeliser detected one.

### P3 - Quality / Maintainability

1. Add tests: Flask test-client for `/run`, `/run-stream`, folder loading,
   admin auth, SSO profile CRUD, storage recovery. Frontend tests for
   stage event rendering. Regression test for corrupt neural memory.
2. Fix lint tooling: `frontend/package.json` references `eslint` but it
   is not installed. Either install + configure or remove the script.
3. Add `.gitignore` (project not under git as of last scan). Exclude
   `node_modules`, `.venv`, `__pycache__`, `dist`, `mlmae/`, `prismara/`, `logs/`,
   generated backups, local credentials.
4. Pin Python dependency versions (`requirements.txt` uses lower bounds
   only).

## 7) Known Caveats / Risks

1. The pipeline assumes at least one Ollama model is pulled. With zero
   local agents available, the orchestrator returns a single `error`
   event with a clear setup hint. This is intentional — local-first.
2. Each stage adds latency. On a fully-populated local pool, expect
   12-15 LLM round-trips per non-simple prompt (one per stage, plus per
   intent). The summariser drops outputs ≤ 1000 chars without
   re-prompting to keep this under control.
3. Disk spillover writes plain UTF-8 text under `prismara/cache/jobs/`. If
   a request includes sensitive workspace contents in the augmenter
   stage, those bytes can briefly land on disk. The job dir is removed
   in `finally`; consider encrypted-at-rest spillover before sharing
   the host with other users.
4. `prismara/.machine_id` vs current machine fingerprint mismatches now
   surface as `MemoryDecodeError` (the previous silent `{}` is gone).
   The user-facing error includes a recovery hint pointing at admin
   "Reset Memory" — that admin action is still TODO (P2).
5. The Researcher uses paid-provider models when the user opts in. Even
   though `data_guard.guard_prompt()` runs on the way out, the user
   should still treat any consented prompt as potentially shared with
   the chosen vendor.

## 8) Resume Checklist

1. `python -c "from src.orchestrator import orchestrate; print('ok')"` —
   import sanity check.
2. Start backend, proxy, frontend.
3. Hit health endpoints (5173, 3000 bootstrap, 5000 health).
4. Send `what is the date today?` → expect deterministic answer, no
   pipeline events.
5. Send a non-trivial prompt → watch the Pipeline Progress strip
   populate. Confirm:
   - `intents` event renders the chip in the UI
   - each stage transitions queued → running → done / skipped
   - `Final Response` and `Execution Metrics` panels populate
   - raw output is collapsed unless errored
6. With "Allow online assistance" off, verify no `consent_used` event
   ever fires. Tick the box, send a prompt that exercises an unavailable
   local capability, verify the `consent_used` badge appears.
7. Open admin panel; verify bootstrap/session fetch still work.
8. If packaging work is planned, run standalone smoke on `7432` and
   verify `/api/run-stream` parity.
