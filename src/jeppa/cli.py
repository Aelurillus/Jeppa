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
    r"(?:本地运行时已启动；不代表平台鉴权成功|本地运行时已停止|"
    r"本地运行时失败 异常=[A-Za-z_][A-Za-z0-9_]*|"
    r"连接通道已打开；已发送鉴权；等待 event=21 checkRes=true（尚未确认就绪）|"
    r"已发送 [0-9]+ 个配置域的订阅；鉴权和订阅确认仍不可知|"
    r"未配置域；未发送域订阅|"
    r"连接通道已关闭；鉴权和订阅状态已重置；SDK 可能重连|"
    r"SDK 错误（[A-Za-z_][A-Za-z0-9_]*）；已隐藏详情以保护凭据|"
    r"鉴权已确认（event=21 checkRes=true）|"
    r"命令=[a-z][a-z0-9_-]* 结果=(?:无权限|冷却中|已取消|成功) 耗时=[0-9]+\.[0-9]+秒|"
    r"命令=[a-z][a-z0-9_-]* 结果=失败 耗时=[0-9]+\.[0-9]+秒 异常=[A-Za-z_][A-Za-z0-9_]*)"
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
            raise RuntimeError("连接在启动过程中结束")
        if control is not None:
            control.state = "runtime-open"
        logger.info("本地运行时已启动；不代表平台鉴权成功")
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
        raise daemon.ControlError("实例已在运行；请先使用 status 查看或使用 stop 停止。")
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
                raise daemon.ControlError("子进程在启动时退出；请检查脱敏日志和配置。")
            try:
                status = daemon.request(config.database, "status", token=token, timeout=0.5)
            except daemon.ControlError:
                time.sleep(0.05)
                continue
            if status["state"] == "runtime-open":
                if child.poll() is not None:
                    raise daemon.ControlError("子进程在启动时退出；请检查脱敏日志。")
                print("已启动独立进程，本地运行时已打开；这不代表平台鉴权成功或已就绪。请使用 status 和 logs 查看。")
                return 0
            time.sleep(0.05)
        raise daemon.ControlError("启动超时；请检查脱敏日志。这不代表平台已就绪。")
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
                raise daemon.ControlError("启动失败且子进程清理超时；请检查本地进程和日志。") from None
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
        print("尚无脱敏日志文件；请先使用 run 或 start 启动 Jeppa。")
        return
    for line in lines:
        print(line)
    if not lines:
        print("暂无脱敏日志。")


class _ChineseFormatter(argparse.HelpFormatter):
    def _format_usage(self, usage, actions, groups, prefix):
        return super()._format_usage(usage, actions, groups, prefix or "用法：")


class _ChineseParser(argparse.ArgumentParser):
    """Localize this CLI only; never change argparse's process-wide gettext."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("formatter_class", _ChineseFormatter)
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)
        self._positionals.title = "位置参数"
        self._optionals.title = "选项"
        self.add_argument("-h", "--help", action="help", help="显示帮助并退出")

    def _check_value(self, action, value):
        if action.choices is not None and value not in action.choices:
            choices = "、".join(str(choice) for choice in action.choices)
            raise argparse.ArgumentError(action, f"无效选项 {value!r}（可选：{choices}）")

    def error(self, message):
        # argparse produces these diagnostics before calling error(). Keep
        # translation local and never echo internal English error templates.
        if message.startswith("the following arguments are required: "):
            detail = "缺少必填参数：" + message.partition(": ")[2].replace("command", "命令")
        elif message.startswith("unrecognized arguments: "):
            detail = "无法识别的参数：" + message.partition(": ")[2]
        elif message.startswith("argument "):
            argument, _, reason = message[len("argument "):].partition(": ")
            if reason == "expected one argument":
                reason = "需要一个参数值"
            elif reason.startswith("ignored explicit argument "):
                reason = "此选项不接受参数值"
            elif not reason.startswith("无效选项 "):
                reason = "参数格式无效；请查看帮助"
            detail = f"参数 {argument}：{reason}"
        elif message.startswith("ambiguous option: "):
            detail = "选项不明确；请使用完整选项名称"
        else:
            detail = "命令行参数无效；请使用 --help 查看帮助"
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}：错误：{detail}\n")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(encoding="utf-8")
    parser = _ChineseParser(prog="jeppa", description="非官方 Oopz 文字机器人。仅可连接已获得明确授权的账号。")
    try:
        installed_version = version("jeppa")
    except PackageNotFoundError:
        installed_version = "未安装"
    parser.add_argument("--version", action="version", version=f"jeppa {installed_version}", help="显示版本并退出")
    commands = parser.add_subparsers(dest="command", required=True, title="命令", metavar="命令")
    for name, description in (
        ("run", "在前台连接 Oopz"), ("start", "启动独立后台进程"),
        ("stop", "平稳停止本地实例（不登录）"),
        ("status", "查看本地进程状态（不登录）"),
        ("logs", "显示最近 50 行脱敏日志（不登录）"),
        ("check-config", "离线校验配置，不登录或连接 SDK"),
    ):
        command = commands.add_parser(name, help=description, description=description)
        command.add_argument("--config", type=Path, metavar="配置文件", help="本地 TOML 文件；省略时仅使用环境变量")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config, require_auth=args.command not in {"stop", "status", "logs"})
    except ConfigError as error:
        print(f"配置错误：{error}", file=sys.stderr)
        return 2
    if args.command == "check-config":
        print("配置有效（仅检查本地语法和必填字段）；未登录、验证令牌、执行插件或检查网络。")
        return 0
    try:
        if args.command == "start":
            return _start(config, args.config)
        if args.command == "stop":
            daemon.stop(config.database, timeout=STOP_TIMEOUT)
            print("已停止；插件、SDK 和存储清理完成后已释放实例锁。")
            return 0
        if args.command == "status":
            if not daemon.is_running(config.database):
                print("已停止；没有实例持有数据库锁。")
                return 0
            status = daemon.request(config.database, "status")
            state = {"starting": "启动中", "runtime-open": "本地运行时已打开", "stopping": "停止中"}[status["state"]]
            print(f"进程状态：{state}；这不代表平台鉴权成功或已就绪。请在已授权聊天中使用 /status 查看连接状态。")
            return 0
        if args.command == "logs":
            _logs(config)
            return 0
        token = os.environ.pop("JEPPA_CONTROL_TOKEN", None)
        if token is not None and re.fullmatch(r"[0-9a-f]{64}", token) is None:
            raise daemon.ControlError("本地启动令牌无效；请使用 jeppa start 重新启动。")
        with _logging(config):
            try:
                # SDK stdout/stderr may contain raw login responses. Only our
                # sanitized handlers retain the original stderr stream.
                with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink), redirect_stderr(sink):
                    asyncio.run(daemon.supervise(config.database, lambda control: launch(config, control), token=token))
            except BaseException as error:
                if not isinstance(error, (KeyboardInterrupt, SystemExit, daemon.ControlError)):
                    logger.error("本地运行时失败 异常=%s", type(error).__name__)
                raise
            logger.info("本地运行时已停止")
    except daemon.ControlError as error:
        print(f"本地管理错误：{error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已停止；插件和 SDK 清理已完成。")
        return 130
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        print(f"操作失败（{type(error).__name__}）；已隐藏详情以保护凭据。请检查配置、文件权限、脱敏日志、插件清理和 SDK 兼容性。这不代表平台已就绪。", file=sys.stderr)
        return 1
    return 0
