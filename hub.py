"""Mirror Hub — Remote Browser Management Server

Generic Chrome profile manager with real-time remote viewer.
Profiles and plugins loaded from config.json.

Usage: python3 hub.py [--port 8900] [--config config.json]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import http.cookies
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

# ===== Global State =====
PROFILES: dict[str, dict] = {}
PLATFORMS: dict[str, dict] = {}
PASSWORD = ''
IDLE_TIMEOUT = 600
_AUTH_TOKEN = ''

_pages = {}
_pws = {}
_last_active: dict[str, float] = {}
_bg_launching: set[str] = set()
_filling: set[str] = set()
_last_cookie_save: dict[str, float] = {}
_loop = None


def load_config(path: str):
    global PROFILES, PLATFORMS, PASSWORD, IDLE_TIMEOUT, _AUTH_TOKEN
    with open(path) as f:
        cfg = json.load(f)
    PROFILES = cfg.get('profiles', {})
    PLATFORMS = cfg.get('platforms', {})
    PASSWORD = cfg.get('password', 'mirror123')
    IDLE_TIMEOUT = cfg.get('idle_timeout', 600)
    _AUTH_TOKEN = hashlib.sha256(PASSWORD.encode()).hexdigest()[:32]
    # Add default platform colors
    for name, pcfg in PLATFORMS.items():
        pcfg.setdefault('color', '#888')


# ===== Async Helpers =====

def _ensure_loop():
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        t = threading.Thread(target=_loop.run_forever, daemon=True)
        t.start()
        time.sleep(0.1)


def run_async(coro, timeout=30):
    _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=timeout)


def cdp_alive(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
        return True
    except:
        return False


# ===== Chrome Management =====

COOKIES_DIR = os.path.join(os.path.dirname(__file__), 'saved_cookies')


def launch_chrome(profile_id: str, port: int) -> bool:
    """Launch Chrome for a profile. Returns True if started."""
    # Check if already running
    if cdp_alive(port):
        return True

    profile_dir = os.path.join(os.path.dirname(__file__), 'chrome-profiles', profile_id)
    os.makedirs(profile_dir, exist_ok=True)

    # Clean locks
    for lock in ('SingletonLock', 'SingletonSocket'):
        try: os.remove(os.path.join(profile_dir, lock))
        except FileNotFoundError: pass

    # Kill stale process
    os.system(f"pkill -f 'remote-debugging-port={port}' 2>/dev/null")
    time.sleep(0.5)

    chrome_path = '/usr/bin/google-chrome'
    if sys.platform == 'darwin':
        chrome_path = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

    args = [
        chrome_path,
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={port}",
        "--window-size=2560,1440",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
        "--restore-last-session",
        "--remote-allow-origins=*",
        "--start-maximized",
    ]

    import platform as _plat
    if _plat.system() == 'Linux':
        args.extend(["--no-sandbox", "--disable-dev-shm-usage"])

    import subprocess
    env = dict(os.environ)
    env.setdefault('DISPLAY', ':99')
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            preexec_fn=os.setpgrp, env=env)
    time.sleep(3)

    # Verify CDP + restore cookies
    for _ in range(10):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2)
            _restore_cookies(profile_id, port)
            print(f'[hub] Chrome started: {profile_id} port={port}')
            return True
        except:
            time.sleep(1)

    print(f'[hub] Chrome failed to start: {profile_id} port={port}')
    return False


def save_cookies(profile_id: str, port: int):
    """Save all cookies via CDP WebSocket"""
    os.makedirs(COOKIES_DIR, exist_ok=True)
    try:
        import websocket
        ws_info = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3).read())
        ws_url = ws_info.get('webSocketDebuggerUrl', '')
        ws = websocket.create_connection(ws_url, timeout=10)

        ws.send(json.dumps({'id': 1, 'method': 'Storage.getCookies'}))
        resp = json.loads(ws.recv())
        cookies = resp.get('result', {}).get('cookies', [])

        if not cookies:
            ws.send(json.dumps({'id': 2, 'method': 'Network.getAllCookies'}))
            resp2 = json.loads(ws.recv())
            cookies = resp2.get('result', {}).get('cookies', [])

        ws.close()

        if cookies:
            with open(os.path.join(COOKIES_DIR, f"{profile_id}.json"), 'w') as f:
                json.dump(cookies, f)
            print(f'[hub] Saved {len(cookies)} cookies for {profile_id}')
    except ImportError:
        print('[hub] pip install websocket-client for cookie persistence')
    except Exception as e:
        print(f'[hub] Cookie save failed for {profile_id}: {e}')


def _restore_cookies(profile_id: str, port: int):
    """Restore cookies via CDP WebSocket"""
    cookie_file = os.path.join(COOKIES_DIR, f"{profile_id}.json")
    if not os.path.exists(cookie_file):
        return
    try:
        import websocket
        with open(cookie_file) as f:
            cookies = json.load(f)
        if not cookies:
            return
        ws_info = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3).read())
        ws = websocket.create_connection(ws_info['webSocketDebuggerUrl'], timeout=10)
        for i, c in enumerate(cookies):
            params = {k: c[k] for k in ('name', 'value', 'domain', 'path', 'secure', 'httpOnly') if k in c}
            if c.get('expires', -1) > 0:
                params['expires'] = c['expires']
            if c.get('sameSite'):
                params['sameSite'] = c['sameSite']
            ws.send(json.dumps({'id': i + 1, 'method': 'Network.setCookie', 'params': params}))
            ws.recv()
        ws.close()
        print(f'[hub] Restored {len(cookies)} cookies for {profile_id}')
    except:
        pass


# ===== Page Management =====

async def _get_page(profile_id: str, auto_launch: bool = False):
    """Get or create a page connection for a profile"""
    if profile_id in _pages:
        try:
            _ = _pages[profile_id].url
            return _pages[profile_id]
        except:
            del _pages[profile_id]
            if profile_id in _pws:
                try: await _pws[profile_id].stop()
                except: pass
                del _pws[profile_id]

    cfg = PROFILES.get(profile_id)
    if not cfg:
        return None

    port = cfg['port']

    if not cdp_alive(port):
        if not auto_launch:
            return None
        launch_chrome(profile_id, port)

    if not cdp_alive(port):
        return None

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None

    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    context = browser.contexts[0]

    page = None
    for p in context.pages:
        try:
            url = p.url
            if url and url != "about:blank" and not url.startswith("chrome"):
                page = p
                break
        except:
            continue
    if not page:
        for p in context.pages:
            try:
                _ = p.url
                page = p
                break
            except:
                continue
    if not page:
        page = await context.new_page()

    _pages[profile_id] = page
    _pws[profile_id] = pw

    # Navigate to default URL if blank
    try:
        url = page.url
        if not url or url == 'about:blank' or url.startswith('chrome'):
            default_url = cfg.get('url', '')
            if not default_url:
                platform = cfg.get('platform', '')
                pcfg = PLATFORMS.get(platform, {})
                default_url = pcfg.get('login_url', 'about:blank')
            if default_url and default_url != 'about:blank':
                await page.goto(default_url, wait_until='domcontentloaded', timeout=30000)
    except:
        pass

    return page


def _bg_launch(profile_id: str):
    if profile_id in _bg_launching:
        return
    _bg_launching.add(profile_id)
    def _run():
        try:
            page = run_async(_get_page(profile_id, auto_launch=True))
            if page:
                print(f'[hub] bg-launch done: {profile_id}')
        except Exception as e:
            print(f'[hub] bg-launch error: {profile_id}: {e}')
        finally:
            _bg_launching.discard(profile_id)
    threading.Thread(target=_run, daemon=True).start()


def _maybe_save_cookies(profile_id: str):
    now = time.time()
    if now - _last_cookie_save.get(profile_id, 0) < 300:
        return
    _last_cookie_save[profile_id] = now
    cfg = PROFILES.get(profile_id)
    if not cfg:
        return
    def _run():
        try:
            save_cookies(profile_id, cfg['port'])
        except:
            pass
    threading.Thread(target=_run, daemon=True).start()


# ===== URL Classification =====

def _classify_url(url: str, platform: str) -> str:
    if not url or url == 'about:blank' or url.startswith('chrome'):
        return ''
    if 'login' in url or 'passport' in url:
        return 'login'
    # Platform-specific checks
    pcfg = PLATFORMS.get(platform, {})
    login_url = pcfg.get('login_url', '')
    if login_url:
        from urllib.parse import urlparse as _up
        login_domain = _up(login_url).netloc
        current_domain = _up(url).netloc
        if login_domain and login_domain == current_domain and url.rstrip('/') == login_url.rstrip('/'):
            return 'login'
    for pattern in pcfg.get('login_patterns', []):
        if pattern in url:
            return 'login'
    return 'ok'


# ===== Auto-Login Plugin System =====

_login_plugins = {}

def register_login_plugin(platform: str, plugin):
    """Register a login plugin for a platform.
    Plugin should have: detect_state(page, config) and auto_fill(page, account, password, config)
    """
    _login_plugins[platform] = plugin


async def _try_auto_login(profile_id: str, page):
    """Try auto-login using platform plugin"""
    if profile_id in _filling:
        return
    cfg = PROFILES.get(profile_id)
    if not cfg or not cfg.get('account'):
        return
    platform = cfg.get('platform', '')
    plugin = _login_plugins.get(platform)
    if not plugin:
        return

    _filling.add(profile_id)
    try:
        state = await plugin.detect_state(page, cfg)
        if state in ('logged_in', 'captcha', 'other'):
            return
        await plugin.auto_fill(page, cfg.get('account', ''), cfg.get('password', ''), cfg)
    except Exception as e:
        print(f'[hub] auto-login error {profile_id}: {e}')
    finally:
        _filling.discard(profile_id)


def _bg_fill(profile_id: str, page):
    if profile_id in _filling:
        return
    def _run():
        try:
            run_async(_try_auto_login(profile_id, page))
        except:
            pass
    threading.Thread(target=_run, daemon=True).start()


# ===== HTTP Handler =====

_LOGIN_HTML = '''<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mirror Hub - Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#1a1a1a;display:flex;justify-content:center;align-items:center;height:100vh;font-family:-apple-system,sans-serif}
.box{background:#2C2825;padding:40px;border-radius:16px;width:320px;text-align:center}
.box h2{color:#E8E2DA;margin-bottom:24px;font-size:18px}
.box input{width:100%;padding:10px 14px;border-radius:8px;border:1px solid #4A4340;background:#3D3632;color:#E8E2DA;font-size:14px;margin-bottom:16px}
.box button{width:100%;padding:10px;border-radius:8px;border:none;background:#D4A574;color:#fff;font-size:14px;cursor:pointer;font-weight:600}
.box .err{color:#D85D5D;font-size:12px;margin-bottom:12px}
</style></head><body>
<div class="box">
<h2>Browser Mirror Hub</h2>
<form method="POST" action="/mirror/auth">
<input type="password" name="password" placeholder="Enter password" autofocus>
<div class="err" id="err"></div>
<button type="submit">Enter</button>
</form>
</div>
<script>if(location.search.includes('err=1'))document.getElementById('err').textContent='Wrong password'</script>
</body></html>'''


class HubHandler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _check_auth(self):
        # Bearer token (for SDK/API clients)
        auth = self.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            return auth[7:] == _AUTH_TOKEN
        # Cookie (for browser UI)
        cookie_str = self.headers.get('Cookie', '')
        cookies = http.cookies.SimpleCookie(cookie_str)
        return cookies.get('mirror_token') and cookies['mirror_token'].value == _AUTH_TOKEN

    def _json_response(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip('/').split('/')

        if parts[0] == 'auth':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(_LOGIN_HTML.encode())
            return

        if not self._check_auth():
            self.send_response(302)
            self.send_header('Location', '/mirror/auth')
            self.end_headers()
            return

        if len(parts) == 0 or parts[0] == '':
            self._serve_index()
            return

        profile_id = parts[0]
        action = parts[1] if len(parts) > 1 else ''

        if profile_id == 'api':
            if action == 'batch-status':
                self._serve_batch_status()
            else:
                self.send_error(404)
            return

        if profile_id not in PROFILES:
            self.send_error(404)
            return

        if action == 'screenshot':
            self._serve_screenshot(profile_id)
        elif action == 'status':
            self._serve_status(profile_id)
        elif action == 'cdp':
            self._serve_cdp(profile_id)
        elif action == '' or action == 'index.html':
            self._serve_viewer(profile_id)
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip('/').split('/')

        if parts[0] == 'auth':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode()
            password = ''
            for pair in body.split('&'):
                if pair.startswith('password='):
                    password = urllib.parse.unquote_plus(pair.split('=', 1)[1])
            if password == PASSWORD:
                self.send_response(302)
                self.send_header('Set-Cookie', f'mirror_token={_AUTH_TOKEN}; Path=/mirror; HttpOnly; Max-Age=604800')
                self.send_header('Location', '/mirror/')
                self.end_headers()
            else:
                self.send_response(302)
                self.send_header('Location', '/mirror/auth?err=1')
                self.end_headers()
            return

        if not self._check_auth():
            self._json_response(401, {'error': 'unauthorized'})
            return

        if len(parts) < 2:
            self.send_error(404)
            return

        profile_id = parts[0]
        action = parts[1]

        if profile_id not in PROFILES:
            self.send_error(404)
            return

        _last_active[profile_id] = time.time()

        content_length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(content_length)) if content_length > 0 else {}

        if action == 'restart':
            self._restart_browser(profile_id)
            return

        if action == 'launch':
            self._launch_profile(profile_id)
            return

        if action == 'stop':
            self._stop_profile(profile_id)
            return

        try:
            page = run_async(_get_page(profile_id))
            if not page:
                self._json_response(503, {'error': 'Chrome not running'})
                return

            if action == 'click':
                run_async(page.mouse.click(body.get('x', 0), body.get('y', 0)))
            elif action == 'type':
                run_async(page.keyboard.type(body.get('text', ''), delay=50))
            elif action == 'press':
                run_async(page.keyboard.press(body.get('key', 'Enter')))
            elif action == 'navigate':
                run_async(page.goto(body.get('url', ''), wait_until='domcontentloaded', timeout=30000))
            elif action == 'scroll':
                async def do_scroll():
                    await page.mouse.move(body.get('x', 0), body.get('y', 0))
                    await page.mouse.wheel(body.get('deltaX', 0), body.get('deltaY', 0))
                run_async(do_scroll())
            elif action == 'mousedown':
                run_async(page.mouse.move(body.get('x', 0), body.get('y', 0)))
                run_async(page.mouse.down())
            elif action == 'mousemove':
                run_async(page.mouse.move(body.get('x', 0), body.get('y', 0)))
            elif action == 'mouseup':
                run_async(page.mouse.move(body.get('x', 0), body.get('y', 0)))
                run_async(page.mouse.up())
            else:
                self.send_error(404)
                return

            self._json_response(200, {'ok': True})
        except Exception as e:
            self._json_response(500, {'error': str(e)[:200]})

    def _restart_browser(self, profile_id):
        cfg = PROFILES.get(profile_id)
        if not cfg:
            self._json_response(404, {'error': 'not found'})
            return
        self._json_response(200, {'ok': True})
        def _do():
            port = cfg['port']
            if profile_id in _pages: del _pages[profile_id]
            if profile_id in _pws:
                try: run_async(_pws[profile_id].stop())
                except: pass
                del _pws[profile_id]
            os.system(f"pkill -f 'remote-debugging-port={port}' 2>/dev/null")
            time.sleep(2)
            launch_chrome(profile_id, port)
        threading.Thread(target=_do, daemon=True).start()

    def _serve_cdp(self, profile_id):
        """GET /<profile_id>/cdp — return CDP connection info for SDK clients."""
        cfg = PROFILES[profile_id]
        port = cfg['port']
        self._json_response(200, {
            'profile_id': profile_id,
            'port': port,
            'cdp_url': f'http://127.0.0.1:{port}',
            'alive': cdp_alive(port),
            'platform': cfg.get('platform', ''),
            'fingerprint_index': cfg.get('fingerprint_index', 0),
        })

    def _launch_profile(self, profile_id):
        """POST /<profile_id>/launch — start Chrome for the profile (async, idempotent)."""
        cfg = PROFILES[profile_id]
        port = cfg['port']
        if cdp_alive(port):
            self._json_response(200, {'ok': True, 'already_running': True, 'port': port})
            return
        self._json_response(202, {'ok': True, 'starting': True, 'port': port})
        threading.Thread(target=lambda: launch_chrome(profile_id, port), daemon=True).start()

    def _stop_profile(self, profile_id):
        """POST /<profile_id>/stop — kill Chrome for the profile, clean up connections."""
        cfg = PROFILES[profile_id]
        port = cfg['port']
        self._json_response(200, {'ok': True})
        def _do():
            if profile_id in _pages: del _pages[profile_id]
            if profile_id in _pws:
                try: run_async(_pws[profile_id].stop())
                except: pass
                del _pws[profile_id]
            os.system(f"pkill -f 'remote-debugging-port={port}' 2>/dev/null")
        threading.Thread(target=_do, daemon=True).start()

    def _serve_screenshot(self, profile_id):
        try:
            _last_active[profile_id] = time.time()
            page = run_async(_get_page(profile_id, auto_launch=False))
            if not page:
                _bg_launch(profile_id)
                self.send_error(503, 'Chrome starting...')
                return
            _bg_fill(profile_id, page)
            _maybe_save_cookies(profile_id)
            data = run_async(page.screenshot(type='jpeg', quality=50))
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Cache-Control', 'no-cache')
            self._cors()
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_error(500, str(e)[:100])

    def _serve_status(self, profile_id):
        cfg = PROFILES[profile_id]
        result = {'profile': profile_id, 'name': cfg['name'], 'platform': cfg.get('platform', ''), 'port': cfg['port']}
        try:
            page = run_async(_get_page(profile_id, auto_launch=False))
            if page:
                result['connected'] = True
                result['url'] = page.url
                result['title'] = run_async(page.title())
            else:
                _bg_launch(profile_id)
                result['connected'] = False
        except:
            result['connected'] = False
        self._json_response(200, result)

    def _serve_batch_status(self):
        import concurrent.futures

        def _check(pid, cfg):
            port = cfg['port']
            if pid in _pages:
                try:
                    return {'alive': True, 'login': _classify_url(_pages[pid].url or '', cfg.get('platform', ''))}
                except: pass
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.15)
            try:
                s.connect(('127.0.0.1', port))
                s.close()
            except:
                s.close()
                return {'alive': False, 'login': ''}
            try:
                data = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1).read()
                for p in json.loads(data):
                    u = p.get('url', '')
                    if u and 'about:blank' not in u and not u.startswith('chrome'):
                        return {'alive': True, 'login': _classify_url(u, cfg.get('platform', ''))}
                return {'alive': True, 'login': ''}
            except:
                return {'alive': True, 'login': ''}

        with concurrent.futures.ThreadPoolExecutor(max_workers=30) as pool:
            futs = {pid: pool.submit(_check, pid, cfg) for pid, cfg in PROFILES.items()}
            result = {}
            for pid, f in futs.items():
                try: result[pid] = f.result(timeout=2)
                except: result[pid] = {'alive': False, 'login': ''}
        self._json_response(200, result)

    def _serve_index(self):
        cards = ''
        platform_list = sorted(set(cfg.get('platform', 'other') for cfg in PROFILES.values()))
        platform_json = json.dumps({pid: cfg.get('platform', '') for pid, cfg in PROFILES.items()})

        for platform in platform_list:
            shops = [(pid, cfg) for pid, cfg in PROFILES.items() if cfg.get('platform', 'other') == platform]
            if not shops: continue
            color = PLATFORMS.get(platform, {}).get('color', '#888')
            cards += f'<div class="platform"><div class="ph" style="border-left:4px solid {color}">{platform} <span class="cnt" id="cnt-{platform}"></span></div>'
            for pid, cfg in shops:
                cards += f'''<a href="/mirror/{pid}/" class="card" id="card-{pid}">
                    <span class="dot" id="dot-{pid}"></span>
                    <div class="info"><div class="name">{cfg['name']}</div><div class="meta" id="meta-{pid}">... {pid}</div></div>
                    <span class="arrow">></span></a>'''
            cards += '</div>'

        html = f'''<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mirror Hub</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#F8F6F3;font-family:-apple-system,sans-serif;color:#2C2825;padding:20px;max-width:600px;margin:0 auto}}
h1{{font-size:22px;margin-bottom:4px}}
.sub{{color:#8C8278;font-size:13px;margin-bottom:20px}}
.platform{{margin-bottom:16px}}
.ph{{font-weight:700;font-size:14px;padding:8px 12px;margin-bottom:6px;border-radius:6px;background:#fff}}
.cnt{{color:#8C8278;font-weight:400;font-size:12px}}
.card{{display:flex;align-items:center;gap:12px;padding:12px 16px;background:#fff;border:1px solid #E8E2DA;border-radius:10px;margin-bottom:6px;text-decoration:none;color:#2C2825;transition:box-shadow .2s}}
.card:hover{{box-shadow:0 2px 8px rgba(44,40,37,.08)}}
.dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0;background:#ddd;transition:background .3s}}
.info{{flex:1;min-width:0}}
.name{{font-weight:600;font-size:14px}}
.meta{{font-size:11px;color:#8C8278;margin-top:1px}}
.arrow{{color:#D4A574;font-size:20px;flex-shrink:0}}
</style></head><body>
<h1>Mirror Hub</h1>
<div class="sub">{len(PROFILES)} profiles</div>
{cards}
<script>
const PLATFORMS={platform_json};
async function loadStatus(){{try{{
const r=await fetch('api/batch-status');const data=await r.json();const counts={{}};
for(const[pid,s]of Object.entries(data)){{
const dot=document.getElementById('dot-'+pid);const meta=document.getElementById('meta-'+pid);
if(!dot||!meta)continue;
let label,color;
if(!s.alive){{label='offline';color='#ccc'}}
else if(s.login==='ok'){{label='online';color='#7FB069'}}
else if(s.login==='login'){{label='need login';color='#E8A838'}}
else{{label='running';color='#6BA3D6'}}
dot.style.background=color;meta.textContent=label+' · '+pid;
const plat=PLATFORMS[pid];if(plat){{if(!counts[plat])counts[plat]={{t:0,ok:0}};counts[plat].t++;if(s.login==='ok')counts[plat].ok++}}
}}
for(const[plat,c]of Object.entries(counts)){{const el=document.getElementById('cnt-'+plat);if(el)el.textContent=c.ok+'/'+c.t+' online'}}
}}catch{{}}}}
loadStatus();setInterval(loadStatus,15000);
</script></body></html>'''
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self._cors()
        self.end_headers()
        self.wfile.write(html.encode())

    def _serve_viewer(self, profile_id):
        cfg = PROFILES[profile_id]
        # Load viewer template
        viewer_path = os.path.join(os.path.dirname(__file__), 'viewer.html')
        if os.path.exists(viewer_path):
            with open(viewer_path) as f:
                html = f.read()
        else:
            html = DEFAULT_VIEWER
        html = html.replace('{{PROFILE_ID}}', profile_id).replace('{{PROFILE_NAME}}', cfg['name']).replace('{{PLATFORM}}', cfg.get('platform', ''))
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self._cors()
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        pass


# Default viewer (minimal)
DEFAULT_VIEWER = '''<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{PROFILE_NAME}}</title>
<style>*{margin:0;padding:0;box-sizing:border-box}body{background:#1a1a1a;display:flex;flex-direction:column;height:100vh;font-family:-apple-system,sans-serif}
#toolbar{background:#2C2825;padding:8px 16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
#toolbar .title{color:#E8E2DA;font-weight:600;font-size:14px;flex:1}
#toolbar button{background:#D4A574;color:#fff;border:none;padding:6px 16px;border-radius:6px;cursor:pointer;font-size:13px}
#wrap{flex:1;overflow:hidden;background:#111;position:relative;cursor:default}
#screen{border-radius:4px;user-select:none}
#info{color:#666;font-size:11px;padding:4px 16px;background:#2C2825;text-align:right}</style></head><body>
<div id="toolbar">
<a href="/mirror/" style="color:#D4A574;text-decoration:none;font-size:14px">Back</a>
<span class="title">{{PROFILE_NAME}}</span>
<button onclick="restartBrowser()" style="background:#E85D5D">Restart</button>
</div>
<div id="wrap"><img id="screen" draggable="false"></div>
<div id="info">Connecting...</div>
<script>
const PID='{{PROFILE_ID}}',base=window.location.pathname.replace(/\\/$/,'').replace(/\\/[^\\/]+$/,'');
const img=document.getElementById('screen'),info=document.getElementById('info');
let imgW=1440,imgH=900,frames=0,restarting=false;
function streamFrames(){if(restarting){setTimeout(streamFrames,500);return}
const n=new Image();n.onload=function(){img.src=n.src;imgW=n.naturalWidth;imgH=n.naturalHeight;frames++;streamFrames()};
n.onerror=function(){setTimeout(streamFrames,500)};n.src=base+'/'+PID+'/screenshot?t='+Date.now()}
streamFrames();
function getCoords(e){const wr=document.getElementById('wrap').getBoundingClientRect();return{x:(e.clientX-wr.left)*(imgW/img.offsetWidth),y:(e.clientY-wr.top)*(imgH/img.offsetHeight)}}
const post=(a,d)=>fetch(base+'/'+PID+'/'+a,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}).catch(()=>{});
img.addEventListener('mousedown',e=>{e.preventDefault();post('mousedown',getCoords(e))});
img.addEventListener('mouseup',e=>{const c=getCoords(e);post('mouseup',c);post('click',c)});
img.addEventListener('wheel',e=>{e.preventDefault();const c=getCoords(e);post('scroll',{...c,deltaX:e.deltaX,deltaY:e.deltaY})},{passive:false});
document.addEventListener('keydown',e=>{if(e.target.tagName==='INPUT')return;e.preventDefault();
const km={Enter:'Enter',Backspace:'Backspace',Tab:'Tab',Escape:'Escape',ArrowUp:'ArrowUp',ArrowDown:'ArrowDown',ArrowLeft:'ArrowLeft',ArrowRight:'ArrowRight'};
if(km[e.key])post('press',{key:km[e.key]});else if(e.key.length===1)post('type',{text:e.key})});
async function restartBrowser(){if(!confirm('Restart browser?'))return;restarting=true;info.textContent='Restarting...';
try{await post('restart',{});let t=0;const poll=setInterval(async()=>{t++;info.textContent='Starting... ('+t+'s)';
try{const r=await fetch(base+'/'+PID+'/status');const s=await r.json();
if(s.connected){clearInterval(poll);restarting=false;info.textContent='Restored'}}catch{}
if(t>30){clearInterval(poll);restarting=false;info.textContent='Timeout'}},1000)}catch{restarting=false;info.textContent='Failed'}}
</script></body></html>'''


# ===== Main =====

def main():
    import functools
    global print
    print = functools.partial(__builtins__['print'] if isinstance(__builtins__, dict) else getattr(__builtins__, 'print'), flush=True)

    parser = argparse.ArgumentParser(description='Mirror Hub')
    parser.add_argument('--port', type=int, default=8900)
    parser.add_argument('--config', type=str, default='config.json')
    args = parser.parse_args()

    load_config(args.config)

    # Load login plugins
    plugins_dir = os.path.join(os.path.dirname(__file__), 'plugins')
    if os.path.isdir(plugins_dir):
        sys.path.insert(0, plugins_dir)
        for fname in os.listdir(plugins_dir):
            if fname.endswith('.py') and not fname.startswith('_'):
                mod_name = fname[:-3]
                try:
                    mod = __import__(mod_name)
                    platform = getattr(mod, 'PLATFORM', mod_name)
                    register_login_plugin(platform, mod)
                    print(f'[hub] Loaded plugin: {mod_name} -> {platform}')
                except Exception as e:
                    print(f'[hub] Plugin load failed: {mod_name}: {e}')

    # Idle reaper
    def _idle_reaper():
        while True:
            time.sleep(60)
            now = time.time()
            for pid in list(_last_active.keys()):
                last = _last_active.get(pid, now)
                if now - last > IDLE_TIMEOUT and pid in _pages:
                    cfg = PROFILES.get(pid)
                    if not cfg: continue
                    port = cfg['port']
                    try: save_cookies(pid, port)
                    except: pass
                    if pid in _pages: del _pages[pid]
                    if pid in _pws:
                        try: run_async(_pws[pid].stop())
                        except: pass
                        del _pws[pid]
                    os.system(f"pkill -f 'remote-debugging-port={port}' 2>/dev/null")
                    _last_active.pop(pid, None)
                    print(f'[hub] Idle reaper: closed {pid}')
    threading.Thread(target=_idle_reaper, daemon=True).start()

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
    server = ThreadedHTTPServer(('0.0.0.0', args.port), HubHandler)

    print(f'[hub] Mirror Hub running at http://localhost:{args.port}')
    print(f'[hub] {len(PROFILES)} profiles, {len(_login_plugins)} plugins')
    print(f'[hub] Idle timeout: {IDLE_TIMEOUT}s')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[hub] Shutting down')
        server.shutdown()


if __name__ == '__main__':
    main()
