# Prismara AI Launch Kit

Use this file as a lightweight marketing kit. The goal is not hype. The goal is to explain the project clearly to the right people and invite useful feedback.

## Positioning

Short version:

> Prismara AI is a local-first multi-model agent workspace for people who want AI help without giving up control of their data.

Longer version:

> Prismara AI is an open-source local-first agent workspace. It runs as a portable desktop app, keeps state under a machine-bound local folder, recommends local models based on hardware, and uses cloud providers only with explicit consent. The project is starting with desktop and will explore a hosted web option next.

## Who It Is For

- Developers experimenting with local AI agents.
- Privacy-conscious users who want local chat history, credentials, and model storage.
- People using Ollama or OpenAI-compatible local runtimes.
- Builders interested in desktop plus hosted web AI tooling.
- Contributors who want to help shape a young open-source project.

## What To Ask For

Do not only ask for stars. Ask for specific feedback:

- Did the setup flow make sense?
- Did System Doctor correctly read your hardware?
- Were local model recommendations useful?
- Was the online-consent model clear?
- What would make the desktop app trustworthy enough to use?
- What should the future hosted web version include or avoid?

## Launch Checklist

- Add at least 2 screenshots to the README.
- Publish the first GitHub Release with `PrismaraAI.exe` and `.sha256`.
- Add a short demo video or GIF.
- Pin a "feedback wanted" issue.
- Add repo topics: `local-ai`, `ollama`, `multi-agent`, `privacy`, `desktop-app`, `ai-workbench`, `open-source`.
- Share one focused post per channel instead of posting everywhere at once.

## GitHub Release Copy

Title:

```text
Prismara AI public alpha
```

Body:

```markdown
Prismara AI is a local-first multi-model agent workspace for private AI work on your own machine.

This first public alpha focuses on the portable Windows desktop path:

- Local runtime folder under `prismara/`
- Machine-bound encrypted state
- System Doctor for hardware and local model readiness
- Ollama-compatible local model setup
- Multi-stage agent pipeline
- Explicit online-provider consent

Please try it, report setup friction, and share what hardware/runtime combination you used.
```

## LinkedIn Post

```text
I just published Prismara AI as an open-source project.

It is a local-first multi-model agent workspace for people who want AI assistance without giving up control of their data.

The current focus is a portable Windows desktop app:
- encrypted local state
- hardware-aware local model recommendations
- Ollama-compatible runtime setup
- multi-stage agent orchestration
- explicit consent before using online providers

Next I am exploring what a hosted web version should look like and what it would cost to run responsibly.

I would love feedback on the setup flow, privacy model, and desktop + web direction:
https://github.com/onerkm-in/prismara-ai
```

## Reddit Or Community Post

```text
I built an open-source local-first multi-model agent workspace: Prismara AI.

The idea is to keep chat history, credentials, memory, logs, backups, and local model storage on the user's machine by default, while still allowing explicit per-request online assistance when needed.

It currently targets a portable Windows desktop app and Ollama-compatible local runtimes. I am also planning a hosted web version, but I want to think carefully about trust boundaries and cost before building that path.

I would appreciate feedback from people running local models:
- Does the setup/trust model make sense?
- What hardware/model detection would you expect?
- What would make this useful enough to try regularly?

Repo: https://github.com/onerkm-in/prismara-ai
```

## Show HN Draft

```text
Show HN: Prismara AI - local-first multi-model agent workspace

Prismara AI is an open-source local-first agent workspace. It is designed around a portable desktop app, encrypted local state, hardware-aware local model setup, and explicit consent before using online providers.

I am sharing it early because I would like feedback on the trust model, Windows packaging, Ollama setup flow, and the future desktop plus hosted-web direction.

Repo: https://github.com/onerkm-in/prismara-ai
```

## Metrics To Watch

- GitHub stars: interest.
- GitHub forks: experimentation.
- Issues: people are trying it or thinking about it.
- Pull requests: contribution momentum.
- Release downloads: real usage intent.
- GitHub Insights traffic: where people are coming from.
- Clones: stronger signal than page views.
- Comments mentioning hardware/configuration: early product learning.

## First 30 Days

- Week 1: polish README, release, screenshots, demo.
- Week 2: post to one or two communities and answer every comment.
- Week 3: turn repeated questions into docs and issues.
- Week 4: publish a short update with what changed based on feedback.
