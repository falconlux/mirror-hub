"""BrowserSession — CDP 会话封装（连远程 Chrome + 注入 stealth + 延迟控制）。

Typical usage:
    from mirror_hub import HubClient, BrowserSession

    hub = HubClient("http://h.tommlly.cc:8329", token="...")
    info = hub.launch("tb_shop_01")

    session = BrowserSession(
        cdp_url=info["cdp_url"],
        fingerprint_index=info.get("fingerprint_index", 0),
        label="tb_shop_01",
    )
    await session.connect()
    try:
        await session.page.goto("https://www.taobao.com")
        await session.delay()
    finally:
        await session.disconnect()
"""
from __future__ import annotations

import asyncio
import math
import random
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from mirror_hub._compat import Page, BrowserContext

from mirror_hub.stealth import get_fingerprint, generate_stealth_js
from mirror_hub.stealth.fingerprint import guess_os_kind_from_ua


class BrowserSession:
    """CDP 浏览器会话管理（不含采集业务逻辑，那是业务方的事）。"""

    def __init__(
        self,
        cdp_url: str,
        fingerprint_index: int = 0,
        stealth: bool = True,
        label: str = "",
        reload_on_connect: bool = False,
        fingerprint_override: dict | None = None,
    ):
        """
        Args:
            cdp_url: from HubClient.launch()[" cdp_url"], e.g. "http://127.0.0.1:9490"
            fingerprint_index: which fingerprint preset to inject (usually from hub)
            stealth: inject stealth.js on connect (default True)
            label: log prefix for identification
            reload_on_connect: whether to reload the existing page once so the
                stealth init-script applies to its current document. Playwright's
                `add_init_script` registers the script on all pages but it only
                runs on the *next* navigation/reload — without this, the page
                used for QR login keeps its raw navigator (Linux platform, 40
                cores, en-US), which Taobao uses to silently block search API.
                Default **False** to preserve caller state (forms, pending XHR,
                WebSocket). Callers that control the page lifecycle and need
                stealth to apply immediately (scrapers that connect and
                immediately goto) should set True.
            fingerprint_override: explicit fingerprint dict (same shape as
                FINGERPRINT_PRESETS entries, must include "name" and "ua").
                When provided, bypasses `fingerprint_index`. Use this when the
                Chrome process was launched with a different preset than the
                SDK defaults — mismatched stealth (Chrome=Windows, stealth
                JS=Mac) gets flagged by Taobao's risk control.
        """
        if fingerprint_override is not None:
            missing = [k for k in ("name", "ua", "platform") if k not in fingerprint_override]
            if missing:
                raise ValueError(f"fingerprint_override 缺少字段: {missing}")
        self.cdp_url = cdp_url
        self.fingerprint_index = fingerprint_index
        self.fingerprint_override = fingerprint_override
        self.stealth = stealth
        self.reload_on_connect = reload_on_connect
        self.label = label or cdp_url.rsplit('/', 1)[-1]
        self._pw = None
        self._browser = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def connect(self) -> None:
        """Connect via CDP, select a usable page, inject stealth, cleanup PW leaks."""
        from mirror_hub._compat import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.connect_over_cdp(self.cdp_url)
        self._context = self._browser.contexts[0]

        # 选第一个 url 有效、非 about:blank、非 chrome:// 的页面
        self._page = None
        for p in self._context.pages:
            try:
                url = p.url
                if url and url != "about:blank" and not url.startswith("chrome"):
                    self._page = p
                    break
            except Exception:
                continue
        if not self._page:
            for p in self._context.pages:
                try:
                    _ = p.url
                    self._page = p
                    break
                except Exception:
                    continue
        if not self._page:
            self._page = await self._context.new_page()

        if self.stealth:
            try:
                if self.fingerprint_override:
                    fp = self.fingerprint_override
                else:
                    # 按 Chrome 原始 UA 选预设，避免跨 OS 指纹冲突。
                    # 走 CDP Browser.getVersion，而不是 page.evaluate(navigator.userAgent)
                    # —— 后者可能被 stealth JS 注入过，拿到伪装后的 UA。
                    os_kind = None
                    real_ua = ""
                    try:
                        cdp = await self._context.new_cdp_session(self._page)
                        try:
                            info = await cdp.send("Browser.getVersion")
                            real_ua = info.get("userAgent", "")
                            os_kind = guess_os_kind_from_ua(real_ua)
                        finally:
                            try:
                                await cdp.detach()
                            except Exception:
                                pass
                    except Exception as e:
                        logger.warning(
                            f"[{self.label}] Browser.getVersion 失败，回退本地脚本 OS 预设: {e}"
                        )
                    fp = get_fingerprint(self.fingerprint_index, os_kind=os_kind)
                    logger.debug(
                        f"[{self.label}] Chrome UA={real_ua[:60]!r} → os_kind={os_kind or '(default)'} → fp={fp['name']}"
                    )
                stealth_js = generate_stealth_js(fp)
                # Playwright 的 add_init_script 会通过 CDP Page.addScriptToEvaluateOnNewDocument
                # 给 context 里所有现有和未来 page 注册。但 init-script 只对下次 navigate/reload
                # 生效——当前已加载的 document 里的 navigator 还是裸指纹，需要后续 reload。
                await self._context.add_init_script(stealth_js)
                logger.info(f"[{self.label}] 已连接 指纹:{fp['name']}")
            except Exception as e:
                logger.warning(f"[{self.label}] 指纹注入失败: {e}，继续运行")
        else:
            logger.info(f"[{self.label}] 已连接浏览器（无 stealth）")

        # 对已有页面立即清理 PW 泄露 + WebRTC（主世界瞬时清理，非关键指纹）
        try:
            await self._page.evaluate("""() => {
                try { delete window.__pwInitScripts; } catch(e) {}
                try { delete window.__playwright__binding__; } catch(e) {}
                try { window.RTCPeerConnection = undefined; } catch(e) {}
                try { window.webkitRTCPeerConnection = undefined; } catch(e) {}
            }""")
        except Exception:
            pass

        # 如果 caller 明确要求，在 connect 返回前 reload 一次现有 page，
        # 让 init-script 立即应用到当前 document（否则只对下次 navigate 生效）。
        # 用 self._page.reload()（非 CDP Page.reload）：playwright 自己管 frame
        # lifecycle，只有新 execution context 就绪后才 resolve，caller 第一个
        # page.evaluate() 不会撞 "Execution context destroyed"。
        if self.stealth and self.reload_on_connect:
            try:
                cur = self._page.url
                if cur and not cur.startswith("about:") and not cur.startswith("chrome"):
                    logger.info(f"[{self.label}] reload 现有 page 应用 stealth: {cur[:60]}")
                    await self._page.reload(wait_until="domcontentloaded", timeout=15000)
            except Exception as e:
                logger.warning(f"[{self.label}] reload 失败: {e}（stealth 需等下次 navigate 生效）")

    async def disconnect(self) -> None:
        if self._pw:
            await self._pw.stop()
            logger.info(f"[{self.label}] 已断开连接")

    @property
    def page(self) -> "Page":
        if not self._page:
            raise RuntimeError("未连接，请先调用 connect()")
        return self._page

    @property
    def context(self) -> "BrowserContext":
        if not self._context:
            raise RuntimeError("未连接，请先调用 connect()")
        return self._context

    async def delay(self, min_s: float = 3, max_s: float = 8) -> None:
        """对数正态随机延迟（更像人类）。"""
        mid = (min_s + max_s) / 2
        wait = random.lognormvariate(math.log(mid), 0.35)
        wait = max(min_s * 0.5, min(wait, max_s * 2))
        logger.debug(f"等待 {wait:.1f}s")
        await asyncio.sleep(wait)

    async def page_delay(self, min_s: float = 8, max_s: float = 15) -> None:
        """翻页延迟（更长）。"""
        await self.delay(min_s, max_s)

    def capture_responses(self, url_pattern: str) -> list:
        """注册 response 拦截器，返回动态 list（异步追加）。"""
        captured = []

        async def on_response(response):
            if url_pattern in response.url:
                try:
                    data = await response.json()
                    captured.append(data)
                except Exception:
                    pass

        self._page.on("response", on_response)
        return captured
