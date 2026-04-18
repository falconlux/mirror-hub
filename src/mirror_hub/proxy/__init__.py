"""Proxy: 代理 IP 工具（目前支持海量IP）。"""
from mirror_hub.proxy.base import Proxy, ProxyProvider
from mirror_hub.proxy.hailiangip import HailiangipProvider, fetch_proxies_simple

__all__ = ["Proxy", "ProxyProvider", "HailiangipProvider", "fetch_proxies_simple"]
