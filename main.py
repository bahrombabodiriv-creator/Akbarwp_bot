import os
import asyncio
from datetime import datetime, date
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ParseMode
import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Нет BOT_TOKEN. Добавь его в Render → Environment.")


DB_PATH = "bot.db"
TZ = "Europe/Moscow"
ZONE = ZoneInfo(TZ)

bot = Bot(BOT_TOKEN, parse_mode=ParseMode.MARKDOWN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone=ZONE)


# ---------------- DB ----------------

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            nickname TEXT,
            birth TEXT,
            about TEXT
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS reminders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            hour INTEGER,
            minute INTEGER,
            text TEXT,
            enabled INTEGER DEFAULT 1
        )""")
        await db.commit()


async def save_user(user: types.User):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO users(user_id, username)
        VALUES(?, ?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username
        """, (user.id, user.username))
        await db.commit()


async def get_users():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, username, nickname FROM users")
        return await cur.fetchall()


async def get_profile(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM users WHERE user_id=?", (uid,))
        return await cur.fetchone()


async def set_nick(uid, nick):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO users(user_id) VALUES(?) ON CONFLICT DO NOTHING", (uid,))
        await db.execute("UPDATE users SET nickname=? WHERE user_id=?", (nick, uid))
        await db.commit()


async def set_birth(uid, birth):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO users(user_id) VALUES(?) ON CONFLICT DO NOTHING", (uid,))
        await db.execute("UPDATE users SET birth=? WHERE user_id=?", (birth, uid))
        await db.commit()


async def set_about(uid, about):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO users(user_id) VALUES(?) ON CONFLICT DO NOTHING", (uid,))
        await db.execute("UPDATE users SET about=? WHERE user_id=?", (about, uid))
        await db.commit()


async def add_reminder(chat_id, hour, minute, text):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
        INSERT INTO reminders(chat_id, hour, minute, text)
        VALUES(?, ?, ?, ?)
        """, (chat_id, hour, minute, text))
        await db.commit()
        return cur.lastrowid


async def list_reminders(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
        SELECT id, hour, minute, text, enabled
        FROM reminders WHERE chat_id=?
        """, (chat_id,))
        return await cur.fetchall()


async def enable_rem(rem_id, val):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE reminders SET enabled=? WHERE id=?", (val, rem_id))
        await db.commit()


