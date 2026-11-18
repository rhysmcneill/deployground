# Namespace Cleanup on Destroy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Destroying a stack also deletes its namespace (and everything ArgoCD left inside
it), by giving every stack a reserved `_system` pseudo-component whose file renders a bare
labelled `Namespace` instead of a workload, created and destroyed by the exact same
file-write/delete calls every real component already goes through.

**Architecture:** Both `DeployTarget` implementations (`ApplicationSetTarget`,
`ApplicationTarget`) unconditionally append a `_system` component file on every
`files_for()` call and its removal on every `delete_files_for()` call — independent of
whatever real components were passed in. `_system` is excluded from `StackIndex` (never
becomes part of `StackRecord.components`, never flows through catalog-resolution logic).
The golden-path chart gains a `namespaceOnly` values flag that swaps the rendered
resource from a workload to a `Namespace`.

**Tech Stack:** Python (pydantic config models), Helm (golden-path chart), pytest.

**Spec:** `docs/superpowers/specs/2026-11-18-namespace-cleanup-design.md`

## Global Constraints

- Reserved component name is the literal string `_system` (constant `RESERVED_COMPONENT`
  in `src/deployground/metadata.py`). No other code introduces a second spelling of it.
- `_system`'s file must contain a complete, valid `deployground:` metadata block (every key
  in `metadata._BLOCK_KEYS`) so both `parse_component_file` and the existing ArgoCD git
  generator's `{{deployground.*}}` substitutions keep working — it is deliberately NOT a
  special-cased file shape.
- `_system` must never appear in `StackRecord.components`, `CatalogEntry.components`, or
  any user-facing listing.
