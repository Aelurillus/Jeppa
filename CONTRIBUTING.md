# 贡献指南

感谢参与 Jeppa。当前实现 Python 文字机器人 CLI、命令权限与冷却、社区 SDK 适配、显式命令插件、隔离 SQLite 和独立进程管理。功能变更请说明需求、平台授权边界和可验证行为，不要将第三方 SDK 的存在当成平台授权；不要求普通开源文档／代码贡献者提供平台接入许可。

## 提交问题

- 使用 bug 表单报告文档或检查错误，附上系统、工具版本、脱敏复现步骤、预期和实际结果。
- 使用功能表单说明使用场景、范围和验收方式，区分设想与已验证事实。
- 安全漏洞、账号资料和凭证不得公开提交；请遵循 [安全政策](SECURITY.md)。

## 本地检查

机器人需要 Python 3.11+；Node.js 只用于仓库维护工具，建议使用 [.node-version](.node-version) 中的版本，最低 Node.js 22。

在仓库根目录运行：

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm run check
```

运行时检查独立于 npm；无需 Oopz 凭据或 Chromium：

```sh
python -m pip install .
python -m unittest discover -s tests -v
jeppa --help
```

开发可使用 `python -m pip install -e .`；提交前保留一次普通安装检查，避免 src 布局漏打包。Windows 可用 `.venv\Scripts\python.exe` / `.venv\Scripts\jeppa.exe` 无需激活，安装和配置示例见 [README](README.md#安装和启动)。测试采用真实上游 parser / reply context，只截获网络发送边界；不连接平台、不验证真实 JWT。测试不应读取个人会话或寻找凭据。新增测试聚焦消费者边界，避免只断言 not-throw 或复制源码。

插件必须在 `async setup(plugin)` 内注册命令、立即登记资源清理；验证加载 / 启动失败回滚、关闭逆序清理、任务错误传播及命令边界。SDK 升级需复核 `event=21 checkRes`、`add_adapter.emit_event`、频道 / 私信 `EventContext.reply` 和异常行为，不能把 `on_ready` 当认证完成。运行时依赖在 `pyproject.toml`，npm 锁文件只管理文档工具；SDK 精确锁定 0.16.0，传递依赖由上游范围解析，并非完整 Python lockfile。

与 CI 一样，检查由以下命令顺序组成：

| 命令                      | 实际范围                                                                            |
| ------------------------- | ----------------------------------------------------------------------------------- |
| `npm run check:markdown`  | Markdown 语法和结构规则                                                             |
| `npm run check:format`    | Markdown、YAML、JSON/JSONC 的解析和一致格式，含隐藏配置目录                         |
| `npm run check:workflows` | action-validator 对 CI 和 Release 工作流进行离线 schema 校验                        |
| `npm run check:secrets`   | Secretlint 推荐规则及 JWT 形态检查，扫描当前文件，含隐藏文件和被 Git 忽略的凭证文件 |

凭证检查排除 `.git`、`node_modules`、`.venv`、Python 缓存 / 构建产物，不排除源码或真实账号配置，不扫描提交历史，不保证发现所有凭证。不要把通过检查当成没有泄漏的证明。文档检查不验证 Oopz；离线 Python 回归也不等于真实平台登录或收发成功。

首次安装需要访问 npm registry；保留工具与 npm 缓存后，检查命令本身不访问网络。需要离线重装时可用 `npm ci --offline --ignore-scripts --no-audit --no-fund`，前提是锁定的所有依赖已经在本机缓存中。依赖升级后必须重新准备缓存；单独复制锁文件不能实现离线安装。

格式问题可以用 `npm run format` 修复，再重新检查。不要通过全局关闭规则或忽略真实凭证来让 CI 通过。新增工作流时须同步扩展 `check:workflows` 的文件列表；当前包含 CI 和 Release。SQLite 数据及轮转日志不是源码，不纳入文本扫描；真实账号配置仍需扫描。

## Pull request

1. 从仓库创建 fork 和工作分支，保持变更范围单一。
2. 更新相关文档及 [变更记录](CHANGELOG.md)，只记录真实完成的行为。
3. 运行本地检查，在 PR 模板中填写实际执行的命令和结果；未执行的检查明确说明。
4. 检查 diff 中没有账号信息、真实 JWT、token、私钥、会话、抓包或私人日志。示例仅使用明确的占位文字，不使用看似真实的密钥。
5. 说明新增第三方内容的来源与许可证。贡献按本仓库 MIT 许可证提供，必须拥有提交内容的授权。

维护者评估后人工合并；没有自动批准、自动合并或发布。CODEOWNERS 用于指明维护者，不意味着单维护者必须取得其他人的批准。

## 维护 CI 与依赖

工作流名称固定为 `CI`，汇总 job id 为 `repository-checks`，检查显示名称为 `Repository checks`，聚合文档与 Python 矩阵结果。修改名称前先调整保护规则，避免悬空的必需检查。修改配置不等于已在 GitHub 跑通；新增矩阵需报告真实运行结果。push / PR 全量运行，不用路径过滤跳过必需检查。

仅用 GitHub 托管 runner；CI 令牌只有 `contents: read`，Release 发布 job 单独申请 `contents: write`，checkout 不保留凭证，不传入仓库 secrets。不使用 `pull_request_target` 执行外来代码。来自 fork 的首次 PR 可能需要维护者在 GitHub 上批准运行，这不等于代码已被批准。

第三方 Actions 使用完整提交 SHA，并在同一行注释已核实版本。更新时核对官方仓库 tag 指向的提交与变更内容。Dependabot 每周检查 Actions、维护工具 npm 依赖和 Python pip 依赖，更新 PR 仍需审查与 CI；不会自动合并。npm 工具只作为开发依赖保存在锁文件中，仓库设置 `private: true` 防止误发 npm 包。Python 版本在 `pyproject.toml`，合并通过检查后在主分支打对应 `v版本号` tag；`Release` 会验证版本、构建并安装 wheel、运行离线回归及创建附带 wheel／源码包的 GitHub Release。可人工触发已有 tag，已发布 tag 不重写；不发布 PyPI，不携带本地配置或自动重启真实账号实例。

当前最新 `markdownlint-cli2@0.24.1` 经 `markdownlint@0.42.1` 使用 `micromark-extension-math@3.1.0`，后者将 KaTeX 限于 `^0.16.0`。为修复 [GHSA-238p-pmpm-9mq7](https://github.com/advisories/GHSA-238p-pmpm-9mq7)，仅对这个父依赖设置 `katex: 0.18.2` 的 npm override，并更新锁文件；不降级检查工具、不忽略告警。已核对 KaTeX 0.17/0.18 的迁移说明：变更涉及内部定义 API 和 CSS 类名，当前工具使用的公开 `renderToString` 接口保留；临时数学 Markdown 的实际 lint 和渲染已通过。上游更新到包含补丁版的原生依赖范围后，应删除这个 override、重新生成锁文件，并复验数学语法检查和 CI。

远端首次 CI 成功后，可将 `Repository checks` 设为默认分支必需检查；单维护者不应强制他人批准，也不要设成维护者无法满足的审批策略。分支保护、安全报告、secret scanning 和 push protection 属于 GitHub 远端设置，不能仅凭这些文件认定已经开启。
