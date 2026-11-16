# Contributing

Thanks for helping out. Deployground is Apache-2.0 licensed; contributions are accepted under the same license.

## Dev setup

Requires Python 3.12+.

    python -m venv .venv
    .venv/bin/pip install -e ".[dev]"

## Tests and lint

    .venv/bin/pytest
    .venv/bin/ruff check

If you touch the charts, also run (needs [Helm](https://helm.sh)):

    helm lint helm/deployground
    helm lint charts/golden-path --set image=ghcr.io/my-org/app:v1.0.0

Write a failing test first for behaviour changes. Tests run without network: Slack, Git and Argo
CD are injected as fakes.

## Pull requests

1. Fork and branch from `main`.
2. Keep changes focused; add or update tests and docs (`docs/quickstart.md`, `examples/config.yaml`).
3. Make sure `pytest` and `ruff check` pass; CI runs both plus `helm lint`.
4. Open a PR describing what changed and why. Use placeholder names (`my-org`, `example.com`) in
   examples; never commit tokens or real organisation details.

## Design notes

The design spec lives in `docs/`. Notable constraints: no database (state is Git), secrets only
via environment variables, and a single bot replica.
