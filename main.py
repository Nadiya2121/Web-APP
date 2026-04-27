import os
import asyncio
import datetime
import uvicorn
import time
import aiohttp
import hmac
import hashlib
import urllib.parse
import secrets

# ==========================================
# 🛑 FIX FOR PYROGRAM EVENT LOOP ERROR
# ==========================================
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())
# ==========================================

from fastapi import FastAPI, Body, Request, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from pydantic import BaseModel
from pyrogram import Client


# ==========================================
# 1. Configuration & Global Variables
# ==========================================
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URI")
OWNER_ID = int(os.getenv("ADMIN_ID", "0"))
APP_URL = os.getenv("APP_URL")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003188773719") 
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin123") 
BOT_USERNAME = "BDMovieZoneBot" # আপনার বটের ইউজারনেম

# Streaming এর জন্য Pyrogram Credentials
API_ID = int(os.getenv("API_ID", "20632324"))
API_HASH = os.getenv("API_HASH", "7472998b241dd149fc2b2167ce045c0e")
DUMP_CHANNEL_ID = int(os.getenv("DUMP_CHANNEL_ID", "-1003974963331")) 

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()
security = HTTPBasic()
pyro_client = Client("streamer_bot", bot_token=TOKEN, api_id=API_ID, api_hash=API_HASH, in_memory=True)

app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"], 
    allow_credentials=True, 
    allow_methods=["*"], 
    allow_headers=["*"]
)

client = AsyncIOMotorClient(MONGO_URL)
db = client['movie_database']

admin_cache = set([OWNER_ID]) 
banned_cache = set() 


# ==========================================
# 2. FSM States (For Uploading Flow)
# ==========================================
class AdminStates(StatesGroup):
    waiting_for_bcast = State()
    waiting_for_reply = State()
    waiting_for_photo = State()
    waiting_for_title = State()
    waiting_for_quality = State() 
    waiting_for_upc_photo = State()
    waiting_for_upc_title = State()


# ==========================================
# 3. Database Initialization & Caching
# ==========================================
async def load_admins():
    admin_cache.clear()
    admin_cache.add(OWNER_ID)
    async for admin in db.admins.find():
        admin_cache.add(admin["user_id"])

async def load_banned_users():
    banned_cache.clear()
    async for b_user in db.banned.find():
        banned_cache.add(b_user["user_id"])

async def init_db():
    await db.movies.create_index([("title", "text")])
    await db.movies.create_index("title")
    await db.movies.create_index("created_at")
    await db.auto_delete.create_index("delete_at")
    await db.users.create_index("joined_at")


# ==========================================
# 4. Security & Authentication Methods
# ==========================================
def validate_tg_data(init_data: str) -> bool:
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        hash_val = parsed_data.pop('hash', None)
        auth_date = int(parsed_data.get('auth_date', 0))
        
        if not hash_val or time.time() - auth_date > 86400: 
            return False
            
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        return calculated_hash == hash_val
    except Exception: 
        return False

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, "admin")
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASS)
    
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Incorrect username or password", 
            headers={"WWW-Authenticate": "Basic"}
        )
    return True


# ==========================================
# 5. Background Tasks (Auto Delete)
# ==========================================
async def auto_delete_worker():
    while True:
        try:
            now = datetime.datetime.utcnow()
            expired_msgs = db.auto_delete.find({"delete_at": {"$lte": now}})
            
            async for msg in expired_msgs:
                try: 
                    await bot.delete_message(chat_id=msg["chat_id"], message_id=msg["message_id"])
                except Exception: 
                    pass
                await db.auto_delete.delete_one({"_id": msg["_id"]})
        except Exception: 
            pass
        await asyncio.sleep(60)


# ==========================================
# 6. Telegram Bot Commands (General & Refer Logic)
# ==========================================
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if uid in banned_cache: 
        return await message.answer("🚫 <b>আপনাকে এই বট থেকে স্থায়ীভাবে ব্যান করা হয়েছে।</b>", parse_mode="HTML")
        
    await state.clear()
    now = datetime.datetime.utcnow()
    
    # Check User & Refer Logic
    user = await db.users.find_one({"user_id": uid})
    if not user:
        args = message.text.split(" ")
        if len(args) > 1 and args[1].startswith("ref_"):
            try:
                referrer_id = int(args[1].split("_")[1])
                if referrer_id != uid:
                    await db.users.update_one({"user_id": referrer_id}, {"$inc": {"refer_count": 1}})
                    # Check if reached 5 refers for VIP
                    ref_user = await db.users.find_one({"user_id": referrer_id})
                    if ref_user and ref_user.get("refer_count", 0) % 5 == 0:
                        current_vip = ref_user.get("vip_until", now)
                        if current_vip < now: current_vip = now
                        new_vip = current_vip + datetime.timedelta(days=1)
                        await db.users.update_one({"user_id": referrer_id}, {"$set": {"vip_until": new_vip}})
                        
                        try:
                            await bot.send_message(referrer_id, "🎉 <b>অভিনন্দন!</b> আপনার ৫ জন রেফার পূর্ণ হয়েছে। আপনাকে ২৪ ঘণ্টার জন্য <b>VIP</b> দেওয়া হয়েছে! এখন আপনি বিনা অ্যাডে মুভি দেখতে পারবেন।", parse_mode="HTML")
                        except: pass
            except Exception: pass

        await db.users.insert_one({
            "user_id": uid,
            "first_name": message.from_user.first_name,
            "joined_at": now,
            "refer_count": 0,
            "vip_until": now - datetime.timedelta(days=1)
        })
    else:
        await db.users.update_one({"user_id": uid}, {"$set": {"first_name": message.from_user.first_name}})
    
    kb = [[types.InlineKeyboardButton(text="🎬 Watch Now", web_app=types.WebAppInfo(url=APP_URL))]]
    markup = types.InlineKeyboardMarkup(inline_keyboard=kb)
    
    if uid in admin_cache:
        text = (
            "👋 <b>হ্যালো অ্যাডমিন!</b>\n\n"
            "⚙️ <b>কমান্ড:</b>\n"
            "🔸 অ্যাড জোন: <code>/setad ID</code> | অ্যাড সংখ্যা: <code>/setadcount সংখ্যা</code>\n"
            "🔸 টেলিগ্রাম: <code>/settg লিংক</code> | 18+: <code>/set18 লিংক</code>\n"
            "🔸 প্রোটেকশন: <code>/protect on</code> বা <code>/protect off</code>\n"
            "🔸 অটো-ডিলিট টাইম: <code>/settime [মিনিট]</code>\n"
            "🔸 স্ট্যাটাস: <code>/stats</code> | ব্রডকাস্ট: <code>/cast</code>\n"
            "🔸 ব্যান: <code>/ban ID</code> | আনব্যান: <code>/unban ID</code>\n"
            "🔸 VIP দিন: <code>/addvip ID দিন</code> | VIP বাতিল: <code>/removevip ID</code>\n"
            "🔸 আপকামিং মুভি অ্যাড: <code>/addupcoming</code>\n"
            "🔸 আপকামিং ডিলিট: <code>/delupcoming</code>\n\n"
            f"🌐 <b>অ্যাডমিন প্যানেল:</b> <a href='{APP_URL}/admin'>এখানে ক্লিক করুন</a>\n"
            "<i>লগিন: admin / admin123</i>\n\n"
            "📥 <b>মুভি অ্যাড করতে প্রথমে ভিডিও বা ডকুমেন্ট ফাইল পাঠান।</b>"
        )
    else: 
        text = f"👋 <b>স্বাগতম {message.from_user.first_name}!</b>\n\nমুভি দেখতে নিচের বাটনে ক্লিক করুন।"
        
    await message.answer(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)

@dp.message(lambda m: m.chat.type == "private" and m.from_user.id not in admin_cache)
async def forward_to_admin(m: types.Message):
    # ইউজার মেসেজ দিলে সেটি অ্যাডমিনের কাছে যাওয়ার জন্য
    try:
        builder = InlineKeyboardBuilder()
        builder.button(text="✍️ রিপ্লাই দিন", callback_data=f"reply_{m.from_user.id}")
        await bot.send_message(OWNER_ID, f"📩 <b>New Message from <a href='tg://user?id={m.from_user.id}'>{m.from_user.first_name}</a></b>:\n\n{m.text or 'Media file'}", parse_mode="HTML", reply_markup=builder.as_markup())
    except Exception: pass


# ==========================================
# 7. Telegram Bot Commands (Admin Settings & VIP)
# ==========================================
def format_views(n):
    if n >= 1000000: return f"{n/1000000:.1f}M".replace(".0M", "M")
    if n >= 1000: return f"{n/1000:.1f}K".replace(".0K", "K")
    return str(n)

@dp.message(Command("stats"))
async def stats_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    uc = await db.users.count_documents({})
    mc = await db.movies.count_documents({})
    now = datetime.datetime.utcnow()
    today_start = datetime.datetime(now.year, now.month, now.day)
    new_users_today = await db.users.count_documents({"joined_at": {"$gte": today_start}})
    
    top_pipeline = [{"$group": {"_id": "$title", "clicks": {"$sum": "$clicks"}}}, {"$sort": {"clicks": -1}}, {"$limit": 5}]
    top_movies = await db.movies.aggregate(top_pipeline).to_list(5)
    
    top_movies_text = "".join(f"{idx}. {mv['_id'][:20]}... - <b>{format_views(mv['clicks'])} views</b>\n" for idx, mv in enumerate(top_movies, 1))
    
    text = (f"📊 <b>অ্যাডভান্সড স্ট্যাটাস:</b>\n\n👥 মোট ইউজার: <code>{uc}</code>\n🟢 আজকের নতুন ইউজার: <code>{new_users_today}</code>\n"
            f"🎬 মোট ফাইল আপলোড: <code>{mc}</code>\n\n🔥 <b>টপ ৫ মুভি/সিরিজ:</b>\n{top_movies_text if top_movies_text else 'কোনো মুভি নেই'}")
    await m.answer(text, parse_mode="HTML")

