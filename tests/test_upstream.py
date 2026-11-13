"""公开监测的离线边界测试；不访问网络、不安装候选 SDK。"""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import Request

SPEC = importlib.util.spec_from_file_location("jeppa_upstream_check", Path(__file__).resolve().parents[1] / "scripts" / "check_upstream.py")
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


def encoded(value):
    return json.dumps(value).encode()


def fixtures():
    return {
        monitor.PYPI_URL: encoded({"info": {"version": "0.16.0"}, "urls": [
            {"filename": "oopz_sdk-0.16.0.whl", "digests": {"sha256": "a" * 64},
             "size": 123, "yanked": False, "url": "https://untrusted.invalid/NEVER-FETCH"}]}),
        monitor.SDK_URL: encoded({"sha": "b" * 40, "commit": {"message": "SECRET-REMOTE-TEXT"}}),
        monitor.WEB_URL: b'<base href="/web/release/"><script src="boot.js?tracking=SECRET"></script><script>_flutter.buildConfig={"mainJsPath":"main.dart.js"}</script>',
        "https://web.oopz.cn/web/release/boot.js": b"public boot bytes",
        "https://web.oopz.cn/web/release/main.dart.js": b"public app bytes",
    }


class UpstreamTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures()
        self.calls = []
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.baseline = Path(self.temp.name) / "baseline.json"
        self.output = Path(self.temp.name) / "report.json"

    def fetch(self, url, maximum):
        self.calls.append(url)
        if url not in self.data:
            raise monitor.ObservationError("SECRET-NETWORK-DETAIL")
        return self.data[url]

    def run_monitor(self, *options):
        with patch("sys.stdout", new_callable=io.StringIO):
            code = monitor.main(["--baseline", str(self.baseline), "--output", str(self.output), *options], fetch=self.fetch)
        return code, json.loads(self.output.read_text(encoding="utf-8"))

    def initialize(self):
        code, report = self.run_monitor("--update-baseline")
        self.assertEqual(code, 0)
        self.assertTrue(report["baseline_updated"])

    def test_explicit_update_and_unchanged(self):
        self.initialize()
        original = self.baseline.read_bytes()
        code, report = self.run_monitor()
        self.assertEqual((code, report["status"]), (0, "unchanged"))
        self.assertEqual(self.baseline.read_bytes(), original)
        self.assertFalse(report["baseline_updated"])
        self.assertNotIn("SECRET", json.dumps(report))
        self.assertNotIn("https://untrusted.invalid/NEVER-FETCH", self.calls)

    def test_all_three_changes_are_fixed_labels_and_never_autoacknowledged(self):
        self.initialize()
        original = self.baseline.read_bytes()
        data = json.loads(self.data[monitor.PYPI_URL])
        data["urls"][0]["yanked"] = True
        self.data[monitor.PYPI_URL] = encoded(data)
        self.data[monitor.SDK_URL] = encoded({"sha": "c" * 40})
        self.data["https://web.oopz.cn/web/release/boot.js"] = b"new bytes"
        code, report = self.run_monitor()
        self.assertEqual((code, report["status"]), (2, "changed"))
        self.assertEqual(len(report["changes"]), 3)
        self.assertEqual(original, self.baseline.read_bytes())

    def test_partial_failure_is_inconclusive_even_with_known_change(self):
        self.initialize()
        self.data.pop(monitor.PYPI_URL)
        self.data[monitor.SDK_URL] = encoded({"sha": "c" * 40})
        code, report = self.run_monitor()
        self.assertEqual((code, report["status"]), (1, "inconclusive"))
        self.assertTrue(report["changes"])
        self.assertEqual(len(report["failures"]), 1)
        self.assertNotIn("SECRET", self.output.read_text(encoding="utf-8"))

    def test_failed_update_preserves_baseline(self):
        self.initialize()
        original = self.baseline.read_bytes()
        self.data.pop(monitor.SDK_URL)
        code, report = self.run_monitor("--update-baseline")
        self.assertEqual(code, 1)
        self.assertFalse(report["baseline_updated"])
        self.assertEqual(self.baseline.read_bytes(), original)

    def test_missing_and_invalid_baseline_are_inconclusive(self):
        for payload in (None, b"SECRET INVALID", b'{"schema": 1, "sources": {}}'):
            if payload is not None:
                self.baseline.write_bytes(payload)
            code, report = self.run_monitor()
            self.assertEqual((code, report["status"]), (1, "inconclusive"))
            self.assertNotIn("SECRET", json.dumps(report))

    def test_report_cannot_overwrite_baseline(self):
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            monitor.main(["--baseline", str(self.baseline), "--output", str(self.baseline)], fetch=self.fetch)
        self.assertFalse(self.calls)

    def test_script_resources_honor_base_remove_query_and_hash_bytes(self):
        observed = monitor.observe_web(self.fetch)
        self.assertEqual(observed["scripts"][0], {
            "resource": "web.oopz.cn/web/release/boot.js",
            "sha256": hashlib.sha256(b"public boot bytes").hexdigest(),
        })
        self.assertTrue(any(url.endswith("/main.dart.js") for url in self.calls))
        self.assertFalse(any("?" in url for url in self.calls))

    def test_unapproved_scripts_and_base_fail_closed_without_fetch(self):
        for html in (
            b'<script src="https://evil.invalid/private.js"></script>',
            b'<script src="https://web.oopz.cn.evil.invalid/x.js"></script>',
            b'<base href="https://evil.invalid/"><script src="x.js"></script>',
            b'<script src="https://' + b'user:' + b'SECRET' + b'@web.oopz.cn/x.js"></script>',
            b'<script src="data:text/javascript,SECRET"></script>',
        ):
            with self.subTest(html=html):
                self.data[monitor.WEB_URL] = html
                self.calls.clear()
                with self.assertRaises(monitor.ObservationError):
                    monitor.observe_web(self.fetch)
                self.assertEqual(self.calls, [monitor.WEB_URL])

    def test_empty_or_excessive_script_sets_are_inconclusive(self):
        for html in (b"<html></html>", b"".join(f'<script src="/{i}.js"></script>'.encode() for i in range(monitor.MAX_SCRIPTS + 1))):
            self.data[monitor.WEB_URL] = html
            with self.assertRaises(monitor.ObservationError):
                monitor.observe_web(self.fetch)

    def test_metadata_rejects_untrusted_fields(self):
        for value in ("SECRET\n<script>", None, {}, "v" * 300):
            self.data[monitor.PYPI_URL] = encoded({"info": {"version": value}})
            with self.assertRaises(monitor.ObservationError):
                monitor.observe_pypi(self.fetch)
        for value in ("SECRET", 123, None):
            self.data[monitor.SDK_URL] = encoded({"sha": value})
            with self.assertRaises(monitor.ObservationError):
                monitor.observe_sdk(self.fetch)

    def test_url_policy_exact_https_hosts_paths_and_no_query(self):
        for url in (
            "http://web.oopz.cn/x.js", "https://web.oopz.cn:443/x.js",
            "https://web.oopz.cn.evil.invalid/x.js", "https://user@web.oopz.cn/x.js",
            "https://127.0.0.1/x.js", "https://web.oopz.cn/x.js?SECRET",
            "https://web.oopz.cn/x.js#SECRET", "https://web.oopz.cn/%2e%2e/x.js",
            "https://web.oopz.cn/../x.js", "https://web.oopz.cn/x\\y.js",
        ):
            with self.subTest(url=url), self.assertRaises(monitor.ObservationError):
                monitor.approved_url(url)
        self.assertEqual(monitor.approved_url(monitor.WEB_URL), monitor.WEB_URL)

    def test_redirects_restrict_host_count_and_deadline(self):
        redirect = monitor.RestrictedRedirects("web.oopz.cn", time.monotonic() + 30)
        req = Request(monitor.WEB_URL)
        req.timeout = 30
        for _ in range(monitor.MAX_REDIRECTS):
            self.assertIsNotNone(redirect.redirect_request(req, None, 302, "", {}, "https://web.oopz.cn/next.js"))
        with self.assertRaises(monitor.ObservationError):
            redirect.redirect_request(req, None, 302, "", {}, monitor.WEB_URL)
        for target in (monitor.PYPI_URL, "https://evil.invalid/", "http://web.oopz.cn/"):
            with self.assertRaises(monitor.ObservationError):
                monitor.RestrictedRedirects("web.oopz.cn", time.monotonic() + 30).redirect_request(req, None, 302, "", {}, target)
        with self.assertRaises(monitor.ObservationError):
            monitor.RestrictedRedirects("web.oopz.cn", time.monotonic() - 1).redirect_request(req, None, 302, "", {}, monitor.WEB_URL)

    def test_fetch_size_encoding_deadline_and_error_redaction(self):
        class Response:
            status = 200
            headers = {}
            def __init__(self, payload):
                self.payload = io.BytesIO(payload)
                self.fp = type("FP", (), {"raw": type("Raw", (), {"_sock": type("Sock", (), {"settimeout": lambda _, value: None})()})()})()
            def geturl(self):
                return monitor.WEB_URL
            def isclosed(self):
                return False
            def read1(self, count):
                return self.payload.read(count)
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False

        for payload, headers, maximum, total in (
            (b"12345", {}, 4, 0), (b"1", {"Content-Length": "999"}, 4, 0),
            (b"1", {"Content-Encoding": "gzip"}, 4, 0),
            (b"1", {"Content-Length": "3"}, 4, 0),
            (b"1", {}, 4, monitor.MAX_TOTAL_BYTES),
        ):
            response = Response(payload)
            response.headers = headers
            fetch = monitor.Fetcher()
            fetch.total = total
            with patch.object(monitor, "build_opener") as opener:
                opener.return_value.open.return_value = response
                with self.assertRaises(monitor.ObservationError):
                    fetch(monitor.WEB_URL, maximum)
        fetch = monitor.Fetcher()
        fetch.deadline = time.monotonic() - 1
        with self.assertRaises(monitor.ObservationError):
            fetch(monitor.WEB_URL, 4)
        with patch.object(monitor, "build_opener") as opener:
            opener.return_value.open.side_effect = OSError("SECRET")
            with self.assertRaises(monitor.ObservationError) as error:
                monitor.Fetcher()(monitor.WEB_URL, 4)
            self.assertNotIn("SECRET", str(error.exception))

    def test_successful_fetch_and_proxy_disabled(self):
        with patch.object(monitor, "ProxyHandler") as proxy, patch.object(monitor, "build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.headers = {"Content-Length": "3"}
            response.geturl.return_value = monitor.WEB_URL
            response.isclosed.return_value = False
            response.read1.side_effect = [b"123", b""]
            self.assertEqual(monitor.Fetcher()(monitor.WEB_URL, 4), b"123")
            proxy.assert_called_once_with({})

    def test_baseline_write_error_is_inconclusive(self):
        real_save = monitor.save_json
        def save(path, value):
            if path == self.baseline:
                raise OSError("SECRET")
            real_save(path, value)
        with patch.object(monitor, "save_json", side_effect=save):
            code, report = self.run_monitor("--update-baseline")
        self.assertEqual((code, report["status"]), (1, "inconclusive"))
        self.assertFalse(report["baseline_updated"])
        self.assertNotIn("SECRET", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
