# Jeppa

Jeppa 是面向 Oopz 的**非官方文字机器人 / 命令插件运行时**，不隶属于 Oopz，也未获其背书。0.3.0 将所有现有命令整合为可禁用的 default 插件，底座管理稳定插件身份、事务生命周期、多账号监督、权限与冷却、隔离 SQLite 和独立进程。平台身份与适配器边界可扩展，但**当前仅实现 Oopz，未接入 QQ**。没有音频、AI、管理 WebUI 或 OneBot。

[中文项目官网](https://aelurillus.github.io/Jeppa/) · [详细凭据接入](docs/credentials.md) · [版本下载](https://github.com/Aelurillus/Jeppa/releases)

截至当前公开资料调研，尚未确认可使用的 Oopz 公共 Bot API。Jeppa 使用 [Oopzbot-SDK](https://github.com/tangqingfeng7/Oopzbot-SDK) 的 [oopz-sdk 0.16.0](https://pypi.org/project/oopz-sdk/0.16.0/)（MIT），不是官方接口；普通账号能够登录不表示存在获授权的 Bot 接口。

[Oopz 用户服务协议](https://help.oopz.cn/agreement/service) §3.6 对反向工程、未授权第三方工具接入和登录等行为设有限制（除非法律允许或取得书面许可）。本仓库的 MIT 许可、第三方 SDK 的开源许可，均不代表 Oopz 平台授权。接入前应核对适用条款与账号授权；条款可能变化，本段不代替法律意见。

## 安装和启动

需要 **Python 3.11+**，推荐 3.13。Node.js 仅用于仓库文档维护，与机器人运行时无关。可以从仓库根目录安装，或安装 [GitHub Releases](https://github.com/Aelurillus/Jeppa/releases) 中的 wheel；不要假定 PyPI 上的同名包指向本项目。

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

也可使用 `python -m jeppa`。文本路径无需安装 Chromium；SDK 自身声明的 Playwright、Pillow 等传递依赖仍会安装，Jeppa 不启动浏览器。缺少或损坏配置以退出码 `2` 安全退出；运行或管理失败退出码 `1`，Ctrl+C 清理后 `130`。CLI 只保留固定的脱敏核心诊断，不打印异常正文、原始事件或 SDK 标准输出；可信插件执行 Python 的权限不是沙箱，恶意代码可以绕过日志边界。

### 配置

见 [jeppa.example.toml](jeppa.example.toml)。配置只读取显式 `--config` 文件与进程环境，不扫描现有 Oopz 会话，不自动加载 `.env`。文件路径和插件相对路径以启动目录为准；把真实配置保存在被忽略的 `*.local.*` 文件，并使用操作系统权限限制访问。

可以运行 `jeppa import-session --input request.local.txt --area 内部域ID --output jeppa.local.toml`，从自己明确提供的单个 DevTools 请求导入；或使用 `jeppa import-session --manual --area 内部域ID` 隐藏输入三个字段。工具不联网、不执行 cURL、不扫描浏览器、不覆盖已有配置；写出文件限制 OS 权限。**复制请求本身含凭据，不要粘贴到聊天、网站或 issue**。详细步骤、支持格式、过期处理与临时文件清理见 [凭据指南](docs/credentials.md)。

`[oopz]` 支持两种显式方式：

- `method = "credentials"`（默认）：`device_id`、`person_uid`、`jwt_token`；可选 `private_key` PEM 字符串。JWT 过期需要更新配置再启动。签名和可选密钥解析完全交由 SDK，Jeppa 不复制上游内置私钥。
- `method = "password_api"`：`phone`、`password`，可选 `device_id`。使用上游纯 API 登录和其令牌续期，不回退浏览器、不自动化验证码或规避风控；平台拒绝时应停止并核对账号与授权。

对应环境变量 `OOPZ_DEVICE_ID`、`OOPZ_PERSON_UID`、`OOPZ_JWT_TOKEN`、`OOPZ_PRIVATE_KEY`、`OOPZ_LOGIN_METHOD`、`OOPZ_LOGIN_PHONE`、`OOPZ_LOGIN_PASSWORD` 优先于文件。不要通过命令行参数传密码，不要提交或在 issue 中粘贴以上值。

`[bot].areas` 是显式订阅的内部域 ID 数组；默认空数组，仅私信，不自动枚举加入的全部域。`[bot].plugins` 省略时加载 `"jeppa.plugins.default:setup"`；显式 `[]` 不加载任何命令。增加第三方入口时仍需列出 default。插件是受信任的 Python 代码，不是沙箱。

多账号使用 `[[accounts]]`，每项包含唯一 `id`、`platform="oopz"`、`areas`，凭据写入紧随其后的 `[accounts.oopz]`。不得同时使用 `[oopz]`、`bot.areas` 或 `OOPZ_` 环境变量。账号共同运行在一个进程和事件循环，任一账号或插件失败会停止并清理全部账号；同一实际机器人身份禁止重复连接。

`[plugins.插件ID]` 支持 `enabled`、`config`、`areas` 和 `channels`。配置按模块声明的稳定 ID 匹配，不按入口路径；范围与命令权限取交集。省略范围不限制，`[]` 全部拒绝，设置 `areas` 后拒绝私信。default 不接受业务配置键。示例见 [配置模板](jeppa.example.toml)。

`check-config` 只验证本地 TOML、必填字段和类型，不登录、不执行插件、不验证 JWT 有效性或平台可用性。`--help` 和配置诊断不导入 SDK。无平台凭据也可以运行帮助、回归测试和离线 SDK 边界验证。

### 权限与防刷

`[permissions]` 设置默认 `users`、`channels` 和 `cooldown`；`[permissions.commands.命令名]` 逐字段覆盖。ID 使用平台内部 ID，不是昵称或显示域号。省略白名单表示不限制，空数组 `[]` 表示拒绝所有；频道限制同时适用于私信频道。冷却默认 2 秒，`0` 禁用，按命令、平台、机器人账号、会话、用户隔离，在执行前预留；进程重启不保留冷却。

```toml
[permissions]
cooldown = 2

[permissions.commands.faq-set]
users = ["authorized-operator-person-id"]

[permissions.commands.faq-delete]
users = ["authorized-operator-person-id"]
```

权限检查在执行前完成；`/help` 只显示该用户可用的命令，不消耗所列命令的冷却。拒绝和冷却命中均静默，不为刷屏发送额外回复。标记 `restricted=True` 的命令必须有非空用户白名单，即使默认策略开放也不会自动获得权限。Jeppa 不自动推断 Oopz 管理员角色。

### 默认整体插件

`jeppa.plugins.default:setup` 拥有 `/ping`、`/help`、`/status` 及下列全部实用功能，无额外依赖。底座本身没有硬编码命令：

| 命令                           | 行为                                                           |
| ------------------------------ | -------------------------------------------------------------- |
| `/roll`、`/roll 2d6`           | 默认 1d6；支持 1–100 颗、每颗 2–1000000 面，返回各次结果与总和 |
| `/choose 茶 \| 咖啡 \| 白开水` | 从至少两个非空选项中随机选一项，选项可以含空格                 |
| `/faq`、`/faq rules`           | 列出或查询当前域的 FAQ                                         |
| `/faq-set rules 请友善交流`    | 白名单操作员新增或更新条目，答案最多 1800 字符                 |
| `/faq-delete rules`            | 白名单操作员删除条目                                           |

FAQ key 为 1–64 个 ASCII 字母、数字、下划线或连字符，以字母或数字开头，不区分大小写。FAQ 在同域各频道共享、不同域隔离，不在私信中读写；不会预填真实服务器地址或规则。插件回复最多 2000 字符。实际操作员 ID 需要显式配置，示例空白名单不会授予写权限。

### SQLite 与插件数据

`[storage].path` 默认 `data/jeppa.sqlite3`。仅保存插件明确写入的 JSON 值，不自动记录聊天、凭据或命令历史；每次写入独立提交。schema v2 在旧 v1 上事务升级，保留历史数据并拒绝未知未来 schema。这是本地单进程存储，不是共享数据库或插件沙箱。

命名空间包含稳定插件 ID、平台、机器人账号及会话范围。`plugin.store(context, scope="user")` 提供 `get/set/delete/items`；公开频道 `area` 按域、`channel` 按域和频道、`user` 按域、频道和用户隔离；私信禁止 `area`，其余范围保留频道和对方。更换模块入口但保持 `PLUGIN.id` 不影响新数据归属。

**从 0.2.x 升级：**先 `stop` 并备份数据库与本地配置，将旧 `jeppa.plugins.utility:setup` 替换为 `jeppa.plugins.default:setup`。旧库在 SDK 确认账号身份后认领到该账号，FAQ 从旧 utility 命名空间迁入 default；冲突整笔回滚，不覆盖。多账号必须用 `[storage].legacy_account="账号id"` 明确归属，不能猜测。其他历史第三方插件入口名不会自动映射为新 ID，需要维护者显式迁移；不要删除旧库冒充迁移成功。

### 独立运行、停止与日志

```powershell
.\.venv\Scripts\jeppa.exe --version
.\.venv\Scripts\jeppa.exe start --config jeppa.local.toml
.\.venv\Scripts\jeppa.exe status --config jeppa.local.toml
.\.venv\Scripts\jeppa.exe logs --config jeppa.local.toml
.\.venv\Scripts\jeppa.exe stop --config jeppa.local.toml
```

`start` 创建脱离终端和 omp 的独立进程，保留启动工作目录；不要随后移动配置。`run` 是前台模式，同样接受 `stop`。二者使用数据库旁的 OS 文件锁防止同一数据库的重复实例；本机随机令牌控制通道仅监听回环地址，停止会等待 SDK、插件与数据库清理，不向记录的 PID 发送 kill。不同数据库可以启动不同实例，操作员仍须避免为同一账号重复连接。

`status` 只检查本地实例状态，不表示 Oopz 已登录；聊天 `/status` 显示运行时长、插件、命令成功／失败计数和三个独立连接状态。没有显式鉴权帧时显示“鉴权确认：未确认”，不会用收包伪造成功。计数不持久化，状态请求本身在回复后才计入成功。

`[logging]` 默认 `path="logs/jeppa.log"`、`max_bytes=1048576`、`backups=3`，`logs` 显示最近最多 50 条固定脱敏诊断。记录命令名称、结果、耗时和异常类型，不记录参数、用户 ID、聊天正文或 traceback。`stop/status/logs` 不需要账号凭据，但必须指向相同数据库与日志配置；配置仍需语法有效。

限制真实配置、数据目录和日志目录的 OS 权限，仅允许运行账号访问；控制文件中的令牌是本机秘密。Windows 可先创建 `data`，再执行 `icacls data /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F"`。轮转和仓库忽略规则不能代替文件权限。该版本不安装 Windows 服务、计划任务或开机自启，也不在崩溃后自动重启；系统重启后运行 `start`。

### 连接状态与限制

Jeppa 不把上游 `on_ready` 当成上线完成：

1. “连接通道已打开；已发送鉴权”：WebSocket 已连接并发送鉴权，**不表示已经就绪**。
2. “鉴权已确认”：实际收到 `event=21` 且 `checkRes=true`，才记录鉴权确认。
3. “已发送域订阅”：在连接打开并发送鉴权后按 SDK 既有时序发送配置中的域订阅，不等待可能缺席的 `event=21`。仅表示发送成功，不等于鉴权确认、订阅确认或端到端在线。

如果服务端不发显式确认，就保持未确认，不用任意业务帧伪造确认。重连清空以上状态，每次连接打开并发送鉴权后重新发送显式配置的域订阅；之后收到严格布尔值 `checkRes=true` 只更新鉴权确认，不重复订阅。SDK 负责心跳和连接重试；没有断线事件重放、恰好一次处理或持久消息队列保证。私信无需域订阅。Jeppa 和 SDK 都默认不处理自身消息，消息编辑也不会重复触发命令。插件／回复异常为可见运行失败，撤销资源并停机，不无限重试该命令。

## 插件 API

配置 `plugins = ["examples/greet.py:setup"]` 后启动，发送 `/greet Ada`。完整可执行示例见 [examples/greet.py](examples/greet.py)：

```python
from jeppa import Context, Plugin, PluginInfo

PLUGIN = PluginInfo("greet", "问候", "1.0.0", "向指定名字打招呼")


async def setup(plugin: Plugin) -> None:
    async def greet(context: Context, args: tuple[str, ...]) -> None:
        if len(args) != 1:
            await context.reply("用法：/greet <名字>")
            return
        await context.reply(f"👋 你好，{args[0]}！")

    plugin.command("greet", greet, description="打个招呼")
```

- 命令名为 `[a-z][a-z0-9_-]*`；完整首个空白分隔词匹配，`/pingpong` 不触发 `/ping`。命令区分大小写，参数按空白拆分，不做 shell 引号／转义解析；未知命令不回复。`/help` 用中文列出有权使用的命令；`/ping` 回复“🏓 在呢！”。用户输入的选项、名称和 FAQ 正文不会被自动翻译。
- `Message` 保留原消息字段，并要求关键字 `platform`、`bot_id`。`session_id` 为 `(platform, bot_id, "channel", area, channel)` 或 `(platform, bot_id, "private", channel, person)`；不同平台、机器人和私信对方不会共用数据或冷却。自身消息过滤以当前消息的机器人身份为准。
- `await context.reply(text)` 使用 SDK 真实 `EventContext.reply`，频道携带 area/channel/reference message，私信携带对方/channel/reference message，不冒充频道发送。
- 获取资源后立即 `plugin.defer(close_callback)`，支持同步或异步回调，卸载时逆序清理。`plugin.task(coroutine)` 管理后台任务，异常触发停机，卸载取消并等待任务。
- `async def setup(plugin)` 加载失败会撤销已注册命令和资源；重复命令拒绝注册。`await plugin.close()` 卸载插件，正常退出也会关闭所有插件；某项清理失败不跳过其余资源，最后报告聚合错误。可在 Python 中使用 `Runtime.load()` / `Runtime.install()` 显式管理，不提供热重载或自动发现市场。导入文件的顶层副作用无法回滚，资源创建应放在 `setup` 并立即登记清理。
- 插件必须导出 `PLUGIN: PluginInfo`；`id` 唯一且稳定，状态显示友好名称与版本，不暴露入口路径。`plugin.config` 为配置顶层的只读副本，插件负责其业务配置校验。`Runtime.commands(message)` 返回该会话可用命令，`Runtime.plugins()` 返回插件元信息，`Runtime.status(message)` 返回当前账号连接及计数；通过 `set_connection` 发布连接状态，不让业务插件依赖底座私有字典。

## 本地验证

运行时回归使用标准库，无需额外测试框架：

```sh
python -m pip install .
python -m unittest discover -s tests -v
jeppa --help
```

离线回归覆盖命令边界、自回复环、权限与受限命令、并发冷却、过滤帮助、连接状态、插件回滚和关闭、SQLite 重启与隔离、实用命令、本机认证控制及真实子进程清理。测试不连接平台。2026-11-13 授权域 `800704622` 的历史实测已核实 SDK HTTP 200、官方频道消息可见及真实 `event=9` 回流；之后用户确认 `/ping` 得到 `pong`，恢复后的官方页面也显示 `/ping` 和 `/help` 回复。未取得显式 `event=21` 成功帧。**HTTP 成功、消息可见、命令闭环和显式鉴权确认是不同证据，不能互相代替；旧版实测不自动证明新版全部命令在线通过。** 不验证其他域或无关私信。

Node.js 维护工具最低需要 22，建议 [.node-version](.node-version) 指定版本：

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm run check
```

它检查 Markdown、格式、工作流 schema 和 Secretlint，而不是平台可用性。扫描排除 `.venv`、依赖和生成缓存，但真实配置仍被凭证规则扫描；不扫描 Git 历史，也不能保证发现所有秘密。详见 [贡献指南](CONTRIBUTING.md#本地检查)。

## GitHub 自动化

`CI` 对 push、pull request 和手动触发运行；文档检查和 Python 安装 / 回归检查汇总到稳定名称 `Repository checks`。Python 配置覆盖 Ubuntu / Windows 与 3.11 / 3.13，不使用账号或仓库 secrets，不连接 Oopz。第三方 Actions 锁定完整提交 SHA，Dependabot 为 Actions、npm 维护工具和 pip 依赖创建更新 PR，仍需人工审查。

`Release` 工作流在 `v*` tag 或指定现有 tag 的人工触发后核对包版本、构建 wheel／源码包、运行离线测试并创建带附件的 GitHub Release；不发布 PyPI，也不自动部署账号实例。正式版本从通过 PR 和必需检查的主分支打 tag，不能用发布流程绕过分支保护。检查通过不表示平台接入获授权或线上可用。主仓库已核实的 [GitHub 设置](https://github.com/Aelurillus/Jeppa) 包含 Issues、Discussions、私下漏洞报告及单维护者可用的 PR / `Repository checks` 保护；不会随 fork 自动复制，实际状态以 GitHub 为准。

`Project website` 仅上传 `site/` 到 GitHub Pages，不上传仓库配置或账号资料。`公开上游变化信号` 每日监测公开 SDK/PyPI 元数据与 Oopz 网页脚本摘要，并跑已锁定 SDK 的离线边界回归；变化或无法判断时更新单一维护 issue。**它不是私有接口完整验证，不登录、不自动升级依赖，也不保证无变化即账号可用。** 接口升级须人工审查、更新明确基线及授权端到端验证。

## 参与贡献与安全

请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [SECURITY.md](SECURITY.md)。不要在 issue、PR、日志或示例中提交 JWT、密码、私钥、账号配置、会话或原始抓包。漏洞与凭据泄漏请使用私下报告渠道。

## 许可证

本项目按 [MIT 许可证](LICENSE) 发布，版权归 Aelurillus，2026。该许可证不改变 Oopz 平台自身的条款或授权要求。
