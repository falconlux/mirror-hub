"""海量IP (流冠代理) API — 动态代理IP提取

两步签名流程提取动态代理IP，支持HTTP/SOCKS5。
需要先在浏览器中登录 hailiangip.com 获取cookie。

用法:
    from mirror_hub.proxy.hailiangip import HailiangipProvider, fetch_proxies_simple

    # 方式1: 通过浏览器cookie（需登录态）
    provider = HailiangipProvider(cdp_port=9222)
    await provider.init()
    proxy = await provider.get_proxy()

    # 方式2: 加密API（无需登录）
    proxies = fetch_proxies_simple(count=3)
"""
from __future__ import annotations

import asyncio
import json
import time

from loguru import logger
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from mirror_hub._compat import Page

from mirror_hub.proxy.base import Proxy, ProxyProvider

# 默认配置
DEFAULT_ORDER_ID = "O21081011325140162110"
HAILIANGIP_URL = "https://www.hailiangip.com/tool/page/getip"
KEY_API = "/user/generateGetIpUrlKey"
URL_API = "/api/generateUrl"

# 动态IP加密API（10分钟有效期，南京）
ENCRYPTED_API_URL = (
    "https://api.hailiangip.com:8522/api/getIpEncrypt?"
    "dataType=0&encryptParam=7BYsDsYKbGv0BaoQFxmvol7X6W6D09G07Ee5izQJnglIkPL7"
    "%2F6VL5VkgpJyJytfO7oJnaV5W86jKuEm0y6hoKVkmfjbXCingp0G5EG%2BG55V9Yom2Qyksb"
    "%2F9zozCUOQgsIWkoYWht3fX6DJRoQLj%2FsiFEOVSKd7rYH8sMD79r9w0CI4n3zNJRvRomO6"
    "LLTrnlpPABNYNJWkHTfNKd0rPvTLhiKf6bPqRShUoN9E1%2FgqE%3D"
)


