# Browser-Hub → Mirror-Hub 迁移规格（qihua 视角）

> **目的**：把 qihua 从 `browser_hub` SDK 依赖中切出来，在 mirror-hub 中**按需重写**所需能力。不搬代码，只迁能力。
>
> **现状**：qihua 在 7 个文件中调用了 browser_hub 的 10 个符号，**真正的业务依赖只有 3 块**：启动 Chrome、CDP 连接 + 指纹注入、淘宝登录态检测。

---

## 一、qihua 实际调用清单（按模块）

| # | 调用符号 | 文件 | 用途 |
|---|---------|------|------|
| 1 | `BrowserProfile(profile_name, port, chrome_path, profiles_dir)` + `.launch()` + `.cdp_url` | `main.py` cmd_launch | CLI 启动单个浏览器让用户手动登录 |
| 2 | `BrowserSession(debug_port, profile_index, label)` + `.connect() / .disconnect()` + `.page / .context / .delay() / .page_delay()` | `src/scrapers/base.py` | 所有爬虫基类的会话对象 |
| 3 | `TaobaoHealthChecker().check_health(session) / .detect_punish(session)` | `src/scrapers/base.py` | 淘宝登录态 + 反爬检测 |
| 4 | `PunishInfo, SessionHealth` | `src/scrapers/base.py` | 健康状态数据模型 |
| 5 | `launcher.launch_chrome / kill_chromes / verify_cdp_alive` | `scripts/launch_profiles.py`、`scripts/mirror/browser_compat.py` | 批量启动 Chrome |
| 6 | `launcher.label_profiles` | `scripts/launch_profiles.py` | 给窗口打标签（调试用） |
| 7 | `stealth.get_fingerprint / get_chrome_args` | `launch_profiles.py`、`mirror/browser_compat.py`、`api/routes/profiles.py` × 2 | 获取指纹预设 / Chrome 启动参数 |
| 8 | `proxy.hailiangip.fetch_proxies_simple(n)` | `launch_profiles.py` | 取 n 个代理 IP |
| 9 | `_compat.async_playwright` | `mirror/browser_compat.py` | Playwright 封装（优先 rebrowser） |

---

## 二、mirror-hub 需要提供的接口（最小规格）

### 模块 1 — `mirror_hub.stealth`（P0，最高频）

**职责**：指纹预设 + Chrome 启动参数

```python
# ── 函数 ──
def get_fingerprint(profile_index: int) -> dict:
    """根据 profile_index 取指纹字典（自动按 OS 选 Mac/Win 预设，取模循环）"""

def get_chrome_args(fp: dict) -> list[str]:
    """根据指纹返回 Chrome --window-size / --user-agent 等参数列表"""

def generate_stealth_js(fp: dict) -> str:
    """生成 init script，覆盖 navigator.userAgent/platform/hardwareConcurrency 等"""

# ── 常量 ──
FINGERPRINT_PRESETS: list[dict]  # 当前 OS 的预设列表（5 个 Mac + 4 个 Win）
```

**指纹字典结构**：
```python
{
  "name": "mac-m1-1440x900",
  "screen": {"width": 1440, "height": 900},
  "window": "1366,728",
  "ua": "Mozilla/5.0 ...",
  "platform": "MacIntel" | "Win32",
  "webgl_vendor": "Apple Inc.",
  "webgl_renderer": "ANGLE (Apple, Apple M1, ...)",
  "cores": 8, "memory": 8,
  "timezone": "Asia/Shanghai",
  "languages": ["zh-CN", "zh", "en-US", "en"],
}
```

**重写建议**：照搬逻辑即可，但可以把预设数据 **外置成 JSON**（`mirror_hub/stealth/presets/{mac,win}.json`），方便增删指纹不改代码。

---

### 模块 2 — `mirror_hub.launcher`（P0）

**职责**：无状态启动/停止 Chrome 进程

```python
def launch_chrome(
    chrome_path: str,
    profile_dir: str,
    port: int,
    profile_index: int = 0,
    proxy: str | None = None,
) -> subprocess.Popen:
    """启动单个 Chrome，内部调 get_fingerprint + get_chrome_args"""

def kill_chromes(ports: list[int]) -> None:
    """pkill -f 'remote-debugging-port=<port>'（注意：不会误杀日常 Chrome）"""

def verify_cdp_alive(port: int) -> bool:
    """curl http://127.0.0.1:<port>/json/version 确认端口活着"""

async def label_profiles(ports: list[int], labels: list[str]) -> None:
    """通过 CDP 把 document.title 加上 [label] 前缀（调试辅助，可不做）"""
```

