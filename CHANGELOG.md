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
- 独立 Python 3.11+ 安装包与 `jeppa` CLI，使用精确版本 `oopz-sdk==0.16.0`，与 npm 文档维护工具分离。
- 本地 TOML / 环境配置、离线安全诊断；真实 SDK 消息规范化与频道 / 私信回复边界，默认忽略自身消息。
- 显式命令插件、`/ping` / `/help`、实际外部 greet 示例；加载失败回滚命令和资源，退出逆序清理，后台 / 命令错误可见失败。
- 区分 transport-open、event 21 显式鉴权确认、域订阅已发送；不伪称上游 on_ready 或订阅发送就是线上就绪。
- 标准库回归测试和 Python 安装检查 CI 配置，Windows / Ubuntu 与 Python 3.11 / 3.13 矩阵、稳定 Repository checks 汇总，pip Dependabot；配置变更不等于远端检查已运行通过。
- 修复域订阅被可缺席的 `event=21 checkRes=true` 阻塞：按 SDK 既有时序在 transport-open / auth-sent 后仅发送显式配置域，重连重发；显式鉴权确认独立记录且不会重复订阅，保留自身消息过滤。
- 授权指定域真实联调核实内部域 / 频道映射、SDK HTTP 200、受控 SDK 测试消息在官方客户端可见；正式修复重装后的 CLI 路径收到目标频道测试消息 `event=9`，自身发送者被 SDK 过滤。未收到显式鉴权成功确认或另一账号 `/ping`，不宣称命令闭环已验证。

当前有可本地安装的文字运行时，但尚未发布 PyPI 软件包或启用部署流程。本条目不是版本发布；指定域的受控实测不代表其他账号、域或平台协议持续可用。远端设置属于 Jeppa 主仓库，不会随 fork 自动复制。
