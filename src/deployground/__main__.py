"""Entrypoint: ``python -m deployground --config PATH``."""

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from deployground.app import StartupError, build_app
from deployground.config.loader import ConfigWatcher


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deployground", description="Deployground Slack bot")
    parser.add_argument("--config", required=True, type=Path, help="path to the YAML config file")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        watcher = ConfigWatcher(args.config, os.environ)
    except Exception as exc:
        print(f"Invalid configuration ({args.config}): {exc}", file=sys.stderr)
        return 2
    try:
        build_app(watcher, os.environ).run()
    except StartupError as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logging.getLogger(__name__).exception("fatal error")
        print(f"Fatal error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
