# Configuration

Copy [`examples/config.yaml`](../examples/config.yaml) and edit it. This page covers what each
part does; the example file is a complete, working starting point, annotated to show every
combination supported today (golden-path and custom-chart components, both deploy targets, both
status sources, ...) — see [`examples/README.md`](../examples/README.md) for a guided tour of
which part of the file shows which combination.

## Overview

A config file has:

- **`git`** — your repos and how to authenticate to them.
- **`environments`** — each one's Argo CD destination (`https://kubernetes.default.svc` if Argo
  CD manages its own cluster, or a registered cluster name for a multi-cluster Argo CD), TTL
  defaults, `autoMerge`, and `requiresApproval`.
- **`catalog`** — reusable component definitions deployers pick from.
- **`capabilities`** — guardrails per capability (today, `image-deploy`'s `allowedRegistries` and
  `ingressDomains`).
- **`access`** — who can deploy where (Slack user, usergroup, or channel IDs; default deny).

Config holds only the **names** of env vars for secrets, never the secrets themselves
(`authRef: {tokenEnv: GITHUB_TOKEN_NONPROD}`). Any key can also be overridden by an env var
`DEPLOYGROUND_<PATH>` with `__` between segments, e.g. `DEPLOYGROUND_LIFECYCLE__MAXLIVEPERUSER=5`.

The component name `system` is reserved by deployground itself — it owns each stack's namespace
lifecycle so `destroy` can clean the namespace up — and cannot be used as a real catalog
component name; config validation rejects it.

## The catalog

Each catalog entry is one or more named **components**. The simplest component renders through
the built-in golden-path chart:

```yaml
catalog:
  my-stack:
    components:
      web:
        image: ghcr.io/my-org/web
        defaultVersion: v1.0.0
        port: 8080
        ingressHost: "{stack}.example.com"
```

Deploying `my-stack` under the stack name `demo` would put it at `demo.example.com`.

### Custom charts per component

Most components just set `image`/`defaultVersion`/`port`/... as above. A component can instead
point at its own Helm chart — useful when a service already ships its own chart, or needs more
than golden-path's fixed field set — by declaring `chart`/`values` instead of `image`:

```yaml
myservice:
  chart:
    type: oci # or: git, same shape as deployTarget.workloadChart
    repo: ghcr.io/my-org
    chart: myservice-chart
    version: "1.4.0" # the chart's own version -- may itself be made overridable below
  values: # operator-authored values, layered by Helm on top of the chart's own
    image: { repository: ghcr.io/my-org/myservice, tag: "v2" }
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
both required explicitly — a custom chart can't inherit golden-path's own OCI coordinates by
leaving them unset — or `type: git` with `gitRepo`/`chartPath` required and `gitRef` optional).
`values` is whatever the operator writes for that chart — deployground only ever reads the
specific paths named by `imageRef`, `ingressRef` (optional, same idea as `imageRef` but for the
field an Ingress resource reads its host from) and `overridable[].path`. A component sets either
golden-path's `image` or a custom `chart`, never both.

`overridable` is the generalization of today's single per-deploy version override: a golden-path
component gets one implicit entry (`image`/version) with no config change needed; a custom-chart
component lists as many `{path, label}` entries as it wants exposed in the deploy form, including
`chart.version` itself.

`allowedRegistries` and `ingressDomains` still apply to custom-chart components exactly as they
do for golden-path ones — deployground resolves the image/ingress host via `imageRef`/`ingressRef`
into `values` instead of reading the typed `image`/`ingressHost` fields directly. For a custom
chart, that coverage is only as complete as the paths the operator declares: a chart with sidecar
images, or an Ingress configured some other way, has no deployground-visible path to check.

## Validate

Preview what will be applied (see [Bootstrapping Argo CD](bootstrap-argocd.md)) with:

```bash
pip install .            # provides the `deployground` command
deployground bootstrap --config my-config.yaml
```

This also validates the config — a bad reference (an `imageRef` path that doesn't exist in
`values`, an unknown repo name, the reserved `system` component name, and so on) fails loudly
here rather than at deploy time.

**Next:** [Installing the bot](install.md).
