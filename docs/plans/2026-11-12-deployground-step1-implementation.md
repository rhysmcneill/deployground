# Deployground Step 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Slack app (Socket Mode) where a user deploys a stack of one or many pre-built images to Kubernetes via a GitOps PR, with per-component versions, access guardrails, automatic expiry, and live status back in Slack.

**Architecture:** A composition-root Python service wires pluggable providers (git host, deploy target, status source, authz) behind protocols. The flow is: Slack form → authz → capability `render()` → one Git PR of per-component files (each with a `deployground:` metadata block) → a bootstrapped ArgoCD ApplicationSet turns them into Applications → status (Argo Notifications push, Argo API poll fallback, configured per Argo instance) is aggregated per stack → the Slack thread updates live. A reaper expires stacks. **No database:** Git is the source of truth; the bot rebuilds an in-memory stack index on startup by listing/parsing the component files' `deployground:` blocks (Git creds only, topology-agnostic). Single replica.

**Tech Stack:** Python 3.12+, slack-bolt (Socket Mode), Pydantic v2 (config + schema), ghapi (GitHub), httpx (Argo API pull fallback), PyYAML, pytest + pytest-asyncio, ruff. No database/ORM; no Kubernetes client needed for core state.

**Spec:** `docs/2026-11-12-slack-deploy-core-design.md` (this repo). Executors read both.

## Global Constraints
- Language: **Python 3.12+ with slack-bolt in Socket Mode** — the only inbound listener is a cluster-internal webhook for status; everything else is outbound.
- **Default-deny** authorization; every interaction is authz-checked, and submits are re-checked.
- Secrets come only from env vars / secret refs (`tokenEnv: ...`); **never inline a secret** in config or code.
- **One PR per stack** — all component FileChanges for a create or destroy go in a single PR (atomic).
- Every generated Application is stamped with labels `deployground.io/stack=<id>` and `deployground.io/component=<name>`, plus the notification subscription annotation.
- Config (including the `stacks` catalog) **hot-reloads** without restart; the app validates merged config at startup and refuses to start on error.
- **No database and no cluster creds for core state** — the durable record is the `deployground:` block in each component file in Git; the bot rebuilds an in-memory index from Git on startup. `warnedAt` is in-memory; single replica. The status connection to each ArgoCD is pluggable and affects live status only.
- Lifecycle defaults: TTL default **24h**, warn **1h** before expiry, reaper interval **1m**. `ttl: none` only for admins.
- Default path template (when configured): `{env}/{stack}/{component}/config.yaml`. Target repo+path is required config, no built-in location.
- **No `deployground.yaml` in source repos** — the catalog is config only.
- License Apache-2.0; name "Deployground"; no employer repos/names/infra in code, docs, or examples.

## Review Focus
- **Oversized Slack views** — a catalog or override list beyond Slack's ~100-option / 100-block limits must degrade (switch the stack picker to `external_select`, paginate/summarize overrides) rather than error. Pinned in Task 15.
- **Invalid or dangling config** — a stack referencing an unknown registry, environment, or repo, or a missing `deployTarget.pathTemplate`, must fail validation with a clear message, not at deploy time. Pinned in Task 2.
- **Duplicate/replayed events** — retried Slack interactions and repeated status callbacks must be idempotent, keyed by stack/component id. Pinned in Task 12 (status dedup) and Task 16 (progress update keyed by `thread_ts`).
- **Partial stack failure** — if any component is Failed/Degraded, the stack aggregates to Failed and never reports success. Pinned in Task 12 (`test_any_failed_is_failed`).
- **Membership lookup failure / free-plan usergroups** — if a Slack usergroup/channel lookup errors or usergroups are unavailable, authz denies gracefully (default-deny), never crashes or fails open. Pinned in Task 10.
- **Bot restart** — with no database, the reaper and My stacks must keep working after a restart by rebuilding the index purely from the `deployground:` blocks in Git; a malformed/legacy file must be skipped with a log, not crash the rebuild. Pinned in Tasks 7 and 21.

---

### Task 1: Project scaffold
**Files:**
- Create: `pyproject.toml`, `src/deployground/__init__.py`, `tests/__init__.py`, `ruff.toml`, `.github/workflows/ci.yml`, `LICENSE` (Apache-2.0), `README.md`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Produces: the `deployground` package importable; `pytest` and `ruff` run in CI.

