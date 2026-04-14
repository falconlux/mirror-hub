"""Example login plugin template.

Each plugin must define:
- PLATFORM: str — platform name to match in config
- detect_state(page, config) -> str — 'logged_in' | 'need_login' | 'captcha' | 'other'
- auto_fill(page, account, password, config) -> None — fill login form
"""

PLATFORM = 'example'


async def detect_state(page, config):
    """Detect login state from current page"""
    url = page.url or ''
    if 'login' in url:
        return 'need_login'
    return 'logged_in'


async def auto_fill(page, account, password, config):
    """Fill login form and submit"""
    import asyncio
    acc = page.locator('input[type="text"]')
    pwd = page.locator('input[type="password"]')
    if await acc.count() > 0:
        await acc.fill(account)
        await pwd.fill(password)
        btn = page.locator('button[type="submit"]')
        if await btn.count() > 0:
            await btn.click()
