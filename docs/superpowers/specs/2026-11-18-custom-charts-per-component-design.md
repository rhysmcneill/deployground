# Custom Helm Charts Per Component — Design

## Intent

Today every catalog component renders through the one built-in `golden-path` chart, with a
fixed, typed field set (`image`, `port`, `replicas`, `ingressHost`, `env`, `secretRefs`,
`imagePullSecrets`, `serviceAccountName`, `ingressClassName`). Real services often need more
configuration than that chart exposes, or already have their own Helm chart. Goal: let a
catalog component point at an arbitrary chart (OCI or git, private or public) instead of the
built-in one, while keeping the Slack deploy form exactly as abstracted from Helm/chart
internals as it is today — the deploying user still only ever picks a catalog entry and
tweaks a short list of named fields, never touches a values file or a chart's schema.

## Goals

- A catalog component can reference any chart (OCI/git registry, any repo), not just the
  built-in golden-path chart.
- The operator who adds that catalog entry supplies the chart's own values (as much or as
  little as needed) once, at config-authoring time.
- The Slack form stays schema-agnostic: it renders whatever fields the operator declared as
  user-facing for that component, nothing more — identical mechanism whether the component
  uses golden-path or a custom chart.
- The deploying user can still override an image tag/version per deploy (today's only
  override), and — new — optionally the chart version itself, and optionally any other
  field the operator chose to expose, all without knowing the chart's values shape.
- The existing `allowedRegistries` guardrail keeps working for custom-chart components, not
  just golden-path ones.
- Zero changes required to existing golden-path-backed catalog entries or to how the bot
  authenticates to anything — this is additive.

## Non-goals

- A generic "point the form at an already-prepared values file" escape hatch. Rejected as
  the primary mechanism (it pushes MORE chart-schema knowledge onto the deploying user, not
  less); could exist later as a power-user escape hatch, out of scope here.
