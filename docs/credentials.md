# 本地凭据接入 🔐

Jeppa 是非官方 Oopz 项目。这里复用的是**你自己已登录、已授权账户的客户端会话**，不是官方 Bot API 授权流程。先确认平台条款、目标域规则及自己的操作权限；不要获取他人的会话，不要把账号密码交给脚本。

导入器只读取你明确选择的本地文件、明确指定的标准输入或隐藏的手动输入；**不运行 cURL、不联网、不扫描浏览器资料、Cookie 数据库、配置目录或历史记录**。它不会执行请求，也无法证明令牌签名、账号身份或服务端授权有效。项目网站不接收、处理或保存任何凭据。

## 1. 在浏览器中选择一个请求

1. 在自己的本机浏览器登录 Oopz 网页客户端 `https://web.oopz.cn/`，进入你确实获准访问的目标域。不要在他人电脑、远程演示或正在录屏时操作。
2. 按 `F12` 或 `Ctrl+Shift+I` 打开开发者工具，选择 **Network / 网络**。清空已有记录，关闭 **Preserve log / 保留日志**，只观察随后的一小段正常操作。不要勾选保存敏感数据或主动导出整个会话。
3. 使用 **Fetch/XHR** 过滤，在网页中正常打开目标域的频道或刷新已有列表。只选择一个**正常读取信息的 GET 请求**；不要为了取得凭据发送消息、删改内容或调用未知接口。GET 只是筛选条件，仍须从网页操作确认它是读取请求，不代表所有 GET 都天然无副作用。
4. 打开该请求的 **Headers / 标头 → General / 常规**，确认 **Request Method / 请求方法**是 `GET`，**Request URL / 请求网址**使用 HTTPS，主机必须是以下之一：
   - `gateway.oopz.cn`
   - `gateway1.oopz.cn`
   - `gateway2.oopz.cn`
   - `gateway-web1.oopz.cn`

   只接受默认 HTTPS 端口或显式 `443`。相似拼写、子域后缀、HTTP、带用户名密码的网址或其他端口都不接受。网页入口 `web.oopz.cn` 本身不是凭据请求网关。

5. 展开 **Request Headers / 请求标头**，确认**同一个请求**中完整存在 `Oopz-Device-Id`、`Oopz-Person`、`Oopz-Signature`。看不到时，先正常刷新页面并重新选择读请求，不要混用不同请求补齐字段。若客户端已不再发送这些字段，停止导入并查看项目兼容性说明；不要猜测新字段。
6. 右键这**一个请求**，选择 **Copy / 复制 → Copy as cURL (bash) / 复制为 cURL（bash）**。不要选 PowerShell、Windows cmd、`fetch`、Node.js 或整组请求。复制的内容虽然形似命令，但接下来**只保存为文本，绝不粘贴到终端执行**。

### 三个字段是什么

| 请求头           | TOML 配置项         | 用途与注意事项                                                         |
| ---------------- | ------------------- | ---------------------------------------------------------------------- |
| `Oopz-Device-Id` | `[oopz].device_id`  | 当前已授权客户端会话的设备标识；与本次请求保持一致，不要随机生成。     |
| `Oopz-Person`    | `[oopz].person_uid` | 当前登录者的内部人员 ID；不是手机号、昵称或显示名称。                  |
| `Oopz-Signature` | `[oopz].jwt_token`  | 当前会话令牌，按密码级秘密保护；不要添加 Bearer 前缀或自行解码后改写。 |

名称大小写不敏感。`Oopz-Sign`、`Oopz-Time` 是其他请求签名信息，不是这里的会话令牌；**不要填入 `jwt_token`，也不需要私钥**。导入器不保存 Cookie、Authorization、请求体、其他请求头、网址及查询参数。即便某些值看似只是 ID，也不要公开原始请求。

### 内部域 ID，不是页面显示编号

`--area` 要求 SDK 订阅使用的**内部域 ID（area）**，不是域名、邀请链接、频道 ID、人员 ID，也不是页面上面向用户显示的数字编号。

