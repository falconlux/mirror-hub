"""浏览器指纹隔离 — 让每个 Chrome profile 看起来像不同设备。

预设数据外置在 `presets/{mac,win}.json`，方便增删指纹不改代码。
此模块只提供加载 + 生成注入脚本的逻辑。

指纹维度：UA / Platform / WebGL / Canvas / Audio / hardwareConcurrency / deviceMemory / languages

重要：只用与实际运行机器相同 OS 的预设，跨 OS 伪装会被字体/Header/GPU 能力检测识破。
"""
from __future__ import annotations

import hashlib
import json
import sys as _sys
from pathlib import Path

_PRESET_DIR = Path(__file__).parent / "presets"


def _load_presets(os_kind: str) -> list[dict]:
    fname = {"mac": "mac.json", "win": "win.json"}.get(os_kind, "win.json")
    with open(_PRESET_DIR / fname, encoding="utf-8") as f:
        return json.load(f)


def _default_os_kind() -> str:
    if _sys.platform == "darwin":
        return "mac"
    # Linux/Win 脚本默认用 Win 预设（伪装 Linux 太可疑）
    return "win"


# 默认用运行脚本的 OS 选预设（向后兼容）
FINGERPRINT_PRESETS: list[dict] = _load_presets(_default_os_kind())


def get_fingerprint(profile_index: int, os_kind: str | None = None) -> dict:
    """按 profile_index 取指纹（对预设数量取模循环）。

    Args:
        profile_index: 索引
        os_kind: 'mac' 或 'win'。None 时用运行脚本的 OS（旧行为）。
            当本地脚本连接的是**伪装成另一 OS 的远程 Chrome**（如 Mac 本地
            连 Linux GPU 上跑的 Win-伪装 Chrome），传 os_kind='win' 强制匹配，
            否则 stealth JS 会和 Chrome 真实 UA 冲突，被淘宝风控识破。
    """
    presets = _load_presets(os_kind) if os_kind else FINGERPRINT_PRESETS
    return presets[profile_index % len(presets)]


def guess_os_kind_from_ua(ua: str) -> str:
    """从 UA 字符串推断应该用哪套预设。

    Linux UA 显式返回 'win'—伪装 Linux 会暴露服务器特征（字体/GPU/缺桌面 API
    等），用 win 预设反检测更稳。空/未知 UA 也回退 win。
    """
    ua_lower = (ua or "").lower()
    if "mac" in ua_lower or "darwin" in ua_lower:
        return "mac"
    if "linux" in ua_lower or "x11" in ua_lower:
        # caller 忘了给 Chrome 传 --user-agent，暴露真实 Linux UA——
        # 用 win 预设，由 stealth JS 把 navigator.userAgent 覆盖成 Windows
        return "win"
    return "win"


