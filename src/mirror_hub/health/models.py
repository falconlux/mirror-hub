"""健康检测数据模型 — 平台无关。

业务方（如淘宝/京东检测器）通过 PunishInfo.register_types() 注册自己的 punish 类型。
"""
from __future__ import annotations


class PunishInfo:
    """反爬拦截详情。

    `TYPE_MAP` 默认为空，各平台通过 `register_types()` 注册自己的类型表：

        PunishInfo.register_types({
            "sliding_captcha": {"level": "medium", "label": "滑块验证码", "recovery": "手动过"},
            "account_banned": {"level": "hard", "label": "账号封禁", "recovery": "换号"},
        })
    """

    _type_registry: dict[str, dict] = {}

    @classmethod
    def register_types(cls, types: dict[str, dict]) -> None:
        """注册平台特定的 punish 类型。

        Args:
            types: {type_name: {"level": "hard|medium|soft", "label": str, "desc": str, "recovery": str}}
        """
        cls._type_registry.update(types)

    def __init__(self, punish_type: str | None = None, detail: str = ""):
        self.type = punish_type
        self.detail = detail

    @property
    def active(self) -> bool:
        return self.type is not None

    @property
    def level(self) -> str:
        """hard=账号封禁 / medium=验证码拦截 / soft=临时限制 / none=正常"""
        if not self.type:
            return "none"
        return self._type_registry.get(self.type, {}).get("level", "unknown")

    @property
    def label(self) -> str:
        if not self.type:
            return "正常"
        return self._type_registry.get(self.type, {}).get("label", self.type)

    @property
    def desc(self) -> str:
        if not self.type:
            return ""
        return self._type_registry.get(self.type, {}).get("desc", "")

    @property
    def recovery(self) -> str:
        if not self.type:
            return ""
        return self._type_registry.get(self.type, {}).get("recovery", "")

    @property
    def recoverable(self) -> bool:
        """soft/medium 可以等待或手动过验证码，hard 需要换号。"""
        return self.level in ("soft", "medium")

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "level": self.level,
            "label": self.label,
            "desc": self.desc,
            "recovery": self.recovery,
            "detail": self.detail,
            "recoverable": self.recoverable,
        }

    def __repr__(self):
        return f"<PunishInfo {self.type or 'none'} level={self.level}>"


class SessionHealth:
    """浏览器会话健康状态（平台无关的顶层模型）。"""

    def __init__(self):
        self.connected: bool = False
        self.page_responsive: bool = False
        self.logged_in: bool = False
        self.punish: PunishInfo = PunishInfo()
        self.cookies_count: int = 0
        self.current_url: str = ""
        self.account_nick: str = ""
        self.account_id: str = ""
        self.login_id: str = ""
        self.page_title: str = ""
        self.extra: dict = {}  # 平台特定数据（搜索结果数、店铺 ID 等）

    @property
    def punished(self) -> str | None:
        """兼容旧代码。"""
        return self.punish.type

    @property
    def ok(self) -> bool:
        """是否可以正常采集。"""
        return self.connected and self.page_responsive and self.logged_in and not self.punish.active

    @property
    def status_label(self) -> str:
        if not self.connected:
            return "disconnected"
        if not self.page_responsive:
            return "unresponsive"
        if self.punish.active:
            return f"punished:{self.punish.type}"
        if not self.logged_in:
            return "logged_out"
        return "healthy"

    def to_dict(self) -> dict:
        return {
            "connected": self.connected,
            "page_responsive": self.page_responsive,
            "logged_in": self.logged_in,
            "account_nick": self.account_nick,
            "account_id": self.account_id,
            "login_id": self.login_id,
            "cookies_count": self.cookies_count,
            "current_url": self.current_url,
            "page_title": self.page_title,
            "status": self.status_label,
            "ok": self.ok,
            "punish": self.punish.to_dict() if self.punish.active else None,
            **self.extra,
        }

    def __repr__(self):
        nick = f" nick={self.account_nick}" if self.account_nick else ""
        return f"<SessionHealth {self.status_label}{nick} cookies={self.cookies_count}>"
