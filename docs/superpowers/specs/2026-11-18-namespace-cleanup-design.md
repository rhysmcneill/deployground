# Namespace cleanup on destroy — Design

## Intent

Destroying a stack never deletes its namespace (`CreateNamespace=true` only ever creates).
A `ClusterExternalSecret` provisions `harbor-pull-secret` into every namespace labelled
`deployground.io/managed=true`, so it keeps re-provisioning that secret into namespaces
left behind by destroyed stacks. Goal: destroy deletes the namespace too, and everything
still in it goes with it (so any future dangling cluster-scoped resource is covered, not
just this one secret).

## Approach

Reuse the existing one-Application(or-manifest)-per-component machinery instead of adding
a dedicated generator/ApplicationSet. Every deployed stack gets one extra, reserved
"component" named `_system` whose file renders a bare `Namespace` object (via a
`namespaceOnly: true` toggle on the existing golden-path chart) instead of a workload.
It is written and removed by the exact same `files_for`/`delete_files_for` calls that
already handle every other component, so it is created and destroyed atomically with the
rest of the stack by construction — no new invariant to keep safe, no new ApplicationSet,
no new published chart, no user-facing config.

- `_system` is excluded from `StackIndex`/`StackRecord.components` (it's plumbing, not a
  catalog component), so it never appears in `extend`'s catalog-resolution logic or in any
  user-facing listing.
- `files_for` appends the `_system` file on every call (deploy and extend); `delete_files_for`
  appends its removal on every call. Both deploy targets (`ApplicationSetTarget`,
  `ApplicationTarget`) get this change, for parity.
- The golden-path chart gets a `namespaceOnly` values flag: when set, `deployment.yaml`
  renders nothing (new guard; `service.yaml`/`ingress.yaml` already no-op with no
  `port`/`ingressHost` set) and a new `namespace.yaml` renders one `Namespace` with the
  `deployground.io/managed=true` label. Chart version bump (0.4.2 → 0.5.0).
- Catalog config validation rejects a real component literally named `_system`, so a
  catalog entry can never collide with the reserved name.

## Why not alternatives considered

- **Dedicated extra Application just for the Namespace**: works, but is an entirely new
  generator/ApplicationSet plus new bootstrap manifests for a single resource — more
  moving parts than the problem needs.
- **One arbitrarily-chosen existing component "owns" the Namespace**: rejected — its
  safety depended on an unenforced invariant (nothing else ever touches one component's
  file independently of the whole stack) that could silently break later. The `_system`
  pseudo-component sidesteps this: its own lifecycle is explicit, not borrowed from a real
  component's fate.

## What's unaffected

No change to the Slack form, the catalog schema (beyond the name guardrail), notifications,
or the per-catalog chart-override feature (deferred, separate) — `_system` always uses the
default `workloadChart`, never a per-component override, since it isn't a catalog component.

## Left to the plan

Exact field/constant names, chart template diffs, the `StackIndex` skip condition, and the
catalog-name-collision validation's error message.
