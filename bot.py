import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# ---------------- CONFIG ----------------

BOT_TOKEN = "9222505585:AAHOySY_3xAC7xw1B_sovNzEA8-vb7ODWaA"
ADMIN_IDS = {7595104128}
DATA_FILE = Path(
    os.getenv(
        "DATA_FILE",
        "/home/Saifuliy/telegram_file_sharing_bot/data.json"
    )
)

BOT_USERNAME = "Expose_Movie_Bot"
AUTO_DELETE_SECONDS = 10 * 60

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---------------- JSON DATABASE ----------------

db_lock = asyncio.Lock()


def load_db():
    if not DATA_FILE.exists():
        return {"links": {}, "pending": {}, "deletions": []}

    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        data = {}

    data.setdefault("links", {})
    data.setdefault("pending", {})
    data.setdefault("deletions", [])
    return data


def save_db(data):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = DATA_FILE.with_suffix(".tmp")

    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    temp_file.replace(DATA_FILE)


def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def readable_size(size):
    if size is None:
        return "Unknown"

    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}"
        value /= 1024

    return f"{value:.2f} TB"


def user_display_name(user):
    return user.full_name or user.username or str(user.id)


def get_file_info(message):
    """Return a normalized file record for supported Telegram messages."""

    if message.document:
        item = message.document
        return {
            "kind": "document",
            "file_id": item.file_id,
            "unique_id": item.file_unique_id,
            "filename": item.file_name or f"document_{item.file_unique_id}",
            "size": item.file_size,
        }

    if message.video:
        item = message.video
        return {
            "kind": "video",
            "file_id": item.file_id,
            "unique_id": item.file_unique_id,
            "filename": item.file_name or f"video_{item.file_unique_id}.mp4",
            "size": item.file_size,
        }

    if message.photo:
        item = message.photo[-1]
        return {
            "kind": "photo",
            "file_id": item.file_id,
            "unique_id": item.file_unique_id,
            "filename": f"photo_{item.file_unique_id}.jpg",
            "size": item.file_size,
        }

    if message.audio:
        item = message.audio
        return {
            "kind": "audio",
            "file_id": item.file_id,
            "unique_id": item.file_unique_id,
            "filename": item.file_name or f"audio_{item.file_unique_id}",
            "size": item.file_size,
        }

    if message.voice:
        item = message.voice
        return {
            "kind": "voice",
            "file_id": item.file_id,
            "unique_id": item.file_unique_id,
            "filename": f"voice_{item.file_unique_id}.ogg",
            "size": item.file_size,
        }

    if message.animation:
        item = message.animation
        return {
            "kind": "animation",
            "file_id": item.file_id,
            "unique_id": item.file_unique_id,
            "filename": item.file_name or f"animation_{item.file_unique_id}.gif",
            "size": item.file_size,
        }

    if message.sticker:
        item = message.sticker
        return {
            "kind": "sticker",
            "file_id": item.file_id,
            "unique_id": item.file_unique_id,
            "filename": f"sticker_{item.file_unique_id}.webp",
            "size": item.file_size,
        }

    return None


# ---------------- KEYBOARDS ----------------

def create_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 Create Link",
                    callback_data="create_link"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="cancel_upload"
                )
            ],
        ]
    )


# ---------------- AUTO DELETE ----------------

async def schedule_deletion(chat_id, message_id):
    async with db_lock:
        data = load_db()
        delete_at = (
            now_utc() + timedelta(seconds=AUTO_DELETE_SECONDS)
        ).isoformat()

        data["deletions"].append({
            "chat_id": chat_id,
            "message_id": message_id,
            "delete_at": delete_at,
        })
        save_db(data)


async def deletion_worker():
    """Persistent queue: pending deletions survive bot restarts."""

    while True:
        try:
            async with db_lock:
                data = load_db()
                current = now_utc()
                remaining = []
                due = []

                for task in data["deletions"]:
                    try:
                        delete_at = datetime.fromisoformat(
                            task["delete_at"]
                        )
                        if delete_at.tzinfo is None:
                            delete_at = delete_at.replace(
                                tzinfo=timezone.utc
                            )

                        if delete_at <= current:
                            due.append(task)
                        else:
                            remaining.append(task)
                    except (KeyError, ValueError, TypeError):
                        continue

                data["deletions"] = remaining
                save_db(data)

            for task in due:
                try:
                    await bot.delete_message(
                        chat_id=int(task["chat_id"]),
                        message_id=int(task["message_id"]),
                    )
                except Exception:
                    pass

        except Exception as error:
            print("Deletion worker error:", error)

        await asyncio.sleep(5)


# ---------------- START / HELP ----------------

