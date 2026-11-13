"""Redacted diagnostics and independent process management, offline by default."""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from importlib.metadata import PackageNotFoundError, version
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

from .config import ConfigError, load_config
from . import daemon
from .runtime import Runtime, builtins
from .storage import Storage


START_TIMEOUT = 15.0
STOP_TIMEOUT = 15.0
LOG_LINES = 50
logger = logging.getLogger(__name__)
# Only fixed core diagnostics cross the logging boundary. Plugin and SDK log
# payloads, exception text, tracebacks and arbitrary extra fields never do.
_SAFE_MESSAGES = re.compile(
    r"(?:runtime-open; platform authentication not inferred|runtime-stopped|"
    r"runtime-failed exception=[A-Za-z_][A-Za-z0-9_]*|"
    r"transport-open; auth-sent; awaiting event=21 checkRes=true \(not ready\)|"
    r"subscription-sent for [0-9]+ configured area\(s\); auth/subscription confirmation unavailable|"
    r"no areas configured; no area subscription sent|"
    r"transport-closed; auth/subscription state reset; SDK may reconnect|"
    r"SDK error \([A-Za-z_][A-Za-z0-9_]*\); details withheld to protect credentials|"
    r"auth-confirmed \(event=21 checkRes=true\)|"
    r"command=[a-z][a-z0-9_-]* outcome=(?:denied|cooldown|cancelled|success|failed) "
    r"elapsed=[0-9]+\.[0-9]+(?: exception=[A-Za-z_][A-Za-z0-9_]*)?)"
)


def _safe_message(message: str) -> bool:
    return len(message) <= 1000 and _SAFE_MESSAGES.fullmatch(message) is not None


