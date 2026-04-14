# Mirror Hub — 远程浏览器管理平台

通过 Web 界面远程管理多个 Chrome 浏览器实例。支持实时截图、鼠标键盘操作、Cookie 持久化、自动登录插件、空闲自动回收。

## 功能

- **远程操作**：实时截图流 + 鼠标点击/拖拽/滚轮 + 键盘输入 + 触摸屏支持
- **多 Profile 管理**：每个 Chrome 独立 profile，互不干扰
- **Cookie 持久化**：Chrome 重启后自动恢复登录态（通过 CDP WebSocket）
- **自动登录插件**：可插拔的平台登录插件系统
- **空闲回收**：无访问 10 分钟自动关闭 Chrome，节省资源
- **密码保护**：Cookie 鉴权，7 天有效
- **缩放/平移**：支持放大查看细节，适合手机操作
- **一键重启**：带状态反馈的浏览器重启按钮

## 快速开始

### 1. 环境准备（仅首次）

```bash
# Python 依赖
pip install playwright websocket-client
playwright install chromium

# Linux 需要 Xvfb（Mac 不需要）
apt install xvfb
Xvfb :99 -screen 0 2560x1440x24 -maxclients 1024 &
export DISPLAY=:99

# 推荐：提高系统限制（50+ Chrome 实例时需要）
sysctl -w fs.inotify.max_user_instances=8192
sysctl -w fs.inotify.max_user_watches=1048576
```

### 2. 克隆项目

```bash
git clone https://github.com/falconlux/mirror-hub.git
cd mirror-hub
```

### 3. 配置

```bash
cp config.example.json config.json
```

编辑 `config.json`：

```json
{
  "password": "your-password",
  "idle_timeout": 600,
  "platforms": {
    "taobao": {
      "color": "#FF4400",
      "login_url": "https://login.taobao.com",
      "login_patterns": ["/login", "/passport"]
    }
  },
  "profiles": {
    "shop-a": {
      "name": "我的店铺A",
      "port": 9600,
      "platform": "taobao",
      "url": "https://myseller.taobao.com",
      "account": "shop_a:admin",
      "password": "xxx"
    },
    "shop-b": {
      "name": "我的店铺B",
      "port": 9601,
      "platform": "taobao",
      "url": "https://myseller.taobao.com",
      "account": "shop_b:admin",
      "password": "xxx"
    }
  }
}
```

### 4. 启动

```bash
python3 hub.py --port 8901 --config config.json
```

### 5. 访问

打开 `http://localhost:8901`，输入密码进入。

## 配置说明

### config.json

| 字段 | 类型 | 说明 |
|------|------|------|
| `password` | string | 登录密码 |
| `idle_timeout` | int | 空闲关闭时间（秒），默认 600 |
| `platforms` | object | 平台配置（颜色、登录URL等） |
| `profiles` | object | 浏览器 profile 配置 |

### Profile 配置

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | ✅ | 显示名称 |
| `port` | ✅ | Chrome CDP 调试端口（每个 profile 唯一） |
| `platform` | 否 | 关联的平台（用于颜色、自动登录） |
| `url` | 否 | 启动后默认导航的 URL |
| `account` | 否 | 自动登录账号（需配合插件） |
| `password` | 否 | 自动登录密码（需配合插件） |

### Platform 配置

| 字段 | 说明 |
|------|------|
| `color` | 首页分组标签颜色（hex） |
| `login_url` | 登录页 URL（用于状态检测） |
| `login_patterns` | URL 中包含这些字符串则判定为登录页 |

## 端口分配建议

多项目共享服务器时，每个项目使用不同的端口段：

| 项目 | Hub 端口 | Chrome 端口段 |
|------|----------|--------------|
| 项目 A | 8900 | 9490-9599 |
| 项目 B | 8901 | 9600-9699 |
| 项目 C | 8902 | 9700-9799 |

## 自动登录插件

在 `plugins/` 目录下创建 Python 文件，自动加载。

### 插件接口

```python
# plugins/my_platform.py

PLATFORM = 'my_platform'  # 对应 config 里的 platform 名

async def detect_state(page, config) -> str:
    """检测当前页面的登录状态
    
    返回值:
    - 'logged_in': 已登录，不操作
    - 'need_login': 需要登录，会调用 auto_fill
    - 'captcha': 验证码，跳过
    - 'other': 其他状态，跳过
    """
    url = page.url or ''
    if 'login' in url:
        return 'need_login'
    return 'logged_in'


async def auto_fill(page, account, password, config):
    """自动填写登录表单并提交
    
    参数:
    - page: Playwright Page 对象
    - account: config 里的 account 字段
    - password: config 里的 password 字段
    - config: 完整的 profile 配置字典
    """
    import asyncio
    
    # 填写账号
    acc_input = page.locator('input[name="username"]')
    await acc_input.fill(account)
    
    # 填写密码
    pwd_input = page.locator('input[type="password"]')
    await pwd_input.fill(password)
    
    # 点击登录
    await asyncio.sleep(0.5)
    btn = page.locator('button[type="submit"]')
    await btn.click()
```

