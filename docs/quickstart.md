# Quickstart

Deployground is a bot that deploys through GitOps: it writes one config file per component i.e. service
to your GitOps repo, and an Argo CD ApplicationSet turns each file into a running workload.

You need: a Kubernetes cluster with Argo CD (and optionally Argo CD Notifications), a GitOps
repo, a GitHub token per repo, and a Slack workspace where you can install apps.

Follow these in order for a first install:

1. [Slack setup](slack/SLACK-SETUP.md) — create the Slack app, get your tokens.
2. [Configuration](configuration.md) — write your `config.yaml`.
3. [Installing the bot](install.md) — the Secret, the Helm install.
4. [Bootstrapping Argo CD](bootstrap-argocd.md) — the ApplicationSet, notifications, status
   connectivity.
5. [Deploying from Slack](deploying.md) — using the deploy form day to day.

See also: [Architecture](architecture.md) for how the pieces fit together and where the seams
are for extending it.
