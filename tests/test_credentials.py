from contextlib import redirect_stderr, redirect_stdout
import base64
import ctypes
import getpass
import io
import json
import os
from pathlib import Path
import shlex
import stat
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from jeppa import credentials as importer


URL = "https://gateway.oopz.cn/synthetic-read-only?area=unselected-area"
DEVICE = "synthetic-device"
PERSON = "synthetic-person"
TOKEN = "synthetic-sensitive-token"


def request(*, url=URL, device=DEVICE, person=PERSON, token=TOKEN):
    return {
        "url": url,
        "method": "GET",
        "headers": [
            {"name": "Oopz-Device-Id", "value": device},
            {"name": "oopz-person", "value": person},
            {"name": "OOPZ-SIGNATURE", "value": token},
            {"name": "Oopz-Sign", "value": "discard-synthetic-sign"},
            {"name": "Oopz-Time", "value": "discard-synthetic-time"},
            {"name": "Cookie", "value": "discard-synthetic-cookie"},
        ],
    }


def curl_text(**kwargs):
    data = request(**kwargs)
    words = ["curl", shlex.quote(data["url"])]
    for header in data["headers"]:
        words.extend(["-H", shlex.quote(f'{header["name"]}: {header["value"]}')])
    return " \\\n  ".join(words)


def har(*requests):
    return json.dumps({"log": {"entries": [{"request": item} for item in requests]}})


