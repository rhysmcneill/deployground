# Deployground, Step 1: Core, Form Engine, Stacks, Lifecycle, Status

> Deployground: self-service deploys for apps and infra, from the tools your team already uses.

Status: draft for review. Date: 2026-11-12. Working name: Deployground (deploy plus playground). Repo, Python package, Helm chart, container image, and config/env prefix (`DEPLOYGROUND_`) all use it, defined in one place so a rename stays cheap until publication.

## 1. Purpose
A Slack app where a user fills in a form to deploy workloads into Kubernetes clusters, with automatic expiry. Built first for one user in a homelab, and designed so company developers can use it later with group access, approvals, and environment scoping added without redesign.

Success criteria for Step 1:
- A user deploys a **stack** (one service, or many services together) from a Slack form, with no typed commands.
- Each service in a stack can take its **own version**, overridden at deploy time without filling a field per service.
- Every deployment has an owner and an expiry (default 24h), and is warned about, extendable, and removed automatically — the whole stack as one unit.
- The user sees **live progress** in Slack and a clear done/failed message.
- The system is a thin front end over Git and ArgoCD. The bot never applies workloads to the cluster directly, and nothing Deployground needs is installed out-of-band.
- A new deployment type can be added by implementing one capability, without changing the core.

## 2. Scope
In scope: Slack app; form engine; capability interface; the stack-deploy capability (images); the catalog of stack definitions; GitOps writer and pluggable deploy target; status and progress reporting; lifecycle (TTL, warn, extend, destroy, reaper); access guardrails (user / Slack usergroup / channel, environment-scoped).

Out of scope (later steps or never):
- Building images from a repo and per-PR previews (Step 2).
- IaC stack provisioning, CI plan/approve/apply, and outputs wiring (Step 3).
- Slash commands (decided against).
- A web UI (a capability may link to a small web form later if modals are not enough).
- Source-repo catalog manifests (e.g. a `deployground.yaml` in each app repo) — explicitly not planned.
- Per-stack access scoping, OIDC authz, registry-populated version lists, day-2 version bumps (extension points).

Roadmap: Step 2 adds the `repo` capability (image builds + per-PR preview environments) reusing Step 1. Step 3 adds the `infra` capability (a Terramate/Terraform stack committed to an IaC repo; CI runs plan, approval, apply), with an event-driven status source. Each gets its own spec.

