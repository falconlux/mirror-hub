"""代理基础抽象"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Proxy:
    """代理IP数据类"""
    ip: str
    port: int
    area: str = ""
    real_ip: str = ""
    protocol: str = "http"  # http | socks5
    expire_at: float = 0    # unix timestamp

    @property
    def url(self) -> str:
        return f"{self.protocol}://{self.ip}:{self.port}"

    @property
    def chrome_arg(self) -> str:
        return f"--proxy-server={self.url}"

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expire_at if self.expire_at else False

    def to_dict(self) -> dict:
        return {
            "ip": self.ip, "port": self.port, "area": self.area,
            "url": self.url, "chrome_arg": self.chrome_arg,
            "real_ip": self.real_ip, "expire_at": self.expire_at,
        }


class ProxyProvider(ABC):
    """代理提供商抽象基类"""

    @abstractmethod
    async def get_proxy(self, **kwargs) -> Proxy | None:
        """提取一个代理IP"""
        ...

    @abstractmethod
    async def get_proxies(self, count: int = 1, **kwargs) -> list[Proxy]:
        """提取多个代理IP"""
        ...

    async def get_fresh_proxy(self) -> Proxy | None:
        """获取一个未过期的代理，过期则自动提取新的"""
        return await self.get_proxy()
