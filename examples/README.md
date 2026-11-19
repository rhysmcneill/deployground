# Example config

[`config.yaml`](config.yaml) is one complete, working config, annotated to show every real
combination supported today. Copy it and delete what you don't need. See
[Configuration](../docs/configuration.md) for the prose explanation of each section.

What each piece of it demonstrates:

| Section | Shows |
|---|---|
| `git.repos` | Two repos (nonprod/prod), each with its own token env var |
| `deployTarget` | `type: applicationset` (the default); the comment shows the one-line swap to `type: application`, and the commented `workloadChart` block shows overriding the default chart |
| `environments.homelab` | Direct-cluster destination (`https://kubernetes.default.svc`), auto-merge on, no approval |
| `environments.prod` | A named registered cluster, auto-merge off, `requiresApproval: true`, no TTL (`ttl: { default: none }`) |
| `catalog.api` | The minimal golden-path component: `image`/`defaultVersion`/`port` |
| `catalog.my-etire-stack.api` | A second golden-path component alongside the first, in one catalog entry |
| `catalog.my-etire-stack.web` | Golden-path's `ingressHost` with the `{stack}` placeholder; the commented alternatives show `ingressClassName`-only (host defaults to the bare stack name) and the private-registry/IRSA fields (`imagePullSecrets`, `serviceAccountName`) |
| `catalog.my-etire-stack.worker` | Golden-path's `env`/`secretRefs` — plain env vars plus envFrom from a pre-existing Secret |
| `catalog.custom-chart-example.myservice` | A custom OCI chart, `imageRef`, and `overridable` including `chart.version` |
| `catalog.custom-chart-example.myservice-git` | The same, but `type: git` (so `chart.version` resolves to `gitRef` instead), plus `ingressRef` — and a note on why `{stack}` does **not** get substituted into custom-chart `values` the way it does for golden-path's `ingressHost` |
| `capabilities.image-deploy` | `allowedRegistries`/`ingressDomains` guardrails |
| `access.rules` | Admin usergroup, a channel scoped to one environment, a user scoped to all, a usergroup scoped to prod |
| `statusSource` | The default push (`argocd-notifications`) with webhook auth; the commented alternative shows the pull-based `argocd-api` shape |
| `lifecycle` | TTL warning window, reaper interval, per-user live-stack cap |

Validate it (or your own copy) against the real schema with:

```bash
deployground bootstrap --config examples/config.yaml
```
