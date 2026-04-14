# Mirror Hub — 远程浏览器管理平台

GPU 服务器上的统一 Chrome 浏览器管理服务。所有项目共享同一个 Hub 实例，通过配置文件添加自己的浏览器 profiles。

## 架构

```
GPU 服务器 (h.tommlly.cc:8329)
├── Mirror Hub (端口 8900)          ← 唯一实例，管理所有项目的 Chrome
│   ├── kefu 项目 (端口 9490-9562)   ← 50 个店铺浏览器
│   ├── dpzx 项目 (端口 9600-9699)   ← 示例：其他项目
│   └── ...
├── Xvfb :99 (2560x1440)           ← 共享虚拟显示
├── SSH 隧道 → kefu.1to10.cn       ← 公网访问
└── PM2 管理所有进程
```

## 新项目接入步骤

### 1. 编辑配置文件

SSH 登录服务器，编辑 profiles 配置：

```bash
ssh root@h.tommlly.cc -p 8329
vi /root/kefu/scraper/profiles.json
```

在 `profiles` 里添加你的浏览器：

```json
{
  "my-browser-1": {
    "name": "我的浏览器1",
    "port": 9600,
    "platform": "自定义平台",
    "url": "https://example.com",
    "account": "user@example.com",
    "password": "xxx"
  },
  "my-browser-2": {
    "name": "我的浏览器2",
    "port": 9601,
    "platform": "自定义平台"
  }
}
```

### 2. 重启 Hub

```bash
pm2 restart mirror-hub
```

### 3. 访问

公网：`https://kefu.1to10.cn/mirror/my-browser-1/`
密码：`kefu2026`

## 端口分配

每个项目使用独立的端口段，避免冲突：

| 项目 | Chrome 端口段 | 已使用 |
|------|--------------|--------|
| kefu-抖音 | 9490-9498 | 9 个 |
| kefu-天猫 | 9500-9503 | 4 个 |
| kefu-京东 | 9510-9523 | 11 个 |
| kefu-得物 | 9530-9534 | 5 个 |
| kefu-拼多多 | 9540-9562 | 23 个 |
| **可用** | **9600-9999** | 预留给其他项目 |

## Profile 配置说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | ✅ | 显示名称 |
| `port` | ✅ | Chrome CDP 端口（全局唯一） |
| `platform` | 否 | 平台分组名（首页按此分组） |
| `url` | 否 | Chrome 启动后默认打开的 URL |
| `account` | 否 | 自动登录账号 |
| `password` | 否 | 自动登录密码 |

## 平台配置

在配置文件的 `platforms` 里添加平台信息（颜色、登录页检测）：

```json
{
  "platforms": {
    "我的平台": {
      "color": "#FF6600",
      "login_url": "https://example.com/login",
      "login_patterns": ["/login", "/signin"]
    }
  }
}
```

## 自动登录插件

如果你的平台需要自动填写账号密码，在 `plugins/` 目录创建插件：

```python
# /root/kefu/scraper/plugins/my_platform.py

PLATFORM = '我的平台'  # 对应 config 里的 platform 名

async def detect_state(page, config):
    """返回: 'logged_in' | 'need_login' | 'captcha' | 'other'"""
    url = page.url or ''
    if 'login' in url:
        return 'need_login'
    return 'logged_in'

async def auto_fill(page, account, password, config):
    """自动填写登录表单"""
    await page.locator('input[name="user"]').fill(account)
    await page.locator('input[type="password"]').fill(password)
    await page.locator('button[type="submit"]').click()
```

## 功能说明

### 远程操作

打开 `https://kefu.1to10.cn/mirror/{profile-id}/` 即可远程操作浏览器：
- 鼠标点击、拖拽（支持滑块验证码）
- 键盘输入
- 触摸屏操作（手机/平板）
- 缩放/平移（右上角按钮或双指捏合）
- 重启浏览器（工具栏红色按钮）

### Cookie 持久化

- 运行中每 5 分钟自动保存 cookie
- Chrome 重启后通过 CDP WebSocket 恢复 cookie
- 存储路径：`/root/kefu/scraper/saved_cookies/{profile-id}.json`

### 空闲回收

- 10 分钟无人访问的 Chrome 自动关闭
- 关闭前自动保存 cookie
- 下次访问时自动重新启动并恢复 cookie

### 状态检测

首页异步显示每个 profile 的状态：
- 🟢 已登录
- 🟡 待登录
- 🔵 运行中
- ⚫ 未启动

## API

| 接口 | 方法 | 说明 |
|------|------|------|
| `/{profile}/screenshot` | GET | JPEG 截图 |
| `/{profile}/status` | GET | 连接状态 |
| `/{profile}/click` | POST | `{x, y}` 鼠标点击 |
| `/{profile}/mousedown` | POST | `{x, y}` 鼠标按下 |
| `/{profile}/mousemove` | POST | `{x, y}` 鼠标移动 |
| `/{profile}/mouseup` | POST | `{x, y}` 鼠标释放 |
| `/{profile}/type` | POST | `{text}` 键盘输入 |
| `/{profile}/press` | POST | `{key}` 按键 |
| `/{profile}/scroll` | POST | `{x, y, deltaX, deltaY}` 滚轮 |
| `/{profile}/navigate` | POST | `{url}` 导航 |
| `/{profile}/restart` | POST | 重启 Chrome |
| `/api/batch-status` | GET | 所有 profile 批量状态 |

## 服务器环境

GPU 服务器已配置好以下环境，新项目无需重复安装：

| 组件 | 状态 |
|------|------|
| Google Chrome | ✅ 已安装 |
| Playwright | ✅ 已安装 |
| websocket-client | ✅ 已安装 |
| Xvfb | ✅ 运行中（:99, 2560x1440, maxclients=1024） |
| inotify 限制 | ✅ 已提高（instances=8192） |
| file-max | ✅ 已提高（2097152） |

## PM2 进程

```
mirror-hub      — Hub HTTP 服务（端口 8900, treekill=false）
ssh-tunnel      — SSH 反向隧道（→ kefu 服务器 18900）
tunnel-watchdog — 隧道健康检测（每 60 秒）
mysql-tunnel    — MySQL 转发（本地 13306 → 腾讯云 MySQL）
```

## 注意事项

1. **端口唯一**：每个 profile 的 port 必须全局唯一，不能和其他项目冲突
2. **Chrome 独立**：Chrome 进程独立于 Hub（`treekill=false`），重启 Hub 不影响 Chrome
3. **Cookie 安全**：`saved_cookies/` 包含登录凭据，注意文件权限
4. **资源管理**：空闲回收确保不会有几十个 Chrome 同时常驻
5. **Xvfb 共享**：所有 Chrome 共用 DISPLAY=:99，不要修改或重启 Xvfb

## 连接方式

```bash
# SSH 到 GPU 服务器
ssh root@h.tommlly.cc -p 8329

# 本地直连 Hub（开发调试用）
ssh -L 8900:localhost:8900 root@h.tommlly.cc -p 8329
# 然后浏览器打开 http://localhost:8900

# 公网访问
https://kefu.1to10.cn/mirror/
```
