# Custom Helm Charts Per Component Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A catalog component can point at an arbitrary Helm chart (not just the built-in
golden-path chart), with the operator declaring which of its values the Slack form exposes
as named, user-facing override fields — while the deploying user never sees a values file
or a chart's schema, and `allowedRegistries`/`ingressDomains` keep being enforced.

**Architecture:** `Component` gains an optional custom-chart shape (`chart`/`values`/`image`/
`ingress`/`overridable`), mutually exclusive with golden-path's typed fields, validated by a
single model. A new small dot-path get/set helper is shared by catalog resolution and both
guardrail call sites (deploy-time and config-load-time). The per-deploy override payload
unifies to `dict[component, dict[path, value]]` end-to-end (Slack blocks, `RenderInputs`,
`Catalog.resolve_versions()`); golden-path components get one implicit `{"version": ...}`
entry so their behavior and rendered modal are unchanged. Rendering (`files_for()`) branches
only where it structurally must — writing a custom chart's `values` block vs golden-path's
typed fields — never upstream of that.

**Tech Stack:** Python, pydantic v2, pytest, Slack Block Kit (plain-dict builders, no SDK
imports in `forms.py`).

**Spec:** `docs/superpowers/specs/2026-11-18-custom-charts-per-component-design.md`

## Global Constraints

- Zero catalog-config changes or behavior changes for existing golden-path-only components.
  Every existing test fixture using plain `Component` fields must keep passing unmodified.
- `allowedRegistries` is enforced for custom-chart components both at config-load time
  (`Config.validate_refs()`) and deploy time (`ImageDeployCapability._check_guardrails()`),
  reading the value at `component.image.path` inside `component.values`.
- `ingressDomains` is enforced the same way via `component.ingress.path`, only when a
  custom-chart component declares `ingress` at all (no ingress declared = nothing to check,
  same as a golden-path component with no `ingressHost`).