@dp.message(CommandStart())
async def start_handler(message: Message):
    args = message.text.split(maxsplit=1)

    if len(args) > 1 and args[1].startswith("file_"):
        link_id = args[1][5:]
        await deliver_shared_link(message, link_id)
        return

    await message.answer(
        "👋 স্বাগতম!\n\n"
        "📤 আমাকে এক বা একাধিক ফাইল পাঠান।\n"
        "সব ফাইল পাঠানো হলে Create Link বাটনে চাপ দিন।\n\n"
        "🔗 আপনার ফাইলের শেয়ার লিংক তৈরি হবে।\n"
        "⏳ বট থেকে পাঠানো ফাইল ও তথ্য ১০ মিনিট পর মুছে যাবে।\n\n"
        "সাহায্য: /help"
    )


@dp.message(Command("help"))
async def help_handler(message: Message):
    await message.answer(
        "📌 ব্যবহারবিধি:\n\n"
        "1. এক বা একাধিক ফাইল পাঠান।\n"
        "2. Create Link বাটনে চাপ দিন।\n"
        "3. তৈরি হওয়া লিংক অন্যদের দিন।\n"
        "4. লিংক খুললে বট ফাইল পাঠাবে।\n\n"
        "/done - লিংক তৈরি\n"
        "/cancel - আপলোড বাতিল\n"
        "/admin - Admin Panel (শুধু অ্যাডমিন)"
    )


# ---------------- RECEIVE FILES ----------------

@dp.message(
    F.photo
    | F.video
    | F.document
    | F.audio
    | F.voice
    | F.animation
    | F.sticker
)
async def receive_file(message: Message):
    item = get_file_info(message)

    if not item:
        await message.answer("এই ফাইলটি সমর্থিত নয়।")
        return

    user_id = str(message.from_user.id)
    item["uploader"] = user_display_name(message.from_user)
    item["uploader_id"] = message.from_user.id
    item["upload_date"] = iso_now()

    async with db_lock:
        data = load_db()
        data["pending"].setdefault(user_id, [])
        data["pending"][user_id].append(item)
        count = len(data["pending"][user_id])
        save_db(data)

    await message.answer(
        f"✅ ফাইল গ্রহণ করা হয়েছে!\n"
        f"📁 {item['filename']}\n"
        f"📦 {readable_size(item['size'])}\n\n"
        f"মোট ফাইল: {count}\n"
        f"আরও ফাইল পাঠাতে পারেন। সব শেষ হলে Create Link চাপুন।",
        reply_markup=create_keyboard(),
    )


# ---------------- CREATE SHARE LINK ----------------

async def create_link_for_user(user_id: int, target_message: Message):
    key = str(user_id)

    async with db_lock:
        data = load_db()
        files = data["pending"].get(key, [])

        if not files:
            await target_message.answer(
                "❌ কোনো ফাইল পাওয়া যায়নি। আগে ফাইল পাঠান।"
            )
            return

        link_id = uuid.uuid4().hex
        data["links"][link_id] = {
            "files": files,
            "uploader": files[0]["uploader"],
            "created_at": iso_now(),
            "views": 0,
        }

        data["pending"].pop(key, None)
        save_db(data)

    link = f"https://t.me/{BOT_USERNAME}?start=file_{link_id}"

    await target_message.answer(
        "🎉 আপনার লিংক তৈরি হয়েছে!\n\n"
        f"📁 মোট ফাইল: {len(files)}\n"
        f"🔗 শেয়ার লিংক:\n{link}\n\n"
        "⚠️ লিংক খুললে বট ফাইল পাঠাবে। "
        "বটের পাঠানো ফাইল ও তথ্য ১০ মিনিট পর মুছে যাবে।"
    )


@dp.callback_query(F.data == "create_link")
async def create_link_callback(callback: CallbackQuery):
    await callback.answer()
    await create_link_for_user(
        callback.from_user.id,
        callback.message
    )


@dp.message(Command("done"))
async def done_handler(message: Message):
    await create_link_for_user(
        message.from_user.id,
        message
    )


@dp.callback_query(F.data == "cancel_upload")
async def cancel_callback(callback: CallbackQuery):
    async with db_lock:
        data = load_db()
        data["pending"].pop(str(callback.from_user.id), None)
        save_db(data)

    await callback.answer("আপলোড বাতিল করা হয়েছে।")
    await callback.message.answer("❌ আপনার অপেক্ষমাণ ফাইলগুলো বাতিল হয়েছে।")


@dp.message(Command("cancel"))
async def cancel_handler(message: Message):
    async with db_lock:
        data = load_db()
        data["pending"].pop(str(message.from_user.id), None)
        save_db(data)

    await message.answer("❌ আপনার অপেক্ষমাণ ফাইলগুলো বাতিল হয়েছে।")


# ---------------- SEND SHARED FILES ----------------

