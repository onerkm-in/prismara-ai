# Prismara AI Roadmap

This roadmap is intentionally practical. Prismara AI should become useful as a private desktop app first, then grow into a hosted web option where that makes sense.

## Near Term

- Publish the first public GitHub Release with `PrismaraAI.exe`, SHA-256 hash, screenshots, and release notes.
- Add a short demo video or GIF showing setup, System Doctor, local model selection, and a prompt run.
- Improve first-run onboarding for users without Ollama or local models installed.
- Add more setup diagnostics for common Windows, antivirus, firewall, and GPU cases.
- Collect early feedback through GitHub issues.

## Desktop Track

- Keep the app portable and local-first.
- Improve Windows packaging confidence through repeatable builds and release hashes.
- Add clearer signed-release guidance.
- Harden runtime folder migration and recovery flows.
- Add screenshots and smoke-test checklists for contributors.

## Web Track

The hosted web direction needs a deliberate architecture pass before implementation.

Questions to answer:

- Will the web version run fully hosted inference, user-provided provider keys, local gateway mode, or a hybrid?
- What data must be stored server-side, and what should remain client-side only?
- What authentication model is needed for individual users vs teams?
- What is the cost model for hosted inference, storage, logs, and background jobs?
- Which platform fits best for frontend hosting, API hosting, workers, storage, and secrets?
- How should desktop and web accounts relate, if at all?

Candidate areas to research next:

- Frontend hosting: Vercel, Netlify, Cloudflare Pages, static hosting.
- API hosting: Render, Fly.io, Railway, Azure App Service, AWS App Runner, Google Cloud Run.
- Background workers: Cloud Run jobs, Fly machines, hosted queues, or self-hosted workers.
- Storage: Postgres, object storage, encrypted file storage, tenant-aware backups.
- Auth: GitHub, Google, email magic links, enterprise SSO later.

## Good First Issues

- Add screenshots to the README.
- Add a basic architecture diagram.
- Add a Windows setup troubleshooting page.
- Add a release checklist.
- Improve local model recommendation copy.
- Add docs for running the dev server.
- Add tests around secure storage and transfer-window behavior.

## Maintainer Notes

Preserve these product principles:

- Local-first by default.
- No silent cloud fallback.
- Explicit user consent for online providers.
- No committed secrets, logs, model weights, runtime folders, or recovery packs.
- Desktop and web should share product intent, but not force the same trust model.
