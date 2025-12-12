# ---------------- KEEP ALIVE ----------------
# This MUST stay at the very TOP before ANY imports
from keep_alive import keep_alive
keep_alive()   # Start the uptime webserver

# -------------------- IMPORTS --------------------
import os
import json
import time
import logging
import uuid
from typing import Dict, Any, List

# Solana + Solders updated imports
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer

# Telegram imports
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ---------------- logging ----------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------------- config / env ----------------
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")
SOLANA_CLIENT = Client(RPC_URL)

RAW_PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY_JSON", "")
FUNDING_WALLET_ADDRESS = os.getenv("FUNDING_WALLET_ADDRESS", "")
REAL_FAUCET_ENABLED = os.getenv("REAL_FAUCET_ENABLED", "false").lower() in ("1","true","yes")

BOT_TOKEN = os.getenv("8517816526:AAFe9vBEy0t6dY7vYRsIqATQKVDMY216Cn4")
ADMIN_IDS_RAW = os.getenv("6694858410", "")  # comma separated numeric Telegram user IDs
ADMIN_IDS: List[str] = [x.strip() for x in ADMIN_IDS_RAW.split(",") if x.strip()]

ADMIN_CONTACT = "09057542461"
NETWORK_FEE_ADDRESS = "H5v457ZXQKcPivrtrA4aaQyQU8WCGxyrQRZFSCCTLRR2"

DATA_FILE = "users.json"

POINTS_PER_CLAIM = 10
REAL_FAUCET_AMOUNT_SOL = 0.001

POINTS_COOLDOWN = 60
REAL_FAUCET_COOLDOWN = 3600

AUTO_EARN_POINTS = 50
AUTO_EARN_INTERVAL = 86400  # 24 hours

# ---------------- db helpers ----------------
def load_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        base = {"users": {}, "withdrawals": []}
        save_data(base)
        return base
    try:
        return json.load(open(DATA_FILE, "r"))
    except Exception:
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
            "next_auto_earn": 0,
            "ref_by": None,
            "referrals": 0,
            "created_at": int(time.time()),
        }
        save_data(db)

def is_admin(uid: str) -> bool:
    return str(uid) in ADMIN_IDS

# ---------------- funding keypair ----------------
FUNDING_KEYPAIR = None
if RAW_PRIVATE_KEY:
    try:
        parts = json.loads(RAW_PRIVATE_KEY)
        secret = bytes(parts)
        FUNDING_KEYPAIR = Keypair.from_secret_key(secret)
        FUNDING_WALLET_ADDRESS = str(FUNDING_KEYPAIR.public_key)
        log.info("Loaded funding keypair for address %s", FUNDING_WALLET_ADDRESS)
    except Exception as e:
        log.error("Failed to load SOL private key: %s", e)
        FUNDING_KEYPAIR = None
        REAL_FAUCET_ENABLED = False

