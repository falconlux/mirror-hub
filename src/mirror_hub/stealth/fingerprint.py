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


def _load_presets_for_current_os() -> list[dict]:
    if _sys.platform == "darwin":
        fname = "mac.json"
    elif _sys.platform == "win32":
        fname = "win.json"
    else:
        # Linux 服务器用 Win 预设（反检测靠 UA 伪装，真实 Linux UA 反而可疑）
        fname = "win.json"
    with open(_PRESET_DIR / fname, encoding="utf-8") as f:
        return json.load(f)


FINGERPRINT_PRESETS: list[dict] = _load_presets_for_current_os()


def get_fingerprint(profile_index: int) -> dict:
    """按 profile_index 取指纹（对预设数量取模循环）。"""
    return FINGERPRINT_PRESETS[profile_index % len(FINGERPRINT_PRESETS)]


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

// 14. 清理 Playwright 泄露
try {{ delete window.__pwInitScripts; }} catch(e) {{}}
try {{ delete window.__playwright__binding__; }} catch(e) {{}}
setTimeout(() => {{
    try {{ delete window.__pwInitScripts; }} catch(e) {{}}
    try {{ delete window.__playwright__binding__; }} catch(e) {{}}
}}, 100);
"""
