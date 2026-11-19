# Quickstart

Deployground is a Slack bot that deploys through GitOps: it writes one config file per component
to your GitOps repo, and an Argo CD ApplicationSet turns each file into a running workload. There
is no database; state is the `deployground:` block in each file.

You need: a Kubernetes cluster with Argo CD (and optionally Argo CD Notifications), a GitOps
repo, a GitHub token per repo, and a Slack workspace where you can install apps.

## 1. Slack app (one-time, manual)

Steps 1-3 are manual because Slack requires a human to consent to the install. With Socket Mode
there is **no** public URL, signing secret or event endpoint to configure.

1. Go to <https://api.slack.com/apps> -> *Create New App* -> *From an app manifest*, pick your
   workspace, and paste `deploy/slack-app-manifest.yaml`. It enables Socket Mode, the App Home tab,
   interactivity, the global shortcut `open_deploy_form`, and the bot scopes `chat:write`,
   `commands`, `usergroups:read`, `users:read`, `users:read.email`, `im:write`, `channels:read`,
   `groups:read`.
2. *Basic Information* -> *App-Level Tokens* -> generate a token with the `connections:write`
   scope. This is the **app token** (`xapp-...`).
3. *Install App* -> *Install to Workspace*. Copy the **bot token** (`xoxb-...`).
4. Put both into the Secret the chart reads (see below) as `SLACK_APP_TOKEN` and
   `SLACK_BOT_TOKEN`.

## 2. Configuration

Copy `examples/config.yaml` and edit it: your repos, environments (each one's Argo CD
destination -- `https://kubernetes.default.svc` if Argo CD manages its own cluster, or a
registered cluster name for a multi-cluster Argo CD -- plus TTL, `autoMerge`,
`requiresApproval`), the catalog (reusable component definitions), `capabilities.image-deploy.allowedRegistries`
and `ingressDomains`, and `access.rules` (Slack user, usergroup or channel IDs; default deny).
Config holds only the **names** of env vars for secrets (`authRef: {tokenEnv: GITHUB_TOKEN_NONPROD}`).
Any key can also be overridden by an env var `DEPLOYGROUND_<PATH>` with `__` between segments,
e.g. `DEPLOYGROUND_LIFECYCLE__MAXLIVEPERUSER=5`.

The component name `system` is reserved by deployground itself -- it owns each stack's
namespace lifecycle so `destroy` can clean the namespace up -- and cannot be used as a real
catalog component name; config validation rejects it.

### Custom charts per component

Most components just set `image`/`defaultVersion`/`port`/... and render through the built-in
golden-path chart (above). A component can instead point at its own Helm chart -- useful when a
service already ships its own chart, or needs more than golden-path's fixed field set -- by
declaring `chart`/`values` instead of `image`:

```yaml
myservice:
  chart:
    type: oci # or: git, same shape as deployTarget.workloadChart
    repo: ghcr.io/my-org
    chart: myservice-chart
    version: "1.4.0" # the chart's own version -- may itself be made overridable below
  values: # operator-authored values, layered by Helm on top of the chart's own
    replicas: 2 # values.yaml defaults -- deployground never inspects this shape
  imageRef:
    path: image.repository # REQUIRED: where the image repo lives in `values`, so
    # allowedRegistries can still read it
  overridable: # fields the Slack form renders as named, per-deploy overrides
    - { path: image.tag, label: "Version" }
    - { path: chart.version, label: "Chart version" }
    - { path: replicas, label: "Replicas" }
```

`chart` mirrors `deployTarget.workloadChart`'s shape (`type: oci` with `repo`/`chart`/`version`,
or `type: git` with `gitRepo`/`gitRef`/`chartPath`). `values` is whatever the operator writes for
that chart -- deployground only ever reads the specific paths named by `imageRef`, `ingressRef`
(optional, same idea as `imageRef` but for the field an Ingress resource reads its host from) and
`overridable[].path`. A component sets either golden-path's `image` or a custom `chart`, never
both. `overridable` is the generalization of today's single per-deploy version override: a
golden-path component gets one implicit entry (`image`/version) with no config change needed; a
custom-chart component lists as many `{path, label}` entries as it wants exposed in the deploy
form, including `chart.version` itself.

`allowedRegistries` and `ingressDomains` still apply to custom-chart components exactly as they
do for golden-path ones -- deployground resolves the image/ingress host via `imageRef`/`ingressRef`
into `values` instead of reading the typed `image`/`ingressHost` fields directly.

Validate it and preview what will be applied (next section) with:

    pip install .            # provides the `deployground` command
    deployground bootstrap --config my-config.yaml

## 3. Install the bot (Helm)

Create the Secret with every env var the chart and config need:

    kubectl create namespace deployground
    kubectl -n deployground create secret generic deployground-secrets \
      --from-literal=SLACK_BOT_TOKEN=xoxb-... \
      --from-literal=SLACK_APP_TOKEN=xapp-... \
      --from-literal=GITHUB_TOKEN_NONPROD=... \
      --from-literal=GITHUB_TOKEN_PROD=... \
      --from-literal=DG_WEBHOOK_TOKEN=$(openssl rand -hex 24)

The chart reads the Secret named by `existingSecret` (default `deployground-secrets`) via
`envFrom`, so key names are the env var names. Put your config under `config:` in a values file
(it is rendered to a ConfigMap mounted as `--config`) and optionally pin `image.tag`.
Multi-arch images (amd64/arm64) are published to `ghcr.io/rhysmcneill/deployground:<version>` on
each release (the tag defaults to the chart `appVersion`); or build locally with the repo
`Dockerfile`. The chart itself is also published, as an OCI artifact at
`oci://ghcr.io/rhysmcneill/deployground-chart`:

    helm install deployground oci://ghcr.io/rhysmcneill/deployground-chart --version <version> \
      -n deployground -f my-values.yaml