async def del_rem(rem_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM reminders WHERE id=?", (rem_id,))
        await db.commit()


async def edit_rem_time(rem_id, hour, minute):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE reminders SET hour=?, minute=? WHERE id=?", (hour, minute, rem_id))
        await db.commit()


# ------------ HELPERS ------------

def profile_block(row):
    if not row:
        return "❌ Профиль не найден."
    uid, username, nick, birth, about = row
    nick = nick or username or "—"
    about = about or "—"

    b_text = birth or "—"
    age = "—"
    left = "—"

    if birth:
        b = date.fromisoformat(birth)
        today = date.today()
        age_val = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
        age = f"{age_val}"

        next_b = date(today.year, b.month, b.day)
        if next_b < today:
            next_b = date(today.year + 1, b.month, b.day)
        left = (next_b - today).days

    return (
        "───────────\n"
        "    👤 Профиль\n"
        "───────────\n"
        f"Ник: *{nick}*\n"
        f"Возраст: *{age}*\n"
        f"ДР: *{b_text}*\n"
        f"До ДР: *{left} дней*\n"
        f"О себе: *{about}*\n"
        "───────────"
    )


async def is_admin(chat_id, user_id):
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.is_chat_admin() or m.status == "creator"
    except:
        return False


async def send_ping(chat_id, text):
    users = await get_users()
    mlines = []
    for uid, username, nick in users:
        nm = nick or username or "user"
        nm = nm.replace("[", "\\[").replace("]", "\\]")
        mlines.append(f"[{nm}](tg://user?id={uid})")
    allm = " ".join(mlines)
    msg = (
        "───────────\n"
        "  🔔 Оповещение\n"
        "───────────\n"
        f"{allm}\n"
        f"{text}\n"
        "───────────"
    )
    await bot.send_message(chat_id, msg)


async def load_schedule():
    scheduler.remove_all_jobs()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
        SELECT id, chat_id, hour, minute, text, enabled
        FROM reminders WHERE enabled=1
        """)
        rows = await cur.fetchall()
        for rid, chat, h, m, text, en in rows:
            scheduler.add_job(
                send_ping,
                CronTrigger(hour=h, minute=m),
                args=[chat, text],
                id=f"rem{rid}"
            )

# ---------- HANDLERS ----------

@dp.message()
async def collect(message: types.Message):
    if message.from_user:
        await save_user(message.from_user)


@dp.message(Command("all"))
async def cmd_all(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("❌ Нет прав.")
    return await send_ping(message.chat.id, "")


@dp.message(Command("ник"))
async def cmd_nick(message: types.Message):
    txt = message.text.split(" ", 1)
    if len(txt) < 2:
        return await message.reply("Напиши так: `/ник твой_ник`")
    await set_nick(message.from_user.id, txt[1])
    await message.reply("Ник обновлён ✔")


@dp.message(Command("инфо"))
async def cmd_info(message: types.Message):
    txt = message.text.split(" ", 1)
    if len(txt) < 2:
        return await message.reply("Напиши так: `/инфо текст`")
    await set_about(message.from_user.id, txt[1])
    await message.reply("Информация обновлена ✔")


@dp.message(Command("др"))
async def cmd_birth(message: types.Message):
    txt = message.text.split(" ", 1)
    if len(txt) < 2:
        return await message.reply("Напиши дату так: `/др 2005-06-20`")
    try:
        date.fromisoformat(txt[1])
    except:
        return await message.reply("Формат ДР: YYYY-MM-DD")
    await set_birth(message.from_user.id, txt[1])
    await message.reply("ДР установлена ✔")


@dp.message(Command("кдр"))
async def cmd_kdr(message: types.Message):
    uid = message.from_user.id
    if message.reply_to_message:
        uid = message.reply_to_message.from_user.id
    pr = await get_profile(uid)
    return await message.reply(profile_block(pr))


@dp.message(Command("профиль"))
async def cmd_prof(message: types.Message):
    uid = message.from_user.id
    if message.reply_to_message:
        uid = message.reply_to_message.from_user.id
    pr = await get_profile(uid)
    return await message.reply(profile_block(pr))


@dp.message(Command("упом"))
async def cmd_upom(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("❌ Нет прав.")

    if "(" not in message.text or ")" not in message.text:
        return await message.reply("Используй: `/упом(19:00)`\nНа следующей строке текст.")

    try:
        t = message.text.split("(")[1].split(")")[0]
        h, m = map(int, t.split(":"))
    except:
        return await message.reply("Время в формате HH:MM")

    lines = message.text.split("\n")
    if len(lines) < 2:
        return await message.reply("Напиши текст на следующей строке.")
    text = lines[1]

    rem_id = await add_reminder(message.chat.id, h, m, text)
    await load_schedule()
    
    await message.reply(f"Напоминание создано ✔\nID: `{rem_id}`")
    await send_ping(message.chat.id, text)


@dp.message(Command("список"))
async def cmd_list(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("❌ Нет прав.")

    rows = await list_reminders(message.chat.id)
    if not rows:
        return await message.reply("Нет напоминаний.")

    txt = "Ваши напоминания:\n"
    for rid, h, m, text, en in rows:
        st = "Вкл" if en else "Выкл"
        txt += f"ID {rid} — {h:02d}:{m:02d} — {st}\n{text}\n\n"

    await message.reply(txt)


@dp.message(Command("удалить"))
async def cmd_del(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("❌ Нет прав.")

    parts = message.text.split()
    if len(parts) < 2:
        return await message.reply("Используй: `/удалить 3`")
    rid = int(parts[1])
    await del_rem(rid)
    await load_schedule()
    await message.reply("Удалено ✔")


@dp.message(Command("выключить"))
async def cmd_off(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("❌ Нет прав.")

    rid = int(message.text.split()[1])
    await enable_rem(rid, 0)
    await load_schedule()
    await message.reply("Отключено ✔")


@dp.message(Command("включить"))
async def cmd_on(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("❌ Нет прав.")

    rid = int(message.text.split()[1])
    await enable_rem(rid, 1)
    await load_schedule()
    await message.reply("Включено ✔")


@dp.message(Command("время"))
async def cmd_time(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("❌ Нет прав.")

    parts = message.text.split()
    if len(parts) != 3:
        return await message.reply("Используй: `/время 3 19:00`")

    rid = int(parts[1])
    h, m = map(int, parts[2].split(":"))
    await edit_rem_time(rid, h, m)
    await load_schedule()
    await message.reply("Время изменено ✔")


# ---------------- RUN ----------------

async def main():
    await init_db()
    await load_schedule()
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
