"""Real subprocess coverage of local ownership and authenticated shutdown."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from jeppa import daemon


_RUNNER = """
import asyncio
from pathlib import Path
import sys
from jeppa.daemon import supervise
async def runner(control):
    control.state = 'runtime-open'
    try:
        await asyncio.Event().wait()
    finally:
        await asyncio.sleep(0.05)
        Path(sys.argv[2]).write_text('cleaned', encoding='utf-8')
asyncio.run(supervise(Path(sys.argv[1]), runner))
"""


def subprocess_environment():
    environment = dict(os.environ)
    source = str(Path(__file__).resolve().parents[1] / "src")
    environment["PYTHONPATH"] = source + os.pathsep + environment.get("PYTHONPATH", "")
    for key in tuple(environment):
        if key.startswith("OOPZ_") or key == "JEPPA_CONTROL_TOKEN":
            environment.pop(key)
    return environment


class DaemonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite3"
        self.cleaned = self.root / "cleaned"
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                try:
                    daemon.stop(self.database, timeout=3)
                except daemon.ControlError:
                    child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
        self.temporary.cleanup()

    def spawn(self):
        child = subprocess.Popen([sys.executable, "-c", _RUNNER, str(self.database), str(self.cleaned)],
                                 env=subprocess_environment(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(child)
        return child

    def wait_ready(self, child):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            self.assertIsNone(child.poll(), "Local control child exited before readiness")
            try:
                status = daemon.request(self.database, "status", timeout=0.2)
            except daemon.ControlError:
                time.sleep(0.02)
                continue
            if status["state"] == "runtime-open":
                return
        self.fail("Local control child never became ready")

    def cli(self, *arguments):
        return subprocess.run([sys.executable, "-m", "jeppa", *arguments], cwd=self.root,
                              env=subprocess_environment(), text=True, capture_output=True, timeout=8)

    def test_real_subprocess_status_singleton_and_graceful_stop_without_login(self):
        child = self.spawn()
        self.wait_ready(child)
        token = daemon.read_control(self.database)["token"]
        duplicate = self.spawn()
        self.assertNotEqual(duplicate.wait(timeout=5), 0)
        self.assertIsNone(child.poll())
        config = self.root / "offline.toml"
        config.write_text('[storage]\npath = "state.sqlite3"\n', encoding="utf-8")
        status = self.cli("status", "--config", str(config))
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("runtime-open", status.stdout)
        self.assertIn("NOT inferred", status.stdout)
        self.assertNotIn(token, status.stdout + status.stderr)
        stopped = self.cli("stop", "--config", str(config))
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertEqual(child.wait(timeout=5), 0)
        self.assertEqual(self.cleaned.read_text(encoding="utf-8"), "cleaned")
        self.assertFalse(daemon.is_running(self.database))
        self.assertFalse(daemon.control_paths(self.database)[1].exists())
        self.assertNotIn(token, stopped.stdout + stopped.stderr)
        self.assertIn("Stopped", self.cli("status", "--config", str(config)).stdout)

    def test_unauthenticated_and_replaced_token_cannot_stop_process(self):
        child = self.spawn()
        self.wait_ready(child)
        metadata = daemon.read_control(self.database)
        for payload in (
            {"token": "wrong", "operation": "stop"},
            {"token": "é" * 64, "operation": "stop"},
            {"token": metadata["token"], "operation": []},
            [],
        ):
            with socket.create_connection(("127.0.0.1", metadata["port"]), timeout=2) as connection:
                connection.sendall(json.dumps(payload).encode() + b"\n")
                self.assertEqual(connection.recv(4096), b"")
        with self.assertRaises(daemon.ControlError):
            daemon.stop(self.database, token="0" * 64, timeout=1)
        self.assertIsNone(child.poll())
        self.assertEqual(daemon.request(self.database, "status")["state"], "runtime-open")
        daemon.stop(self.database, timeout=5)
        self.assertEqual(child.wait(timeout=5), 0)

    def test_stale_pid_file_never_signals_an_unrelated_process(self):
        _, control = daemon.control_paths(self.database)
        control.write_text(json.dumps({"pid": os.getpid(), "port": 1, "token": "0" * 64}), encoding="utf-8")
        with self.assertRaises(daemon.ControlError):
            daemon.stop(self.database, timeout=0.2)
        self.assertFalse(daemon.is_running(self.database))

    def test_failed_runner_releases_lock_and_removes_control_metadata(self):
        code = """
import asyncio
from pathlib import Path
import sys
from jeppa.daemon import supervise
async def fail(control):
    raise RuntimeError('private-credential-must-not-leak')
try:
    asyncio.run(supervise(Path(sys.argv[1]), fail))
except RuntimeError:
    pass
else:
    raise SystemExit(2)
"""
        result = subprocess.run([sys.executable, "-c", code, str(self.database)],
                                env=subprocess_environment(), capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(daemon.is_running(self.database))
        self.assertFalse(daemon.control_paths(self.database)[1].exists())
        self.assertNotIn("private-credential", result.stdout + result.stderr)