- [ ] **Step 1: Write the failing test** — `tests/test_smoke.py::test_package_imports` imports `deployground` and asserts `deployground.__version__` is a non-empty str.
- [ ] **Step 2: Run** `pytest tests/test_smoke.py -v` → FAIL (module/attr missing).
- [ ] **Step 3:** Create `pyproject.toml` (project name `deployground`, Python ≥3.12, deps above as optional groups: runtime + `dev`), set `__version__` in `src/deployground/__init__.py`, Apache-2.0 LICENSE, ruff config, CI running `ruff check` + `pytest`.
- [ ] **Step 4: Run** `pytest -v` → PASS.
- [ ] **Step 5: Commit** `chore: scaffold deployground package, CI, license`.

---

### Task 2: Config models + validation
**Files:**
- Create: `src/deployground/config/models.py`
- Test: `tests/config/test_models.py`

**Interfaces:**
- Produces: Pydantic models `Config`, `RepoRef`, `Environment`, `StackDef`, `Component`, `CapabilityCfg`, `AccessRule`, `DeployTargetCfg`, `StatusSourceCfg`, `Lifecycle`. (No store model — there is no database. Config models are suffixed `Cfg` where a runtime protocol shares the name: `DeployTargetCfg` vs the `DeployTarget` protocol in Task 8, `StatusSourceCfg` vs the `StatusSource` protocol in Task 12.) Key fields:
  - `Component(image: str, defaultVersion: str, port: int | None, replicas: int = 1, ingressHost: str | None, env: dict[str,str] = {}, secretRefs: list[str] = [])`
  - `StackDef(components: dict[str, Component])`
  - `Environment(cluster: str, repo: str, autoMerge: bool, ttl: TtlPolicy, requiresApproval: bool)` where `TtlPolicy(default: str|None, max: str|None)` (`"none"`→no expiry)
  - `AccessRule(user|group|channel: str (exactly one), environments: list[str] = ["*"], admin: bool = False)`
  - `DeployTargetCfg(type: Literal["applicationset","application"], repo: str, pathTemplate: str)`
  - `StatusSourceCfg(type: Literal["argocd-notifications","argocd-api"], webhookPath: str | None, url: str|None, tokenRef: TokenRef|None, pollInterval: str|None)`
  - `Config.validate_refs()` cross-checks: every `Environment.repo` and `DeployTargetCfg.repo` exists in `repos`; every `Component.image` registry is in its capability's `allowedRegistries`; every `AccessRule.environments` entry is a known env or `"*"`; exactly one principal per rule.

- [ ] **Step 1: Write failing tests** in `tests/config/test_models.py`:
  - `test_minimal_valid_config_parses` — a dict with one repo, one env, one single-component stack, `access.rules` with one user, `deployTarget` and `statusSource` parses into `Config`.
  - `test_rule_requires_exactly_one_principal` — a rule with both `user` and `group` raises `ValidationError`.
  - `test_env_referencing_unknown_repo_fails` — `validate_refs()` raises with a message naming the missing repo.
  - `test_stack_image_outside_allowed_registry_fails` — a component image not under `allowedRegistries` raises.
  - `test_ttl_none_parses` — `ttl.default == "none"` is accepted and means no expiry.
- [ ] **Step 2: Run** `pytest tests/config/test_models.py -v` → FAIL.
- [ ] **Step 3: Implement** the models + `validate_refs()` in `config/models.py`. Durations parsed by a helper `parse_duration(str) -> timedelta | None` (`"none"`→None); keep it here, re-used later.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(config): typed config models with cross-reference validation`.

---

### Task 3: Config loader with layering + hot-reload
**Files:**
- Create: `src/deployground/config/loader.py`
- Test: `tests/config/test_loader.py`

**Interfaces:**
- Consumes: `Config` (Task 2).
- Produces: `load_config(path: Path, env: Mapping[str,str]) -> Config` (built-in defaults → YAML file → env-var overrides, prefix `DEPLOYGROUND_`, then `validate_refs()`); `ConfigWatcher.current() -> Config` and `ConfigWatcher.reload()` that re-reads and atomically swaps, keeping the previous `Config` if the new one fails validation (logs the error).

- [ ] **Step 1: Write failing tests:** `test_env_override_beats_file` (an env var overrides a file value); `test_invalid_reload_keeps_previous` (`reload()` with a now-invalid file returns the old `Config` and does not raise); `test_startup_invalid_raises` (`load_config` on invalid file raises).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** loader + watcher (`reload()` re-invokes `load_config`; swap under a lock).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(config): layered loader with safe hot-reload`.

