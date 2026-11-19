# Installing the bot

## Create the Secret

Create the Secret with every env var the chart and config need:

```bash
kubectl create namespace deployground
kubectl -n deployground create secret generic deployground-secrets \
  --from-literal=SLACK_BOT_TOKEN=xoxb-... \
  --from-literal=SLACK_APP_TOKEN=xapp-... \
  --from-literal=GITHUB_TOKEN_NONPROD=... \
  --from-literal=GITHUB_TOKEN_PROD=... \
  --from-literal=DG_WEBHOOK_TOKEN=$(openssl rand -hex 24)
```

This is a one-time manual create for a brand-new Secret nothing else manages yet — fine for a
quick start. In a GitOps-managed cluster, it's recommended to provision it the same way you
handle every other secret instead: an `ExternalSecret` (External Secrets Operator) pulling from
your actual secret manager (Vault, Infisical, AWS/GCP Secrets Manager, etc.), or Sealed Secrets.
`existingSecret` just needs to name whatever Secret ends up with these keys — deployground
doesn't care how it got created.

The chart reads the Secret named by `existingSecret` (default `deployground-secrets`) via
`envFrom`, so key names are the env var names your config's `authRef`/`tokenRef` entries point at.

## Install with Helm

Put your config under `config:` in a values file (it is rendered to a ConfigMap mounted as
`--config`) and optionally pin `image.tag`. Multi-arch images (amd64/arm64) are published to
`ghcr.io/rhysmcneill/deployground:<version>` on each release (the tag defaults to the chart
`appVersion`); or build locally with the repo `Dockerfile`.

The chart itself is also published, as an OCI artifact at
`oci://ghcr.io/rhysmcneill/deployground-chart`:

```bash
helm install deployground oci://ghcr.io/rhysmcneill/deployground-chart --version <version> \
  -n deployground -f my-values.yaml
```

Or from a local checkout (e.g. a fork, or a specific git ref):

```bash
helm install deployground helm/charts/deployground -n deployground -f my-values.yaml
```

## Non-secret env vars

| Var | Default | Purpose |
|---|---|---|
| `DG_WEBHOOK_PORT` | `8080` | Port the webhook server listens on |
| `DG_WEBHOOK_HOST` | `0.0.0.0` | Bind address |

## Operational notes

- Every deploy's whole lifecycle (progress, completion, extend, destroy) lives in the deploying
  user's own Messages Tab — there is no shared channel to configure.
- The bot runs as a **single replica**; it rebuilds its in-memory index from Git on startup.
  There's no database.
- Send `SIGHUP` (or restart) to reload config. The Helm chart restarts the pod automatically
  when the config changes.

**Next:** [Bootstrapping Argo CD](bootstrap-argocd.md).
