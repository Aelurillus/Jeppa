# Jeppa

Jeppa 的目标是一个面向 Oopz 的机器人开发框架，定位类似 Koishi / Discord Bot 生态中的框架。本项目是**非官方项目**，不隶属于 Oopz，也未获其背书。当前仓库只包含项目说明、贡献与安全规范，以及用于检查这些文件和 GitHub Actions 配置的 CI；尚未提供机器人运行时、SDK、登录流程、消息收发接口或可部署程序。

## 当前状态

- 项目定位：为后续 Oopz 机器人开发整理公开的工程基础。
- 当前内容：Markdown 文档、仓库协作配置和 GitHub Actions 检查。
- 当前不支持：任何可运行的机器人、Oopz 客户端、官方 API 兼容层或生产部署。
- 发布状态：当前没有可发布产物，也没有启用发布或部署流程。

截至当前公开资料调研，尚未确认可使用的 Oopz 公共 Bot API；普通账号能够登录官方 Web 端，并不表示存在获授权的 Bot 接口。社区 SDK 的登录与收发尚未在本项目中实测。

[Oopz 用户服务协议](https://help.oopz.cn/agreement/service) §3.6 对反向工程、未授权第三方工具接入和登录等行为设有限制（除非法律允许或取得书面许可）。本仓库的 MIT 许可、第三方 SDK 的开源许可，均不代表 Oopz 平台授权。未经确认授权与适用条款，不应把普通账号接入能力视为可公开部署的机器人能力。条款可能变化，实际接入前应重新核对；本段不代替法律意见。

## 本地验证

Node.js 只用于文档和仓库维护工具，不决定未来机器人的运行时技术栈。建议使用 [.node-version](.node-version) 中的 Node.js 26.13.0；当前工具最低需要 Node.js 22。依赖精确版本和完整性信息记录在 `package-lock.json` 中：

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm run check
```

`npm run check` 依次运行 Markdown lint、Prettier 的 Markdown/YAML/JSON 格式与解析检查、action-validator 的工作流 schema 校验，以及 Secretlint 推荐规则和 JWT 形态检查。扫描包含隐藏配置和被忽略的凭证文件，排除 Git 内部数据和已安装依赖；不扫描 Git 历史，不能保证发现所有秘密，也不验证 Oopz 功能。具体命令、离线缓存前提及维护方式见 [贡献指南](CONTRIBUTING.md#本地检查)。

## GitHub 自动化

工作流 `CI` 对 push、pull request 和手动触发运行，唯一检查名为 `Repository checks`。它只读仓库、不使用 Oopz 账号或仓库 secrets；第三方 Actions 锁定完整提交 SHA，Dependabot 每周为 Actions 与 npm 维护工具创建更新 PR，仍需人工审查。首次 fork PR 的运行可能需要维护者批准。

当前只启用 **CI**，没有 **CD**：不发布包、不创建版本 tag/release、不部署机器人。检查通过仅代表当前文件通过规则，不表示接入合法或机器人可用。

主仓库已在 GitHub 托管 Ubuntu runner 上完成[首次 CI 检查](https://github.com/Aelurillus/Jeppa/actions/runs/43362302589)。已开启 [Issues](https://github.com/Aelurillus/Jeppa/issues)、[Discussions](https://github.com/Aelurillus/Jeppa/discussions)、私下漏洞报告、Dependabot alerts/安全更新，以及 secret scanning/push protection。`main` 要求通过绑定 GitHub Actions 的 `Repository checks` 并经 PR 合并，不要求他人批准或 CODEOWNER 审批；管理员可绕过，禁止强推和删除分支。上述为主仓库已核实的设置，不会随 fork 自动复制，也不保证以后保持不变；以 GitHub 实际设置为准。

## 参与贡献

请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。Bug 和功能建议请使用 GitHub 的 issue 表单；提交变更前运行上面的本地检查。请不要在 issue、pull request、日志或示例中提交 JWT、token、私钥、账号配置或其他凭证。

## 安全问题

安全问题请阅读 [SECURITY.md](SECURITY.md) 中的私下报告要求。请勿通过公开 issue 或 pull request 披露凭证或可被利用的细节。

## 许可证

本项目按 [MIT 许可证](LICENSE) 发布，版权归 Aelurillus，2026。该许可证不改变 Oopz 平台自身的条款或授权要求。
