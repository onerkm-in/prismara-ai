# Contributing to Prismara AI

Thank you for wanting to contribute. Prismara AI is a local-first, privacy-conscious multi-model agent workspace, so contributions should preserve user control, local storage safety, and explicit online consent.

## Ways To Contribute

- Report bugs with clear reproduction steps.
- Suggest features or platform support.
- Improve documentation, onboarding, and examples.
- Fix focused issues in the frontend, backend, packaging, or local AI setup.
- Help review pull requests and test desktop or web deployment paths.

## Contribution Flow

1. Fork the repository.
2. Create a feature branch from `main`.
3. Make focused changes.
4. Run relevant checks.
5. Open a pull request using the PR template.

Use a branch name like:

```text
feature/model-setup-copy
fix/windows-launcher-log-path
docs/hosting-options
```

## Local Setup

Python dependencies:

```bat
python -m pip install -r requirements.txt
```

Frontend dependencies:

```bat
cd frontend
npm install
npm run build
```

Server dependencies:

```bat
cd server
npm install
```

## Checks

Run the checks that match your change:

```bat
cd frontend
npm run build
```

```bat
python -m compileall launcher.py main.py src server
```

If you change packaging, also verify:

```bat
build.bat
```

## Privacy And Security Expectations

- Do not commit `.env`, credentials, tokens, model weights, runtime data, logs, chat history, or recovery packs.
- Keep `prismara/`, legacy `mlmae/`, `dist/`, `build/`, `release/`, `node_modules/`, and local caches out of pull requests.
- Preserve the local-first trust model.
- Online providers must remain opt-in and consent-based.
- Do not weaken encrypted-at-rest behavior or transfer-window protections without a clear design discussion.

## Pull Request Guidelines

- Keep PRs focused and reviewable.
- Explain what changed and why.
- Include screenshots for visible UI changes.
- Include validation steps and known limitations.
- Avoid unrelated formatting or refactors.

## Desktop And Web Direction

Prismara AI is expected to support both:

- A portable desktop app for local-first private use.
- A future hosted web option for teams or remote access.

When contributing platform changes, please call out whether the change affects desktop, web, or both.
