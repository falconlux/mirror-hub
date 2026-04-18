"""Playwright 兼容层 — 优先用 rebrowser-playwright（反检测更好），降级到官方 playwright"""
try:
    from rebrowser_playwright.async_api import async_playwright, Page, BrowserContext
except ImportError:
    from playwright.async_api import async_playwright, Page, BrowserContext

__all__ = ["async_playwright", "Page", "BrowserContext"]