---

### Task 4: Catalog + version resolution
**Files:**
- Create: `src/deployground/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `Config`, `StackDef`, `Component`.
- Produces: `Catalog(config_provider)` with `list_stacks() -> list[str]`, `get(stack: str) -> StackDef`, and `resolve_versions(stack: str, overrides: dict[str,str]) -> dict[str, ResolvedComponent]` where `ResolvedComponent` is a `Component` with `version` set from `overrides[name]` else `defaultVersion`. Raises `UnknownStack`/`UnknownComponent` on bad input.

- [ ] **Step 1: Write failing tests:** `test_single_service_is_stack_of_one`; `test_resolve_uses_default_when_no_override`; `test_resolve_applies_override_per_component` (override one of three, other two keep defaults); `test_unknown_component_override_raises`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `Catalog`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(catalog): stack catalog with per-component version resolution`.

---

### Task 5: Metadata model + (de)serialization
**Files:**
- Create: `src/deployground/states.py`, `src/deployground/metadata.py`
- Test: `tests/test_metadata.py`

**Interfaces:**
- Produces: the shared enums `CompState{Progressing,Healthy,Failed}` and `StackState{Requested,Deploying,Running,Expiring,Destroying,Destroyed,Failed}` in `states.py` (consumed by Tasks 7, 12, 14, 16). In `metadata.py`: the `deployground:` block schema and key constants; `ComponentRecord(stack, component, owner, owner_email, env, expires_at, version, thread_ts, channel)` and `StackRecord(name, env, owner, expires_at, thread_ts, channel, components: dict[str, ComponentRecord])`; `to_metadata_block(ComponentRecord) -> dict` (goes under the component file's `deployground:` key), `parse_metadata_block(file_dict) -> ComponentRecord`, and `to_annotations(ComponentRecord) -> dict[str,str]` + label constants (`deployground.io/stack|component|managed`) used by the deploy target.

- [ ] **Step 1: Write failing tests:** `test_block_roundtrips_component_record` (record → `to_metadata_block` → `parse_metadata_block` → equal); `test_expires_at_is_rfc3339`; `test_to_annotations_has_expected_keys`; `test_label_constants` (keys exact).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** enums + records + (de)serialization.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: stack metadata model and deployground block (de)serialization`.

---

### Task 6: Git client (GitHub)
**Files:**
- Create: `src/deployground/git/base.py`, `src/deployground/git/github.py`
- Test: `tests/git/test_github.py`

**Interfaces:**
- Consumes: `RepoRef`, `FileChange` (define `FileChange(repo, path, content: str | None)` here; `content=None` means delete).
- Produces: `GitClient` protocol: `open_pr(repo: RepoRef, branch: str, title: str, changes: list[FileChange], auto_merge: bool) -> PrResult(url, number, merged: bool)` and `list_files(repo: RepoRef, path_prefix: str) -> list[tuple[str, str]]` (path, content — used by StackIndex to rebuild). `GitHubClient` implements both with ghapi (create branch, put/delete files, open PR, optionally merge; recursive tree listing for `list_files`). Network calls isolated behind a thin `_api` seam so tests inject a fake.

- [ ] **Step 1: Write failing tests** with a fake api: `test_open_pr_batches_all_changes_one_pr` (N changes → one PR, one branch); `test_delete_change_removes_file`; `test_auto_merge_merges_when_true`; `test_list_files_returns_paths_and_content_under_prefix`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** protocol + GitHub client.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(git): GitHub client opening atomic multi-file PRs`.

---

### Task 7: StackIndex (Git-backed, in-memory)
**Files:**
- Create: `src/deployground/index.py`
- Test: `tests/test_index.py`

**Interfaces:**
- Consumes: `GitClient.list_files` (Task 6), `parse_metadata_block` + `StackRecord` (Task 5), `states.py`.
- Produces: `StackIndex(git, deploy_target_cfg)` with `rebuild() -> None` (lists files under the target path(s), parses each `deployground:` block, groups by `stack` into `StackRecord`s held in memory), `get(stack) -> StackRecord | None`, `list_by_owner(user)`, `list_live()`, `list_expiring(before: datetime)`, `count_live_for(user) -> int`, and in-memory `upsert(StackRecord)` / `remove(stack)` the bot calls right after its own deploy/destroy PRs (so the cache is current without a re-list). Pure in-memory cache; the durable source is Git.

- [ ] **Step 1: Write failing tests** (fake GitClient returning component files): `test_rebuild_groups_components_by_stack`; `test_list_expiring_filters_by_time`; `test_list_by_owner`; `test_count_live_for`; `test_upsert_and_remove_update_cache`; `test_rebuild_skips_malformed_file_and_logs` (a file without a valid `deployground:` block is skipped, rebuild still succeeds).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `StackIndex`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(index): Git-backed in-memory stack index`.

---

### Task 8: Deploy target — ApplicationSet (default)
**Files:**
- Create: `src/deployground/deploytarget/base.py`, `src/deployground/deploytarget/applicationset.py`
- Test: `tests/deploytarget/test_applicationset.py`

**Interfaces:**
- Consumes: `ResolvedComponent` (Task 4), `ComponentRecord`/`to_metadata_block`/`to_annotations` + label constants (Task 5), `DeployTargetCfg` (Task 2), `FileChange` (Task 6).
- Produces: `DeployTarget` protocol: `files_for(stack: str, env: str, components: dict[str,ResolvedComponent], records: dict[str,ComponentRecord]) -> list[FileChange]`, `delete_files_for(stack: str, env: str, component_names: list[str]) -> list[FileChange]`, `bootstrap_manifests(config: Config) -> dict[str,str]`. `ApplicationSetTarget` writes one component file per component at the rendered `pathTemplate`, containing the §8 **workload fields** (incl. resolved `image`) **plus the `deployground:` metadata block** (via `to_metadata_block`). `bootstrap_manifests` returns the ApplicationSet YAML — a Git file generator over the owned subpath, sync/prune/finalizers on, templating the selector **labels** (`deployground.io/stack|component|managed`), the metadata **annotations** (from the file's `deployground:` block), and the notification subscription annotation onto each Application — plus the `argocd-notifications-cm` snippet.

- [ ] **Step 1: Write failing tests:** `test_files_one_per_component_at_path_template` (3 components → 3 FileChanges at `{env}/{stack}/{component}/config.yaml`); `test_file_contains_workload_and_deployground_block` (resolved image + the `deployground:` block with owner/expiresAt/version/threadTs); `test_delete_files_targets_all_components`; `test_bootstrap_applicationset_templates_labels_annotations_and_subscription` (assert label keys, metadata-annotation mapping, the `notifications.argoproj.io/subscribe...` annotation, prune+finalizer).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** protocol + ApplicationSet target + bootstrap manifests.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(deploytarget): ApplicationSet target + bootstrap manifests`.

---

### Task 9: Deploy target — Application CR (alternative)
**Files:**
- Create: `src/deployground/deploytarget/application.py`
- Test: `tests/deploytarget/test_application.py`

**Interfaces:**
- Produces: `ApplicationTarget` implementing the Task 8 protocol by writing one native Argo `Application` manifest per component with the selector labels, the metadata annotations (from `to_annotations`), and the subscription annotation stamped **directly** on the Application (no separate data file; the workload values go in the Application's source/helm values). `bootstrap_manifests` returns only the notifications snippet (no ApplicationSet). A `make_deploy_target(config) -> DeployTarget` factory selects by `deployTarget.type`. (Note: with this target, `StackIndex` still rebuilds from the committed Application manifests' `deployground.io/*` annotations parsed back — keep `list_files` parsing tolerant of both file shapes.)

- [ ] **Step 1: Write failing tests:** `test_application_cr_per_component_has_labels_metadata_and_subscription_annotations`; `test_factory_selects_by_type`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `ApplicationTarget` + factory.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(deploytarget): native Application CR target + factory`.

---

### Task 10: Authorization provider
**Files:**
- Create: `src/deployground/authz/base.py`, `src/deployground/authz/allowlist.py`
- Test: `tests/authz/test_allowlist.py`

**Interfaces:**
- Consumes: `AccessRule`; a `MembershipResolver` protocol `usergroups_of(user) -> set[str]`, `channels_of(user) -> set[str]` (Slack-backed in prod, faked in tests; results cached ~5 min).
- Produces: `AuthzProvider` protocol: `allowed_environments(user) -> set[str]` (`"*"` expands to all configured envs), `is_admin(user) -> bool`, `can_manage(user, stack_owner) -> bool` (owner or admin). `AllowlistAuthz(rules, resolver, all_envs)` implements it, **default-deny**, union across matching rules.

- [ ] **Step 1: Write failing tests:** `test_user_rule_grants_scoped_envs`; `test_group_and_channel_membership_grant`; `test_default_deny_when_no_rule`; `test_admin_can_manage_others`; `test_non_admin_cannot_manage_others`; `test_membership_lookup_error_denies` (resolver raises → empty set, no exception); `test_star_expands_to_all_envs`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** provider (wrap resolver calls in try/except → deny).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(authz): default-deny allowlist by user/group/channel with env scope`.

---

### Task 11: Capability — image-deploy
**Files:**
- Create: `src/deployground/capability/base.py`, `src/deployground/capability/image_deploy.py`
- Test: `tests/capability/test_image_deploy.py`

**Interfaces:**
- Consumes: `Catalog` (Task 4), `DeployTarget` (Task 8), `ComponentRecord` (Task 5), capability config guardrails.
- Produces: `Capability` protocol: `id`, `title`, `render(inputs, ctx) -> list[FileChange]`, `destroy(stack, ctx) -> list[FileChange]`, `needs_approval(inputs, env) -> bool`, `ttl_policy`. `ImageDeployCapability` resolves versions via the catalog, enforces guardrails (registry, ingress domain), builds a `ComponentRecord` per component from the form values + computed `expiresAt`, and delegates file generation to the deploy target (which writes workload fields + the `deployground:` block). `needs_approval` returns the env's `requiresApproval`.
- **Deliberate step-1 divergences from spec §6/§7 (recorded, not omissions):** (a) `form_schema()` is dropped from the protocol — with one capability, the deploy form is built directly by the form engine (Task 15); the generic schema→Block Kit renderer + `form_schema()` are an extension for when a second capability exists. (b) `status(stack)` is dropped from the protocol — status comes from the separate pluggable `StatusSource` (Tasks 12–13), not the capability.

- [ ] **Step 1: Write failing tests:** `test_render_emits_all_component_files_in_one_batch`; `test_render_file_includes_deployground_block`; `test_render_applies_version_overrides`; `test_render_rejects_disallowed_ingress_domain`; `test_destroy_emits_delete_for_all_components`; `test_needs_approval_follows_env`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** protocol + capability.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(capability): image-deploy stack capability`.

---

### Task 12: Status aggregation
**Files:**
- Create: `src/deployground/status/aggregate.py`, `src/deployground/status/base.py`
- Test: `tests/status/test_aggregate.py`

**Interfaces:**
- Consumes: `CompState`/`StackState` from `states.py` (Task 5).
- Produces: `aggregate(component_states: dict[str,CompState]) -> StackState` (**Running only when all Healthy; Failed if any Failed; else Deploying/Progressing**); `StatusSource` protocol: `start(on_change: Callable[[StatusEvent], None])`, where `StatusEvent(stack_id, component, state: CompState)`; an idempotency helper keyed by `(stack_id, component, state)` so repeated events are no-ops.

- [ ] **Step 1: Write failing tests:** `test_all_healthy_is_running`; `test_any_failed_is_failed` (one Failed among Healthy → Failed; never success); `test_mixed_is_progressing`; `test_duplicate_event_is_noop`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `aggregate`, the `StatusSource` protocol, and the idempotency guard (reuse the enums from `states.py`, Task 5 — do not redefine them).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(status): per-stack state aggregation + idempotency`.

---

### Task 13: Status sources — Notifications push + Argo API poll
**Files:**
- Create: `src/deployground/status/notifications.py`, `src/deployground/status/argocd_api.py`
- Test: `tests/status/test_sources.py`

**Interfaces:**
- Consumes: Task 12 protocol; `StatusSourceCfg` (Task 2); labels to map an incoming app → `(stack_id, component)`.
- Produces: `NotificationsSource` — an ASGI route at `statusSource.webhookPath` that parses an Argo Notifications payload, maps labels to `StatusEvent`, and calls `on_change` (default). `ArgoApiSource` — polls the Argo API every `pollInterval`, diffs app health, emits `StatusEvent` (fallback). `make_status_source(config) -> StatusSource` selects by type.

- [ ] **Step 1: Write failing tests:** `test_notifications_payload_maps_labels_to_event`; `test_notifications_unknown_labels_ignored`; `test_api_poll_emits_on_change` (fake api; health change → event); `test_factory_selects_by_type`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** both sources + factory (webhook via a small ASGI app, e.g. Starlette; poll via httpx behind a fake-able seam).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(status): Argo Notifications push + Argo API poll sources`.

---

### Task 14: Lifecycle service (deploy / extend / destroy)
**Files:**
- Create: `src/deployground/lifecycle/service.py`
- Test: `tests/lifecycle/test_service.py`

**Interfaces:**
- Consumes: `AuthzProvider`, `Capability`, `GitClient`, `StackIndex` (Task 7), env config. The submitting user/email come from the Slack form payload, not a store.
- Produces: `LifecycleService` with `deploy(user, owner_email, stack, env, overrides, ttl, thread_ts, channel) -> DeployResult`, `extend(user, stack, by) -> None`, `destroy(user, stack) -> None`. `deploy` enforces: authz env allowed (else `Denied`), `maxLivePerUser` (counted from the index), ttl ≤ env max, `ttl none` admin-only; computes `expiresAt`; builds the `ComponentRecord`s from the form values; calls `render()` (component files carry the `deployground:` block); opens **one PR** (auto-merge per env); then `index.upsert(StackRecord)`. `extend` rewrites `expiresAt` in every component file of the stack in one PR and updates the index; `destroy` enforces `can_manage`, opens the delete PR, and `index.remove`.

- [ ] **Step 1: Write failing tests** (fakes for git/index/authz/capability): `test_deploy_denied_for_disallowed_env`; `test_deploy_opens_single_pr_and_upserts_index`; `test_deploy_takes_owner_from_payload_not_store`; `test_ttl_none_requires_admin`; `test_ttl_capped_at_env_max`; `test_max_live_per_user_enforced_from_index`; `test_destroy_requires_manage_permission`; `test_extend_updates_expiry_in_all_component_files`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `LifecycleService`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(lifecycle): deploy/extend/destroy orchestration with guardrails`.

---

### Task 15: Slack form engine
**Files:**
- Create: `src/deployground/slack/forms.py`
- Test: `tests/slack/test_forms.py`

**Interfaces:**
- Consumes: `Catalog`, `AuthzProvider`.
- Produces: `build_deploy_modal(user, catalog, authz, prefill: DeployInputs | None = None) -> View` (stack dropdown; `static_select` when ≤100 stacks else `external_select`; env dropdown filtered to `allowed_environments(user)`; expires-in with default 24h; when `prefill` is given, fields are pre-populated — used by the prefilled-button entry point in Task 17); `build_override_blocks(stack, selected_components) -> list[Block]` (a version input per selected component, prefilled to default) for `views.update`; `parse_submission(view_state) -> tuple[DeployInputs | None, dict[str,str]]` returning `(inputs, errors)` where a non-empty `errors` map drives `response_action: errors` inline validation (spec §6). Note: this is the **step-1 direct form engine**; the generic `form_schema()`→Block Kit renderer is deferred until a second capability exists (Task 11 divergence).

- [ ] **Step 1: Write failing tests:** `test_modal_lists_only_allowed_envs`; `test_stack_picker_uses_external_select_over_100`; `test_override_blocks_only_for_selected_prefilled_to_default`; `test_parse_submission_extracts_overrides_and_ttl`; `test_expires_in_defaults_to_24h`; `test_prefill_populates_fields`; `test_parse_submission_returns_inline_errors_for_bad_input`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** builders + parser (Block Kit dicts).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(slack): deploy form with prefill and scoped envs and overrides`.

---

### Task 16: Progress renderer + App Home
**Files:**
- Create: `src/deployground/slack/progress.py`, `src/deployground/slack/home.py`
- Test: `tests/slack/test_progress.py`, `tests/slack/test_home.py`

**Interfaces:**
- Consumes: `StackState`/`CompState`, `StackIndex` (Task 7).
- Produces: `render_progress(stack, component_states) -> list[Block]` (per-component rows for ≤ N components, summarized counts above it; overall line) — covers both deploy (`Deploying → Running`) and teardown (`Destroying → Destroyed`); `completion_blocks(stack, ok: bool) -> list[Block]` (✅ complete @-mention owner + links, or ❌ `<component> failed` + Argo link); `build_home(user, index, authz) -> View` (My stacks rows from `index.list_by_owner(user)`, with status/expires-in and Extend/Destroy buttons); `update_thread(client, stack_record, blocks)` edits in place keyed by `thread_ts`, idempotent (re-applying the same blocks is a no-op).

- [ ] **Step 1: Write failing tests:** `test_progress_summarizes_large_stacks`; `test_progress_renders_destroying_state`; `test_completion_pings_owner`; `test_failure_names_component`; `test_home_lists_user_stacks_from_index_with_buttons`; `test_update_thread_idempotent_by_thread_ts` (same blocks twice → single edit, no duplicate post).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** renderers.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(slack): live progress renderer and App Home`.

---

### Task 17: Slack actions wiring (submit / extend / destroy+confirm)
**Files:**
- Create: `src/deployground/slack/actions.py`, `src/deployground/slack/app.py`
- Test: `tests/slack/test_actions.py`

**Interfaces:**
- Consumes: `LifecycleService`, forms (15), progress/home (16), the reaper's warn-ack hook `acknowledge_warning(stack: str) -> None` (Task 18), injected via `build_slack_app(deps)` and faked in tests.
- Produces: bolt handlers registered on the app:
  - **Entry points**: open-form shortcut; App Home "New deployment" button; a **prefilled `deploy` button** (spec §5) whose `action` payload carries field values → opens the modal via `build_deploy_modal(..., prefill=...)`.
  - `views.update` on stack select / override multiselect.
  - **Deploy submit**: on `parse_submission` errors return `response_action: errors` (inline); otherwise **post the initial progress message first** to obtain `thread_ts`/`channel`, then `LifecycleService.deploy(..., thread_ts, channel)` so files embed `threadTs`; a deploy failure (PR/Git error raised) edits the thread to **Failed** (does not leave it "Deploying").
  - **Lifecycle buttons** — on both the App Home rows and the reaper's **warning message**: **Extend 24h** → `extend`; **Keep** → acknowledge the warning (clears it via the reaper's in-memory warn-ack; no date change); **Destroy / Destroy now** → **always** an ephemeral Confirm/Cancel naming stack+env+component count → on confirm `destroy`.
  - `build_slack_app(deps) -> App` (Socket Mode).