在刚才这个**目标域的已授权读取请求**中，查看 **Payload / 载荷 → Query String Parameters / 查询字符串参数**是否明确有 `area`；若接口以响应对象提供域信息，则在 **Preview / 预览**查看该目标域对象的内部 `area` 字段。把该值与当前目标域对应关系核对后，逐字使用。不同客户端版本字段可能变化：若只看到名称、显示编号，或无法确认 `area` 对应关系，**先不填域**，不要把整段响应上传求助，也不要猜一个 UUID。

导入器**不从网址、HAR 或响应自动推断域**。每个希望订阅且有权访问的域都要显式传一次 `--area`；不传时配置为 `areas = []`，仅处理私聊，不代表订阅所有域。频道内仍需要相应账号权限。

## 2. 安全保存一个临时文本

文件名采用 `capture.local.txt`，输出采用 `jeppa.local.toml`；仓库忽略 `*.local.*`，但忽略规则**不是访问控制或防泄漏保证**。不要保存在同步网盘、共享目录、公共桌面或网站上传框中；关闭会记录剪贴板的第三方工具和跨设备剪贴板同步。

### Windows：先限制目录权限，再粘贴

在自己的 PowerShell 中运行以下**不含凭据**的准备命令。目录选择当前用户的本地应用数据目录，创建失败或 ACL 设置失败就停止：

```powershell
$privateDir = Join-Path $env:LOCALAPPDATA 'Jeppa-private'
New-Item -ItemType Directory -Path $privateDir -ErrorAction Stop | Out-Null
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
icacls $privateDir /inheritance:r /grant:r "*${sid}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath $privateDir -ErrorAction Stop
    throw '无法限制目录权限，已停止；不要保存凭据。'
}
notepad (Join-Path $privateDir 'capture.local.txt')
```

这是专用新目录示例；若目录已经存在，`New-Item -ErrorAction Stop` 会停止。**不要为重试而删除含现有配置的目录**，请另选一个新的专用目录名。不要在未检查 ACL 的既有目录上反复套用权限命令。

在记事本中粘贴单条 cURL 文本，按 UTF-8 保存，然后关闭编辑器。记事本可能在退出后保留标签页/恢复内容，操作前关闭其会话恢复并在操作后移除该标签页。**不要把文本作为 PowerShell 命令运行。**

导入器为新输出文件设置受保护 DACL，只允许当前 Windows 用户 SID 完全访问；**不是依赖 Windows 上不够充分的 `chmod 600`**。设置失败时先删除空文件并报错，不继续写入凭据。若提示无法清理残留文件，请在本机检查和删除指定输出文件，不要分享内容。管理员、已入侵系统及同一用户下的恶意程序仍不在此保护边界内。

### Linux/macOS

在本地非共享目录先运行 `umask 077`，使用本地文本编辑器新建 `capture.local.txt` 并粘贴。保存后运行 `chmod 600 capture.local.txt`。不要使用会把内容上传云端的编辑器插件。导入器新建输出文件的权限为 `0600`。

## 3. 在本机导入

先按项目安装指南安装 Jeppa。在已经安装 Jeppa 的 Python 环境运行以下命令；其中内部域 ID 是**说明性占位值，必须替换**，不得把真实令牌写进命令行。

Windows PowerShell：

```powershell
python -m jeppa.credentials --input "$privateDir\capture.local.txt" --output "$privateDir\jeppa.local.toml" --area '替换为已核对的内部域ID'
```

Linux/macOS（当前目录为专用私有目录）：

```bash
python -m jeppa.credentials --input capture.local.txt --output jeppa.local.toml --area '替换为已核对的内部域ID'
```

多个域写为 `--area '内部域ID甲' --area '内部域ID乙'`。仅私聊就完全省略 `--area`。默认输出路径是当前目录的 `jeppa.local.toml`；使用绝对路径可避免找错文件。