**重写建议**：
- `launch_chrome` / `kill_chromes` / `verify_cdp_alive` 几乎零改动
- `label_profiles` 仅调试用，**可以先不提供**，qihua 脚本里改成 no-op

---

### 模块 3 — `mirror_hub.BrowserProfile`（P1）

**职责**：状态化的 Chrome 环境（封装 user_data_dir + port + 启停）

```python
class BrowserProfile:
    def __init__(
        self,
        profile_name: str,
        debug_port: int = 9222,
        profile_index: int = 0,
        chrome_path: str = "...",
        profiles_dir: Path | str = Path("data/profiles"),
    ): ...

    def launch(self, proxy: str | None = None) -> subprocess.Popen: ...
    def stop(self) -> None: ...
    @property
    def cdp_url(self) -> str: ...  # "http://127.0.0.1:<port>"
    def is_running(self) -> bool: ...
```

**qihua 里实际只用到**：`BrowserProfile(...)`, `.launch()`, `.cdp_url`

`ProfileManager` **qihua 没用**，可以不移植。

---

### 模块 4 — `mirror_hub.BrowserSession`（P1，核心）

**职责**：CDP 连接 + 指纹注入 + 延迟控制

```python
class BrowserSession:
    def __init__(
        self,
        debug_port: int = 9222,
        profile_index: int = 0,
        stealth: bool = True,
        label: str = "",
    ): ...

    async def connect(self) -> None:
        """连接 CDP、选页面、注入 stealth.js、清理 Playwright 残留"""

    async def disconnect(self) -> None: ...

    @property
    def page(self) -> Page: ...       # 当前活动页面
    @property
    def context(self) -> BrowserContext: ...

    async def delay(self, min_s=3, max_s=8) -> None:
        """对数正态随机延迟（更像人类）"""

    async def page_delay(self, min_s=8, max_s=15) -> None: ...

    def capture_responses(self, url_pattern: str) -> list:
        """注册 response 拦截器，返回 list（异步追加）"""
```

**关键行为**（不能漏）：
- connect 时要选「第一个 url 有效、非 about:blank、非 chrome://」的页面
- 全失败时退化到 `new_page()`
- 注入 stealth.js **用 `context.add_init_script`**（不是 evaluate）
- 连接后立即清理 `window.__pwInitScripts` / `__playwright__binding__` / `RTCPeerConnection`

**重写建议**：这块是最精细的，几乎要 1:1 对齐 browser_hub 的实现（或至少经过真实反爬测试）。

---

### 模块 5 — `mirror_hub.health`（P1）

**职责**：淘宝登录态 + 反爬检测

```python
class PunishInfo:
    """反爬拦截详情（type/level/recoverable/...）+ to_dict()"""
    @classmethod
    def register_types(cls, types: dict[str, dict]) -> None: ...

class SessionHealth:
    """会话健康（connected/logged_in/punish/nick/...）+ .ok / .status_label / .to_dict()"""

class TaobaoHealthChecker:
    async def check_health(self, session: BrowserSession) -> SessionHealth: ...
    async def detect_punish(self, session: BrowserSession) -> PunishInfo: ...
```

**重写建议**：
- `PunishInfo / SessionHealth` 是纯数据类，可以直接照搬
- `TaobaoHealthChecker` 涉及平台特定的 cookie/URL 判定，先照搬
- 未来可以抽出 `HealthChecker` 基类，让 `TaobaoHealthChecker / DouyinHealthChecker` 继承

---

### 模块 6 — `mirror_hub.proxy`（P2，低频）

```python
def fetch_proxies_simple(n: int = 1) -> list[Proxy]:
    """从海量IP API 取 n 个代理（返回 Proxy 对象列表）"""

class Proxy:
    """代理对象，用 str(proxy) 得到 "socks5://ip:port" 或 "http://ip:port" """
```

**重写建议**：本地已经有 `hailiangip-proxy` skill，可以考虑把它做成独立包 `mirror_hub.proxy`，或者直接让 qihua 自己在项目里实现。**如果 mirror-hub 要做轻量 SDK，这部分可以剔除**。