- [ ] **Step 1: Write failing tests** (bolt in test mode / fake client): `test_destroy_requires_confirmation_before_acting` (first click shows confirm, no destroy call); `test_confirm_triggers_destroy`; `test_submit_posts_message_then_calls_deploy_with_thread_ts`; `test_submit_shows_inline_errors_on_bad_input`; `test_deploy_failure_marks_thread_failed`; `test_prefilled_button_opens_modal_with_values`; `test_keep_button_clears_warning_without_changing_date`; `test_extend_button_calls_extend`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** handlers + app builder.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(slack): deploy/extend/destroy actions with confirm`.

---

### Task 18: Reaper
**Files:**
- Create: `src/deployground/lifecycle/reaper.py`
- Test: `tests/lifecycle/test_reaper.py`

**Interfaces:**
- Consumes: `StackIndex` (Task 7), `LifecycleService`, a clock, a Slack notifier.
- Produces: `Reaper(index, lifecycle, clock, notifier, warn_before, interval)` with `tick()` (one pass), `run()` (loop), and `acknowledge_warning(stack)` (the Keep-button hook: marks the stack warned so it isn't re-warned, without changing `expiresAt` — it still expires on schedule unless extended). `tick`: reads `index.list_expiring(now + warn_before)`; stacks within `warn_before` of expiry get a one-time warning (DM + thread, Extend/Keep/Destroy now) tracked by an **in-memory** `warnedAt` set; expired stacks are destroyed; failed destroys retried next tick and alert the owner. Skips `ttl none` (no `expiresAt`).

- [ ] **Step 1: Write failing tests** (fake clock, fake index): `test_warns_once_before_expiry`; `test_destroys_after_expiry`; `test_skips_ttl_none`; `test_failed_destroy_retried_next_tick`; `test_warnedAt_in_memory_only` (a fresh reaper may re-warn once — asserts the set, not persistence); `test_acknowledge_warning_suppresses_rewarn_but_still_expires`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** reaper.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(lifecycle): reaper for expiry warnings and teardown`.

