# 变更记录

本文件只记录实际完成的仓库变更，不把设想当作已支持功能。

## 0.2.0 — 2026-11-13

### 新增

- 命令级用户／频道白名单、逐字段策略继承、按命令／会话／用户隔离的冷却；执行前检查，帮助过滤不可用命令，受限命令默认拒绝。
- 实用插件 `/roll`、`/choose`、域内 FAQ 查询及白名单操作员维护，输入与回复大小有限制，不预填真实群服信息。
- `/status` 展示运行时长、插件、命令计数和三个独立连接状态；命令日志只记录名称、结果、耗时及异常类别。
- SQLite schema v1、插件／域／频道／用户隔离的 JSON 存储与私信对方隔离，不自动保存聊天记录，拒绝未来未知 schema。
- 独立 `start/stop/status/logs`、跨进程单实例文件锁、带本机令牌的回环控制及优雅关闭、固定脱敏诊断和轮转日志；不安装开机自启或自动重启服务。
- 包版本统一为 0.2.0，增加 `--version` 和核对 tag 的 GitHub Release 构建／离线回归／wheel 与源码包发布流程；不发布 PyPI。

### 此前基础

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
- 授权指定域真实联调核实内部域／频道、SDK HTTP 200、官方客户端可见消息与真实 `event=9` 回流；后续用户确认 `/ping` 得到 `pong`，恢复后的官方页面可见 `/ping` 和 `/help` 回复。未取得显式鉴权成功帧；历史成功不自动证明新版本所有功能已在线验收。

本版本可本地安装，GitHub Release 工作流提供分发包，不自动部署账号实例或发布 PyPI。指定域的受控实测不代表其他账号、域或平台协议持续可用。远端设置属于 Jeppa 主仓库，不会随 fork 自动复制。
