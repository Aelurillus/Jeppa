# Jeppa

<!-- markdownlint-disable MD033 -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="site/assets/logo-dark.png">
  <img src="site/assets/logo.png" width="360" height="113" alt="Jeppa">
</picture>
<!-- markdownlint-enable MD033 -->

一个用 Python 写的 Oopz 文字机器人框架。可以直接用它掷骰、随机选东西、维护群内 FAQ，也可以写自己的命令插件。

Jeppa 负责消息收发、命令权限、冷却和数据存储，具体功能交给插件。目前只支持 Oopz 文字消息，没有接入 QQ、OneBot、语音或 AI，也没有管理面板。

如果你在找 Oopz Bot、oopzBot 或 Oopz 机器人，Jeppa 提供的是可本地运行、可用 Python 扩展的文字机器人。Jeppa 与其使用的 Oopzbot-SDK 是独立项目，也不是 Oopz 官方机器人。

[项目网站](https://aelurillus.github.io/Jeppa/) · [安装指南](https://aelurillus.github.io/Jeppa/guide.html) · [凭据接入](docs/credentials.md) · [版本下载](https://github.com/Aelurillus/Jeppa/releases) · [更新记录](CHANGELOG.md)

> 这是非官方项目，通过社区 [Oopzbot-SDK](https://github.com/tangqingfeng7/Oopzbot-SDK) 接入，当前使用 `oopz-sdk 0.16.0`。使用前请确认账号和目标域的授权，并阅读 [Oopz 用户服务协议](https://help.oopz.cn/agreement/service)（尤其是 §3.6 的第三方工具接入限制）。项目的 MIT 许可不代表平台授权。

## 能做什么

默认插件带有以下命令：

| 命令                           | 用途                                   |
| ------------------------------ | -------------------------------------- |
| `/ping`                        | 看看机器人有没有回应                   |
| `/help`                        | 查看自己有权使用的命令                 |
| `/status`                      | 查看运行时间、插件、命令计数和连接状态 |
| `/roll`、`/roll 2d6`           | 掷骰，默认一颗六面骰                   |
| `/choose 茶 \| 咖啡 \| 白开水` | 从给出的选项中随机选一个               |
| `/faq`、`/faq rules`           | 列出或查询当前域的 FAQ                 |
| `/faq-set rules 请友善交流`    | 新增或更新 FAQ，需要配置操作员白名单   |
| `/faq-delete rules`            | 删除 FAQ，同样需要白名单               |

FAQ 在同一域的各频道共享，不同域互不影响，不支持私信。默认插件可以整体关闭；不需要的命令也可以通过权限配置禁用。

## 安装和启动

需要 **Python 3.11+**，推荐 3.13。下载源码后，在仓库根目录执行下面的命令。也可以安装 [Releases](https://github.com/Aelurillus/Jeppa/releases) 中的 wheel；本项目尚未发布到 PyPI，请不要直接安装同名包。

### Windows PowerShell

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
Copy-Item jeppa.example.toml jeppa.local.toml
```

按下一节填写 `jeppa.local.toml`，然后启动：

```powershell
.\.venv\Scripts\jeppa.exe check-config --config jeppa.local.toml
.\.venv\Scripts\jeppa.exe run --config jeppa.local.toml
```

### Linux / macOS

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
cp jeppa.example.toml jeppa.local.toml
# 填好配置后：
.venv/bin/jeppa check-config --config jeppa.local.toml
.venv/bin/jeppa run --config jeppa.local.toml
```

不需要激活虚拟环境，也不需要安装浏览器。Node.js 只在维护仓库文档时用到。

### 填写配置

配置模板在 [jeppa.example.toml](jeppa.example.toml)。首次接入建议按[凭据指南](docs/credentials.md)操作：从自己已登录的 Oopz 网页复制一条指定请求，保存到本地，再导入配置。**复制出的请求含有账号凭据，不要执行它，也不要发到聊天、issue 或在线解析网站。**

用下面的命令导入，替换示例中的内部域 ID。输出文件必须尚不存在；这一步可以代替前面的复制模板和手填配置。如果已经复制了模板，直接按指南填写即可。

```sh
jeppa import-session --input request.local.txt --area 内部域ID --output jeppa.local.toml
```

这里的 `jeppa` 指虚拟环境中的可执行文件，Windows 使用 `.\.venv\Scripts\jeppa.exe`，Linux / macOS 使用 `.venv/bin/jeppa`。导入器只在本地解析，不执行 cURL、不联网、不扫描浏览器，也不会覆盖已有配置。也可以使用 `jeppa import-session --manual --area 内部域ID` 隐藏输入所需字段。

几个容易弄错的地方：

- `[bot].areas` 填**内部域 ID**，不是界面上的域号；留空时只收私信，不会自动订阅所有已加入的域。
- 默认加载 `jeppa.plugins.default:setup`。自定义 `bot.plugins` 时，想保留默认命令就要把它也列进去；`[]` 表示不加载任何命令。
- `check-config` 只检查本地配置，不会登录或验证凭据是否过期。凭据过期后需要更新并重启。
- 真实配置请留在本地 `*.local.*` 文件中，并限制文件访问权限。Jeppa 不自动读取 `.env`；单账号的 `OOPZ_` 环境变量优先于文件。

密码 API 登录、多账号、插件范围和其他字段见[完整安装指南](https://aelurillus.github.io/Jeppa/guide.html)与配置模板。

### 权限和 FAQ 管理

默认命令冷却为 2 秒。用户和频道白名单可按命令单独设置；省略表示不限制，空数组 `[]` 表示全部禁止。

FAQ 的写入和删除默认不开放，需要在配置中填入操作员的**用户内部 ID**：

```toml
[permissions.commands.faq-set]
users = ["authorized-operator-person-id"]

[permissions.commands.faq-delete]
users = ["authorized-operator-person-id"]
```

Jeppa 不会自动把 Oopz 管理员当作操作员。没有权限或触发冷却时，机器人不会额外回复。

## 后台运行

前面的 `run` 在当前终端运行，按 Ctrl+C 停止。需要关闭终端后继续运行时，改用 `start`：

```sh
jeppa start --config jeppa.local.toml
jeppa status --config jeppa.local.toml
jeppa logs --config jeppa.local.toml
jeppa stop --config jeppa.local.toml
```

它不依赖 omp，也不安装系统服务或开机自启；重启电脑后需要重新 `start`。后台运行后不要移动配置，管理命令应使用相同的配置和工作目录。

插件数据默认保存在 `data/jeppa.sqlite3`，日志在 `logs/jeppa.log`。日志不记录聊天正文、命令参数或凭据；仍请限制配置、数据和日志目录的访问权限。

**本地进程在运行，不等于平台已登录。** `/status` 会分别显示连接打开、鉴权确认和订阅发送状态；没有收到明确的鉴权确认就会保留“未确认”。测试命令请用另一个账号发送，机器人会忽略自己发出的消息。更多说明见[连接状态](https://aelurillus.github.io/Jeppa/guide.html#connection)。

从 **0.2.x 升级**时，先停机并备份配置和数据库，再把旧的 `jeppa.plugins.utility:setup` 改成 `jeppa.plugins.default:setup`。多账号迁移还需指定旧数据归属，详见[迁移说明](https://aelurillus.github.io/Jeppa/guide.html#migration)。

## 写一个插件

例如，添加一个 `/greet Ada` 命令：

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

保存为 `examples/greet.py`，在配置的 `[bot]` 中加入：

```toml
[bot]
plugins = ["jeppa.plugins.default:setup", "examples/greet.py:setup"]
```

`PLUGIN.id` 是插件的稳定标识，也用于配置和数据隔离。命令使用异步函数，通过 `context.reply()` 回复。需要保存数据时使用 `plugin.store()`；打开的资源用 `plugin.defer()` 登记清理，后台任务用 `plugin.task()` 管理。

插件运行的是普通 Python 代码，没有沙箱，只加载你信任的插件。接口和完整示例见[插件开发指南](https://aelurillus.github.io/Jeppa/plugins.html)与 [examples/greet.py](examples/greet.py)。

## 开发和维护

离线回归使用标准库 `unittest`，不需要 Oopz 账号：

```sh
python -m pip install .
python -m unittest discover -s tests -v
jeppa --help
```

文档和工作流检查需要 Node.js 22+，推荐 [.node-version](.node-version) 指定的版本：

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm run check
```

GitHub CI 覆盖 Windows / Ubuntu 和 Python 3.11 / 3.13；版本发布提供 wheel 和源码包，不自动部署机器人。每日任务会检查公开 SDK 和 Oopz 网页资源的变化，异常时更新维护 issue，供人工排查，不会自动升级依赖。它不登录账号，也不是平台可用性检测，详见[兼容性说明](https://aelurillus.github.io/Jeppa/compatibility.html)。

贡献前请看 [CONTRIBUTING.md](CONTRIBUTING.md)。报告问题时请附上版本、复现步骤和脱敏日志；账号凭据、原始请求和安全漏洞请勿公开提交，安全问题按 [SECURITY.md](SECURITY.md) 联系维护者。

## 许可证

[MIT](LICENSE) · Aelurillus
