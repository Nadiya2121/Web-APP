import os, asyncio, datetime, uvicorn
import aiohttp
from fastapi import FastAPI, Body, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from motor.motor_asyncio import AsyncIOMotorClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bson import ObjectId
from pydantic import BaseModel

# --- কনফিগারেশন ---
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URI")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
APP_URL = os.getenv("APP_URL")

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

client = AsyncIOMotorClient(MONGO_URL)
db = client['movie_database']

admin_temp = {}

# --- ব্যাকগ্রাউন্ড অটো-ডিলিট ওয়ার্কার ---
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
        except Exception as e:
            print("Auto-Delete Worker Error:", e)
        await asyncio.sleep(60)

# ==========================================
# ১. বটের সব কমান্ড (Commands)
# ==========================================

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await db.users.update_one({"user_id": message.from_user.id}, {"$set": {"first_name": message.from_user.first_name}}, upsert=True)
    kb = [[types.InlineKeyboardButton(text="🎬 ওপেন মুভি অ্যাপ", web_app=types.WebAppInfo(url=APP_URL))]]
    markup = types.InlineKeyboardMarkup(inline_keyboard=kb)
    
    if message.from_user.id == ADMIN_ID:
        text = (
            "👋 <b>হ্যালো অ্যাডমিন!</b>\n\n"
            "⚙️ <b>কমান্ড:</b>\n"
            "🔸 জোন: <code>/setad</code> | টেলিগ্রাম: <code>/settg</code> | 18+: <code>/set18</code>\n"
            "🔸 অটো-ডিলিট টাইম: <code>/settime [মিনিট]</code>\n"
            "🔸 ডিলিট: <code>/del</code> | স্ট্যাটাস: <code>/stats</code> | ব্রডকাস্ট: <code>/cast</code>\n\n"
            "📥 <b>মুভি অ্যাড করতে প্রথমে ভিডিও বা ডকুমেন্ট ফাইল পাঠান।</b>"
        )
    else:
        text = f"👋 <b>স্বাগতম {message.from_user.first_name}!</b>\n\n[আপনার টেলিগ্রাম আইডি: <code>{message.from_user.id}</code>]\n\nমুভি দেখতে নিচের বাটনে ক্লিক করুন।"
    await message.answer(text, reply_markup=markup, parse_mode="HTML")