# ---------------- telegram handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    ensure_user(uid)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏦 Account", callback_data="account"),
         InlineKeyboardButton("👫 Referral", callback_data="referral")],
        [InlineKeyboardButton("✅ Claim Points Faucet", callback_data="claim_points"),
         InlineKeyboardButton("⏳ Daily Auto-Earn", callback_data="auto_earn")],
        [InlineKeyboardButton("📞 Buy SOL", callback_data="buy_sol"),
         InlineKeyboardButton("💸 Network Fee Info", callback_data="fee_info")],
        [InlineKeyboardButton("📤 Withdraw (request)", callback_data="withdraw"),
         InlineKeyboardButton("🌐 Solana Tools", callback_data="sol_tools")],
    ])

    await update.message.reply_text("Welcome — choose an action:", reply_markup=kb)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(update.effective_user.id)
    ensure_user(uid)
    await query.answer()

    if query.data == "account":
        u = db["users"][uid]
        text = (
            f"🏦 Your account:\n"
            f"Points: {u['points']}\n"
            f"Referrals: {u['referrals']}\n"
            f"Created: <code>{u['created_at']}</code>"
        )
        if FUNDING_WALLET_ADDRESS:
            text += f"\nFunding wallet: <code>{FUNDING_WALLET_ADDRESS}</code>"
        await query.edit_message_text(text, parse_mode="HTML")
        return

    if query.data == "buy_sol":
        await query.edit_message_text(
            "📞 <b>Buy SOLANA</b>\n\n"
            f"Contact Admin: {ADMIN_CONTACT}",
            parse_mode="HTML"
        )
        return

    if query.data == "fee_info":
        await query.edit_message_text(
            "💸 Network Fee (informational):\n"
            f"<code>{NETWORK_FEE_ADDRESS}</code>",
            parse_mode="HTML"
        )
        return

    if query.data == "claim_points":
        now = int(time.time())
        last = db["users"][uid].get("last_points", 0)
        if now - last < POINTS_COOLDOWN:
            return await query.edit_message_text(f"⏳ Wait {POINTS_COOLDOWN - (now-last)} seconds.")
        db["users"][uid]["points"] += POINTS_PER_CLAIM
        db["users"][uid]["last_points"] = now
        save_data(db)
        return await query.edit_message_text(f"✅ +{POINTS_PER_CLAIM} points added!")

    if query.data == "auto_earn":
        now = int(time.time())
        next_time = db["users"][uid].get("next_auto_earn", 0)
        if now < next_time:
            return await query.edit_message_text("⏳ You've already claimed your daily auto-earn.")
        db["users"][uid]["points"] += AUTO_EARN_POINTS
        db["users"][uid]["next_auto_earn"] = now + AUTO_EARN_INTERVAL
        save_data(db)
        return await query.edit_message_text(f"🎉 Daily {AUTO_EARN_POINTS} points added!")

    if query.data == "withdraw":
        return await query.edit_message_text(
            "Withdraw Options:\n"
            "/withdraw_points <amount>\n"
            "/withdraw_sol <amount> <address>"
        )

    if query.data == "referral":
        ref_code = uid
        bot_username = context.bot.username
        link = f"https://t.me/{bot_username}?start={ref_code}"
        return await query.edit_message_text(f"Your referral link:\n{link}")

    if query.data == "sol_tools":
        kb2 = InlineKeyboardMarkup([
            [InlineKeyboardButton("Check SOL Balance", callback_data="balance_onchain")],
            [InlineKeyboardButton("Funding Wallet", callback_data="show_fund")],
        ])
        return await query.edit_message_text("Solana Tools:", reply_markup=kb2)

    if query.data == "balance_onchain":
        return await query.edit_message_text("Use /balance <address>")

    if query.data == "show_fund":
        if FUNDING_WALLET_ADDRESS:
            return await query.edit_message_text(f"<code>{FUNDING_WALLET_ADDRESS}</code>", parse_mode="HTML")
        return await query.edit_message_text("Funding wallet not set.")

# ---------------- commands ----------------
async def sendme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    ensure_user(uid)
    if not REAL_FAUCET_ENABLED or FUNDING_KEYPAIR is None:
        return await update.message.reply_text("Real faucet disabled.")
    try:
        addr = context.args[0]
    except:
        return await update.message.reply_text("Usage: /sendme <address>")
    now = int(time.time())
    last = db["users"][uid].get("last_real", 0)
    if now - last < REAL_FAUCET_COOLDOWN:
        return await update.message.reply_text("⏳ Too soon.")
    try:
        pub = PublicKey(addr)
    except:
        return await update.message.reply_text("Invalid address.")
    lamports = int(REAL_FAUCET_AMOUNT_SOL * 1_000_000_000)
    tx = Transaction()
    tx.add(
        transfer(
            TransferParams(
                from_pubkey=FUNDING_KEYPAIR.public_key,
                to_pubkey=pub,
                lamports=lamports
            )
        )
    )
    try:
        res = SOLANA_CLIENT.send_transaction(tx, FUNDING_KEYPAIR)
        if "result" in res:
            db["users"][uid]["last_real"] = now
            save_data(db)
            return await update.message.reply_text(f"Sent {REAL_FAUCET_AMOUNT_SOL} SOL!")
        else:
            return await update.message.reply_text("Failed transaction.")
    except Exception as e:
        return await update.message.reply_text(f"Error: {e}")

async def withdraw_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    ensure_user(uid)
    try:
        amount = int(context.args[0])
    except:
        return await update.message.reply_text("Usage: /withdraw_points <amount>")
    if amount > db["users"][uid]["points"]:
        return await update.message.reply_text("Not enough points.")
    wid = str(uuid.uuid4())
    db["withdrawals"].append({
        "id": wid, "uid": uid, "type": "points", "amount": amount,
        "status": "pending", "address": None, "created_at": int(time.time())
    })
    db["users"][uid]["points"] -= amount
    save_data(db)
    return await update.message.reply_text(f"Request created: {wid}")