- The override payload is `dict[component_name, dict[path, value]]` everywhere it crosses a
  function boundary (`DeployInputs.overrides`, `RenderInputs.overrides`,
  `Catalog.resolve_versions()`'s parameter) — no parallel/legacy flat-string shape anywhere.
- Slack block ids for overrides are `f"{OVERRIDE_PREFIX}{component}__{path}"` (literal double
  underscore `__` as the component/path separator — distinct from `.` inside a path and from
  any existing `::` prefix separator). Component names and override paths must never contain
  `__` themselves; this is a documented constraint, not something validated at runtime by
  this plan.
- Slack field labels for a component with multiple overridable entries are
  `f"{component} — {entry.label}"` (en dash, matching today's existing `f"{name} version"`
  disambiguation pattern across multiple components in one modal) — never the bare
  `entry.label` alone, which would be ambiguous across components.
- A custom-chart component's `chart` field mirrors `WorkloadChartCfg`'s existing shape
  (`type: Literal["oci","git"]`, `repo`/`chart`/`version` for oci, `gitRepo`/`gitRef`/
  `chartPath` for git) — reuse `WorkloadChartCfg` itself rather than inventing a second,
  near-identical model.

## Review Focus

- A custom-chart component with no `overridable` entries at all: the Slack form must render
  zero fields for it beyond Stack name/Environment/TTL — not crash, not show a stray empty
  block. (Task 6)
- A custom-chart component whose `overridable` entry targets `chart.version`: the resolved
  chart source's `targetRevision` must reflect the override, not just a values-block field.
  (Task 3, Task 5)
- An operator typo in `image.path` (path doesn't exist in `values`) must fail loudly at
  config-load time (`validate_refs()`), not silently skip the registry check. (Task 2)
- A golden-path component mixed in the same catalog entry as a custom-chart component: both
  must resolve and render correctly side by side, with the modal showing the right fields
  for each (implicit single "Version" field for golden-path, declared list for the other).
  (Task 3, Task 6)
- `handle_modal_change`'s component-reselection flow (Slack's `dispatch_action` round-trip)
  must rebuild a changed component's fields with its FULL `overridable` list, not just the
  one field that happened to exist before this feature (today's single-field assumption).
  (Task 7)

---

### Task 1: Dot-path get/set helper

**Files:**
- Create: `src/deployground/dotpath.py`
- Test: `tests/test_dotpath.py`

**Interfaces:**
- Produces: `get_path(data: dict, path: str) -> Any` (raises `KeyError` with a clear message
  naming the full path if any segment is missing); `set_path(data: dict, path: str, value:
  Any) -> dict` (returns a new dict with the value set at that dot-path, creating
  intermediate dicts as needed; does not mutate `data`).

- [ ] **Step 1: Write failing tests**

```python
def test_get_path_reads_nested_value():
    assert get_path({"image": {"tag": "v1"}}, "image.tag") == "v1"

def test_get_path_raises_on_missing_segment():
    with pytest.raises(KeyError, match="image.tag"):
        get_path({"image": {}}, "image.tag")

def test_set_path_creates_intermediate_dicts_without_mutating_input():
    original = {"a": 1}
    result = set_path(original, "b.c", "v")
    assert result == {"a": 1, "b": {"c": "v"}}
    assert original == {"a": 1}

def test_set_path_overwrites_existing_leaf():
    assert set_path({"a": {"b": 1}}, "a.b", 2) == {"a": {"b": 2}}
```

- [ ] **Step 2: Run to verify failures** — `pytest tests/test_dotpath.py -v`, expect
  `ModuleNotFoundError`.

- [ ] **Step 3: Implement `get_path`/`set_path` in `src/deployground/dotpath.py`.** Split
  `path` on `.`; `get_path` walks the dict raising `KeyError(f"missing {path!r}: no key
  {segment!r}")` on the first absent segment; `set_path` does a `copy.deepcopy` of `data`
  first, then walks/creates nested dicts for all but the last segment, setting the last.

- [ ] **Step 4: Run to verify passes** — `pytest tests/test_dotpath.py -v`.

- [ ] **Step 5: Commit**

```bash
git add src/deployground/dotpath.py tests/test_dotpath.py
git commit -m "feat(dotpath): add a small dot-path get/set helper"
```

---

### Task 2: Config models for a custom-chart component

**Files:**
- Modify: `src/deployground/config/models.py`
- Test: `tests/config/test_models.py`

**Interfaces:**
- Consumes: `dotpath.get_path` (Task 1).
- Produces: `FieldRef(_Model)` with one field `path: str`. `OverridableField(_Model)` with
  `path: str`, `label: str`. `Component` gains optional fields `chart: WorkloadChartCfg |
  None = None`, `values: dict[str, Any] = Field(default_factory=dict)`, `image: FieldRef |
  None = None` (shadows nothing — golden-path's image is the existing required `str` field
  named `image`; see note below), `ingress: FieldRef | None = None`, `overridable:
  list[OverridableField] = Field(default_factory=list)`.
- Produces: `Component.is_custom_chart` — a `@property` returning `self.chart is not None`,
  used by every later task instead of each re-deriving the same check.

**Note on the `image` field name collision:** `Component.image: str` (golden-path's typed
image reference) already exists and is required today. Introducing a *second* `image` field
of a different type on the same model is not possible in one pydantic model. Resolve by
making golden-path's `image`/`defaultVersion` **optional** (`str | None = None`) and adding
the custom-chart image pointer as a **differently-named** field: `imageRef: FieldRef | None
= None`. Mirror this for ingress: keep `ingressHost`/`ingressClassName` as-is (already
optional) for golden-path, add `ingressRef: FieldRef | None = None` for custom charts.

- [ ] **Step 1: Write failing tests**

```python
def test_component_rejects_both_golden_path_and_chart_fields():
    with pytest.raises(ValidationError, match="chart"):
        Component.model_validate({
            "image": "ghcr.io/x", "defaultVersion": "1",
            "chart": {"type": "oci", "chart": "x"},
        })

def test_component_requires_image_ref_when_chart_is_set():
    with pytest.raises(ValidationError, match="imageRef"):
        Component.model_validate({"chart": {"type": "oci", "chart": "x"}})

def test_component_requires_image_or_chart():
    with pytest.raises(ValidationError, match="image"):
        Component.model_validate({"defaultVersion": "1"})

def test_custom_chart_component_is_valid():
    comp = Component.model_validate({
        "chart": {"type": "oci", "chart": "myservice-chart", "version": "1.4.0"},
        "values": {"replicas": 2},
        "imageRef": {"path": "image.repository"},
        "overridable": [{"path": "image.tag", "label": "Version"}],
    })
    assert comp.is_custom_chart is True

def test_golden_path_component_is_not_custom_chart():
    comp = Component.model_validate({"image": "ghcr.io/x", "defaultVersion": "1"})
    assert comp.is_custom_chart is False
```

- [ ] **Step 2: Run to verify failures** — `pytest tests/config/test_models.py -k custom_chart -v`.

- [ ] **Step 3: Implement.** Add `FieldRef`/`OverridableField` models. On `Component`: make
  `image`/`defaultVersion` `str | None = None`; add `chart`, `values`, `imageRef`, `ingressRef`,
  `overridable` as described above; add `is_custom_chart` property; add a
  `@model_validator(mode="after")` `_golden_path_xor_chart` that raises `ValueError` when
  both `chart` and `image` are set, raises when neither is set, and raises when `chart` is
  set but `imageRef` is `None` (name the missing field in the message, e.g. `"imageRef is
  required when chart is set"`).

- [ ] **Step 4: Extend `Config.validate_refs()`** for the registry-guardrail config-load-time
  check (currently reads `comp.image` directly for every component): for a component where
  `comp.is_custom_chart`, use `dotpath.get_path(comp.values, comp.imageRef.path)` to get the
  image string instead, wrapping a `KeyError` from `get_path` into the same `errors.append(...)`
  pattern (message: `f"catalog entry {catalog_name!r} component {comp_name!r}: imageRef path
  {comp.imageRef.path!r} not found in values"`). Golden-path components keep reading
  `comp.image` exactly as today.

- [ ] **Step 5: Run to verify passes** — `pytest tests/config/test_models.py -v`, then the
  full suite `python -m pytest -q` to confirm no existing fixture broke.

- [ ] **Step 6: Commit**

```bash
git add src/deployground/config/models.py tests/config/test_models.py
git commit -m "feat(config): add the custom-chart component shape and its config-load guardrail"
```

---

### Task 3: `Catalog.resolve_versions()` generalization

**Files:**
- Modify: `src/deployground/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `dotpath.get_path`/`set_path` (Task 1), `Component.is_custom_chart`/`imageRef`/
  `overridable` (Task 2).
- Produces: `resolve_versions(self, stack: str, overrides: dict[str, dict[str, str]]) ->
  dict[str, ResolvedComponent | ResolvedCustomComponent]` — note the changed `overrides`
  type (was `dict[str, str]`).
- Produces: `ResolvedCustomComponent(_Model)`: `chart: WorkloadChartCfg`, `values: dict[str,
  Any]` (post-override), `imageRef: FieldRef`, `ingressRef: FieldRef | None = None` (copied
  from `comp.ingressRef` unchanged — overrides never target it, only `comp.values` paths and
  `chart.version` do; it's carried through purely so Task 4's guardrail can read it off the
  resolved component without re-fetching the original `Component`).
- `UnknownComponent` is still raised for an override naming a component not in the stack
  (unchanged semantics, just checking `overrides` keys as before — the inner `dict[path,
  value]` doesn't change that check).

- [ ] **Step 1: Write failing tests**

```python
def test_resolve_versions_golden_path_unchanged_with_version_override():
    resolved = catalog.resolve_versions("demo", {"api": {"version": "2.0.0"}})
    assert resolved["api"].version == "2.0.0"

def test_resolve_versions_golden_path_defaults_without_override():
    resolved = catalog.resolve_versions("demo", {})
    assert resolved["api"].version == "1.0.0"  # comp.defaultVersion from the fixture

def test_resolve_versions_custom_chart_applies_values_override():
    resolved = catalog.resolve_versions("demo", {"myservice": {"image.tag": "v9", "replicas": "5"}})
    comp = resolved["myservice"]
    assert comp.values["image"]["tag"] == "v9"
    assert comp.values["replicas"] == "5"

def test_resolve_versions_custom_chart_overrides_chart_version():
    resolved = catalog.resolve_versions("demo", {"myservice": {"chart.version": "2.0.0"}})
    assert resolved["myservice"].chart.version == "2.0.0"

def test_resolve_versions_unknown_component_override_raises():
    with pytest.raises(UnknownComponent):
        catalog.resolve_versions("demo", {"nope": {"version": "x"}})
```

(Use/extend this plan's test fixtures to include one `demo` stack with a golden-path `api`
component and a custom-chart `myservice` component, matching Task 2's example shapes — check
`tests/test_catalog.py`'s existing fixture setup first and extend it rather than replacing
it, so existing tests there keep passing unchanged.)

- [ ] **Step 2: Run to verify failures** — `pytest tests/test_catalog.py -v`.

- [ ] **Step 3: Implement.** In `resolve_versions`: for each component, if NOT
  `comp.is_custom_chart`: unchanged behavior, except the override lookup becomes
  `overrides.get(name, {}).get("version", comp.defaultVersion)` (golden-path's implicit
  single path is literally `"version"`). If `comp.is_custom_chart`: start from
  `values = comp.values`, `chart = comp.chart`; for each `path, value` in
  `overrides.get(name, {}).items()`: if `path == "chart.version"`, `chart =
  chart.model_copy(update={"version": value})`; else `values = dotpath.set_path(values, path,
  value)`; return `ResolvedCustomComponent(chart=chart, values=values, imageRef=comp.imageRef)`.

- [ ] **Step 4: Run to verify passes** — `pytest tests/test_catalog.py -v`, then
  `python -m pytest -q` for the full suite.

- [ ] **Step 5: Commit**

```bash
git add src/deployground/catalog.py tests/test_catalog.py
git commit -m "feat(catalog): generalize resolve_versions to per-path overrides for custom charts"
```

---

### Task 4: Guardrails for custom-chart components (deploy-time)

**Files:**
- Modify: `src/deployground/capability/image_deploy.py`
- Test: `tests/capability/test_image_deploy.py`

**Interfaces:**
- Consumes: `ResolvedCustomComponent`/`ResolvedComponent` (Task 3), `dotpath.get_path`
  (Task 1).
- Produces: `ImageDeployCapability._check_guardrails()` handling both resolved-component
  shapes: for `ResolvedComponent` (golden-path), unchanged (`comp.image`/`comp.ingressHost`
  read directly); for `ResolvedCustomComponent`, read the image via
  `dotpath.get_path(comp.values, comp.imageRef.path)`, and the ingress host — if the
  original `Component.ingressRef` was declared (need to thread it through
  `ResolvedCustomComponent` too; add an `ingressRef: FieldRef | None = None` field there
  mirroring Task 2/3's `imageRef`) — via `dotpath.get_path(comp.values, comp.ingressRef.path)`,
  skipping the ingress check entirely when `ingressRef` is `None`.

- [ ] **Step 1: Write failing tests**

```python
def test_check_guardrails_blocks_disallowed_registry_for_custom_chart():
    comp = ResolvedCustomComponent(
        chart=..., values={"image": {"repository": "evil.example/x"}},
        imageRef=FieldRef(path="image.repository"),
    )
    with pytest.raises(GuardrailViolation, match="evil.example"):
        ImageDeployCapability._check_guardrails({"myservice": comp}, ctx)

def test_check_guardrails_allows_matching_registry_for_custom_chart():
    # values.image.repository under an allowed registry -> no raise

def test_check_guardrails_skips_ingress_check_when_no_ingress_ref():
    # custom-chart component with ingressRef=None never raises on ingressDomains

def test_check_guardrails_blocks_disallowed_ingress_domain_for_custom_chart():
    # custom-chart component with ingressRef set, value outside ingressDomains -> raises
```

(Read the existing tests in this file first for ctx/cfg fixture construction style and reuse
it rather than inventing a new fixture shape.)

- [ ] **Step 2: Run to verify failures.**

- [ ] **Step 3: Implement** the branch described above inside `_check_guardrails`, keeping
  the existing golden-path branch's code untouched (add an `if`/`else` on
  `isinstance(comp, ResolvedComponent)` vs the custom-chart type, or on a shared
  `is_custom_chart`-style marker — implementer's choice of exactly how to discriminate the
  two resolved-component types at this call site, as long as golden-path's existing checks
  are unmoved).

- [ ] **Step 4: Run to verify passes**, then full suite.

- [ ] **Step 5: Commit**

```bash
git add src/deployground/capability/image_deploy.py src/deployground/catalog.py tests/capability/test_image_deploy.py
git commit -m "feat(guardrails): enforce allowedRegistries/ingressDomains for custom-chart components"
```

---

### Task 5: Render custom-chart components (both deploy targets)

**Files:**
- Modify: `src/deployground/deploytarget/applicationset.py`
- Modify: `src/deployground/deploytarget/application.py`
- Test: `tests/deploytarget/test_applicationset.py`, `tests/deploytarget/test_application.py`

**Interfaces:**
- Consumes: `ResolvedCustomComponent` (Task 3), `chart_source()` (existing,
  `workloadchart.py` — reuse as-is, it already accepts any `WorkloadChartCfg`-shaped value,
  which `ResolvedCustomComponent.chart` is).
- Produces: `files_for()` in both targets writing a custom-chart component's file from
  `comp.values` directly (plus the `deployground:`/annotation metadata exactly as for any
  other component) instead of building the fixed golden-path field dict, and using
  `chart_source(comp.chart)` (the component's OWN resolved chart, not
  `self._cfg.workloadChart`) for that component's Application source.

- [ ] **Step 1: Write failing tests**

For `ApplicationSetTarget`:
```python
def test_files_for_custom_chart_component_writes_values_and_uses_its_own_chart():
    changes = target.files_for("demo", "dev", {"myservice": custom_resolved_component}, {"myservice": record("myservice")})
    content = yaml.safe_load(next(c.content for c in changes if "myservice" in c.path))
    assert content["replicas"] == 2  # from comp.values, not a golden-path field
    assert content["deployground"]["component"] == "myservice"
```
For `ApplicationTarget`, the equivalent: the written Application manifest's
`spec.sources[0]` matches `chart_source(custom_resolved_component.chart)`, and
`spec.sources[0].helm.values` parses to `comp.values`.

- [ ] **Step 2: Run to verify failures.**

- [ ] **Step 3: Implement.** In each target's component-building code path (`files_for`'s
  loop, and `ApplicationTarget._application`-equivalent), branch on whether `comp` is a
  `ResolvedCustomComponent`: if so, the file/values content is `dict(comp.values)` (plus the
  existing metadata block) instead of the golden-path field-by-field dict; the Application's
  chart source for that one Application is `chart_source(comp.chart)` instead of
  `chart_source(self._cfg.workloadChart)`. Golden-path's existing branch is untouched.

- [ ] **Step 4: Run to verify passes**, then full suite — pay attention to the Review Focus
  item about a golden-path and a custom-chart component coexisting in one stack: add one
  test per target exercising both together in a single `files_for()` call.

- [ ] **Step 5: Commit**

```bash
git add src/deployground/deploytarget/applicationset.py src/deployground/deploytarget/application.py tests/deploytarget/test_applicationset.py tests/deploytarget/test_application.py
git commit -m "feat(deploytarget): render custom-chart components with their own chart and values"
```

---

### Task 6: Slack form — multi-field overrides

**Files:**
- Modify: `src/deployground/slack/forms.py`
- Test: `tests/slack/test_forms.py`

**Interfaces:**
- Produces: `DeployInputs.overrides: dict[str, dict[str, str]]` (was `dict[str, str]`).
- Produces: `build_override_blocks(entry: CatalogEntry, selected_components: list[str],
  values: Mapping[str, dict[str, str]] | None = None) -> list[Block]` — for each selected
  component, iterate its `overridable` list (golden-path components get the implicit single
  `OverridableField(path="version", label="Version")` entry — add a small helper
  `_overridable_fields(comp: Component) -> list[OverridableField]` returning that implicit
  one-entry list when `not comp.is_custom_chart`, else `comp.overridable`), emitting one
  `_input` block per entry with `block_id = f"{OVERRIDE_PREFIX}{name}__{entry.path}"` and
  `label = f"{name} — {entry.label}"`, prefilled from `values[name][entry.path]` or a
  sensible default (golden-path: `comp.defaultVersion`; custom chart: the value already at
  that path via `dotpath.get_path(comp.values, entry.path)`, falling back to `""` if the
  override-target path doesn't pre-exist in `values` — e.g. `chart.version`, which lives on
  `comp.chart.version` not inside `comp.values`; handle that one path specially, reading
  `comp.chart.version` instead of `dotpath.get_path`).
- Produces: `parse_submission()`'s override-parsing loop changes from
  `block_id[len(OVERRIDE_PREFIX):]` (whole remainder = component name) to splitting on the
  first `__`: `component_name, path = block_id[len(OVERRIDE_PREFIX):].split("__", 1)`,
  building `overrides.setdefault(component_name, {})[path] = value`.

- [ ] **Step 1: Write failing tests**

```python
def test_build_override_blocks_golden_path_emits_one_field_labeled_version():
    blocks = build_override_blocks(entry_with_golden_path_component, ["api"])
    assert len(blocks) == 1
    assert blocks[0]["block_id"] == "override::api__version"
    assert blocks[0]["label"]["text"] == "api — Version"

def test_build_override_blocks_custom_chart_emits_one_field_per_overridable_entry():
    blocks = build_override_blocks(entry_with_custom_chart_component, ["myservice"])
    ids = {b["block_id"] for b in blocks}
    assert ids == {"override::myservice__image.tag", "override::myservice__chart.version", "override::myservice__replicas"}

def test_build_override_blocks_custom_chart_no_overridable_entries_emits_nothing():
    # a custom-chart component with overridable=[] yields zero blocks for that component

def test_parse_submission_groups_overrides_by_component_and_path():
    inputs, errors = parse_submission(view_state_with_two_fields_on_myservice)
    assert inputs.overrides["myservice"] == {"image.tag": "v9", "replicas": "5"}
```

- [ ] **Step 2: Run to verify failures.**

- [ ] **Step 3: Implement** as described above. Update `build_deploy_modal`'s call sites
  that read/write `prefill.overrides` (the `chosen = [c for c in sorted(prefill.overrides)
  ...]` line and `build_override_blocks(catalog_entry, chosen, prefill.overrides)` call)
  for the new nested shape — `prefill.overrides` is still keyed by component name at the
  top level, so `sorted(prefill.overrides)` is unchanged; only the VALUE per component
  changes shape, which `build_override_blocks` now expects.

- [ ] **Step 4: Run to verify passes**, then full suite.

- [ ] **Step 5: Commit**

```bash
git add src/deployground/slack/forms.py tests/slack/test_forms.py
git commit -m "feat(forms): render one field per operator-declared overridable entry"
```

---

### Task 7: Slack actions — thread the new overrides shape through

**Files:**
- Modify: `src/deployground/slack/actions.py`
- Test: `tests/slack/test_actions.py`

**Interfaces:**
- Consumes: `DeployInputs.overrides: dict[str, dict[str, str]]` (Task 6).
- Produces: `handle_modal_change()`'s override-rebuilding loop reading ALL of a selected
  component's fields back from `values` (not just one), and `_default_version()` renamed/
  generalized to `_default_overrides(deps, catalog, component) -> dict[str, str]` returning
  the right default dict for either component shape (golden-path:
  `{"version": comp.defaultVersion}`; custom chart: one entry per `comp.overridable`, each
  defaulted from `comp.values` via `dotpath.get_path` where present, else `""`, with
  `chart.version` defaulted from `comp.chart.version`).
- `handle_prefill()`'s `dict(data.get("overrides") or {})` becomes a nested-dict reconstruction
  (`{k: dict(v) for k, v in (data.get("overrides") or {}).items()}`) since the stored JSON
  blob's shape changes too.

- [ ] **Step 1: Write failing tests**

```python
def test_handle_modal_change_rebuilds_all_fields_for_reselected_custom_chart_component():
    # simulate a view with myservice freshly added to OVERRIDE_PICKER_BLOCK's selection,
    # assert the rebuilt view's blocks include all 3 of myservice's overridable fields,
    # each defaulted correctly

def test_default_overrides_golden_path_returns_version_default():
    assert _default_overrides(deps, "demo", "api") == {"version": "1.0.0"}

def test_default_overrides_custom_chart_returns_one_entry_per_overridable_field():
    assert _default_overrides(deps, "demo", "myservice") == {
        "image.tag": "latest", "chart.version": "1.4.0", "replicas": "2",
    }

def test_handle_prefill_reconstructs_nested_overrides_dict():
    # body carrying a JSON "value" with overrides={"myservice": {"replicas": "2"}} round-trips
```

(Read the existing tests in this file first for the `body`/`Deps`/fake-client fixture style
and reuse it.)

- [ ] **Step 2: Run to verify failures.**

- [ ] **Step 3: Implement** as described above.

- [ ] **Step 4: Run to verify passes**, then the FULL suite (`python -m pytest -q`) — this
  is the last task touching shared plumbing, so this is where any remaining ripple across
  `tests/e2e/test_deploy_flow.py` or `tests/lifecycle/test_service.py` (which both go
  through `DeployInputs`/`RenderInputs.overrides` end-to-end) would surface. Fix any such
  ripple's expected shapes (nested dict instead of flat) rather than leaving the suite red;
  note any such fix in the task report, same as a prior plan's Task 4 ripple.

- [ ] **Step 5: Commit**

```bash
git add src/deployground/slack/actions.py tests/slack/test_actions.py
git commit -m "feat(actions): thread the per-path overrides shape through modal rebuild and prefill"
```

---

### Task 8: Docs

**Files:**
- Modify: `docs/quickstart.md`
- Modify: `examples/config.yaml`

**Interfaces:**
- None (documentation/example only).

- [ ] **Step 1: Document the custom-chart component shape** in `docs/quickstart.md`'s
  catalog-configuration section: `chart`/`values`/`imageRef`/`ingressRef`/`overridable`,
  with one short worked example (reuse the `myservice` example from the design doc), and a
  sentence on the `allowedRegistries`/`ingressDomains` guardrails still applying via
  `imageRef`/`ingressRef`.

- [ ] **Step 2: Add one custom-chart example component** to `examples/config.yaml` alongside
  the existing golden-path example(s), so a new operator sees a working reference.

- [ ] **Step 3: Commit**

```bash
git add docs/quickstart.md examples/config.yaml
git commit -m "docs: document per-component custom charts"
```

## Self-Review

1. **Spec coverage**: chart ref per component (Task 2/3/5), operator-authored `values`
   layered by Helm (Task 5, relies on Helm's own composition — no deployground-side chart
   defaults merging needed, consistent with the design doc), generic `overridable` fields
   rendered in the form (Task 6), chart-version-as-an-overridable-path (Task 3/6/7),
   `allowedRegistries`/`ingressDomains` guardrails via `imageRef`/`ingressRef` at both
   config-load and deploy time (Task 2, Task 4), private chart/image registry access
   (explicitly Non-goals requiring no code — correctly absent from this plan). All covered.
2. **Step scan**: every code step names exact signatures/field names/test names; the
   Task 5/Task 7 implementation steps leave the exact discrimination mechanism
   (`isinstance` vs a shared marker) to the implementer, which is a reasonable open choice
   per Task 2's existing `is_custom_chart` property already providing one clean option.
3. **Type consistency**: `FieldRef`/`OverridableField`/`ResolvedCustomComponent` names and
   fields are used identically across Tasks 2-7; `dotpath.get_path`/`set_path` signatures
   from Task 1 are used identically in Tasks 2-4.
4. **Review Focus**: each line has an owning task and test, listed above each Review Focus
   bullet in parentheses.
5. **Proportion**: 8 tasks, each touching 1-3 files; proportionate to a design doc of this
   size (the doc itself is mostly prose/rationale, not code, and this plan is the first
   place the actual signatures appear — consistent with the design doc's own "Left to the
   plan" section naming exactly these items).
