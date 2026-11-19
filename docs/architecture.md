# Architecture

## Data flow

```
Slack/MS Teams etc form → authz check → render component files → one Git PR
                                                                   │
                                                    ArgoCD syncs ──┘
                                                                   │
                    Slack thread updates live  ←  status (push or poll)
```

There is no database. Git is the source of truth for every live deploy; the bot rebuilds its
whole in-memory index by reading the GitOps repo on every restart. It never touches the cluster
directly — the only things it installs out-of-band are the Secret it reads and one bootstrapped
Argo CD `ApplicationSet`.

## Built to grow

The core — the GitOps writer, lifecycle (TTL/warn/reap), and access guardrails — doesn't change
as new things get added on top of it; none of it has ever imported anything Slack-specific.
Everything that varies sits behind a seam. Three are real protocols today — deploy targets and
status sources each already have two implementations, capabilities its first (`image-deploy`) —
and one, the chat layer, is still a stated direction rather than code:

- **The chat layer** — Slack today, directly (there's no platform abstraction yet; that's the
  honest state of Step 1). Adding Microsoft Teams, Discord, or another tool means extracting that
  interface and giving it a second implementation, not touching the GitOps writer, lifecycle, or
  access guardrails underneath — none of those know Slack exists.
- **Capabilities** — what gets deployed. Today there's one: `image-deploy`, for pre-built
  container images. The design leaves room for a `repo` capability (build from source, with
  per-PR preview environments) and an `infra` capability (Terraform/Terramate stacks via CI,
  plan → approve → apply) — each reusing the same form engine, lifecycle, and chat-layer UX a new
  capability gets for free.
- **Deploy targets** — how a render becomes a running workload: a bootstrapped Argo CD
  `ApplicationSet` by default, or a native `Application` CR for operators who restrict
  ApplicationSets. There's no Flux target today, and none currently planned — the seam exists,
  but this isn't a roadmap commitment.
- **Status sources** — how progress gets back to the chat thread: Argo CD Notifications (push) or
  the Argo CD API (poll) today; any event-driven source (a CI pipeline, EventBridge) fits the
  same shape.

## Not covered yet

Packaging capabilities, deploy targets, and status sources as third-party plugins via Python
entry points is deferred. The factories (`make_deploy_target`, `make_status_source`) are the
seam, but entry-point discovery is a post-step-1 extension.

## Project layout

```
src/deployground/   the bot — config, catalog, Slack, lifecycle, deploy targets, status sources
helm/charts/         Helm charts: the bot itself, and the default workload chart (golden-path)
docs/                setup, configuration, and architecture docs; the design spec and plan
examples/            a complete example config.yaml
deploy/               the Slack app manifest
```
