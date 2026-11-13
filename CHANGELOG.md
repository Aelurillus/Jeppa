# 变更记录

本文件只记录实际完成的仓库变更，不把设想当作已支持功能。

## 未发布

### 新增

- 中文项目说明、非官方声明和 Oopz 接入协议风险提示。
- MIT 许可证、贡献指南、安全报告政策及敏感文件忽略规则。
- Issue 表单、PR 模板、CODEOWNERS 和 Dependabot 配置。
- 针对 Markdown、YAML/JSON、Actions 工作流和当前文件凭证模式的 CI 配置及可复现的本地检查命令。
- 主仓库首次 GitHub Actions CI 通过，开启 Issues、Discussions、私下漏洞报告、Dependabot alerts/安全更新、secret scanning/push protection，并设置单维护者可用的 `main` PR 与必需检查保护。
- 修复维护工具间接依赖 KaTeX 的 GHSA-238p-pmpm-9mq7：为 `micromark-extension-math@3.1.0` 限定覆盖到补丁版 `0.18.2`，同步锁文件并验证数学 Markdown lint 与渲染。

当前没有机器人实现、可发布软件包或部署流程；此条目不是版本发布。远端设置属于 Jeppa 主仓库，不会随 fork 自动复制。
