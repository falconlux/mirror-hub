"""Stealth: 指纹预设 + 反检测 JS 注入脚本。

Typical usage:
    from mirror_hub.stealth import get_fingerprint, generate_stealth_js
    fp = get_fingerprint(profile_index=0)
    js = generate_stealth_js(fp)
    await context.add_init_script(js)
"""
from mirror_hub.stealth.fingerprint import (
    FINGERPRINT_PRESETS,
    get_fingerprint,
    generate_stealth_js,
)

__all__ = [
    "FINGERPRINT_PRESETS",
    "get_fingerprint",
    "generate_stealth_js",
]
