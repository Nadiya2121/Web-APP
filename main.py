import os, asyncio, datetime, uvicorn
from fastapi import FastAPI, Body, Request
from fastapi.responses import HTMLResponse
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from motor.motor_asyncio import AsyncIOMotorClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bson import ObjectId

# --- কনফিগারেশন ---
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URI")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
APP_URL = os.getenv("APP_URL")

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()
scheduler = AsyncIOScheduler()

# MongoDB কানেকশন
client = AsyncIOMotorClient(MONGO_URL)
db = client['movie_database']

admin_temp = {}

# --- ১. বটের কাজ (অ্যাডমিন কমান্ড) ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    kb = [[types.InlineKeyboardButton(text="🎬 ওপেন মুভি অ্যাপ", web_app=types.WebAppInfo(url=APP_URL))]]
    markup = types.InlineKeyboardMarkup(inline_keyboard=kb)
    
    if message.from_user.id == ADMIN_ID:
        text = (
            "👋 **হ্যালো অ্যাডমিন!**\n\n"
            "⚙️ অ্যাড আইডি সেট: `/setad [ID]`\n"
            "🔗 চ্যানেল লিঙ্ক সেট: `/setlink [URL]`\n\n"
            "📥 মুভি অ্যাড করতে প্রথমে ভিডিও ফাইল পাঠান।"
        )
    else:
        text = f"👋 **স্বাগতম {message.from_user.first_name}!**\nমুভি দেখতে নিচের বাটনে ক্লিক করুন।"
    await message.answer(text, reply_markup=markup, parse_mode="Markdown")