- The golden-path chart's existing behavior (ingress tls scoping to `ingressClassName ==
  "tailscale"`, numeric port, etc. from prior work) must be unaffected by the
  `namespaceOnly` addition — `namespaceOnly: true` never carries `port`/`ingressHost`, so
  `service.yaml`/`ingress.yaml`'s existing guards already no-op for it; only
  `deployment.yaml` needs a new guard.
- Golden-path chart version bumps 0.4.2 → 0.5.0 (new feature, backward compatible:
  `namespaceOnly` defaults to unset/false).

## Review Focus

- A catalog entry with a real component literally named `_system` must be rejected at
  config-load time, not silently collide with the reserved pseudo-component later.
- `extend()` on a stack must not attempt to regenerate or touch the `_system` file through
  the catalog-resolution path (it isn't a catalog component) — it's still appended
  unconditionally by `files_for`, but never via `resolved`/`still_cataloged`.
- `destroy()` must delete the `_system` file even though it's absent from
  `StackRecord.components` (the thing `delete_files_for` is normally called with).
- `StackIndex.rebuild()` must skip `_system` files without logging them as a parse warning
  (they parse fine; they're just not a real component for indexing purposes).
- `deployment.yaml` rendering with `namespaceOnly: true` and no `image` must not fail on
  the `required "image is required"` guard — it must not render at all.

---

### Task 1: Golden-path chart — `namespaceOnly` support

**Files:**
- Modify: `helm/charts/golden-path/templates/deployment.yaml`
- Create: `helm/charts/golden-path/templates/namespace.yaml`
- Modify: `helm/charts/golden-path/Chart.yaml` (version `0.4.2` → `0.5.0`)
- Test: manual `helm template` verification (this chart has no automated test harness
  today — follow existing convention, verified via `helm template` as prior ingress work
  was)

**Interfaces:**
- Produces: a chart that, given `namespaceOnly: true` and no other values, renders exactly
  one `Namespace` object named `{{ .Release.Namespace }}` labelled
  `deployground.io/managed: "true"`, and renders nothing else (no Deployment, Service,
  Ingress).

- [ ] **Step 1: Wrap `deployment.yaml`'s existing content in a guard**

  Add `{{- if not .Values.namespaceOnly }}` as the first line and `{{- end }}` as the last
  line of the existing file content (no other changes to the template body).

- [ ] **Step 2: Create `namespace.yaml`**

```yaml
{{- if .Values.namespaceOnly }}
apiVersion: v1
kind: Namespace
metadata:
  name: {{ .Release.Namespace }}
  labels:
    deployground.io/managed: "true"
{{- end }}
```

- [ ] **Step 3: Bump `Chart.yaml` version to `0.5.0`**

- [ ] **Step 4: Verify with `helm template`**

  Run, from `helm/charts/golden-path`:
  ```bash
  helm template test . --set namespaceOnly=true --namespace demo-stack
  ```
  Expected: output contains only a `Namespace` named `demo-stack` with the managed label,
  nothing else.

  ```bash
  helm template test . --set image=ghcr.io/org/app --set port=8080 --namespace demo-stack
  ```
  Expected: output unchanged from before this task (Deployment + Service, no Namespace) —
  confirms `namespaceOnly` unset/false doesn't regress existing renders.

- [ ] **Step 5: Commit**

```bash
git add helm/charts/golden-path
git commit -m "feat(golden-path): add namespaceOnly mode for the stack-cleanup namespace resource"
```

---

### Task 2: Reserved component name constant + catalog guardrail

**Files:**
- Modify: `src/deployground/metadata.py`
- Modify: `src/deployground/config/models.py`
- Test: `tests/config/test_models.py`, `tests/test_metadata.py`

**Interfaces:**
- Produces: `metadata.RESERVED_COMPONENT: str = "_system"`, importable by `index.py`,
  `applicationset.py`, `application.py`.
- Produces: `CatalogEntry` validation raising `ValueError` when `_system` is a key of
  `components`.

- [ ] **Step 1: Add the constant**

  In `src/deployground/metadata.py`, alongside `LABEL_MANAGED` etc.:
  ```python
  RESERVED_COMPONENT = "_system"
  ```
  with a one-line comment: reserved for the per-stack namespace-cleanup pseudo-component;
  never a real catalog component name.

- [ ] **Step 2: Write the failing test for the catalog guardrail**

  In `tests/config/test_models.py`:
  ```python
  def test_catalog_entry_rejects_reserved_component_name():
      with pytest.raises(ValidationError, match="_system"):
          CatalogEntry.model_validate({"components": {"_system": {"image": "x", "defaultVersion": "1"}}})
  ```
  (Match the existing test file's import/style for `ValidationError` — see how
  `test_models.py` already asserts on other `model_validator` failures, e.g. the
  `AccessRule` one-principal test, and mirror that exactly.)

- [ ] **Step 3: Run it to verify it fails**

  `pytest tests/config/test_models.py -k reserved_component -v`
  Expected: FAIL (no such validation exists yet).

- [ ] **Step 4: Implement the validator**

  In `src/deployground/config/models.py`, add to `CatalogEntry`:
  ```python
  @model_validator(mode="after")
  def _no_reserved_component_name(self) -> Self:
      if RESERVED_COMPONENT in self.components:
          raise ValueError(f"component name {RESERVED_COMPONENT!r} is reserved")
      return self
  ```
  Import `RESERVED_COMPONENT` from `deployground.metadata` at the top of the file.

- [ ] **Step 5: Run it to verify it passes**

  `pytest tests/config/test_models.py -k reserved_component -v`

- [ ] **Step 6: Commit**

```bash
git add src/deployground/metadata.py src/deployground/config/models.py tests/config/test_models.py
git commit -m "feat(config): reserve the _system component name for namespace cleanup"
```

---

### Task 3: `StackIndex` skips `_system` files

**Files:**
- Modify: `src/deployground/index.py`
- Test: `tests/test_index.py`

**Interfaces:**
- Consumes: `metadata.RESERVED_COMPONENT` (Task 2).
- Produces: `StackIndex.rebuild()` that silently ignores any file whose parsed
  `ComponentRecord.component == RESERVED_COMPONENT` — it contributes to no `StackRecord`,
  logs no warning, and is otherwise treated as if it didn't exist for indexing purposes.

- [ ] **Step 1: Write the failing test**

  In `tests/test_index.py`, follow the existing fixture style (check how other tests in
  this file construct a fake `GitClient.list_files` return and call `rebuild()`/`get()`).
  Add a file for stack `demo` with a valid `deployground:` block whose `component` is
  `_system`, alongside one real component file for the same stack. After `rebuild()`:
  - `index.get("demo").components` has exactly the real component, no `_system` key.
  - No warning was logged for the `_system` file (assert on `caplog` if the file already
    uses it for the "skipping" warning case; otherwise this is implicit — just assert the
    component dict is correct and no exception propagated).

- [ ] **Step 2: Run it to verify it fails**

  `pytest tests/test_index.py -k system -v`
  Expected: FAIL (`_system` currently ends up as a component in `stack.components`).

- [ ] **Step 3: Implement the skip**

  In `index.py`'s `rebuild()` loop, right after `record = parse_component_file(...)`
  succeeds, before the `stacks.get(record.stack)` block:
  ```python
  if record.component == RESERVED_COMPONENT:
      continue
  ```
  Import `RESERVED_COMPONENT` from `deployground.metadata`.

- [ ] **Step 4: Run it to verify it passes**

  `pytest tests/test_index.py -k system -v`

- [ ] **Step 5: Commit**

```bash
git add src/deployground/index.py tests/test_index.py
git commit -m "fix(index): exclude the reserved _system component from the stack index"
```

---

### Task 4: `ApplicationSetTarget` writes/removes the `_system` file

**Files:**
- Modify: `src/deployground/deploytarget/applicationset.py`
- Test: `tests/deploytarget/test_applicationset.py`

**Interfaces:**
- Consumes: `metadata.RESERVED_COMPONENT` (Task 2).
- Produces: `files_for()` always includes one extra `FileChange` (beyond the real
  components passed in) at path `self._path(stack, env, RESERVED_COMPONENT)`, whose
  content is `{"namespaceOnly": True, "deployground": {...}}` — a complete metadata block
  built from an arbitrary one of the `records` values passed in (stack/catalog/owner/
  ownerEmail/env/threadTs/channel copied verbatim, `component` overridden to
  `RESERVED_COMPONENT`, `version` set to `""`, `expiresAt` copied verbatim).
  `delete_files_for()` always includes one extra `FileChange` with `content=None` at the
  same path, regardless of `component_names`.

- [ ] **Step 1: Write the failing test for `files_for`**

  In `tests/deploytarget/test_applicationset.py`, extend (or add alongside) the existing
  `files_for` test: call `target.files_for("demo", "dev", {name: resolved(name) for name in
  NAMES}, {name: record(name) for name in NAMES})` and assert:
  - The returned list has `len(NAMES) + 1` entries.
  - Exactly one entry's `path` equals
    `CFG.pathTemplate.format(env="dev", stack="demo", component="_system")`.
  - That entry's parsed YAML content has `namespaceOnly: true` and a `deployground:` block
    with `component: "_system"`, `stack: "demo"`, and the same `owner`/`env` as the other
    entries' blocks.

- [ ] **Step 2: Write the failing test for `delete_files_for`**

  Call `target.delete_files_for("demo", "dev", NAMES)` (the real component names only, as
  `destroy()` actually calls it — `_system` is deliberately NOT in this list, since it was
  excluded from `StackRecord.components` in Task 3) and assert the returned list has
  `len(NAMES) + 1` entries, with one extra entry at the `_system` path and `content=None`.

- [ ] **Step 3: Run both to verify they fail**

  `pytest tests/deploytarget/test_applicationset.py -v`

- [ ] **Step 4: Implement in `applicationset.py`**

  In `files_for()`, after the existing `for name, comp in components.items()` loop, build
  and append the extra change:
  ```python
  if records:
      base = next(iter(records.values()))
      block = to_metadata_block(
          replace(base, component=RESERVED_COMPONENT, version="")
      )
      changes.append(
          FileChange(
              repo=self._repo_ref,
              path=self._path(stack, env, RESERVED_COMPONENT),
              content=yaml.safe_dump({"namespaceOnly": True, BLOCK_KEY: block}, sort_keys=False),
          )
      )
  ```
  (`replace` from `dataclasses`, already a frozen dataclass per `ComponentRecord`.) The
  `if records:` guard matches the plan's Global Constraints note that `_system` is always
  written alongside at least one real component — `files_for` is never called with an
  empty `records` dict in practice (deploy always resolves ≥1 catalog component; extend's
  `records` argument is the stack's full original component set). Import `replace` and
  `RESERVED_COMPONENT`.

  In `delete_files_for()`, append one more entry unconditionally:
  ```python
  changes = [...]  # existing list-comprehension result
  changes.append(
      FileChange(
          repo=self._repo_ref,
          path=self._path(stack, env, RESERVED_COMPONENT),
          content=None,
      )
  )
  return changes
  ```

- [ ] **Step 5: Run tests to verify they pass**

  `pytest tests/deploytarget/test_applicationset.py -v`

- [ ] **Step 6: Commit**

```bash
git add src/deployground/deploytarget/applicationset.py tests/deploytarget/test_applicationset.py
git commit -m "feat(applicationset-target): write/remove the reserved _system namespace file"
```

---

### Task 5: `ApplicationTarget` writes/removes the `_system` Application

**Files:**
- Modify: `src/deployground/deploytarget/application.py`
- Test: `tests/deploytarget/test_application.py`

**Interfaces:**
- Consumes: `metadata.RESERVED_COMPONENT` (Task 2), same chart source helper
  (`chart_source`) already imported in this file.
- Produces: `files_for()` always includes one extra `FileChange` at
  `self._path(stack, env, RESERVED_COMPONENT)` holding a literal Argo `Application`
  manifest (same shape as `_application()`'s output) whose `spec.sources[0].helm.values`
  is `{"namespaceOnly": true}` and whose `metadata.name` is `f"{stack}-{RESERVED_COMPONENT}"`
  (sanitize if needed — `_system` contains no characters invalid in a k8s name once
  joined with `-`, so no further sanitization is required).
  `delete_files_for()` always includes one extra `FileChange` with `content=None` at the
  same path, regardless of `component_names`.

- [ ] **Step 1: Read `test_application.py`'s existing fixtures first**

  Match its exact style for constructing a `target`/`record`/expected annotations before
  writing new assertions — don't invent a divergent fixture shape.

- [ ] **Step 2: Write the failing test for `files_for`**

  Call `target.files_for(...)` with the same shape of inputs the existing tests use, and
  assert: one extra entry at the `_system` path, parseable as YAML with
  `kind: Application`, `spec.sources[0].helm.values` parsing (it's itself a YAML string,
  per the existing `_application()` convention) to `{"namespaceOnly": True}`, and
  `metadata.annotations` present (reuse `to_annotations` with the same synthetic record
  approach as Task 4) so `parse_component_file`'s `Application`-shape branch can read it
  back if needed — though per Task 3 it will be skipped by component name regardless.

- [ ] **Step 3: Write the failing test for `delete_files_for`**

  Same shape as Task 4 Step 2, adapted to this target.

- [ ] **Step 4: Run both to verify they fail**

  `pytest tests/deploytarget/test_application.py -v`

- [ ] **Step 5: Implement in `application.py`**

  Add a small sibling builder next to `_application()`:
  ```python
  def _namespace_application(self, stack: str, cluster: str, record: ComponentRecord) -> dict[str, Any]:
      source = chart_source(self._cfg.workloadChart)
      source["helm"] = {"values": yaml.safe_dump({"namespaceOnly": True}, sort_keys=False)}
      return {
          "apiVersion": "argoproj.io/v1alpha1",
          "kind": "Application",
          "metadata": {
              "name": f"{stack}-{RESERVED_COMPONENT}",
              "namespace": "argocd",
              "labels": {
                  LABEL_STACK: stack,
                  LABEL_COMPONENT: RESERVED_COMPONENT,
                  LABEL_MANAGED: "true",
              },
              "annotations": to_annotations(record),
              "finalizers": [FINALIZER],
          },
          "spec": {
              "project": "default",
              "sources": [source],
              "destination": {"name": cluster, "namespace": stack},
              "syncPolicy": {
                  "automated": {"prune": True, "selfHeal": True},
                  "syncOptions": ["CreateNamespace=true"],
                  "managedNamespaceMetadata": {"labels": {LABEL_MANAGED: "true"}},
              },
          },
      }
  ```
  In `files_for()`, after the existing list comprehension, append one `FileChange` built
  from `self._namespace_application(stack, cluster, replace(next(iter(records.values())),
  component=RESERVED_COMPONENT, version=""))` guarded by `if records:` (mirrors Task 4).

  In `delete_files_for()`, append the extra removal entry exactly as in Task 4 Step 4.

- [ ] **Step 6: Run tests to verify they pass**

  `pytest tests/deploytarget/test_application.py -v`

- [ ] **Step 7: Commit**

```bash
git add src/deployground/deploytarget/application.py tests/deploytarget/test_application.py
git commit -m "feat(application-target): write/remove the reserved _system namespace Application"
```

---

### Task 6: Docs

**Files:**
- Modify: `docs/quickstart.md`

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Add a short note**

  In the section introducing the catalog (`components:` under `catalog:`), add one
  sentence: the component name `_system` is reserved by deployground itself (it owns each
  stack's namespace lifecycle so `destroy` can clean the namespace up) and cannot be used
  as a real component name.

- [ ] **Step 2: Commit**

```bash
git add docs/quickstart.md
git commit -m "docs: note the reserved _system component name"
```

## Self-Review

1. **Spec coverage**: destroy-deletes-namespace (Tasks 1, 4, 5), atomic-with-whole-stack-
   destroy (achieved structurally by Tasks 4/5 piggybacking on the real
   files_for/delete_files_for calls, no new invariant), no new user-facing config (Task 2
   only adds a validation, not a new field), reserved-name collision guard (Task 2). All
   covered.
2. **Step scan**: every code step has an exact signature/snippet; no "handle edge cases"
   placeholders.
3. **Type consistency**: `RESERVED_COMPONENT` used identically (import from `metadata`) in
   Tasks 2-5; `ComponentRecord`/`to_metadata_block`/`to_annotations` signatures match their
   Task 2 (unmodified) definitions.
4. **Review Focus**: each line above maps to a task's tests (catalog guardrail → Task 2;
   extend not touching `_system` via catalog resolution → inherent in Task 4's design,
   exercised implicitly since `files_for`'s `records` param in the extend path never
   contains `_system`, per Task 3 — no separate test needed beyond Task 3's index test;
   destroy deleting `_system` despite its absence from `StackRecord.components` → Task 4/5
   Step 2 tests calling `delete_files_for` with only real names; index skip-without-warning
   → Task 3; deployment.yaml not failing on missing image → Task 1 Step 4).
5. **Proportion**: six small tasks, each touching 1-2 files; proportionate to a five-file
   feature.
