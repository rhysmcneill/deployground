# Bootstrapping Argo CD

## Generate the manifests

`deployground bootstrap` **only prints** manifests, each preceded by `# --- <name> ---`, so you
can review them. Nothing is applied for you:

```bash
deployground bootstrap --config my-config.yaml > bootstrap.yaml
# review, then apply to the Argo CD cluster/namespace
kubectl apply -f bootstrap.yaml
```

For the default `applicationset` target you get:

- One **`ApplicationSet`** per environment. Each targets that environment's cluster and watches
  only its own subtree of the component path.
- An **`argocd-notifications-cm`** ConfigMap defining the `deployground` webhook service,
  templates, and the `on-deployed` / `on-health-degraded` / `on-sync-failed` triggers.

## Applying each piece

**The notifications ConfigMap is never something to `kubectl apply` directly, and never
something the chart automates.** `argocd-notifications-cm` is normally already owned by however
you installed Argo CD itself (e.g. the `argo-cd` Helm chart's own `notifications:` values).
Applying it separately would work until the next time you upgrade that install, which would
silently wipe your changes back out. **Merge its `data` keys into that install's own config
instead** — once; re-merge only if you change `statusSource`/triggers later.

The **`ApplicationSet`** has no such owner — it's uniquely deployground's own resource — so it's
safe to apply directly, and the chart can do it for you: set `bootstrap.enabled: true` to run
`deployground bootstrap --only applicationset` as a PreSync hook on every sync, via a dedicated,
narrowly-scoped ServiceAccount (create/update on ApplicationSets only — the bot's own pod is
never granted this). It's off by default: review the ApplicationSet by hand once before turning
it on, since enabling it means no human reviews it before each apply thereafter.

## The workload chart

The default workload chart is published at `oci://ghcr.io/rhysmcneill/golden-path-chart` and is
pulled automatically — no manual copy into the target GitOps repo is needed. Point
`deployTarget.workloadChart` at your own chart (`type: oci` or `type: git`) to use a different
default; see [`helm/charts/golden-path/README.md`](../helm/charts/golden-path/README.md). (A
single component can also pick its own chart independently of this default — see
[Configuration](configuration.md#custom-charts-per-component).)

## Status connectivity

How deploy progress reaches Slack is set per Argo CD via `statusSource`:

- **Push (default), `type: argocd-notifications`**: Argo CD Notifications POSTs to the bot's
  webhook (`statusSource.webhookPath`, served on `DG_WEBHOOK_PORT`). Needs network from Argo CD
  to the bot's Service. Best when the bot runs in or next to the Argo cluster.
  `statusSource.webhookUrl` is the base URL (scheme + host [+ port], no path) Argo uses; the path
  is appended. It defaults to the in-cluster Service DNS
  (`http://deployground.deployground.svc.cluster.local`); the Helm chart fills in your own
  release/namespace automatically. If Argo CD runs in a different cluster from the bot, set it to
  an ingress / externally-reachable URL (e.g. `https://deployground.example.com`).
- **Pull, `type: argocd-api`**: the bot polls Argo CD's API. Set `url`,
  `tokenRef: {tokenEnv: ARGOCD_TOKEN}`, and `pollInterval`. Needs network from the bot to Argo CD,
  and no notifications setup; use it when Argo cannot reach the bot.

The webhook URL the ConfigMap uses is `<statusSource.webhookUrl><webhookPath>`.

### Webhook authentication

Set `statusSource.secretRef: {tokenEnv: DG_WEBHOOK_TOKEN}` to require every webhook request to
carry an `X-Deployground-Token` header equal to that env var's value. The bootstrap ConfigMap
then adds that header, sourced from the key `deployground-webhook-token` of
`argocd-notifications-secret`. Set it to the same value as the bot's token:

```bash
kubectl -n argocd patch secret argocd-notifications-secret --type merge \
  -p '{"stringData":{"deployground-webhook-token":"<same value as DG_WEBHOOK_TOKEN>"}}'
```

Without `secretRef` the endpoint is unauthenticated; restrict it with a NetworkPolicy.

**Next:** [Deploying from Slack](deploying.md).