---

### Task 19: Composition root + startup
**Files:**
- Create: `src/deployground/app.py`, `src/deployground/__main__.py`
- Test: `tests/test_startup.py`

**Interfaces:**
- Consumes: all providers + factories.
- Produces: `build_app(config, env) -> Application` wiring git client, `StackIndex`, deploy target, authz (+ real Slack `MembershipResolver`), capability registry, status source (wired to update progress via `aggregate`), lifecycle, reaper, Slack app. `run()` validates config, **rebuilds the index from Git** (`StackIndex.rebuild()` — no migrations, no DB, no cluster client for core state), starts the status source (webhook ASGI server for notifications, or poller), the reaper loop, and Socket Mode. Fails fast with a clear message on bad config or unreachable Git.

- [ ] **Step 1: Write failing tests:** `test_build_app_wires_status_to_progress` (a `StatusEvent` updates component state and re-renders the thread); `test_startup_rebuilds_index_from_git`; `test_startup_fails_fast_on_invalid_config`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** composition root + entrypoint.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: composition root and service startup`.

---

### Task 20: Bootstrap command + packaging + §13 deliverables
**Files:**
- Create: `src/deployground/cli.py` (`deployground bootstrap`), `helm/deployground/` (bot chart), `charts/golden-path/` (example golden-path chart), `deploy/slack-app-manifest.yaml`, `examples/config.yaml`, `docs/quickstart.md`, `CONTRIBUTING.md`
- Test: `tests/test_bootstrap_cli.py`

**Interfaces:**
- Consumes: `DeployTarget.bootstrap_manifests` (Tasks 8/9).
- Produces: `deployground bootstrap --config <path>` printing the ApplicationSet + `argocd-notifications-cm` snippet for the operator to review/apply; the bot Helm chart (Deployment, Service for the status webhook, ConfigMap from values, secret refs — **no database component**); the **example golden-path chart** the ApplicationSet references (consumes a component file; supplies the §8 resource defaults); an **example `config.yaml`**; a Slack app manifest (Socket Mode, App Home, shortcut, interactivity, scopes incl. `usergroups:read`); a **CONTRIBUTING.md**; and a **quickstart doc** (`docs/quickstart.md`) whose **Slack setup** section walks the one-time workspace bootstrap step-by-step: (1) create the app via *Create New App → From an app manifest* using `deploy/slack-app-manifest.yaml`; (2) generate an app-level token (`xapp-…`, scope `connections:write`); (3) *Install to Workspace* to obtain the bot token (`xoxb-…`); (4) place both tokens into the bot's config/secret (name the exact Helm values keys / env vars the chart reads) — and which also covers the optional per-Argo status connectivity. Note explicitly that steps 1–3 are manual because Slack requires human install consent, and that Socket Mode means there is **no** public URL / signing secret / event endpoint to configure.
- **Deferred (stated so executors don't assume coverage):** packaging capabilities / deploy targets / status sources as third-party plugins via Python entry points — the factories (`make_deploy_target`, `make_status_source`) are the seam, but entry-point discovery is a post-step-1 extension.

- [ ] **Step 1: Write failing test** `test_bootstrap_emits_manifests_for_config` (CLI output contains an `ApplicationSet` kind and the notifications ConfigMap for a sample config).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** CLI; add bot chart, golden-path chart, example config, Slack manifest, CONTRIBUTING.md, quickstart (charts/manifest validated by `helm lint` / schema in CI, not unit-tested).
- [ ] **Step 4: Run** → PASS; `helm lint helm/deployground charts/golden-path`.
- [ ] **Step 5: Commit** `feat: bootstrap CLI, Helm charts, Slack manifest, examples, docs`.

---

### Task 21: End-to-end wiring test
**Files:**
- Test: `tests/e2e/test_deploy_flow.py`

**Interfaces:**
- Consumes: `build_app` with fake Slack + fake Git (in-memory repo) + a scripted status source. No database.

- [ ] **Step 1: Write failing tests:** `test_single_service_deploy_to_healthy` (submit `api` → one PR with the `deployground:` block → scripted Healthy event → thread shows Running + completion ping); `test_stack_with_overrides` (`pool-party`, override two → correct resolved images in the PR, one Failed event → stack Failed, no success); `test_destroy_updates_thread_destroying_then_destroyed` (confirm destroy → delete PR + the same thread shows `Destroying → Destroyed`); `test_expiry_reaps_all_components` (short ttl + fake clock → delete PR for all components); `test_index_rebuilds_from_git_after_restart` (drop the in-memory index, call `rebuild()`, assert My stacks and the reaper's expiring set reappear purely from the committed files).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3:** Fix wiring gaps surfaced by the e2e tests.
- [ ] **Step 4: Run** full suite → PASS; `ruff check`.
- [ ] **Step 5: Commit** `test(e2e): deploy, override, and expiry flows`.
