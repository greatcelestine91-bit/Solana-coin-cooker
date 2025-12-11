# main.py
import os
import json
import time
import logging
from typing import Dict, Any, List
import asyncio
import uuid

from solana.rpc.api import Client
from solana.keypair import Keypair
from solana.publickey import PublicKey
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer

from telegram import (
    InlineKeyboardMarkup, InlineKeyboardButton, Update
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes
)

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------- Config (env vars) ----------
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")
SOLANA_CLIENT = Client(RPC_URL)

RAW_PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY_JSON", "")
FUNDING_WALLET_ADDRESS = os.getenv("FUNDING_WALLET_ADDRESS", "")
REAL_FAUCET_ENABLED = os.getenv("REAL_FAUCET_ENABLED", "false").lower() in ("1", "true", "yes")

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "")  # comma separated user ids (numbers)
ADMIN_IDS: List[str] = [x.strip() for x in ADMIN_IDS_RAW.split(",") if x.strip()]

# Info you asked to display (informational only)
ADMIN_CONTACT = "09057542461"
NETWORK_FEE_ADDRESS = "H5v457ZXQKcPivrtrA4aaQyQU8WCGxyrQRZFSCCTLRR2"

DATA_FILE = "users.json"

# Faucet & earnings config
POINTS_PER_CLAIM = 10
REAL_FAUCET_AMOUNT_SOL = 0.001

POINTS_COOLDOWN = 60          # seconds between manual point claims
REAL_FAUCET_COOLDOWN = 3600   # seconds between manual real faucet claims

AUTO_EARN_POINTS = 5          # points given to active users each 24h
AUTO_EARN_INTERVAL = 86400    # 24 hours in seconds

# ---------- Helpers: load/save ----------
def load_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        # initial structure
        base = {
            "users": {},  # uid -> {points, last_points, last_real, last_auto, ref_by, referrals}
            "withdrawals": []  # list of withdrawal requests
        }
        save_data(base)
        return base
    try:
        return json.load(open(DATA_FILE, "r"))
    except Exception:
        # recover with empty structure
        base = {"users": {}, "withdrawals": []}
        save_data(base)
        return base

def save_data(d: Dict[str, Any]):
    json.dump(d, open(DATA_FILE, "w"), indent=2)

db = load_data()

def ensure_user(uid: str):
    if uid not in db["users"]:
        db["users"][uid] = {
            "points": 0,
            "last_points": 0,
            "last_real": 0,
            "last_auto": 0,
            "ref_by": None,
            "referrals": 0,
            "created_at": int(time.time())
        }
        save_data(db)

def is_admin(uid: str) -> bool:
    return str(uid) in ADMIN_IDS

# ---------- Funding keypair ----------
FUNDING_KEYPAIR = None
if RAW_PRIVATE_KEY:
    try:
        parts = json.loads(RAW_PRIVATE_KEY)
        secret = bytes(parts)
        FUNDING_KEYPAIR = Keypair.from_secret_key(secret)
        FUNDING_WALLET_ADDRESS = str(FUNDING_KEYPAIR.public_key)
        log.info("Loaded funding keypair for address %s", FUNDING_WALLET_ADDRESS)
    except Exception as e:
        log.error("Failed to load SOL private key from env: %s", e)
        FUNDING_KEYPAIR = None
        REAL_FAUCET_ENABLED = False