- Automatic chart-schema discovery/validation (e.g. validating the operator's override block
  against a chart's `values.schema.json`). Worth doing later as a safety net for whoever
  authors the catalog entry; not required for this feature to work.
- New bot-level credential handling for private chart registries (ECR/ACR/private
  GHCR/private git) or private image registries. Both are already-solved, existing
  mechanisms (see Constraints) — this feature doesn't add new auth surface.
- Migrating existing golden-path catalog entries to any new shape. They keep working exactly
  as they do today.

## Anti-goals

- A deploying user ever needing to read or understand a chart's `values.yaml` to deploy it.
- The Slack form growing chart-specific code paths (one branch per chart "kind"). The form
  must stay one generic renderer over whatever fields config declares.
- Weakening `allowedRegistries`/`ingressDomains` enforcement for custom-chart components
  relative to golden-path ones.

## Constraints

- **Private chart repo access is an ArgoCD concern, not a deployground one.** ArgoCD's
  repo-server is what actually fetches and renders the chart (deployground only ever writes
  a `repoURL`/`chart`/`targetRevision` reference, same as it does for golden-path today via
  `chart_source()`). A private chart repo (ECR/ACR/private GHCR/private git) needs its
  credentials registered with ArgoCD itself (`argocd repo add`, or a labelled repo-creds
  Secret) — a one-time cluster-operator task, independent of this feature, identical to what
  would already be required if golden-path's own registry were private.
- **Private image registry access is already solved.** `imagePullSecrets` (a list of
  pre-existing `kubernetes.io/dockerconfigjson` Secret names, provisioned by the operator —
  e.g. via the `ClusterExternalSecret` pattern already in use) is already a first-class
  per-component field and concept. A custom chart just needs its own `imagePullSecrets`-
  equivalent values key populated the same way; almost every chart follows this exact
  convention already.
- **The registry guardrail needs a reliable place to read "the image."** `ResolvedComponent.image`
  is a typed Python field today, read directly by `ImageDeployCapability._check_guardrails()`.
  A custom chart's image reference lives at an arbitrary, chart-specific path inside its
  values — there is no field deployground can assume. The guardrail only ever needs the
  **repository portion** of the image (not the tag — the registry can't change by bumping a
  version), and that portion is always operator-set, never user-overridable per deploy.

## Builder-check findings and resolutions

A fresh-eyes build-readiness check on this doc surfaced three gaps, resolved here:

1. **Ingress guardrail gap**: the Anti-goals section promises `ingressDomains` enforcement
   keeps working for custom-chart components, but the Decided design only added `image.path`
   (for `allowedRegistries`), nothing analogous for ingress. Resolved: add an `ingress.path`
   field mirroring `image.path` — optional (only required when the chart actually renders an
   Ingress), static/not user-facing, read by `ImageDeployCapability._check_guardrails()` the
   same way `image.path` is. If a component declares no `ingress.path`, there's nothing to
   check, same as a golden-path component with no `ingressHost` set today. If the operator
   also wants the host itself user-overridable, they add an `overridable` entry pointing at
   the same path — no separate mechanism needed.
2. **Config-load-time validation gap**: today `allowedRegistries` is also enforced at
   config-load time (`Config.validate_refs()`), not just at deploy time, for golden-path's
   typed `image` field. Resolved: `validate_refs()` is extended to read `image.path` out of a
   custom-chart component's static `values` the same way, so custom-chart operators keep the
   same fail-fast config-validation UX golden-path operators get, instead of only finding out
   about a disallowed registry at deploy time.
3. **The override data model** (highest-impact finding, resolved by explicit choice — see
   "Decided design: unified overrides" below): today's override plumbing is a single flat
   `dict[component, version]` threaded through `RenderInputs`, `Catalog.resolve_versions()`
   and the Slack modal's one-block-per-component scheme. `overridable` being a *list* per
   component (several independently-overridable fields at once) doesn't fit that shape.
   Decision: **unify** rather than keep two parallel override mechanisms (golden-path's old
   single-value one and a new multi-field one for custom charts) — chosen explicitly for
   scalability as more chart types/fields get added later, at the cost of a larger one-time
   diff now across `RenderInputs`, `Catalog`, and the Slack form/action handlers.

## Decided design: unified overrides

Every component's overrides become `dict[path, value]` (previously `version: str`), and the
per-stack override payload becomes `dict[component, dict[path, value]]` (previously
`dict[component, version]`), used identically by both golden-path and custom-chart
components — no branching between "golden-path-style" and "custom-chart-style" overrides
anywhere in `RenderInputs`, `Catalog.resolve_versions()`, or the capability layer.

- **Golden-path components need no catalog-config changes.** When a component has no
  `overridable` list declared (i.e. doesn't use a custom chart), deployground treats it as
  having exactly one implicit entry: `{path: "version", label: "Version"}` — reproducing
  today's exact behavior and today's exact Slack modal (one "Version" field) through the
  same generalized code path, not a special case of it.
- **The Slack form is unaffected in appearance** (see the worked mockups already agreed):
  it renders one field per `overridable` entry per component, whether that list has one
  implicit entry (golden-path) or several explicit ones (custom chart).
- **Block-id scheme**: today's `OVERRIDE_PREFIX + component` key becomes
  `OVERRIDE_PREFIX + component + "__" + path` (double underscore, not a dot, so it can't be
  confused with a dot-path's own separators when parsed back out of the submitted block ids).
- **`Catalog.resolve_versions()`** changes signature from `dict[component, version]` to
  `dict[component, dict[path, value]]`, and is responsible for: for each component, starting
  from its chart's resolved `values` (golden-path's typed fields, or a custom chart's
  `values` block), applying each submitted `{path: value}` onto a deep copy before producing
  that component's `ResolvedComponent`/render payload. Golden-path's single `version` path is
  handled by the exact same dot-path-apply logic as any custom-chart path — `version` is just
  the name of the (only) field golden-path happens to expose.

## Approaches considered

1. **Operator-declared values template with a fixed placeholder set** (e.g. only `{{version}}`
   substituted into an operator-written values blob). Simple, but doesn't generalize to
   "the operator wants more than one user-facing override" without inventing more special
   placeholders one at a time.
2. **Built-in adapters per chart family** (e.g. a Bitnami-shaped mapper hardcoded into
   deployground). Rejected: real maintenance burden, only pays off at a scale this project
   doesn't operate at.
3. **Operator declares a generic list of overridable dot-paths per component, chosen.**
   The operator writes the chart ref + base values (merged by Helm on top of the chart's own
   `values.yaml` defaults — no deployground-side schema merging needed) plus a short list of
   `{path, label}` entries marking which values (or the chart version itself) the Slack form
   exposes as named, user-facing fields. The form renders generically over whatever list is
   declared, identical code path for golden-path and custom-chart components (golden-path's
   existing "version" override becomes just the default entry in this same list).

Approach 3 was chosen: it's the only one that keeps the form fully schema-agnostic while
letting different components expose different override surfaces, with no new code per chart.

## Decided design

**Per-component config, additive — existing `Component` shape is untouched for golden-path
components.** A component that wants a non-default chart declares a parallel block, e.g.:

```yaml
components:
  myservice:
    chart:
      type: oci                 # or git, mirrors deployTarget.workloadChart's existing shape
      repo: ghcr.io/my-org
      chart: myservice-chart
      version: "1.4.0"          # the chart's own version — may itself be declared overridable below
    values:                     # operator-authored base values; layered by Helm on top of
      replicas: 2                # the chart's own packaged values.yaml defaults, same as any
                                   # `helm install -f partial-values.yaml` composition
    image:
      path: image.repository    # REQUIRED, not user-facing: where the image repo reference
                                  # lives in `values`, so the registry guardrail can read it
    overridable:                 # what the Slack form renders as named fields for this component
      - path: image.tag
        label: "Version"
      - path: chart.version
        label: "Chart version"
      - path: replicas
        label: "Replicas"
```

- `chart` mirrors the existing `deployTarget.workloadChart` config shape (type/repo/chart/
  version, or git fields) — this is not a new concept, just moved from a single global
  default to optionally-per-component.
- `values` is whatever the operator writes for that chart; deployground never inspects or
  validates its shape beyond what `image.path` and `overridable[].path` point at.
- `image.path` is required whenever `chart` is set (i.e. whenever not using golden-path) and
  is never user-facing — deployground reads the value at that path from the operator's own
  `values` block and runs it through `allowedRegistries`, exactly as `comp.image` does today.
- `overridable` is the generalization of today's hardcoded "version" override: the Slack
  form, for each catalog entry, renders one field per `overridable` entry across its
  components (golden-path components keep exactly today's one entry, `image`/version,
  defined implicitly so no existing catalog entry needs to change). A `path` can target
  either `values` (dot-path into the component's values block) or `chart.version` (the one
  reserved path meaning "override the chart's own `targetRevision`").
- At render time, deployground applies the resolved overrides onto a deep copy of `values`
  (and onto `chart.version` if that path was targeted) before writing the component's file —
  no change to the multi-source Application/ApplicationSet template mechanism itself, since
  the chart source is already a config-resolved `chart_source()`-shaped dict today; it just
  resolves per-component instead of only from the global `deployTarget.workloadChart`.

## What's unaffected

- `deployTarget.workloadChart` stays the global default for any component with no `chart`
  block (all existing catalog entries).
- TTL/lifecycle, the reserved `system` pseudo-component and namespace cleanup, and
  notifications are all orthogonal and untouched. `ingressDomains` enforcement itself is
  explicitly preserved (via `ingress.path`, above), not untouched in the sense of "unaddressed."
- The Slack form's existing Catalog/Stack-name split is unaffected; the per-component
  override UI is generalized (one field per `overridable` entry, golden-path's implicit
  single entry included) rather than replaced — a golden-path-only config renders and
  behaves identically to today.

## Left to the plan

Exact Python model names/types for `chart`/`values`/`image`/`ingress`/`overridable` (likely
new pydantic models alongside `Component` in `config/models.py`), the dot-path get/set
helper used by both `resolve_versions()` and the two guardrail paths, the exact Slack Block
Kit field type per declared override (text input for now; typed inputs are a future
extension), and how `ImageDeployCapability._check_guardrails()` and `Config.validate_refs()`
each read `image.path`/`ingress.path` generically for custom-chart components while keeping
golden-path's existing typed-field path unchanged (two readers, one dot-path helper, not two
guardrail implementations).
