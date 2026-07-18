"""
Run this ONCE, after your PythonAnywhere web app is live, to tell Telegram
where to send updates. You can run this from your own laptop -- it just
makes one API call to Telegram, it doesn't need to stay running.

Usage:
    export BOT_TOKEN="your_token_here"
    python set_webhook.py https://yourusername.pythonanywhere.com
"""

import os
import sys
import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PASTE_YOUR_TOKEN_HERE")

if len(sys.argv) != 2:
    print("Usage: python set_webhook.py https://yourusername.pythonanywhere.com")
    sys.exit(1)

base_url = sys.argv[1].rstrip("/")
webhook_url = f"{base_url}/webhook/{BOT_TOKEN}"

resp = requests.post(
    f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
    data={"url": webhook_url},
)
print(resp.json())