# ---------- Telegram Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    ensure_user(uid)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏦 Account", callback_data="account"),
         InlineKeyboardButton("👫 Referral", callback_data="referral")],
        [InlineKeyboardButton("✅ Claim Points Faucet", callback_data="claim_points"),
         InlineKeyboardButton("💳 Request Real SOL (if enabled)", callback_data="claim_real")],
        [InlineKeyboardButton("📞 Buy SOL", callback_data="buy_sol"),
         InlineKeyboardButton("💸 Network Fee Info", callback_data="fee_info")],
        [InlineKeyboardButton("📤 Withdraw (request)", callback_data="withdraw"),
         InlineKeyboardButton("🌐 Solana Tools", callback_data="sol_tools")]
    ])

    await update.message.reply_text("Welcome to the legit Solana helper bot.\nChoose an action:", reply_markup=kb)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(update.effective_user.id)
    ensure_user(uid)
    await query.answer()

    # --- account ---
    if query.data == "account":
        u = db["users"][uid]
        text = f"🏦 Your account:\nPoints: {u['points']}\nReferrals: {u['referrals']}\nCreated: <code>{u['created_at']}</code>"
        if FUNDING_WALLET_ADDRESS:
            text += f"\nFunding wallet: <code>{FUNDING_WALLET_ADDRESS}</code>"
        await query.edit_message_text(text, parse_mode="HTML")
        return

    # --- buy sol (info) ---
    if query.data == "buy_sol":
        await query.edit_message_text(
            "📞 <b>Buy SOLANA</b>\n\n"
            "To buy SOL safely, contact admin:\n"
            f"📱 <b>{ADMIN_CONTACT}</b>\n\n"
            "Admin will guide you through a safe purchase.",
            parse_mode="HTML"
        )
        return

    # --- fee info (info) ---
    if query.data == "fee_info":
        await query.edit_message_text(
            "💸 <b>Solana Network Fee Information</b>\n\n"
            "If a network fee is required, you may use the address below (informational):\n\n"
            f"<code>{NETWORK_FEE_ADDRESS}</code>\n\n"
            f"⚠️ Always confirm with admin ({ADMIN_CONTACT}) before sending anything.",
            parse_mode="HTML"
        )
        return

    # --- claim points (manual, respects cooldown) ---
    if query.data == "claim_points":
        now = int(time.time())
        last = db["users"][uid].get("last_points", 0)
        if now - last < POINTS_COOLDOWN:
            await query.edit_message_text(f"⏳ Wait {POINTS_COOLDOWN - (now-last)}s before claiming again.")
            return
        db["users"][uid]["points"] += POINTS_PER_CLAIM
        db["users"][uid]["last_points"] = now
        save_data(db)
        await query.edit_message_text(f"✅ You received {POINTS_PER_CLAIM} points. Total: {db['users'][uid]['points']}")
        return

    # --- claim real faucet (informational/actionable) ---
    if query.data == "claim_real":
        if not REAL_FAUCET_ENABLED or FUNDING_KEYPAIR is None:
            await query.edit_message_text("⚠️ Real faucet is not enabled on this bot.")
            return
        now = int(time.time())
        last = db["users"][uid].get("last_real", 0)
        if now - last < REAL_FAUCET_COOLDOWN:
            await query.edit_message_text(f"⏳ Wait {REAL_FAUCET_COOLDOWN - (now-last)}s before requesting SOL again.")
            return
        await query.edit_message_text("Send /sendme <YOUR_SOLANA_ADDRESS> to receive SOL (if enabled).")
        return

    # --- withdraw request (creates pending withdrawal) ---
    if query.data == "withdraw":
        await query.edit_message_text(
            "To request withdrawal:\n"
            "- Points -> /withdraw_points <amount>\n"
            "- SOL -> /withdraw_sol <amount> <address>\n\n"
            "Requests become pending and admin must approve."
        )
        return

    # --- referral info/button ---
    if query.data == "referral":
        ref_code = uid
        bot_username = context.bot.username or "<bot>"
        invite_link = f"https://t.me/{bot_username}?start={ref_code}"
        await query.edit_message_text(f"Share this link to get referrals:\n{invite_link}\nYou'll earn points per referral.")
        return

    # --- sol tools ---
    if query.data == "sol_tools":
        kb2 = InlineKeyboardMarkup([
            [InlineKeyboardButton("Check On-Chain SOL Balance", callback_data="balance_onchain"),
             InlineKeyboardButton("Show Funding Wallet (if set)", callback_data="show_fund")],
            [InlineKeyboardButton("Back", callback_data="back")]
        ])
        await query.edit_message_text("Solana Tools:", reply_markup=kb2)
        return

    if query.data == "balance_onchain":
        await query.edit_message_text("Use /balance <SOL_ADDRESS> to check on-chain balance.")
        return

    if query.data == "show_fund":
        if FUNDING_WALLET_ADDRESS:
            await query.edit_message_text(f"Funding wallet address: <code>{FUNDING_WALLET_ADDRESS}</code>", parse_mode="HTML")
        else:
            await query.edit_message_text("Funding wallet is not configured.")
        return

    if query.data == "back":
        await query.edit_message_text("Back to main menu. Send /start to reopen menu.")
        return

# ---------- Commands: sendme, withdraws, balance ----------

