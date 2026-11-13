"""公开上游变化信号；只读取数据，不下载或执行候选 SDK。

维护者审核后显式运行 --update-baseline；日常检查永不自动认可变化。
退出码：0 无变化（或基线更新成功），2 有变化，1 无法完整判断。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
from http.client import HTTPException
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

PYPI_URL = "https://pypi.org/pypi/oopz-sdk/json"
SDK_URL = "https://api.github.com/repos/tangqingfeng7/Oopzbot-SDK/commits/HEAD"
WEB_URL = "https://web.oopz.cn/"
APPROVED_HOSTS = frozenset({"pypi.org", "api.github.com", "web.oopz.cn"})
SCRIPT_HOSTS = frozenset({"web.oopz.cn"})
MAX_REDIRECTS = 3
MAX_SCRIPTS = 16
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_HTML_BYTES = 1024 * 1024
MAX_SCRIPT_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
REQUEST_SECONDS = 30
TOTAL_SECONDS = 180
SOURCE_LABELS = {"pypi": "PyPI 发布元数据", "sdk": "SDK 默认分支 HEAD", "web": "Oopz 公开网页与脚本"}
LIMITATIONS = [
    "公开变化仅供人工审核，不证明私有接口已损坏或兼容。",
    "不登录、不携带凭据、不调用私有接口、不安装或执行候选 SDK。",
    "仅检查入口 HTML、静态 script src 和 Flutter mainJsPath；不递归执行脚本、加载动态依赖或 WebAssembly。",
    "脚本只允许精确白名单主机与安全路径；查询和片段不发送，入口 HTML 摘要仍会捕捉其变化。",
    "契约回归只验证项目已固定安装的 SDK，不验证远端候选版本。",
]
SAFE_PATH = re.compile(r"/[A-Za-z0-9_./@+,\-]*\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ObservationError(Exception):
    """故障详情不进入公开报告。"""


def approved_url(url: str, hosts=APPROVED_HOSTS) -> str:
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https" and parsed.hostname in hosts
            and parsed.netloc == parsed.hostname
            and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment
            and SAFE_PATH.fullmatch(parsed.path or "/")
            and not any(part in {".", ".."} for part in parsed.path.split("/"))
        )
    except (TypeError, ValueError):
        raise ObservationError() from None
    if not valid:
        raise ObservationError()
    return urlunsplit(("https", parsed.hostname, parsed.path or "/", "", ""))


class RestrictedRedirects(HTTPRedirectHandler):
    def __init__(self, host: str, deadline: float):
        self.host = host
        self.deadline = deadline
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > MAX_REDIRECTS:
            raise ObservationError()
        approved_url(newurl, {self.host})
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ObservationError()
        req.timeout = min(req.timeout, remaining)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Fetcher:
    """直接 HTTPS、无环境代理、无 cookie；限制重定向、字节和时间。"""
    def __init__(self):
        self.deadline = time.monotonic() + TOTAL_SECONDS
        self.total = 0

    def __call__(self, url: str, maximum: int) -> bytes:
        url = approved_url(url)
        remaining = min(REQUEST_SECONDS, self.deadline - time.monotonic())
        if remaining <= 0:
            raise ObservationError()
        end = time.monotonic() + remaining
        opener = build_opener(ProxyHandler({}), RestrictedRedirects(urlsplit(url).hostname, end))
        request = Request(url, headers={
            "User-Agent": "Jeppa-Upstream-Monitor/0.3 (public metadata only)",
            "Accept-Encoding": "identity",
            "Accept": "application/json, text/html, application/javascript, */*;q=0.1",
        })
        try:
            with opener.open(request, timeout=remaining) as response:
                approved_url(response.geturl(), {urlsplit(url).hostname})
                if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                    raise ObservationError()
                length = response.headers.get("Content-Length")
                if length is not None and (not length.isdigit() or int(length) > maximum):
                    raise ObservationError()
                chunks = []
                size = 0
                while True:
                    if response.isclosed():
                        break
                    left = min(end, self.deadline) - time.monotonic()
                    if left <= 0:
                        raise ObservationError()
                    # read1 不等待填满缓冲区；每次接收重新收紧 socket 超时。
                    response.fp.raw._sock.settimeout(left)
                    chunk = response.read1(min(65536, maximum - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    self.total += len(chunk)
                    if size > maximum or self.total > MAX_TOTAL_BYTES:
                        raise ObservationError()
                    chunks.append(chunk)
                if length is not None and size != int(length):
                    raise ObservationError()
                return b"".join(chunks)
        except (OSError, HTTPError, URLError, HTTPException, ValueError, AttributeError):
            raise ObservationError() from None


def json_data(data: bytes):
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        raise ObservationError() from None


def observe_pypi(fetch):
    data = json_data(fetch(PYPI_URL, MAX_METADATA_BYTES))
    try:
        version = data["info"]["version"]
        if not isinstance(version, str) or not re.fullmatch(r"[0-9][A-Za-z0-9.!+_-]{0,79}", version):
            raise ObservationError()
        files = data["urls"]
        if not isinstance(files, list) or not 1 <= len(files) <= 32:
            raise ObservationError()
        artifacts = []
        for item in files:
            name, digest, size, yanked = item["filename"], item["digests"]["sha256"], item["size"], item["yanked"]
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.+\-]{1,160}", name)
                    or not isinstance(digest, str) or not SHA256.fullmatch(digest)
                    or type(size) is not int or size <= 0 or type(yanked) is not bool):
                raise ObservationError()
            artifacts.append({"filename": name, "sha256": digest, "size": size, "yanked": yanked})
        return {"version": version, "artifacts": sorted(artifacts, key=lambda item: item["filename"])}
    except (KeyError, TypeError, AttributeError):
        raise ObservationError() from None


def observe_sdk(fetch):
    data = json_data(fetch(SDK_URL, MAX_METADATA_BYTES))
    try:
        sha = data["sha"]
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ObservationError()
        return {"head": sha}
    except (KeyError, TypeError):
        raise ObservationError() from None


class EntryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.base = None
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "base" and self.base is None and attrs.get("href"):
            self.base = attrs["href"]
        if tag == "script" and attrs.get("src"):
            self.scripts.append(attrs["src"])


def script_url(base: str, value: str) -> str:
    # 不传播远端查询参数、片段或 URL 中的用户信息。
    try:
        parsed = urlsplit(urljoin(base, value))
        if parsed.username or parsed.password:
            raise ObservationError()
        return approved_url(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")), SCRIPT_HOSTS)
    except ValueError:
        raise ObservationError() from None


def observe_web(fetch):
    html = fetch(WEB_URL, MAX_HTML_BYTES)
    try:
        text = html.decode("utf-8")
        parser = EntryParser()
        parser.feed(text)
        base = script_url(WEB_URL, parser.base) if parser.base else WEB_URL
        resources = {script_url(base, value) for value in parser.scripts}
        # Flutter 的内联公开 buildConfig 只提取字符串，不解释或执行 JS。
        for value in re.findall(r'"mainJsPath"\s*:\s*("(?:[^"\\]|\\.)*")', text):
            resources.add(script_url(base, json.loads(value)))
        if not 1 <= len(resources) <= MAX_SCRIPTS:
            raise ObservationError()
        scripts = []
        for url in sorted(resources):
            payload = fetch(url, MAX_SCRIPT_BYTES)
            if not payload:
                raise ObservationError()
            scripts.append({"resource": urlsplit(url).hostname + urlsplit(url).path,
                            "sha256": hashlib.sha256(payload).hexdigest()})
        return {"entry_sha256": hashlib.sha256(html).hexdigest(), "scripts": scripts}
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise ObservationError() from None


def observe(fetch):
    sources, failures = {}, []
    for key, function in (("pypi", observe_pypi), ("sdk", observe_sdk), ("web", observe_web)):
        try:
            sources[key] = function(fetch)
        except ObservationError:
            failures.append(SOURCE_LABELS[key] + "：采集失败或超出安全限制")
    return sources, failures


def read_baseline(path: Path):
    # 基线也不可信：禁止把原始文本或异常内容回显到报告。
    try:
        if path.stat().st_size > MAX_METADATA_BYTES:
            raise ObservationError()
        baseline = json_data(path.read_bytes())
        if (not isinstance(baseline, dict) or baseline.get("schema") != 1
                or not isinstance(baseline.get("sources"), dict)
                or set(baseline["sources"]) != set(SOURCE_LABELS)):
            raise ObservationError()
        return baseline["sources"]
    except (OSError, KeyError, TypeError):
        raise ObservationError() from None


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None, *, fetch=None) -> int:
    parser = argparse.ArgumentParser(description="检查公开上游变化信号（不登录、不执行远端代码）")
    parser.add_argument("--baseline", type=Path, default=Path(__file__).with_name("upstream-baseline.json"), help="经审核的基线 JSON")
    parser.add_argument("--output", type=Path, default=Path("upstream-report.json"), help="脱敏 JSON 报告路径")
    parser.add_argument("--update-baseline", action="store_true", help="维护者审核后，显式接受本次完整观测为基线")
    args = parser.parse_args(argv)
    if args.output.resolve() == args.baseline.resolve():
        parser.error("报告和基线路径必须不同")
    sources, failures = observe(fetch if fetch is not None else Fetcher())
    changes = []
    if not args.update_baseline:
        try:
            baseline = read_baseline(args.baseline)
            changes = [SOURCE_LABELS[key] + "发生变化" for key in SOURCE_LABELS
                       if key in sources and sources[key] != baseline[key]]
        except ObservationError:
            failures.append("基线缺失或格式无效：请维护者审核后显式更新")
    status = "inconclusive" if failures else "changed" if changes else "unchanged"
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if args.update_baseline and not failures:
        try:
            save_json(args.baseline, {"schema": 1, "observed_at": checked_at, "sources": sources})
        except OSError:
            failures.append("基线写入失败")
            status = "inconclusive"
    report = {"checked_at": checked_at, "status": status, "changes": changes,
              "sources": sources, "failures": failures, "limitations": LIMITATIONS,
              "baseline_updated": bool(args.update_baseline and not failures)}
    try:
        save_json(args.output, report)
    except OSError:
        print("公开上游报告写入失败；未输出异常或远端原文。")
        return 1
    print({"unchanged": "公开上游：未发现变化", "changed": "公开上游：发现变化，需人工审核",
           "inconclusive": "公开上游：无法完整判断，需检查报告"}[status])
    return {"unchanged": 0, "changed": 2, "inconclusive": 1}[status]


if __name__ == "__main__":
    raise SystemExit(main())
