"""``deployground`` command line: ``bootstrap`` (print manifests) and ``run`` (start the bot)."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from deployground.config.loader import load_config
from deployground.deploytarget import make_deploy_target


def _bootstrap(config_path: Path) -> int:
    try:
        config = load_config(config_path, os.environ)
    except Exception as exc:
        print(f"Invalid configuration ({config_path}): {exc}", file=sys.stderr)
        return 2
    target = make_deploy_target(
        config.deployTarget,
        config.git.repos[config.deployTarget.repo],
        {name: env.cluster for name, env in config.environments.items()},
    )
    for name, text in target.bootstrap_manifests(config).items():
        print(f"# --- {name} ---")
        print(text.rstrip("\n"))
        print("---")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deployground", description="Deployground")
    sub = parser.add_subparsers(dest="command", required=True)
    boot = sub.add_parser(
        "bootstrap", help="print the Argo CD manifests to review and apply (does not apply them)"
    )
    boot.add_argument("--config", required=True, type=Path, help="path to the YAML config file")
    run = sub.add_parser("run", help="run the Slack bot (same as python -m deployground)")
    run.add_argument("--config", required=True, type=Path, help="path to the YAML config file")
    args = parser.parse_args(argv)

    if args.command == "bootstrap":
        return _bootstrap(args.config)
    from deployground.__main__ import main as run_bot

    return run_bot(["--config", str(args.config)])


if __name__ == "__main__":
    sys.exit(main())