class HailiangipProvider(ProxyProvider):
    """海量IP代理池 — 通过浏览器cookie调API提取代理"""

    def __init__(
        self,
        cdp_port: int = 9222,
        order_id: str = DEFAULT_ORDER_ID,
        proxy_type: int = 1,        # 1=HTTP, 2=SOCKS5
        unbind_time: int = 600,      # 占用时长(秒): 60/180/300/600
        pid: str = "27",             # 省份ID (27=江苏)
        cid: str = "337",            # 城市ID (337=南京)
    ):
        self.cdp_port = cdp_port
        self.order_id = order_id
        self.proxy_type = proxy_type
        self.unbind_time = unbind_time
        self.pid = pid
        self.cid = cid
        self._pw = None
        self._page: Page | None = None
        self._proxies: list[Proxy] = []

    async def init(self):
        """连接浏览器，打开海量IP页面获取cookie"""
        from mirror_hub._compat import async_playwright
        self._pw = await async_playwright().start()
        browser = await self._pw.chromium.connect_over_cdp(
            f"http://127.0.0.1:{self.cdp_port}"
        )
        ctx = browser.contexts[0]
        self._page = await ctx.new_page()
        await self._page.goto(HAILIANGIP_URL, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(3)

        logged_in = await self._page.evaluate(
            '() => document.body.innerText.includes("个人中心") || document.body.innerText.includes("退出")'
        )
        if not logged_in:
            raise RuntimeError("海量IP未登录，请先在浏览器中登录 hailiangip.com")

        logger.info("[代理] 海量IP已连接，登录态有效")

    async def close(self):
        """关闭连接"""
        if self._page:
            await self._page.close()
        if self._pw:
            await self._pw.stop()

    async def get_proxy(self, **kwargs) -> Proxy | None:
        """提取一个代理IP"""
        city = kwargs.get("city", "")
        proxies = await self.get_proxies(count=1, city=city)
        return proxies[0] if proxies else None

    async def get_proxies(self, count: int = 1, **kwargs) -> list[Proxy]:
        """提取多个代理IP"""
        city = kwargs.get("city", "")
        if not self._page:
            raise RuntimeError("HailiangipProvider未初始化，请先调用 init()")

        # Step 1: 获取签名key
        key_data = await self._page.evaluate(f'''async () => {{
            const r = await fetch('{KEY_API}', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest'}},
                body: 'selOrderType=2&number={count}&orderId={self.order_id}&type={self.proxy_type}&datatype=0&separator=0&singleIp=0&pid={self.pid}&cid={self.cid}&noDuplicate=0&availableTime={self.unbind_time}'
            }});
            return await r.json();
        }}''')

        if not key_data.get("result"):
            logger.error(f"[代理] 获取key失败: {key_data}")
            return []

        key = key_data["result"]["key"]
        timestamp = key_data["result"]["timestamp"]
        client_ip = await self._page.evaluate("() => getClientIp()")

        # Step 2: 生成最终API URL
        gen_data = {
            "key": key, "timestamp": timestamp, "clientIp": client_ip,
            "num": count, "orderId": self.order_id,
            "type": self.proxy_type, "dataType": 0,
            "unbindTime": self.unbind_time,
            "lineSeparator": 0, "singleIp": 0,
            "safeLevel": 1, "paramEncrypt": 0,
            "pid": self.pid, "cid": self.cid, "noDuplicate": 0,
        }

        url_data = await self._page.evaluate(f'''async () => {{
            const r = await fetch('{URL_API}', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({json.dumps(gen_data)})
            }});
            return await r.json();
        }}''')

        if url_data.get("code") != 0:
            logger.error(f"[代理] 生成URL失败: {url_data}")
            return []

        api_url = url_data["result"]

        # Step 3: 调用API提取IP
        import urllib.request
        try:
            resp = urllib.request.urlopen(api_url, timeout=10)
            result = json.loads(resp.read().decode())
        except Exception as e:
            logger.error(f"[代理] API调用失败: {e}")
            return []

        if result.get("code") != 0:
            logger.error(f"[代理] 提取失败: {result}")
            return []

        expire_at = time.time() + self.unbind_time
        protocol = "socks5" if self.proxy_type == 2 else "http"

        proxies = []
        for item in result.get("data", []):
            proxy = Proxy(
                ip=item["ip"],
                port=item["port"],
                area=item.get("area", ""),
                real_ip=item.get("realIp", ""),
                protocol=protocol,
                expire_at=expire_at,
            )
            proxies.append(proxy)
            logger.info(f"[代理] 提取: {proxy.url} ({proxy.area}) 有效{self.unbind_time // 60}分钟")

        self._proxies.extend(proxies)
        return proxies

    async def get_fresh_proxy(self) -> Proxy | None:
        """获取一个未过期的代理，过期则自动提取新的"""
        self._proxies = [p for p in self._proxies if not p.is_expired]
        if self._proxies:
            return self._proxies.pop(0)
        return await self.get_proxy()


def fetch_proxies_simple(count: int = 1) -> list[Proxy]:
    """直接用加密API提取代理（不需要浏览器登录态）"""
    import urllib.request

    proxies = []
    for _ in range(count):
        try:
            resp = urllib.request.urlopen(ENCRYPTED_API_URL, timeout=10)
            result = json.loads(resp.read().decode())
            if result.get("code") == 0:
                for item in result.get("data", []):
                    proxy = Proxy(
                        ip=item["ip"],
                        port=item["port"],
                        area=item.get("area", ""),
                        real_ip=item.get("realIp", ""),
                        protocol="http",
                        expire_at=time.time() + 600,
                    )
                    proxies.append(proxy)
                    logger.info(f"[代理] {proxy.url} ({proxy.area})")
        except Exception as e:
            logger.error(f"[代理] 提取失败: {e}")
    return proxies


def fetch_proxy() -> Proxy | None:
    """提取单个代理（最简用法）"""
    proxies = fetch_proxies_simple(1)
    return proxies[0] if proxies else None
