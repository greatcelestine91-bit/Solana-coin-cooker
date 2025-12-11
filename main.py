import os
import json
from solana.rpc.api import Client
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

# --------------------------
# CONFIG
# --------------------------
RPC_URL = "https://api.mainnet-beta.solana.com"
SOLANA_CLIENT = Client(RPC_URL)

YOUR_SOLANA_ADDRESS = "H5v457ZXQKcPivrtrA4aaQyQU8WCGxyrQRZFSCCTLRR2"

DATA_FILE = "users.json"

# --------------------------
# SAVE / LOAD
# --------------------------
def load():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        return json.load(open(DATA_FILE, "r"))
    except:
        return {}

def save(d):
    json.dump(d, open(DATA_FILE, "w"), indent=2)

users = load()

# --------------------------
# START COMMAND
# --------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    if uid not in users:
        users[uid] = {"balance": 0}
        save(users)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 My Balance", callback_data="balance")],
        [InlineKeyboardButton("📥 Deposit SOL", callback_data="deposit")],
        [InlineKeyboardButton("📤 Withdraw SOL", callback_data="withdraw")]
    ])

    await update.message.reply_text(
        "Welcome to your *Solana Wallet Bot* 🚀",
        parse_mode="Markdown",
        reply_markup=kb
    )

# --------------------------
# BUTTON HANDLER
# --------------------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(update.effective_user.id)
    await query.answer()

    if query.data == "balance":
        bal = users[uid]["balance"]
        return await query.edit_message_text(
            f"💰 Your balance: *{bal} SOL*",
            parse_mode="Markdown"
        )

    if query.data == "deposit":
        txt = (
            "📥 *Deposit SOL*\n\n"
            "Send SOL to the address below:\n"
            f"`{YOUR_SOLANA_ADDRESS}`"
        )
        return await query.edit_message_text(txt, parse_mode="Markdown")

    if query.data == "withdraw":
        return await query.edit_message_text(
            "📤 *Withdraw*\nUse:\n`/withdraw 0.1 YOUR_WALLET_ADDRESS`",
            parse_mode="Markdown"
        )

# --------------------------
# WITHDRAW
# --------------------------
async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    try:
        amount = float(context.args[0])
        target = context.args[1]
    except:
        return await update.message.reply_text(
            "Format:\n`/withdraw 0.1 YOUR_ADDRESS`",
            parse_mode="Markdown"
        )

    if amount > users[uid]["balance"]:
        return await update.message.reply_text("❌ Not enough balance.")

    # No real Solana TX here — just mock.
    users[uid]["balance"] -= amount
    save(users)

    await update.message.reply_text(
        f"✅ Withdrawal request received:\n\n"
        f"Amount: *{amount} SOL*\n"
        f"To: `{target}`",
        parse_mode="Markdown"
    )

# --------------------------
# RUN
# --------------------------
async def main():
    TOKEN = os.getenv("8517816526:AAFe9vBEy0t6dY7vYRsIqATQKVDMY216Cn4")
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("withdraw", withdraw))
    app.add_handler(CallbackQueryHandler(button))

    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
