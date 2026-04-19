"""
PLUXO API + Telegram admin bot. Stock and balances are shared with the Pluxo HTML app.

Set TELEGRAM_BOT_TOKEN and OWNER_TELEGRAM_ID in .env (never put tokens in HTML).
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, request, send_file
from flask_cors import CORS

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
SHOP_PRODUCTS_JSON = ROOT_DIR / "shop_products.json"


def resolve_index_html() -> Path | None:
    """Find the main Pluxo HTML file next to this script (handles odd names like 'index (27).html')."""
    root = ROOT_DIR
    for name in ("index.html", "index (27).html"):
        p = root / name
        if p.is_file():
            return p
    for p in sorted(root.glob("index*.html")):
        if p.is_file():
            return p
    for p in sorted(root.glob("*.html")):
        if p.is_file():
            return p
    return None
STATE_PATH = DATA_DIR / "state.json"
WEBHOOK_SECRET = os.environ.get("PLUXO_WEBHOOK_SECRET", "pluxo_secret_2024")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_RAW = os.environ.get("OWNER_TELEGRAM_ID", "").strip()
# If "1"/"true"/"yes": do not start Telegram polling (API only). Use when Railway or another PC runs the bot.
_DISABLE = os.environ.get("DISABLE_TELEGRAM_BOT", "").strip().lower()
TELEGRAM_BOT_DISABLED = _DISABLE in ("1", "true", "yes", "on")

state_lock = threading.Lock()
state: dict[str, Any] = {}

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})


def _default_state() -> dict[str, Any]:
    oid: int | None = None
    if OWNER_RAW.isdigit():
        oid = int(OWNER_RAW)
    admins: list[int] = []
    if oid is not None:
        admins.append(oid)
    extra = os.environ.get("ADMIN_TELEGRAM_IDS", "")
    for part in extra.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            i = int(part)
            if i not in admins:
                admins.append(i)
    return {
        "users": {},
        "stock": [],
        "next_product_id": 1,
        "owner_telegram_id": oid,
        "admin_telegram_ids": admins,
        "dice": {"bets": [], "history": []},
        "blackjack": {"matches": [], "history": []},
    }


def load_state() -> None:
    global state
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
    else:
        state = _default_state()
        save_state()
        return
    # Merge env owner into loaded state
    if OWNER_RAW.isdigit():
        oid = int(OWNER_RAW)
        state["owner_telegram_id"] = oid
        lst = state.setdefault("admin_telegram_ids", [])
        if oid not in lst:
            lst.append(oid)


def save_state() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    tmp.replace(STATE_PATH)


def require_secret() -> None:
    if request.headers.get("X-Webhook-Secret") != WEBHOOK_SECRET:
        abort(403)


def norm_user(name: str) -> str:
    return (name or "").strip().lower()


def get_balance_record(username: str) -> dict[str, float]:
    u = norm_user(username)
    users = state.setdefault("users", {})
    if u not in users:
        users[u] = {"balance": 0.0, "totalRecharge": 0.0}
    return users[u]


def extract_bin(card_blob: str) -> str:
    m = re.search(r"\d{6,19}", card_blob.replace(" ", ""))
    if m:
        return m.group()[:6]
    return "000000"


# --- Flask: Pluxo site (same origin as API) ---


def _send_index() -> Any:
    path = resolve_index_html()
    if not path:
        return (
            f"<!DOCTYPE html><html><body style='font-family:system-ui;padding:24px'>"
            f"<p>No <code>*.html</code> found in:</p><pre>{ROOT_DIR}</pre>"
            f"<p>Put <code>index (27).html</code> or <code>index.html</code> next to <code>pluxo_backend.py</code>.</p>"
            "</body></html>",
            404,
            {"Content-Type": "text/html; charset=utf-8"},
        )
    # Response(bytes) avoids rare send_file issues with spaces/parentheses on Windows paths.
    data = path.read_bytes()
    return Response(
        data,
        mimetype="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@app.before_request
def _serve_pluxo_home_before_routing():
    """Guarantee / and /index.html return the site (runs before URL matching)."""
    if request.method not in ("GET", "HEAD"):
        return None
    if request.path not in ("/", "/index.html"):
        return None
    return _send_index()


@app.route("/", methods=["GET", "HEAD"])
def root():
    return _send_index()


@app.route("/index.html", methods=["GET", "HEAD"])
def index_html_alias():
    return _send_index()


@app.get("/pluxo-ok")
def pluxo_ok():
    """Visit this to confirm you are hitting THIS app (not some other server on :5000)."""
    idx = resolve_index_html()
    return jsonify(
        {
            "pluxo": True,
            "folder": str(ROOT_DIR),
            "index_html": str(idx) if idx else None,
        }
    )


@app.get("/shop_products.json")
def shop_products_static():
    """Fallback file the HTML may fetch when the API has no products."""
    if SHOP_PRODUCTS_JSON.is_file():
        return send_file(SHOP_PRODUCTS_JSON, mimetype="application/json", max_age=0)
    return jsonify([])


# --- Flask: products (public) ---


@app.get("/api/products")
def api_products():
    with state_lock:
        return jsonify(state.get("stock", []))


# --- Flask: register ---


@app.post("/api/register")
def api_register():
    require_secret()
    data = request.get_json(force=True, silent=True) or {}
    username = norm_user(data.get("username", ""))
    if not username:
        return jsonify({"ok": False, "error": "username required"}), 400
    with state_lock:
        get_balance_record(username)
        save_state()
    return jsonify({"ok": True, "success": True})


# --- Flask: balance ---


@app.get("/api/balance/<username>")
def api_balance_get(username: str):
    require_secret()
    u = norm_user(username)
    with state_lock:
        rec = get_balance_record(u)
        return jsonify(
            {
                "success": True,
                "balance": float(rec["balance"]),
                "totalRecharge": float(rec.get("totalRecharge", 0)),
            }
        )


@app.post("/api/balance/update")
def api_balance_update():
    require_secret()
    data = request.get_json(force=True, silent=True) or {}
    username = norm_user(data.get("username", ""))
    action = data.get("action", "")
    amount = float(data.get("amount", 0) or 0)
    if not username or action not in ("add", "subtract"):
        return jsonify({"success": False, "error": "bad request"}), 400
    with state_lock:
        rec = get_balance_record(username)
        if action == "subtract":
            if rec["balance"] < amount:
                return jsonify({"success": False, "error": "insufficient"}), 400
            rec["balance"] = round(rec["balance"] - amount, 2)
        else:
            rec["balance"] = round(rec["balance"] + amount, 2)
            rec["totalRecharge"] = round(float(rec.get("totalRecharge", 0)) + amount, 2)
        nb = rec["balance"]
        save_state()
    return jsonify({"success": True, "newBalance": nb})


# --- Flask: checkout ---


@app.post("/api/purchase/checkout")
def api_checkout():
    require_secret()
    data = request.get_json(force=True, silent=True) or {}
    username = norm_user(data.get("username", ""))
    items = data.get("items") or []
    if not username or not isinstance(items, list) or not items:
        return jsonify({"error": "invalid payload"}), 400

    with state_lock:
        rec = get_balance_record(username)
        stock = state.setdefault("stock", [])
        total = 0.0
        resolved: list[dict[str, Any]] = []
        for it in items:
            pid = it.get("productId")
            price = float(it.get("price", 0) or 0)
            row = next((s for s in stock if s.get("id") == pid), None)
            if not row:
                return jsonify({"error": f"product {pid} not found"}), 400
            if abs(float(row.get("price", 0)) - price) > 0.009:
                return jsonify({"error": "price mismatch"}), 400
            total += price
            resolved.append(row)
        if rec["balance"] < total - 0.001:
            return jsonify({"error": "insufficient balance"}), 400
        bought: list[dict[str, Any]] = []
        for row in resolved:
            stock[:] = [s for s in stock if s.get("id") != row.get("id")]
            bought.append(
                {
                    "bin": row.get("bin"),
                    "bank": row.get("bank"),
                    "base": row.get("base"),
                    "price": float(row.get("price", 0)),
                    "refundable": row.get("refundable", True),
                    "full_info": row.get("full_info", ""),
                }
            )
        rec["balance"] = round(rec["balance"] - total, 2)
        nb = rec["balance"]
        save_state()

    return jsonify({"newBalance": nb, "items": bought})


# --- Dice ---


def _dice_roll() -> int:
    return random.randint(1, 6)


def _settle_balances_dice(
    creator: str, opponent: str, amount: float, cr: int, opr: int
) -> tuple[str, float, float]:
    """Returns winner username or 'tie', creator final balance, opponent final balance."""
    c, o = norm_user(creator), norm_user(opponent)
    rec_c = get_balance_record(c)
    rec_o = get_balance_record(o)
    amt = float(amount)
    if cr == opr:
        # Tie: each nets -amt/2 (refund half stake)
        rec_c["balance"] = round(rec_c["balance"] + amt / 2, 2)
        rec_o["balance"] = round(rec_o["balance"] + amt / 2, 2)
        return "tie", rec_c["balance"], rec_o["balance"]
    winner = c if cr > opr else o
    rec_w = get_balance_record(winner)
    # Winner recovers both stakes (+2*amt on top of current after both paid)
    rec_w["balance"] = round(rec_w["balance"] + 2 * amt, 2)
    return (winner if winner == c else o), rec_c["balance"], rec_o["balance"]


@app.post("/api/games/dice/create")
def dice_create():
    require_secret()
    data = request.get_json(force=True, silent=True) or {}
    creator = norm_user(data.get("creator", ""))
    creator_name = data.get("creatorName") or creator
    amount = float(data.get("amount", 0) or 0)
    if not creator or amount <= 0:
        return jsonify({"error": "invalid"}), 400
    with state_lock:
        rec = get_balance_record(creator)
        if rec["balance"] < amount:
            return jsonify({"error": "Insufficient balance"}), 400
        rec["balance"] = round(rec["balance"] - amount, 2)
        nb = rec["balance"]
        bet_id = str(uuid.uuid4())[:12]
        bet = {
            "id": bet_id,
            "creator": creator,
            "creatorName": creator_name,
            "amount": amount,
            "status": "waiting",
            "opponent": None,
            "opponentName": None,
        }
        state["dice"]["bets"].append(bet)
        save_state()
    return jsonify({"newBalance": nb, "bet": bet})


@app.get("/api/games/dice/bets")
def dice_bets():
    require_secret()
    with state_lock:
        return jsonify({"bets": list(state["dice"]["bets"])})


@app.get("/api/games/dice/history")
def dice_history():
    require_secret()
    with state_lock:
        return jsonify({"history": list(state["dice"]["history"])})


@app.post("/api/games/dice/accept")
def dice_accept():
    require_secret()
    data = request.get_json(force=True, silent=True) or {}
    bet_id = data.get("betId", "")
    opponent = norm_user(data.get("opponent", ""))
    opponent_name = data.get("opponentName") or opponent
    with state_lock:
        bets = state["dice"]["bets"]
        bet = next((b for b in bets if b.get("id") == bet_id), None)
        if not bet or bet.get("status") != "waiting":
            return jsonify({"error": "Bet not found"}), 400
        if bet["creator"] == opponent:
            return jsonify({"error": "cannot join own bet"}), 400
        amt = float(bet["amount"])
        rec_o = get_balance_record(opponent)
        if rec_o["balance"] < amt:
            return jsonify({"error": "Insufficient balance"}), 400
        rec_o["balance"] = round(rec_o["balance"] - amt, 2)
        cr, opr = _dice_roll(), _dice_roll()
        winner, bc, bo = _settle_balances_dice(bet["creator"], opponent, amt, cr, opr)
        hist_id = bet_id
        creator_display = bet.get("creatorName") or bet["creator"]
        wname = (
            "Tie"
            if winner == "tie"
            else (creator_display if norm_user(winner) == bet["creator"] else opponent_name)
        )
        hist = {
            "id": hist_id,
            "creator": bet["creator"],
            "creatorName": creator_display,
            "opponent": opponent,
            "opponentName": opponent_name,
            "amount": amt,
            "creatorRoll": cr,
            "opponentRoll": opr,
            "winner": winner,
            "winnerName": wname,
            "status": "completed",
            "completedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "creatorBalanceAfter": bc,
            "opponentBalanceAfter": bo,
        }
        state["dice"]["history"].insert(0, hist)
        bets[:] = [b for b in bets if b.get("id") != bet_id]
        save_state()
        viewer = opponent
        vb = get_balance_record(viewer)["balance"]
        wkey = "tie" if winner == "tie" else norm_user(winner)
        result = {
            "id": hist_id,
            "creator": bet["creator"],
            "opponent": opponent,
            "amount": amt,
            "creatorRoll": cr,
            "opponentRoll": opr,
            "winner": wkey,
        }
        return jsonify({"result": result, "viewerBalance": vb})


@app.post("/api/games/dice/cancel")
def dice_cancel():
    require_secret()
    data = request.get_json(force=True, silent=True) or {}
    bet_id = data.get("betId", "")
    username = norm_user(data.get("username", ""))
    with state_lock:
        bets = state["dice"]["bets"]
        bet = next((b for b in bets if b.get("id") == bet_id), None)
        if not bet or bet.get("creator") != username:
            return jsonify({"error": "cannot cancel"}), 400
        if bet.get("status") != "waiting":
            return jsonify({"error": "not waiting"}), 400
        amt = float(bet["amount"])
        rec = get_balance_record(username)
        rec["balance"] = round(rec["balance"] + amt, 2)
        nb = rec["balance"]
        bets[:] = [b for b in bets if b.get("id") != bet_id]
        save_state()
    return jsonify({"newBalance": nb, "amount": amt})


# --- Blackjack ---


def _bj_score() -> int:
    return random.randint(17, 21)


def _settle_bj_balances(creator: str, opponent: str, amount: float, cs: int, os: int) -> tuple[str, float, float]:
    c, o = norm_user(creator), norm_user(opponent)
    amt = float(amount)
    rec_c = get_balance_record(c)
    rec_o = get_balance_record(o)
    if cs == os:
        rec_c["balance"] = round(rec_c["balance"] + amt / 2, 2)
        rec_o["balance"] = round(rec_o["balance"] + amt / 2, 2)
        return "tie", rec_c["balance"], rec_o["balance"]
    winner = c if cs > os else o
    rec_w = get_balance_record(winner)
    rec_w["balance"] = round(rec_w["balance"] + 2 * amt, 2)
    return (winner if winner == c else o), rec_c["balance"], rec_o["balance"]


@app.post("/api/games/blackjack/create")
def bj_create():
    require_secret()
    data = request.get_json(force=True, silent=True) or {}
    creator = norm_user(data.get("creator", ""))
    creator_name = data.get("creatorName") or creator
    amount = float(data.get("amount", 0) or 0)
    if not creator or amount <= 0:
        return jsonify({"error": "invalid"}), 400
    with state_lock:
        rec = get_balance_record(creator)
        if rec["balance"] < amount:
            return jsonify({"error": "Insufficient balance"}), 400
        rec["balance"] = round(rec["balance"] - amount, 2)
        nb = rec["balance"]
        mid = str(uuid.uuid4())[:12]
        m = {
            "id": mid,
            "creator": creator,
            "creatorName": creator_name,
            "amount": amount,
            "status": "waiting",
            "opponent": None,
            "opponentName": None,
        }
        state["blackjack"]["matches"].append(m)
        save_state()
    return jsonify({"newBalance": nb, "match": m})


@app.get("/api/games/blackjack/matches")
def bj_matches():
    require_secret()
    with state_lock:
        return jsonify({"matches": list(state["blackjack"]["matches"])})


@app.get("/api/games/blackjack/history")
def bj_history():
    require_secret()
    with state_lock:
        return jsonify({"history": list(state["blackjack"]["history"])})


@app.post("/api/games/blackjack/join")
def bj_join():
    require_secret()
    data = request.get_json(force=True, silent=True) or {}
    mid = data.get("matchId", "")
    opponent = norm_user(data.get("opponent", ""))
    opponent_name = data.get("opponentName") or opponent
    with state_lock:
        matches = state["blackjack"]["matches"]
        m = next((x for x in matches if x.get("id") == mid), None)
        if not m or m.get("status") != "waiting":
            return jsonify({"error": "Match not available"}), 400
        if m["creator"] == opponent:
            return jsonify({"error": "cannot join own"}), 400
        amt = float(m["amount"])
        rec_o = get_balance_record(opponent)
        if rec_o["balance"] < amt:
            return jsonify({"error": "Insufficient balance"}), 400
        rec_o["balance"] = round(rec_o["balance"] - amt, 2)
        cs, os_ = _bj_score(), _bj_score()
        winner, bc, bo = _settle_bj_balances(m["creator"], opponent, amt, cs, os_)
        creator_display = m.get("creatorName") or m["creator"]
        wname = (
            "Tie"
            if winner == "tie"
            else (creator_display if norm_user(winner) == m["creator"] else opponent_name)
        )
        hist = {
            "id": mid,
            "creator": m["creator"],
            "creatorName": creator_display,
            "opponent": opponent,
            "opponentName": opponent_name,
            "amount": amt,
            "creatorScore": cs,
            "opponentScore": os_,
            "winner": winner,
            "winnerName": wname,
            "status": "completed",
            "completedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "creatorBalanceAfter": bc,
            "opponentBalanceAfter": bo,
        }
        state["blackjack"]["history"].insert(0, hist)
        matches[:] = [x for x in matches if x.get("id") != mid]
        save_state()
        vb = get_balance_record(opponent)["balance"]
        wkey = "tie" if winner == "tie" else norm_user(winner)
        result = {
            "id": mid,
            "creator": m["creator"],
            "opponent": opponent,
            "amount": amt,
            "creatorScore": cs,
            "opponentScore": os_,
            "winner": wkey,
        }
        return jsonify({"result": result, "viewerBalance": vb})


@app.post("/api/games/blackjack/cancel")
def bj_cancel():
    require_secret()
    data = request.get_json(force=True, silent=True) or {}
    mid = data.get("matchId", "")
    username = norm_user(data.get("username", ""))
    with state_lock:
        matches = state["blackjack"]["matches"]
        m = next((x for x in matches if x.get("id") == mid), None)
        if not m or m.get("creator") != username:
            return jsonify({"error": "cannot cancel"}), 400
        amt = float(m["amount"])
        rec = get_balance_record(username)
        rec["balance"] = round(rec["balance"] + amt, 2)
        nb = rec["balance"]
        matches[:] = [x for x in matches if x.get("id") != mid]
        save_state()
    return jsonify({"newBalance": nb, "amount": amt})


# --- Telegram bot ---


def _is_owner(uid: int) -> bool:
    oid = state.get("owner_telegram_id")
    return oid is not None and int(uid) == int(oid)


def _is_staff(uid: int) -> bool:
    if _is_owner(uid):
        return True
    return int(uid) in [int(x) for x in state.get("admin_telegram_ids", [])]


def _brand_from_bin(bin6: str) -> str:
    if not bin6:
        return "VISA"
    if bin6[0] == "4":
        return "VISA"
    if bin6[0] == "5":
        return "MASTERCARD"
    if bin6.startswith("34") or bin6.startswith("37"):
        return "AMEX"
    return "VISA"


async def tg_start(update, context) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    lines = [
        "🔐 *PLUXO Admin Bot*",
        f"Your Telegram ID: `{uid}`",
        "Set `OWNER_TELEGRAM_ID` in the server `.env` to this value to unlock admin commands.",
        "",
        "Use /help for commands.",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def tg_help(update, context) -> None:
    text = (
        "Balance\n"
        "/balance <user>\n"
        "/setbalance <user> <amt>\n"
        "/addbalance <user> <amt>\n"
        "/removebalance <user> <amt>\n"
        "/users\n\n"
        "Stock (syncs to website /api/products)\n"
        "/stock <price> <card_data> — use ;; between multiple cards\n"
        "/removestockslot <id,id,...>\n"
        "/clearstock\n\n"
        "Owner only\n"
        "/addadmin <telegram_id>\n"
        "/removeadmin <telegram_id>\n"
        "/admins\n\n"
        "/myid — show your Telegram id (put in OWNER_TELEGRAM_ID in .env)"
    )
    await update.message.reply_text(text)


async def tg_myid(update, context) -> None:
    uid = update.effective_user.id
    await update.message.reply_text(f"Your Telegram user id: `{uid}`", parse_mode="Markdown")


async def tg_balance(update, context) -> None:
    if not _is_staff(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /balance <user>")
        return
    u = norm_user(context.args[0])
    with state_lock:
        rec = get_balance_record(u)
        b = rec["balance"]
    await update.message.reply_text(f"{u}: ${b:.2f}")


async def tg_setbalance(update, context) -> None:
    if not _is_staff(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setbalance <user> <amount>")
        return
    u = norm_user(context.args[0])
    amt = float(context.args[1])
    with state_lock:
        rec = get_balance_record(u)
        rec["balance"] = round(amt, 2)
        save_state()
    await update.message.reply_text(f"{u} balance set to ${amt:.2f}")


async def tg_addbalance(update, context) -> None:
    if not _is_staff(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addbalance <user> <amount>")
        return
    u = norm_user(context.args[0])
    amt = float(context.args[1])
    with state_lock:
        rec = get_balance_record(u)
        rec["balance"] = round(rec["balance"] + amt, 2)
        rec["totalRecharge"] = round(float(rec.get("totalRecharge", 0)) + amt, 2)
        save_state()
        nb = rec["balance"]
    await update.message.reply_text(f"{u}: added ${amt:.2f} → ${nb:.2f}")


async def tg_removebalance(update, context) -> None:
    if not _is_staff(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /removebalance <user> <amount>")
        return
    u = norm_user(context.args[0])
    amt = float(context.args[1])
    with state_lock:
        rec = get_balance_record(u)
        rec["balance"] = round(max(0.0, rec["balance"] - amt), 2)
        save_state()
        nb = rec["balance"]
    await update.message.reply_text(f"{u}: removed ${amt:.2f} → ${nb:.2f}")


async def tg_users(update, context) -> None:
    if not _is_staff(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return
    with state_lock:
        names = sorted(state.get("users", {}).keys())
    if not names:
        await update.message.reply_text("No users yet.")
        return
    chunk = names[:80]
    await update.message.reply_text("Users:\n" + "\n".join(chunk))


async def tg_stock(update, context) -> None:
    if not _is_staff(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return
    text = update.message.text or ""
    # /stock <price> <rest>
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        await update.message.reply_text("Usage: /stock <price> <card_data>\nMultiple cards: separate with ;;")
        return
    try:
        price = float(parts[1])
    except ValueError:
        await update.message.reply_text("Invalid price.")
        return
    blob = parts[2].strip()
    cards = [c.strip() for c in blob.split(";;") if c.strip()]
    if not cards:
        await update.message.reply_text("No card lines found.")
        return
    added = 0
    with state_lock:
        stock = state.setdefault("stock", [])
        nid = int(state.get("next_product_id", 1))
        for card in cards:
            bin6 = extract_bin(card)
            row = {
                "id": nid,
                "bin": bin6,
                "brand": _brand_from_bin(bin6),
                "type": "CREDIT",
                "bank": "BANK",
                "base": "2026_US_Base",
                "refundable": True,
                "price": round(price, 2),
                "full_info": card,
            }
            stock.append(row)
            nid += 1
            added += 1
        state["next_product_id"] = nid
        save_state()
    await update.message.reply_text(f"Added {added} card(s) at ${price:.2f} each. Website loads via /api/products.")


async def tg_removestockslot(update, context) -> None:
    if not _is_staff(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /removestockslot <id,id,...>")
        return
    raw = " ".join(context.args)
    ids: set[int] = set()
    for p in re.split(r"[,\s]+", raw):
        p = p.strip()
        if p.isdigit():
            ids.add(int(p))
    if not ids:
        await update.message.reply_text("No valid ids.")
        return
    with state_lock:
        stock = state.setdefault("stock", [])
        before = len(stock)
        stock[:] = [s for s in stock if int(s.get("id", -1)) not in ids]
        removed = before - len(stock)
        save_state()
    await update.message.reply_text(f"Removed {removed} row(s).")


async def tg_clearstock(update, context) -> None:
    if not _is_staff(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return
    with state_lock:
        n = len(state.get("stock", []))
        state["stock"] = []
        save_state()
    await update.message.reply_text(f"Cleared {n} items from shop stock.")


async def tg_addadmin(update, context) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("Owner only.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /addadmin <telegram_id>")
        return
    aid = int(context.args[0])
    with state_lock:
        lst = state.setdefault("admin_telegram_ids", [])
        if aid not in lst:
            lst.append(aid)
        save_state()
    await update.message.reply_text(f"Admin added: {aid}")


async def tg_removeadmin(update, context) -> None:
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("Owner only.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /removeadmin <telegram_id>")
        return
    aid = int(context.args[0])
    oid = state.get("owner_telegram_id")
    if oid is not None and aid == int(oid):
        await update.message.reply_text("Cannot remove owner.")
        return
    with state_lock:
        lst = state.setdefault("admin_telegram_ids", [])
        state["admin_telegram_ids"] = [x for x in lst if int(x) != aid]
        save_state()
    await update.message.reply_text(f"Removed admin {aid} if present.")


async def tg_admins(update, context) -> None:
    if not _is_staff(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return
    oid = state.get("owner_telegram_id")
    with state_lock:
        lst = list(state.get("admin_telegram_ids", []))
    lines = [f"Owner: `{oid}`", "Admins:"]
    for a in lst:
        lines.append(f"• `{a}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def run_telegram_bot() -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN not set - skipping Telegram bot.")
        return
    from telegram.error import Conflict
    from telegram.ext import Application, CommandHandler

    async def post_init(app) -> None:
        # Clear webhook so polling works if this token was used with a webhook before.
        await app.bot.delete_webhook(drop_pending_updates=True)

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    application.add_handler(CommandHandler("start", tg_start))
    application.add_handler(CommandHandler("help", tg_help))
    application.add_handler(CommandHandler("myid", tg_myid))
    application.add_handler(CommandHandler("balance", tg_balance))
    application.add_handler(CommandHandler("setbalance", tg_setbalance))
    application.add_handler(CommandHandler("addbalance", tg_addbalance))
    application.add_handler(CommandHandler("removebalance", tg_removebalance))
    application.add_handler(CommandHandler("users", tg_users))
    application.add_handler(CommandHandler("stock", tg_stock))
    application.add_handler(CommandHandler("removestockslot", tg_removestockslot))
    application.add_handler(CommandHandler("clearstock", tg_clearstock))
    application.add_handler(CommandHandler("addadmin", tg_addadmin))
    application.add_handler(CommandHandler("removeadmin", tg_removeadmin))
    application.add_handler(CommandHandler("admins", tg_admins))

    print("Telegram bot polling…")
    try:
        application.run_polling(drop_pending_updates=False)
    except Conflict:
        print(
            "\n[Telegram] Conflict: another process is already using getUpdates for this bot token.\n"
            "  → Stop the other app (second terminal, Railway, etc.), or run API-only locally:\n"
            "     set DISABLE_TELEGRAM_BOT=1 in your .env\n"
            "  Flask keeps running; only Telegram polling was skipped.\n"
        )


def run_bot_thread() -> None:
    if TELEGRAM_BOT_DISABLED:
        print(
            "DISABLE_TELEGRAM_BOT is set - Telegram bot not started (API only).\n"
            "  Use this when the same token polls elsewhere (e.g. cloud + local)."
        )
        return
    t = threading.Thread(target=run_telegram_bot, name="telegram-bot", daemon=True)
    t.start()


load_state()

# Start Telegram bot once when this module loads (needed for Gunicorn/Railway, not only `python pluxo_backend.py`).
_telegram_bootstrapped = False


def ensure_telegram_bot_started() -> None:
    global _telegram_bootstrapped
    if _telegram_bootstrapped:
        return
    _telegram_bootstrapped = True
    run_bot_thread()


ensure_telegram_bot_started()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    idx = resolve_index_html()
    print("-" * 60)
    print("PLUXO")
    print("  Folder:", ROOT_DIR)
    print("  HTML:  ", idx if idx else "(none - add index.html or index (27).html)")
    print("  Open:  http://127.0.0.1:%s/  or  http://localhost:%s/" % (port, port))
    print("  Routes:", ", ".join(sorted(str(r.rule) for r in app.url_map.iter_rules())))
    print("-" * 60)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
