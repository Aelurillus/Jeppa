# Jeppa

Jeppa 是面向 Oopz 的**非官方文字机器人 / 命令插件运行时**，定位类似 Koishi / Discord Bot 框架的文字命令部分，不隶属于 Oopz，也未获其背书。现已提供可安装的 Python CLI、社区 SDK 适配、频道 / 私信回复、插件生命周期，以及内置 `/ping`、`/help`。没有音频、AI、管理 WebUI、OneBot 或多平台兼容层。

截至当前公开资料调研，尚未确认可使用的 Oopz 公共 Bot API。Jeppa 使用 [Oopzbot-SDK](https://github.com/tangqingfeng7/Oopzbot-SDK) 的 [oopz-sdk 0.16.0](https://pypi.org/project/oopz-sdk/0.16.0/)（MIT），不是官方接口；普通账号能够登录不表示存在获授权的 Bot 接口。

[Oopz 用户服务协议](https://help.oopz.cn/agreement/service) §3.6 对反向工程、未授权第三方工具接入和登录等行为设有限制（除非法律允许或取得书面许可）。本仓库的 MIT 许可、第三方 SDK 的开源许可，均不代表 Oopz 平台授权。接入前应核对适用条款与账号授权；条款可能变化，本段不代替法律意见。

## 安装和启动

需要 **Python 3.11+**，推荐 3.13。Node.js 仅用于仓库文档维护，与机器人运行时无关。从仓库根目录安装；当前未发布 PyPI 包，不要假定 `pip install jeppa` 指向本项目。

Windows PowerShell（不需要激活 venv，也不需要修改执行策略）：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\jeppa.exe --help
Copy-Item jeppa.example.toml jeppa.local.toml
# 用编辑器填写 jeppa.local.toml，不要在公开终端记录中粘贴凭据。
.\.venv\Scripts\jeppa.exe check-config --config jeppa.local.toml
.\.venv\Scripts\jeppa.exe run --config jeppa.local.toml
```

Linux / macOS：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/jeppa --help
cp jeppa.example.toml jeppa.local.toml
# 本地编辑配置后执行：
.venv/bin/jeppa check-config --config jeppa.local.toml
.venv/bin/jeppa run --config jeppa.local.toml
```

也可使用 `python -m jeppa`。文本路径无需安装 Chromium；SDK 自身声明的 Playwright、Pillow 等传递依赖仍会安装，Jeppa 不启动浏览器。缺少或损坏配置以退出码 `2` 安全退出并给出配置命令；运行失败退出码 `1`，Ctrl+C 清理后 `130`。CLI 不输出异常正文、凭据或原始事件，错误类别及失败阶段用于定位问题；受信任的第三方插件自行打印的数据不受 CLI 日志过滤保证。

### 配置

见 [jeppa.example.toml](jeppa.example.toml)。配置只读取显式 `--config` 文件与进程环境，不扫描现有 Oopz 会话，不自动加载 `.env`。文件路径和插件相对路径以启动目录为准；把真实配置保存在被忽略的 `*.local.*` 文件，并使用操作系统权限限制访问。

`[oopz]` 支持两种显式方式：

- `method = "credentials"`（默认）：`device_id`、`person_uid`、`jwt_token`；可选 `private_key` PEM 字符串。JWT 过期需要更新配置再启动。签名和可选密钥解析完全交由 SDK，Jeppa 不复制上游内置私钥。
- `method = "password_api"`：`phone`、`password`，可选 `device_id`。使用上游纯 API 登录和其令牌续期，不回退浏览器、不自动化验证码或规避风控；平台拒绝时应停止并核对账号与授权。

对应环境变量 `OOPZ_DEVICE_ID`、`OOPZ_PERSON_UID`、`OOPZ_JWT_TOKEN`、`OOPZ_PRIVATE_KEY`、`OOPZ_LOGIN_METHOD`、`OOPZ_LOGIN_PHONE`、`OOPZ_LOGIN_PASSWORD` 优先于文件。不要通过命令行参数传密码，不要提交或在 issue 中粘贴以上值。

`[bot].areas` 是显式订阅的域 ID 数组；默认空数组，不自动枚举并订阅账号加入的全部域。频道回复保留域和频道，私信保留对方与私信频道。`[bot].plugins` 是可信 Python 入口数组，例如 `"examples/greet.py:setup"` 或已安装模块的 `"my_plugin:setup"`。这是执行代码的权限，不是插件沙箱。

`check-config` 只验证本地 TOML、必填字段和类型，不登录、不执行插件、不验证 JWT 有效性或平台可用性。`--help` 和配置诊断不导入 SDK。无平台凭据也可以运行帮助、回归测试和离线 SDK 边界验证。

### 连接状态与限制

Jeppa 不把上游 `on_ready` 当成上线完成：

1. `transport-open; auth-sent`：WebSocket 已连接并发送鉴权，**不是 ready**。
2. `auth-confirmed`：实际收到 `event=21` 且 `checkRes=true`，才记录鉴权确认。
3. `subscription-sent`：在 transport-open / auth-sent 后按 SDK 既有时序发送配置中的域订阅，不等待可能缺席的 `event=21`。仅表示发送成功，不等于鉴权确认、订阅确认或端到端在线。

如果服务端不发显式确认，就保持未确认，不用任意业务帧伪造确认。重连清空以上状态，每次 transport-open / auth-sent 后重新发送显式配置的域订阅；之后收到严格布尔值 `checkRes=true` 只更新鉴权确认，不重复订阅。SDK 负责心跳和连接重试；没有断线事件重放、恰好一次处理或持久消息队列保证。私信无需域订阅。Jeppa 和 SDK 都默认不处理自身消息，消息编辑也不会重复触发命令。插件 / 回复异常为可见运行失败，撤销资源并停机，不无限重试该命令。

## 插件 API

配置 `plugins = ["examples/greet.py:setup"]` 后启动，发送 `/greet Ada`。完整可执行示例见 [examples/greet.py](examples/greet.py)：

```python
from jeppa import Context, Plugin


async def setup(plugin: Plugin) -> None:
    async def greet(context: Context, args: tuple[str, ...]) -> None:
        if len(args) != 1:
            await context.reply("Usage: /greet <name>")
            return
        await context.reply(f"Hello, {args[0]}!")

    plugin.command("greet", greet, description="Greet one name")
```

- 命令名为 `[a-z][a-z0-9_-]*`；完整首个空白分隔词匹配，`/pingpong` 不触发 `/ping`。命令区分大小写，参数按空白拆分，不做 shell 引号 / 转义解析；未知命令不回复。`/help` 列出当前注册命令；`/ping` 回复 `pong`。
- `context.message` 保留 `text`、`area`、`channel`、`person`、`message_id`、`is_private`。`session_id` 为频道的 `("channel", area, channel)` 或私信的 `("private", channel, person)`，不把不同域或私信对方折叠成一个 ID；不会自动持久化用户数据。
- `await context.reply(text)` 使用 SDK 真实 `EventContext.reply`，频道携带 area/channel/reference message，私信携带对方/channel/reference message，不冒充频道发送。
- 获取资源后立即 `plugin.defer(close_callback)`，支持同步或异步回调，卸载时逆序清理。`plugin.task(coroutine)` 管理后台任务，异常触发停机，卸载取消并等待任务。
- `async def setup(plugin)` 加载失败会撤销已注册命令和资源；重复命令拒绝注册。`await plugin.close()` 卸载插件，正常退出也会关闭所有插件；某项清理失败不跳过其余资源，最后报告聚合错误。可在 Python 中使用 `Runtime.load()` / `Runtime.install()` 显式管理，不提供热重载或自动发现市场。导入文件的顶层副作用无法回滚，资源创建应放在 `setup` 并立即登记清理。

## 本地验证

运行时回归使用标准库，无需额外测试框架：

```sh
python -m pip install .
python -m unittest discover -s tests -v
jeppa --help
```

永久回归覆盖命令边界、参数、自回复环、会话 ID、启动回滚 / 资源撤销、鉴权状态、无显式确认时的单域订阅、编辑事件及失败传播；使用真实 SDK parser，仅在网络发送边界截获调用。2026-11-13 Windows Python 3.13.17 普通包重装及 7 项适配器回归通过。经用户明确授权，在显示 ID `800704622` 的域内从官方网页真实请求核实内部域 / 频道；SDK 对该域无副作用 HTTP 请求返回 200，受控 SDK 发送的标注测试消息在官方“综合文字”频道可见。修复后安装包的生产 CLI 路径仅订阅该域，收到目标频道标注测试消息的真实 `event=9`；发送者为自身，SDK 自消息过滤判断为真。临时观察器只记录脱敏收包信息，不修改订阅或过滤行为；100 秒窗口结束后进程退出码为 2，不声称优雅退出。未收到另一账号的 `/ping`，因此 `/ping` → `pong` 命令闭环未验证；本次也未收到显式 `event=21` 成功确认。**HTTP 成功、消息可见、真实消息回流和显式鉴权确认是不同证据，不能互相代替。** 仅使用账号持有者明确授权的会话及指定域，不验证其他域或私信。

Node.js 维护工具最低需要 22，建议 [.node-version](.node-version) 指定版本：

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm run check
```

它检查 Markdown、格式、工作流 schema 和 Secretlint，而不是平台可用性。扫描排除 `.venv`、依赖和生成缓存，但真实配置仍被凭证规则扫描；不扫描 Git 历史，也不能保证发现所有秘密。详见 [贡献指南](CONTRIBUTING.md#本地检查)。

## GitHub 自动化

`CI` 对 push、pull request 和手动触发运行；文档检查和 Python 安装 / 回归检查汇总到稳定名称 `Repository checks`。Python 配置覆盖 Ubuntu / Windows 与 3.11 / 3.13，不使用账号或仓库 secrets，不连接 Oopz。第三方 Actions 锁定完整提交 SHA，Dependabot 为 Actions、npm 维护工具和 pip 依赖创建更新 PR，仍需人工审查。

当前没有 **CD**：不发布包、不创建版本 tag/release、不部署机器人。检查通过不表示平台接入获授权或线上可用。主仓库已核实的 [GitHub 设置](https://github.com/Aelurillus/Jeppa) 包含 Issues、Discussions、私下漏洞报告及单维护者可用的 PR / `Repository checks` 保护；不会随 fork 自动复制，实际状态以 GitHub 为准。

## 参与贡献与安全

请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [SECURITY.md](SECURITY.md)。不要在 issue、PR、日志或示例中提交 JWT、密码、私钥、账号配置、会话或原始抓包。漏洞与凭据泄漏请使用私下报告渠道。

## 许可证

本项目按 [MIT 许可证](LICENSE) 发布，版权归 Aelurillus，2026。该许可证不改变 Oopz 平台自身的条款或授权要求。
