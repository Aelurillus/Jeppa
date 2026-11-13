"""CLI diagnostics do not import or connect the SDK."""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import sys

from .config import ConfigError, load_config
from .runtime import Runtime, builtins


async def launch(config) -> None:
    runtime = Runtime()
    try:
        await runtime.install(builtins, name="builtin")
        for entry in config.plugins:
            await runtime.load(entry)
        from .oopz import run
        await run(config, runtime)
    finally:
        await runtime.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jeppa", description="Unofficial Oopz text bot. Only connect accounts explicitly authorized for this task.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, description in (("run", "Load plugins and connect Oopz"), ("check-config", "Validate local config without login or SDK connection")):
        command = commands.add_parser(name, help=description)
        command.add_argument("--config", type=Path, help="Local TOML file; omitted means environment only")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    if args.command == "check-config":
        print("Configuration valid (local syntax/required fields only); no login, token validity, plugin execution or network checks performed.")
        return 0
    # The upstream SDK can include payloads in exception logging. Keep only our
    # fixed status messages, never raw SDK events, exception text or tracebacks.
    handler = logging.StreamHandler()
    handler.addFilter(lambda record: record.name == "jeppa" or record.name.startswith("jeppa."))
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    try:
        asyncio.run(launch(config))
    except KeyboardInterrupt:
        print("Stopped; plugin and SDK cleanup completed.")
        return 130
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        print(f"Runtime failed ({type(error).__name__}); exception details withheld to protect credentials. Check plugin setup/cleanup, SDK compatibility, account authorization and local credentials. No online readiness was inferred.", file=sys.stderr)
        return 1
    return 0
