# Deploying `webhook_bot_full.py` on PythonAnywhere (Free, No Credit Card)

## 1. Sign up
https://www.pythonanywhere.com/ → free "Beginner" account. No card needed.

## 2. Upload the project
Open a **Bash console** on PythonAnywhere, then either `git clone` your repo
or use the **Files** tab to upload:
- `webhook_bot_full.py`
- `requirements.txt`
- `.env` (create this from `.env.example`, filled with your real values —
  do NOT commit your real `.env` to a public GitHub repo)

Put them all in one folder, e.g. `/home/yourusername/tshirt-bot/`.

## 3. Install dependencies
```
cd tshirt-bot
pip install -r requirements.txt --user
```

## 4. Fill in your `.env`
```
BOT_TOKEN=123456:ABC-your-real-token
ADMIN_CHAT_ID=123456789
BANK_NAME=Commercial Bank of Ethiopia
ACCOUNT_NAME=Your Church Name
ACCOUNT_NUMBER=1000123456789
PRICE=500 ብር
```
`ADMIN_CHAT_ID` should be your personal Telegram numeric ID (or a group's,
if you want multiple people approving). If you're not sure how to get it,
message **@userinfobot** on Telegram and it will reply with your ID.

## 5. Create the web app
- **Web** tab → **Add a new web app** → **Manual configuration** → Python 3.10
- Open the WSGI config file it generates and replace its contents with:
```python
import sys
path = '/home/yourusername/tshirt-bot'
if path not in sys.path:
    sys.path.append(path)

from webhook_bot_full import application
```
(swap `yourusername` for your real PythonAnywhere username)

## 6. Reload
Click **Reload** on the Web tab. Your bot is now live at:
`https://yourusername.pythonanywhere.com`

## 7. Register the webhook with Telegram
From your own machine:
```
python set_webhook.py https://yourusername.pythonanywhere.com
```
(uses the `BOT_TOKEN` env var — export it locally first, or edit the
script directly for a one-off run)

You should see `{"ok":true,"result":true,"description":"Webhook was set"}`.

## 8. Test
Message your bot on Telegram. `/start` should show the language picker,
walk through name → phone → size → payment → screenshot, forward the
screenshot to your `ADMIN_CHAT_ID` with Approve/Reject buttons, and
finally message the user once you approve.

## Checking / exporting data
- `/status` — any user can check their own progress
- `/export` or `/export approved` (admin-only, replies with a name+phone CSV)
- Or query directly via a Bash console:
  ```
  sqlite3 registrations.db "SELECT name, phone, size, status FROM registrations;"
  ```

## Notes specific to this version
- The bot stores conversation progress in PTB's own `context.user_data`,
  which lives in memory. If the PythonAnywhere web app restarts mid-event
  (rare, but possible on a free plan), anyone mid-registration would need
  to send `/start` again — already-approved/pending registrations in the
  database are unaffected.
- Free PythonAnywhere apps don't sleep between requests, so this stays
  responsive (unlike Render's free tier).
- If you ever redeploy to a new URL, re-run `set_webhook.py` with the new
  URL.
