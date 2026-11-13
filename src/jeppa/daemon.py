"""Single-instance ownership and authenticated, loopback-only process control.

The advisory lock, not a recorded PID, defines process ownership. Control tokens
are local secrets; neither requests nor instance metadata belong in diagnostics.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import time
from typing import Awaitable, Callable


class ControlError(RuntimeError):
    """A local management operation failed; messages contain no remote data."""


def control_paths(database: Path) -> tuple[Path, Path]:
    database = database.resolve()
    return database.with_name(database.name + ".lock"), database.with_name(database.name + ".control.json")


class InstanceLock:
    def __init__(self, database: Path):
        self.path, _ = control_paths(database)
        self.file = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0, os.SEEK_END)
                if not handle.tell():
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise ControlError("Instance is already running or its lock is unavailable.") from None
        self.file = handle

    def release(self) -> None:
        if self.file is None:
            return
        handle, self.file = self.file, None
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def is_running(database: Path) -> bool:
    lock = InstanceLock(database)
    try:
        lock.acquire()
    except ControlError:
        return True
    lock.release()
    return False


def read_control(database: Path) -> dict:
    _, path = control_paths(database)
    try:
        with path.open("rb") as handle:
            data = json.loads(handle.read(4097))
        if (not isinstance(data, dict) or set(data) != {"pid", "port", "token"}
                or type(data["pid"]) is not int or data["pid"] <= 0
                or type(data["port"]) is not int or not 1 <= data["port"] <= 65535
                or not isinstance(data["token"], str) or re.fullmatch(r"[0-9a-f]{64}", data["token"]) is None):
            raise ValueError
        return data
    except (OSError, ValueError, TypeError):
        raise ControlError("Local control is unavailable; check the instance and file permissions.") from None


def request(database: Path, operation: str, *, token: str | None = None, timeout: float = 2.0) -> dict:
    if operation not in {"status", "stop"}:
        raise ValueError("Unknown control operation")
    data = read_control(database)
    if token is not None and not hmac.compare_digest(token, data["token"]):
        raise ControlError("Instance changed; no control request was sent.")
    try:
        with socket.create_connection(("127.0.0.1", data["port"]), timeout=timeout) as connection:
            connection.settimeout(timeout)
            connection.sendall(json.dumps({"token": data["token"], "operation": operation}).encode() + b"\n")
            with connection.makefile("rb") as response:
                result = json.loads(response.readline(4097))
        if (not isinstance(result, dict) or result.get("ok") is not True
                or result.get("state") not in {"starting", "runtime-open", "stopping"}):
            raise ValueError
        # Return only the protocol's fixed, non-sensitive fields.
        return {"ok": True, "state": result["state"]}
    except (OSError, ValueError, TypeError):
        raise ControlError("Authenticated local control failed; no process was terminated.") from None


def stop(database: Path, *, timeout: float = 15.0, token: str | None = None) -> None:
    deadline = time.monotonic() + timeout
    if not is_running(database):
        raise ControlError("No instance is running.")
    data = read_control(database)
    expected = token if token is not None else data["token"]
    process = None
    kernel = None
    if os.name == "nt":
        # A Windows process handle retains identity even if the PID is reused.
        # SYNCHRONIZE grants waiting only, never termination permission.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        process = kernel.OpenProcess(0x00100000, False, data["pid"])
        if not process:
            raise ControlError("Cannot verify process exit; check local process permissions.")
    try:
        request(database, "stop", token=expected, timeout=min(2.0, max(0.1, timeout)))
        while time.monotonic() < deadline:
            if not is_running(database):
                if kernel is None:
                    # On Unix release is the final action after all resources
                    # close; the caller may reap its child separately.
                    return
                result = kernel.WaitForSingleObject(process, 0)
                if result == 0:
                    return
                if result != 258:
                    raise ControlError("Cannot verify graceful process exit; inspect local process state.")
            try:
                current = read_control(database)
            except ControlError:
                current = None
            if current is not None and not hmac.compare_digest(current["token"], expected):
                raise ControlError("Instance changed while stopping; the replacement was not stopped.")
            time.sleep(0.05)
        raise ControlError("Graceful stop timed out; inspect sanitized logs. No process was force-killed.")
    finally:
        if process is not None:
            kernel.CloseHandle(process)


class ControlServer:
    def __init__(self, database: Path, *, token: str | None = None):
        self.database = database
        _, self.path = control_paths(database)
        self.token = token or secrets.token_hex(32)
        self.stop_event = asyncio.Event()
        self.state = "starting"
        self.server = None
        self.clients: set[asyncio.Task] = set()

    async def open(self) -> None:
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0, limit=4096)
        port = self.server.sockets[0].getsockname()[1]
        temporary = self.path.with_name(self.path.name + "." + secrets.token_hex(8))
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "port": port, "token": self.token}, handle)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self.clients.add(task)
        try:
            data = json.loads(await asyncio.wait_for(reader.readline(), 2.0))
            if (not isinstance(data, dict) or not isinstance(data.get("token"), str)
                    or re.fullmatch(r"[0-9a-f]{64}", data["token"]) is None
                    or not hmac.compare_digest(data["token"], self.token)
                    or data.get("operation") not in {"status", "stop"}):
                return
            if data["operation"] == "stop":
                self.state = "stopping"
                self.stop_event.set()
            writer.write(json.dumps({"ok": True, "state": self.state}).encode() + b"\n")
            await writer.drain()
        except (OSError, ValueError, TypeError, RecursionError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
            self.clients.discard(task)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for task in tuple(self.clients):
            task.cancel()
        if self.clients:
            await asyncio.gather(*tuple(self.clients), return_exceptions=True)
        self.path.unlink(missing_ok=True)


async def supervise(database: Path, runner: Callable[[ControlServer], Awaitable[None]], *, token: str | None = None) -> None:
    """Own the lock until the runner's cancellation and all cleanup finish."""
    lock = InstanceLock(database)
    lock.acquire()
    control = ControlServer(database, token=token)
    running = stopping = None
    loop = asyncio.get_running_loop()
    signals = []
    try:
        await control.open()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, control.stop_event.set)
                signals.append(signum)
            except (NotImplementedError, RuntimeError):
                pass
        running = asyncio.create_task(runner(control))
        stopping = asyncio.create_task(control.stop_event.wait())
        done, _ = await asyncio.wait({running, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if running in done:
            await running
        else:
            control.state = "stopping"
            running.cancel()
            with suppress(asyncio.CancelledError):
                await running
    finally:
        for task in (running, stopping):
            if task is not None and not task.done():
                task.cancel()
        if running is not None or stopping is not None:
            await asyncio.gather(*(task for task in (running, stopping) if task is not None), return_exceptions=True)
        try:
            await control.close()
        finally:
            for signum in signals:
                loop.remove_signal_handler(signum)
            lock.release()