async def sendme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    ensure_user(uid)
    if not REAL_FAUCET_ENABLED or FUNDING_KEYPAIR is None:
        return await update.message.reply_text("Real faucet not enabled.")
    try:
        addr = context.args[0]
    except:
        return await update.message.reply_text("Usage: /sendme <YourSolanaAddress>")
    now = int(time.time())
    last = db["users"][uid].get("last_real", 0)
    if now - last < REAL_FAUCET_COOLDOWN:
        return await update.message.reply_text(f"Wait {REAL_FAUCET_COOLDOWN - (now-last)}s before requesting again.")
    try:
        to_pub = PublicKey(addr)
    except:
        return await update.message.reply_text("Invalid address.")
    lamports = int(REAL_FAUCET_AMOUNT_SOL * 1_000_000_000)
    try:
        tx = Transaction()
        tx.add(
            transfer(
                TransferParams(
                    from_pubkey=FUNDING_KEYPAIR.public_key,
                    to_pubkey=to_pub,
                    lamports=lamports
                )
            )
        )
        res = SOLANA_CLIENT.send_transaction(tx, FUNDING_KEYPAIR)
        if res.get("result"):
            db["users"][uid]["last_real"] = now
            save_data(db)
            return await update.message.reply_text(f"✅ Sent {REAL_FAUCET_AMOUNT_SOL} SOL to <code>{addr}</code>.\nTx: <code>{res['result']}</code>", parse_mode="HTML")
        else:
            return await update.message.reply_text(f"❌ Failed to send SOL. Response: {res}")
    except Exception as e:
        log.exception("sendme tx failed")
        return await update.message.reply_text(f"❌ Error sending: {e}")

# --- withdraw_points (creates pending withdrawal entry) ---
async def withdraw_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    ensure_user(uid)
    try:
        amount = int(context.args[0])
    except:
        return await update.message.reply_text("Usage: /withdraw_points <amount>")
    if amount > db["users"][uid]["points"]:
        return await update.message.reply_text("Not enough points.")
    # create pending withdrawal (points)
    wid = str(uuid.uuid4())
    req = {
        "id": wid,
        "uid": uid,
        "type": "points",
        "amount": amount,
        "address": None,
        "status": "pending",
        "created_at": int(time.time()),
        "handled_by": None,
        "handled_at": None
    }
    db["withdrawals"].append(req)
    # temporarily reserve points (deduct from available until approved/rejected)
    db["users"][uid]["points"] -= amount
    save_data(db)
    await update.message.reply_text(f"✅ Withdrawal request recorded (points). Request ID: {wid}. Admin will review.")

# --- withdraw_sol (request only, admin must approve and perform transfer) ---
async def withdraw_sol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    ensure_user(uid)
    try:
        amount = float(context.args[0])
        addr = context.args[1]
    except:
        return await update.message.reply_text("Usage: /withdraw_sol <amount> <address>")
    # record request
    wid = str(uuid.uuid4())
    req = {
        "id": wid,
        "uid": uid,
        "type": "sol",
        "amount": float(amount),
        "address": addr,
        "status": "pending",
        "created_at": int(time.time()),
        "handled_by": None,
        "handled_at": None
    }
    db["withdrawals"].append(req)
    save_data(db)
    await update.message.reply_text(f"✅ Withdrawal request for {amount} SOL recorded. Request ID: {wid}. Admin will review.\nNote: Admin will instruct about any network fee if needed.")

# --- check on-chain balance ---
async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        addr = context.args[0]
    except:
        return await update.message.reply_text("Usage: /balance <address>")
    try:
        pub = PublicKey(addr)
    except:
        return await update.message.reply_text("Invalid address.")
    res = SOLANA_CLIENT.get_balance(pub)
    if "result" in res and res["result"]["value"] is not None:
        lamports = res["result"]["value"]
        sol = lamports / 1_000_000_000
        await update.message.reply_text(f"Balance for <code>{addr}</code>: {sol} SOL", parse_mode="HTML")
    else:
        await update.message.reply_text("Unable to fetch balance.")

# --- referral handling at /start <ref> ---
async def start_with_ref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    ensure_user(uid)
    if context.args:
        ref = context.args[0]
        if ref != uid and ref in db["users"]:
            if db["users"][uid]["ref_by"] is None:
                db["users"][uid]["ref_by"] = ref
                db["users"][ref]["referrals"] = db["users"][ref].get("referrals", 0) + 1
                db["users"][ref]["points"] = db["users"][ref].get("points", 0) + 20
                save_data(db)
                await update.message.reply_text("Referral accepted! Referrer received 20 points.")
    await start(update, context)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "/start - open menu\n"
        "/balance <address> - check SOL balance\n"
        "/withdraw_points <amount>\n"
        "/withdraw_sol <amount> <address>\n"
        "/sendme <address> - claim real SOL (if enabled)\n"
        "/help - show this help"
    )
    await update.message.reply_text(txt)

# ---------- ADMIN COMMANDS ----------
# Admin-only helper
def require_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = str(update.effective_user.id)
        if not is_admin(uid):
            await update.message.reply_text("❌ You are not an admin.")
            return
        return await func(update, context)
    return wrapper

@require_admin
async def admin_list_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # show pending withdrawals
    lines = []
    for w in db["withdrawals"]:
        lines.append(f"{w['id']} | {w['type']} | {w['amount']} | {w['status']} | user:{w['uid']}")
    if not lines:
        await update.message.reply_text("No withdrawal requests.")
    else:
        await update.message.reply_text("\n".join(lines))

