import os
import datetime
import requests
from dotenv import load_dotenv

# Load env files
load_dotenv()

def get_env_var(name: str, default: str = None) -> str:
    """Securely get an environment variable."""
    return os.environ.get(name, default)

def format_utc_to_local(utc_time_str: str) -> str:
    """Convert a UTC ISO string to the system's local timezone and format it."""
    try:
        dt = datetime.datetime.fromisoformat(utc_time_str)
        # astimezone() with no args automatically uses the system local timezone
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return utc_time_str

def format_duration(seconds: float) -> str:
    """Format duration in seconds to a human-readable string."""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"

def post_to_discord(webhook_url: str, content: str = None, embeds: list = None) -> bool:
    """Post a message or embed payload to a Discord webhook."""
    if not webhook_url:
        return False
    
    payload = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds
        
    try:
        # Standard timeout to prevent hanging
        response = requests.post(webhook_url, json=payload, timeout=10)
        return response.status_code in (200, 204)
    except Exception as e:
        print(f"Error posting to Discord Webhook: {e}")
        return False