async def withdraw_sol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    ensure_user(uid)
    try:
        amount = float(context.args[0])
        addr = context.args[1]
    except:
        return await update.message.reply_text("Usage: /withdraw_sol <amount> <address>")
    wid = str(uuid.uuid4())
    db["withdrawals"].append({
        "id": wid, "uid": uid, "type": "sol", "amount": amount,
        "address": addr, "status": "pending", "created_at": int(time.time())
    })
    save_data(db)
    return await update.message.reply_text(f"Request created: {wid}")

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
    if "result" in res:
        lamports = res["result"]["value"]
        sol = lamports / 1_000_000_000
        return await update.message.reply_text(f"{sol} SOL")
    return await update.message.reply_text("Error getting balance.")

async def start_with_ref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    ensure_user(uid)
    if context.args:
        ref = context.args[0]
        if ref != uid and ref in db["users"] and db["users"][uid]["ref_by"] is None:
            db["users"][uid]["ref_by"] = ref
            db["users"][ref]["referrals"] += 1
            db["users"][ref]["points"] += 20
            save_data(db)
            await update.message.reply_text("Referral added!")
    await start(update, context)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start\n"
        "/balance <addr>\n"
        "/withdraw_points <amount>\n"
        "/withdraw_sol <amount> <addr>\n"
        "/sendme <addr>\n"
        "/help"
    )

# ---------------- admin ----------------
def require_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(str(update.effective_user.id)):
            return await update.message.reply_text("Not admin.")
        return await func(update, context)
    return wrapper

@require_admin
async def admin_list_withdrawals(update, context):
    text = "\n".join([f"{w['id']} | {w['type']} | {w['amount']} | {w['status']}" for w in db["withdrawals"]])
    if not text:
        text = "No withdrawals."
    await update.message.reply_text(text)

@require_admin
async def admin_approve_withdraw(update, context):
    try:
        wid = context.args[0]
    except:
        return await update.message.reply_text("Usage: /approve_withdraw <id>")
    for w in db["withdrawals"]:
        if w["id"] == wid and w["status"] == "pending":
            w["status"] = "approved"
            save_data(db)
            return await update.message.reply_text("Approved.")
    await update.message.reply_text("Not found.")

@require_admin
async def admin_reject_withdraw(update, context):
    try:
        wid = context.args[0]
    except:
        return await update.message.reply_text("Usage: /reject_withdraw <id>")
    for w in db["withdrawals"]:
        if w["id"] == wid and w["status"] == "pending":
            if w["type"] == "points":
                db["users"][w["uid"]]["points"] += int(w["amount"])
            w["status"] = "rejected"
            save_data(db)
            return await update.message.reply_text("Rejected.")
    await update.message.reply_text("Not found.")

@require_admin
async def admin_setpoints(update, context):
    try:
        uid, amount = context.args
        amount = int(amount)
    except:
        return await update.message.reply_text("Usage: /setpoints <user_id> <points>")
    ensure_user(uid)
    db["users"][uid]["points"] = amount
    save_data(db)
    await update.message.reply_text("Done.")

@require_admin
async def admin_broadcast(update, context):
    msg = " ".join(context.args)
    bot = context.application.bot
    count = 0
    for uid in db["users"]:
        try:
            await bot.send_message(int(uid), msg)
            count += 1
        except:
            pass
    await update.message.reply_text(f"Sent to {count} users.")

@require_admin
async def admin_stats(update, context):
    total = len(db["users"])
    pending = len([w for w in db["withdrawals"] if w["status"] == "pending"])
    await update.message.reply_text(f"Users: {total}\nPending: {pending}")

# ---------------- auto earn ----------------
async def auto_earn_job(context):
    now = int(time.time())
    updated = False
    for uid, u in db["users"].items():
        prev = u.get("next_auto_earn", 0)
        if now >= prev:
            u["points"] = u.get("points", 0) + AUTO_EARN_POINTS
            u["next_auto_earn"] = now + AUTO_EARN_INTERVAL
            updated = True
    if updated:
        save_data(db)

# ---------------- run bot ----------------
# ------------- run bot -------------
async def main():
    if not BOT_TOKEN:
        log.error("BOT TOKEN missing")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_with_ref))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("sendme", sendme))
    app.add_handler(CommandHandler("withdraw_points", withdraw_points))
    app.add_handler(CommandHandler("balance", balance_cmd))

    app.add_handler(CommandHandler("list_withdrawals", admin_list_withdrawals))
    app.add_handler(CommandHandler("approve_withdraw", admin_approve_withdraw))
    app.add_handler(CommandHandler("reject_withdraw", admin_reject_withdraw))
    app.add_handler(CommandHandler("setpoints", admin_setpoints))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CommandHandler("stats", admin_stats))

    app.add_handler(CallbackQueryHandler(button))

    job_queue = app.job_queue
    job_queue.run_repeating(auto_earn_job, interval=AUTO_EARN_INTERVAL, first=10)

    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