class CredentialConsumerTests(unittest.TestCase):
    def test_curl_json_and_har_extract_only_same_triple(self):
        direct = request()
        mapping = request()
        mapping["headers"] = {header["name"]: header["value"] for header in mapping["headers"]}
        for text in (curl_text(), json.dumps(direct), json.dumps(mapping), har(direct, direct)):
            with self.subTest(format=text[:4]):
                result = importer.parse_capture(text)
                self.assertEqual((result.device_id, result.person_uid, result.jwt_token),
                                 (DEVICE, PERSON, TOKEN))
                self.assertNotIn(TOKEN, repr(result))

    def test_cli_writes_real_toml_with_only_explicit_areas_and_default(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "capture.local.txt"
            output = Path(directory) / "config.local.toml"
            source.write_text(curl_text(token='synthetic-quote-"-slash-\\-中文'), encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = importer.main(["--input", str(source), "--output", str(output),
                                        "--area", "explicit-area", "--area", "another-area",
                                        "--area", "explicit-area"])
            self.assertEqual(result, 0)
            parsed = tomllib.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(parsed["oopz"], {
                "method": "credentials", "device_id": DEVICE, "person_uid": PERSON,
                "jwt_token": 'synthetic-quote-"-slash-\\-中文',
            })
            self.assertEqual(parsed["bot"], {
                "areas": ["explicit-area", "another-area"],
                "plugins": ["jeppa.plugins.default:setup"],
            })
            self.assertEqual(set(parsed), {"oopz", "bot"})
            self.assertNotIn("unselected-area", output.read_text(encoding="utf-8"))
            combined = stdout.getvalue() + stderr.getvalue()
            for secret in (DEVICE, PERSON, "synthetic-quote", URL, "discard-synthetic"):
                self.assertNotIn(secret, combined)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_no_area_never_infers_url_area(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "config.local.toml"
            importer.write_config(output, importer.parse_capture(curl_text()), [])
            parsed = tomllib.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(parsed["bot"]["areas"], [])

    def test_refuses_existing_file_and_symlink_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "config.local.toml"
            output.write_text("existing synthetic configuration", encoding="utf-8")
            values = importer.parse_capture(curl_text())
            with self.assertRaises(importer.CredentialError):
                importer.write_config(output, values, [])
            self.assertEqual(output.read_text(encoding="utf-8"), "existing synthetic configuration")
            link = Path(directory) / "linked.local.toml"
            try:
                link.symlink_to(output)
            except (OSError, NotImplementedError):
                return
            with self.assertRaises(importer.CredentialError):
                importer.write_config(link, values, [])
            self.assertTrue(link.is_symlink())
            self.assertEqual(output.read_text(encoding="utf-8"), "existing synthetic configuration")

    def test_windows_acl_failure_happens_before_secret_write_and_removes_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "config.local.toml"
            values = importer.parse_capture(curl_text())

            def deny(path):
                self.assertEqual(path.read_bytes(), b"")
                raise OSError("synthetic ACL failure")

            # 创建路径发生在补丁之前，避免切换 os.name 影响 pathlib 的平台选择。
            with patch.object(importer.os, "name", "nt"), patch.object(importer, "_restrict_windows_acl", side_effect=deny):
                with self.assertRaises(importer.CredentialError) as raised:
                    importer.write_config(output, values, [])
            self.assertNotIn("synthetic ACL", str(raised.exception))
            self.assertFalse(output.exists())

    def test_windows_acl_is_applied_to_empty_file_before_successful_write(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "config.local.toml"
            values = importer.parse_capture(curl_text())

            def restrict(path):
                self.assertEqual(path.read_bytes(), b"")

            with patch.object(importer.os, "name", "nt"), patch.object(importer, "_restrict_windows_acl", side_effect=restrict) as acl:
                importer.write_config(output, values, [])
            acl.assert_called_once_with(output)
            self.assertEqual(tomllib.loads(output.read_text(encoding="utf-8"))["oopz"]["jwt_token"], TOKEN)

    def test_host_header_cannot_override_approved_url(self):
        record = request()
        record["headers"].append({"name": "Host", "value": "foreign.invalid"})
        with self.assertRaises(importer.CredentialError):
            importer.parse_capture(json.dumps(record))
        record["headers"][-1]["value"] = "gateway.oopz.cn:443"
        self.assertEqual(importer.parse_capture(json.dumps(record)).person_uid, PERSON)
        record["headers"].append({"name": ":authority", "value": "gateway.oopz.cn"})
        with self.assertRaises(importer.CredentialError):
            importer.parse_capture(json.dumps(record))

    @unittest.skipUnless(os.name == "nt", "仅 Windows 验证真实 DACL")
    def test_windows_saved_file_has_protected_single_current_user_acl(self):
        from ctypes import wintypes

        class ACL(ctypes.Structure):
            _fields_ = [("revision", wintypes.BYTE), ("reserved", wintypes.BYTE),
                        ("size", wintypes.WORD), ("count", wintypes.WORD), ("reserved2", wintypes.WORD)]

        class AllowedACE(ctypes.Structure):
            _fields_ = [("type", wintypes.BYTE), ("flags", wintypes.BYTE),
                        ("size", wintypes.WORD), ("mask", wintypes.DWORD), ("sid", wintypes.DWORD)]

        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        advapi.GetFileSecurityW.restype = wintypes.BOOL
        advapi.GetSecurityDescriptorControl.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
        advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
        advapi.GetSecurityDescriptorDacl.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)]
        advapi.GetSecurityDescriptorDacl.restype = wintypes.BOOL
        advapi.GetAce.argtypes = [wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID)]
        advapi.GetAce.restype = wintypes.BOOL
        advapi.EqualSid.argtypes = [wintypes.LPVOID, wintypes.LPVOID]
        advapi.EqualSid.restype = wintypes.BOOL
        advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi.OpenProcessToken.restype = wintypes.BOOL
        advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        advapi.GetTokenInformation.restype = wintypes.BOOL
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        token = wintypes.HANDLE()
        try:
            self.assertTrue(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)))
            size = wintypes.DWORD()
            advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            user = ctypes.create_string_buffer(size.value)
            self.assertTrue(advapi.GetTokenInformation(token, 1, user, size, ctypes.byref(size)))
            sid = ctypes.cast(user, ctypes.POINTER(wintypes.LPVOID))[0]
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "config.local.toml"
                importer.write_config(output, importer.parse_capture(curl_text()), [])
                needed = wintypes.DWORD()
                advapi.GetFileSecurityW(str(output), 4, None, 0, ctypes.byref(needed))
                descriptor = ctypes.create_string_buffer(needed.value)
                self.assertTrue(advapi.GetFileSecurityW(str(output), 4, descriptor, needed, ctypes.byref(needed)))
                control, revision = wintypes.WORD(), wintypes.DWORD()
                self.assertTrue(advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)))
                self.assertTrue(control.value & 0x1000, "DACL must be protected from inheritance")
                present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), wintypes.LPVOID()
                self.assertTrue(advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)))
                self.assertTrue(present.value and dacl.value, "A null DACL would grant public access")
                self.assertEqual(ctypes.cast(dacl, ctypes.POINTER(ACL)).contents.count, 1)
                ace = wintypes.LPVOID()
                self.assertTrue(advapi.GetAce(dacl, 0, ctypes.byref(ace)))
                allowed = ctypes.cast(ace, ctypes.POINTER(AllowedACE)).contents
                self.assertEqual(allowed.type, 0, "Only an access-allowed ACE is expected")
                self.assertEqual(allowed.flags, 0, "The ACE must be explicit, not inherited")
                self.assertEqual(allowed.mask, 0x001F01FF, "Current user must retain full file access")
                self.assertTrue(advapi.EqualSid(ace.value + AllowedACE.sid.offset, sid),
                                "The only authorized SID must be the process user")
        finally:
            if token:
                kernel.CloseHandle(token)

    def test_foreign_and_ambiguous_capture_is_rejected_as_a_whole(self):
        invalid = [
            json.dumps(request(url="https://gateway.oopz.cn.evil.invalid/read")),
            json.dumps(request(url="http://gateway.oopz.cn/read")),
            json.dumps(request(url="https://gateway.oopz.cn:444/read")),
            json.dumps(request(url="https://user@gateway.oopz.cn/read")),
            har(request(), request(url="https://foreign.invalid/read")),
            har(request(), request(person="different-synthetic-person")),
            har(request(), request(device="different-synthetic-device")),
            har(request(), request(token="different-synthetic-token")),
        ]
        for text in invalid:
            with self.subTest(kind=text[:20]):
                with self.assertRaises(importer.CredentialError) as raised:
                    importer.parse_capture(text)
                self.assertNotIn(TOKEN, str(raised.exception))
                self.assertNotIn(URL, str(raised.exception))

    def test_missing_duplicate_non_get_and_control_fields_rejected(self):
        missing = request()
        missing["headers"].pop(0)
        duplicate = request()
        duplicate["headers"].append({"name": "oopz-person", "value": PERSON})
        non_get = request()
        non_get["method"] = "POST"
        implicit_method = request()
        del implicit_method["method"]
        for record in (missing, duplicate, non_get, implicit_method,
                       request(token="synthetic\nsecret"), request(person="synthetic\x00person")):
            with self.assertRaises(importer.CredentialError):
                importer.parse_capture(json.dumps(record))
        duplicate_json = json.dumps(request()).replace('"method": "GET"', '"method": "GET", "method": "POST"')
        with self.assertRaises(importer.CredentialError):
            importer.parse_capture(duplicate_json)

    def test_curl_never_executes_substitutions_or_appended_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "must-not-exist"
            command = f"$(touch {marker.as_posix()})"
            result = importer.parse_capture(curl_text(token=command))
            self.assertEqual(result.jwt_token, command)
            self.assertFalse(marker.exists())
            for suffix in (f"; touch {marker.as_posix()}", " | sh", " && echo synthetic-secret"):
                with self.assertRaises(importer.CredentialError):
                    importer.parse_capture(curl_text() + suffix)
                self.assertFalse(marker.exists())
            with self.assertRaises(importer.CredentialError):
                importer.parse_capture(curl_text() + " --config synthetic-file")

    def test_oversize_and_expired_jwt_rejected_without_echo(self):
        payload = base64.urlsafe_b64encode(json.dumps({"exp": 1}).encode()).decode().rstrip("=")
        expired = f"synthetic.{payload}.synthetic-signature"
        for text in ("x" * (importer.MAX_CAPTURE_BYTES + 1), curl_text(token=expired)):
            with self.assertRaises(importer.CredentialError) as raised:
                importer.parse_capture(text)
            self.assertNotIn(expired, str(raised.exception))

    def test_manual_hidden_inputs_map_to_toml(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "manual.local.toml"
            with patch.object(importer.getpass, "getpass", side_effect=[URL, DEVICE, PERSON, TOKEN]) as prompt:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(importer.main(["--manual", "--output", str(output)]), 0)
            self.assertEqual(prompt.call_count, 4)
            self.assertEqual(tomllib.loads(output.read_text(encoding="utf-8"))["oopz"]["jwt_token"], TOKEN)

    def test_manual_no_echo_fallback_and_foreign_host_fail_closed(self):
        for values in (["https://foreign.invalid/read"], None):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "manual.local.toml"
                stdout, stderr = io.StringIO(), io.StringIO()
                effect = values if values is not None else getpass.GetPassWarning("synthetic-secret-warning")
                with patch.object(importer.getpass, "getpass", side_effect=effect):
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        self.assertEqual(importer.main(["--manual", "--output", str(output)]), 1)
                self.assertFalse(output.exists())
                self.assertNotIn("foreign.invalid", stderr.getvalue())
                self.assertNotIn("synthetic-secret-warning", stderr.getvalue())

    def test_stdin_is_explicit_and_failure_messages_never_echo_input(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "stdin.local.toml"
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(importer.sys, "stdin", io.StringIO(curl_text())):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(importer.main(["--stdin", "--output", str(output)]), 0)
            self.assertEqual(tomllib.loads(output.read_text(encoding="utf-8"))["oopz"]["person_uid"], PERSON)
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(importer.main(["--unknown-argument", TOKEN]), 1)
            for value in (DEVICE, PERSON, TOKEN, URL):
                self.assertNotIn(value, stdout.getvalue() + stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
