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


# ==========================================
# 1. Configuration & Global Variables
# ==========================================
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URI")
OWNER_ID = int(os.getenv("ADMIN_ID", "0"))
APP_URL = os.getenv("APP_URL")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003188773719") 
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin123") 

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()
security = HTTPBasic()

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


# ==========================================
# 3. Database Initialization & Caching
# ==========================================
async def load_admins():
    """Load all admins from DB to cache on startup"""
    admin_cache.clear()
    admin_cache.add(OWNER_ID)
    async for admin in db.admins.find():
        admin_cache.add(admin["user_id"])

async def load_banned_users():
    """Load all banned users from DB to cache on startup"""
    banned_cache.clear()
    async for b_user in db.banned.find():
        banned_cache.add(b_user["user_id"])

async def init_db():
    """Create indexes for faster database search performance"""
    await db.movies.create_index([("title", "text")])
    await db.movies.create_index("title")
    await db.movies.create_index("created_at")
    await db.auto_delete.create_index("delete_at")
    await db.users.create_index("joined_at")


# ==========================================
# 4. Security & Authentication Methods
# ==========================================
def validate_tg_data(init_data: str) -> bool:
    """Validates if the request is genuinely from Telegram Web App"""
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        hash_val = parsed_data.pop('hash', None)
        auth_date = int(parsed_data.get('auth_date', 0))
        
        # Check if the data is older than 24 hours (Security)
        if not hash_val or time.time() - auth_date > 86400: 
            return False
            
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        return calculated_hash == hash_val
    except Exception: 
        return False

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    """Verifies the Admin Login for the Web Panel"""
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
    """Background loop to delete messages after set time"""
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
# 6. Telegram Bot Commands (General)
# ==========================================
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    
    # Check if user is banned
    if uid in banned_cache: 
        return await message.answer(
            "🚫 <b>আপনাকে এই বট থেকে স্থায়ীভাবে ব্যান করা হয়েছে।</b>", 
            parse_mode="HTML"
        )
        
    await state.clear()
    now = datetime.datetime.utcnow()
    
    # Add User to Database if new
    await db.users.update_one(
        {"user_id": uid}, 
        {
            "$set": {"first_name": message.from_user.first_name}, 
            "$setOnInsert": {"joined_at": now}
        }, 
        upsert=True
    )
    
    kb = [
        [types.InlineKeyboardButton(text="🎬 Watch Now", web_app=types.WebAppInfo(url=APP_URL))]
    ]
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
            "🔸 ব্যান: <code>/ban ID</code> | আনব্যান: <code>/unban ID</code>\n\n"
            f"🌐 <b>অ্যাডমিন প্যানেল:</b> <a href='{APP_URL}/admin'>এখানে ক্লিক করুন</a>\n"
            "<i>লগিন: admin / admin123</i>\n\n"
            "📥 <b>মুভি অ্যাড করতে প্রথমে ভিডিও বা ডকুমেন্ট ফাইল পাঠান।</b>"
        )
    else: 
        text = (
            f"👋 <b>স্বাগতম {message.from_user.first_name}!</b>\n\n"
            "মুভি দেখতে নিচের বাটনে ক্লিক করুন।"
        )
        
    await message.answer(
        text, 
        reply_markup=markup, 
        parse_mode="HTML", 
        disable_web_page_preview=True
    )


# ==========================================
# 7. Telegram Bot Commands (Admin Settings)
# ==========================================
@dp.message(Command("stats"))
async def stats_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: 
        return
        
    uc = await db.users.count_documents({})
    mc = await db.movies.count_documents({})
    
    now = datetime.datetime.utcnow()
    today_start = datetime.datetime(now.year, now.month, now.day)
    new_users_today = await db.users.count_documents({"joined_at": {"$gte": today_start}})
    
    top_pipeline = [
        {
            "$group": {
                "_id": "$title", 
                "clicks": {"$sum": "$clicks"}
            }
        },
        {"$sort": {"clicks": -1}}, 
        {"$limit": 5}
    ]
    top_movies = await db.movies.aggregate(top_pipeline).to_list(5)
    
    top_movies_text = ""
    for idx, mv in enumerate(top_movies, 1):
        top_movies_text += f"{idx}. {mv['_id'][:20]}... - <b>{mv['clicks']} views</b>\n"
    
    text = (
        f"📊 <b>অ্যাডভান্সড স্ট্যাটাস:</b>\n\n"
        f"👥 মোট ইউজার: <code>{uc}</code>\n"
        f"🟢 আজকের নতুন ইউজার: <code>{new_users_today}</code>\n"
        f"🎬 মোট ফাইল আপলোড: <code>{mc}</code>\n\n"
        f"🔥 <b>টপ ৫ মুভি/সিরিজ:</b>\n"
        f"{top_movies_text if top_movies_text else 'কোনো মুভি নেই'}"
    )
    
    await m.answer(text, parse_mode="HTML")

@dp.message(Command("ban"))
async def ban_user_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: 
        return
    try:
        target_uid = int(m.text.split()[1])
        if target_uid in admin_cache: 
            return await m.answer("⚠️ অ্যাডমিনকে ব্যান করা যাবে না!")
            
        await db.banned.update_one(
            {"user_id": target_uid}, 
            {"$set": {"user_id": target_uid}}, 
            upsert=True
        )
        banned_cache.add(target_uid)
        
        await m.answer(f"🚫 ইউজার <code>{target_uid}</code> কে ব্যান করা হয়েছে!", parse_mode="HTML")
    except Exception: 
        await m.answer("⚠️ সঠিক নিয়ম: <code>/ban ইউজার_আইডি</code>", parse_mode="HTML")

@dp.message(Command("unban"))
async def unban_user_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: 
        return
    try:
        target_uid = int(m.text.split()[1])
        await db.banned.delete_one({"user_id": target_uid})
        banned_cache.discard(target_uid)
        
        await m.answer(f"✅ ইউজার <code>{target_uid}</code> আনব্যান হয়েছে!", parse_mode="HTML")
    except Exception: 
        await m.answer("⚠️ সঠিক নিয়ম: <code>/unban ইউজার_আইডি</code>", parse_mode="HTML")

@dp.message(Command("setadcount"))
async def set_ad_count_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: 
        return
    try:
        count = int(m.text.split(" ")[1])
        if count < 1: 
            count = 1
        await db.settings.update_one(
            {"id": "ad_count"}, 
            {"$set": {"count": count}}, 
            upsert=True
        )
        await m.answer(f"✅ অ্যাড দেখার সংখ্যা সেট করা হয়েছে: <b>{count} টি</b>।", parse_mode="HTML")
    except Exception: 
        await m.answer("⚠️ সঠিক নিয়ম: <code>/setadcount 3</code>", parse_mode="HTML")