- `--input`：读取明确指定的 UTF-8 文件，最大 **2 MiB**。不会搜索其他文件。
- `--stdin`：只有显式使用此参数才读取标准输入，同样限制 2 MiB。终端直接粘贴可能回显，故优先使用受限本地文件；不要用含真实凭据的 `echo`、here-string 或 shell 命令历史构造输入。
- `--manual`：隐藏输入模式；没有指定输入方式时也使用此模式。
- `--output`：仅新建，**已有文件、目录、符号链接都不覆写**；没有 `--force`。更新凭据应先生成另一个受限的 `*.local.toml`，核对并停止旧进程后，再由你明确替换旧文件。
- `--area`：仅使用显式提供的内部域 ID；重复值去重，不推断任何订阅范围。

独立入口是 `python -m jeppa.credentials`；主 CLI 提供同参数的 `jeppa import-session`。导入成功只表示本地解析、权限设置和写入完成，**不表示登录验证成功**。随后可先运行 `jeppa check-config --config '配置路径'` 检查配置，再按运行指南启动；运行机器人会联网，凭据导入不会。

### 明确导出的请求头 JSON

也可自行在本地制作以下结构。`url`、显式的 `method = GET` 和三个请求头必须来自同一请求，值全为字符串。下面仅展示占位格式，不能直接登录：

```json
{
  "url": "https://gateway.oopz.cn/替换为所选GET请求路径",
  "method": "GET",
  "headers": {
    "Oopz-Device-Id": "替换为该请求的设备标识",
    "Oopz-Person": "替换为该请求的内部人员ID",
    "Oopz-Signature": "替换为该请求的会话令牌"
  }
}
```

`headers` 也可使用 HAR 风格的 `[{"name": "请求头名称", "value": "请求头值"}]`。单独的请求头对象、没有网址的片段、重复 JSON 字段和重复凭据头会被拒绝。不要用剪辑工具把不同账号或不同设备请求拼成一组。

### 既有 HAR 的严格本地处理

**优先复制单个 cURL 请求，不建议为了本功能导出 HAR。** HAR 往往包含多个账号令牌、Cookie、请求体、响应和无关用户信息。只有你已经有自己明确授权保管的本地 HAR、且能承担其敏感性时，才可通过相同的 `--input` 在本机读取；不得上传到网站、issue、聊天或在线转换工具。

读取范围仅为 `log.entries[].request` 中的网址、方法和请求头，不使用响应。没有三个目标凭据头中任何一个的记录不参与选择；含任一目标头的记录必须是批准网关上的 GET 且三项完整，否则整份拒绝。多条记录必须给出**完全相同的一组三项凭据**；不同账号、设备、令牌、跨域夹带凭据、部分凭据或重复头一律拒绝，不自动挑“第一条”或“最新的一条”。超过 2 MiB 也拒绝。失败时重新复制单个 GET 请求，而不是裁剪大量捕获后猜测。

cURL 解析器只接收一条 `curl`、一个 HTTPS 地址、请求头及少量常见复制选项（Cookie、User-Agent、Referer 仅丢弃，不导入）；拒绝请求体、脚本、多命令、重定向、配置文件选项及不支持的 shell 语法。bash ANSI-C 引号 `$'…'`、PowerShell/cmd 版本不保证兼容，遇到不支持的格式改用明确 JSON 或隐藏手动输入。输入中的命令替换不会执行，但也不要把携带脚本表达式的内容当作真实凭据使用。

## 4. 隐藏手动输入

```bash
python -m jeppa.credentials --manual --output jeppa.local.toml --area '替换为已核对的内部域ID'
```

依次输入：同一已授权 GET 请求的完整 HTTPS 地址、`Oopz-Device-Id`、`Oopz-Person`、`Oopz-Signature`。四项均使用 `getpass` 隐藏输入，地址仅供网关校验，不写入配置。不输入请求头名称，不添加包围引号；粘贴字段值后按回车。三项取自同一请求，不能随机生成。若终端不能保证隐藏输入，工具会停止，不降级回显；请改用本地交互终端。

