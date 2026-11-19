# Example config

[`config.yaml`](config.yaml) is one complete, working config, annotated to show every real
combination supported today. Copy it and delete what you don't need. See
[Configuration](../docs/configuration.md) for the prose explanation of each section.

What each piece of it demonstrates:

| Section | Shows |
|---|---|
| `git.repos` | Two repos (nonprod/prod), each with its own token env var |
| `deployTarget` | `applicationset` (default); comments show `application` and a custom `workloadChart` |
| `environments.homelab` | Direct-cluster destination, auto-merge on, no approval |
| `environments.prod` | Named registered cluster, auto-merge off, approval required, no TTL |
| `catalog.api` | Minimal golden-path component: `image`/`defaultVersion`/`port` |
| `catalog.my-etire-stack.api` | A second golden-path component in the same catalog entry |
| `catalog.my-etire-stack.web` | `ingressHost` with `{stack}`; commented `imagePullSecrets`/`serviceAccountName` |
| `catalog.my-etire-stack.admin` | `ingressClassName` alone — host defaults to the bare stack name |
| `catalog.my-etire-stack.worker` | No ingress at all; `env`/`secretRefs` |
| `catalog.custom-chart-example.myservice` | OCI custom chart, `imageRef`, `overridable` incl. `chart.version` |
| `catalog.custom-chart-example.myservice-git` | Git-type chart (`chart.version` → `gitRef`), `ingressRef` |
| `capabilities.image-deploy` | `allowedRegistries`/`ingressDomains` guardrails |
| `access.rules` | Admin usergroup, a channel scoped to one environment, a user scoped to all, a usergroup scoped to prod |
| `statusSource` | The default push (`argocd-notifications`) with webhook auth; the commented alternative shows the pull-based `argocd-api` shape |
| `lifecycle` | TTL warning window, reaper interval, per-user live-stack cap |

Validate it (or your own copy) against the real schema with:

```bash
deployground bootstrap --config examples/config.yaml
```
