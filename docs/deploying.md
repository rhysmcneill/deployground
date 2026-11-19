# Deploying from Slack

Run the `open_deploy_form` global shortcut (set up in [Slack setup](slack/SLACK-SETUP.md)) to
open the deploy modal.

The form has two naming fields that are easy to confuse:

- **Catalog** (dropdown): *what to deploy*. It picks one entry from the `catalog:` block of the
  config, and the entry's components are listed so you can override individual fields
  (version, or whatever the operator declared `overridable` for a custom-chart component — see
  [Configuration](configuration.md#custom-charts-per-component)).
- **Stack name** (free text): *what to call this deploy*. It is the name of your live deployment
  — lowercase letters, numbers and hyphens — and it becomes the namespace and the default URL
  (`{stack}` in an `ingressHost`). It does not have to match the catalog entry, so the same
  catalog entry can be deployed several times under different stack names.

```
┌─ New deployment ────────────────┐
│ Catalog:      [ my-catalog  ▾ ] │
│ Stack name:   [ my-stack      ] │
│ Environment:  [ dev         ▾ ] │
│ Expires in:   [ 24h           ] │
│ Override versions? (optional)   │
│   ☑ web   → [ v4.6.0        ]   │
│   ☐ api                         │
│   ☐ worker                      │
│              [ Submit ]         │
└──────────────────────────────────┘
```

Submitting opens one PR with every component's manifest, stamped with an owner, an expiry, and
the resolved values. The same Slack thread then tracks progress live:

```
Deploying...
  web     ⏳
  api     ⏳
  worker  ⏳
```

...filling in as each component goes `Deploying → Running`, then posting
`✅ Deployment complete — @you`. If a component fails, the thread tells you exactly which one.

## Extend / Destroy

Every deploy has a TTL. You get a warning before it expires, with one-click **Extend** /
**Destroy now** buttons right in the same thread. If you do nothing, the reaper tears it down —
one PR, every resource it created, gone.

<!-- A short GIF walking through deploy → progress → extend/destroy would go well here. -->
