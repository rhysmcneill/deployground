# Split catalog selection from the live stack's own name — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user deploy the same catalog entry multiple times concurrently, each under
a name they choose in the Slack form, with no namespace/DNS collisions; plus a visual pass
on the deploy form.

**Architecture:** Split the one fused "stack" concept into two: `catalog` (which
catalog-defined entry to deploy — a config lookup key, unchanged data) and `stack` (the
live deploy's own identity — namespace, git path, index key, ingress host, exactly what
"stack" already means everywhere in the UI and code today). Thread `catalog` through
every place that currently resolves catalog data by reusing the stack name; persist it in
each live stack's own metadata so `extend`/`destroy` can look it up later without it.

**Tech Stack:** Python 3.12, pydantic v2, pytest, Slack Block Kit (via slack-bolt), PyYAML.

**Spec:** `docs/superpowers/specs/2026-11-17-catalog-vs-stack-name-design.md`

## Global Constraints

- A live stack written before this change has no `catalog` key in its metadata; reading it
  back must default `catalog` to that record's own `stack` value (the old fused behavior),
  not fail.
- `extend`/`destroy` must never crash when a live stack's catalog entry has been renamed or
  removed — treat every component as "not cataloged" (metadata still updates, nothing
  regenerates), same as the already-merged per-component version of this tolerance.
- The Stack name field must validate as a DNS-1123 label: lowercase alphanumeric and
  hyphens, 1-63 characters, no leading or trailing hyphen.
- `config.yaml`'s top-level catalog section is renamed `stacks:` -> `catalog:` (the model
  class `StackDef` -> `CatalogEntry`); this is a breaking config schema change, called out
  for whoever updates the one live deployment's config afterward.

## Review Focus

- Deploying with a Stack name that's already live for a *different* existing stack must
  produce the same inline Slack form error as an invalid name, before anything is created.
- Extending a stack whose catalog entry was fully removed (not just one component) must not
  open an empty PR — no changes, no PR call, but the stack's expiry still updates.
- A Stack name containing uppercase letters, underscores, or spaces must be rejected by
  `parse_submission` before `ack()`, not by Kubernetes after a PR already merged.