def make_file_caption(item, link_data, link_id):
    upload_date = item.get("upload_date", "Unknown")
    try:
        upload_date = datetime.fromisoformat(
            upload_date
        ).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        pass

    return (
        "╔══════════════════╗\n"
        "       📁 FILE INFORMATION\n"
        "╚══════════════════╝\n\n"
        f"📄 Name: {item['filename']}\n"
        f"👤 Uploader: {item.get('uploader', 'Unknown')}\n"
        f"👁 Link Opens: {link_data['views']}\n"
        f"📦 Size: {readable_size(item.get('size'))}\n"
        f"📅 Upload Date: {upload_date}\n"
        f"🆔 Unique ID: {item['unique_id']}\n\n"
        "⏳ এই বট মেসেজ ১০ মিনিট পর মুছে যাবে।"
    )


async def send_one_file(chat_id, item, link_data, link_id):
    caption = make_file_caption(item, link_data, link_id)
    kind = item["kind"]
    file_id = item["file_id"]

    if kind == "photo":
        sent = await bot.send_photo(
            chat_id, file_id, caption=caption
        )
        await schedule_deletion(chat_id, sent.message_id)

    elif kind == "video":
        sent = await bot.send_video(
            chat_id, file_id, caption=caption
        )
        await schedule_deletion(chat_id, sent.message_id)

    elif kind == "document":
        sent = await bot.send_document(
            chat_id, file_id, caption=caption
        )
        await schedule_deletion(chat_id, sent.message_id)

    elif kind == "audio":
        sent = await bot.send_audio(
            chat_id, file_id, caption=caption
        )
        await schedule_deletion(chat_id, sent.message_id)

    elif kind == "voice":
        sent = await bot.send_voice(
            chat_id, file_id, caption=caption
        )
        await schedule_deletion(chat_id, sent.message_id)

    elif kind == "animation":
        sent = await bot.send_animation(
            chat_id, file_id, caption=caption
        )
        await schedule_deletion(chat_id, sent.message_id)

    elif kind == "sticker":
        sent = await bot.send_sticker(chat_id, file_id)
        await schedule_deletion(chat_id, sent.message_id)

        info = await bot.send_message(chat_id, caption)
        await schedule_deletion(chat_id, info.message_id)


async def deliver_shared_link(message: Message, link_id: str):
    async with db_lock:
        data = load_db()
        link_data = data["links"].get(link_id)

        if not link_data:
            await message.answer(
                "❌ এই লিংকটি পাওয়া যায়নি অথবা এটি সঠিক নয়।"
            )
            return

        link_data["views"] = link_data.get("views", 0) + 1
        files = list(link_data["files"])
        current_link_data = dict(link_data)
        save_db(data)

    warning = await message.answer(
        "📥 আপনার ফাইলগুলো পাঠানো হচ্ছে...\n"
        "⚠️ বটের পাঠানো ফাইল ও তথ্য ১০ মিনিট পর মুছে যাবে।"
    )
    await schedule_deletion(message.chat.id, warning.message_id)

    for item in files:
        try:
            await send_one_file(
                message.chat.id,
                item,
                current_link_data,
                link_id,
            )
        except Exception as error:
            print("File send error:", error)
            error_message = await message.answer(
                f"❌ {item.get('filename', 'একটি ফাইল')} পাঠানো যায়নি।"
            )
            await schedule_deletion(
                message.chat.id,
                error_message.message_id
            )


# ---------------- ADMIN PANEL ----------------

@dp.message(Command("admin"))
async def admin_handler(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ এই প্যানেল শুধু অ্যাডমিনের জন্য।")
        return

    async with db_lock:
        data = load_db()
        links = data["links"]

        total_links = len(links)
        total_files = sum(
            len(x.get("files", [])) for x in links.values()
        )
        total_views = sum(
            x.get("views", 0) for x in links.values()
        )

        recent = sorted(
            links.items(),
            key=lambda x: x[1].get("created_at", ""),
            reverse=True,
        )[:10]

    text = (
        "╔════════════════════╗\n"
        "       👑 ADMIN PANEL\n"
        "╚════════════════════╝\n\n"
        f"🔗 Total Links: {total_links}\n"
        f"📁 Total Files: {total_files}\n"
        f"👁 Total Link Opens: {total_views}\n\n"
        "📋 Recent Links:\n"
    )

    for link_id, item in recent:
        text += (
            f"\n🆔 {link_id[:12]}\n"
            f"👤 {item.get('uploader', 'Unknown')}\n"
            f"📁 {len(item.get('files', []))} files\n"
            f"👁 {item.get('views', 0)} opens\n"
        )

    await message.answer(text)


# ---------------- STARTUP ----------------

async def main():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    asyncio.create_task(deletion_worker())

    print("Bot is starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
  
