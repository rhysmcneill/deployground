# Deployground

**Self-service deploys for apps and infra, from the tools your team already uses.**

[![CI](https://github.com/rhysmcneill/deployground/actions/workflows/ci.yml/badge.svg)](https://github.com/rhysmcneill/deployground/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Deployground lets anyone on your team deploy a service, or a whole stack of services, straight
from the chat tool they're already sitting in — no `kubectl`, no YAML, no waiting on a platform
team. Pick a stack, pick versions, hit submit. A PR opens, GitOps takes it from there, and you
watch it go live in the same thread. Every deploy is owned, time-boxed, and torn down
automatically when it expires.

**Slack is the first integration, not the only one planned.** The GitOps writer, lifecycle, and
access guardrails don't know or care which chat tool triggered them — Slack is just what Step 1
built the front end in. Teaching Deployground a second chat platform (Microsoft Teams, say) is
on the roadmap as that front end's natural next step, not a rewrite of the core underneath it.

It's a thin front end over **Git and ArgoCD** — the bot never touches your cluster directly, and
nothing it needs is installed out-of-band beyond one bootstrapped ApplicationSet. No database:
Git is the source of truth, and the bot rebuilds its state from Git on every restart.

## Why

Most "self-service deploy" tooling is either a bespoke internal CLI nobody remembers how to use,
or a platform UI that's one more tab and one more login. Deployground meets people where they
already are — a Slack form — and turns that into the GitOps PR your existing ArgoCD setup
already knows how to apply. If your team can approve a pull request, they already know how to
operate this.

## How it works

```
Slack form → authz check → render component files → one Git PR
                                                         │
                                          ArgoCD syncs ──┘
                                                         │
          Slack thread updates live  ←  status (push or poll)
```

1. **Pick a stack** from a Slack dropdown — one service, or many deployed together as a unit.
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
│ Stack:        [ pool-party  ▾ ] │
│ Environment:  [ homelab     ▾ ] │
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
few lines of config — an image, a port, a default version.

## Built to grow

Deployground's core — the GitOps writer, lifecycle (TTL/warn/reap), and access guardrails —
doesn't change as new things get added on top of it; none of it has ever imported anything
Slack-specific. Everything that varies sits behind a seam. Three are real protocols today —
deploy targets and status sources each already have two implementations, capabilities its
first (`image-deploy`) — and one, the chat layer, is still a stated direction rather than code:

- **The chat layer** — Slack today, directly (there's no platform abstraction yet; that's the
  honest state of Step 1). Adding Microsoft Teams or another tool means extracting that
  interface and giving it a second implementation, not touching the GitOps writer, lifecycle,
  or access guardrails underneath — none of those know Slack exists.
- **Capabilities** — what gets deployed. Today there's one: `image-deploy`, for pre-built
  container images. The design leaves room for a `repo` capability (build from source, with
  per-PR preview environments) and an `infra` capability (Terraform/Terramate stacks via CI,
  plan → approve → apply) — each reusing the same form engine, lifecycle, and chat-layer UX a
  new capability gets for free.
- **Deploy targets** — how a render becomes a running workload: a bootstrapped ArgoCD
  `ApplicationSet` by default, or a native `Application` CR for operators who restrict
  ApplicationSets. Flux and raw-manifest targets fit the same seam.
- **Status sources** — how progress gets back to the chat thread: ArgoCD Notifications (push) or
  the ArgoCD API (poll) today; any event-driven source (a CI pipeline, EventBridge) fits the
  same shape.

## Project layout

```
src/deployground/   the bot — config, catalog, Slack, lifecycle, deploy targets, status sources
helm/charts/         Helm charts: the bot itself, and the default workload chart (golden-path)
docs/                quickstart, the design spec, and the implementation plan
examples/            a complete example config.yaml
deploy/               the Slack app manifest
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, running the tests, and the PR flow.

## License

Apache-2.0. See [LICENSE](LICENSE).
