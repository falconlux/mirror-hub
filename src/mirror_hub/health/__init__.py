"""Health: 会话健康检查的抽象接口。

平台特定实现（淘宝/京东/抖店等）由业务方继承 HealthChecker 完成。
"""
from mirror_hub.health.models import PunishInfo, SessionHealth
from mirror_hub.health.checker import HealthChecker

__all__ = ["PunishInfo", "SessionHealth", "HealthChecker"]