@dp.message(Command("protect"))
async def protect_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: 
        return
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
    if m.from_user.id not in admin_cache: 
        return
    try:
        mins = int(m.text.split(" ")[1])
        await db.settings.update_one({"id": "del_time"}, {"$set": {"minutes": mins}}, upsert=True)
        await m.answer("✅ অটো-ডিলিট টাইম সেট করা হয়েছে।")
    except Exception: 
        await m.answer("⚠️ সঠিক নিয়ম: <code>/settime 60</code> (মিনিট)", parse_mode="HTML")

@dp.message(Command("setad"))
async def set_ad(m: types.Message):
    if m.from_user.id not in admin_cache: 
        return
    try:
        zone = m.text.split(" ")[1]
        await db.settings.update_one({"id": "ad_config"}, {"$set": {"zone_id": zone}}, upsert=True)
        await m.answer("✅ জোন আপডেট হয়েছে।")
    except Exception: 
        await m.answer("⚠️ সঠিক নিয়ম: <code>/setad 1234567</code>")


# ==========================================
# 8. Movie Upload Logic (Quality & Episode Flow)
# ==========================================
@dp.message(F.content_type.in_({'video', 'document'}), lambda m: m.from_user.id in admin_cache)
async def receive_movie_file(m: types.Message, state: FSMContext):
    fid = m.video.file_id if m.video else m.document.file_id
    ftype = "video" if m.video else "document"
    
    await state.set_state(AdminStates.waiting_for_photo)
    await state.update_data(file_id=fid, file_type=ftype)
    
    await m.answer(
        "✅ ফাইল পেয়েছি! এবার মুভির <b>পোস্টার (Photo)</b> সেন্ড করুন।\n"
        "বাতিল করতে /start দিন।", 
        parse_mode="HTML"
    )

@dp.message(AdminStates.waiting_for_photo, F.photo)
async def receive_movie_photo(m: types.Message, state: FSMContext):
    await state.update_data(photo_id=m.photo[-1].file_id)
    await state.set_state(AdminStates.waiting_for_title)
    
    await m.answer(
        "✅ পোস্টার পেয়েছি! এবার <b>মুভি বা ওয়েব সিরিজের নাম</b> লিখে পাঠান।\n"
        "<i>(নোট: যদি ওয়েব সিরিজ হয় বা একই মুভির অন্য কোয়ালিটি অ্যাড করতে চান, তবে আগের নামটিই হুবহু দিন)</i>", 
        parse_mode="HTML"
    )

@dp.message(AdminStates.waiting_for_title, F.text)
async def receive_movie_title(m: types.Message, state: FSMContext):
    await state.update_data(title=m.text.strip())
    await state.set_state(AdminStates.waiting_for_quality)
    
    await m.answer(
        "✅ নাম সেভ হয়েছে! এবার এই ফাইলটির <b>কোয়ালিটি বা এপিসোড নাম্বার</b> দিন।\n"
        "<i>(উদাহরণ: 480p, 720p, 1080p অথবা Episode 01, Episode 02)</i>", 
        parse_mode="HTML"
    )

@dp.message(AdminStates.waiting_for_quality, F.text)
async def receive_movie_quality(m: types.Message, state: FSMContext):
    quality = m.text.strip()
    data = await state.get_data()
    await state.clear()
    
    title = data["title"]
    photo_id = data["photo_id"]
    
    # Save the movie document
    await db.movies.insert_one({
        "title": title, 
        "quality": quality, 
        "photo_id": photo_id, 
        "file_id": data["file_id"], 
        "file_type": data["file_type"], 
        "clicks": 0, 
        "created_at": datetime.datetime.utcnow()
    })
    
    await m.answer(f"🎉 <b>{title} [{quality}]</b> অ্যাপে সফলভাবে যুক্ত করা হয়েছে!", parse_mode="HTML")
    
    # Send Notification to Channel
    if CHANNEL_ID and CHANNEL_ID != "-100XXXXXXXXXX":
        try:
            bot_info = await bot.get_me()
            kb = [
                [types.InlineKeyboardButton(text="🎬 মুভিটি দেখতে এখানে ক্লিক করুন", url=f"https://t.me/{bot_info.username}?start=new")]
            ]
            markup = types.InlineKeyboardMarkup(inline_keyboard=kb)
            
            caption = (
                f"🎬 <b>নতুন ফাইল যুক্ত হয়েছে!</b>\n\n"
                f"📌 <b>নাম:</b> {title}\n"
                f"🏷 <b>কোয়ালিটি/এপিসোড:</b> {quality}\n\n"
                f"👇 <i>দেখতে নিচের বাটনে ক্লিক করুন।</i>"
            )
            
            await bot.send_photo(
                chat_id=CHANNEL_ID, 
                photo=photo_id, 
                caption=caption, 
                parse_mode="HTML", 
                reply_markup=markup
            )
        except Exception as e: 
            print(f"Error sending channel notification: {e}")


# ==========================================
# 9. Broadcast & User Reply System
# ==========================================
@dp.message(Command("cast"))
async def broadcast_prep(m: types.Message, state: FSMContext):
    if m.from_user.id not in admin_cache: 
        return
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
            await asyncio.sleep(0.05) # Rate limit protection
        except Exception: 
            pass
            
    await m.answer(f"✅ সম্পন্ন! সর্বমোট <b>{success}</b> জনকে মেসেজ পাঠানো হয়েছে।", parse_mode="HTML")

