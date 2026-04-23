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

# --- ১. বটের কাজ (অ্যাডমিন কমান্ড) ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    # ইউজার সেভ করা (Stats & Broadcast এর জন্য)
    await db.users.update_one(
        {"user_id": message.from_user.id}, 
        {"$set": {"first_name": message.from_user.first_name}}, 
        upsert=True
    )
    
    kb = [[types.InlineKeyboardButton(text="🎬 ওপেন মুভি অ্যাপ", web_app=types.WebAppInfo(url=APP_URL))]]
    markup = types.InlineKeyboardMarkup(inline_keyboard=kb)
    
    if message.from_user.id == ADMIN_ID:
        text = (
            "👋 **হ্যালো অ্যাডমিন!**\n\n"
            "⚙️ **কমান্ড প্যানেল:**\n"
            "🔸 অ্যাড আইডি সেট: `/setad [ID]`\n"
            "🔸 বাটন লিংক: `/settg [URL]` এবং `/set18 [URL]`\n"
            "🔸 মুভি ডিলিট: `/del`\n"
            "📊 স্ট্যাটাস চেক: `/stats`\n"
            "📣 ব্রডকাস্ট: `/cast [আপনার মেসেজ]`\n\n"
            "📥 **নতুন মুভি অ্যাড করতে প্রথমে ভিডিও বা ডকুমেন্ট ফাইল পাঠান।**"
        )
    else:
        text = f"👋 **স্বাগতম {message.from_user.first_name}!**\nমুভি দেখতে নিচের বাটনে ক্লিক করুন।"
    await message.answer(text, reply_markup=markup, parse_mode="Markdown")

@dp.message(Command("stats"))
async def stats_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    users_count = await db.users.count_documents({})
    movies_count = await db.movies.count_documents({})
    await message.answer(f"📊 **অ্যাডমিন স্ট্যাটাস:**\n👥 মোট ইউজার: `{users_count}` জন\n🎬 মোট মুভি: `{movies_count}` টি")

@dp.message(Command("cast"))
async def broadcast_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.replace("/cast", "").strip()
    if not text: return await message.answer("⚠️ নিয়ম: `/cast আপনার মেসেজ`")
    
    await message.answer("⏳ ব্রডকাস্ট শুরু হয়েছে...")
    users = await db.users.find().to_list(length=None)
    success = 0
    for u in users:
        try:
            await bot.send_message(u['user_id'], text)
            success += 1
            await asyncio.sleep(0.05) # Telegram limit safe
        except: pass
    await message.answer(f"✅ ব্রডকাস্ট সম্পন্ন! \nমেসেজ পাঠানো হয়েছে: {success} জনকে।")