---

### 模块 7 — `mirror_hub._compat`（P2，工具）

```python
# 一个 7 行文件，try rebrowser-playwright, except playwright
from mirror_hub._compat import async_playwright, Page, BrowserContext
```

**重写建议**：1:1 照搬，改包名即可。

---

## 三、建议的 mirror-hub SDK 目录结构

```
mirror-hub/
├── hub.py                          ← 保留：服务端（Chrome 进程池管理，远程调度）
├── plugins/
├── src/mirror_hub/
│   ├── __init__.py                 ← export: BrowserProfile, BrowserSession, PunishInfo, SessionHealth
│   ├── _compat.py                  ← rebrowser-playwright 兼容层
│   ├── stealth/
│   │   ├── __init__.py             ← export: get_fingerprint, get_chrome_args, generate_stealth_js
│   │   ├── fingerprint.py          ← 只放逻辑
│   │   └── presets/
│   │       ├── mac.json            ← 5 个 Mac 指纹
│   │       └── win.json            ← 4 个 Win 指纹
│   ├── launcher.py                 ← launch_chrome, kill_chromes, verify_cdp_alive (去掉 label_profiles)
│   ├── profile.py                  ← BrowserProfile（去掉 ProfileManager）
│   ├── connection.py               ← BrowserSession
│   ├── health/
│   │   ├── __init__.py             ← export: PunishInfo, SessionHealth, TaobaoHealthChecker
│   │   ├── models.py
│   │   └── taobao.py
│   └── client.py (可选)            ← HTTP 客户端，调 hub.py 的远程 Chrome 调度接口
├── pyproject.toml
└── README.md
```

**砍掉的部分**：
- `ProfileManager`（qihua 没用）
- `label_profiles`（仅调试）
- `agent.py + agent_cli.py`（qihua 没用，且是 browser-hub 独立功能）
- `proxy/`（改用现有的 hailiangip-proxy skill，或独立项目）

**mirror-hub 最终角色**：
- **服务端**：`hub.py` 管理 GPU 服务器上的 Chrome 池
- **SDK**：`src/mirror_hub/` 提供客户端能力（可本地启 Chrome 也可连 hub）
- **未来方向**：`client.py` 统一抽象"本地 Chrome / 远程 hub Chrome"，业务代码不关心浏览器在哪

---

## 四、逐模块迁移顺序（推荐）

| 步骤 | 迁移的模块 | qihua 切换的文件数 | 风险 |
|------|-----------|-------------------|------|
| 1 | `stealth` (P0) | 4 处 | 低（纯函数） |
| 2 | `launcher` (P0, 去 label_profiles) | 2 处 | 低 |
| 3 | `_compat` | 1 处 | 低 |
| 4 | `BrowserProfile` | 1 处（main.py） | 低 |
| 5 | `BrowserSession` | 1 处（base.py） | **中**（反检测关键） |
| 6 | `health.*` | 1 处 | 中 |
| 7 | `proxy` 或剔除 | 1 处 | 低 |
| 8 | 卸载 `pip uninstall browser-hub`，跑完整 qihua 流程 | - | 高（整体验证） |
| 9 | `jianshang/browser-hub` 彻底归档，删除 deploy.md 中相关段落 | - | 低 |

每一步骤完成后：qihua 跑一次典型场景（启动 Chrome → 登录 → 采集一个 SKU）→ 通过则进下一步。

---

## 五、与用户确认点

1. **mirror-hub 定位**：只做"本地 Chrome SDK + 远程 Hub 客户端"，不做代理管理和 Agent 系统 — 对吗？
2. **指纹预设外置 JSON**：要 / 不要？
3. **ProfileManager 砍掉**：qihua 没用到，可以不移植 — 对吗？
4. **label_profiles 砍掉**：qihua 调试用，改 no-op — 对吗？
5. **proxy 砍掉**：让 qihua 自己用 hailiangip-proxy skill — 对吗？
6. **agent.py 不搬**：qihua 没用 — 对吗？
7. **淘宝检测器**：要不要顺便抽象 `HealthChecker` 基类为以后的抖店/京东留口子？（还是先照搬淘宝版本，将来用到再抽）

回答这 7 个问题后，我开始按顺序实现。