@dp.callback_query(F.data.startswith("reply_"))
async def process_reply_cb(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id not in admin_cache: 
        return
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
# 10. Web Admin Panel API & HTML
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
            <!-- Header -->
            <div class="flex justify-between items-center mb-8 border-b border-gray-700 pb-4">
                <h1 class="text-3xl font-bold text-red-500">
                    <i class="fa-solid fa-shield-halved"></i> MovieZone Admin
                </h1>
            </div>
            
            <!-- Statistics Cards -->
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

            <!-- Movie Management Table -->
            <div class="bg-gray-800 rounded-xl shadow-lg border border-gray-700 p-6">
                <h2 class="text-xl font-bold mb-4 text-gray-200">
                    <i class="fa-solid fa-film text-red-400"></i> Manage Movies (Grouped)
                </h2>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm whitespace-nowrap">
                        <thead class="bg-gray-700 text-gray-300">
                            <tr>
                                <th class="p-4 rounded-tl-lg">Movie / Series Title</th>
                                <th class="p-4">Total Views</th>
                                <th class="p-4">Files/Episodes</th>
                                <th class="p-4 rounded-tr-lg">Action</th>
                            </tr>
                        </thead>
                        <tbody id="movieTableBody">
                            <tr>
                                <td colspan="4" class="text-center p-8 text-gray-400">Loading data...</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Admin Logic -->
        <script>
            async function loadAdminData() {
                try {
                    const res = await fetch('/api/admin/data');
                    const data = await res.json();
                    
                    document.getElementById('statUsers').innerText = data.total_users;
                    document.getElementById('statMovies').innerText = data.total_groups;
                    document.getElementById('statNew').innerText = data.new_users_today;
                    
                    let html = '';
                    data.movies.forEach(m => {
                        html += `
                        <tr class="border-b border-gray-700 hover:bg-gray-750 transition">
                            <td class="p-4 font-medium text-base">` + m._id + `</td>
                            <td class="p-4 text-gray-400 font-bold">
                                <i class="fa-solid fa-eye text-gray-500"></i> ` + m.clicks + `
                            </td>
                            <td class="p-4 text-green-400 font-bold">` + m.file_count + `</td>
                            <td class="p-4 flex gap-4">
                                <button onclick="editMovie('`+encodeURIComponent(m._id)+`', '`+m._id.replace(/'/g, "\\'")+`')" class="text-blue-400 bg-blue-900 bg-opacity-30 px-3 py-1 rounded">
                                    <i class="fa-solid fa-pen-to-square"></i> Edit Name
                                </button>
                                <button onclick="deleteMovie('`+encodeURIComponent(m._id)+`')" class="text-red-400 bg-red-900 bg-opacity-30 px-3 py-1 rounded">
                                    <i class="fa-solid fa-trash"></i> Delete All
                                </button>
                            </td>
                        </tr>`;
                    });
                    
                    document.getElementById('movieTableBody').innerHTML = html;
                } catch (e) { 
                    alert("Error loading data from the server!"); 
                }
            }

            async function deleteMovie(encodedTitle) {
                if(!confirm('Are you absolutely sure you want to delete ALL files for this movie?')) {
                    return;
                }
                
                await fetch('/api/admin/movie/' + encodedTitle, {
                    method: 'DELETE'
                });
                
                loadAdminData();
            }

            async function editMovie(encodedTitle, oldTitle) {
                let newTitle = prompt("Enter new title for all files in this group:", oldTitle);
                
                if(newTitle && newTitle.trim() !== "" && newTitle !== oldTitle) {
                    await fetch('/api/admin/movie/' + encodedTitle, {
                        method: 'PUT', 
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({title: newTitle.trim()})
                    });
                    loadAdminData();
                }
            }
            
            // Initial Load
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
    
    # Aggregation to group movies by title for admin view
    pipeline = [
        {
            "$group": {
                "_id": "$title", 
                "clicks": {"$sum": "$clicks"}, 
                "file_count": {"$sum": 1}, 
                "created_at": {"$max": "$created_at"}
            }
        },
        {"$sort": {"created_at": -1}}, 
        {"$limit": 50}
    ]
    movies = await db.movies.aggregate(pipeline).to_list(50)
    
    return {
        "total_users": uc, 
        "total_groups": len(movies), 
        "new_users_today": new_users, 
        "movies": movies
    }

@app.delete("/api/admin/movie/{title}")
async def delete_movie_api(title: str, auth: bool = Depends(verify_admin)):
    # Deletes ALL files associated with the group title
    await db.movies.delete_many({"title": title})
    return {"ok": True}

@app.put("/api/admin/movie/{title}")
async def edit_movie_api(title: str, data: dict = Body(...), auth: bool = Depends(verify_admin)):
    new_title = data.get("title")
    if new_title: 
        await db.movies.update_many({"title": title}, {"$set": {"title": new_title}})
    return {"ok": True}


# ==========================================
# 11. Main Web App UI (Frontend)
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
            /* 
             * Base Styles & Resets
             */
            * { 
                margin: 0; 
                padding: 0; 
                box-sizing: border-box; 
            }
            html { 
                scroll-behavior: smooth; 
            }
            body { 
                background: #0f172a; 
                font-family: sans-serif; 
                color: #fff; 
                -webkit-font-smoothing: antialiased; 
                overscroll-behavior-y: none;
            } 
            
            /* 
             * Header Styles
             */
            header { 
                display: flex; 
                justify-content: space-between; 
                align-items: center; 
                padding: 15px; 
                border-bottom: 1px solid #1e293b; 
                position: sticky; 
                top: 0; 
                background: rgba(15, 23, 42, 0.95); 
                backdrop-filter: blur(10px); 
                z-index: 1000; 
            }
            .logo { 
                font-size: 24px; 
                font-weight: bold; 
            }
            .logo span { 
                background: red; 
                color: #fff; 
                padding: 2px 6px; 
                border-radius: 5px; 
                margin-left: 5px; 
                font-size: 16px; 
            }
            .user-info { 
                display: flex; 
                align-items: center; 
                gap: 8px; 
                background: #1e293b; 
                padding: 6px 14px; 
                border-radius: 25px; 
                font-weight: bold; 
                font-size: 14px; 
                border: 1px solid #334155; 
            }
            .user-info img { 
                width: 28px; 
                height: 28px; 
                border-radius: 50%; 
                object-fit: cover; 
            }
            
            /* 
             * Search Box Styles
             */
            .search-box { 
                padding: 15px; 
            }
            .search-input { 
                width: 100%; 
                padding: 16px; 
                border-radius: 25px; 
                border: none; 
                outline: none; 
                text-align: center; 
                background: #1e293b; 
                color: #fff; 
                font-size: 18px; 
                font-weight: bold;
                transition: 0.3s; 
                box-shadow: inset 0 2px 5px rgba(0,0,0,0.3); 
            }
            .search-input::placeholder { 
                color: #94a3b8; 
                font-weight: 500; 
                font-size: 16px; 
            }
            .search-input:focus { 
                box-shadow: 0 0 15px rgba(248,113,113,0.7); 
            }
            
            /* 
             * Enhanced Section Titles 
             */
            .section-title { 
                padding: 5px 15px 15px; 
                font-size: 22px; 
                font-weight: 900; 
                display: flex; 
                align-items: center; 
                gap: 8px;
                background: linear-gradient(45deg, #ff416c, #ff4b2b);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                text-shadow: 0px 4px 15px rgba(255, 75, 43, 0.4);
            }
            .section-title i { 
                -webkit-text-fill-color: #ff416c; 
            }
            
            /* 
             * Trending Horizontal Slider
             */
            .trending-container { 
                display: flex; 
                overflow-x: auto; 
                gap: 15px; 
                padding: 0 15px 20px; 
                scroll-behavior: smooth; 
                -webkit-overflow-scrolling: touch; 
            }
            .trending-container::-webkit-scrollbar { 
                display: none; 
            }
            .trending-card { 
                min-width: 140px; 
                max-width: 140px; 
                background: #1e293b; 
                border-radius: 12px; 
                overflow: hidden; 
                cursor: pointer; 
                flex-shrink: 0; 
                position: relative; 
                transition: transform 0.2s; 
            }
            .trending-card:active { 
                transform: scale(0.95); 
            }
            .trending-card img { 
                height: 200px; 
                object-fit: cover; 
                width: 100%; 
                border-radius: 10px; 
                display: block; 
            }
            
            /* 
             * Movie Grid (Main Content)
             */
            .grid { 
                padding: 0 15px 20px; 
                display: grid; 
                grid-template-columns: repeat(2, 1fr); 
                gap: 15px; 
            }
            .card { 
                background: #1e293b; 
                border-radius: 12px; 
                overflow: hidden; 
                cursor: pointer; 
                transition: transform 0.2s, box-shadow 0.2s; 
            }
            .card:active { 
                transform: scale(0.95); 
            }
            
            /* Card Post Content & Glowing Effect */
            .post-content { 
                position: relative; 
                padding: 3px; 
                border-radius: 12px; 
                background: linear-gradient(45deg, #ff0000, #ff7300, #fffb00, #48ff00, #00ffd5, #002bff, #7a00ff, #ff00c8, #ff0000); 
                background-size: 400%; 
                animation: glowing 8s linear infinite; 
            }
            
            @keyframes glowing { 
                0% { background-position: 0 0; } 
                50% { background-position: 400% 0; } 
                100% { background-position: 0 0; } 
            }
            
            .post-content img { 
                width: 100%; 
                height: 230px; 
                object-fit: cover; 
                display: block; 
                border-radius: 10px; 
            }
            
            /* Card Badges */
            .top-badge { 
                position: absolute; 
                top: 10px; 
                left: 10px; 
                background: linear-gradient(45deg, #ff0000, #cc0000); 
                color: white; 
                padding: 4px 8px; 
                border-radius: 6px; 
                font-size: 11px; 
                font-weight: bold; 
                z-index: 10;
            }
            
            .view-badge { 
                position: absolute; 
                bottom: 10px; 
                left: 10px; 
                background: rgba(0,0,0,0.75); 
                color: #fff; 
                padding: 4px 8px; 
                border-radius: 6px; 
                font-size: 12px; 
                font-weight: bold; 
                display: flex; 
                align-items: center; 
                gap: 5px; 
            }
            
            .ep-badge { 
                position: absolute; 
                top: 10px; 
                right: 10px; 
                background: #10b981; 
                color: white; 
                padding: 4px 8px; 
                border-radius: 6px; 
                font-size: 11px; 
                font-weight: bold; 
                z-index: 10;
            }

            .card-footer { 
                padding: 12px; 
                font-size: 14px; 
                font-weight: bold; 
                text-align: center; 
                color: #f8fafc; 
                line-height: 1.4; 
                white-space: normal; 
                word-wrap: break-word; 
                display: block; 
            }
            
            /* Skeleton Loading Effect */
            .skeleton { 
                background: #1e293b; 
                border-radius: 12px; 
                height: 260px; 
                overflow: hidden; 
                position: relative; 
            }
            .skeleton::after { 
                content: ""; 
                position: absolute; 
                top: 0; 
                left: 0; 
                width: 100%; 
                height: 100%; 
                background: linear-gradient(90deg, transparent, rgba(255,255,255,0.05), transparent); 
                animation: shimmer 1.5s infinite; 
            }
            @keyframes shimmer { 
                0% { transform: translateX(-100%); } 
                100% { transform: translateX(100%); } 
            }

            /* Pagination */
            .pagination { 
                display: flex; 
                justify-content: center; 
                align-items: center; 
                gap: 8px; 
                padding: 10px 15px 120px; 
                flex-wrap: wrap; 
            }
            .page-btn { 
                background: #1e293b; 
                color: #fff; 
                border: 1px solid #334155; 
                padding: 10px 16px; 
                border-radius: 8px; 
                cursor: pointer; 
                font-weight: bold; 
                transition: 0.3s; 
                outline: none; 
            }
            .page-btn.active { 
                background: #f87171; 
                border-color: #f87171; 
                color: white; 
                box-shadow: 0 0 10px rgba(248,113,113,0.4);
            }
            .page-btn:disabled { 
                opacity: 0.4; 
                cursor: not-allowed; 
            }

            /* Floating Buttons */
            .floating-btn { 
                position: fixed; 
                right: 20px; 
                color: white; 
                width: 50px; 
                height: 50px; 
                border-radius: 50%; 
                display: flex; 
                align-items: center; 
                justify-content: center; 
                font-size: 22px; 
                z-index: 500; 
                cursor: pointer; 
                box-shadow: 0 4px 15px rgba(0,0,0,0.5); 
                transition: 0.3s;
            }
            .floating-btn:active { 
                transform: scale(0.9); 
            }
            .btn-18 { 
                bottom: 155px; 
                background: linear-gradient(45deg, #ff0000, #990000); 
                border: 2px solid #fff; 
                font-weight: bold; 
                font-size: 18px;
            }
            .btn-tg { 
                bottom: 95px; 
                background: linear-gradient(45deg, #24A1DE, #1b7ba8); 
            }
            .btn-req { 
                bottom: 35px; 
                background: linear-gradient(45deg, #10b981, #059669); 
            }

            /* 
             * Modals & Texts (Quality Select, Success, Request)
             */
            .modal { 
                position: fixed; 
                top: 0; 
                left: 0; 
                width: 100%; 
                height: 100%; 
                background: rgba(0,0,0,0.85); 
                display: none; 
                align-items: center; 
                justify-content: center; 
                z-index: 3000; 
                backdrop-filter: blur(5px);
            }
            .modal-content { 
                background: #1e293b; 
                width: 92%; 
                max-width: 400px; 
                padding: 25px; 
                border-radius: 20px; 
                text-align: center; 
                border: 1px solid #334155; 
                max-height: 85vh; 
                overflow-y: auto;
            }
            
            .instruction-text { 
                color: #fbbf24; 
                font-size: 15.5px; 
                font-weight: bold; 
                margin-bottom: 20px; 
                line-height: 1.5; 
            }
            
            .quality-btn { 
                display: flex; 
                justify-content: space-between; 
                align-items: center; 
                background: #0f172a; 
                border: 1px solid #334155; 
                padding: 16px; 
                border-radius: 12px; 
                margin-bottom: 12px; 
                color: white; 
                font-weight: bold; 
                font-size: 16px; 
                cursor: pointer; 
                transition: 0.3s; 
                width: 100%;
            }
            .quality-btn:active { 
                transform: scale(0.98); 
            }
            .quality-locked { 
                border-left: 5px solid #ef4444; 
            }
            .quality-unlocked { 
                border-left: 5px solid #10b981; 
            }

            .close-btn { 
                background: #334155; 
                color: white; 
                padding: 12px 20px; 
                border-radius: 12px; 
                margin-top: 15px; 
                border: none; 
                width: 100%; 
                font-weight: bold; 
                font-size: 16px; 
                cursor: pointer;
            }

            .req-input { 
                width: 100%; 
                padding: 16px; 
                margin: 20px 0; 
                border-radius: 12px; 
                border: 2px solid #334155; 
                background: #0f172a; 
                color: white; 
                outline: none; 
                font-size: 16px; 
                font-weight: bold;
            }
            .req-input:focus { 
                border-color: #10b981; 
            }
            
            .btn-submit { 
                background: linear-gradient(45deg, #10b981, #059669); 
                color: white; 
                border: none; 
                padding: 15px 20px; 
                border-radius: 12px; 
                font-weight: bold; 
                width: 100%; 
                font-size: 18px; 
                cursor: pointer; 
                transition: 0.3s;
            }
            .btn-submit:active { 
                transform: scale(0.95); 
            }
            
            /* Bright Notice Box for Copyright Warning */
            .notice-box { 
                background: linear-gradient(135deg, rgba(248,113,113,0.15), rgba(220,38,38,0.25)); 
                border-left: 5px solid #ef4444; 
                padding: 15px; 
                text-align: left; 
                margin: 25px 0; 
                border-radius: 8px; 
            }
            .notice-box p { 
                color: #fecaca; 
                font-size: 16.5px; 
                font-weight: bold; 
                margin: 0; 
                line-height: 1.6; 
                text-shadow: 0 1px 3px rgba(0,0,0,0.5); 
            }

            /* 
             * Ad Screen Styles 
             */
            .ad-screen { 
                position: fixed; 
                top: 0; 
                left: 0; 
                width: 100%; 
                height: 100%; 
                background: rgba(15, 23, 42, 0.98); 
                display: none; 
                flex-direction: column; 
                align-items: center; 
                justify-content: center; 
                z-index: 4000; 
            }
            
            .timer-ui { 
                display: flex; 
                flex-direction: column; 
                align-items: center; 
            }
            
            .rgb-timer-container { 
                position: relative; 
                width: 140px; 
                height: 140px; 
                border-radius: 50%; 
                display: flex; 
                align-items: center; 
                justify-content: center; 
                margin-bottom: 30px; 
                background: #0f172a; 
                box-shadow: 0 0 40px rgba(0,0,0,0.9); 
            }
            
            .rgb-ring { 
                position: absolute; 
                width: 100%; 
                height: 100%; 
                border-radius: 50%; 
                border: 6px solid transparent; 
                background: linear-gradient(#0f172a, #0f172a) padding-box, conic-gradient(#ff0000, #ff7300, #fffb00, #48ff00, #00ffd5, #002bff, #7a00ff, #ff00c8, #ff0000) border-box; 
                animation: spinRing 1.5s linear infinite; 
            }
            
            .timer-text { 
                position: relative; 
                font-size: 55px; 
                font-weight: bold; 
                color: #fff; 
                z-index: 2; 
                text-shadow: 0 0 20px rgba(255,255,255,0.9);
            }
            
            @keyframes spinRing { 
                100% { transform: rotate(360deg); } 
            }

            .ad-step-text { 
                font-size: 20px; 
                font-weight: bold; 
                color: #fff; 
                margin-bottom: 25px; 
                background: #1e293b; 
                padding: 12px 25px; 
                border-radius: 30px; 
                border: 2px solid #fbbf24; 
                text-shadow: 0 0 10px rgba(251,191,36,0.5);
            }
            
            .btn-next-ad { 
                display: none; 
                background: linear-gradient(45deg, #f87171, #ef4444); 
                color: white; 
                border: none; 
                padding: 18px 40px; 
                border-radius: 35px; 
                font-size: 20px; 
                font-weight: bold; 
                cursor: pointer; 
                box-shadow: 0 5px 25px rgba(248,113,113,0.7); 
                transition: 0.3s;
            }
            
            .btn-next-ad:active { 
                transform: scale(0.95); 
            }
        </style>
    </head>
    <body>
        
        <!-- UI Header -->
        <header>
            <div class="logo">MovieZone <span>BD</span></div>
            <div class="user-info">
                <span id="uName">Guest</span>
                <img id="uPic" src="https://cdn-icons-png.flaticon.com/512/3135/3135715.png">
            </div>
        </header>

        <!-- Search Bar -->
        <div class="search-box">
            <input type="text" id="searchInput" class="search-input" placeholder="🔍 মুভি বা ওয়েব সিরিজ খুঁজুন...">
        </div>

        <!-- Trending Section -->
        <div id="trendingWrapper">
            <div class="section-title">
                <i class="fa-solid fa-fire"></i> ট্রেন্ডিং মুভি
            </div>
            <div class="trending-container" id="trendingGrid">
                <!-- Skeleton Loaders -->
                <div class="skeleton" style="min-width:140px; height:240px;"></div>
                <div class="skeleton" style="min-width:140px; height:240px;"></div>
                <div class="skeleton" style="min-width:140px; height:240px;"></div>
            </div>
        </div>

        <!-- Main Movie Grid -->
        <div class="section-title">
            <i class="fa-solid fa-film"></i> নতুন সব মুভি
        </div>
        <div class="grid" id="movieGrid"></div>
        <div class="pagination" id="paginationBox"></div>

        <!-- Floating Action Buttons -->
        <div class="floating-btn btn-18" onclick="window.open('{{LINK_18}}')">18+</div>
        <div class="floating-btn btn-tg" onclick="window.open('{{TG_LINK}}')">
            <i class="fa-brands fa-telegram"></i>
        </div>
        <div class="floating-btn btn-req" onclick="openReqModal()">
            <i class="fa-solid fa-code-pull-request"></i>
        </div>

        <!-- ============================================== -->
        <!-- Modals & Screens                               -->
        <!-- ============================================== -->

        <!-- 1. Quality Selection Modal -->
        <div id="qualityModal" class="modal">
            <div class="modal-content">
                <h2 id="modalTitle" style="color:#38bdf8; margin-bottom: 8px; font-size: 22px; font-weight:900;">
                    Movie Title
                </h2>
                <p class="instruction-text">
                    👇 আপনি কোনটি দেখতে চান তা নির্বাচন করুন:
                </p>
                
                <div id="qualityList">
                    <!-- Dynamic Quality Buttons Injected Here -->
                </div>
                
                <button class="close-btn" onclick="closeQualityModal()">বন্ধ করুন</button>
            </div>
        </div>

        <!-- 2. Ad Playing Screen -->
        <div id="adScreen" class="ad-screen">
            <div class="ad-step-text" id="adStepText">অ্যাড: 1/1</div>
            
            <div class="timer-ui" id="timerUI">
                <div class="rgb-timer-container">
                    <div class="rgb-ring"></div>
                    <div class="timer-text" id="timer">15</div>
                </div>
                <p style="color: #fbbf24; font-size: 18px; font-weight: bold; margin-top:15px; text-shadow: 0 0 10px rgba(251,191,36,0.5);">
                    সার্ভারের সাথে কানেক্ট হচ্ছে...
                </p>
            </div>
            
            <button class="btn-next-ad" id="nextAdBtn" onclick="nextAdStep()">
                পরবর্তী অ্যাড দেখুন <i class="fa-solid fa-arrow-right"></i>
            </button>
        </div>

        <!-- 3. Success Message Modal -->
        <div id="successModal" class="modal">
            <div class="modal-content">
                <i class="fa-solid fa-circle-check" style="font-size:80px; color:#4ade80; text-shadow: 0 0 25px rgba(74,222,128,0.6);"></i>
                <h2 style="margin:20px 0 10px; color:white; font-size: 26px;">
                    সম্পন্ন হয়েছে!
                </h2>
                <p style="color: #4ade80; font-size: 17px; font-weight: bold;">
                    ✅ ফাইলটি বটের ইনবক্সে পাঠানো হয়েছে।
                </p>
                
                <div class="notice-box">
                    <p>
                        <i class="fa-solid fa-triangle-exclamation" style="color: #fbbf24; font-size: 18px;"></i> 
                        <b>সতর্কতা:</b> কপিরাইট এড়াতে মুভিটি কিছুক্ষণ পর অটোমেটিক ডিলিট হয়ে যাবে। দয়া করে এখনই বট থেকে সেভ বা ফরোয়ার্ড করে নিন!
                    </p>
                </div>
                
                <button class="btn-submit" onclick="tg.close()">বটে ফিরে যান</button>
            </div>
        </div>

        <!-- 4. Movie Request Modal -->
        <div id="reqModal" class="modal">
            <div class="modal-content">
                <h2 style="color:white; font-size: 24px;">মুভি রিকোয়েস্ট</h2>
                <p class="instruction-text" style="margin-top: 10px;">
                    👇 যে মুভিটি খুঁজছেন তার সঠিক নাম লিখুন:
                </p>
                
                <input type="text" id="reqText" class="req-input" placeholder="উদাঃ Avatar 2022">
                <button class="btn-submit" onclick="sendReq()">সাবমিট করুন</button>
                
                <p style="margin-top:25px; color:#94a3b8; font-size: 16px; cursor:pointer; font-weight:bold;" onclick="document.getElementById('reqModal').style.display='none'">
                    বাতিল করুন
                </p>
            </div>
        </div>

        <!-- ============================================== -->
        <!-- Logic & Scripts                                -->
        <!-- ============================================== -->
        <script>
            // Init Telegram Web App
            let tg = window.Telegram.WebApp; 
            tg.expand();
            
            // Server Configurations Injected
            const ZONE_ID = "{{ZONE_ID}}";
            const REQUIRED_ADS = parseInt("{{AD_COUNT}}");
            const INIT_DATA = tg.initData || "";
            
            // Application State
            let currentPage = 1; 
            let isLoading = false; 
            let searchQuery = "";
            let uid = tg.initDataUnsafe?.user?.id || 0;
            
            // Ad Flow State
            let currentAdStep = 1; 
            let activeFileId = null;
            let autoScrollInterval; 
            let isTouching = false;
            let abortController = null;
            
            // Data Cache
            let loadedMovies = {}; 

            // Load User Data visually
            if(tg.initDataUnsafe && tg.initDataUnsafe.user) {
                document.getElementById('uName').innerText = tg.initDataUnsafe.user.first_name;
                
                if(tg.initDataUnsafe.user.photo_url) {
                    document.getElementById('uPic').src = tg.initDataUnsafe.user.photo_url;
                }
            }

            // Inject Ad Network Script
            const s = document.createElement('script');
            s.src = '//libtl.com/sdk.js'; 
            s.setAttribute('data-zone', ZONE_ID); 
            s.setAttribute('data-sdk', 'show_' + ZONE_ID);
            document.head.appendChild(s);

            // UI Helper Functions
            function drawSkeletons(count) {
                let html = ""; 
                for(let i=0; i<count; i++) {
                    html += `<div class="skeleton"></div>`; 
                }
                return html;
            }

            function startAutoScroll() {
                if(autoScrollInterval) {
                    clearInterval(autoScrollInterval);
                }
                
                autoScrollInterval = setInterval(() => {
                    if(isTouching) return; 
                    
                    let grid = document.getElementById('trendingGrid');
                    if(grid) {
                        let cardWidth = 155;
                        
                        // Scroll logic
                        if (grid.scrollLeft >= (grid.scrollWidth - grid.clientWidth - 10)) {
                            grid.scrollTo({ left: 0, behavior: 'smooth' });
                        } else {
                            grid.scrollBy({ left: cardWidth, behavior: 'smooth' });
                        }
                    }
                }, 3500);
            }

            // Data Fetching Functions
            async function loadTrending() {
                try {
                    const r = await fetch(`/api/trending?uid=${uid}`);
                    const data = await r.json();
                    
                    // Ban Check
                    if(data.error === "banned") {
                        document.body.innerHTML = `
                            <h2 style='color:#ef4444; text-align:center; font-family:sans-serif; margin-top:80px; font-size:24px;'>
                                🚫 You are permanently Banned!
                            </h2>`;
                        return;
                    }
                    
                    const grid = document.getElementById('trendingGrid');
                    
                    if(data.length === 0) {
                        document.getElementById('trendingWrapper').style.display = 'none';
                        return;
                    }
                    
                    // Render Trending
                    grid.innerHTML = data.map(m => {
                        loadedMovies[m._id] = m;
                        let epCount = m.files.length;
                        
                        return `
                        <div class="trending-card" onclick="openQualityModal('${m._id}')">
                            <div class="post-content">
                                <div class="top-badge">🔥 TOP</div>
                                <img src="/api/image/${m.photo_id}" loading="lazy" onerror="this.src='https://via.placeholder.com/400x240?text=No+Image'">
                                <div class="ep-badge">
                                    <i class="fa-solid fa-list"></i> ${epCount}
                                </div>
                                <div class="view-badge">
                                    <i class="fa-solid fa-eye"></i> ${m.clicks}
                                </div>
                            </div>
                            <div class="card-footer">${m._id}</div>
                        </div>`;
                    }).join('');
                    
                    // Scroll listeners
                    grid.addEventListener('touchstart', () => isTouching = true, {passive: true});
                    grid.addEventListener('touchend', () => { 
                        setTimeout(() => isTouching = false, 1000); 
                    }, {passive: true});
                    
                    grid.addEventListener('mouseenter', () => isTouching = true);
                    grid.addEventListener('mouseleave', () => isTouching = false);
                    
                    setTimeout(startAutoScroll, 2000);
                } catch(e) {
                    console.error("Trending Error: ", e);
                }
            }

            async function loadMovies(page = 1, signal = null) {
                if(isLoading) return;
                isLoading = true;
                currentPage = page;
                
                const grid = document.getElementById('movieGrid');
                const pBox = document.getElementById('paginationBox');
                
                grid.innerHTML = drawSkeletons(16); 
                pBox.innerHTML = "";

                try {
                    const r = await fetch(`/api/list?page=${currentPage}&q=${encodeURIComponent(searchQuery)}&uid=${uid}`, { signal });
                    const data = await r.json();
                    
                    if(data.error === "banned") return;

                    if(data.movies && data.movies.length === 0) {
                        grid.innerHTML = `
                            <p style='grid-column: span 2; text-align:center; color:#fbbf24; font-size: 18px; font-weight:bold; padding:40px;'>
                                🚫 কোনো মুভি পাওয়া যায়নি!
                            </p>`;
                    } else if (data.movies) {
                        grid.innerHTML = data.movies.map(m => {
                            loadedMovies[m._id] = m; 
                            let epCount = m.files.length;
                            
                            return `
                            <div class="card" onclick="openQualityModal('${m._id}')">
                                <div class="post-content">
                                    <img src="/api/image/${m.photo_id}" loading="lazy" onerror="this.src='https://via.placeholder.com/400x240?text=No+Image'">
                                    <div class="ep-badge">
                                        <i class="fa-solid fa-list"></i> ${epCount}
                                    </div>
                                    <div class="view-badge">
                                        <i class="fa-solid fa-eye"></i> ${m.clicks}
                                    </div>
                                </div>
                                <div class="card-footer">${m._id}</div>
                            </div>`;
                        }).join('');
                        
                        renderPagination(data.total_pages);
                    }
                } catch(e) {
                    console.error("Load Movies Error: ", e);
                }
                
                isLoading = false;
            }

            function renderPagination(totalPages) {
                if (totalPages <= 1) return;
                
                let html = "";
                
                html += `<button class="page-btn" ${currentPage === 1 ? 'disabled' : ''} onclick="goToPage(${currentPage - 1})">
                            <i class="fa-solid fa-angle-left"></i>
                         </button>`;
                
                let start = Math.max(1, currentPage - 1); 
                let end = Math.min(totalPages, currentPage + 1);
                
                if (start > 1) { 
                    html += `<button class="page-btn" onclick="goToPage(1)">1</button>`; 
                    if (start > 2) html += `<span style="color:gray;">...</span>`; 
                }
                
                for (let i = start; i <= end; i++) { 
                    html += `<button class="page-btn ${i === currentPage ? 'active' : ''}" onclick="goToPage(${i})">${i}</button>`; 
                }
                
                if (end < totalPages) { 
                    if (end < totalPages - 1) html += `<span style="color:gray;">...</span>`; 
                    html += `<button class="page-btn" onclick="goToPage(${totalPages})">${totalPages}</button>`; 
                }
                
                html += `<button class="page-btn" ${currentPage === totalPages ? 'disabled' : ''} onclick="goToPage(${currentPage + 1})">
                            <i class="fa-solid fa-angle-right"></i>
                         </button>`;
                         
                document.getElementById('paginationBox').innerHTML = html;
            }

            function goToPage(p) {
                if (p < 1) return; 
                
                loadMovies(p);
                window.scrollTo({ 
                    top: document.getElementById('movieGrid').offsetTop - 100, 
                    behavior: 'smooth' 
                });
            }

            // Search Event Listener with Debounce
            let timeout = null;
            document.getElementById('searchInput').addEventListener('input', function(e) {
                clearTimeout(timeout); 
                searchQuery = e.target.value.trim();
                
                if(searchQuery !== "") { 
                    document.getElementById('trendingWrapper').style.display = 'none'; 
                    isTouching = true; 
                } else { 
                    document.getElementById('trendingWrapper').style.display = 'block'; 
                    isTouching = false; 
                    loadTrending(); 
                }
                
                timeout = setTimeout(() => { 
                    if(abortController) abortController.abort();
                    abortController = new AbortController();
                    loadMovies(1, abortController.signal); 
                }, 500); 
            });

            // Modal Interactions
            function openQualityModal(title) {
                const movie = loadedMovies[title];
                if(!movie) return;
                
                document.getElementById('modalTitle').innerText = movie._id;
                
                let listHtml = movie.files.map(f => {
                    let icon = f.is_unlocked 
                        ? '<i class="fa-solid fa-unlock-keyhole text-green-400" style="font-size:20px;"></i>' 
                        : '<i class="fa-solid fa-lock text-red-400" style="font-size:20px;"></i>';
                        
                    let cls = f.is_unlocked ? 'quality-unlocked' : 'quality-locked';
                    
                    return `
                    <button class="quality-btn ${cls}" onclick="handleQualityClick('${f.id}', ${f.is_unlocked})">
                        <span>${f.quality}</span> 
                        ${icon}
                    </button>`;
                }).join('');
                
                document.getElementById('qualityList').innerHTML = listHtml;
                document.getElementById('qualityModal').style.display = 'flex';
            }
            
            function closeQualityModal() { 
                document.getElementById('qualityModal').style.display = 'none'; 
            }

            function handleQualityClick(fileId, isUnlocked) {
                closeQualityModal();
                
                if(isUnlocked) { 
                    sendFile(fileId); 
                } else { 
                    activeFileId = fileId; 
                    currentAdStep = 1; 
                    startAdTimer(); 
                }
            }

            // Ad Serving Logic
            function startAdTimer() {
                // Call Ad Network
                if (typeof window['show_' + ZONE_ID] === 'function') {
                    window['show_' + ZONE_ID]();
                }
                
                // Show Ad UI
                document.getElementById('adScreen').style.display = 'flex';
                document.getElementById('timerUI').style.display = 'flex';
                document.getElementById('nextAdBtn').style.display = 'none';
                
                document.getElementById('adStepText').innerText = `অ্যাড: ${currentAdStep}/${REQUIRED_ADS}`;
                
                let t = 15; 
                document.getElementById('timer').innerText = t;
                
                let iv = setInterval(() => {
                    t--; 
                    document.getElementById('timer').innerText = t;
                    
                    if(t <= 0) { 
                        clearInterval(iv); 
                        
                        if(currentAdStep < REQUIRED_ADS) {
                            document.getElementById('timerUI').style.display = 'none';
                            document.getElementById('nextAdBtn').style.display = 'block';
                            document.getElementById('nextAdBtn').innerHTML = `পরবর্তী অ্যাড দেখুন (${currentAdStep + 1}/${REQUIRED_ADS}) <i class="fa-solid fa-arrow-right"></i>`;
                        } else { 
                            sendFile(activeFileId); 
                        }
                    }
                }, 1000);
            }

            function nextAdStep() { 
                currentAdStep++; 
                startAdTimer(); 
            }

            // File Delivery API Calls
            async function sendFile(id) {
                try {
                    const res = await fetch('/api/send', { 
                        method: 'POST', 
                        headers: {'Content-Type': 'application/json'}, 
                        body: JSON.stringify({
                            userId: uid, 
                            movieId: id, 
                            initData: INIT_DATA
                        })
                    });
                    
                    const responseData = await res.json();
                    
                    if(!responseData.ok) {
                        alert("⚠️ Security verification failed! Please open via Telegram App.");
                        return;
                    }
                    
                    document.getElementById('adScreen').style.display = 'none';
                    document.getElementById('successModal').style.display = 'flex';
                    
                    setTimeout(() => { 
                        loadTrending(); 
                        loadMovies(currentPage); 
                    }, 1000); 
                    
                } catch (e) {
                    console.error(e);
                }
            }

            function openReqModal() { 
                document.getElementById('reqModal').style.display = 'flex'; 
                document.getElementById('reqText').focus(); 
            }
            
            async function sendReq() {
                const text = document.getElementById('reqText').value;
                if(!text) return alert('মুভির নাম লিখুন!');
                
                try {
                    await fetch('/api/request', { 
                        method: 'POST', 
                        headers: {'Content-Type': 'application/json'}, 
                        body: JSON.stringify({
                            uid: uid, 
                            uname: tg.initDataUnsafe.user?.first_name || 'Guest', 
                            movie: text, 
                            initData: INIT_DATA
                        })
                    });
                    
                    document.getElementById('reqModal').style.display = 'none';
                    document.getElementById('reqText').value = '';
                    alert('রিকোয়েস্ট সফলভাবে পাঠানো হয়েছে!');
                    
                } catch (e) {
                    console.error(e);
                }
            }

            // Init App
            loadTrending();
            loadMovies(1); 
        </script>
    </body>
    </html>
    """
    
    html_code = html_code.replace("{{ZONE_ID}}", zone_id).replace("{{TG_LINK}}", tg_url).replace("{{LINK_18}}", link_18).replace("{{AD_COUNT}}", str(required_ads))
    
    return html_code


# ==========================================
# 12. Main Web App APIs (Trending & Lists)
# ==========================================
@app.get("/api/trending")
async def trending_movies(uid: int = 0):
    if uid in banned_cache: 
        return {"error": "banned"}
        
    unlocked_movie_ids = []
    
    if uid != 0:
        time_limit = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
        async for u in db.user_unlocks.find({"user_id": uid, "unlocked_at": {"$gt": time_limit}}):
            unlocked_movie_ids.append(u["movie_id"])

    pipeline = [
        {
            "$group": {
                "_id": "$title",
                "photo_id": {"$first": "$photo_id"},
                "clicks": {"$sum": "$clicks"},
                "files": {
                    "$push": {
                        "id": {"$toString": "$_id"},
                        "quality": {"$ifNull": ["$quality", "Main File"]}
                    }
                }
            }
        },
        {"$sort": {"clicks": -1}},
        {"$limit": 10}
    ]
    
    movies = await db.movies.aggregate(pipeline).to_list(10)
    
    for m in movies:
        for f in m["files"]:
            f["is_unlocked"] = f["id"] in unlocked_movie_ids

    return movies

@app.get("/api/list")
async def list_movies(page: int = 1, q: str = "", uid: int = 0):
    if uid in banned_cache: 
        return {"error": "banned"}
        
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
        {
            "$group": {
                "_id": "$title",
                "photo_id": {"$first": "$photo_id"},
                "clicks": {"$sum": "$clicks"},
                "created_at": {"$max": "$created_at"},
                "files": {
                    "$push": {
                        "id": {"$toString": "$_id"},
                        "quality": {"$ifNull": ["$quality", "Main File"]}
                    }
                }
            }
        },
        {"$sort": {"created_at": -1}},
        {"$skip": skip},
        {"$limit": limit}
    ]
    
    count_pipe = [
        {"$match": match_stage}, 
        {"$group": {"_id": "$title"}}, 
        {"$count": "total"}
    ]
    
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
            
            await db.file_cache.update_one(
                {"photo_id": photo_id},
                {"$set": {
                    "file_path": file_path, 
                    "expires_at": now + datetime.timedelta(minutes=50)
                }},
                upsert=True
            )
            
        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
        
        async def stream_image():
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as resp:
                    async for chunk in resp.content.iter_chunked(1024): 
                        yield chunk
                        
        return StreamingResponse(stream_image(), media_type="image/jpeg")
    except Exception: 
        return {"error": "not found"}


# ==========================================
# 13. File Sender API & Request API
# ==========================================
class SendRequestModel(BaseModel):
    userId: int
    movieId: str
    initData: str

@app.post("/api/send")
async def send_file(d: SendRequestModel):
    if d.userId == 0 or d.userId in banned_cache or not validate_tg_data(d.initData): 
        return {"ok": False, "error": "Security validation failed"}
    
    try:
        m = await db.movies.find_one({"_id": ObjectId(d.movieId)})
        if m:
            time_cfg = await db.settings.find_one({"id": "del_time"})
            del_minutes = time_cfg['minutes'] if time_cfg else 60
            
            protect_cfg = await db.settings.find_one({"id": "protect_content"})
            is_protected = protect_cfg['status'] if protect_cfg else True
            
            q_text = m.get("quality", "")
            title_text = f"{m['title']} [{q_text}]" if q_text else m['title']
            
            caption = (
                f"🎥 <b>{title_text}</b>\n\n"
                f"⏳ <b>সতর্কতা:</b> কপিরাইট এড়াতে মুভিটি <b>{del_minutes} মিনিট</b> পর অটো-ডিলিট হয়ে যাবে। "
                f"দয়া করে এখনই ফরওয়ার্ড বা সেভ করে নিন!\n\n"
                f"📥 Join: @TGLinkBase"
            )
            
            sent_msg = None
            if m.get("file_type") == "video": 
                sent_msg = await bot.send_video(
                    d.userId, 
                    m['file_id'], 
                    caption=caption, 
                    parse_mode="HTML", 
                    protect_content=is_protected
                )
            else: 
                sent_msg = await bot.send_document(
                    d.userId, 
                    m['file_id'], 
                    caption=caption, 
                    parse_mode="HTML", 
                    protect_content=is_protected
                )
            
            await db.movies.update_one(
                {"_id": ObjectId(d.movieId)}, 
                {"$inc": {"clicks": 1}}
            )
            
            await db.user_unlocks.update_one(
                {"user_id": d.userId, "movie_id": d.movieId}, 
                {"$set": {"unlocked_at": datetime.datetime.utcnow()}}, 
                upsert=True
            )
            
            if sent_msg:
                delete_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=del_minutes)
                await db.auto_delete.insert_one({
                    "chat_id": d.userId, 
                    "message_id": sent_msg.message_id, 
                    "delete_at": delete_at
                })
                
    except Exception as e: 
        print(f"Error sending file: {e}")
        
    return {"ok": True}

class ReqModel(BaseModel):
    uid: int
    uname: str
    movie: str
    initData: str

@app.post("/api/request")
async def handle_request(data: ReqModel):
    if data.uid in banned_cache or not validate_tg_data(data.initData):
        return {"ok": False}
        
    try: 
        builder = InlineKeyboardBuilder()
        builder.button(text="✍️ রিপ্লাই দিন", callback_data=f"reply_{data.uid}")
        
        await bot.send_message(
            OWNER_ID, 
            f"🔔 <b>নতুন মুভি রিকোয়েস্ট!</b>\n\n"
            f"👤 ইউজার: {data.uname} (<code>{data.uid}</code>)\n"
            f"🎬 মুভির নাম: <b>{data.movie}</b>", 
            parse_mode="HTML", 
            reply_markup=builder.as_markup()
        )
    except Exception: 
        pass
        
    return {"ok": True}


# ==========================================
# 14. Main Application Startup
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
    
    print("Connecting to Telegram Bot API...")
    await bot.delete_webhook(drop_pending_updates=True)
    
    print("Server is Running!")
    await asyncio.gather(server.serve(), dp.start_polling(bot))

if __name__ == "__main__": 
    asyncio.run(start())