### 插件示例：拼多多

```python
# plugins/pinduoduo.py

PLATFORM = '拼多多'

async def detect_state(page, config):
    url = page.url or ''
    if 'pinduoduo.com/login' in url:
        return 'need_login'
    if 'login' in url:
        return 'need_login'
    return 'logged_in'

async def auto_fill(page, account, password, config):
    import asyncio
    tab = page.locator("text=账号登录")
    if await tab.count() > 0:
        await tab.first.click()
        await asyncio.sleep(1)
    acc = page.locator("input[placeholder*='账号']")
    if await acc.count() > 0:
        await acc.fill(account)
        await page.locator("input[type='password']").fill(password)
        btn = page.locator("button", has_text="登录")
        await btn.first.click()
```

## Nginx 反向代理

将 Mirror Hub 暴露到公网：

```nginx
location /mirror/ {
    proxy_pass http://127.0.0.1:8901/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 30s;
    proxy_connect_timeout 10s;
}
```

注意：Hub 内部的重定向路径硬编码了 `/mirror/`，如果用不同的 location 需要修改 `hub.py` 里的 `Location` header。

## SSH 隧道（远程访问）

如果 Hub 在内网服务器，可以用 SSH 隧道暴露：

```bash
# 在 Hub 服务器上
ssh -N -R 18901:localhost:8901 user@public-server

# 在公网服务器 nginx 里
location /mirror/ {
    proxy_pass http://127.0.0.1:18901/;
}
```

## PM2 部署

```bash
# 启动
pm2 start hub.py --name mirror-hub --interpreter python3 -- --port 8901

# 重要：禁用 treekill，否则重启 Hub 会杀掉所有 Chrome
pm2 delete mirror-hub
pm2 start ecosystem.config.js --only mirror-hub
```

`ecosystem.config.js`:
```javascript
module.exports = {
  apps: [{
    name: 'mirror-hub',
    script: '/usr/bin/python3',
    args: '-u hub.py --port 8901',
    cwd: '/path/to/mirror-hub',
    treekill: false,
    kill_timeout: 3000,
  }]
}
```

## API 接口

### 页面

| URL | 说明 |
|-----|------|
| `/` | 首页（所有 profile 列表） |
| `/auth` | 登录页 |
| `/{profile}/` | 远程操作 viewer |

### REST API

| 接口 | 方法 | Body | 说明 |
|------|------|------|------|
| `/{profile}/screenshot` | GET | - | JPEG 截图 |
| `/{profile}/status` | GET | - | 连接状态 |
| `/{profile}/click` | POST | `{x, y}` | 鼠标点击 |
| `/{profile}/mousedown` | POST | `{x, y}` | 鼠标按下 |
| `/{profile}/mousemove` | POST | `{x, y}` | 鼠标移动 |
| `/{profile}/mouseup` | POST | `{x, y}` | 鼠标释放 |
| `/{profile}/type` | POST | `{text}` | 键盘输入 |
| `/{profile}/press` | POST | `{key}` | 按键（Enter/Tab 等） |
| `/{profile}/scroll` | POST | `{x, y, deltaX, deltaY}` | 滚轮 |
| `/{profile}/navigate` | POST | `{url}` | 导航到 URL |
| `/{profile}/restart` | POST | - | 重启 Chrome |
| `/api/batch-status` | GET | - | 所有 profile 状态 |

## 工作原理

```
用户浏览器  ←→  Mirror Hub (Python HTTP)  ←→  Chrome CDP  ←→  目标网站
               │                              │
               ├─ 截图流 (JPEG stream)         ├─ page.screenshot()
               ├─ 鼠标/键盘 (POST)            ├─ page.mouse.click()
               ├─ Cookie 管理 (WebSocket)      ├─ Network.setCookie
               └─ 空闲回收 (定时器)            └─ pkill chrome
```

## 系统要求

- Python 3.10+
- Google Chrome / Chromium
- Playwright (`pip install playwright && playwright install chromium`)
- websocket-client (`pip install websocket-client`，Cookie 持久化需要）
- Xvfb（Linux 无头环境）

## 注意事项

- 每个 profile 的 `port` 必须唯一，不能和其他项目冲突
- Chrome 进程通过 `preexec_fn=os.setpgrp` 独立运行，不受 Hub 重启影响
- Cookie 文件存储在 `saved_cookies/` 目录，包含敏感信息，注意安全
- 空闲回收只在有 `_last_active` 记录的 profile 上触发，从未访问的不会被回收
- 建议 Xvfb 使用 `-maxclients 1024` 参数（默认 256 不够多 Chrome 用）
