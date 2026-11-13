"""仅在本机解析用户明确提供的请求；不执行命令，不访问网络。"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
import getpass
import json
import math
import os
from pathlib import Path
import shlex
import sys
import time
from urllib.parse import urlsplit
import warnings


MAX_CAPTURE_BYTES = 2 * 1024 * 1024
APPROVED_HOSTS = frozenset({
    "gateway.oopz.cn", "gateway1.oopz.cn", "gateway2.oopz.cn",
    "gateway-web1.oopz.cn",
})
_HEADER_FIELDS = {
    "oopz-device-id": "device_id",
    "oopz-person": "person_uid",
    "oopz-signature": "jwt_token",
}


class CredentialError(ValueError):
    """只包含固定、脱敏的中文错误。"""


@dataclass(frozen=True, slots=True)
class Credentials:
    device_id: str = field(repr=False)
    person_uid: str = field(repr=False)
    jwt_token: str = field(repr=False)


def _field(value: object, *, maximum: int = 8192) -> str:
    if (not isinstance(value, str) or not value or len(value) > maximum
            or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise CredentialError("凭据或域 ID 为空、过长，或包含不允许的空白和控制字符。")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise CredentialError("凭据或域 ID 的字符编码无效。") from None
    return value


def _validate_url(value: object) -> None:
    if not isinstance(value, str) or any(ord(char) <= 32 for char in value):
        raise CredentialError("请求地址无效；必须使用批准的 Oopz HTTPS 网关。")
    try:
        url = urlsplit(value)
        valid = (url.scheme == "https" and url.hostname in APPROVED_HOSTS
                 and url.port in (None, 443) and url.username is None
                 and url.password is None and not url.fragment
                 and "\\" not in value)
    except ValueError:
        valid = False
    if not valid:
        raise CredentialError("请求地址不在批准的 Oopz HTTPS 网关范围内。")


def _validate_credentials(values: dict[str, str]) -> Credentials:
    credentials = Credentials(
        _field(values["device_id"], maximum=256),
        _field(values["person_uid"], maximum=256),
        _field(values["jwt_token"]),
    )
    # 只检查本地可读的 exp；不把解码当作签名、账号或服务端有效性验证。
    parts = credentials.jwt_token.split(".")
    if len(parts) == 3:
        try:
            payload = json.loads(base64.urlsafe_b64decode(
                parts[1] + "=" * (-len(parts[1]) % 4)
            ))
        except (ValueError, UnicodeError, RecursionError):
            payload = None
        if isinstance(payload, dict):
            expires = payload.get("exp")
            if (isinstance(expires, (int, float)) and not isinstance(expires, bool)
                    and (isinstance(expires, int) or math.isfinite(expires))
                    and expires <= time.time()):
                raise CredentialError("令牌的本地到期时间已过；请重新取得已授权请求。")
    return credentials


def _header_pairs(headers: object) -> list[tuple[str, str]]:
    if isinstance(headers, dict):
        pairs = list(headers.items())
    elif isinstance(headers, list):
        pairs = []
        for header in headers:
            if not isinstance(header, dict) or set(header) != {"name", "value"}:
                raise CredentialError("请求头 JSON 格式无效。")
            pairs.append((header["name"], header["value"]))
    else:
        raise CredentialError("请求头 JSON 必须为对象或名称和值的列表。")
    if any(not isinstance(name, str) or not isinstance(value, str)
           for name, value in pairs):
        raise CredentialError("请求头名称和值必须为字符串。")
    return pairs


def _request_credentials(request: object) -> Credentials | None:
    if not isinstance(request, dict):
        raise CredentialError("请求记录格式无效。")
    headers = _header_pairs(request.get("headers"))
    values: dict[str, str] = {}
    for name, value in headers:
        key = _HEADER_FIELDS.get(name.lower())
        if key is not None:
            if key in values:
                raise CredentialError("请求包含重复凭据头；无法确定唯一凭据。")
            values[key] = value
    if not values:
        return None
    _validate_url(request.get("url"))
    authorities = [value for name, value in headers if name.lower() in {"host", ":authority"}]
    if (len(authorities) > 1 or (authorities and authorities[0].lower() not in {
            urlsplit(request["url"]).hostname,
            f"{urlsplit(request['url']).hostname}:443",
    })):
        raise CredentialError("请求主机头与批准的网址不一致，或包含重复主机头。")
    if request.get("method") != "GET":
        raise CredentialError("只接受明确的 GET 只读请求；请重新复制单个请求。")
    if set(values) != set(_HEADER_FIELDS.values()):
        raise CredentialError("请求缺少完整的三项凭据头；不得拼接不同请求。")
    return _validate_credentials(values)


def _curl_request(text: str) -> dict[str, object]:
    try:
        lexer = shlex.shlex(text.replace("\\\r\n", "").replace("\\\n", ""),
                            posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        raise CredentialError("cURL 引号或续行格式无效；请选择 cURL（bash）。") from None
    if not tokens or tokens[0] != "curl":
        raise CredentialError("仅接受单条 cURL（bash）请求，不接受脚本或 PowerShell。")
    headers: list[dict[str, str]] = []
    urls: list[str] = []
    method = "GET"
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in {"-H", "--header", "--url", "-X", "--request", "-b", "--cookie", "-A", "--user-agent", "-e", "--referer"}:
            index += 1
            if index >= len(tokens):
                raise CredentialError("cURL 选项缺少值。")
            value = tokens[index]
            if token in {"-H", "--header"}:
                name, separator, content = value.partition(":")
                if not separator or not name or name != name.strip():
                    raise CredentialError("cURL 请求头格式无效。")
                headers.append({"name": name, "value": content.strip(" \t")})
            elif token == "--url":
                urls.append(value)
            elif token in {"-X", "--request"}:
                if method != "GET" or value != "GET":
                    raise CredentialError("只接受 GET 只读请求。")
                method = value
            # Cookie、Referer、User-Agent 只消耗参数，绝不复制到配置。
        elif token in {"--compressed", "--globoff", "--http1.1", "--http2", "--silent", "--show-error", "-s", "-S"}:
            pass
        elif token.startswith("https://"):
            urls.append(token)
        else:
            raise CredentialError("cURL 包含不支持的选项、脚本或多个命令；请复制单个 GET 请求。")
        index += 1
    if len(urls) != 1:
        raise CredentialError("必须明确提供且仅提供一个请求地址。")
    return {"url": urls[0], "method": method, "headers": headers}


def _unique_json(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CredentialError("JSON 包含重复字段；无法确定唯一请求。")
        result[key] = value
    return result


def parse_capture(text: str) -> Credentials:
    """从有限的显式输入提取唯一三元组，不保留原请求及额外请求头。"""
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        raise CredentialError("输入必须为有效的 UTF-8 文本。") from None
    if size > MAX_CAPTURE_BYTES:
        raise CredentialError("输入超过 2 MiB；请仅复制单个已授权的 GET 请求。")
    text = text.lstrip("\ufeff").strip()
    if text.startswith("{"):
        try:
            document = json.loads(text, object_pairs_hook=_unique_json)
        except (ValueError, RecursionError):
            raise CredentialError("JSON 格式无效或包含歧义字段。") from None
        if not isinstance(document, dict):
            raise CredentialError("JSON 必须包含请求地址和请求头。")
        if "log" in document:
            log = document["log"]
            if not isinstance(log, dict) or not isinstance(log.get("entries"), list):
                raise CredentialError("HAR 请求列表格式无效。")
            requests = []
            for entry in log["entries"]:
                if not isinstance(entry, dict) or "request" not in entry:
                    raise CredentialError("HAR 请求记录格式无效。")
                requests.append(entry["request"])
        else:
            requests = [document]
    else:
        requests = [_curl_request(text)]
    selected: Credentials | None = None
    for request in requests:
        candidate = _request_credentials(request)
        if candidate is not None:
            if selected is not None and selected != candidate:
                raise CredentialError("输入包含多组账号或设备凭据；请复制单个已授权请求。")
            selected = candidate
    if selected is None:
        raise CredentialError("未找到完整凭据；需要同一请求中的三项 Oopz 请求头。")
    return selected


def _restrict_windows_acl(path: Path) -> None:
    """空文件先设置受保护的当前用户专用 DACL，成功之后才写入秘密。"""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
    advapi.SetFileSecurityW.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    kernel.LocalFree.restype = wintypes.LPVOID
    token = wintypes.HANDLE()
    sid_text = wintypes.LPWSTR()
    descriptor = wintypes.LPVOID()
    try:
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise OSError("无法限制本地文件访问权限。")
        length = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
        if not length.value:
            raise OSError("无法限制本地文件访问权限。")
        buffer = ctypes.create_string_buffer(length.value)
        if not advapi.GetTokenInformation(token, 1, buffer, length, ctypes.byref(length)):
            raise OSError("无法限制本地文件访问权限。")
        sid = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID))[0]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise OSError("无法限制本地文件访问权限。")
        sddl = f"D:P(A;;FA;;;{sid_text.value})"
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise OSError("无法限制本地文件访问权限。")
        # DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
        if not advapi.SetFileSecurityW(str(path.absolute()), 0x80000004, descriptor):
            raise OSError("无法限制本地文件访问权限。")
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, wintypes.LPVOID))
        if token:
            kernel.CloseHandle(token)


def write_config(path: Path, credentials: Credentials, areas: list[str] | tuple[str, ...]) -> None:
    """独占创建；权限设置失败不写秘密并删除空文件，永不覆写已有路径。"""
    credentials = _validate_credentials({
        "device_id": credentials.device_id, "person_uid": credentials.person_uid,
        "jwt_token": credentials.jwt_token,
    })
    explicit_areas = list(dict.fromkeys(_field(area, maximum=256) for area in areas))
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    content = (
        "# 本地敏感配置：不要提交、上传或分享；删除文件不会撤销服务端会话。\n"
        "# 只订阅下方显式填写的内部域 ID；空列表仅处理私聊。\n"
        "[oopz]\nmethod = \"credentials\"\n"
        f"device_id = {quote(credentials.device_id)}\n"
        f"person_uid = {quote(credentials.person_uid)}\n"
        f"jwt_token = {quote(credentials.jwt_token)}\n\n"
        "[bot]\n"
        f"areas = {quote(explicit_areas)}\n"
        'plugins = ["jeppa.plugins.default:setup"]\n'
    )
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise CredentialError("目标已存在；为保护现有配置，已拒绝覆写。请明确选择新的本地文件名。") from None
    except OSError:
        raise CredentialError("无法独占创建本地配置；请检查目录和访问权限。") from None
    try:
        if os.name == "nt":
            _restrict_windows_acl(path)
        else:
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, ValueError, AttributeError, KeyboardInterrupt):
        if descriptor != -1:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            path.unlink()
        except OSError:
            raise CredentialError("保存失败且无法删除残留文件；请在本机检查目标文件并删除，勿分享其内容。") from None
        raise CredentialError("无法安全设置权限或保存配置；已删除新建文件，未保留配置。") from None


def _manual() -> Credentials:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            url = getpass.getpass("已授权 GET 请求的 HTTPS 地址（隐藏输入）：")
            _validate_url(url)
            values = {
                "device_id": getpass.getpass("Oopz-Device-Id（隐藏输入）："),
                "person_uid": getpass.getpass("Oopz-Person（隐藏输入）："),
                "jwt_token": getpass.getpass("Oopz-Signature（隐藏输入）："),
            }
    except getpass.GetPassWarning:
        raise CredentialError("当前终端不能隐藏输入；已停止，请改用支持隐藏输入的本地终端。") from None
    return _validate_credentials(values)


class _ChineseParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CredentialError("参数无效；请运行 --help 查看用法。不要把凭据放入命令行参数。")

    def format_help(self) -> str:
        return super().format_help().replace("usage: ", "用法：")


def main(argv: list[str] | None = None) -> int:
    parser = _ChineseParser(
        prog="python -m jeppa.credentials", add_help=False,
        description="仅在本机导入已授权请求，不执行 cURL、不联网、不验证服务端登录。",
    )
    parser._optionals.title = "选项"
    parser.add_argument("-h", "--help", action="help", help="显示中文帮助并退出")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--input", type=Path, metavar="本地文件", help="读取单条 cURL（bash）、请求头 JSON 或本地 HAR，最多 2 MiB")
    sources.add_argument("--stdin", action="store_true", help="明确从标准输入读取同样的文本；不自动读取")
    sources.add_argument("--manual", action="store_true", help="隐藏输入已授权请求地址和三项凭据；未指定输入方式时默认使用")
    parser.add_argument("--output", type=Path, default=Path("jeppa.local.toml"), metavar="本地配置", help="新建配置，默认 jeppa.local.toml；拒绝覆写")
    parser.add_argument("--area", action="append", default=[], metavar="内部域ID", help="显式订阅一个内部域 ID，可重复；省略表示仅私聊")
    try:
        arguments = parser.parse_args(argv)
        if arguments.input is not None or arguments.stdin:
            if arguments.input is not None:
                with arguments.input.open("rb") as stream:
                    capture = stream.read(MAX_CAPTURE_BYTES + 1)
            else:
                capture = getattr(sys.stdin, "buffer", sys.stdin).read(MAX_CAPTURE_BYTES + 1)
            if isinstance(capture, bytes):
                if len(capture) > MAX_CAPTURE_BYTES:
                    raise CredentialError("输入超过 2 MiB；请仅复制单个已授权的 GET 请求。")
                capture = capture.decode("utf-8-sig")
            credentials = parse_capture(capture)
        else:
            credentials = _manual()
        write_config(arguments.output, credentials, arguments.area)
    except CredentialError as error:
        print(f"导入失败：{error}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, EOFError, KeyboardInterrupt, RecursionError):
        print("导入失败：无法安全读取输入或完成保存；未输出凭据，请检查本地文件和终端。", file=sys.stderr)
        return 1
    print("已在本机保存受限配置；未联网验证凭据。仅订阅显式域，未指定域时仅处理私聊。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