@dp.message(Command("ban"))
async def ban_user_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        target_uid = int(m.text.split()[1])
        if target_uid in admin_cache: return await m.answer("⚠️ অ্যাডমিনকে ব্যান করা যাবে না!")
        await db.banned.update_one({"user_id": target_uid}, {"$set": {"user_id": target_uid}}, upsert=True)
        banned_cache.add(target_uid)
        await m.answer(f"🚫 ইউজার <code>{target_uid}</code> কে ব্যান করা হয়েছে!", parse_mode="HTML")
    except Exception: 
        await m.answer("⚠️ সঠিক নিয়ম: <code>/ban ইউজার_আইডি</code>", parse_mode="HTML")

@dp.message(Command("unban"))
async def unban_user_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        target_uid = int(m.text.split()[1])
        await db.banned.delete_one({"user_id": target_uid})
        banned_cache.discard(target_uid)
        await m.answer(f"✅ ইউজার <code>{target_uid}</code> আনব্যান হয়েছে!", parse_mode="HTML")
    except Exception: 
        await m.answer("⚠️ সঠিক নিয়ম: <code>/unban ইউজার_আইডি</code>", parse_mode="HTML")

@dp.message(Command("setadcount"))
async def set_ad_count_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        count = int(m.text.split(" ")[1])
        count = max(1, count)
        await db.settings.update_one({"id": "ad_count"}, {"$set": {"count": count}}, upsert=True)
        await m.answer(f"✅ অ্যাড দেখার সংখ্যা সেট করা হয়েছে: <b>{count} টি</b>।", parse_mode="HTML")
    except Exception: 
        await m.answer("⚠️ সঠিক নিয়ম: <code>/setadcount 3</code>", parse_mode="HTML")

@dp.message(Command("protect"))
async def protect_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        state = m.text.split(" ")[1].lower()
        if state == "on":
            await db.settings.update_one({"id": "protect_content"}, {"$set": {"status": True}}, upsert=True)
            await m.answer("✅ ফরোয়ার্ড প্রোটেকশন চালু করা হয়েছে।")
        elif state == "off":
            await db.settings.update_one({"id": "protect_content"}, {"$set": {"status": False}}, upsert=True)
            await m.answer("✅ ফরোয়ার্ড প্রোটেকশন বন্ধ করা হয়েছে।")
    except Exception: 
        await m.answer("⚠️ সঠিক নিয়ম: <code>/protect on</code> অথবা <code>/protect off</code>")

