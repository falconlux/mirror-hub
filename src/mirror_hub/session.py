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
        reload_on_connect: bool = True,
        fingerprint_override: dict | None = None,
    ):
        """
        Args:
            cdp_url: from HubClient.launch()[" cdp_url"], e.g. "http://127.0.0.1:9490"
            fingerprint_index: which fingerprint preset to inject (usually from hub)
            stealth: inject stealth.js on connect (default True)
            label: log prefix for identification
            reload_on_connect: reload the existing page once so stealth init-scripts
                actually apply to it. Playwright's add_init_script only affects pages
                loaded *after* injection — without this, pre-existing pages keep their
                raw fingerprint (Linux platform, 40 cores, en-US), which Taobao uses
                to silently block search API. Default True. Set False for workflows
                that must preserve in-page state (forms, scroll, etc.).
            fingerprint_override: explicit fingerprint dict (same shape as
                FINGERPRINT_PRESETS entries). When provided, bypasses
                `fingerprint_index`. Use this when the Chrome process was launched
                with a different preset than the SDK defaults — mismatched stealth
                (Chrome=Windows, stealth JS=Mac) gets flagged by Taobao's risk control.
        """
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
                    # 必须走 CDP Browser.getVersion，不能用 page.evaluate(navigator.userAgent)
                    # —— 后者可能被本次或之前的 stealth JS 注入过，拿到的是伪装后的 UA。
                    os_kind = None
                    try:
                        cdp = await self._context.new_cdp_session(self._page)
                        try:
                            info = await cdp.send("Browser.getVersion")
                            real_ua = info.get("userAgent", "")
                            os_kind = guess_os_kind_from_ua(real_ua)
                        finally:
                            await cdp.detach()
                    except Exception:
                        pass
                    fp = get_fingerprint(self.fingerprint_index, os_kind=os_kind)
                stealth_js = generate_stealth_js(fp)
                # 1) 对未来新 page 生效（Playwright 层）
                await self._context.add_init_script(stealth_js)
                # 2) 对当前 target 也注册 init script（CDP 层，覆盖现有 page 的后续 navigate/reload）
                try:
                    cdp = await self._context.new_cdp_session(self._page)
                    try:
                        await cdp.send(
                            "Page.addScriptToEvaluateOnNewDocument",
                            {"source": stealth_js},
                        )
                    finally:
                        await cdp.detach()
                except Exception as e:
                    logger.debug(f"[{self.label}] CDP addScriptToEvaluateOnNewDocument 失败: {e}")
                logger.info(f"[{self.label}] 已连接 指纹:{fp['name']}")
            except Exception as e:
                logger.warning(f"[{self.label}] 指纹注入失败: {e}，继续运行")
                logger.info(f"[{self.label}] 已连接浏览器")
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

        # 关键：如果现有 page 已经 navigate 过真实页面（非 about:blank / chrome://），
        # reload 一次让 stealth init-script 真正应用到主世界。
        # 不 reload 的话，UA=Windows 但 platform=Linux 这种冲突指纹会被风控拦下。
        if self.stealth and self.reload_on_connect:
            try:
                cur = self._page.url
                if cur and not cur.startswith("about:") and not cur.startswith("chrome"):
                    logger.info(f"[{self.label}] reload 现有 page 应用 stealth: {cur[:60]}")
                    cdp = await self._context.new_cdp_session(self._page)
                    try:
                        await cdp.send("Page.reload", {"ignoreCache": True})
                    finally:
                        try:
                            await cdp.detach()
                        except Exception:
                            pass
                    # 给 Chrome 发起导航的时间，再等 DOM 就绪。
                    # 先短 sleep 让 playwright 追上 frame detach，再 wait_for_load_state，
                    # 否则 caller 第一个 evaluate 会撞 "Execution context destroyed"。
                    await asyncio.sleep(1.0)
                    try:
                        await self._page.wait_for_load_state("domcontentloaded", timeout=30000)
                    except Exception as e:
                        logger.debug(f"[{self.label}] reload wait_for_load_state 超时: {e}")
                    await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"[{self.label}] reload 失败: {e}，继续运行")

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
