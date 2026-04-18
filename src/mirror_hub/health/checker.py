"""HealthChecker — 平台检测器抽象基类。

业务方（qihua/kefu/douyin-ads-toolkit）继承此类实现平台特定检测：

    from mirror_hub.health import HealthChecker, SessionHealth, PunishInfo

    class TaobaoHealthChecker(HealthChecker):
        async def check_health(self, session) -> SessionHealth:
            h = SessionHealth()
            ...  # 淘宝特定判定：cookies、URL、account_nick
            return h

        async def detect_punish(self, session) -> PunishInfo:
            ...  # 淘宝反爬检测：滑块、验证码、封禁页
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mirror_hub.session import BrowserSession
    from mirror_hub.health.models import PunishInfo, SessionHealth


class HealthChecker(ABC):
    """平台检测器抽象基类。"""

    platform_name: str = ""  # 子类应设置，如 "taobao" / "jd" / "douyin"

    @abstractmethod
    async def check_health(self, session: "BrowserSession") -> "SessionHealth":
        """检查会话健康状态（登录态、响应性、反爬）。"""
        ...

    @abstractmethod
    async def detect_punish(self, session: "BrowserSession") -> "PunishInfo":
        """检测当前是否触发反爬。"""
        ...
