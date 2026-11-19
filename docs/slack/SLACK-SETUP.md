# Slack setup

Deployground talks to Slack over **Socket Mode** — there is no public URL, signing secret, or
event endpoint to configure. Everything below is manual and one-time, because Slack requires a
human to consent to installing an app into a workspace.

---

## 1. Create the app from the manifest

1. Go to <https://api.slack.com/apps> → **Create New App** → **From an app manifest**.
2. Pick the workspace you want to install into.
3. Paste in the contents of [`deploy/slack-app-manifest.yaml`](../../deploy/slack-app-manifest.yaml).

The manifest enables:

| Feature | Why |
|---|---|
| Socket Mode | No public URL/signing secret needed |
| App Home tab | Shows a user their live stacks |
| Interactivity | Modal forms, buttons (Extend / Destroy now) |
| Global shortcut `open_deploy_form` | The entry point to the deploy modal |

And requests these bot scopes: `chat:write`, `commands`, `usergroups:read`, `users:read`,
`users:read.email`, `im:write`, `channels:read`, `groups:read`.

---

## 2. Generate the app-level token

1. **Basic Information** → **App-Level Tokens** → **Generate Token and Scopes**.
2. Name it anything, add the `connections:write` scope.
3. Copy the token — this is the **app token** (`xapp-...`). Socket Mode uses this, not the bot
   token, to open the WebSocket connection.

---

## 3. Install the app and get the bot token

1. **Install App** → **Install to Workspace** → approve the permissions.
2. Copy the **bot token** (`xoxb-...`) from the Install App page.

---

## 4. Store both tokens

Both tokens go into the Kubernetes Secret the Helm chart reads (see
[Installing the bot](../install.md)), as `SLACK_APP_TOKEN` and `SLACK_BOT_TOKEN`:

```bash
kubectl -n deployground create secret generic deployground-secrets \
  --from-literal=SLACK_APP_TOKEN=xapp-... \
  --from-literal=SLACK_BOT_TOKEN=xoxb-...
  # ...plus the other keys install.md lists
```

---

## Verify

Once the bot is installed and running ([Installing the bot](../install.md)), confirm Slack-side
connectivity:

1. Open the app's **Home** tab in Slack — it should load without an error.
2. Run the `open_deploy_form` global shortcut (lightning bolt icon in the message composer, or
   the shortcuts menu) — the deploy modal should open.

If the Home tab or shortcut doesn't respond, check the bot's logs for a Socket Mode connection
error first — that almost always means `SLACK_APP_TOKEN` or `SLACK_BOT_TOKEN` is wrong or
missing from the Secret.

**Next:** [Configuration](../configuration.md) — write your `config.yaml`.
