# Deployground

**Self-service deploys for apps and infra, from the tools your team already uses.**

[![CI](https://github.com/rhysmcneill/deployground/actions/workflows/ci.yml/badge.svg)](https://github.com/rhysmcneill/deployground/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Deployground lets anyone on your team deploy a service, or a whole stack of services, straight
from the chat tool they're already sitting in — no `kubectl`, no YAML, no waiting on a platform
team. Pick a stack, pick versions, hit submit. A PR opens, GitOps takes it from there, and you
watch it go live in the same thread. Every deploy is owned, time-boxed, and torn down
automatically when it expires.

It's a thin front end over **Git and ArgoCD** — the bot never touches your cluster directly, and
nothing it needs is installed out-of-band beyond one bootstrapped ApplicationSet. No database:
Git is the source of truth, and the bot rebuilds its state from Git on every restart.

## Contents

- [Why](#why)
- [How it works](#how-it-works)
- [Try it](#try-it)
- [Compatible with](#compatible-with)
- [Getting started](#getting-started)
- [Documentation](#documentation)
- [Architecture](#architecture)
- [Contributing](#contributing)
- [License](#license)

## Why

Most "self-service deploy" tooling is either a bespoke internal CLI nobody remembers how to use,
or a platform UI that's one more tab and one more login. Deployground meets people where they
already are — a Slack form — and turns that into the GitOps PR your existing ArgoCD setup
already knows how to apply. If your team can approve a pull request, they already know how to
operate this.

## How it works

```
Slack/MS Teams etc form → authz check → render component files → one Git PR
                                                                   │
                                                    ArgoCD syncs ──┘
                                                                   │
                    Slack thread updates live  ←  status (push or poll)
```

1. **Pick a catalog entry** from a Slack dropdown — one service, or many deployed together as a
   unit — and give your deploy its own **stack name**, so the same catalog entry can be live
   under several names at once.
2. **Override versions** per component if you need to (optional; everything else deploys at its
   configured default).
3. Deployground **opens one PR** with every component's manifest, each stamped with an owner,
   an expiry, and the resolved version.
4. Your **ArgoCD** instance syncs it — Deployground never applies anything to the cluster itself.
5. The **same Slack thread** tracks progress live and pings you when it's done (or tells you
   exactly which component failed).
6. Every deploy has a **TTL**. You get a warning before it expires, with one-click *Extend* /
   *Destroy now* buttons; if you do nothing, the reaper tears it down — one PR, everything it
   created, gone.

Access is **default-deny** and scoped by Slack user, usergroup, or channel, per environment.
"Allowed in prod" still goes through a PR review if that environment requires approval.

## Try it

```
┌─ New deployment ────────────────┐
│ Catalog:      [ my-catalog  ▾ ] │
│ Stack name:   [ my-stack      ] │
│ Environment:  [ dev         ▾ ] │
│ Expires in:   [ 24h           ] │
│ Override versions? (optional)   │
│   ☑ web   → [ v4.6.0        ]   │
│   ☐ api                         │
│   ☐ worker                      │
│              [ Submit ]         │
└──────────────────────────────────┘
```
…and the thread fills in as the three components go `Deploying → Running`, then posts
`✅ Deployment complete — @you`.

## Compatible with

**Chat/UI front end** — Slack today, direct and complete (forms, threads, buttons, App Home).
The core doesn't know Slack exists, so a second front end is additive, not a rewrite:

| Front end | Status |
|---|---|
| Slack | ✅ Available today |
| Microsoft Teams | 🗺️ Stated direction, not yet built |

**Deploy target** — how a render becomes a running workload:

| Target | Status |
|---|---|
| Argo CD `ApplicationSet` (bootstrapped for you) | ✅ Available today |
| Argo CD `Application` CR (for operators who restrict ApplicationSets) | ✅ Available today |
| Flux | ❌ Not planned |

**Status source** — how deploy progress gets back to the chat thread:

| Source | Status |
|---|---|
| Argo CD Notifications (push) | ✅ Available today |
| Argo CD API (poll) | ✅ Available today |

See [Architecture](#architecture) for why adding a new one of any of these doesn't touch the
core underneath it.

## Getting started

```
helm install deployground oci://ghcr.io/rhysmcneill/deployground-chart \
  --version <version> -n deployground -f my-values.yaml
```

Follow **[docs/quickstart.md](docs/quickstart.md)** for the full walkthrough: creating the Slack
app from the included manifest, wiring up a GitOps repo, and bootstrapping the ArgoCD
ApplicationSet that turns Deployground's PRs into running workloads. There's a filled-in example
at [`examples/config.yaml`](examples/config.yaml) to start from.

Deploying your own services needs no chart of your own: the default workload chart
(`golden-path-chart`) is published and versioned alongside the bot, so adding a stack is just a
few lines of config — an image, a port, a default version. A service that needs more can bring
its own chart instead — see [Configuration](docs/configuration.md#custom-charts-per-component).

## Documentation

| | |
|---|---|
| [Quickstart](docs/quickstart.md) | Start here — the install walkthrough, in order |
| [Slack setup](docs/slack/SLACK-SETUP.md) | Create the Slack app, get your tokens |
| [Configuration](docs/configuration.md) | Writing `config.yaml`: catalog, custom charts, guardrails, access |
| [Example config](examples/README.md) | Every supported combination, annotated in one working file |
| [Installing the bot](docs/install.md) | The Secret, the Helm install |
| [Bootstrapping Argo CD](docs/bootstrap-argocd.md) | The ApplicationSet, notifications, status connectivity |
| [Deploying from Slack](docs/deploying.md) | Using the deploy form day to day |
| [Architecture](docs/architecture.md) | How the pieces fit together, and the seams for extending it |
| [Contributing](CONTRIBUTING.md) | Dev setup, running the tests, the PR flow |

## Architecture

Deployground's core — the GitOps writer, lifecycle (TTL/warn/reap), and access guardrails —
doesn't change as new things get added on top of it; none of it has ever imported anything
Slack-specific. Everything that varies (the chat front end, what gets deployed, how it's
rendered, how progress gets reported) sits behind a seam, so adding a new one of any of them is
additive. Full detail, including the project layout and what's deferred, is in
**[docs/architecture.md](docs/architecture.md)**.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, running the tests, and the PR flow.

## License

Apache-2.0. See [LICENSE](LICENSE).