class _Sanitized(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name not in {"jeppa.cli", "jeppa.runtime", "jeppa.oopz"}:
            return False
        if Path(record.pathname).resolve() not in {
            Path(__file__).resolve(), Path(__file__).with_name("runtime.py").resolve(),
            Path(__file__).with_name("oopz.py").resolve(),
        }:
            return False
        try:
            message = record.getMessage()
        except Exception:
            return False
        if not _safe_message(message):
            return False
        record.msg, record.args = message, ()
        record.exc_info = record.exc_text = record.stack_info = None
        return True


@contextmanager
def _logging(config):
    config.log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers = []
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    try:
        handlers.append(RotatingFileHandler(config.log_file, maxBytes=config.log_max_bytes,
                                            backupCount=config.log_backups, encoding="utf-8"))
        handlers.append(logging.StreamHandler())
        for handler in handlers:
            handler.addFilter(_Sanitized())
            handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        root.handlers = handlers
        root.setLevel(logging.INFO)
        yield
    finally:
        root.handlers, root.level = old_handlers, old_level
        for handler in handlers:
            handler.close()


async def launch(config, control: daemon.ControlServer | None = None) -> None:
    storage = Storage(config.database)
    runtime = None
    connection = None
    try:
        runtime = Runtime(default_policy=config.default_policy, policies=config.policies, storage=storage)
        await runtime.install(builtins, name="builtin")
        for entry in config.plugins:
            await runtime.load(entry)
        from .oopz import run
        connection = asyncio.create_task(run(config, runtime))
        # Catch synchronous/early SDK startup failures before advertising the
        # *local runtime*. Network login/authentication is deliberately separate.
        await asyncio.sleep(0)
        if connection.done():
            await connection
            raise RuntimeError("Connection ended during startup")
        if control is not None:
            control.state = "runtime-open"
        logger.info("runtime-open; platform authentication not inferred")
        await connection
    finally:
        try:
            if connection is not None and not connection.done():
                connection.cancel()
                await asyncio.gather(connection, return_exceptions=True)
            if runtime is not None:
                await runtime.close()
        finally:
            storage.close()


def _start(config, path: Path | None) -> int:
    if daemon.is_running(config.database):
        raise daemon.ControlError("Instance already running; use status or stop first.")
    token = secrets.token_hex(32)
    env = dict(os.environ, JEPPA_CONTROL_TOKEN=token)
    command = [sys.executable, "-m", "jeppa", "run"]
    if path is not None:
        command += ["--config", str(path.resolve())]
    options = {"cwd": str(Path.cwd()), "env": env, "stdin": subprocess.DEVNULL,
               "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    child = subprocess.Popen(command, **options)
    deadline = time.monotonic() + START_TIMEOUT
    try:
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise daemon.ControlError("Child exited during startup; inspect sanitized logs and configuration.")
            try:
                status = daemon.request(config.database, "status", token=token, timeout=0.5)
            except daemon.ControlError:
                time.sleep(0.05)
                continue
            if status["state"] == "runtime-open":
                if child.poll() is not None:
                    raise daemon.ControlError("Child exited during startup; inspect sanitized logs.")
                print("Started independent process; local runtime open. Platform authentication/readiness is NOT inferred. Use status and logs.")
                return 0
            time.sleep(0.05)
        raise daemon.ControlError("Startup timed out; inspect sanitized logs. Platform readiness was not inferred.")
    except BaseException:
        if child.poll() is None:
            try:
                daemon.stop(config.database, timeout=STOP_TIMEOUT, token=token)
            except daemon.ControlError:
                # This is our own freshly spawned child handle, never a PID
                # read from a file. No unrelated/reused PID can be terminated.
                child.terminate()
            try:
                child.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                raise daemon.ControlError("Startup failed and child cleanup timed out; inspect local process and logs.") from None
        raise


def _logs(config) -> None:
    try:
        with config.log_file.open(encoding="utf-8", errors="replace") as handle:
            lines = deque(maxlen=LOG_LINES)
            for line in handle:
                level, separator, message = line.rstrip("\r\n").partition(" ")
                if separator and level in {"INFO", "WARNING", "ERROR", "CRITICAL", "DEBUG"} and _safe_message(message):
                    lines.append(f"{level} {message}")
    except FileNotFoundError:
        print("No sanitized log file yet; run or start Jeppa first.")
        return
    for line in lines:
        print(line)
    if not lines:
        print("No sanitized log entries.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jeppa", description="Unofficial Oopz text bot. Only connect explicitly authorized accounts.")
    try:
        installed_version = version("jeppa")
    except PackageNotFoundError:
        installed_version = "not installed"
    parser.add_argument("--version", action="version", version=f"jeppa {installed_version}")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, description in (
        ("run", "Connect Oopz in the foreground"), ("start", "Start an independent process"),
        ("stop", "Stop the local instance gracefully (no login)"),
        ("status", "Inspect local process status (no login)"),
        ("logs", "Show the latest 50 sanitized log lines (no login)"),
        ("check-config", "Validate config without login or SDK connection"),
    ):
        command = commands.add_parser(name, help=description)
        command.add_argument("--config", type=Path, help="Local TOML file; omitted means environment only")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config, require_auth=args.command not in {"stop", "status", "logs"})
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    if args.command == "check-config":
        print("Configuration valid (local syntax/required fields only); no login, token validity, plugin execution or network checks performed.")
        return 0
    try:
        if args.command == "start":
            return _start(config, args.config)
        if args.command == "stop":
            daemon.stop(config.database, timeout=STOP_TIMEOUT)
            print("Stopped; instance lock released after plugin, SDK and storage cleanup.")
            return 0
        if args.command == "status":
            if not daemon.is_running(config.database):
                print("Stopped; no instance owns the database lock.")
                return 0
            status = daemon.request(config.database, "status")
            print(f"Process {status['state']}; platform authentication/readiness is NOT inferred. Use /status in the authorized chat for connection state.")
            return 0
        if args.command == "logs":
            _logs(config)
            return 0
        token = os.environ.pop("JEPPA_CONTROL_TOKEN", None)
        if token is not None and re.fullmatch(r"[0-9a-f]{64}", token) is None:
            raise daemon.ControlError("Invalid local startup token; restart using jeppa start.")
        with _logging(config):
            try:
                # SDK stdout/stderr may contain raw login responses. Only our
                # sanitized handlers retain the original stderr stream.
                with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink), redirect_stderr(sink):
                    asyncio.run(daemon.supervise(config.database, lambda control: launch(config, control), token=token))
            except BaseException as error:
                if not isinstance(error, (KeyboardInterrupt, SystemExit, daemon.ControlError)):
                    logger.error("runtime-failed exception=%s", type(error).__name__)
                raise
            logger.info("runtime-stopped")
    except daemon.ControlError as error:
        print(f"Local management error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Stopped; plugin and SDK cleanup completed.")
        return 130
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        print(f"Operation failed ({type(error).__name__}); details withheld to protect credentials. Check configuration, file permissions, sanitized logs, plugin cleanup and SDK compatibility. No online readiness was inferred.", file=sys.stderr)
        return 1
    return 0