## 3. Model
- **Stack** — the one logical unit a user creates, owns, expires, and reaps together. A **single service is a stack of one.** One Stack = 1..N **components** → N ArgoCD **Applications** → **N Kubernetes `Deployment` resources** (plus their Services/Ingress), all tracked and managed as a single Stack. ("Stack" is Deployground's unit; the Kubernetes object is always written as "k8s Deployment resource" to avoid confusion.)
- **Component** — one unit within a Stack: one image → one Argo Application → one k8s Deployment. Carries an image repo, a **defaultVersion**, and run settings (port, replicas, ingress, env, secret refs). Resource requests/limits are not a component field — they come from the golden-path chart defaults.
- **Stack definition** — the catalog entry a Stack is created from: a name plus its components and their default versions. Defined in Deployground's own config (Section 13), not in source repos.

## 4. Architecture
Components:
- **Slack app** (Python, slack-bolt, Socket Mode → outbound only, no public ingress).
- **Form engine** renders a capability's schema to a Block Kit modal and validates submissions.
- **Capability registry** holds capabilities implementing the interface in Section 6.
- **Catalog** of stack definitions, loaded from config and hot-reloaded.
- **GitOps writer** opens a Git PR with a capability's file changes (auto-merge where configured).
- **Deploy target** (pluggable) turns a render into files the reconciler acts on — default: data files consumed by a bootstrapped ApplicationSet (Section 9).
- **Stack index** — an in-memory cache of stacks, rebuilt on startup by listing the component files in the target repo(s) and parsing their `deployground:` metadata blocks (using only the bot's Git credentials; topology-agnostic — no database, no cluster access). The durable record is the `deployground:` block in each component file (Section 8); Application annotations are a derived bonus, not the read path.
- **Status source** (pluggable) feeds component health back to the bot — default: ArgoCD Notifications push (Section 10).
- **Reaper** — a periodic loop handling warnings and expiry.

Data flow: entry point → form → validate → authz → `render()` → PR → merge → reconciler deploys → status (push/pull) → Slack thread updates → Healthy. Expiry or manual destroy → `destroy()` PR → prune → Destroyed.

## 5. Entry points
- **App Home tab**: a New deployment button and a **My stacks** list (name, status, expires-in, Extend / Destroy). Destroy is a button, not a form — it always opens a Confirm/Cancel step (Section 12).
- **Global shortcut** that opens the same form from anywhere in Slack.
- **Prefilled buttons** posted by CI or a PR bot, carrying field values in the button payload.

## 6. Form engine
- A capability declares a JSON-schema-like `form_schema`: field type, label, default, required flag, options, validation.
- Rendered as Block Kit input blocks. Defaults map to `initial_value` / `initial_option`; fields are required unless marked optional. Server-side validation on submit returns inline errors via `response_action: errors`.
- The deploy flow:
  1. **Pick one stack definition** from a dropdown (`static_select`; `external_select` with typeahead when the catalog exceeds Slack's ~100-option limit).
  2. The stack deploys at each component's **default version**.
  3. **Override versions? (optional)** — a multi-select of that stack's components; only the picked ones reveal a version input prefilled to its default. Built with `views.update`. Keeps a one-service stack trivial and a 20-component stack short.
  4. Shared fields: **environment** (filtered to the user's allowed environments, Section 11) and **expires-in** (default 24h).
- Forms split into steps if they exceed Slack modal limits.

## 7. Capability interface
```
Capability:
  id: str
  title: str
  form_schema: Schema
  ttl_policy: {default: duration, max: duration}
  render(inputs, ctx) -> list[FileChange]     # emits ALL component files for the stack
  destroy(stack) -> list[FileChange]
  status(stack) -> StackState                 # via the pluggable StatusSource
  needs_approval(inputs, env) -> bool
FileChange: {repo, path, content | delete}
```
- One `render()` call emits the files for **every component** of the stack, so a Stack is created or destroyed in a **single PR** — nothing half-written.
- **Version resolution** happens in `render()`: overridden versions where given, config defaults otherwise; the resolved version per component is written into its file (the config default is only the starting point).
- `render()` output is written through the pluggable **deploy target** (Section 9).
- Each component's generated Application is stamped with labels (`deployground.io/stack=<id>`, `deployground.io/component=<name>`, `deployground.io/managed=true`) for status correlation (Section 10) and selection, and with the `deployground:` metadata as annotations. The durable record the bot reads on restart is the `deployground:` block in the Git file, not the annotations (Section 3).

## 8. Stack-deploy capability (images)
Deploys a stack of one or many pre-built images. For each component it writes one data file into the configured target repo, at a path from a configurable template (default `{env}/{stack}/{component}/config.yaml`, Section 13). Each file carries the **workload fields** plus a **`deployground:` metadata block** — the latter is the durable record the bot reads back on restart (Section 3) and what the deploy target maps onto the Application as annotations. Example component file:
```yaml
# workload — consumed by the golden-path chart via the deploy target
image: ghcr.io/org/api:1.2.3   # resolved version (override or default)
port: 8080
replicas: 1
ingressHost: demo-api.home.example
env: {KEY: value}
secretRefs: [name]
# deployground metadata — read back from Git to rebuild the index; mapped to Application annotations
deployground:
  stack: demo
  component: api
  owner: U012ABC              # Slack user ID (from the form payload)
  ownerEmail: user@example.com
  env: homelab
  expiresAt: 2026-11-13T09:00:00Z
  version: v1.2.3
  threadTs: "1700000000.000123"
  channel: C0DEPLOY
```
The deploy target maps the `deployground:` block to Application **annotations** and stamps selector **labels** `deployground.io/stack`, `deployground.io/component`, `deployground.io/managed=true`. Guardrails (allowed registries, ingress domains) apply per component (Section 13); resource requests/limits come from the golden-path chart defaults. How these files become running workloads is the deploy target's job (Section 9).

## 9. ArgoCD integration (deploy target)
Principle: **nothing is applied out-of-band. The glue lives in the GitOps repo and the company's own ArgoCD manages it. The only live workload Deployground runs is the bot.**

Default — **one bootstrapped ApplicationSet**:
- Deployground **generates** a single `ApplicationSet` manifest (templated from its config), scoped to a Deployground-owned subpath. It turns the small data files we write into one Argo Application per component, with automated sync, prune, and finalizers on (so reaping removes all of a stack's components).
- The **operator applies it once** — a reviewed PR into their repo, or output from a `deployground bootstrap` command. The bot never auto-installs it.
- Result: exactly one permanent new Argo object; all ephemeral create/destroy churn is just data files under the one owned subpath.

Least privilege: after setup, the bot's ongoing credential only opens PRs that add/remove data files under the target path — never rights to manage ApplicationSets or the cluster.

Alternative target (config option, not default): write a native Argo **Application** manifest per component into a path the company's existing app-of-apps already watches — for orgs that restrict ApplicationSet. Flux (`HelmRelease`/`Kustomization`) and raw manifests are later options behind the same seam. The deploy target is **pluggable**, ApplicationSet default.

Target location: **configurable, no default.** One config value (repo + path) feeds **both** the generated ApplicationSet (what it watches) and the writer (where it writes), so they can't drift. Operators may point it at a dedicated Deployground repo/path (keeps ephemeral churn out of their main app GitOps repo) or a subpath inside an existing repo.

## 10. Status and progress reporting
Goal: the user always knows where a deploy is and gets a clear done/failed message — across all components of a stack, and later across infra stages.

**Correlation and aggregation**: component Applications carry the `stack`/`component` labels (Section 7). The bot aggregates per stack: **Healthy** only when all components are Synced+Healthy; **Failed** if any fails; **Progressing** otherwise.

**How status arrives — push (ArgoCD Notifications) is the default**:
- **Push (default)**: ArgoCD **Notifications** POSTs app health/sync changes to the bot's cluster-internal webhook endpoint (Argo → bot Service; no public ingress). Preferred because the bot holds **no standing Argo API token** (lower privilege than pull) and updates are near-real-time.
- **Auto-subscription**: the generated ApplicationSet **stamps the notification subscription annotation on every component Application**, so the operator registers the webhook + trigger/template in `argocd-notifications-cm` **once** (Deployground ships the snippet as part of bootstrap) and every future deploy is auto-subscribed — no per-deploy wiring.
- **Pull (optional fallback)**: where Notifications isn't available, the bot can poll the ArgoCD API instead — this needs an API token the operator provisions, so it is opt-in, not the default.
- Both sit behind a pluggable **StatusSource** interface, **configured per Argo instance/environment** — so the bot works whether ArgoCD is in the same cluster, another account, or a homelab (push where the network allows Argo → bot; pull where it doesn't). Rationale: both paths need some operator setup; push via Notifications is the more idiomatic one and avoids the bot holding a broad Argo credential.
- **Status never gates core state.** Health comes from Argo, but the bot's knowledge of which stacks exist, who owns them, and when they expire is rebuilt from Git (Section 3) — so a missing/unreachable Argo connection degrades live status only, never the reaper or My stacks.

**Progress UI (Slack)**: on submit, one thread message is **edited in place** as state changes — per-component rows for small stacks, summarized counts for large ones, plus an overall line. On completion, a distinct **"✅ Deployment complete"** message **@-mentions the owner** with ArgoCD links and any ingress URLs; on failure, **"❌ <component> failed"** with the Argo link. Lifecycle events (expiry warning, destroy) post to the same thread.

**Infra-ready (event-driven)**: the status model is push/event-driven throughout. For infra (Step 3) the StatusSource is the **infra/CI pipeline emitting events** — e.g. AWS **EventBridge** (or a GitHub Actions webhook) on plan/apply state changes → a small forwarder → the bot's webhook — rendered as stages (plan → approve → apply → outputs) in the same thread with the same live-message UI. Same push shape, different source; detailed in the Step 3 spec.

## 11. Access guardrails
Pluggable **authz provider**, **default deny**. Config holds a list of **allow rules**; each names a **principal** and an optional environment scope:
- Principal is one of: `user` (Slack user ID), `group` (Slack usergroup ID), or `channel` (anyone who is a member). A rule may set `admin: true`.
- `environments` scopes the rule (default: all). A user's access is the **union** of environments from all matching rules.

On each interaction the bot resolves the requester's identity, **usergroup memberships** and **channel memberships** (Slack API, cached ~5 min), evaluates rules, and:
- Denies with an ephemeral message if no rule matches.
- Filters the form's **environment dropdown** to allowed environments, and re-checks on submit (defense in depth).
- **Admins** may manage others' stacks and use `ttl: none`; non-admins manage only their own stacks.

Stacking with other guardrails (Section 12 / 13): per-environment `requiresApproval`, `autoMerge`, and TTL default/max; `maxLivePerUser`; owner-only extend/destroy. "Allowed in prod" still hits the prod reviewer when `requiresApproval` is set.

Caveats: **Slack usergroups require a paid plan** — on a free homelab workspace, use user IDs or channel membership. Needs Slack scopes `usergroups:read` and conversation/channel member reads. Membership is cached with a short TTL.

## 12. Lifecycle
States (per stack): `Requested → Deploying → Running → Expiring → Destroying → Destroyed`. `Failed` is reachable from any active state. A stack's N components warn, extend, and reap **as one unit**. The bot runs as a **single replica** (Socket Mode is a single consumer).
- Default TTL is 24h, capped per capability. `ttl: none` only for admins (Section 11).
- `expiresAt` lives in each component file's `deployground:` block (the durable record); the reaper reads live stacks from the Git-backed index (Section 3), not a database.
- The reaper runs every minute. 1h before expiry it DMs the owner and posts in the thread with Extend 24h / Keep / Destroy now buttons. At expiry it calls `destroy()` and opens a delete PR.
- Extend rewrites `expiresAt` in **every component file of the stack** in one atomic PR. Keep clears the warning without changing the date. `warnedAt` is kept in memory (not persisted), so a restart risks at most one duplicate warning. A per-user cap limits live stacks. Failed destroys are retried and alert the owner.

**Early destroy** (before expiry): handled by a **Destroy button**, not a form — there are no inputs to collect, only a selection and a confirmation.
- Entry points: the **Destroy** button on each **My stacks** row, and the **Destroy now** button on the expiry warning. (No separate destroy form or picker in Step 1.)
- **Always confirms**: the button opens an ephemeral Confirm/Cancel step naming the stack, environment, and component count (e.g. "Destroy `pool-party` in `homelab`? Removes 20 components."). This guards against fat-fingering, including for in-progress and multi-component stacks.
- **Authorization**: only the stack **owner** or an **admin** may destroy it (Section 11); others get a denial.
- **Flow**: Confirm → `destroy()` opens a delete PR → reconciler prunes → the **same Slack thread** updates `Destroying → Destroyed`, using the same live-message UI as deploy, so teardown shows progress too.

## 13. Configurability and open-source readiness
Principle: nothing company- or user-specific is hardcoded; the project is adoptable by any team through configuration alone, with defaults that work for a homelab.

Config is layered, lowest to highest precedence, nothing hand-edited on a machine:
1. **Built-in defaults** compiled in, enough for a homelab.
2. **Structured config** from the deployer: Helm `values.yaml` (rendered to a ConfigMap) or an equivalent mounted source.
3. **Environment variable overrides** for any key.
4. **Secrets** only from env vars or a secret store (External Secrets, Vault, a cloud secret manager), referenced by name, never inlined.

The app validates merged config at startup and refuses to start on errors; it hot-reloads non-secret config (including the stack catalog) without a restart. A remote config source (loaded from Git, so changes go through PRs) is a later option behind an interface.

What the config covers:
- **Repos**: every repo the bot touches, named in config — the GitOps/target repo(s), the IaC repo (Step 3), and source repos/allowlist (Step 2). Each entry: host, owner/name, branch, auth reference.
- **Environments**: which repo, branch, and path each maps to; per-environment TTL default/max, `requiresApproval`, `autoMerge`.
- **Deploy target**: pluggable; `applicationset` (default) or `application` CR mode; the target repo + path template, shared by the generated ApplicationSet and the writer (no default location).
- **Stacks catalog**: stack definitions (components, default versions, run settings) — hot-reloaded, no source-repo files.
- **Capabilities**: enabled flags, allowed registries, ingress domains, TTL policy.
- **Access**: `access.rules` (Section 11), default deny.
- **Status source**: configured **per Argo instance/environment** — `argocd-notifications` (default; webhook path + the subscription annotation stamped on generated Apps) or `argocd-api` (pull; endpoint + token ref + poll interval).
- **Lifecycle**: default TTL, warn lead time, reaper interval, per-user cap.
- **Git host**: provider interface, GitHub first.
- **State**: **no database.** The durable record is the `deployground:` block in each component file (Section 8); on startup the bot rebuilds an in-memory index by listing/parsing those files in Git (its Git credentials only — no DB, no cluster access, topology-agnostic). `warnedAt` is in-memory; single replica.
- **Chat layer**: behind an interface so other chat tools can be added later; only Slack is built.

Secrets (Slack tokens, Git credentials, any status/API tokens) come from env vars or mounted secrets; config holds only references (e.g. `tokenEnv: GITHUB_TOKEN`).

Example config (illustrative placeholders):
```yaml
git:
  provider: github
  repos:
    gitops-nonprod: { owner: my-org, name: gitops-nonprod, branch: main, authRef: {tokenEnv: GITHUB_TOKEN_NONPROD} }
    gitops-prod:    { owner: my-org, name: gitops-prod,    branch: main, authRef: {tokenEnv: GITHUB_TOKEN_PROD} }
    iac:            { owner: my-org, name: homelab-iac,    branch: main, authRef: {tokenEnv: GITHUB_TOKEN} }   # Step 3
    sources:        { allow: ["my-org/*"] }                                                                     # Step 2

deployTarget:
  type: applicationset            # or: application
  repo: gitops-nonprod            # which configured repo
  pathTemplate: "{env}/{stack}/{component}/config.yaml"   # no built-in default; set here

environments:
  homelab: { cluster: homelab, repo: gitops-nonprod, autoMerge: true,  ttl: {default: 24h, max: 168h}, requiresApproval: false }
  prod:    { cluster: prod,    repo: gitops-prod,    autoMerge: false, ttl: {default: none},            requiresApproval: true }

stacks:
  api:
    components:
      api: { image: ghcr.io/my-org/api, defaultVersion: v1.2.3, port: 8080 }
  pool-party:
    components:
      api:    { image: ghcr.io/my-org/api,    defaultVersion: v1.2.3, port: 8080 }
      web:    { image: ghcr.io/my-org/web,    defaultVersion: v4.5.6, port: 3000 }
      worker: { image: ghcr.io/my-org/worker, defaultVersion: v2.0.0 }

capabilities:
  image-deploy:
    enabled: true
    allowedRegistries: ["ghcr.io/my-org"]
    ingressDomains: ["home.example.com"]

access:
  rules:
    - { group:   S012ADMIN,  environments: ["*"], admin: true }   # paid Slack usergroup
    - { channel: C0DEPLOY,   environments: [homelab] }            # #deployground-users
    - { user:    U012ABC,    environments: ["*"] }
    - { group:   S0PRODTEAM, environments: [prod] }
  # default: deny

statusSource:
  type: argocd-notifications      # default; or: argocd-api
  webhookPath: /argocd/notifications
  # argocd-api fallback:
  # type: argocd-api
  # url: https://argocd.example.com
  # tokenRef: {tokenEnv: ARGOCD_TOKEN}
  # pollInterval: 15s

lifecycle: {warnBefore: 1h, reaperInterval: 1m, maxLivePerUser: 10}
# No store: durable state is the deployground: block in each component file in Git;
# the bot rebuilds an in-memory index from those files on startup.
```

Open-source deliverables:
- Apache-2.0 license; project name Deployground (no Slack trademark; not tied to one chat tool).
- Slack app manifest (quick workspace setup).
- Helm chart for the bot (no database component); a `deployground bootstrap` output for the ApplicationSet + `argocd-notifications-cm` snippet; an example golden-path Helm chart.
- Capabilities and deploy targets / status sources packaged as plugins (Python entry points).
- Example config, quickstart docs, contributing guide, CI running unit + integration tests.
- No references to any employer's repos, naming, or infrastructure in code, docs, or examples.

Before publishing: confirm with your employer, if applicable, that there is no IP or policy conflict.

## 14. Error handling
- Validation errors appear inline in the modal.
- A PR / Git API failure marks the stack Failed in-thread; one PR per stack means no half-written stacks.
- Sync or health failure is reported in-thread with the Argo link; rollback is a revert PR.
- Handlers are idempotent, keyed by stack/component ID, so retried Slack or status events do nothing twice.

## 15. Testing
- Unit: schema→modal (defaults, required, prefill, override reveal), version resolution, `render()`/`destroy()` snapshots (single + multi-component), TTL/extend logic, authz rule evaluation (user/group/channel, env scoping, default deny).
- Integration: fake Git + status events through the core with a fake clock; ApplicationSet vs Application-CR deploy targets; push vs pull status sources; stack-level aggregation.
- End-to-end: deploy `api` and `pool-party` (with overrides) to the homelab with a short TTL; watch live progress, completion ping, and reaping of all components.

## 16. Assumptions to confirm
- **Language: Python + slack-bolt (decided.)** GitHub as the Git host.
- **No database and no cluster read-credentials for core state.** The durable record is the `deployground:` block in each Git component file; the bot rebuilds an in-memory index from Git on startup. Single replica.
- A GitOps/target repo + path (configurable, no default) the company's ArgoCD watches via the bootstrapped ApplicationSet.
- The bot runs as a k8s Deployment, itself deployed via GitOps; it can run anywhere with outbound access to Git and Slack — same cluster as ArgoCD, another account, or a homelab.
- ArgoCD is available; the **status** connection is configured per Argo instance — push (ArgoCD Notifications, the default, operator applies the shipped snippet) or pull (Argo API) — and affects live status only, never core state.

## 17. Extension points (Steps 2–3 and beyond)
- `repo` capability: build from a repo + per-PR previews (Step 2); `infra` capability: IaC via CI with an event-driven (EventBridge/webhook) StatusSource (Step 3).
- Chained capabilities (repo → image → deploy; infra → service); multi-stage requests with approval gates; external triggers (GitHub PR events) that create/destroy stacks.
- Flux / raw-manifest deploy targets; OIDC / other authz providers; per-stack access scoping.
- Version inputs populated from the container registry's tags; day-2 per-component version bump from "My stacks".
