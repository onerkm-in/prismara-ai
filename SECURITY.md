# Security Policy

Prismara AI handles local state, credentials, chat history, model configuration, and recovery data. Please report security issues responsibly.

## Supported Versions

The `main` branch is the active development branch.

## Reporting A Vulnerability

Do not open a public issue for a vulnerability.

Please contact the project owner privately with:

- A clear description of the issue.
- Steps to reproduce.
- Impact and affected files or features.
- Any suggested fix, if available.

## Sensitive Areas

Please take extra care around:

- Machine-bound encrypted storage.
- Credential and SSO handling.
- Transfer-window logic.
- Backup and recovery packs.
- Online provider consent.
- Local file indexing and workspace uploads.
- Future hosted web authentication and tenant boundaries.

## Contributor Rules

- Never commit real credentials, `.env` files, logs, recovery packs, model weights, chat history, or runtime folders.
- Do not add telemetry, remote upload behavior, or online provider calls without explicit consent controls.
- Avoid weakening local-first defaults.