@dp.message(Command("setad"))
async def set_ad(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        new_id = message.text.split(" ")[1]
        await db.settings.update_one({"id": "ad_config"}, {"$set": {"zone_id": new_id}}, upsert=True)
        await message.answer(f"✅ জোন আইডি আপডেট হয়েছে: `{new_id}`")
    except: await message.answer("ভুল ফরম্যাট! /setad 10916755")

@dp.message(Command("setlink"))
async def set_link(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        url = message.text.split(" ")[1]
        await db.settings.update_one({"id": "tg_link"}, {"$set": {"url": url}}, upsert=True)
        await message.answer(f"✅ লিঙ্ক আপডেট হয়েছে: `{url}`")
    except: await message.answer("ভুল ফরম্যাট! /setlink https://t.me/MovieeBD")

@dp.message(F.document | F.video)
async def catch_file(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    fid = message.document.file_id if message.document else message.video.file_id
    admin_temp[message.from_user.id] = fid
    await message.answer("✅ ফাইল পেয়েছি! এখন লিখুন: `নাম | থাম্বনেইল লিঙ্ক`")

@dp.message(F.text)
async def save_movie(message: types.Message):
    if message.from_user.id != ADMIN_ID or "|" not in message.text: return
    uid = message.from_user.id
    if uid not in admin_temp: return
    try:
        title, thumb = message.text.split("|")
        await db.movies.insert_one({
            "title": title.strip(), "thumbnail": thumb.strip(),
            "file_id": admin_temp[uid], "created_at": datetime.datetime.utcnow()
        })
        del admin_temp[uid]
        await message.answer("🎉 মুভিটি অ্যাপে যুক্ত করা হয়েছে!")
    except Exception as e: await message.answer(f"এরর: {e}")

# --- ২. ওয়েব অ্যাপ UI (ফুল ডিজাইন + ফিক্সড লজিক) ---

@app.get("/", response_class=HTMLResponse)
async def web_ui():
    # সেটিংস রিড করা
    ad_cfg = await db.settings.find_one({"id": "ad_config"})
    link_cfg = await db.settings.find_one({"id": "tg_link"})
    zone_id = ad_cfg['zone_id'] if ad_cfg else "10916755"
    tg_url = link_cfg['url'] if link_cfg else "https://t.me/MovieeBD"

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
            body { background:#fff; font-family: sans-serif; color:#333; }
            header { display:flex; justify-content:space-between; align-items:center; padding:15px; border-bottom:1px solid #eee; position:sticky; top:0; background:#fff; z-index:1000; }
            .logo { font-size:24px; font-weight:bold; }
            .logo span { background:red; color:#fff; padding:2px 5px; border-radius:5px; margin-left:5px; font-size:16px; }
            .user-info { display:flex; align-items:center; gap:8px; background:#f1f5f9; padding:5px 12px; border-radius:20px; border:1px solid #ddd; font-weight:bold; font-size:14px; }
            .user-info img { width:26px; height:26px; border-radius:50%; border:1px solid #000; object-fit:cover; }
            .search-box { padding:15px; }
            .search-input { width:100%; padding:12px; border-radius:25px; border:2px solid #ddd; outline:none; text-align:center; background:#f9f9f9; }
            .grid { padding:0 15px 100px; }
            .card { margin-bottom:25px; cursor:pointer; }
            .post-content { border-radius:15px; overflow:hidden; border:3px solid; border-image: linear-gradient(to right, #0f0, #00f) 1; position:relative; }
            .post-content img { width:100%; height:200px; object-fit:cover; display:block; }
            .lock-overlay { position:absolute; top:50%; left:50%; transform:translate(-50%, -50%); background:rgba(0,0,0,0.6); padding:5px 15px; border-radius:20px; color:red; font-weight:bold; font-size:12px; display:flex; align-items:center; }
            .card-footer { display:flex; align-items:center; padding:10px 5px; }
            .mb-logo { background:#f87171; color:#fff; width:35px; height:35px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-weight:bold; font-size:14px; margin-right:10px; }
            .floating-18 { position:fixed; bottom:95px; right:20px; background:red; color:white; width:50px; height:50px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-weight:bold; z-index:500; border:2px solid #fff; }
            .floating-tg { position:fixed; bottom:30px; right:20px; background:#24A1DE; color:white; width:55px; height:55px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:24px; z-index:500; }
            .ad-screen { position:fixed; top:0; left:0; width:100%; height:100%; background:#0f172a; display:none; flex-direction:column; align-items:center; justify-content:center; z-index:2000; color:#fff; }
            .timer { width:100px; height:100px; border-radius:50%; border:5px solid red; display:flex; align-items:center; justify-content:center; font-size:40px; margin-bottom:20px; color:red; font-weight:bold; }
            .modal { position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.8); display:none; align-items:center; justify-content:center; z-index:3000; }
            .modal-content { background:#fff; width:90%; padding:30px; border-radius:15px; text-align:center; color:#333; }
        </style>
    </head>
    <body>
        <header>
            <div class="logo">MovieZone <span>BD</span></div>
            <div class="user-info">
                <span id="uName">Guest</span>
                <img id="uPic" src="https://cdn-icons-png.flaticon.com/512/3135/3135715.png">
            </div>
        </header>

        <div class="search-box">
            <input type="text" class="search-input" placeholder="মুভি বা এপিসোড সার্চ করুন..." oninput="search()">
        </div>

        <div class="grid" id="movieGrid"><p style="text-align:center; padding:20px; color:gray;">মুভি লোড হচ্ছে...</p></div>

        <div class="floating-18">18+</div>
        <div class="floating-tg" id="tgBtn"><i class="fa-brands fa-telegram"></i></div>

        <div id="adScreen" class="ad-screen">
            <div class="timer" id="timer">15</div>
            <p>সার্ভারের সাথে কানেক্ট হচ্ছে...</p>
        </div>

        <div id="successModal" class="modal">
            <div class="modal-content">
                <i class="fa-solid fa-circle-check" style="font-size:60px; color:green;"></i>
                <h2 style="margin:15px 0;">সফলভাবে সম্পন্ন হয়েছে!</h2>
                <button onclick="tg.close()" style="background:#00ff88; color:#000; padding:12px; border-radius:8px; border:none; width:100%; font-weight:bold; cursor:pointer;">ইনবক্স চেক করুন</button>
            </div>
        </div>

        <script>
            let tg = window.Telegram.WebApp; tg.expand();
            let movies = [];
            const ZONE_ID = \"""" + zone_id + r"""\";
            const TG_LINK = \"""" + tg_url + r"""\";

            // প্রোফাইল ও লিঙ্ক সেটআপ
            if(tg.initDataUnsafe.user) {
                document.getElementById('uName').innerText = tg.initDataUnsafe.user.first_name;
                if(tg.initDataUnsafe.user.photo_url) {
                    document.getElementById('uPic').src = tg.initDataUnsafe.user.photo_url;
                }
            }
            document.getElementById('tgBtn').onclick = () => window.open(TG_LINK);

            // মনিট্যাগ স্ক্রিপ্ট ইনজেক্ট
            const s = document.createElement('script');
            s.src = '//libtl.com/sdk.js'; s.setAttribute('data-zone', ZONE_ID); s.setAttribute('data-sdk', 'show_' + ZONE_ID);
            document.head.appendChild(s);

            async function load() {
                try {
                    const r = await fetch('/api/list');
                    movies = await r.json();
                    render(movies);
                } catch(e) { document.getElementById('movieGrid').innerHTML = "মুভি লোড হতে পারেনি!"; }
            }

            function render(data) {
                const g = document.getElementById('movieGrid');
                if(data.length === 0) { g.innerHTML = "কোন মুভি পাওয়া যায়নি!"; return; }
                g.innerHTML = data.map(m => `
                    <div class="card" onclick="startAd('${m._id}')">
                        <div class="post-content">
                            <img src="${m.thumbnail}" onerror="this.src='https://via.placeholder.com/400x200?text=No+Image'">
                            <div class="lock-overlay"><i class="fa-solid fa-lock"></i> 24H Locked</div>
                        </div>
                        <div class="card-footer">
                            <div class="mb-logo">MB</div>
                            <div style="font-size:14px; font-weight:500;">${m.title} <br><span style="color:gray; font-size:12px;">Join : @MovieeBD</span></div>
                        </div>
                    </div>
                `).join('');
            }

            function search() {
                let q = document.querySelector('.search-input').value.toLowerCase();
                render(movies.filter(m => m.title.toLowerCase().includes(q)));
            }

            function startAd(id) {
                if (typeof window['show_' + ZONE_ID] === 'function') window['show_' + ZONE_ID]();
                document.getElementById('adScreen').style.display = 'flex';
                let t = 15;
                let iv = setInterval(() => {
                    t--; document.getElementById('timer').innerText = t;
                    if(t <= 0) { clearInterval(iv); send(id); }
                }, 1000);
            }

            async function send(id) {
                await fetch('/api/send', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({userId: tg.initDataUnsafe.user.id, movieId: id})});
                document.getElementById('adScreen').style.display = 'none';
                document.getElementById('successModal').style.display = 'flex';
            }
            load();
        </script>
    </body>
    </html>
    """
    return html_code

# --- ৩. API এবং রানার ---

@app.get("/api/list")
async def list_movies():
    movies = []
    async for m in db.movies.find().sort("created_at", -1):
        m["_id"] = str(m["_id"])
        movies.append(m)
    return movies

@app.post("/api/send")
async def send_file(d: dict = Body(...)):
    m = await db.movies.find_one({"_id": ObjectId(d['movieId'])})
    if m:
        await bot.send_document(d['userId'], m['file_id'], caption=f"🎥 {m['title']}\nJoin : @BDMovieZoneBot")
    return {"ok": True}

async def start():
    port = int(os.getenv("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="asyncio")
    server = uvicorn.Server(config)
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.gather(server.serve(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(start())