@require_admin
async def admin_approve_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        wid = context.args[0]
    except:
        return await update.message.reply_text("Usage: /approve_withdraw <request_id>")
    for w in db["withdrawals"]:
        if w["id"] == wid and w["status"] == "pending":
            w["status"] = "approved"
            w["handled_by"] = str(update.effective_user.id)
            w["handled_at"] = int(time.time())
            save_data(db)
            # If points withdrawal, mark completed (we already reserved points)
            # If SOL withdrawal and FUNDING_KEYPAIR is available, admin may want to run actual transfer separately.
            await update.message.reply_text(f"Approved withdrawal {wid}. Please process payment manually (or extend bot to auto-send).")
            return
    await update.message.reply_text("Request not found or not pending.")

@require_admin
async def admin_reject_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        wid = context.args[0]
    except:
        return await update.message.reply_text("Usage: /reject_withdraw <request_id>")
    for i, w in enumerate(db["withdrawals"]):
        if w["id"] == wid and w["status"] == "pending":
            # if points, return points to user
            if w["type"] == "points":
                db["users"][w["uid"]]["points"] += int(w["amount"])
            w["status"] = "rejected"
            w["handled_by"] = str(update.effective_user.id)
            w["handled_at"] = int(time.time())
            save_data(db)
            await update.message.reply_text(f"Rejected withdrawal {wid}. Points refunded if applicable.")
            return
    await update.message.reply_text("Request not found or not pending.")

@require_admin
async def admin_setpoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target = context.args[0]
        pts = int(context.args[1])
    except:
        return await update.message.reply_text("Usage: /setpoints <user_id> <points>")
    ensure_user(target)
    db["users"][target]["points"] = pts
    save_data(db)
    await update.message.reply_text(f"Set points for {target} -> {pts}")

@require_admin
async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = " ".join(context.args)
    if not msg:
        return await update.message.reply_text("Usage: /broadcast <message>")
    # naive broadcast: iterate users and send DM (could be rate limited)
    sent = 0
    app = context.application
    for uid in list(db["users"].keys()):
        try:
            await app.bot.send_message(int(uid), msg)
            sent += 1
        except Exception:
            continue
    await update.message.reply_text(f"Broadcast sent to {sent} users (attempted).")

@require_admin
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_users = len(db["users"])
    pending = len([w for w in db["withdrawals"] if w["status"] == "pending"])
    approved = len([w for w in db["withdrawals"] if w["status"] == "approved"])
    await update.message.reply_text(f"Users: {total_users}\nPending withdraws: {pending}\nApproved: {approved}")

# ---------- Auto-earn background job ----------
async def auto_earn_job(context: ContextTypes.DEFAULT_TYPE):
    now = int(time.time())
    changed = False
    for uid, u in db["users"].items():
        # give points only if it's been >= AUTO_EARN_INTERVAL since last_auto
        last_auto = u.get("last_auto", 0)
        if now - last_auto >= AUTO_EARN_INTERVAL:
            u["points"] = u.get("points", 0) + AUTO_EARN_POINTS
            u["last_auto"] = now
            changed = True
            # optionally notify user (commented out to avoid spam)
            try:
                await context.application.bot.send_message(int(uid), f"✅ Auto-earn: you received {AUTO_EARN_POINTS} points.")
            except Exception:
                # ignore if bot can't message user
                pass
    if changed:
        save_data(db)

# ---------- Setup and run ----------
async def main():
    if not BOT_TOKEN:
        log.error("8517816526:AAFe9vBEy0t6dY7vYRsIqATQKVDMY216Cn4")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # user commands
    app.add_handler(CommandHandler("start", start_with_ref))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("sendme", sendme))
    app.add_handler(CommandHandler("withdraw_points", withdraw_points))
    app.add_handler(CommandHandler("withdraw_sol", withdraw_sol))
    app.add_handler(CommandHandler("balance", balance_cmd))

    # admin commands
    app.add_handler(CommandHandler("list_withdrawals", admin_list_withdrawals))
    app.add_handler(CommandHandler("approve_withdraw", admin_approve_withdraw))
    app.add_handler(CommandHandler("reject_withdraw", admin_reject_withdraw))
    app.add_handler(CommandHandler("setpoints", admin_setpoints))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CommandHandler("stats", admin_stats))

    # callback handler for inline buttons
    app.add_handler(CallbackQueryHandler(button))

    # schedule auto-earn job: run every AUTO_EARN_INTERVAL seconds
    job_queue = app.job_queue
    job_queue.run_repeating(auto_earn_job, interval=AUTO_EARN_INTERVAL, first=10)

    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