# --- নতুন আপলোড লজিক (File -> Photo -> Text) ---
@dp.message(F.document | F.video)
async def catch_file(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    fid = message.video.file_id if message.video else message.document.file_id
    ftype = "video" if message.video else "document"
        
    admin_temp[message.from_user.id] = {"step": "photo", "file_id": fid, "type": ftype}
    await message.answer("✅ ফাইল পেয়েছি! এবার মুভির **পোস্টার (Photo)** সেন্ড করুন।")

@dp.message(F.photo)
async def catch_photo(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    uid = message.from_user.id
    if uid in admin_temp and admin_temp[uid].get("step") == "photo":
        admin_temp[uid]["photo_id"] = message.photo[-1].file_id # Best resolution
        admin_temp[uid]["step"] = "title"
        await message.answer("✅ পোস্টার পেয়েছি! এবার মুভির **নাম** লিখে পাঠান।")

@dp.message(F.text)
async def catch_text(message: types.Message):
    uid = message.from_user.id
    if uid != ADMIN_ID or str(message.text).startswith("/"): return
    
    if uid in admin_temp and admin_temp[uid].get("step") == "title":
        title = message.text.strip()
        await db.movies.insert_one({
            "title": title, 
            "photo_id": admin_temp[uid]["photo_id"],
            "file_id": admin_temp[uid]["file_id"], 
            "file_type": admin_temp[uid]["type"],
            "created_at": datetime.datetime.utcnow()
        })
        del admin_temp[uid]
        await message.answer(f"🎉 **{title}** অ্যাপে সফলভাবে যুক্ত করা হয়েছে!")

# --- মুভি ডিলিট ও লিংক কমান্ড (আগের মতোই) ---
@dp.message(Command("del"))
async def del_movie_list(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    movies = await db.movies.find().sort("created_at", -1).limit(20).to_list(length=20)
    if not movies: return await message.answer("কোনো মুভি নেই।")
    builder = InlineKeyboardBuilder()
    for m in movies: builder.button(text=f"❌ {m['title']}", callback_data=f"del_{str(m['_id'])}")
    builder.adjust(1)
    await message.answer("⚠️ ডিলিট করতে ক্লিক করুন:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("del_"))
async def del_movie_callback(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    await db.movies.delete_one({"_id": ObjectId(c.data.split("_")[1])})
    await c.answer("✅ ডিলিট হয়েছে!", show_alert=True)
    await c.message.delete()

@dp.message(Command("setad"))
async def set_ad(m: types.Message):
    if m.from_user.id == ADMIN_ID:
        await db.settings.update_one({"id": "ad_config"}, {"$set": {"zone_id": m.text.split(" ")[1]}}, upsert=True)
        await m.answer("✅ জোন আপডেট হয়েছে।")

@dp.message(Command("settg"))
async def set_tg(m: types.Message):
    if m.from_user.id == ADMIN_ID:
        await db.settings.update_one({"id": "link_tg"}, {"$set": {"url": m.text.split(" ")[1]}}, upsert=True)
        await m.answer("✅ টেলিগ্রাম লিংক আপডেট হয়েছে।")

@dp.message(Command("set18"))
async def set_18(m: types.Message):
    if m.from_user.id == ADMIN_ID:
        await db.settings.update_one({"id": "link_18"}, {"$set": {"url": m.text.split(" ")[1]}}, upsert=True)
        await m.answer("✅ 18+ লিংক আপডেট হয়েছে।")

# --- ২. ওয়েব অ্যাপ UI (Skeletons, Scroll, Request Button) ---

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
            body { background:#0f172a; font-family: sans-serif; color:#fff; } /* Dark Mode Default */
            header { display:flex; justify-content:space-between; align-items:center; padding:15px; border-bottom:1px solid #1e293b; position:sticky; top:0; background:#0f172a; z-index:1000; }
            .logo { font-size:24px; font-weight:bold; }
            .logo span { background:red; color:#fff; padding:2px 5px; border-radius:5px; margin-left:5px; font-size:16px; }
            .user-info { display:flex; align-items:center; gap:8px; background:#1e293b; padding:5px 12px; border-radius:20px; font-weight:bold; font-size:14px; }
            .user-info img { width:26px; height:26px; border-radius:50%; object-fit:cover; }
            .search-box { padding:15px; }
            .search-input { width:100%; padding:14px; border-radius:25px; border:none; outline:none; text-align:center; background:#1e293b; color:#fff; font-size:16px; transition: 0.3s; }
            .search-input:focus { box-shadow: 0 0 10px rgba(248,113,113,0.5); }
            
            .grid { padding:0 15px 100px; display: grid; gap: 20px; }
            .card { background:#1e293b; border-radius:15px; overflow:hidden; cursor:pointer; transition: transform 0.2s; }
            .card:active { transform: scale(0.98); }
            .post-content { position:relative; }
            .post-content img { width:100%; height:220px; object-fit:cover; display:block; }
            .lock-overlay { position:absolute; top:50%; left:50%; transform:translate(-50%, -50%); background:rgba(0,0,0,0.7); padding:8px 15px; border-radius:20px; color:red; font-weight:bold; font-size:12px; }
            .card-footer { padding:12px; font-size:15px; font-weight:bold; }
            
            /* Skeleton Loading CSS */
            .skeleton { background: #1e293b; border-radius: 15px; height: 270px; overflow: hidden; position: relative; }
            .skeleton::after {
                content: ""; position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                background: linear-gradient(90deg, transparent, rgba(255,255,255,0.05), transparent);
                animation: shimmer 1.5s infinite;
            }
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

        <!-- Ad Screen -->
        <div id="adScreen" class="ad-screen">
            <div class="timer" id="timer">15</div>
            <p>সার্ভারের সাথে কানেক্ট হচ্ছে...</p>
        </div>

        <!-- Success Modal -->
        <div id="successModal" class="modal">
            <div class="modal-content">
                <i class="fa-solid fa-circle-check" style="font-size:60px; color:#10b981;"></i>
                <h2 style="margin:15px 0;">সম্পন্ন হয়েছে!</h2>
                <p style="margin-bottom: 20px; color:gray;">বটের ইনবক্স চেক করুন, মুভি পাঠানো হয়েছে।</p>
                <button class="btn-submit" onclick="tg.close()">বটে ফিরে যান</button>
            </div>
        </div>

        <!-- Request Modal -->
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
            
            let page = 1;
            let isLoading = false;
            let hasMore = true;
            let searchQuery = "";

            if(tg.initDataUnsafe && tg.initDataUnsafe.user) {
                document.getElementById('uName').innerText = tg.initDataUnsafe.user.first_name;
                if(tg.initDataUnsafe.user.photo_url) document.getElementById('uPic').src = tg.initDataUnsafe.user.photo_url;
            }

            const s = document.createElement('script');
            s.src = '//libtl.com/sdk.js'; s.setAttribute('data-zone', ZONE_ID); s.setAttribute('data-sdk', 'show_' + ZONE_ID);
            document.head.appendChild(s);

            function drawSkeletons(count) {
                let html = "";
                for(let i=0; i<count; i++) html += `<div class="skeleton"></div>`;
                return html;
            }

            async function loadMovies(reset = false) {
                if(isLoading || (!hasMore && !reset)) return;
                isLoading = true;
                const grid = document.getElementById('movieGrid');
                
                if(reset) { page = 1; hasMore = true; grid.innerHTML = drawSkeletons(4); }
                else { grid.innerHTML += drawSkeletons(2); } // Append skeletons at bottom

                try {
                    const r = await fetch(`/api/list?page=${page}&q=${searchQuery}`);
                    const data = await r.json();
                    
                    // Remove skeletons
                    grid.querySelectorAll('.skeleton').forEach(el => el.remove());

                    if(data.length === 0) {
                        hasMore = false;
                        if(page === 1) grid.innerHTML = "<p style='text-align:center;color:gray;padding:20px;'>কোনো মুভি পাওয়া যায়নি!</p>";
                    } else {
                        const html = data.map(m => `
                            <div class="card" onclick="startAd('${m._id}')">
                                <div class="post-content">
                                    <img src="/api/image/${m.photo_id}" onerror="this.src='https://via.placeholder.com/400x200?text=Image+Error'">
                                    <div class="lock-overlay"><i class="fa-solid fa-lock"></i> Locked</div>
                                </div>
                                <div class="card-footer">${m.title}</div>
                            </div>
                        `).join('');
                        
                        if(reset) grid.innerHTML = html; else grid.innerHTML += html;
                        page++;
                    }
                } catch(e) { console.log(e); }
                isLoading = false;
            }

            // Live Search with Debounce
            let timeout = null;
            document.getElementById('searchInput').addEventListener('input', function(e) {
                clearTimeout(timeout);
                searchQuery = e.target.value.trim();
                timeout = setTimeout(() => { loadMovies(true); }, 500); // 500ms delay
            });

            // Infinite Scroll
            window.addEventListener('scroll', () => {
                if(window.innerHeight + window.scrollY >= document.body.offsetHeight - 200) loadMovies();
            });

            function startAd(id) {
                if (typeof window['show_' + ZONE_ID] === 'function') window['show_' + ZONE_ID]();
                document.getElementById('adScreen').style.display = 'flex';
                let t = 15;
                let iv = setInterval(() => {
                    t--; document.getElementById('timer').innerText = t;
                    if(t <= 0) { clearInterval(iv); sendFile(id); }
                }, 1000);
            }

            async function sendFile(id) {
                await fetch('/api/send', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({userId: tg.initDataUnsafe.user?.id || 0, movieId: id})});
                document.getElementById('adScreen').style.display = 'none';
                document.getElementById('successModal').style.display = 'flex';
            }

            function openReqModal() { document.getElementById('reqModal').style.display = 'flex'; }
            
            async function sendReq() {
                const text = document.getElementById('reqText').value;
                if(!text) return alert('মুভির নাম লিখুন!');
                await fetch('/api/request', { method:'POST', headers:{'Content-Type':'application/json'}, 
                    body:JSON.stringify({uid: tg.initDataUnsafe.user?.id || 0, uname: tg.initDataUnsafe.user?.first_name || 'Guest', movie: text})
                });
                document.getElementById('reqModal').style.display = 'none';
                document.getElementById('reqText').value = '';
                alert('রিকোয়েস্ট সফলভাবে পাঠানো হয়েছে!');
            }

            loadMovies(true); // Initial load
        </script>
    </body>
    </html>
    """
    html_code = html_code.replace("{{ZONE_ID}}", zone_id).replace("{{TG_LINK}}", tg_url).replace("{{LINK_18}}", link_18)
    return html_code

# --- ৩. API এন্ডপয়েন্ট ---

# 3.1: Paginated & Searchable List
@app.get("/api/list")
async def list_movies(page: int = 1, q: str = ""):
    limit = 10
    skip = (page - 1) * limit
    query = {"title": {"$regex": q, "$options": "i"}} if q else {}
    
    movies = []
    async for m in db.movies.find(query).sort("created_at", -1).skip(skip).limit(limit):
        m["_id"] = str(m["_id"])
        m["created_at"] = str(m.get("created_at", ""))
        movies.append(m)
    return movies

# 3.2: Proxy Image Endpoint (টেলিগ্রামের ছবি ওয়েবপেজে দেখানোর জন্য)
@app.get("/api/image/{photo_id}")
async def get_image(photo_id: str):
    try:
        file_info = await bot.get_file(photo_id)
        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        
        async def stream_image():
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as resp:
                    async for chunk in resp.content.iter_chunked(1024):
                        yield chunk
        return StreamingResponse(stream_image(), media_type="image/jpeg")
    except Exception as e:
        print("Image Error:", e)
        return {"error": "Image not found"}

# 3.3: Send File Endpoint
@app.post("/api/send")
async def send_file(d: dict = Body(...)):
    if d['userId'] == 0: return {"ok": False}
    try:
        m = await db.movies.find_one({"_id": ObjectId(d['movieId'])})
        if m:
            caption = f"🎥 **{m['title']}**\n\n📥 Join: @MovieeBD"
            if m.get("file_type") == "video": await bot.send_video(d['userId'], m['file_id'], caption=caption)
            else: await bot.send_document(d['userId'], m['file_id'], caption=caption)
    except: pass
    return {"ok": True}

# 3.4: Request Movie Endpoint
class ReqModel(BaseModel):
    uid: int
    uname: str
    movie: str

@app.post("/api/request")
async def handle_request(data: ReqModel):
    text = f"🔔 **নতুন মুভি রিকোয়েস্ট!**\n\n👤 ইউজার: {data.uname} (`{data.uid}`)\n🎬 মুভির নাম: **{data.movie}**"
    try: await bot.send_message(ADMIN_ID, text)
    except: pass
    return {"ok": True}

async def start():
    port = int(os.getenv("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="asyncio")
    server = uvicorn.Server(config)
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.gather(server.serve(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(start())
