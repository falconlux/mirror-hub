"""mirror-hub SDK — Chrome CDP 客户端（连远程 Hub 上的 Chrome）

Typical usage:
    from mirror_hub import HubClient, BrowserSession

    hub = HubClient("http://h.tommlly.cc:8329", token="...")
    info = hub.launch("tb_shop_01")              # tell hub to start Chrome
    session = BrowserSession(info["cdp_url"])
    await session.connect()
    await session.page.goto("https://www.taobao.com")
    ...
    await session.disconnect()
"""
from mirror_hub.client import HubClient
from mirror_hub.session import BrowserSession
from mirror_hub.health.models import PunishInfo, SessionHealth
from mirror_hub.health.checker import HealthChecker

__version__ = "0.2.0"

__all__ = [
    "HubClient",
    "BrowserSession",
    "PunishInfo",
    "SessionHealth",
    "HealthChecker",
]