也可以**完全手工编辑**本地 TOML。先建立仅自己可读写的空文件，再用本地编辑器填写；不要先写秘密再设置权限。Windows 可复用上面的专用私有目录并运行以下准备命令：

```powershell
$config = Join-Path $privateDir 'manual.local.toml'
New-Item -ItemType File -Path $config -ErrorAction Stop | Out-Null
icacls $config /inheritance:r /grant:r "*${sid}:F" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath $config -ErrorAction Stop
    throw '无法限制文件权限，已停止；不要填写凭据。'
}
notepad $config
```

Linux/macOS 先 `umask 077`，以本地编辑器**新建** `manual.local.toml`，并核对权限为 `0600`。填写下列结构；所有说明文字都是占位值，必须替换。TOML 字符串中双引号和反斜线须按 TOML 规则转义，无法确定时使用导入器，避免手改损坏令牌：

```toml
[oopz]
method = "credentials"
device_id = "替换为同一请求的Oopz-Device-Id"
person_uid = "替换为同一请求的Oopz-Person"
jwt_token = "替换为同一请求的Oopz-Signature"

[bot]
areas = ["替换为已核对的内部域ID"]
plugins = ["jeppa.plugins.default:setup"]
```

仅私聊将 `areas` 改为 `[]`。`default` 是现有基础命令的整体插件；此文件只配置一个账户，不会从 HAR 自动添加多账户。不要把真实 `jwt_token` 放进环境诊断截图、参数、测试、示例或版本库。

## 5. 保存、清理与令牌过期

1. 导入后在自己的机器上确认输出文件存在并检查权限。**不要用 `cat`、`type`、截图或终端回显整份配置核对。** `check-config` 只检查配置，不替代服务端登录。
2. 立即删除临时 `capture.local.txt`、既有 HAR 的临时副本和编辑器恢复副本。Windows：`Remove-Item -LiteralPath (Join-Path $privateDir 'capture.local.txt')`；Linux/macOS：`rm -- capture.local.txt`。只删除自己明确创建的文件，不使用递归清理或通配符清理凭据目录。普通删除不承诺对 SSD、备份、云端同步或取证恢复的安全擦除。
3. 清除当前剪贴板。PowerShell：`Set-Clipboard -Value ''`；Windows 再按 `Win+V` 清除剪贴板历史，并关闭跨设备同步。macOS 可运行 `printf '' | pbcopy`；Linux 通过桌面的剪贴板管理器清空当前内容及历史。清理浏览器开发者工具记录，关闭包含捕获内容的编辑器标签页，核对编辑器备份/恢复机制没有保留秘密。
4. 最终配置只保存在本机受限目录。需要停用时停止使用该配置的进程，删除该配置及自己的备份。**删文件、删临时文本或清剪贴板都不会撤销服务端会话**；如果疑似泄漏，使用 Oopz 客户端提供的会话/设备管理、退出登录等撤销方式。无法确认撤销范围时联系平台处理，不要假设改一个本地值就能使旧令牌失效。
5. 工具只在令牌看起来是三段 JWT、且载荷包含可读数值 `exp` 时检查本地到期时间；已过期则拒绝。它不验证签名，不证明未过期令牌有效，也不改写令牌。没有可读 `exp` 的值仍可能被服务端拒绝。系统时钟错误也会影响这个本地检查。
6. 若启动时提示认证失败、会话失效、账户权限变化，先停止机器人，回到自己的客户端确认账号状态，重新复制一个当前的已授权 GET 请求，生成**新文件**再由自己替换旧配置。不要循环尝试旧令牌、添加私钥或改用来历不明的请求。导入错误只显示固定中文原因，不输出原始网址、字段值、请求或异常详情。

向项目反馈问题时，仅提供工具版本、使用的输入类型（cURL/JSON/本地 HAR/手动）和脱敏错误类别；**不要附原始输入、配置、令牌、Cookie、私钥、HAR 或包含这些内容的截图。**
