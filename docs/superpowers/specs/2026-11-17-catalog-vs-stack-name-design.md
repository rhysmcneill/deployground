# Split catalog selection from the live stack's own name

## Intent and why

Today the Slack deploy form's "Stack" dropdown does two unrelated jobs at once: it picks
which catalog-defined set of components to deploy, *and* that same string becomes the
live deploy's unique identity (namespace, git path, index key, and now the Tailscale/DNS
ingress host). Because of that fusion, only one live deploy of any given catalog entry
can ever exist -- there's no way to deploy "homepage-test" twice, so there's no way to
give two copies distinct DNS names either. The user wants to deploy the same catalog
entry multiple times concurrently, each under a name they choose, with no DNS/namespace
collisions.

## Goals

- The deploy form lets the user pick a **Catalog** entry (which components/images to
  use) and separately type a **Stack name** (free text) that becomes the live deploy's
  identity everywhere it's used today: namespace, git path, index key, ingress host.
- Before anything is created, reject a stack name that's already live, and reject one
  that isn't a safe DNS/namespace label -- both as an inline Slack form error, same as
  today's "Select a stack."
- `extend`/`destroy` keep working on an existing live stack exactly as today.
- Deploy form visual polish: a header, dividers grouping "what to deploy" (Catalog +
  its component overrides) / "what to call it" (Stack name) / "where and how long"
  (Environment, Expires in), and hint text on Catalog, Stack name, and Environment
  (today only "Expires in" has one). Final order: Catalog, Override components (+ its
  dynamic per-component inputs), Stack name, divider, Environment, Expires in.

## Non-goals

- Changing how `env`, `ttl`, or component version overrides work.
- Any change to the golden-path chart itself.
- Enforcing global (cross-cluster) name uniqueness -- only within this bot's own index.

## Constraints found while scoping

- `extend()`/`destroy()` currently look up the catalog using the stack name itself
  (`catalog.get(stack)`), because today they're the same string. Once they differ, a
  live stack must remember *which* catalog entry it came from, so `extend` can still
  resolve versions correctly later. That means a new field in the persisted
  `deployground:` metadata block (`ComponentRecord`/`StackRecord`), not just a form
  change.
- `StackIndex` rebuilds its in-memory state by parsing that same metadata block back out
  of every component file in git (see `metadata.py`'s module docstring) -- the new field
  has to be parsed there too, and must degrade safely for any already-live stack deployed
  before this change (it won't have the field at all).

## Approach (agreed)

Two separate inputs:
- **Catalog** -- a `static_select`/`external_select` dropdown, exactly like today's
  "Stack" dropdown, just relabeled and now feeding a separate `catalog` field instead of
  `stack`.
- **Stack name** -- a new free-text input. Validated against a DNS-1123-label pattern
  (lowercase alphanumeric and hyphens, 1-63 chars, no leading/trailing hyphen) client-side
  in `parse_submission`, the same way "Select a stack"/"Select an environment" are today.

On submit, before opening any PR: look up `deps.index.get(stack_name)`. If it already
exists, fail the same way a bad field does -- `ack(response_action="errors", errors=...)`
naming the "stack" block -- so the user sees it inline in the modal, nothing is created.

Everywhere "stack" is used today as the live deploy's identity (namespace, path template,
index key, ingress host, Slack UI, `StackRecord.name`) keeps meaning exactly that; nothing
changes there. Everywhere "stack" is used today as a catalog lookup key becomes a lookup
by the new `catalog` field instead:

- `Config.stacks` -> `Config.catalog` (a dict keyed by catalog entry name); the
  `StackDef` model is renamed `CatalogEntry` for clarity, since "stack" now means the live
  deploy, not the template.
- `Catalog.get(key)` / `Catalog.resolve_versions(key, overrides)` take that catalog key,
  not the stack name.
- `ComponentRecord` gains a `catalog: str` field, persisted in the `deployground:` block
  (new key `catalog`) and parsed back out by `StackIndex`. A component file from before
  this change has no `catalog` key; it's read as a legacy field equal to the stack's own
  name (the old fused behavior), so `extend` on an old stack keeps resolving against a
  catalog entry of that same name if one still exists, and tolerates it not existing
  exactly like the already-shipped "dropped component" fix.
- `LifecycleService.deploy(..., catalog, stack, ...)` takes both; resolves versions via
  `catalog`, builds everything else (namespace, paths, records) via `stack`, same as today.
  `RenderInputs` and `ImageDeployCapability.render` (the actual call site of
  `resolve_versions`, one layer below `deploy`) get the same `catalog` field for the same
  reason -- this is the one place deploy's fused `stack` param is used for catalog lookup
  today.
- `StackRecord` gains `catalog: str` too, derived the same "first component seen wins" way
  `StackIndex.rebuild` already derives `env`/`owner`/`expires_at`/etc. from the first
  component record it sees for that stack -- so `extend`/`destroy` have one place to read
  it from, consistent with how every other stack-level field already works.
- `extend`/`destroy` read the live record's own `catalog` field (not the stack name) when
  they need to re-resolve the catalog, and must tolerate it two ways: a dropped
  *component* within a catalog entry that still exists (`UnknownComponent`), and the whole
  catalog entry having been renamed/removed (`Catalog.get` raising `UnknownStack`) --
  treated the same as "every component is uncataloged": metadata (e.g. expiry) still
  updates, nothing is regenerated. A fix for the first case exists on an unmerged branch;
  this change subsumes it rather than depending on its merge order.
- The "name already live" pre-check needs `deps.index`, which `forms.parse_submission`
  deliberately never gets (its own docstring: a pure function, no bolt/deps imports). So
  it runs in `slack/actions.py:handle_submit`, before `ack()` -- `parse_submission` only
  validates the DNS-label format. `LifecycleService.deploy`'s own existing check-under-lock
  stays as the race-safe backstop it already is.

### Rejected alternatives

- **Auto-generate the name** (e.g. `f"{catalog}-{random}"`): removes user input entirely,
  but the user explicitly wants to choose and see the name up front, and it still needs a
  collision-retry loop, which is no simpler than validating a typed name.
- **Auto-suffix on collision** (keep one "Stack" field, silently append `-2` etc. if
  taken): no explicit error, and the user wants an explicit error instead.

## What's left to the builder

- Exact Slack Block Kit layout/spacing for the two new fields (follows the existing
  `_input` helper pattern in `forms.py`).
- Exact DNS-label regex and its error copy.
- Whether `Config.catalog`'s YAML key in `config.yaml` is renamed from `stacks:` to
  `catalog:` as part of this change (recommended, since `CatalogEntry` is the new model
  name) -- a breaking config change for any existing deployment, but there's exactly one
  live installation (this homelab) to update alongside it.
