# newapi.ai 多账号自动签到

用于公益站多账号每日签到。  

Affs:
- [AnyRouter](https://anyrouter.top/register?aff=wJrb)
- [WONG](https://wzw.pp.ua/register?aff=N6Q9)
- [薄荷 API](https://x666.me/register?aff=dgzt)
- [Huan API](https://ai.huan666.de/register?aff=qEnU)
- [KFC API](https://kfc-api.sxxe.net/register?aff=xPnf)
- [HotaruApi](https://hotaruapi.com/register?aff=q6xq)
- [Elysiver](https://elysiver.h-e.top/register?aff=5JsA)

其它使用 `newapi.ai` 功能相似, 可自定义环境变量 `PROVIDERS` 支持或 `PR` 到仓库。

## 功能特性

- ✅ 单个/多账号自动签到
- ✅ 多种机器人通知（可选）
- ✅ linux.do 登录认证
- ✅ github 登录认证 (with OTP)
- ✅ 站点账号密码登录认证
- ✅ Cloudflare bypass

## 使用方法

### 1. Fork 本仓库

点击右上角的 "Fork" 按钮，将本仓库 fork 到你的账户。

### 2. 设置 GitHub Environment Secret

1. 在你 fork 的仓库中，点击 "Settings" 选项卡
2. 在左侧菜单中找到 "Environments" -> "New environment"
3. 新建一个名为 `production` 的环境
4. 点击新建的 `production` 环境进入环境配置页
5. 点击 "Add environment secret" 创建 secret：
   - Name: `ACCOUNTS`
   - Value: 你的多账号配置数据

#### 2.0 快速生成 JSON（推荐）

仓库根目录提供了一个纯 HTML 生成器：`secret-json-generator.html`。

在线打开：[Secret JSON Generator](https://acehubert.github.io/newapi-ai-check-in/secret-json-generator.html)

使用方式：

1. 在本地直接双击打开 `secret-json-generator.html`（或拖进浏览器）
2. 选择要生成的 secret（如 `ACCOUNTS`、`PROXY`、`PROVIDERS`）
3. 按页面提示填入参数并点击「产出 JSON」
4. 复制结果，粘贴到 GitHub -> Settings -> Environments -> `production` -> Environment secrets 的 Value

说明：
- 生成器只在浏览器本地运行，不会上传你的账号或密码。
- `ACCOUNTS_LINUX_DO` 与 `ACCOUNTS_GITHUB` 使用相同 JSON 数组格式（`[{"username":"...","password":"..."}]`）。

#### 2.1 全局 OAuth 账号配置（可选）

可以配置全局的 Linux.do 和 GitHub 账号，供多个 provider 共享使用。

##### 2.1.1 ACCOUNTS_LINUX_DO

在仓库的 Settings -> Environments -> production -> Environment secrets 中添加：
   - Name: `ACCOUNTS_LINUX_DO`
   - Value: Linux.do 账号列表

```json
[
  {"username": "用户名1", "password": "密码1"},
  {"username": "用户名2", "password": "密码2"}
]
```

##### 2.1.2 ACCOUNTS_GITHUB

在仓库的 Settings -> Environments -> production -> Environment secrets 中添加：
   - Name: `ACCOUNTS_GITHUB`
   - Value: GitHub 账号列表

```json
[
  {"username": "用户名1", "password": "密码1"},
  {"username": "用户名2", "password": "密码2"}
]
```

### 3 多账号配置格式
> 如果未提供 `name` 字段，会使用 `{provider.name} 1`、`{provider.name} 2` 等默认名称。  
> 配置中 `cookies`、`github`、`linux.do`、`site` 必须至少配置 1 个。  
> 使用 `cookies` 设置时，`api_user` 字段必填。  

#### 3.1 登录配置格式

`github` 和 `linux.do` 字段支持以下三种配置格式：

**1. bool 类型 - 使用全局账号**
```json
{"provider": "anyrouter", "linux.do": true}
```
当设置为 `true` 时，使用 `LINUX_DO_ACCOUNTS` 或 `GITHUB_ACCOUNTS` 中配置的所有账号。

**2. dict 类型 - 单个账号**
```json
{"provider": "anyrouter", "linux.do": {"username": "用户名", "password": "密码"}}
```

**3. array 类型 - 多个账号**
```json
{"provider": "anyrouter", "linux.do": [
  {"username": "用户名1", "password": "密码1"},
  {"username": "用户名2", "password": "密码2"}
]}
```

`site` 字段支持以下两种配置格式：

**1. dict 类型 - 单个站点账号**
```json
{"provider": "anyrouter", "site": {"username": "站点用户名", "password": "站点密码"}}
```

**2. array 类型 - 多个站点账号**
```json
{"provider": "anyrouter", "site": [
  {"username": "用户名1", "password": "密码1"},
  {"username": "用户名2", "password": "密码2", "mode": "browser"}
]}
```

#### 3.2 完整示例

```json
[
    {
      "name": "我的账号",
      "cookies": {
        "session": "account1_session_value"
      },
      "system_access_token": "sk-xxxxxxxxxxxxxxxx"
      "api_user": "account1_api_user_id",
      "github": {
        "username": "myuser",
        "password": "mypass"
      },
      "linux.do": {
        "username": "myuser",
        "password": "mypass"
      },
      "site": {
        "username": "site-user",
        "password": "site-pass"
      },
      // --- 额外的配置说明 ---
      // 当前账号使用代理
      "proxy": {
        "server": "http://username:password@proxy.example.com:8080"
      },
      //provider: x666 可选配置（自动通过 linux.do 登录获取）
      // "access_token": "来自 https://qd.x666.me/",  // 已废弃，会自动获取
      "get_cdk_cookies": {
        // provider: runawaytime 必须配置
        "session": "来自 https://fuli.hxi.me/",
        // provider: b4u 必须配置
        "__Secure-authjs.session-token": "来自 https://tw.b4u.qzz.io/"
      }
    },
    {
      "name": "使用全局账号",
      "provider": "anyrouter",
      "linux.do": true,
      "github": true
    },
    {
      "name": "多个 OAuth 账号",
      "provider": "anyrouter",
      "linux.do": [
        {"username": "user1", "password": "pass1"},
        {"username": "user2", "password": "pass2"}
      ]
    }
  ]
```

#### 3.3 字段说明：

- `name` (可选)：自定义账号显示名称，用于通知和日志中标识账号
- `provider` (可选)：供应商，内置 `anyrouter`、`wong`、`huan666`、`x666`、`kfc`、`elysiver`、`hotaru`、`tabitoken`、`seekai`、`justwoker`、`beizhi`，默认使用 `anyrouter`
- `proxy` (可选)：单个账号代理配置，支持 `http`、`socks5` 代理
- `cookies`(可选)：用于身份验证的 cookies 数据
- `system_access_token`(可选)：系统访问令牌，通过 `Authorization: Bearer <token>` 方式认证签到
- `api_user`(旧版站点的 cookies 或 system_access_token 设置时必需)：用于请求头的 new-api-user 参数；`tabitoken` 等新版会话站点不需要
- `linux.do`(可选)：用于登录身份验证，支持三种格式：
  - `true`：使用 `LINUX_DO_ACCOUNTS` 中的全局账号
  - `{"username": "xxx", "password": "xxx"}`：单个账号
  - `[{"username": "xxx", "password": "xxx"}, ...]`：多个账号
- `github`(可选)：用于登录身份验证，支持三种格式：
  - `true`：使用 `GITHUB_ACCOUNTS` 中的全局账号
  - `{"username": "xxx", "password": "xxx"}`：单个账号
  - `[{"username": "xxx", "password": "xxx"}, ...]`：多个账号
- `site`(可选)：用于站点账号密码登录，支持两种格式：
  - `{"username": "xxx", "password": "xxx"}`：单个账号
  - `[{"username": "xxx", "password": "xxx"}, ...]`：多个账号
  - `mode` 可选值：`auto`、`api`、`browser`，默认 `auto`

#### 3.3.1 `site` 登录说明

- `mode=api`：直接请求站点登录接口：`/api/user/login?turnstile=`。
- `mode=browser`：使用内置浏览器打开登录页，切换到“邮箱或用户名登录”，填写账号密码并提交。
- `mode=auto`：先尝试 `api` 登录，失败后自动回退到 `browser` 登录。
- 登录成功后会自动读取响应或浏览器中的 cookie，并使用响应中的 `data.id` 或页面本地存储/用户信息接口中的用户 ID 作为 `api_user`。
- 因此使用 `site` 登录时，不需要手动填写 `cookies.session` 和 `api_user`。
- 如果目标站点启用了额外验证码、前端校验或防护，优先使用 `mode=auto` 或 `mode=browser`。

#### 3.4 供应商配置：

在仓库的 Settings -> Environments -> production -> Environment secrets 中添加：
   - Name: `PROVIDERS`
   - Value: 供应商
   - 说明: 自定义 provider 默认不会自动添加到账号中；只有配置 `"auto_add": true`，且账号配置中没有使用该 provider 时，才会自动添加执行（详见 [PROVIDERS.json](./PROVIDERS.json)）。


#### 3.5 代理配置
> 应用到所有的账号，如果单个账号需要使用代理，请在单个账号配置中添加 `proxy` 字段。  
> 打开 [webshare](https://dashboard.webshare.io/) 注册账号，获取免费代理

在仓库的 Settings -> Environments -> production -> Environment secrets 中添加：
   - Name: `PROXY`
   - Value: 代理服务器地址


```bash
{
  "server": "http://username:password@proxy.example.com:8080"
}

或者

{
  "server": "http://proxy.example.com:8080",
  "username": "username",
  "password": "password"
}
```


#### 3.6 如何获取 cookies 与 api_user 的值。

通过 F12 工具，切到 Application 面板，Cookies -> session 的值，最好重新登录下，但有可能提前失效，失效后报 401 错误，到时请再重新获取。

![获取 cookies](./assets/request-cookie-session.png)

通过 F12 工具，切到 Application 面板，面板，Local storage -> user 对象中的 id 字段。

![获取 api_user](./assets/request-api-user.png)

#### 3.7 如何获取 System Access Token

登录 newapi 后台，进入 **个人设置 -> 账户管理 -> 安全设置** 页面，点击 **生成令牌** 复制生成的令牌值。

![获取 System Access Token](./assets/system-access-token.png)

获取到令牌后，在账号配置中设置 `system_access_token` 和 `api_user` 字段即可：

```json
{
  "name": "使用系统访问令牌",
  "provider": "x666",
  "api_user": "12345",
  "system_access_token": "sk-xxxxxxxxxxxxxxxx"
}
```

#### 3.8 `GitHub` 在新设备上登录会有两次验证

通过打印日志中链接打开并输入验证码。

![输入 OTP](./assets/github-otp.png)

#### 3.9 TaBi Token 自动签到

TaBi Token 使用新版 New API Bearer 鉴权，并由 Cloudflare/Turnstile 保护。内置 `tabitoken`
provider 会执行 `GET /api/user/checkin?month=YYYY-MM` 幂等检查、必要时调用
`POST /api/user/checkin`，最后再次查询状态验证结果。

推荐在 TaBi Token 的 **个人资料 -> 安全 -> Access Token** 中生成长期访问令牌。在
GitHub 仓库的 **Settings -> Environments -> production -> Environment secrets** 新建
`TABITOKEN_ACCESS_TOKEN`，值填写该令牌。专用 workflow 会自动生成等价于以下内容的账号配置：

```json
[
  {
    "name": "TaBi Token",
    "provider": "tabitoken",
    "system_access_token": "此处填写 Access Token"
  }
]
```

新版会话站点使用 `Authorization: Bearer <token>`，上述配置不需要 `api_user`。令牌只放在
`TABITOKEN_ACCESS_TOKEN` secret，不写入仓库文件。

如果已经配置全局 `ACCOUNTS_GITHUB`，`tabitoken` 会在没有同名账号时自动复用该 GitHub
账号完成 OAuth 登录、轮换 `new_api_refresh` cookie 并取得短期 Bearer token，无需再改
`ACCOUNTS`。二者同时存在时，专用 workflow 优先使用 Access Token。

`.github/workflows/tabitoken.yml` 每天北京时间 **09:17** 自动运行，也可以在 Actions 中打开
**TaBi Token 自动签到** 后点击 **Run workflow** 手动验证。流程会先查当天状态，避免重复签到；
接口要求 Turnstile 时会获取验证令牌并重试一次。

十二账号模式使用 `TABITOKEN_ACCESS_TOKEN`、`TABITOKEN_ACCESS_TOKEN_2` 至
`TABITOKEN_ACCESS_TOKEN_12` 十二个 Environment Secrets。workflow 会生成十二个独立账号并依次
签到；它会拒绝空白或重复令牌，且只有程序确认十二个账号全部成功时任务才成功。

该专用 workflow 支持 VMess：把只含目标节点、监听 `mixed-port: 7890` 的 Mihomo 配置保存为
GitHub Environment Secret `TABITOKEN_CLASH_CONFIG`。运行时会下载固定版本并校验 SHA-256，
验证配置和代理出口后，只为本次签到设置 `PROXY={"server":"http://127.0.0.1:7890"}`；
节点服务器、UUID、SNI 与 WebSocket 路径不会写入仓库或 Actions 日志。

#### 3.10 SeekAI 自动签到

内置 `seekai` provider 对接 `https://seekai.cc` 的新版 New API 会话鉴权和
`/api/user/checkin` 接口。`.github/workflows/seekai.yml` 每天北京时间 **09:27** 运行，
使用 SeekAI **个人资料 -> 安全 -> 访问令牌** 中生成的长期令牌。将该令牌保存为 production
Environment Secret `SEEKAI_ACCESS_TOKEN`；workflow 会生成仅包含 SeekAI 账号的临时配置，
令牌只在 Actions 内存与掩码环境中使用，不写入仓库文件或浏览器状态缓存。

十二账号模式使用 `SEEKAI_ACCESS_TOKEN`、`SEEKAI_ACCESS_TOKEN_2` 至
`SEEKAI_ACCESS_TOKEN_12` 十二个 production Environment Secrets。workflow 会拒绝空白或重复令牌，
只有程序确认十二个账号全部成功时任务才成功；十二个账号顺序复用同一个已验证的 VMess 出口。

SeekAI workflow 直接复用 `TABITOKEN_CLASH_CONFIG` 中已经配置的 VMess 节点。流程会先校验
Mihomo 下载文件和代理配置，再确认代理出口与直连出口不同，最后才通过本地
`http://127.0.0.1:7890` 执行签到。程序先查询当天签到状态，已经签到时直接成功退出，
避免重复领取。

#### 3.11 JustWoker 自动签到

内置 `justwoker` provider 对接 `https://api.justwoker.icu` 的新版 New API 会话鉴权和
`/api/user/checkin` 接口。`.github/workflows/justwoker.yml` 每天北京时间 **09:37** 运行，
使用个人资料页 **安全 -> 访问令牌** 中生成的长期令牌。将令牌保存为 production
Environment Secret `JUSTWOKER_ACCESS_TOKEN`；令牌只在 Actions 内存与掩码环境中使用，
不会写入仓库文件。

六账号模式依次使用 `JUSTWOKER_ACCESS_TOKEN`、`JUSTWOKER_ACCESS_TOKEN_2` 至
`JUSTWOKER_ACCESS_TOKEN_6` 六个 production Environment Secrets。workflow 会拒绝缺失、空白、
含首尾空格或重复的令牌，
只有程序确认六个账号全部成功时任务才成功；六个账号顺序复用同一个已验证的 VMess 出口。

JustWoker workflow 复用 `TABITOKEN_CLASH_CONFIG` 中的 VMess 节点。流程会校验固定版本
Mihomo 的 SHA-256、代理配置和出口变化，再通过本地 `http://127.0.0.1:7890` 查询当日状态并
执行签到；当天已经签到时直接成功退出，避免重复领取。

#### 3.12 北栀自动签到

内置 `beizhi` provider 对接 `https://beizhi.sylu.cc` 的新版 New API Bearer 鉴权和
`/api/user/checkin` 接口。`.github/workflows/beizhi.yml` 每天北京时间 **09:47** 运行，
使用个人资料页 **安全 -> 访问令牌** 中生成的长期令牌。令牌只保存在 production
Environment Secrets 中，不写入仓库文件。

六账号模式依次使用 `BEIZHI_ACCESS_TOKEN`、`BEIZHI_ACCESS_TOKEN_2` 至
`BEIZHI_ACCESS_TOKEN_6` 六个 production Environment Secrets。workflow 会拒绝缺失、空白、
含首尾空格或重复的令牌，只有程序确认六个账号全部成功时任务才成功。

北栀 workflow 复用 `TABITOKEN_CLASH_CONFIG` 中的 VMess 节点。流程校验 Mihomo 下载文件、
代理配置和出口变化后，通过本地 `http://127.0.0.1:7890` 查询当日状态并执行签到；已签到账号
直接成功退出，六个账号顺序复用同一个代理出口。

#### 3.13 NOFX 自动签到

NOFX 使用邮箱 Magic Link 登录，没有可长期复用的密码或公开签到 Token。工作流通过
Playwright 复用你手动登录后导出的会话状态，每天只打开一次任务页并点击可见的签到按钮。

首次配置步骤（10 个账号）：

1. 本地分别运行 `uv run python nofx_checkin.py --capture-state nofx-state-1.json` 至
   `nofx-state-10.json`（第一个文件也可命名为 `nofx-state.json`）。
2. 在打开的浏览器中输入邮箱并完成 Magic Link 登录，回到终端按回车保存状态。
3. 将 10 个文件分别编码为 base64，写入 `production` Environment Secrets
   `NOFX_STORAGE_STATE_B64_1` 至 `NOFX_STORAGE_STATE_B64_10`：
   `[Convert]::ToBase64String([IO.File]::ReadAllBytes('.\\nofx-state-1.json'))`（PowerShell）。
4. 启用 **NOFX 自动签到** workflow，并先手动运行一次验证。

会话状态等同于登录凭据，只能放在 GitHub Environment Secret，禁止提交到仓库、上传
Artifact 或输出到日志。会话过期后工作流会失败并提示重新导出；不要通过高频请求或绕过
站点验证来维持会话。

### 4. 启用 GitHub Actions

1. 在你的仓库中，点击 "Actions" 选项卡
2. 如果提示启用 Actions，请点击启用
3. 找到 "newapi.ai 自动签到" workflow
4. 点击 "Enable workflow"

### 5. 测试运行

你可以手动触发一次签到来测试：

1. 在 "Actions" 选项卡中，点击 "newapi.ai 自动签到"
2. 点击 "Run workflow" 按钮
3. 确认运行

![运行结果](./assets/check-in.png)

## 执行时间

- 脚本每 8 小时执行一次（1. action 无法准确触发，基本延时 1~1.5h；2. 目前观测到 anyrouter.top 的签到是每 24h 而不是零点就可签到）
- 你也可以随时手动触发签到

## 注意事项

- 可以在 Actions 页面查看详细的运行日志
- 支持部分账号失败，只要有账号成功签到，整个任务就不会失败
- `GitHub` 新设备 OTP 验证，注意日志中的链接或配置了通知注意接收的链接，访问链接进行输入验证码

## 开启通知

脚本支持多种通知方式，可以通过配置以下环境变量开启，如果 `webhook` 有要求安全设置，例如钉钉，可以在新建机器人时选择自定义关键词，填写 `newapi.ai`。

### 邮箱通知

- `EMAIL_USER`: 发件人邮箱地址
- `EMAIL_PASS`: 发件人邮箱密码/授权码
- `CUSTOM_SMTP_SERVER`: 自定义发件人 SMTP 服务器(可选)
- `EMAIL_TO`: 收件人邮箱地址

### 钉钉机器人

- `DINGDING_WEBHOOK`: 钉钉机器人的 Webhook 地址

### 飞书机器人

- `FEISHU_WEBHOOK`: 飞书机器人的 Webhook 地址

### 企业微信机器人

- `WEIXIN_WEBHOOK`: 企业微信机器人的 Webhook 地址

### PushPlus 推送

- `PUSHPLUS_TOKEN`: PushPlus 的 Token

### Server 酱

- `SERVERPUSHKEY`: Server 酱的 SendKey

### Telegram 机器人

- `TELEGRAM_BOT_TOKEN`: Telegram 机器人的 Token
- `TELEGRAM_CHAT_ID`: 接收消息的 Chat ID

## 防止Action因长时间无活动而自动禁止
- `ACTIONS_TRIGGER_PAT`: 在Github Settings -> Developer Settings -> Personal access tokens -> Tokens(classic) 中新建一个包含repo和workflow的令牌

配置步骤：

1. 在仓库的 Settings -> Environments -> production -> Environment secrets 中添加上述环境变量
2. 每个通知方式都是独立的，可以只配置你需要的推送方式
3. 如果某个通知方式配置不正确或未配置，脚本会自动跳过该通知方式

## 故障排除

如果签到失败，请检查：

1. 账号配置格式是否正确
2. 网站是否更改了签到接口
3. 查看 Actions 运行日志获取详细错误信息

## 本地开发环境设置

如果你需要在本地测试或开发，请按照以下步骤设置：

```bash
# 安装所有依赖
uv sync --dev

# 安装 Camoufox 浏览器
python3 -m camoufox fetch

# 按 .env.example 创建 .env
uv run main.py
```

## 测试

```bash
uv sync --dev

# 安装 Camoufox 浏览器
python3 -m camoufox fetch

# 运行测试
uv run pytest tests/
```

## 免责声明

本脚本仅用于学习和研究目的，使用前请确保遵守相关网站的使用条款.
