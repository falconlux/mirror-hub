# Mirror Hub — Remote Browser Management

Manage multiple Chrome browser instances remotely through a web interface.

## Features

- **Remote Viewer**: Real-time screenshot streaming + mouse/keyboard/touch forwarding
- **Multi-Profile**: Manage dozens of Chrome profiles with independent sessions
- **Auto Login**: Plugin-based auto-login for different platforms
- **Cookie Persistence**: Auto-save/restore cookies across Chrome restarts
- **Idle Reaper**: Auto-close inactive browsers to save resources
- **Password Protection**: Simple cookie-based auth
- **Zoom & Pan**: Mobile-friendly viewer with pinch-zoom support
- **Restart Button**: One-click browser restart with status feedback

## Quick Start

```bash
# 1. Configure profiles
cp config.example.json config.json
# Edit config.json with your profiles

# 2. Start
python3 hub.py --port 8900

# 3. Access
open http://localhost:8900
```

## Configuration

`config.json`:
```json
{
  "password": "your-password",
  "idle_timeout": 600,
  "profiles": {
    "my-profile": {
      "name": "My Browser",
      "port": 9500,
      "platform": "custom",
      "url": "https://example.com"
    }
  },
  "platforms": {
    "custom": {
      "color": "#888",
      "login_url": "https://example.com/login"
    }
  }
}
```

## Auto-Login Plugins

Create `plugins/my_platform.py`:
```python
async def detect_state(page, platform):
    """Return: 'logged_in', 'need_login', 'captcha', 'other'"""
    ...

async def auto_fill(page, account, password, config):
    """Fill login form and click submit"""
    ...
```

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/{profile}/screenshot` | GET | JPEG screenshot |
| `/{profile}/status` | GET | Connection status |
| `/{profile}/click` | POST | Mouse click |
| `/{profile}/mousedown` | POST | Mouse down |
| `/{profile}/mousemove` | POST | Mouse move |
| `/{profile}/mouseup` | POST | Mouse up |
| `/{profile}/type` | POST | Keyboard type |
| `/{profile}/press` | POST | Keyboard press |
| `/{profile}/scroll` | POST | Mouse wheel |
| `/{profile}/navigate` | POST | Navigate to URL |
| `/{profile}/restart` | POST | Restart Chrome |
| `/api/batch-status` | GET | All profiles status |

## Requirements

- Python 3.10+
- Playwright (`pip install playwright && playwright install chromium`)
- Xvfb (Linux headless)
