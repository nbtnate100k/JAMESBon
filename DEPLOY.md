# Deploy Pluxo to GitHub and Railway

## 1. Push to GitHub

From the project folder (do **not** commit `.env`; it stays local):

```bash
git init
git add .
git commit -m "Pluxo backend and site"
```

Create a new repository on GitHub, then:

```bash
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git branch -M main
git push -u origin main
```

## 2. Railway (24/7 server + Telegram bot)

1. Sign in at [railway.app](https://railway.app) and **New Project** → **Deploy from GitHub** → pick this repo.
2. Railway detects Python and uses `requirements.txt`, `Procfile`, and `railway.toml`.
3. Open the service → **Variables** and add:

| Variable | Notes |
|----------|--------|
| `TELEGRAM_BOT_TOKEN` | From BotFather |
| `OWNER_TELEGRAM_ID` | Your numeric Telegram user id (`/myid` in the bot) |
| `PLUXO_WEBHOOK_SECRET` | Same string as in your HTML (`pluxo_secret_2024` unless you changed it) |
| `DISABLE_TELEGRAM_BOT` | Leave **unset** or `0` on Railway so the bot polls 24/7 |

4. **Do not** set `DISABLE_TELEGRAM_BOT=1` on Railway unless you run the bot only on your PC.
5. After deploy, copy the public URL (e.g. `https://something.up.railway.app`).
6. In `index (27).html`, set `RAILWAY_API_URL` to that URL (search for `RAILWAY_API_URL` in the file) so the shop/balance API calls your Railway server when the page is not opened from localhost.

## 3. Data persistence (optional)

`data/state.json` (balances, stock) lives on the container disk by default and can reset on redeploy. To keep it across deploys, add a **Volume** in Railway mounted at `/app/data` (Linux path; Railway docs show the exact mount path for your stack).

## 4. Health check

Railway uses `GET /pluxo-ok` as a health check. It should return JSON with `"pluxo": true`.