- Re-rendering the modal after picking a Catalog entry (today's `dispatch_action` reconnect)
  must still show that entry's components in the Override picker, now driven by the
  `catalog` field instead of `stack`.
- A pre-existing live stack's component file with no `catalog` key must still load via
  `StackIndex.rebuild()` (skipping it would silently drop a live deploy from the index).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/deployground/config/models.py` | `Config.catalog` (renamed from `stacks`), `CatalogEntry` (renamed from `StackDef`) |
| `src/deployground/metadata.py` | `ComponentRecord.catalog` / `StackRecord.catalog`, legacy-default parsing, annotation round-trip |
| `src/deployground/index.py` | `StackIndex.rebuild()` passes `catalog` through when building a `StackRecord` |
| `src/deployground/catalog.py` | `Catalog.list_entries()` / `.get()` / `.resolve_versions()` read `Config.catalog` |
| `src/deployground/capability/base.py` | `RenderInputs.catalog` |
| `src/deployground/capability/image_deploy.py` | resolves versions via `inputs.catalog`, stamps `catalog` onto each `ComponentRecord` |
| `src/deployground/lifecycle/service.py` | `deploy(..., catalog, stack, ...)`; `extend`/`destroy` read `record.catalog`, tolerate it being gone |
| `src/deployground/slack/forms.py` | `DeployInputs.catalog`; modal gets Catalog dropdown + Stack name text field + header/dividers/hints; `parse_submission` validates the name format |
| `src/deployground/slack/actions.py` | liveness pre-check in `handle_submit`; `catalog`-keyed modal-change/options/prefill handling |
| `src/deployground/slack/app.py` | dispatch_action/options registrations move from block `"stack"` to `"catalog"` |
| `examples/config.yaml`, `docs/quickstart.md` | `stacks:` -> `catalog:`, "Stack"/"Catalog" terminology |

---

### Task 1: Config model rename (`stacks` -> `catalog`, `StackDef` -> `CatalogEntry`)

**Files:**
- Modify: `src/deployground/config/models.py:89` (`StackDef` class), `:183` (`Config.stacks`), `:207` (`validate_refs`)
- Modify: `examples/config.yaml` (top-level `stacks:` key -> `catalog:`)
- Test: `tests/config/test_models.py`

**Interfaces:**
- Produces: `CatalogEntry` (model, same shape as today's `StackDef`: `components: dict[str, Component]`); `Config.catalog: dict[str, CatalogEntry]`.

- [ ] **Step 1: Write the failing test** in `tests/config/test_models.py`: a config dict with a top-level `catalog:` key (not `stacks:`) validates, and `Config.model_validate(...).catalog` is a `dict[str, CatalogEntry]`. Also assert that a config with a lingering `stacks:` key raises (pydantic `extra="forbid"` already does this once the field is renamed).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/config/test_models.py -k catalog -v`
Expected: FAIL (`Config` has no field `catalog`)

- [ ] **Step 3: Rename in `src/deployground/config/models.py`**: `class StackDef` -> `class CatalogEntry` (same body); `Config.stacks: dict[str, StackDef]` -> `Config.catalog: dict[str, CatalogEntry]`; update `validate_refs()`'s loop (`for stack_name, stack in self.stacks.items()`) to `for catalog_name, entry in self.catalog.items()` and its error message prefix from `f"stack {stack_name!r}"` to `f"catalog entry {catalog_name!r}"`.

- [ ] **Step 4: Update `examples/config.yaml`**: rename the top-level `stacks:` key to `catalog:` (no other content changes needed here — Task 7 handles the Stack-name/Catalog-label doc comments).

- [ ] **Step 5: Run test to verify it passes, then run the full suite to catch every other reference**

Run: `pytest -q`
Expected: many failures across `tests/test_catalog.py`, `tests/deploytarget/*`, `tests/capability/*`, `tests/lifecycle/*`, `tests/slack/*` — expected at this point; each later task fixes its own file's failures. Confirm the *new* test from Step 1 passes.

- [ ] **Step 6: Commit**

```bash
git add src/deployground/config/models.py examples/config.yaml tests/config/test_models.py
git commit -m "feat(config): rename stacks to catalog, StackDef to CatalogEntry"
```

---

### Task 2: Persist `catalog` on `ComponentRecord`/`StackRecord`

**Files:**
- Modify: `src/deployground/metadata.py` (`ComponentRecord`, `StackRecord`, `_BLOCK_KEYS`, `to_metadata_block`, `_record_from_block`, `to_annotations`, `_ANNOTATION_TO_BLOCK`, `from_annotations`)
- Test: `tests/test_metadata.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ComponentRecord.catalog: str` (required field on construction); `StackRecord.catalog: str`. `parse_metadata_block`/`from_annotations` default `catalog` to the record's own `stack` value when the key/annotation is absent (legacy files).

- [ ] **Step 1: Write the failing tests** in `tests/test_metadata.py`:
  - `to_metadata_block` on a `ComponentRecord(..., catalog="homepage-test", stack="pr-123", ...)` includes `"catalog": "homepage-test"` in the output dict.
  - `parse_metadata_block` round-trips a block containing `catalog`, recovering the same `ComponentRecord`.
  - `parse_metadata_block` on a block *without* a `catalog` key (today's shape, all other required keys present) succeeds and returns a record with `catalog` equal to that block's own `stack` value.
  - `to_annotations`/`from_annotations` round-trip `catalog` the same way, and `from_annotations` on annotations missing `deployground.io/catalog` defaults it to the `stack` annotation's value.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_metadata.py -v`
Expected: FAIL (`ComponentRecord` has no `catalog` field / block has no `catalog` key)

- [ ] **Step 3: Implement in `src/deployground/metadata.py`**:
  - Add `catalog: str` to `ComponentRecord` and `StackRecord` (dataclass field, after `stack`/`name` respectively).
  - `to_metadata_block`: add `"catalog": record.catalog` to the returned dict.
  - `_record_from_block`: do **not** add `"catalog"` to `_BLOCK_KEYS` (it must stay optional, or every pre-existing live stack's file fails the `missing` check on next rebuild). Read it as `catalog_value = block.get("catalog", block["stack"])`; if present, validate it's a `str` (`InvalidMetadata` otherwise) same as the other string fields. Pass `catalog=catalog_value` into the returned `ComponentRecord`.
  - `to_annotations`: add `f"{ANNOTATION_PREFIX}catalog": record.catalog`.
  - `_ANNOTATION_TO_BLOCK`: add `"catalog": "catalog"`.
  - `from_annotations`: after building `block` from present annotations, if `"catalog"` wasn't found, set `block["catalog"] = block["stack"]` before calling `_record_from_block(block)` (keeps the same default-to-stack behavior for the Application-manifest shape).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_metadata.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/deployground/metadata.py tests/test_metadata.py
git commit -m "feat(metadata): persist catalog on ComponentRecord/StackRecord, default to stack for legacy files"
```

---

### Task 3: Thread `catalog` through `StackIndex.rebuild`

**Files:**
- Modify: `src/deployground/index.py:47` (the `StackRecord(...)` construction inside `rebuild`)
- Test: `tests/test_index.py`

**Interfaces:**
- Consumes: `StackRecord.catalog` (Task 2), `ComponentRecord.catalog` (Task 2).
- Produces: nothing new; `rebuild()`'s existing public behavior, now also carrying `catalog`.

- [ ] **Step 1: Write the failing tests** in `tests/test_index.py`:
  - Build two component files for the same stack name via the test's existing git-listing fake, each with a `deployground:` block carrying `catalog: homepage-test`; after `rebuild()`, `index.get(stack).catalog == "homepage-test"` ("first component seen wins", matching the existing `env`/`owner`/etc. pattern one line below).
  - A legacy file: a `deployground:` block with no `catalog` key at all (today's real shape, still used by every already-live stack). After `rebuild()`, the stack is still present in the index (not silently dropped), with `catalog` equal to its own `stack` value — this is the Task 2 default surfacing through the actual rebuild path, not just the lower-level parser.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_index.py -v`
Expected: FAIL (`StackRecord(...)` call in `rebuild` is missing the required `catalog` argument -> `TypeError`, or the new assertion fails)

- [ ] **Step 3: Implement**: in `src/deployground/index.py`, add `catalog=record.catalog` to the `StackRecord(...)` constructor call inside `rebuild()` (same line as `env=record.env`, etc.).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_index.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/deployground/index.py tests/test_index.py
git commit -m "feat(index): carry catalog through StackIndex.rebuild"
```

---

### Task 4: `Catalog` reads `Config.catalog`, renames `list_stacks` -> `list_entries`

**Files:**
- Modify: `src/deployground/catalog.py` (`Catalog.list_stacks`, `.get`, `.resolve_versions`; imports)
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `Config.catalog`, `CatalogEntry` (Task 1).
- Produces: `Catalog.list_entries() -> list[str]` (renamed from `list_stacks`); `Catalog.get(key: str) -> CatalogEntry`; `Catalog.resolve_versions(key: str, overrides: dict[str, str]) -> dict[str, ResolvedComponent]` (signatures unchanged, just reading the renamed config field — callers pass a catalog key, not a stack name, from here on).

- [ ] **Step 1: Update the existing tests** in `tests/test_catalog.py`: rename every `list_stacks()` call to `list_entries()`; the fixture's config dict key `"stacks"` -> `"catalog"` (`make_catalog`'s config body). No new test needed here — `resolve_ingress_host` tests are untouched (different concept, same file).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_catalog.py -v`
Expected: FAIL (`AttributeError: 'Catalog' object has no attribute 'list_entries'` / config validation error on stray `stacks` key)

- [ ] **Step 3: Implement in `src/deployground/catalog.py`**: import `CatalogEntry` instead of `StackDef`; rename `list_stacks` -> `list_entries`, body `sorted(self._config_provider().catalog)`; in `get`, `self._config_provider().catalog`; `resolve_versions` unchanged apart from calling the renamed `get`. Rename the `UnknownStack`/`UnknownComponent` exceptions' constructor parameter from `stack` to `catalog_key` for clarity (keep class names — they're raised for catalog lookups specifically and nothing outside this module constructs them by keyword).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_catalog.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/deployground/catalog.py tests/test_catalog.py
git commit -m "feat(catalog): read Config.catalog, rename list_stacks to list_entries"
```

---

### Task 5: `RenderInputs.catalog` and `ImageDeployCapability`

**Files:**
- Modify: `src/deployground/capability/base.py:29` (`RenderInputs`)
- Modify: `src/deployground/capability/image_deploy.py:26-38` (`render`)
- Test: `tests/capability/test_image_deploy.py`

**Interfaces:**
- Consumes: `Catalog.resolve_versions(catalog_key, overrides)` (Task 4), `ComponentRecord.catalog` (Task 2).
- Produces: `RenderInputs.catalog: str` (new required field, alongside the existing `stack: str`).

- [ ] **Step 1: Update `tests/capability/test_image_deploy.py`**: `make_cfg()`'s config dict key `"stacks"` -> `"catalog"`; `make_inputs()` adds `catalog="demo"` to its default `args` dict (same value as `stack` for these tests — they don't exercise catalog != stack, that's Task 6's job); add one new test, `test_render_resolves_versions_from_catalog_not_stack`: build a `RenderInputs` with `catalog="demo"` and `stack="pr-123"` (a name not in the config's `catalog` dict), assert `render()` still succeeds and every `ComponentRecord` in the result has `stack="pr-123"` and `catalog="demo"` — proves version resolution and the live identity are now independent.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/capability/test_image_deploy.py -v`
Expected: FAIL (`RenderInputs() missing required argument 'catalog'` / resolve_versions still keyed by `stack`)

- [ ] **Step 3: Implement**:
  - `src/deployground/capability/base.py`: add `catalog: str` to `RenderInputs` (place it right before `stack`, matching the plan's "catalog, then stack" convention used everywhere else).
  - `src/deployground/capability/image_deploy.py`, `render()`: `ctx.catalog.resolve_versions(inputs.catalog, inputs.overrides)` (was `inputs.stack`); in the `ComponentRecord(...)` construction, add `catalog=inputs.catalog` (keep `stack=inputs.stack` as today — that's still the live identity).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/capability/test_image_deploy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/deployground/capability/base.py src/deployground/capability/image_deploy.py tests/capability/test_image_deploy.py
git commit -m "feat(capability): resolve versions from RenderInputs.catalog, not stack"
```

---

### Task 6: `LifecycleService` — `deploy(catalog, stack, ...)`; `extend`/`destroy` tolerate a gone catalog entry

**Files:**
- Modify: `src/deployground/lifecycle/service.py` (`deploy`, `_deploy_reserved`, `extend`)
- Test: `tests/lifecycle/test_service.py`

**Interfaces:**
- Consumes: `RenderInputs.catalog` (Task 5), `StackRecord.catalog`/`ComponentRecord.catalog` (Task 2), `Catalog.get`/`resolve_versions` raising `UnknownStack`/`UnknownComponent` (Task 4, `deployground.catalog`).
- Produces: `LifecycleService.deploy(user, owner_email, catalog, stack, env, overrides, ttl, thread_ts, channel) -> DeployResult` (new `catalog` positional parameter inserted right before `stack`). `extend`/`destroy` signatures unchanged (`stack` only — they look up `catalog` from the live record).

- [ ] **Step 1: Update `tests/lifecycle/test_service.py`'s `make_cfg`-equivalent fixture and `deploy()` helper**: the config fixture's `"stacks"` key -> `"catalog"`; the module's `deploy(svc, ...)` test helper gains a `catalog="demo"` parameter (default `"demo"`, same as today's hardcoded `stack="demo"`), and its call becomes `svc.deploy(user=user, owner_email=email, catalog=catalog, stack="demo", env=env, overrides=..., ttl=ttl, thread_ts="1.1", channel="C1")`.

- [ ] **Step 2: Write the new failing tests**:
  - `test_deploy_resolves_versions_from_catalog_not_stack_name`: call the `deploy()` helper with `catalog="demo"` and a `stack` name not present in the catalog (pass it through directly rather than via the helper's hardcoded `"demo"`, or extend the helper to also take `stack=`); assert it succeeds and `index.records[<stack>].catalog == "demo"`.
  - `test_extend_tolerates_a_component_dropped_from_the_catalog` (already exists from the merged `fix/extend-tolerates-dropped-component` work — keep it, update its `record.catalog` field since `StackRecord` now requires one, e.g. `catalog="demo"`).
  - `test_extend_tolerates_the_whole_catalog_entry_being_gone`: a `StackRecord` whose `catalog="deleted-entry"` (not present in `make_cfg()`'s `catalog` dict) with one live component; call `svc.extend(...)`; assert `git.prs == []` (no PR opened — nothing to regenerate) and `index.records[stack].expires_at` is still pushed out by the requested duration.

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/lifecycle/test_service.py -v`
Expected: FAIL (`deploy() missing argument 'catalog'` / `extend` raises `UnknownStack` instead of tolerating it / no such test yet)

- [ ] **Step 4: Implement in `src/deployground/lifecycle/service.py`**:
  - `deploy(self, user, owner_email, catalog, stack, env, overrides, ttl, thread_ts, channel)`: thread `catalog` through to `_deploy_reserved`.
  - `_deploy_reserved(..., catalog, stack, ...)`: `RenderInputs(catalog=catalog, stack=stack, ...)`; its own direct `self._ctx.catalog.resolve_versions(stack, overrides)` call (line ~153 today) becomes `resolve_versions(catalog, overrides)`; `StackRecord(..., catalog=catalog, ...)` and each `ComponentRecord(..., catalog=catalog, ...)`.
  - `extend(self, user, stack, by)`: replace the existing `self._ctx.catalog.get(stack)` / `resolve_versions(stack, ...)` calls with `record.catalog` in place of `stack`, wrapped to tolerate the whole entry being gone:
    ```python
    from deployground.catalog import UnknownStack
    try:
        catalog_components = self._ctx.catalog.get(record.catalog).components
    except UnknownStack:
        catalog_components = {}
    still_cataloged = {name: c for name, c in components.items() if name in catalog_components}
    if still_cataloged:
        resolved = self._ctx.catalog.resolve_versions(
            record.catalog, {name: c.version for name, c in still_cataloged.items()}
        )
        resolved = {name: resolved[name] for name in still_cataloged}
        changes = self._ctx.deploy_target.files_for(stack, record.env, resolved, components)
    else:
        changes = []
    ```
    and only call `self._open_pr(...)` when `changes` is non-empty; always `self._index.upsert(replace(record, expires_at=new_expiry, components=components))` regardless.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/lifecycle/test_service.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/deployground/lifecycle/service.py tests/lifecycle/test_service.py
git commit -m "feat(lifecycle): split deploy's catalog/stack params; extend tolerates a fully-removed catalog entry"
```

---

### Task 7: Deploy form — Catalog + Stack name fields, visual polish

**Files:**
- Modify: `src/deployground/slack/forms.py` (`DeployInputs`, `build_deploy_modal`, `build_override_blocks`, `parse_submission`)
- Test: `tests/slack/test_forms.py`

**Interfaces:**
- Consumes: `Catalog.list_entries()` (Task 4), `CatalogEntry` (Task 1).
- Produces: `DeployInputs(catalog: str, stack: str, env: str, overrides: dict[str, str], ttl: str)` (new field `catalog` inserted before the existing `stack`, which now means the free-typed name); `parse_submission` returns a new error key `"stack"` for a bad/empty name (block id `"stack"` is reused — same key the old dropdown used, now a text input) — no change to the function's return type.
- A new exported constant `STACK_NAME_PATTERN` (the compiled DNS-1123-label regex) for Task 8 to reuse in its own validation-adjacent test, if needed, and for `parse_submission`'s own use.

- [ ] **Step 1: Update `tests/slack/test_forms.py`'s fixture and helpers**: `CFG`'s `"stacks"` key -> `"catalog"`; `catalog()` helper's local var `stacks` -> `entries` (cosmetic, keep behavior); `state(...)` helper: rename its `stack=` parameter to `catalog=` for the dropdown value, add a new `name=` parameter that writes `s["stack"] = _text(name)` (reusing the existing `_text` test helper already used for `ttl`/overrides); default `name="pr-123"`.

- [ ] **Step 2: Write the new/updated failing tests**:
  - `test_catalog_picker_uses_external_select_over_100`: renamed/adjusted from `test_stack_picker_uses_external_select_over_100` — checks the block at `block_id="catalog"`.
  - `test_stack_name_is_a_plain_text_input_with_hint`: `block(view, "stack")["element"]["type"] == "plain_text_input"` and it carries a `"hint"` key.
  - `test_override_picker_and_blocks_sit_between_catalog_and_stack_name`: assert the order of `block_id`s in a prefilled view's `blocks` is `["catalog", "override_components", "override::web", "stack", "env", "ttl"]` (for a prefill with one overridden component) — pins the UX ordering decision.
  - `test_parse_submission_rejects_invalid_stack_name`: `state(name="Not Valid!")` -> `errors["stack"]` present, `inputs is None`.
  - `test_parse_submission_accepts_valid_stack_name`: `state(name="pr-123")` -> `inputs.stack == "pr-123"` and `inputs.catalog == "shop"` (or whatever `state()`'s `catalog=` default is).
  - `test_parse_submission_rejects_empty_stack_name`: `state(name="")` -> `errors["stack"]`.
  - Update `test_prefill_populates_fields`, `test_parse_submission_extracts_overrides_and_ttl`, `test_parse_submission_returns_inline_errors_for_bad_input`, `test_stack_and_override_picker_dispatch_actions` (rename to `..._catalog_and_override_picker_...`) for the new two-field shape and the `"catalog"` dispatch_action block id.

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/slack/test_forms.py -v`
Expected: FAIL (no `"catalog"` block, `DeployInputs` missing field, no name validation)

- [ ] **Step 4: Implement in `src/deployground/slack/forms.py`**:
  - `DeployInputs`: add `catalog: str` before `stack: str`.
  - Add `STACK_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")` (module level; needs `import re`).
  - `build_override_blocks`: parameter renamed `entry: CatalogEntry` (was `stack: StackDef`) — purely a rename, same body (uses `entry.components`).
  - `build_deploy_modal`: rename the `stacks` local to `entries = catalog.list_entries()`; the dropdown block's `block_id` changes from `"stack"` to `"catalog"` (keep `dispatch_action=True` on it); `known_stack` -> `known_catalog = prefill is not None and prefill.catalog in entries`, checked against `prefill.catalog` throughout. Insert the header/divider blocks and hints per the spec's approved layout:
    1. `{"type": "header", "text": _text("🚀  New deployment")}`
    2. Catalog input (hint: `"What to deploy"`)
    3. Override picker + per-component blocks (existing `build_override_blocks` output), still gated on `known_catalog`
    4. Stack name input: `_input("stack", "Stack name", {"type": "plain_text_input", "initial_value": prefill.stack if prefill else ""}, hint=_text("Used for the namespace and URL — lowercase letters, numbers, hyphens"))`
    5. `{"type": "divider"}`
    6. Environment input (hint: none needed beyond its placeholder — leave as today)
    7. Expires-in input (existing hint, unchanged)
  - `parse_submission`: rename the `stack = _selected(...)` read to `catalog = _selected(view_state, "catalog")` (error message `"Select a catalog entry."`); add `name = _text_value(view_state, "stack")`; validate with `STACK_NAME_PATTERN.fullmatch(name)`, else `errors["stack"] = "Use lowercase letters, numbers and hyphens only (1-63 characters, no leading/trailing hyphen)."`; on success return `DeployInputs(catalog=catalog, stack=name, env=env, overrides=overrides, ttl=ttl)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/slack/test_forms.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/deployground/slack/forms.py tests/slack/test_forms.py
git commit -m "feat(forms): split Catalog dropdown from a free-text Stack name field, add header/dividers/hints"
```

---

### Task 8: Wire `catalog` through `actions.py`/`app.py`; liveness pre-check

**Files:**
- Modify: `src/deployground/slack/actions.py` (`handle_prefill`, `handle_modal_change`, `_default_version`, `handle_stack_options` -> `handle_catalog_options`, `handle_submit`)
- Modify: `src/deployground/slack/app.py` (block-id registrations)
- Test: `tests/slack/test_actions.py`

**Interfaces:**
- Consumes: `DeployInputs.catalog`/`.stack` (Task 7), `LifecycleService.deploy(catalog, stack, ...)` (Task 6), `Catalog.list_entries()` (Task 4).
- Produces: `Deps` unchanged. `handle_catalog_options` (renamed from `handle_stack_options`) — same signature, now keyed to the `"catalog"` block.

- [ ] **Step 1: Update `tests/slack/test_actions.py` fixtures**: `CFG` import already comes from `tests/slack/test_forms.py` (Task 7 updates it there — no duplicate fixture here). `FakeLifecycle.deploy` gains a `catalog` parameter (inserted before `stack`, matching Task 6's signature) and records it in `self.calls`. `_submit_body`: its `state` dict's `"stack": _select("shop")` becomes `"catalog": _select("shop")` plus `"stack": _text("new-deploy")` (a name distinct from any pre-seeded `FakeIndex` record, so existing "happy path" tests don't collide with the new liveness check). `_record()`'s `StackRecord`/`ComponentRecord` constructions get a `catalog="shop"` field (Task 2 requires it).

- [ ] **Step 2: Write the new failing tests**:
  - `test_submit_rejects_a_stack_name_already_live`: `_submit_body({"stack": _text("shop")})` (colliding with the `FakeIndex`-seeded `_record()`, whose `name == "shop"`); assert `ack.calls[0]["response_action"] == "errors"`, `"stack" in ack.calls[0]["errors"]`, and `deps.lifecycle.calls == []` (nothing was attempted).
  - Update `test_submit_posts_message_then_calls_deploy_with_thread_ts`: assert the recorded `deploy` call's `catalog` and `stack` positions are `("shop", "new-deploy")` (matching the new `_submit_body` default).

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/slack/test_actions.py -v`
Expected: FAIL (no liveness check yet; `FakeLifecycle.deploy` signature mismatch)

- [ ] **Step 4: Implement**:
  - `src/deployground/slack/actions.py`:
    - `handle_prefill`: decode `catalog=data["catalog"]` alongside the existing `stack=data["stack"]`.
    - `handle_modal_change`: read `catalog = selected("catalog")` instead of `selected("stack")`; use it for `_default_version(deps, catalog, name)` and the rebuilt `DeployInputs(catalog=catalog, stack=<text value of the "stack" block>, env=..., overrides=..., ttl=...)` — read the stack-name text value the same inline way `ttl` is already read (`(values.get("stack", {}).get(ACTION_ID) or {}).get("value") or ""`).
    - `_default_version(deps, catalog, component)`: parameter renamed from `stack` to `catalog` (body unchanged: `deps.catalog.get(catalog).components[component]...`).
    - Rename `handle_stack_options` -> `handle_catalog_options`; body unchanged apart from calling it that (still calls `deps.catalog.list_entries()` per Task 4's rename).
    - `handle_submit`: after `inputs, errors = parse_submission(...)`, before the unconditional `ack()`, add the liveness check: if `inputs is not None and deps.index.get(inputs.stack) is not None`, set `errors = {**errors, "stack": f"{inputs.stack!r} is already in use by a live stack."}` and treat as invalid (fall into the existing `if inputs is None:`-style branch — restructure the top of the function so this check and the parse errors share one `ack(response_action="errors", ...)` exit path). Then in the deploy call, pass `inputs.catalog` as the new first positional after `owner_email`: `deps.lifecycle.deploy(user, _owner_email(client, user), inputs.catalog, inputs.stack, inputs.env, inputs.overrides, inputs.ttl, ts, channel)`. The `RenderInputs(...)` built later for the `needs_approval` check also gets `catalog=inputs.catalog`.
  - `src/deployground/slack/app.py`: change `for block in ("stack", OVERRIDE_PICKER_BLOCK):` to `for block in ("catalog", OVERRIDE_PICKER_BLOCK):`; change `@app.options({"block_id": "stack", ...})` to `@app.options({"block_id": "catalog", ...})`, calling `actions.handle_catalog_options`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/slack/test_actions.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: all pass (this is the last task touching production code — everything from Tasks 1-7 should now be fully wired)

- [ ] **Step 7: Commit**

```bash
git add src/deployground/slack/actions.py src/deployground/slack/app.py tests/slack/test_actions.py
git commit -m "feat(slack): wire catalog through action handlers, reject an already-live stack name before ack"
```

---

### Task 9: Docs

**Files:**
- Modify: `docs/quickstart.md` (the "environments (Argo CD cluster name, TTL, ...)" line already touches this area from prior work — update the "Stack"/catalog terminology references), `examples/config.yaml` (comments near the `catalog:` key, if any still say "stack" where they mean "catalog entry")

**Interfaces:** none (docs only).

- [ ] **Step 1: Update `examples/config.yaml`**: re-read the file after Task 1's key rename and fix any comment still calling the section "stacks" (e.g. a header comment above the `catalog:` key) to describe it as the catalog of reusable component definitions, distinct from a live deploy's own name.

- [ ] **Step 2: Update `docs/quickstart.md`**: anywhere it describes the Slack deploy form's "Stack" dropdown, describe the two fields — Catalog (what to deploy) and Stack name (what to call this deploy, becomes the namespace/DNS).

- [ ] **Step 3: Run the full suite once more**

Run: `pytest -q`
Expected: all pass (docs-only change, no behavior to verify beyond "nothing broke")

- [ ] **Step 4: Commit**

```bash
git add docs/quickstart.md examples/config.yaml
git commit -m "docs: describe the Catalog / Stack name split"
```

---

## After this plan ships

The one live `homelab-argocd` deployment's own `deployground` config (`apps/environments/production/homelab/deployground/values.yaml`) still uses the old `stacks:` key and relies on its stack dropdown value being both the catalog key and the live name — it needs its own follow-up update (rename `stacks:` -> `catalog:`) once this is released, plus a version bump, same pattern as every other change this session.