@dp.message(Command("settime"))
async def set_del_time(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        mins = int(m.text.split(" ")[1])
        await db.settings.update_one({"id": "del_time"}, {"$set": {"minutes": mins}}, upsert=True)
        await m.answer("✅ অটো-ডিলিট টাইম সেট করা হয়েছে।")
    except Exception: 
        await m.answer("⚠️ সঠিক নিয়ম: <code>/settime 60</code> (মিনিট)", parse_mode="HTML")

@dp.message(Command("setad"))
async def set_ad(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        zone = m.text.split(" ")[1]
        await db.settings.update_one({"id": "ad_config"}, {"$set": {"zone_id": zone}}, upsert=True)
        await m.answer("✅ জোন আপডেট হয়েছে।")
    except Exception: 
        await m.answer("⚠️ সঠিক নিয়ম: <code>/setad 1234567</code>")

@dp.message(Command("addvip"))
async def add_vip_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        args = m.text.split()
        target_uid = int(args[1])
        days = int(args[2]) if len(args) > 2 else 30 
        
        now = datetime.datetime.utcnow()
        user = await db.users.find_one({"user_id": target_uid})
        if not user:
            return await m.answer("⚠️ এই ইউজারটি ডাটাবেসে নেই। তাকে আগে বট স্টার্ট করতে বলুন।")

        current_vip = user.get("vip_until", now)
        if current_vip < now:
            current_vip = now
            
        new_vip = current_vip + datetime.timedelta(days=days)
        await db.users.update_one({"user_id": target_uid}, {"$set": {"vip_until": new_vip}})
        await m.answer(f"✅ ইউজার <code>{target_uid}</code> কে সফলভাবে <b>{days} দিনের</b> VIP দেওয়া হয়েছে!", parse_mode="HTML")
        
        try:
            await bot.send_message(target_uid, f"🎉 <b>অভিনন্দন!</b> অ্যাডমিন আপনাকে <b>{days} দিনের</b> জন্য VIP মেম্বারশিপ দিয়েছেন।\n\nএখন আপনি কোনো অ্যাড ছাড়াই সরাসরি মুভি দেখতে ও প্লে করতে পারবেন। অ্যাপ রিস্টার্ট করুন।", parse_mode="HTML")
        except Exception: pass
    except Exception: 
        await m.answer("⚠️ সঠিক নিয়ম: <code>/addvip ইউজার_আইডি দিন</code>\nউদাহরণ: <code>/addvip 123456789 30</code>", parse_mode="HTML")

@dp.message(Command("removevip"))
async def remove_vip_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        target_uid = int(m.text.split()[1])
        now = datetime.datetime.utcnow()
        await db.users.update_one({"user_id": target_uid}, {"$set": {"vip_until": now - datetime.timedelta(days=1)}})
        await m.answer(f"❌ ইউজার <code>{target_uid}</code> এর VIP বাতিল করা হয়েছে!", parse_mode="HTML")
    except Exception: 
        await m.answer("⚠️ সঠিক নিয়ম: <code>/removevip ইউজার_আইডি</code>", parse_mode="HTML")


# ==========================================
# 8. Movie Upload Logic (With Stream support)
# ==========================================
@dp.message(F.content_type.in_({'video', 'document'}), lambda m: m.from_user.id in admin_cache)
async def receive_movie_file(m: types.Message, state: FSMContext):
    fid = m.video.file_id if m.video else m.document.file_id
    ftype = "video" if m.video else "document"
    await state.set_state(AdminStates.waiting_for_photo)
    await state.update_data(file_id=fid, file_type=ftype, original_msg_id=m.message_id)
    await m.answer("✅ ফাইল পেয়েছি! এবার মুভির <b>পোস্টার (Photo)</b> সেন্ড করুন।\nবাতিল করতে /start দিন।", parse_mode="HTML")

@dp.message(AdminStates.waiting_for_photo, F.photo)
async def receive_movie_photo(m: types.Message, state: FSMContext):
    await state.update_data(photo_id=m.photo[-1].file_id)
    await state.set_state(AdminStates.waiting_for_title)
    await m.answer("✅ পোস্টার পেয়েছি! এবার <b>মুভি বা ওয়েব সিরিজের নাম</b> লিখে পাঠান।\n<i>(নোট: যদি ওয়েব সিরিজ হয় বা একই মুভির অন্য কোয়ালিটি অ্যাড করতে চান, তবে আগের নামটিই হুবহু দিন)</i>", parse_mode="HTML")

@dp.message(AdminStates.waiting_for_title, F.text)
async def receive_movie_title(m: types.Message, state: FSMContext):
    await state.update_data(title=m.text.strip())
    await state.set_state(AdminStates.waiting_for_quality)
    await m.answer("✅ নাম সেভ হয়েছে! এবার এই ফাইলটির <b>কোয়ালিটি বা এপিসোড নাম্বার</b> দিন।\n<i>(উদাহরণ: 480p, 720p, 1080p অথবা Episode 01)</i>", parse_mode="HTML")

@dp.message(AdminStates.waiting_for_quality, F.text)
async def receive_movie_quality(m: types.Message, state: FSMContext):
    quality = m.text.strip()
    data = await state.get_data()
    await state.clear()
    
    title = data["title"]
    photo_id = data["photo_id"]
    
    dump_msg_id = None
    if DUMP_CHANNEL_ID:
        try:
            dump_msg = await bot.copy_message(chat_id=DUMP_CHANNEL_ID, from_chat_id=m.chat.id, message_id=data["original_msg_id"])
            dump_msg_id = dump_msg.message_id
        except Exception as e:
            print(f"Dump copy failed: {e}")
    
    await db.movies.insert_one({
        "title": title, "quality": quality, "photo_id": photo_id, 
        "file_id": data["file_id"], "file_type": data["file_type"], 
        "stream_msg_id": dump_msg_id,
        "clicks": 0, "created_at": datetime.datetime.utcnow()
    })
    
    await m.answer(f"🎉 <b>{title} [{quality}]</b> অ্যাপে সফলভাবে যুক্ত করা হয়েছে!", parse_mode="HTML")
    
    if CHANNEL_ID and CHANNEL_ID != "-100XXXXXXXXXX":
        try:
            bot_info = await bot.get_me()
            kb = [[types.InlineKeyboardButton(text="🎬 মুভিটি দেখতে এখানে ক্লিক করুন", url=f"https://t.me/{bot_info.username}?start=new")]]
            markup = types.InlineKeyboardMarkup(inline_keyboard=kb)
            caption = f"🎬 <b>নতুন ফাইল যুক্ত হয়েছে!</b>\n\n📌 <b>নাম:</b> {title}\n🏷 <b>কোয়ালিটি/এপিসোড:</b> {quality}\n\n👇 <i>দেখতে নিচের বাটনে ক্লিক করুন।</i>"
            await bot.send_photo(chat_id=CHANNEL_ID, photo=photo_id, caption=caption, parse_mode="HTML", reply_markup=markup)
        except Exception: 
            pass


# ==========================================
# 9. Upcoming Movies Logic
# ==========================================
@dp.message(Command("addupcoming"))
async def add_upc_cmd(m: types.Message, state: FSMContext):
    if m.from_user.id not in admin_cache: return
    await state.set_state(AdminStates.waiting_for_upc_photo)
    await m.answer("🌟 <b>আপকামিং মুভির পোস্টার (Photo) সেন্ড করুন:</b>\nবাতিল করতে /start দিন।", parse_mode="HTML")

@dp.message(AdminStates.waiting_for_upc_photo, F.photo)
async def upc_photo_step(m: types.Message, state: FSMContext):
    await state.update_data(photo_id=m.photo[-1].file_id)
    await state.set_state(AdminStates.waiting_for_upc_title)
    await m.answer("✅ পোস্টার পেয়েছি! এবার আপকামিং মুভির <b>টাইটেল (নাম)</b> লিখে পাঠান।", parse_mode="HTML")

@dp.message(AdminStates.waiting_for_upc_title, F.text)
async def upc_title_step(m: types.Message, state: FSMContext):
    data = await state.get_data()
    await db.upcoming.insert_one({
        "photo_id": data["photo_id"],
        "title": m.text.strip(),
        "added_at": datetime.datetime.utcnow()
    })
    await state.clear()
    await m.answer("✅ আপকামিং মুভি সফলভাবে যুক্ত করা হয়েছে!")

@dp.message(Command("delupcoming"))
async def del_upc_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    await db.upcoming.delete_many({})
    await m.answer("🗑 সব আপকামিং মুভি ডিলিট করা হয়েছে!")


# ==========================================
# 10. Broadcast & User Reply System
# ==========================================
@dp.message(Command("cast"))
async def broadcast_prep(m: types.Message, state: FSMContext):
    if m.from_user.id not in admin_cache: return
    await state.set_state(AdminStates.waiting_for_bcast)
    await m.answer("📢 যে মেসেজটি ব্রডকাস্ট করতে চান সেটি পাঠান।\nবাতিল করতে /start দিন।")

@dp.message(AdminStates.waiting_for_bcast)
async def execute_broadcast(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("⏳ ব্রডকাস্ট শুরু হয়েছে...")
    kb = [[types.InlineKeyboardButton(text="🎬 ওপেন মুভি অ্যাপ", web_app=types.WebAppInfo(url=APP_URL))]]
    markup = types.InlineKeyboardMarkup(inline_keyboard=kb)
    success = 0
    async for u in db.users.find():
        try:
            await m.copy_to(chat_id=u['user_id'], reply_markup=markup)
            success += 1
            await asyncio.sleep(0.05)
        except Exception: pass
    await m.answer(f"✅ সম্পন্ন! সর্বমোট <b>{success}</b> জনকে মেসেজ পাঠানো হয়েছে।", parse_mode="HTML")

@dp.callback_query(F.data.startswith("reply_"))
async def process_reply_cb(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id not in admin_cache: return
    user_id = int(c.data.split("_")[1])
    await state.set_state(AdminStates.waiting_for_reply)
    await state.update_data(target_uid=user_id)
    await c.message.reply("✍️ <b>ইউজারকে কী রিপ্লাই দিতে চান তা লিখে পাঠান:</b>", parse_mode="HTML")
    await c.answer()

@dp.message(AdminStates.waiting_for_reply)
async def send_reply(m: types.Message, state: FSMContext):
    data = await state.get_data()
    target_uid = data.get("target_uid")
    await state.clear()
    try:
        if m.text: 
            await bot.send_message(target_uid, f"📩 <b>অ্যাডমিন রিপ্লাই:</b>\n\n{m.text}", parse_mode="HTML")
        else: 
            await m.copy_to(target_uid, caption=f"📩 <b>অ্যাডমিন রিপ্লাই:</b>\n\n{m.caption or ''}", parse_mode="HTML")
        await m.answer("✅ ইউজারকে সফলভাবে রিপ্লাই পাঠানো হয়েছে!")
    except Exception: 
        await m.answer("⚠️ রিপ্লাই পাঠানো যায়নি! ইউজার হয়তো বট ব্লক করেছে।")


# ==========================================
# 11. Web Admin Panel API & HTML
# ==========================================
@app.get("/admin", response_class=HTMLResponse)
async def web_admin_panel(auth: bool = Depends(verify_admin)):
    html_content = """
    <!DOCTYPE html>
    <html lang="bn">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>MovieZone Admin Panel</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    </head>
    <body class="bg-gray-900 text-white font-sans antialiased">
        <div class="max-w-6xl mx-auto p-5">
            <div class="flex justify-between items-center mb-8 border-b border-gray-700 pb-4">
                <h1 class="text-3xl font-bold text-red-500"><i class="fa-solid fa-shield-halved"></i> MovieZone Admin</h1>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-10">
                <div class="bg-gray-800 p-6 rounded-xl shadow-lg border border-gray-700">
                    <h3 class="text-gray-400 text-sm font-bold">TOTAL USERS</h3>
                    <p class="text-4xl font-bold text-green-400 mt-2" id="statUsers">...</p>
                </div>
                <div class="bg-gray-800 p-6 rounded-xl shadow-lg border border-gray-700">
                    <h3 class="text-gray-400 text-sm font-bold">UNIQUE GROUPS</h3>
                    <p class="text-4xl font-bold text-blue-400 mt-2" id="statMovies">...</p>
                </div>
                <div class="bg-gray-800 p-6 rounded-xl shadow-lg border border-gray-700">
                    <h3 class="text-gray-400 text-sm font-bold">NEW USERS TODAY</h3>
                    <p class="text-4xl font-bold text-yellow-400 mt-2" id="statNew">...</p>
                </div>
            </div>
            <div class="bg-gray-800 rounded-xl shadow-lg border border-gray-700 p-6">
                <h2 class="text-xl font-bold mb-4 text-gray-200"><i class="fa-solid fa-film text-red-400"></i> Manage Movies</h2>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm whitespace-nowrap">
                        <thead class="bg-gray-700 text-gray-300">
                            <tr>
                                <th class="p-4 rounded-tl-lg">Movie / Series Title</th>
                                <th class="p-4">Total Views</th>
                                <th class="p-4">Files</th>
                                <th class="p-4 rounded-tr-lg">Action</th>
                            </tr>
                        </thead>
                        <tbody id="movieTableBody">
                            <tr><td colspan="4" class="text-center p-8 text-gray-400">Loading data...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
        <script>
            function formatViews(num) {
                if (num >= 1000000) return (num / 1000000).toFixed(1).replace(/\\.0$/, '') + 'M';
                if (num >= 1000) return (num / 1000).toFixed(1).replace(/\\.0$/, '') + 'K';
                return num.toString();
            }

            async function loadAdminData() {
                try {
                    const res = await fetch('/api/admin/data');
                    const data = await res.json();
                    document.getElementById('statUsers').innerText = data.total_users;
                    document.getElementById('statMovies').innerText = data.total_groups;
                    document.getElementById('statNew').innerText = data.new_users_today;
                    let html = '';
                    data.movies.forEach(m => {
                        html += `<tr class="border-b border-gray-700 hover:bg-gray-750 transition">
                            <td class="p-4 font-medium text-base">` + m._id + `</td>
                            <td class="p-4 text-gray-400 font-bold"><i class="fa-solid fa-eye text-gray-500"></i> ` + formatViews(m.clicks) + `</td>
                            <td class="p-4 text-green-400 font-bold">` + m.file_count + `</td>
                            <td class="p-4 flex gap-2">
                                <button onclick="addViews('`+encodeURIComponent(m._id)+`')" class="text-yellow-400 bg-yellow-900 bg-opacity-30 px-3 py-1 rounded"><i class="fa-solid fa-fire"></i> Boost</button>
                                <button onclick="editMovie('`+encodeURIComponent(m._id)+`', '`+m._id.replace(/'/g, "\\'")+`')" class="text-blue-400 bg-blue-900 bg-opacity-30 px-3 py-1 rounded">Edit</button>
                                <button onclick="deleteMovie('`+encodeURIComponent(m._id)+`')" class="text-red-400 bg-red-900 bg-opacity-30 px-3 py-1 rounded">Delete</button>
                            </td>
                        </tr>`;
                    });
                    document.getElementById('movieTableBody').innerHTML = html;
                } catch (e) { alert("Error loading data from the server!"); }
            }
            async function deleteMovie(encodedTitle) {
                if(!confirm('Are you absolutely sure you want to delete ALL files for this movie?')) return;
                await fetch('/api/admin/movie/' + encodedTitle, {method: 'DELETE'});
                loadAdminData();
            }
            async function editMovie(encodedTitle, oldTitle) {
                let newTitle = prompt("Enter new title for all files in this group:", oldTitle);
                if(newTitle && newTitle.trim() !== "" && newTitle !== oldTitle) {
                    await fetch('/api/admin/movie/' + encodedTitle, {
                        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: newTitle.trim()})
                    });
                    loadAdminData();
                }
            }
            async function addViews(encodedTitle) {
                let amount = prompt("এই মুভির ভিউ কত বাড়াতে চান? (যেমন: 1000 বা 5000):", "1000");
                if(amount && amount.trim() !== "" && !isNaN(amount)) {
                    await fetch('/api/admin/movie/' + encodedTitle, {
                        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({add_clicks: parseInt(amount)})
                    });
                    loadAdminData();
                }
            }
            loadAdminData();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/api/admin/data")
async def get_admin_data(auth: bool = Depends(verify_admin)):
    uc = await db.users.count_documents({})
    now = datetime.datetime.utcnow()
    today_start = datetime.datetime(now.year, now.month, now.day)
    new_users = await db.users.count_documents({"joined_at": {"$gte": today_start}})
    pipeline = [
        {"$group": {"_id": "$title", "clicks": {"$sum": "$clicks"}, "file_count": {"$sum": 1}, "created_at": {"$max": "$created_at"}}},
        {"$sort": {"created_at": -1}}, {"$limit": 50}
    ]
    movies = await db.movies.aggregate(pipeline).to_list(50)
    return {"total_users": uc, "total_groups": len(movies), "new_users_today": new_users, "movies": movies}

@app.delete("/api/admin/movie/{title}")
async def delete_movie_api(title: str, auth: bool = Depends(verify_admin)):
    await db.movies.delete_many({"title": title})
    return {"ok": True}

@app.put("/api/admin/movie/{title}")
async def edit_movie_api(title: str, data: dict = Body(...), auth: bool = Depends(verify_admin)):
    if new_title := data.get("title"): 
        await db.movies.update_many({"title": title}, {"$set": {"title": new_title}})
        
    if add_clicks := data.get("add_clicks"):
        try:
            clicks_to_add = int(add_clicks)
            await db.movies.update_one({"title": title}, {"$inc": {"clicks": clicks_to_add}})
        except ValueError: pass
            
    return {"ok": True}


# ==========================================
# 12. Main Web App UI (Frontend)
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def web_ui():
    ad_cfg = await db.settings.find_one({"id": "ad_config"})
    tg_cfg = await db.settings.find_one({"id": "link_tg"})
    b18_cfg = await db.settings.find_one({"id": "link_18"})
    ad_count_cfg = await db.settings.find_one({"id": "ad_count"})
    
    zone_id = ad_cfg['zone_id'] if ad_cfg else "10916755"
    tg_url = tg_cfg['url'] if tg_cfg else "https://t.me/MovieeBD"
    link_18 = b18_cfg['url'] if b18_cfg else "https://t.me/MovieeBD"
    required_ads = ad_count_cfg['count'] if ad_count_cfg else 1

    html_code = r"""
    <!DOCTYPE html>
    <html lang="bn">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>MovieZone BD</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
        
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            html { scroll-behavior: smooth; }
            body { background: #0f172a; font-family: sans-serif; color: #fff; -webkit-font-smoothing: antialiased; overscroll-behavior-y: none; } 
            
            header { display: flex; justify-content: space-between; align-items: center; padding: 15px; border-bottom: 1px solid #1e293b; position: sticky; top: 0; background: rgba(15, 23, 42, 0.95); backdrop-filter: blur(10px); z-index: 1000; }
            .logo { font-size: 24px; font-weight: bold; }
            .logo span { background: red; color: #fff; padding: 2px 6px; border-radius: 5px; margin-left: 5px; font-size: 16px; }
            .header-right { display: flex; align-items: center; gap: 10px; }
            .user-info { display: flex; align-items: center; gap: 8px; background: #1e293b; padding: 6px 14px; border-radius: 25px; font-weight: bold; font-size: 14px; border: 1px solid #334155; }
            .user-info img { width: 28px; height: 28px; border-radius: 50%; object-fit: cover; }
            
            .menu-btn { background: #1e293b; border: 1px solid #334155; padding: 8px 12px; border-radius: 8px; cursor: pointer; color: white; font-size: 18px; transition: 0.3s; }
            .menu-btn:active { transform: scale(0.9); }
            
            .dropdown-menu { display: none; position: absolute; top: 65px; right: 15px; background: #1e293b; border: 1px solid #334155; border-radius: 12px; overflow: hidden; box-shadow: 0 5px 20px rgba(0,0,0,0.5); z-index: 2000; width: 180px; }
            .dropdown-menu a { display: block; padding: 12px 15px; color: white; text-decoration: none; font-weight: bold; font-size: 15px; border-bottom: 1px solid #334155; cursor: pointer; transition: 0.2s; }
            .dropdown-menu a:hover { background: #334155; }
            .dropdown-menu a:last-child { border-bottom: none; }
            .dropdown-menu i { width: 20px; text-align: center; margin-right: 8px; }

            .search-box { padding: 15px; }
            .search-input { width: 100%; padding: 16px; border-radius: 25px; border: none; outline: none; text-align: center; background: #1e293b; color: #fff; font-size: 18px; font-weight: bold; transition: 0.3s; box-shadow: inset 0 2px 5px rgba(0,0,0,0.3); }
            .search-input::placeholder { color: #94a3b8; font-weight: 500; font-size: 16px; }
            .search-input:focus { box-shadow: 0 0 15px rgba(248,113,113,0.7); }
            
            .section-title { padding: 5px 15px 15px; font-size: 22px; font-weight: 900; display: flex; align-items: center; gap: 8px; background: linear-gradient(45deg, #ff416c, #ff4b2b); -webkit-background-clip: text; -webkit-text-fill-color: transparent; text-shadow: 0px 4px 15px rgba(255, 75, 43, 0.4); }
            .section-title i { -webkit-text-fill-color: #ff416c; }
            
            .trending-container, .upcoming-container { display: flex; overflow-x: auto; gap: 15px; padding: 0 15px 20px; scroll-behavior: smooth; -webkit-overflow-scrolling: touch; }
            .trending-container::-webkit-scrollbar, .upcoming-container::-webkit-scrollbar { display: none; }
            .trending-card, .upcoming-card { min-width: 140px; max-width: 140px; background: #1e293b; border-radius: 12px; overflow: hidden; cursor: pointer; flex-shrink: 0; position: relative; transition: transform 0.2s; }
            .trending-card:active, .upcoming-card:active { transform: scale(0.95); }
            .trending-card img, .upcoming-card img { height: 200px; object-fit: cover; width: 100%; border-radius: 10px; display: block; }
            
            .grid { padding: 0 15px 20px; display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; }
            .card { background: #1e293b; border-radius: 12px; overflow: hidden; cursor: pointer; transition: transform 0.2s, box-shadow 0.2s; }
            .card:active { transform: scale(0.95); }
            
            .post-content { position: relative; padding: 3px; border-radius: 12px; background: linear-gradient(45deg, #ff0000, #ff7300, #fffb00, #48ff00, #00ffd5, #002bff, #7a00ff, #ff00c8, #ff0000); background-size: 400%; animation: glowing 8s linear infinite; }
            @keyframes glowing { 0% { background-position: 0 0; } 50% { background-position: 400% 0; } 100% { background-position: 0 0; } }
            .post-content img { width: 100%; height: 230px; object-fit: cover; display: block; border-radius: 10px; }
            
            .top-badge { position: absolute; top: 10px; left: 10px; background: linear-gradient(45deg, #ff0000, #cc0000); color: white; padding: 4px 8px; border-radius: 6px; font-size: 11px; font-weight: bold; z-index: 10; }
            .view-badge { position: absolute; bottom: 10px; left: 10px; background: rgba(0,0,0,0.75); color: #fff; padding: 4px 8px; border-radius: 6px; font-size: 12px; font-weight: bold; display: flex; align-items: center; gap: 5px; }
            .ep-badge { position: absolute; top: 10px; right: 10px; background: #10b981; color: white; padding: 4px 8px; border-radius: 6px; font-size: 11px; font-weight: bold; z-index: 10; }

            .card-footer { padding: 12px; font-size: 14px; font-weight: bold; text-align: center; color: #f8fafc; line-height: 1.4; white-space: normal; word-wrap: break-word; display: block; }
            
            .skeleton { background: #1e293b; border-radius: 12px; height: 260px; overflow: hidden; position: relative; }
            .skeleton::after { content: ""; position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: linear-gradient(90deg, transparent, rgba(255,255,255,0.05), transparent); animation: shimmer 1.5s infinite; }
            @keyframes shimmer { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }

            .pagination { display: flex; justify-content: center; align-items: center; gap: 8px; padding: 10px 15px 120px; flex-wrap: wrap; }
            .page-btn { background: #1e293b; color: #fff; border: 1px solid #334155; padding: 10px 16px; border-radius: 8px; cursor: pointer; font-weight: bold; transition: 0.3s; outline: none; }
            .page-btn.active { background: #f87171; border-color: #f87171; color: white; box-shadow: 0 0 10px rgba(248,113,113,0.4); }

            .floating-btn { position: fixed; right: 20px; color: white; width: 50px; height: 50px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 22px; z-index: 500; cursor: pointer; box-shadow: 0 4px 15px rgba(0,0,0,0.5); transition: 0.3s; }
            .floating-btn:active { transform: scale(0.9); }
            .btn-18 { bottom: 155px; background: linear-gradient(45deg, #ff0000, #990000); border: 2px solid #fff; font-weight: bold; font-size: 18px; }
            .btn-tg { bottom: 95px; background: linear-gradient(45deg, #24A1DE, #1b7ba8); }
            .btn-req { bottom: 35px; background: linear-gradient(45deg, #10b981, #059669); }

            .modal { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.85); display: none; align-items: center; justify-content: center; z-index: 3000; backdrop-filter: blur(5px); }
            .modal-content { background: #1e293b; width: 92%; max-width: 400px; padding: 25px; border-radius: 20px; text-align: center; border: 1px solid #334155; max-height: 85vh; overflow-y: auto; }
            .instruction-text { color: #fbbf24; font-size: 15.5px; font-weight: bold; margin-bottom: 20px; line-height: 1.5; }
            .quality-btn { display: flex; justify-content: space-between; align-items: center; background: #0f172a; border: 1px solid #334155; padding: 16px; border-radius: 12px; margin-bottom: 12px; color: white; font-weight: bold; font-size: 16px; cursor: pointer; transition: 0.3s; width: 100%; }
            .quality-btn:active { transform: scale(0.98); }
            .quality-locked { border-left: 5px solid #ef4444; }
            .quality-unlocked { border-left: 5px solid #10b981; }
            .close-btn { background: #334155; color: white; padding: 12px 20px; border-radius: 12px; margin-top: 15px; border: none; width: 100%; font-weight: bold; font-size: 16px; cursor: pointer; }
            .req-input { width: 100%; padding: 16px; margin: 20px 0; border-radius: 12px; border: 2px solid #334155; background: #0f172a; color: white; outline: none; font-size: 16px; font-weight: bold; }
            .btn-submit { background: linear-gradient(45deg, #10b981, #059669); color: white; border: none; padding: 15px 20px; border-radius: 12px; font-weight: bold; width: 100%; font-size: 18px; cursor: pointer; transition: 0.3s; }
            .btn-submit:active { transform: scale(0.95); }
            .notice-box { background: linear-gradient(135deg, rgba(248,113,113,0.15), rgba(220,38,38,0.25)); border-left: 5px solid #ef4444; padding: 15px; text-align: left; margin: 25px 0; border-radius: 8px; }
            .notice-box p { color: #fecaca; font-size: 16.5px; font-weight: bold; margin: 0; line-height: 1.6; text-shadow: 0 1px 3px rgba(0,0,0,0.5); }
            .refer-box { background: #0f172a; padding: 15px; border-radius: 10px; border: 1px dashed #3b82f6; margin: 15px 0; font-size: 14px; word-break: break-all; color: #93c5fd; }

            .ad-screen { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(15, 23, 42, 0.98); display: none; flex-direction: column; align-items: center; justify-content: center; z-index: 4000; }
            .timer-ui { display: flex; flex-direction: column; align-items: center; }
            .rgb-timer-container { position: relative; width: 140px; height: 140px; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin-bottom: 30px; background: #0f172a; box-shadow: 0 0 40px rgba(0,0,0,0.9); }
            .rgb-ring { position: absolute; width: 100%; height: 100%; border-radius: 50%; border: 6px solid transparent; background: linear-gradient(#0f172a, #0f172a) padding-box, conic-gradient(#ff0000, #ff7300, #fffb00, #48ff00, #00ffd5, #002bff, #7a00ff, #ff00c8, #ff0000) border-box; animation: spinRing 1.5s linear infinite; }
            .timer-text { position: relative; font-size: 55px; font-weight: bold; color: #fff; z-index: 2; text-shadow: 0 0 20px rgba(255,255,255,0.9); }
            @keyframes spinRing { 100% { transform: rotate(360deg); } }
            .ad-step-text { font-size: 20px; font-weight: bold; color: #fff; margin-bottom: 25px; background: #1e293b; padding: 12px 25px; border-radius: 30px; border: 2px solid #fbbf24; text-shadow: 0 0 10px rgba(251,191,36,0.5); }
            .btn-next-ad { display: none; background: linear-gradient(45deg, #f87171, #ef4444); color: white; border: none; padding: 18px 40px; border-radius: 35px; font-size: 20px; font-weight: bold; cursor: pointer; box-shadow: 0 5px 25px rgba(248,113,113,0.7); transition: 0.3s; }
            
            .vip-tag { background: linear-gradient(45deg, #fbbf24, #f59e0b); color: #000; font-size: 12px; padding: 3px 8px; border-radius: 12px; font-weight: bold; display: none; margin-left:5px; box-shadow: 0 0 10px rgba(251,191,36,0.5); }

            /* New Full Screen Video Player CSS */
            .video-screen { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: #000; display: none; flex-direction: column; z-index: 5000; }
            .video-header { display: flex; align-items: center; padding: 15px 20px; background: linear-gradient(to bottom, rgba(0,0,0,0.9), transparent); color: white; gap: 15px; width: 100%; position: absolute; top: 0; z-index: 5010; }
            .video-header i { font-size: 24px; cursor: pointer; padding: 5px; text-shadow: 0 2px 5px rgba(0,0,0,0.8); }
            .video-title-bar { flex: 1; font-weight: bold; font-size: 18px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-shadow: 0 2px 5px rgba(0,0,0,0.8); }
            .player-wrapper { flex: 1; display: flex; align-items: center; justify-content: center; width: 100%; height: 100%; background: #000; }
            #onlinePlayer { width: 100%; max-height: 100vh; outline: none; }
        </style>
    </head>
    <body onclick="closeMenu(event)">
        <header>
            <div class="logo">MovieZone <span>BD</span></div>
            <div class="header-right">
                <div class="user-info">
                    <span id="uName">Guest</span>
                    <span id="vipBadge" class="vip-tag"><i class="fa-solid fa-crown"></i> VIP</span>
                    <img id="uPic" src="https://cdn-icons-png.flaticon.com/512/3135/3135715.png">
                </div>
                <div class="menu-btn" onclick="toggleMenu(event)"><i class="fa-solid fa-bars"></i></div>
            </div>
        </header>
        
        <div id="dropdownMenu" class="dropdown-menu">
            <a onclick="openVipModal()"><i class="fa-solid fa-crown text-yellow-400"></i> VIP প্যাকেজ</a>
            <a onclick="openReferModal()"><i class="fa-solid fa-share-nodes text-blue-400"></i> রেফার ও ইনকাম</a>
        </div>

        <div class="search-box">
            <input type="text" id="searchInput" class="search-input" placeholder="🔍 মুভি বা ওয়েব সিরিজ খুঁজুন...">
        </div>

        <div id="trendingWrapper">
            <div class="section-title"><i class="fa-solid fa-fire"></i> ট্রেন্ডিং মুভি</div>
            <div class="trending-container" id="trendingGrid">
                <div class="skeleton" style="min-width:140px; height:240px;"></div>
                <div class="skeleton" style="min-width:140px; height:240px;"></div>
                <div class="skeleton" style="min-width:140px; height:240px;"></div>
            </div>
        </div>

        <!-- Upcoming Section -->
        <div id="upcomingWrapper" style="display: none;">
            <div class="section-title"><i class="fa-solid fa-clock-rotate-left"></i> আপকামিং মুভি</div>
            <div class="upcoming-container" id="upcomingGrid"></div>
        </div>

        <div class="section-title"><i class="fa-solid fa-film"></i> নতুন সব মুভি</div>
        <div class="grid" id="movieGrid"></div>
        <div class="pagination" id="paginationBox"></div>

        <div class="floating-btn btn-18" onclick="window.open('{{LINK_18}}')">18+</div>
        <div class="floating-btn btn-tg" onclick="window.open('{{TG_LINK}}')"><i class="fa-brands fa-telegram"></i></div>
        <div class="floating-btn btn-req" onclick="openReqModal()"><i class="fa-solid fa-code-pull-request"></i></div>

        <!-- Modals -->
        <div id="qualityModal" class="modal">
            <div class="modal-content">
                <h2 id="modalTitle" style="color:#38bdf8; margin-bottom: 8px; font-size: 22px; font-weight:900;">Movie Title</h2>
                <p class="instruction-text">👇 আপনি কোনটি দেখতে চান তা নির্বাচন করুন:</p>
                <div id="qualityList"></div>
                <button class="close-btn" onclick="closeQualityModal()">বন্ধ করুন</button>
            </div>
        </div>

        <!-- NEW Full Screen Video Player -->
        <div id="videoScreen" class="video-screen">
            <div class="video-header">
                <i class="fa-solid fa-arrow-left" onclick="closeVideo()"></i>
                <div class="video-title-bar" id="videoTitleBar">Playing Video...</div>
            </div>
            <div class="player-wrapper">
                <video id="onlinePlayer" controls controlsList="nodownload" preload="auto" playsinline></video>
            </div>
        </div>

        <div id="adScreen" class="ad-screen">
            <div class="ad-step-text" id="adStepText">অ্যাড: 1/1</div>
            <div class="timer-ui" id="timerUI">
                <div class="rgb-timer-container"><div class="rgb-ring"></div><div class="timer-text" id="timer">15</div></div>
                <p style="color: #fbbf24; font-size: 18px; font-weight: bold; margin-top:15px; text-shadow: 0 0 10px rgba(251,191,36,0.5);">সার্ভারের সাথে কানেক্ট হচ্ছে...</p>
            </div>
            <button class="btn-next-ad" id="nextAdBtn" onclick="nextAdStep()">পরবর্তী অ্যাড দেখুন <i class="fa-solid fa-arrow-right"></i></button>
        </div>

        <div id="successModal" class="modal">
            <div class="modal-content">
                <i class="fa-solid fa-circle-check" style="font-size:80px; color:#4ade80; text-shadow: 0 0 25px rgba(74,222,128,0.6);"></i>
                <h2 style="margin:20px 0 10px; color:white; font-size: 26px;">সম্পন্ন হয়েছে!</h2>
                <p style="color: #4ade80; font-size: 17px; font-weight: bold;">✅ ফাইলটি বটের ইনবক্সে পাঠানো হয়েছে।</p>
                <div class="notice-box"><p><i class="fa-solid fa-triangle-exclamation" style="color: #fbbf24; font-size: 18px;"></i> <b>সতর্কতা:</b> কপিরাইট এড়াতে মুভিটি কিছুক্ষণ পর অটোমেটিক ডিলিট হয়ে যাবে। দয়া করে এখনই বট থেকে সেভ বা ফরোয়ার্ড করে নিন!</p></div>
                <button class="btn-submit" onclick="tg.close()">বটে ফিরে যান</button>
            </div>
        </div>

        <div id="reqModal" class="modal">
            <div class="modal-content">
                <h2 style="color:white; font-size: 24px;">মুভি রিকোয়েস্ট</h2>
                <p class="instruction-text" style="margin-top: 10px;">👇 যে মুভিটি খুঁজছেন তার সঠিক নাম লিখুন:</p>
                <input type="text" id="reqText" class="req-input" placeholder="উদাঃ Avatar 2022">
                <button class="btn-submit" onclick="sendReq()">সাবমিট করুন</button>
                <p style="margin-top:25px; color:#94a3b8; font-size: 16px; cursor:pointer; font-weight:bold;" onclick="document.getElementById('reqModal').style.display='none'">বাতিল করুন</p>
            </div>
        </div>
        
        <!-- VIP Modal -->
        <div id="vipModal" class="modal">
            <div class="modal-content">
                <i class="fa-solid fa-crown" style="font-size:70px; color:#fbbf24; text-shadow: 0 0 25px rgba(251,191,36,0.6);"></i>
                <h2 style="margin:15px 0 10px; color:white; font-size: 24px;">VIP প্যাকেজ</h2>
                <p style="color:#cbd5e1; font-size:15px; margin-bottom:15px; line-height:1.5;">VIP নিলে আপনাকে কোনো বিরক্তিকর অ্যাড দেখতে হবে মোহনীয়। <b>সরাসরি মুভি প্লে করতে পারবেন!</b></p>
                
                <div style="background:#0f172a; padding:15px; border-radius:10px; text-align:left; border-left:4px solid #fbbf24; margin-bottom:15px;">
                    <p style="color:#fbbf24; font-weight:bold; margin-bottom:10px; font-size:17px;">মাসিক প্যাকেজ সমূহ:</p>
                    <ul style="color:#94a3b8; font-size:15px; line-height:1.8; list-style-type: disc; margin-left: 20px;">
                        <li><b>১ মাস (৩০ দিন):</b> মাত্র ২০ টাকা</li>
                        <li><b>২ মাস (৬০ দিন):</b> মাত্র ৩৫ টাকা</li>
                        <li><b>লাইফটাইম VIP:</b> ১০০ টাকা</li>
                    </ul>
                </div>

                <div style="background:#1e293b; padding:10px; border-radius:10px; text-align:left; border:1px solid #334155; margin-bottom:20px;">
                    <p style="color:#4ade80; font-size:14px; line-height:1.6;"><b>কীভাবে কিনবেন?</b><br>নিচের বাটনে ক্লিক করে অ্যাডমিনকে মেসেজ দিন। পেমেন্ট করার পর অ্যাডমিন আপনার আইডিতে অটোমেটিক VIP চালু করে দিবে।</p>
                </div>
                
                <button class="btn-submit" style="background: linear-gradient(45deg, #fbbf24, #d97706); color:black;" onclick="window.open('https://t.me/{{BOT_USER}}')"><i class="fa-brands fa-telegram"></i> অ্যাডমিনকে মেসেজ দিন</button>
                <button class="close-btn" onclick="document.getElementById('vipModal').style.display='none'">বন্ধ করুন</button>
            </div>
        </div>

        <!-- Refer Modal -->
        <div id="referModal" class="modal">
            <div class="modal-content">
                <i class="fa-solid fa-share-nodes" style="font-size:60px; color:#38bdf8; text-shadow: 0 0 25px rgba(56,189,248,0.6);"></i>
                <h2 style="margin:15px 0 10px; color:white; font-size: 24px;">রেফার করুন এবং ফ্রী VIP!</h2>
                <p style="color:#cbd5e1; font-size:15px; margin-bottom:15px; line-height:1.5;">আপনার বন্ধুদের ইনভাইট করুন। প্রতি ৫ জন নতুন বন্ধু আপনার লিংকে ক্লিক করে বট স্টার্ট করলেই আপনি পাবেন <b>২৪ ঘণ্টার VIP একদম ফ্রি!</b></p>
                
                <h3 style="color:#4ade80; font-size:18px; margin-top:10px;">আপনার মোট রেফার: <span id="refCountNum" style="font-size:24px; font-weight:900;">0</span> জন</h3>
                
                <div class="refer-box" id="refLinkText">Loading link...</div>
                
                <button class="btn-submit" style="background: linear-gradient(45deg, #3b82f6, #1d4ed8);" onclick="copyReferLink()"><i class="fa-regular fa-copy"></i> লিংক কপি করুন</button>
                <button class="close-btn" onclick="document.getElementById('referModal').style.display='none'">বন্ধ করুন</button>
            </div>
        </div>

        <script>
            let tg = window.Telegram.WebApp; 
            tg.expand();
            const ZONE_ID = "{{ZONE_ID}}";
            const REQUIRED_ADS = parseInt("{{AD_COUNT}}");
            const INIT_DATA = tg.initData || "";
            const BOT_UNAME = "{{BOT_USER}}";
            let currentPage = 1; let isLoading = false; let searchQuery = "";
            let uid = tg.initDataUnsafe?.user?.id || 0;
            let currentAdStep = 1; let activeFileId = null; let autoScrollInterval; let isTouching = false; let abortController = null;
            let loadedMovies = {}; 
            
            let isUserVip = false;
            let userReferCount = 0;

            function formatViews(num) {
                if (num >= 1000000) return (num / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
                if (num >= 1000) return (num / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
                return num.toString();
            }

            if(tg.initDataUnsafe && tg.initDataUnsafe.user) {
                document.getElementById('uName').innerText = tg.initDataUnsafe.user.first_name;
                if(tg.initDataUnsafe.user.photo_url) document.getElementById('uPic').src = tg.initDataUnsafe.user.photo_url;
            }

            const s = document.createElement('script');
            s.src = '//libtl.com/sdk.js'; s.setAttribute('data-zone', ZONE_ID); s.setAttribute('data-sdk', 'show_' + ZONE_ID);
            document.head.appendChild(s);

            async function fetchUserInfo() {
                try {
                    const res = await fetch('/api/user/' + uid);
                    const data = await res.json();
                    isUserVip = data.vip;
                    userReferCount = data.refer_count;
                    
                    if(isUserVip) {
                        document.getElementById('vipBadge').style.display = 'inline-block';
                        if(data.vip_expiry) {
                            document.getElementById('dropdownMenu').insertAdjacentHTML('afterbegin', `<div style="padding: 10px 15px; color: #4ade80; font-size: 13px; font-weight: bold; border-bottom: 1px solid #334155; text-align: center;"><i class="fa-regular fa-clock"></i> মেয়াদ: ${data.vip_expiry}</div>`);
                        }
                    }
                    document.getElementById('refCountNum').innerText = userReferCount;
                    document.getElementById('refLinkText').innerText = `https://t.me/${BOT_UNAME}?start=ref_${uid}`;
                } catch(e) {}
            }

            function toggleMenu(e) {
                e.stopPropagation();
                const menu = document.getElementById('dropdownMenu');
                menu.style.display = (menu.style.display === 'block') ? 'none' : 'block';
            }
            function closeMenu() {
                document.getElementById('dropdownMenu').style.display = 'none';
            }
            
            function openVipModal() { document.getElementById('vipModal').style.display = 'flex'; closeMenu(); }
            function openReferModal() { document.getElementById('referModal').style.display = 'flex'; closeMenu(); }
            
            function copyReferLink() {
                const link = document.getElementById('refLinkText').innerText;
                navigator.clipboard.writeText(link).then(() => {
                    tg.showAlert("✅ আপনার রেফার লিংক সফলভাবে কপি হয়েছে! এখন বন্ধুদের শেয়ার করুন।");
                });
            }

            function drawSkeletons(count) { return Array(count).fill('<div class="skeleton"></div>').join(''); }

            function startAutoScroll() {
                if(autoScrollInterval) clearInterval(autoScrollInterval);
                autoScrollInterval = setInterval(() => {
                    if(isTouching) return; 
                    let grid = document.getElementById('trendingGrid');
                    if(grid) {
                        if (grid.scrollLeft >= (grid.scrollWidth - grid.clientWidth - 10)) grid.scrollTo({ left: 0, behavior: 'smooth' });
                        else grid.scrollBy({ left: 155, behavior: 'smooth' });
                    }
                }, 3500);
            }

            async function loadTrending() {
                try {
                    const r = await fetch(`/api/trending?uid=${uid}`);
                    const data = await r.json();
                    if(data.error === "banned") return document.body.innerHTML = `<h2 style='color:#ef4444; text-align:center; margin-top:80px;'>🚫 You are permanently Banned!</h2>`;
                    
                    const grid = document.getElementById('trendingGrid');
                    if(data.length === 0) return document.getElementById('trendingWrapper').style.display = 'none';
                    
                    grid.innerHTML = data.map(m => {
                        loadedMovies[m._id] = m;
                        return `<div class="trending-card" onclick="openQualityModal('${m._id.replace(/'/g, "\\'")}')">
                            <div class="post-content">
                                <div class="top-badge">🔥 TOP</div>
                                <img src="/api/image/${m.photo_id}" loading="lazy" onerror="this.src='https://via.placeholder.com/400x240?text=No+Image'">
                                <div class="ep-badge"><i class="fa-solid fa-list"></i> ${m.files.length}</div>
                                <div class="view-badge"><i class="fa-solid fa-eye"></i> ${formatViews(m.clicks)}</div>
                            </div>
                            <div class="card-footer">${m._id}</div>
                        </div>`;
                    }).join('');
                    
                    grid.addEventListener('touchstart', () => isTouching = true, {passive: true});
                    grid.addEventListener('touchend', () => setTimeout(() => isTouching = false, 1000), {passive: true});
                    grid.addEventListener('mouseenter', () => isTouching = true);
                    grid.addEventListener('mouseleave', () => isTouching = false);
                    setTimeout(startAutoScroll, 2000);
                } catch(e) { console.error("Trending Error: ", e); }
            }

            async function loadUpcoming() {
                try {
                    const r = await fetch(`/api/upcoming`);
                    const data = await r.json();
                    const grid = document.getElementById('upcomingGrid');
                    const wrapper = document.getElementById('upcomingWrapper');
                    
                    if(data.length > 0) {
                        wrapper.style.display = 'block';
                        grid.innerHTML = data.map(m => `
                        <div class="upcoming-card">
                            <img src="/api/image/${m.photo_id}" loading="lazy" onerror="this.src='https://via.placeholder.com/140x200?text=Upcoming'">
                            <div class="card-footer">${m.title}</div>
                        </div>`).join('');
                    } else {
                        wrapper.style.display = 'none';
                    }
                } catch(e) { console.error("Upcoming Error: ", e); }
            }

            async function loadMovies(page = 1, signal = null) {
                if(isLoading) return; isLoading = true; currentPage = page;
                const grid = document.getElementById('movieGrid');
                const pBox = document.getElementById('paginationBox');
                grid.innerHTML = drawSkeletons(16); pBox.innerHTML = "";

                try {
                    const r = await fetch(`/api/list?page=${currentPage}&q=${encodeURIComponent(searchQuery)}&uid=${uid}`, { signal });
                    const data = await r.json();
                    if(data.error === "banned") return;

                    if(data.movies && data.movies.length === 0) {
                        grid.innerHTML = `<p style='grid-column: span 2; text-align:center; color:#fbbf24; font-size: 18px; padding:40px;'>🚫 কোনো মুভি পাওয়া যায়নি!</p>`;
                    } else if (data.movies) {
                        grid.innerHTML = data.movies.map(m => {
                            loadedMovies[m._id] = m; 
                            return `<div class="card" onclick="openQualityModal('${m._id.replace(/'/g, "\\'")}')">
                                <div class="post-content">
                                    <img src="/api/image/${m.photo_id}" loading="lazy" onerror="this.src='https://via.placeholder.com/400x240?text=No+Image'">
                                    <div class="ep-badge"><i class="fa-solid fa-list"></i> ${m.files.length}</div>
                                    <div class="view-badge"><i class="fa-solid fa-eye"></i> ${formatViews(m.clicks)}</div>
                                </div>
                                <div class="card-footer">${m._id}</div>
                            </div>`;
                        }).join('');
                        renderPagination(data.total_pages);
                    }
                } catch(e) { console.error(e); }
                isLoading = false;
            }

            function renderPagination(totalPages) {
                if (totalPages <= 1) return;
                let html = `<button class="page-btn" ${currentPage === 1 ? 'disabled' : ''} onclick="goToPage(${currentPage - 1})"><i class="fa-solid fa-angle-left"></i></button>`;
                let start = Math.max(1, currentPage - 1); let end = Math.min(totalPages, currentPage + 1);
                if (start > 1) { html += `<button class="page-btn" onclick="goToPage(1)">1</button>`; if (start > 2) html += `<span style="color:gray;">...</span>`; }
                for (let i = start; i <= end; i++) html += `<button class="page-btn ${i === currentPage ? 'active' : ''}" onclick="goToPage(${i})">${i}</button>`; 
                if (end < totalPages) { if (end < totalPages - 1) html += `<span style="color:gray;">...</span>`; html += `<button class="page-btn" onclick="goToPage(${totalPages})">${totalPages}</button>`; }
                html += `<button class="page-btn" ${currentPage === totalPages ? 'disabled' : ''} onclick="goToPage(${currentPage + 1})"><i class="fa-solid fa-angle-right"></i></button>`;
                document.getElementById('paginationBox').innerHTML = html;
            }

            function goToPage(p) {
                if (p < 1) return; 
                loadMovies(p);
                window.scrollTo({ top: document.getElementById('movieGrid').offsetTop - 100, behavior: 'smooth' });
            }

            let timeout = null;
            document.getElementById('searchInput').addEventListener('input', function(e) {
                clearTimeout(timeout); searchQuery = e.target.value.trim();
                if(searchQuery !== "") { document.getElementById('trendingWrapper').style.display = 'none'; document.getElementById('upcomingWrapper').style.display = 'none'; isTouching = true; } 
                else { document.getElementById('trendingWrapper').style.display = 'block'; loadUpcoming(); isTouching = false; loadTrending(); }
                timeout = setTimeout(() => { 
                    if(abortController) abortController.abort();
                    abortController = new AbortController();
                    loadMovies(1, abortController.signal); 
                }, 500); 
            });

            function openQualityModal(title) {
                const movie = loadedMovies[title];
                if(!movie) return;
                document.getElementById('modalTitle').innerText = movie._id;
                
                let listHtml = movie.files.map(f => {
                    let isFree = f.is_unlocked || isUserVip;
                    let icon = isFree ? '<i class="fa-solid fa-paper-plane text-green-400" style="font-size:18px;"></i>' : '<i class="fa-solid fa-lock text-red-400" style="font-size:18px;"></i>';
                    let cls = isFree ? 'quality-unlocked' : 'quality-locked';
                    
                    let btnHtml = `<div style="display:flex; gap:10px; margin-bottom: 12px; width: 100%;">`;
                    
                    btnHtml += `<button class="quality-btn ${cls}" style="margin-bottom:0; flex:1;" onclick="handleQualityClick('${f.id}', ${f.is_unlocked})"><span>${f.quality}</span> ${icon}</button>`;
                    
                    if (isUserVip) {
                        let safeTitle = movie._id.replace(/'/g, "\\'");
                        btnHtml += `<button class="quality-btn quality-unlocked" style="margin-bottom:0; flex:0.4; background: linear-gradient(45deg, #0ea5e9, #2563eb); border-color:#3b82f6; color:white; justify-content:center; gap:8px;" onclick="playOnlineVideo('${f.id}', '${safeTitle}')"><i class="fa-solid fa-play"></i> Play</button>`;
                    }
                    
                    btnHtml += `</div>`;
                    return btnHtml;
                }).join('');
                
                document.getElementById('qualityList').innerHTML = listHtml;
                document.getElementById('qualityModal').style.display = 'flex';
            }
            function closeQualityModal() { document.getElementById('qualityModal').style.display = 'none'; }

            function handleQualityClick(fileId, isUnlocked) {
                closeQualityModal();
                if(isUnlocked || isUserVip) { 
                    sendFile(fileId); 
                } else { 
                    activeFileId = fileId; currentAdStep = 1; startAdTimer(); 
                }
            }
            
            function playOnlineVideo(movieId, movieTitle) {
                closeQualityModal();
                const player = document.getElementById('onlinePlayer');
                document.getElementById('videoTitleBar').innerText = movieTitle;
                player.src = `/api/stream/${movieId}?uid=${uid}`;
                document.getElementById('videoScreen').style.display = 'flex';
                player.play();
            }

            function closeVideo() {
                const player = document.getElementById('onlinePlayer');
                player.pause();
                player.src = ""; 
                document.getElementById('videoScreen').style.display = 'none';
            }

            function startAdTimer() {
                if (typeof window['show_' + ZONE_ID] === 'function') window['show_' + ZONE_ID]();
                document.getElementById('adScreen').style.display = 'flex';
                document.getElementById('timerUI').style.display = 'flex';
                document.getElementById('nextAdBtn').style.display = 'none';
                document.getElementById('adStepText').innerText = `অ্যাড: ${currentAdStep}/${REQUIRED_ADS}`;
                let t = 15; document.getElementById('timer').innerText = t;
                let iv = setInterval(() => {
                    t--; document.getElementById('timer').innerText = t;
                    if(t <= 0) { 
                        clearInterval(iv); 
                        if(currentAdStep < REQUIRED_ADS) {
                            document.getElementById('timerUI').style.display = 'none';
                            document.getElementById('nextAdBtn').style.display = 'block';
                            document.getElementById('nextAdBtn').innerHTML = `পরবর্তী অ্যাড দেখুন (${currentAdStep + 1}/${REQUIRED_ADS}) <i class="fa-solid fa-arrow-right"></i>`;
                        } else { sendFile(activeFileId); }
                    }
                }, 1000);
            }
            function nextAdStep() { currentAdStep++; startAdTimer(); }

            async function sendFile(id) {
                try {
                    const res = await fetch('/api/send', { 
                        method: 'POST', headers: {'Content-Type': 'application/json'}, 
                        body: JSON.stringify({userId: uid, movieId: id, initData: INIT_DATA})
                    });
                    const responseData = await res.json();
                    if(!responseData.ok) return alert("⚠️ Security verification failed! Please open via Telegram App.");
                    
                    document.getElementById('adScreen').style.display = 'none';
                    document.getElementById('successModal').style.display = 'flex';
                    setTimeout(() => { loadTrending(); loadMovies(currentPage); }, 1000); 
                } catch (e) { console.error(e); }
            }

            function openReqModal() { document.getElementById('reqModal').style.display = 'flex'; document.getElementById('reqText').focus(); }
            async function sendReq() {
                const text = document.getElementById('reqText').value;
                if(!text) return alert('মুভির নাম লিখুন!');
                try {
                    await fetch('/api/request', { 
                        method: 'POST', headers: {'Content-Type': 'application/json'}, 
                        body: JSON.stringify({uid: uid, uname: tg.initDataUnsafe.user?.first_name || 'Guest', movie: text, initData: INIT_DATA})
                    });
                    document.getElementById('reqModal').style.display = 'none';
                    document.getElementById('reqText').value = '';
                    alert('রিকোয়েস্ট সফলভাবে পাঠানো হয়েছে!');
                } catch (e) { console.error(e); }
            }

            // Init App
            fetchUserInfo();
            loadTrending();
            loadUpcoming();
            loadMovies(1); 
        </script>
    </body>
    </html>
    """
    html_code = html_code.replace("{{ZONE_ID}}", zone_id).replace("{{TG_LINK}}", tg_url).replace("{{LINK_18}}", link_18).replace("{{AD_COUNT}}", str(required_ads)).replace("{{BOT_USER}}", BOT_USERNAME)
    return html_code


# ==========================================
# 13. Main Web App APIs
# ==========================================
@app.get("/api/user/{uid}")
async def get_user_info(uid: int):
    user = await db.users.find_one({"user_id": uid})
    if not user: return {"vip": False, "refer_count": 0, "vip_expiry": None}
    
    vip_until = user.get("vip_until")
    now = datetime.datetime.utcnow()
    is_vip = False
    vip_expiry_str = None
    
    if vip_until and vip_until > now:
        is_vip = True
        vip_expiry_str = vip_until.strftime("%d %b %Y")
        
    return {
        "vip": is_vip,
        "refer_count": user.get("refer_count", 0),
        "vip_expiry": vip_expiry_str
    }

@app.get("/api/trending")
async def trending_movies(uid: int = 0):
    if uid in banned_cache: return {"error": "banned"}
    unlocked_movie_ids = []
    if uid != 0:
        time_limit = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
        async for u in db.user_unlocks.find({"user_id": uid, "unlocked_at": {"$gt": time_limit}}):
            unlocked_movie_ids.append(u["movie_id"])

    pipeline = [
        {"$group": {"_id": "$title", "photo_id": {"$first": "$photo_id"}, "clicks": {"$sum": "$clicks"}, "files": {"$push": {"id": {"$toString": "$_id"}, "quality": {"$ifNull": ["$quality", "Main File"]}}}}},
        {"$sort": {"clicks": -1}}, {"$limit": 10}
    ]
    movies = await db.movies.aggregate(pipeline).to_list(10)
    for m in movies:
        for f in m["files"]:
            f["is_unlocked"] = f["id"] in unlocked_movie_ids
    return movies

@app.get("/api/upcoming")
async def upcoming_movies():
    pipeline = [{"$sort": {"added_at": -1}}, {"$limit": 10}]
    movies = await db.upcoming.aggregate(pipeline).to_list(10)
    return [{"photo_id": m["photo_id"], "title": m.get("title", "")} for m in movies]

@app.get("/api/list")
async def list_movies(page: int = 1, q: str = "", uid: int = 0):
    if uid in banned_cache: return {"error": "banned"}
    limit = 16
    skip = (page - 1) * limit
    unlocked_ids = []
    
    if uid != 0:
        time_limit = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
        async for u in db.user_unlocks.find({"user_id": uid, "unlocked_at": {"$gt": time_limit}}):
            unlocked_ids.append(u["movie_id"])

    match_stage = {"title": {"$regex": q, "$options": "i"}} if q else {}
    pipeline = [
        {"$match": match_stage},
        {"$group": {"_id": "$title", "photo_id": {"$first": "$photo_id"}, "clicks": {"$sum": "$clicks"}, "created_at": {"$max": "$created_at"}, "files": {"$push": {"id": {"$toString": "$_id"}, "quality": {"$ifNull": ["$quality", "Main File"]}}}}},
        {"$sort": {"created_at": -1}}, {"$skip": skip}, {"$limit": limit}
    ]
    count_pipe = [{"$match": match_stage}, {"$group": {"_id": "$title"}}, {"$count": "total"}]
    c_res = await db.movies.aggregate(count_pipe).to_list(1)
    total_groups = c_res[0]["total"] if c_res else 0
    total_pages = (total_groups + limit - 1) // limit

    movies = await db.movies.aggregate(pipeline).to_list(limit)
    for m in movies:
        for f in m["files"]:
            f["is_unlocked"] = f["id"] in unlocked_ids
    return {"movies": movies, "total_pages": total_pages}

@app.get("/api/image/{photo_id}")
async def get_image(photo_id: str):
    try:
        cache = await db.file_cache.find_one({"photo_id": photo_id})
        now = datetime.datetime.utcnow()
        if cache and cache.get("expires_at", now) > now:
            file_path = cache["file_path"]
        else:
            file_info = await bot.get_file(photo_id)
            file_path = file_info.file_path
            await db.file_cache.update_one({"photo_id": photo_id}, {"$set": {"file_path": file_path, "expires_at": now + datetime.timedelta(minutes=50)}}, upsert=True)
            
        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
        async def stream_image():
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as resp:
                    async for chunk in resp.content.iter_chunked(1024): yield chunk
        return StreamingResponse(stream_image(), media_type="image/jpeg")
    except Exception: 
        return {"error": "not found"}


# ==========================================
# 14. File Sender & Streaming API
# ==========================================
class SendRequestModel(BaseModel):
    userId: int
    movieId: str
    initData: str

@app.post("/api/send")
async def send_file(d: SendRequestModel):
    if d.userId == 0 or d.userId in banned_cache or not validate_tg_data(d.initData): return {"ok": False, "error": "Security validation failed"}
    try:
        m = await db.movies.find_one({"_id": ObjectId(d.movieId)})
        if m:
            time_cfg = await db.settings.find_one({"id": "del_time"})
            del_minutes = time_cfg['minutes'] if time_cfg else 60
            protect_cfg = await db.settings.find_one({"id": "protect_content"})
            is_protected = protect_cfg['status'] if protect_cfg else True
            q_text = m.get("quality", "")
            title_text = f"{m['title']} [{q_text}]" if q_text else m['title']
            
            caption = (f"🎥 <b>{title_text}</b>\n\n⏳ <b>সতর্কতা:</b> কপিরাইট এড়াতে মুভিটি <b>{del_minutes} মিনিট</b> পর অটো-ডিলিট হয়ে যাবে। "
                       f"দয়া করে এখনই ফরওয়ার্ড বা সেভ করে নিন!\n\n📥 Join: @TGLinkBase")
            
            if m.get("file_type") == "video": sent_msg = await bot.send_video(d.userId, m['file_id'], caption=caption, parse_mode="HTML", protect_content=is_protected)
            else: sent_msg = await bot.send_document(d.userId, m['file_id'], caption=caption, parse_mode="HTML", protect_content=is_protected)
            
            await db.movies.update_one({"_id": ObjectId(d.movieId)}, {"$inc": {"clicks": 1}})
            await db.user_unlocks.update_one({"user_id": d.userId, "movie_id": d.movieId}, {"$set": {"unlocked_at": datetime.datetime.utcnow()}}, upsert=True)
            
            if sent_msg:
                delete_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=del_minutes)
                await db.auto_delete.insert_one({"chat_id": d.userId, "message_id": sent_msg.message_id, "delete_at": delete_at})
    except Exception as e: print(f"Error sending file: {e}")
    return {"ok": True}

class ReqModel(BaseModel):
    uid: int
    uname: str
    movie: str
    initData: str

@app.post("/api/request")
async def handle_request(data: ReqModel):
    if data.uid in banned_cache or not validate_tg_data(data.initData): return {"ok": False}
    try: 
        builder = InlineKeyboardBuilder()
        builder.button(text="✍️ রিপ্লাই দিন", callback_data=f"reply_{data.uid}")
        await bot.send_message(OWNER_ID, f"🔔 <b>নতুন মুভি রিকোয়েস্ট!</b>\n\n👤 ইউজার: {data.uname} (<code>{data.uid}</code>)\n🎬 মুভির নাম: <b>{data.movie}</b>", parse_mode="HTML", reply_markup=builder.as_markup())
    except Exception: pass
    return {"ok": True}

@app.get("/api/stream/{movie_id}")
async def stream_video_api(request: Request, movie_id: str, uid: int):
    # শুধুমাত্র VIP ইউজাররাই স্ট্রিম করতে পারবে
    user = await db.users.find_one({"user_id": uid})
    now = datetime.datetime.utcnow()
    if not user or user.get("vip_until", now) < now:
        raise HTTPException(status_code=403, detail="Only VIP members can stream.")
        
    movie = await db.movies.find_one({"_id": ObjectId(movie_id)})
    if not movie or not movie.get("stream_msg_id"):
        raise HTTPException(status_code=404, detail="Stream unavailable")
        
    msg_id = movie["stream_msg_id"]
    
    try:
        msg = await pyro_client.get_messages(DUMP_CHANNEL_ID, msg_id)
        media = msg.video or msg.document
        file_size = media.file_size
    except Exception:
        raise HTTPException(status_code=404, detail="File unavailable")

    range_header = request.headers.get("Range", "")
    start = 0
    end = file_size - 1

    if range_header:
        byte_range = range_header.replace("bytes=", "").split("-")
        start = int(byte_range[0])
        if byte_range[1]:
            end = int(byte_range[1])

    limit = end - start + 1

    async def file_generator():
        async for chunk in pyro_client.stream_media(msg, limit=limit, offset=start):
            yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(limit),
        "Content-Type": "video/mp4",
        "Cache-Control": "public, max-age=86400" # ক্যাশিং যুক্ত করা হলো যাতে ভিডিও বারবার লোড না নেয়
    }
    
    status_code = 206 if range_header else 200
    return StreamingResponse(file_generator(), status_code=status_code, headers=headers)


# ==========================================
# 15. Main Application Startup
# ==========================================
async def start():
    print("Initializing Database & Cache...")
    await init_db()
    await load_admins()
    await load_banned_users()
    
    port = int(os.getenv("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="asyncio")
    server = uvicorn.Server(config)
    
    print("Starting Background Workers...")
    asyncio.create_task(auto_delete_worker())
    
    print("Starting Pyrogram Streamer...")
    await pyro_client.start() 
    
    print("Connecting to Telegram Bot API...")
    await bot.delete_webhook(drop_pending_updates=True)
    
    print("Server is Running!")
    await asyncio.gather(server.serve(), dp.start_polling(bot))

if __name__ == "__main__": 
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start())