or from a local checkout (e.g. a fork, or a specific git ref):

    helm install deployground helm/charts/deployground -n deployground -f my-values.yaml

Non-secret env: `env.DG_WEBHOOK_PORT` (default 8080), and optionally `DG_WEBHOOK_HOST`
(default `0.0.0.0`). Every deploy's whole lifecycle (progress, completion, extend, destroy)
lives in the deploying user's own Messages Tab -- there is no shared channel to configure.
The bot runs as a single
replica; it rebuilds its in-memory index from Git on startup. Send `SIGHUP` (or restart) to
reload config; the chart restarts the pod when the config changes.

## 4. Bootstrap Argo CD

`deployground bootstrap` **only prints** manifests, each preceded by `# --- <name> ---`, so you
can review them. Nothing is applied for you:

    deployground bootstrap --config my-config.yaml > bootstrap.yaml
    # review, then apply to the Argo CD cluster/namespace
    kubectl apply -f bootstrap.yaml

For the default `applicationset` target you get one `ApplicationSet` per environment (each
targets that environment's cluster and watches only its subtree of the component path) and an
`argocd-notifications-cm` ConfigMap defining the `deployground` webhook service, templates and
the `on-deployed` / `on-health-degraded` / `on-sync-failed` triggers.

**The notifications ConfigMap is never something to `kubectl apply` directly, and never
something the chart automates** — `argocd-notifications-cm` is normally already owned by
however you installed Argo CD itself (e.g. the `argo-cd` Helm chart's own `notifications:`
values). Applying it separately would work until the next time you upgrade that install,
which would silently wipe your changes back out. Merge its `data` keys into that install's own
config instead — once; re-merge only if you change `statusSource`/triggers later.

The `ApplicationSet` has no such owner (it's uniquely deployground's own resource), so it's
safe to apply directly, and the chart can do it for you: set `bootstrap.enabled: true` to run
`deployground bootstrap --only applicationset` as a PreSync hook on every sync, via a
dedicated, narrowly-scoped ServiceAccount (create/update on ApplicationSets only — the bot's
own pod is never granted this). It's off by default: review the ApplicationSet by hand once
before turning it on, since enabling it means no human reviews it before each apply
thereafter.

The default workload chart is published at `oci://ghcr.io/rhysmcneill/golden-path-chart` and is
pulled automatically — no manual copy into the target GitOps repo is needed. Point
`deployTarget.workloadChart` at your own chart (`type: oci` or `type: git`) to use a different
one; see `helm/charts/golden-path/README.md`.

The webhook URL in the ConfigMap is `<statusSource.webhookUrl><webhookPath>`; see section 5 for
`webhookUrl` (it defaults to the in-cluster Service DNS for release `deployground` in namespace
`deployground`).

## 5. Status connectivity (per Argo CD instance)

How deploy progress reaches Slack is set per Argo CD via `statusSource`:

- **Push (default), `type: argocd-notifications`**: Argo CD Notifications POSTs to the bot's
  webhook (`statusSource.webhookPath`, served on `DG_WEBHOOK_PORT`). Needs network from Argo CD
  to the bot's Service. Best when the bot runs in or next to the Argo cluster.
  `statusSource.webhookUrl` is the base URL (scheme + host [+ port], no path) Argo uses; the
  path is appended. It defaults to the in-cluster Service DNS
  (`http://deployground.deployground.svc.cluster.local`); the Helm chart fills in your own
  release/namespace automatically. If Argo CD runs in a different cluster from the bot, set it
  to an ingress / externally-reachable URL (e.g. `https://deployground.example.com`).
- **Pull, `type: argocd-api`**: the bot polls Argo CD's API. Set `url`, `tokenRef:
  {tokenEnv: ARGOCD_TOKEN}` and `pollInterval`. Needs network from the bot to Argo CD, and no
  notifications setup; use it when Argo cannot reach the bot.

### Webhook authentication

Set `statusSource.secretRef: {tokenEnv: DG_WEBHOOK_TOKEN}` to require every webhook request to
carry an `X-Deployground-Token` header equal to that env var's value. The bootstrap ConfigMap
then adds that header, sourced from the key `deployground-webhook-token` of
`argocd-notifications-secret`. Set it to the same value as the bot's token:

    kubectl -n argocd patch secret argocd-notifications-secret --type merge \
      -p '{"stringData":{"deployground-webhook-token":"<same value as DG_WEBHOOK_TOKEN>"}}'

Without `secretRef` the endpoint is unauthenticated; restrict it with a NetworkPolicy.

## 6. Deploying from Slack

The deploy form has two naming fields that are easy to confuse:

- **Catalog** (dropdown): *what to deploy*. It picks one entry from the `catalog:` block of the
  config, and the entry's components are listed so you can override individual versions.
- **Stack name** (free text): *what to call this deploy*. It is the name of your live deployment,
  lowercase letters, numbers and hyphens, and it becomes the namespace and the default URL
  (`{stack}` in an `ingressHost`). It does not have to match the catalog entry, so the same
  catalog entry can be deployed several times under different stack names.

## Not covered yet

Packaging capabilities, deploy targets and status sources as third-party plugins via Python
entry points is deferred. The factories (`make_deploy_target`, `make_status_source`) are the
seam, but entry-point discovery is a post-step-1 extension.
