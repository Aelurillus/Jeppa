# 贡献指南

感谢参与 Jeppa。当前只接受与实际仓库内容相符的文档、工程配置和设计讨论；仓库没有已实现的机器人功能。新增运行时或平台适配前，请先通过功能建议说明需求、合法授权来源和可验证的行为，不要将第三方 SDK 的存在当成平台授权。

## 提交问题

- 使用 bug 表单报告文档或检查错误，附上系统、工具版本、脱敏复现步骤、预期和实际结果。
- 使用功能表单说明使用场景、范围和验收方式，区分设想与已验证事实。
- 安全漏洞、账号资料和凭证不得公开提交；请遵循 [安全政策](SECURITY.md)。

## 本地检查

Node.js 只用于仓库维护工具，不是未来机器人运行时的技术栈决定。建议使用 [.node-version](.node-version) 中的版本；当前工具最低需要 Node.js 22。

在仓库根目录运行：

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm run check
```

与 CI 一样，检查由以下命令顺序组成：

| 命令                      | 实际范围                                                                            |
| ------------------------- | ----------------------------------------------------------------------------------- |
| `npm run check:markdown`  | Markdown 语法和结构规则                                                             |
| `npm run check:format`    | Markdown、YAML、JSON/JSONC 的解析和一致格式，含隐藏配置目录                         |
| `npm run check:workflows` | action-validator 对当前 CI 工作流进行离线 schema 校验                               |
| `npm run check:secrets`   | Secretlint 推荐规则及 JWT 形态检查，扫描当前文件，含隐藏文件和被 Git 忽略的凭证文件 |

凭证检查排除 `.git` 内部数据和 `node_modules`，不扫描提交历史，不保证发现所有凭证。不要把通过检查当成没有泄漏的证明。其他检查也不验证 Oopz 登录、接口兼容性或运行时行为。

首次安装需要访问 npm registry；保留工具与 npm 缓存后，检查命令本身不访问网络。需要离线重装时可用 `npm ci --offline --ignore-scripts --no-audit --no-fund`，前提是锁定的所有依赖已经在本机缓存中。依赖升级后必须重新准备缓存；单独复制锁文件不能实现离线安装。

格式问题可以用 `npm run format` 修复，再重新检查。不要通过全局关闭规则或忽略真实凭证来让 CI 通过。新增工作流时须同步扩展 `check:workflows` 的文件列表；当前只有一个工作流。

## Pull request

1. 从仓库创建 fork 和工作分支，保持变更范围单一。
2. 更新相关文档及 [变更记录](CHANGELOG.md)，只记录真实完成的行为。
3. 运行本地检查，在 PR 模板中填写实际执行的命令和结果；未执行的检查明确说明。
4. 检查 diff 中没有账号信息、真实 JWT、token、私钥、会话、抓包或私人日志。示例仅使用明确的占位文字，不使用看似真实的密钥。
5. 说明新增第三方内容的来源与许可证。贡献按本仓库 MIT 许可证提供，必须拥有提交内容的授权。

维护者评估后人工合并；没有自动批准、自动合并或发布。CODEOWNERS 用于指明维护者，不意味着单维护者必须取得其他人的批准。

## 维护 CI 与依赖

工作流名称固定为 `CI`，job id 为 `repository-checks`，检查显示名称为 `Repository checks`。修改名称前先调整保护规则，避免悬空的必需检查。工作流对 push 和 pull request 全量运行，不用路径过滤跳过必需检查，也支持手动触发。

仅用 GitHub 托管 runner；令牌只有 `contents: read`，checkout 不保留凭证，不传入仓库 secrets。不使用 `pull_request_target` 执行外来代码。来自 fork 的首次 PR 可能需要维护者在 GitHub 上批准运行，这不等于代码已被批准。

第三方 Actions 使用完整提交 SHA，并在同一行注释已核实版本。更新时核对官方仓库 tag 指向的提交与变更内容。Dependabot 每周检查 Actions 和维护工具的 npm 依赖，更新 PR 仍需审查与 CI；不会自动合并。工具只作为开发依赖保存在锁文件中，仓库设置 `private: true` 防止误发 npm 包。

远端首次 CI 成功后，可将 `Repository checks` 设为默认分支必需检查；单维护者不应强制他人批准，也不要设成维护者无法满足的审批策略。分支保护、安全报告、secret scanning 和 push protection 属于 GitHub 远端设置，不能仅凭这些文件认定已经开启。
