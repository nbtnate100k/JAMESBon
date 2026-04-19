# Pluxo

Site + admin Telegram bot, all in one Python service.

The Flask app serves the website at `/` and exposes the API the page uses
(`/api/products`, balances, checkout, dice, blackjack). On startup it also runs
the Telegram admin bot in a background thread.

---

## 1. Push to GitHub

Upload **all** of these files to your GitHub repo (root of the repo):

- `pluxo_backend.py`
- `index (27).html`
- `Procfile`
- `nixpacks.toml`
- `railway.toml`
- `requirements.txt`
- `runtime.txt`
- `.gitignore`

> Do **not** upload `.env`, `data/`, or `__pycache__/` (they’re ignored by `.gitignore`).

### Easiest way (no Git install)

1. Create a new public/private repo on GitHub.
2. Click **Add file → Upload files**.
3. Drag the files above into the page.
4. Click **Commit changes**.

When you change anything later, drop the new files in again and **Commit**;
Railway will auto-redeploy.

---

## 2. Deploy on Railway

1. Sign in at [railway.app](https://railway.app).
2. **New Project → Deploy from GitHub repo →** pick this repo.
3. Open the service → **Variables** and add:

| Variable | Value |
|----------|-------|
| `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
| `OWNER_TELEGRAM_ID` | Your numeric Telegram user id |
| `PLUXO_WEBHOOK_SECRET` | `pluxo_secret_2024` (or any string; must match the HTML) |

> Don’t know your Telegram ID? Send any message to your bot, then open
> `https://YOUR-APP.up.railway.app/telegram-status` once deployed and check
> Railway logs — your update id is printed there. Or message
> [@userinfobot](https://t.me/userinfobot) on Telegram.

4. Railway builds and starts automatically. After ~1 minute, open:
   - `https://YOUR-APP.up.railway.app/` → the site loads.
   - `https://YOUR-APP.up.railway.app/telegram-status` → JSON with
     `"bot_thread_alive": true` confirms the bot is polling.

5. In Telegram, open your bot → **/start** → admin menu appears.

---

## 3. Telegram commands

```
/balance <user>              View balance
/setbalance <user> <amt>     Set balance
/addbalance <user> <amt>     Add balance
/removebalance <user> <amt>  Remove balance
/users                       List all users

/stock <price> <cards>       Add cards to shop. Multiple: separate with ;;
/removestockslot <n,n..>     Remove stock by id
/clearstock                  Clear all shop stock

/addadmin <id>               Owner only
/removeadmin <id>            Owner only
/admins                      List admins
```

Stock added via `/stock` shows up on the website at `/api/products` (no need
to edit the HTML).

---

## 4. Local development (optional)

```bash
pip install -r requirements.txt
copy env.example .env       # fill in TELEGRAM_BOT_TOKEN and OWNER_TELEGRAM_ID
python pluxo_backend.py
```

Open `http://127.0.0.1:5000/`.

> If your bot is also running on Railway with the same token, set
> `DISABLE_TELEGRAM_BOT=1` in your local `.env`. Telegram only allows **one**
> long-poll per token.

A `start-localhost.bat` is included so you can double-click to run it on Windows.

---

## 5. Notes

- **Persistence**: Balances and stock are saved to `data/state.json` inside the
  Railway container. They’ll reset on redeploy unless you mount a Railway
  Volume at `/app/data`.
- **Health check**: `GET /pluxo-ok` returns the running app folder + which HTML
  it’s serving.
- **CORS**: API routes accept any origin so you can also host the HTML on
  GitHub Pages and point its `RAILWAY_API_URL` at the Railway URL.