def generate_stealth_js(fp: dict) -> str:
    """生成反检测 JS 脚本，通过 `context.add_init_script()` 注入。

    覆盖的维度：UA/Platform/hardwareConcurrency/deviceMemory/languages/WebGL/Canvas/Audio/webdriver/plugins/WebRTC
    """
    # 用 profile 名生成稳定的 canvas 噪声种子
    seed = int(hashlib.md5(fp["name"].encode()).hexdigest()[:8], 16)

    return f"""
// ============================================
// 指纹隔离脚本 — Profile: {fp["name"]}
// ============================================

// 1. User Agent & Platform
Object.defineProperty(navigator, 'userAgent', {{get: () => '{fp["ua"]}'}});
Object.defineProperty(navigator, 'platform', {{get: () => '{fp["platform"]}'}});
Object.defineProperty(navigator, 'appVersion', {{get: () => '{fp["ua"].replace("Mozilla/", "")}'}});

// 2. 屏幕 — 不覆盖 width/height（CDP连接真实Chrome时用真实值更安全），只覆盖 colorDepth
Object.defineProperty(screen, 'colorDepth', {{get: () => 24}});

// 3. 硬件
Object.defineProperty(navigator, 'hardwareConcurrency', {{get: () => {fp["cores"]}}});
Object.defineProperty(navigator, 'deviceMemory', {{get: () => {fp["memory"]}}});

// 4. 语言
Object.defineProperty(navigator, 'languages', {{get: () => {json.dumps(fp["languages"])}}});
Object.defineProperty(navigator, 'language', {{get: () => '{fp["languages"][0]}'}});

// 5. WebGL 指纹
const webglHandler = {{
    apply: function(target, thisArg, args) {{
        const param = args[0];
        if (param === 37445) return '{fp["webgl_vendor"]}';
        if (param === 37446) return '{fp["webgl_renderer"]}';
        return Reflect.apply(target, thisArg, args);
    }}
}};
try {{
    WebGLRenderingContext.prototype.getParameter = new Proxy(WebGLRenderingContext.prototype.getParameter, webglHandler);
    WebGL2RenderingContext.prototype.getParameter = new Proxy(WebGL2RenderingContext.prototype.getParameter, webglHandler);
}} catch(e) {{}}

// 6. Canvas 噪声（确定性，同一 canvas 实例重复调用结果一致）
const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
const _canvasCache = new WeakMap();
HTMLCanvasElement.prototype.toDataURL = function(type) {{
    if (!_canvasCache.has(this)) {{
        const ctx = this.getContext('2d');
        if (ctx && this.width > 0 && this.height > 0) {{
            try {{
                const img = ctx.getImageData(0, 0, this.width, this.height);
                let seed = {seed};
                const len = Math.min(img.data.length, 400);
                for (let i = 0; i < len; i += 4) {{
                    seed = (seed * 16807 + 1) & 0x7fffffff;
                    img.data[i] = (img.data[i] + (seed % 3) - 1) & 0xff;
                }}
                ctx.putImageData(img, 0, 0);
            }} catch(e) {{}}
        }}
        _canvasCache.set(this, true);
    }}
    return origToDataURL.apply(this, arguments);
}};

// 7. AudioContext 指纹
if (window.AudioContext || window.webkitAudioContext) {{
    const origGetFloatFrequencyData = AnalyserNode.prototype.getFloatFrequencyData;
    AnalyserNode.prototype.getFloatFrequencyData = function(array) {{
        origGetFloatFrequencyData.call(this, array);
        let s = {seed};
        for (let i = 0; i < array.length; i++) {{
            s = (s * 16807 + 1) & 0x7fffffff;
            array[i] += (s % 100) / 10000;
        }}
    }};
}}

// 8. Webdriver 隐藏
Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}});

// 9. Chrome 对象
window.chrome = {{runtime: {{}}, loadTimes: function(){{}}, csi: function(){{}}, app: {{isInstalled: false}}}};

// 10. Plugins
Object.defineProperty(navigator, 'plugins', {{
    get: () => {{
        const p = [
            {{name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format'}},
            {{name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: ''}},
            {{name: 'Native Client', filename: 'internal-nacl-plugin', description: ''}},
        ];
        p.length = 3;
        return p;
    }}
}});

// 11. Permissions
const origQuery = window.Permissions?.prototype?.query;
if (origQuery) {{
    window.Permissions.prototype.query = function(params) {{
        if (params.name === 'notifications') return Promise.resolve({{state: Notification.permission}});
        return origQuery.apply(this, arguments);
    }};
}}

// 12. WebRTC 禁用（防止真实 IP 泄露）
try {{
    window.RTCPeerConnection = undefined;
    window.webkitRTCPeerConnection = undefined;
    window.mozRTCPeerConnection = undefined;
    if (navigator.mediaDevices) {{
        Object.defineProperty(navigator, 'mediaDevices', {{get: () => undefined}});
    }}
}} catch(e) {{}}

// 13. Connection API
try {{
    Object.defineProperty(navigator, 'connection', {{get: () => ({{
        effectiveType: '4g', downlink: 10, rtt: 50, saveData: false
    }})}});
}} catch(e) {{}}

// 14. 时区（Intl 层 override，真正的 timezone 切换靠 CDP Emulation.setTimezoneOverride）
try {{
    const TZ = '{fp.get("timezone", "Asia/Shanghai")}';
    const _OrigDTF = Intl.DateTimeFormat;
    function _PatchedDTF(...args) {{
        if (!(this instanceof _PatchedDTF)) return new _PatchedDTF(...args);
        if (args.length < 2) args.push({{}});
        if (!args[1].timeZone) args[1].timeZone = TZ;
        return new _OrigDTF(...args);
    }}
    _PatchedDTF.prototype = _OrigDTF.prototype;
    _PatchedDTF.supportedLocalesOf = _OrigDTF.supportedLocalesOf;
    Intl.DateTimeFormat = _PatchedDTF;
}} catch(e) {{}}

// 15. Client Hints (sec-ch-ua-*) — 新版风控读这些 header 判定平台一致性
try {{
    if (navigator.userAgentData) {{
        const UA_DATA = {{
            brands: [
                {{brand: 'Chromium', version: '{fp["ua"].split("Chrome/")[1].split(".")[0] if "Chrome/" in fp["ua"] else "136"}'}},
                {{brand: 'Google Chrome', version: '{fp["ua"].split("Chrome/")[1].split(".")[0] if "Chrome/" in fp["ua"] else "136"}'}},
                {{brand: 'Not.A/Brand', version: '99'}},
            ],
            mobile: false,
            platform: 'Windows',
        }};
        Object.defineProperty(navigator.userAgentData, 'brands', {{get: () => UA_DATA.brands}});
        Object.defineProperty(navigator.userAgentData, 'mobile', {{get: () => UA_DATA.mobile}});
        Object.defineProperty(navigator.userAgentData, 'platform', {{get: () => UA_DATA.platform}});
        const origHEV = navigator.userAgentData.getHighEntropyValues.bind(navigator.userAgentData);
        navigator.userAgentData.getHighEntropyValues = function(hints) {{
            return origHEV(hints).then(data => Object.assign(data, {{
                platform: 'Windows', platformVersion: '15.0.0', architecture: 'x86', bitness: '64',
                model: '', uaFullVersion: '{fp["ua"].split("Chrome/")[1].split(" ")[0] if "Chrome/" in fp["ua"] else "136.0.0.0"}',
                fullVersionList: UA_DATA.brands.map(b => ({{brand: b.brand, version: '{fp["ua"].split("Chrome/")[1].split(" ")[0] if "Chrome/" in fp["ua"] else "136.0.0.0"}'}})),
                brands: UA_DATA.brands,
                mobile: false,
            }}));
        }};
    }}
}} catch(e) {{}}

// 16. 清理 Playwright 泄露
try {{ delete window.__pwInitScripts; }} catch(e) {{}}
try {{ delete window.__playwright__binding__; }} catch(e) {{}}
setTimeout(() => {{
    try {{ delete window.__pwInitScripts; }} catch(e) {{}}
    try {{ delete window.__playwright__binding__; }} catch(e) {{}}
}}, 100);
"""