@dp.message(Command("settime"))
async def set_del_time(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    try:
        minutes = int(m.text.split(" ")[1])
        await db.settings.update_one({"id": "del_time"}, {"$set": {"minutes": minutes}}, upsert=True)
        await m.answer(f"✅ অটো-ডিলিট টাইম সেট করা হয়েছে: <b>{minutes}</b> মিনিট।", parse_mode="HTML")
    except:
        await m.answer("⚠️ ভুল ফরম্যাট! নিয়ম: <code>/settime 60</code>", parse_mode="HTML")

@dp.message(Command("stats"))
async def stats_cmd(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    uc = await db.users.count_documents({})
    mc = await db.movies.count_documents({})
    time_cfg = await db.settings.find_one({"id": "del_time"})
    del_m = time_cfg['minutes'] if time_cfg else 60
    await m.answer(f"📊 <b>স্ট্যাটাস:</b>\n👥 মোট ইউজার: <code>{uc}</code>\n🎬 মোট মুভি: <code>{mc}</code>\n⏳ অটো-ডিলিট: <code>{del_m} মিনিট</code>", parse_mode="HTML")

@dp.message(Command("cast"))
async def broadcast_cmd(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    text = m.text.replace("/cast", "").strip()
    if not text: return await m.answer("⚠️ নিয়ম: <code>/cast মেসেজ</code>", parse_mode="HTML")
    await m.answer("⏳ ব্রডকাস্ট শুরু হয়েছে...")
    success = 0
    async for u in db.users.find():
        try:
            await bot.send_message(u['user_id'], text)
            success += 1
            await asyncio.sleep(0.05)
        except: pass
    await m.answer(f"✅ সম্পন্ন! মেসেজ পাঠানো হয়েছে: {success} জনকে।")

@dp.message(Command("del"))
async def del_movie_list(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    movies = await db.movies.find().sort("created_at", -1).limit(20).to_list(length=20)
    if not movies: return await m.answer("কোনো মুভি নেই।")
    builder = InlineKeyboardBuilder()
    for mv in movies: builder.button(text=f"❌ {mv['title']}", callback_data=f"del_{str(mv['_id'])}")
    builder.adjust(1)
    await m.answer("⚠️ ডিলিট করতে ক্লিক করুন:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("del_"))
async def del_movie_callback(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    try:
        await db.movies.delete_one({"_id": ObjectId(c.data.split("_")[1])})
        await c.answer("✅ ডিলিট হয়েছে!", show_alert=True)
        await c.message.edit_text("✅ মুভিটি ডাটাবেস থেকে মুছে ফেলা হয়েছে।", reply_markup=None)
    except: pass

@dp.message(Command("setad"))
async def set_ad(m: types.Message):
    if m.from_user.id == ADMIN_ID:
        try:
            val = m.text.split(" ")[1]
            await db.settings.update_one({"id": "ad_config"}, {"$set": {"zone_id": val}}, upsert=True)
            await m.answer(f"✅ জোন আপডেট হয়েছে: {val}")
        except:
            await m.answer("⚠️ সঠিক নিয়ম: <code>/setad 1234567</code>", parse_mode="HTML")

@dp.message(Command("settg"))
async def set_tg(m: types.Message):
    if m.from_user.id == ADMIN_ID:
        try:
            val = m.text.split(" ")[1]
            await db.settings.update_one({"id": "link_tg"}, {"$set": {"url": val}}, upsert=True)
            await m.answer("✅ টেলিগ্রাম লিংক আপডেট হয়েছে।")
        except:
            await m.answer("⚠️ সঠিক নিয়ম: <code>/settg https://t.me/...</code>", parse_mode="HTML")

@dp.message(Command("set18"))
async def set_18(m: types.Message):
    if m.from_user.id == ADMIN_ID:
        try:
            val = m.text.split(" ")[1]
            await db.settings.update_one({"id": "link_18"}, {"$set": {"url": val}}, upsert=True)
            await m.answer("✅ 18+ লিংক আপডেট হয়েছে।")
        except:
            await m.answer("⚠️ সঠিক নিয়ম: <code>/set18 https://t.me/...</code>", parse_mode="HTML")

# ==========================================
# ২. ফাইল এবং টেক্সট রিসিভ করা (Upload Flow)
# ==========================================

@dp.message(F.document | F.video)
async def catch_file(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    fid = m.video.file_id if m.video else m.document.file_id
    ftype = "video" if m.video else "document"
    admin_temp[m.from_user.id] = {"step": "photo", "file_id": fid, "type": ftype}
    await m.answer("✅ ফাইল পেয়েছি! এবার মুভির <b>পোস্টার (Photo)</b> সেন্ড করুন।", parse_mode="HTML")

@dp.message(F.photo)
async def catch_photo(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    uid = m.from_user.id
    if uid in admin_temp and admin_temp[uid].get("step") == "photo":
        admin_temp[uid]["photo_id"] = m.photo[-1].file_id
        admin_temp[uid]["step"] = "title"
        await m.answer("✅ পোস্টার পেয়েছি! এবার মুভির <b>নাম</b> লিখে পাঠান।", parse_mode="HTML")

@dp.message(F.text)
async def catch_text(m: types.Message):
    uid = m.from_user.id
    if uid != ADMIN_ID or str(m.text).startswith("/"): return
    if uid in admin_temp and admin_temp[uid].get("step") == "title":
        title = m.text.strip()
        await db.movies.insert_one({"title": title, "photo_id": admin_temp[uid]["photo_id"], "file_id": admin_temp[uid]["file_id"], "file_type": admin_temp[uid]["type"], "created_at": datetime.datetime.utcnow()})
        del admin_temp[uid]
        await m.answer(f"🎉 <b>{title}</b> অ্যাপে সফলভাবে যুক্ত করা হয়েছে!", parse_mode="HTML")

# ==========================================
# ৩. ওয়েব অ্যাপ UI এবং APIs
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def web_ui():
    ad_cfg = await db.settings.find_one({"id": "ad_config"})
    tg_cfg = await db.settings.find_one({"id": "link_tg"})
    b18_cfg = await db.settings.find_one({"id": "link_18"})
    
    zone_id = ad_cfg['zone_id'] if ad_cfg else "10916755"
    tg_url = tg_cfg['url'] if tg_cfg else "https://t.me/MovieeBD"
    link_18 = b18_cfg['url'] if b18_cfg else "https://t.me/MovieeBD"

    html_code = r"""
    <!DOCTYPE html>
    <html lang="bn">
    <head>
        <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Moviee BD</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
        <style>
            * { margin:0; padding:0; box-sizing:border-box; }
            body { background:#0f172a; font-family: sans-serif; color:#fff; } 
            header { display:flex; justify-content:space-between; align-items:center; padding:15px; border-bottom:1px solid #1e293b; position:sticky; top:0; background:#0f172a; z-index:1000; }
            .logo { font-size:24px; font-weight:bold; }
            .logo span { background:red; color:#fff; padding:2px 5px; border-radius:5px; margin-left:5px; font-size:16px; }
            .user-info { display:flex; align-items:center; gap:8px; background:#1e293b; padding:5px 12px; border-radius:20px; font-weight:bold; font-size:14px; }
            .user-info img { width:26px; height:26px; border-radius:50%; object-fit:cover; }
            .search-box { padding:15px; }
            .search-input { width:100%; padding:14px; border-radius:25px; border:none; outline:none; text-align:center; background:#1e293b; color:#fff; font-size:16px; transition: 0.3s; }
            .search-input:focus { box-shadow: 0 0 10px rgba(248,113,113,0.5); }
            
            .grid { padding:0 15px 100px; display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; }
            .card { background:#1e293b; border-radius:12px; overflow:hidden; cursor:pointer; transition: transform 0.2s; }
            .card:active { transform: scale(0.95); }
            
            .post-content { 
                position:relative; padding: 3px; border-radius: 12px;
                background: linear-gradient(45deg, #ff0000, #ff7300, #fffb00, #48ff00, #00ffd5, #002bff, #7a00ff, #ff00c8, #ff0000);
                background-size: 400%; animation: glowing 8s linear infinite;
            }
            @keyframes glowing { 0% { background-position: 0 0; } 50% { background-position: 400% 0; } 100% { background-position: 0 0; } }

            .post-content img { width:100%; height:180px; object-fit:cover; display:block; border-radius: 10px; }
            
            .tag { position:absolute; top:50%; left:50%; transform:translate(-50%, -50%); padding:6px 12px; border-radius:20px; font-weight:bold; font-size:12px; display:flex; align-items:center; gap:5px; box-shadow: 0 2px 10px rgba(0,0,0,0.5); }
            .tag-locked { background:rgba(0,0,0,0.8); color:#f87171; border: 1px solid #f87171; }
            .tag-unlocked { background:rgba(0,0,0,0.8); color:#10b981; border: 1px solid #10b981; }
            
            .card-footer { padding:10px 5px; font-size:13px; font-weight:bold; text-align:center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color:#e2e8f0; }
            
            .skeleton { background: #1e293b; border-radius: 12px; height: 215px; overflow: hidden; position: relative; }
            .skeleton::after { content: ""; position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: linear-gradient(90deg, transparent, rgba(255,255,255,0.05), transparent); animation: shimmer 1.5s infinite; }
            @keyframes shimmer { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }

            .floating-btn { position:fixed; right:20px; color:white; width:50px; height:50px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:20px; font-weight:bold; z-index:500; cursor:pointer; box-shadow: 0 4px 10px rgba(0,0,0,0.5); }
            .btn-18 { bottom:155px; background:red; border:2px solid #fff; }
            .btn-tg { bottom:95px; background:#24A1DE; }
            .btn-req { bottom:35px; background:#10b981; }

            .ad-screen { position:fixed; top:0; left:0; width:100%; height:100%; background:#0f172a; display:none; flex-direction:column; align-items:center; justify-content:center; z-index:2000; }
            .timer { width:100px; height:100px; border-radius:50%; border:5px solid red; display:flex; align-items:center; justify-content:center; font-size:40px; margin-bottom:20px; color:red; font-weight:bold; }
            
            .modal { position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.8); display:none; align-items:center; justify-content:center; z-index:3000; }
            .modal-content { background:#1e293b; width:90%; padding:30px; border-radius:15px; text-align:center; }
            .req-input { width: 100%; padding: 12px; margin: 15px 0; border-radius: 8px; border: none; background: #0f172a; color: white; outline:none; }
            .btn-submit { background: #10b981; color: white; border: none; padding: 10px 20px; border-radius: 8px; font-weight: bold; width:100%; font-size:16px;}
        </style>
    </head>
    <body>
        <header>
            <div class="logo">MovieZone <span>BD</span></div>
            <div class="user-info"><span id="uName">Guest</span><img id="uPic" src="https://cdn-icons-png.flaticon.com/512/3135/3135715.png"></div>
        </header>

        <div class="search-box">
            <input type="text" id="searchInput" class="search-input" placeholder="মুভি বা ওয়েব সিরিজ খুঁজুন...">
        </div>

        <div class="grid" id="movieGrid"></div>

        <div class="floating-btn btn-18" onclick="window.open('{{LINK_18}}')">18+</div>
        <div class="floating-btn btn-tg" onclick="window.open('{{TG_LINK}}')"><i class="fa-brands fa-telegram"></i></div>
        <div class="floating-btn btn-req" onclick="openReqModal()"><i class="fa-solid fa-code-pull-request"></i></div>

        <div id="adScreen" class="ad-screen">
            <div class="timer" id="timer">15</div>
            <p>সার্ভারের সাথে কানেক্ট হচ্ছে...</p>
        </div>

        <div id="successModal" class="modal">
            <div class="modal-content">
                <i class="fa-solid fa-circle-check" style="font-size:60px; color:#10b981;"></i>
                <h2 style="margin:15px 0;">সম্পন্ন হয়েছে!</h2>
                <p style="margin-bottom: 20px; color:gray; font-size:14px;">বটের ইনবক্স চেক করুন। <br><span style="color:#f87171;">সতর্কতা: কপিরাইট এড়াতে মুভিটি কিছুক্ষণ পর অটোমেটিক ডিলিট হয়ে যাবে।</span></p>
                <button class="btn-submit" onclick="tg.close()">বটে ফিরে যান</button>
            </div>
        </div>

        <div id="reqModal" class="modal">
            <div class="modal-content">
                <h2>মুভি রিকোয়েস্ট করুন</h2>
                <input type="text" id="reqText" class="req-input" placeholder="মুভির নাম ও রিলিজ সাল লিখুন...">
                <button class="btn-submit" onclick="sendReq()">সাবমিট করুন</button>
                <p style="margin-top:15px; color:gray; cursor:pointer;" onclick="document.getElementById('reqModal').style.display='none'">বাতিল করুন</p>
            </div>
        </div>

        <script>
            let tg = window.Telegram.WebApp; tg.expand();
            const ZONE_ID = "{{ZONE_ID}}";
            
            let page = 1; let isLoading = false; let hasMore = true; let searchQuery = "";
            let uid = tg.initDataUnsafe.user?.id || 0;

            if(tg.initDataUnsafe && tg.initDataUnsafe.user) {
                document.getElementById('uName').innerText = tg.initDataUnsafe.user.first_name;
                if(tg.initDataUnsafe.user.photo_url) document.getElementById('uPic').src = tg.initDataUnsafe.user.photo_url;
            }

            const s = document.createElement('script');
            s.src = '//libtl.com/sdk.js'; s.setAttribute('data-zone', ZONE_ID); s.setAttribute('data-sdk', 'show_' + ZONE_ID);
            document.head.appendChild(s);

            function drawSkeletons(count) {
                let html = ""; for(let i=0; i<count; i++) html += `<div class="skeleton"></div>`; return html;
            }

            async function loadMovies(reset = false) {
                if(isLoading || (!hasMore && !reset)) return;
                isLoading = true;
                const grid = document.getElementById('movieGrid');
                if(reset) { page = 1; hasMore = true; grid.innerHTML = drawSkeletons(6); }
                else { grid.innerHTML += drawSkeletons(4); }

                try {
                    const r = await fetch(`/api/list?page=${page}&q=${searchQuery}&uid=${uid}`);
                    const data = await r.json();
                    grid.querySelectorAll('.skeleton').forEach(el => el.remove());

                    if(data.length === 0) {
                        hasMore = false;
                        if(page === 1) grid.innerHTML = "<p style='grid-column: span 2; text-align:center; color:gray; padding:20px;'>কোনো মুভি পাওয়া যায়নি!</p>";
                    } else {
                        const html = data.map(m => {
                            let tagHtml = m.is_unlocked 
                                ? `<div class="tag tag-unlocked"><i class="fa-solid fa-unlock"></i> 24h Unlocked</div>` 
                                : `<div class="tag tag-locked"><i class="fa-solid fa-lock"></i> Locked</div>`;
                                
                            return `
                            <div class="card" onclick="handleMovieClick('${m._id}', ${m.is_unlocked})">
                                <div class="post-content">
                                    <img src="/api/image/${m.photo_id}" onerror="this.src='https://via.placeholder.com/400x200?text=No+Image'">
                                    ${tagHtml}
                                </div>
                                <div class="card-footer">${m.title}</div>
                            </div>`;
                        }).join('');
                        if(reset) grid.innerHTML = html; else grid.innerHTML += html;
                        page++;
                    }
                } catch(e) {}
                isLoading = false;
            }

            let timeout = null;
            document.getElementById('searchInput').addEventListener('input', function(e) {
                clearTimeout(timeout); searchQuery = e.target.value.trim();
                timeout = setTimeout(() => { loadMovies(true); }, 500);
            });

            window.addEventListener('scroll', () => { if(window.innerHeight + window.scrollY >= document.body.offsetHeight - 200) loadMovies(); });

            function handleMovieClick(id, isUnlocked) {
                if(isUnlocked) {
                    sendFile(id);
                } else {
                    if (typeof window['show_' + ZONE_ID] === 'function') window['show_' + ZONE_ID]();
                    document.getElementById('adScreen').style.display = 'flex';
                    let t = 15;
                    let iv = setInterval(() => {
                        t--; document.getElementById('timer').innerText = t;
                        if(t <= 0) { 
                            clearInterval(iv); 
                            sendFile(id); 
                        }
                    }, 1000);
                }
            }

            async function sendFile(id) {
                await fetch('/api/send', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({userId: uid, movieId: id})});
                document.getElementById('adScreen').style.display = 'none';
                document.getElementById('successModal').style.display = 'flex';
                setTimeout(() => { loadMovies(true); }, 1000); 
            }

            function openReqModal() { document.getElementById('reqModal').style.display = 'flex'; }
            
            async function sendReq() {
                const text = document.getElementById('reqText').value;
                if(!text) return alert('মুভির নাম লিখুন!');
                await fetch('/api/request', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({uid: uid, uname: tg.initDataUnsafe.user?.first_name || 'Guest', movie: text})});
                document.getElementById('reqModal').style.display = 'none';
                document.getElementById('reqText').value = '';
                alert('রিকোয়েস্ট সফলভাবে পাঠানো হয়েছে!');
            }

            loadMovies(true); 
        </script>
    </body>
    </html>
    """
    html_code = html_code.replace("{{ZONE_ID}}", zone_id).replace("{{TG_LINK}}", tg_url).replace("{{LINK_18}}", link_18)
    return html_code

@app.get("/api/list")
async def list_movies(page: int = 1, q: str = "", uid: int = 0):
    limit = 12
    skip = (page - 1) * limit
    query = {"title": {"$regex": q, "$options": "i"}} if q else {}
    
    unlocked_movie_ids = []
    if uid != 0:
        time_limit = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
        async for u in db.user_unlocks.find({"user_id": uid, "unlocked_at": {"$gt": time_limit}}):
            unlocked_movie_ids.append(u["movie_id"])

    movies = []
    async for m in db.movies.find(query).sort("created_at", -1).skip(skip).limit(limit):
        m_id = str(m["_id"])
        m["_id"] = m_id
        m["created_at"] = str(m.get("created_at", ""))
        m["is_unlocked"] = m_id in unlocked_movie_ids 
        movies.append(m)
    return movies

@app.get("/api/image/{photo_id}")
async def get_image(photo_id: str):
    try:
        file_info = await bot.get_file(photo_id)
        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        async def stream_image():
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as resp:
                    async for chunk in resp.content.iter_chunked(1024): yield chunk
        return StreamingResponse(stream_image(), media_type="image/jpeg")
    except: return {"error": "not found"}

@app.post("/api/send")
async def send_file(d: dict = Body(...)):
    uid = d['userId']
    mid = d['movieId']
    if uid == 0: return {"ok": False}
    try:
        m = await db.movies.find_one({"_id": ObjectId(mid)})
        if m:
            time_cfg = await db.settings.find_one({"id": "del_time"})
            del_minutes = time_cfg['minutes'] if time_cfg else 60
            
            caption = f"🎥 <b>{m['title']}</b>\n\n⏳ <b>সতর্কতা:</b> কপিরাইট এড়াতে মুভিটি <b>{del_minutes} মিনিট</b> পর অটো-ডিলিট হয়ে যাবে। দয়া করে এখনই ফরওয়ার্ড বা সেভ করে নিন!\n\n📥 Join: @TGLinkBase"
            
            sent_msg = None
            if m.get("file_type") == "video": sent_msg = await bot.send_video(uid, m['file_id'], caption=caption, parse_mode="HTML")
            else: sent_msg = await bot.send_document(uid, m['file_id'], caption=caption, parse_mode="HTML")
            
            await db.user_unlocks.update_one({"user_id": uid, "movie_id": mid}, {"$set": {"unlocked_at": datetime.datetime.utcnow()}}, upsert=True)
            
            if sent_msg:
                delete_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=del_minutes)
                await db.auto_delete.insert_one({"chat_id": uid, "message_id": sent_msg.message_id, "delete_at": delete_at})
    except Exception as e: 
        print("Send File Error:", e)
    return {"ok": True}

class ReqModel(BaseModel):
    uid: int; uname: str; movie: str

@app.post("/api/request")
async def handle_request(data: ReqModel):
    try: await bot.send_message(ADMIN_ID, f"🔔 <b>মুভি রিকোয়েস্ট!</b>\n\n👤 ইউজার: {data.uname} (<code>{data.uid}</code>)\n🎬 নাম: <b>{data.movie}</b>", parse_mode="HTML")
    except: pass
    return {"ok": True}

async def start():
    port = int(os.getenv("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="asyncio")
    server = uvicorn.Server(config)
    
    asyncio.create_task(auto_delete_worker())
    
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.gather(server.serve(), dp.start_polling(bot))

if __name__ == "__main__": asyncio.run(start())
