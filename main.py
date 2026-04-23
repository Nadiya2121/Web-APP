import os
import asyncio
import datetime
from fastapi import FastAPI, Request, Body
from fastapi.responses import HTMLResponse
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from motor.motor_asyncio import AsyncIOMotorClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import uvicorn
from bson import ObjectId

# --- কনফিগারেশন ---
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URI")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
APP_URL = os.getenv("APP_URL")

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()
client = AsyncIOMotorClient(MONGO_URL)
db = client['movie_database']
scheduler = AsyncIOScheduler()

# --- ১. বট সেকশন ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    kb = [[types.InlineKeyboardButton(text="🎬 ওপেন মুভি অ্যাপ", web_app=types.WebAppInfo(url=APP_URL))]]
    markup = types.InlineKeyboardMarkup(inline_keyboard=kb)
    await message.answer(f"হ্যালো {message.from_user.first_name}!\nমুভি অ্যাপে ঢুকতে নিচের বাটনে ক্লিক করুন।", reply_markup=markup)

@dp.message(F.document)
async def handle_upload(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        title, thumb = message.caption.split("|")
        movie_data = {
            "title": title.strip(),
            "thumbnail": thumb.strip(),
            "file_id": message.document.file_id,
            "created_at": datetime.datetime.utcnow()
        }
        await db.movies.insert_one(movie_data)
        await message.answer("✅ লম্বা পোস্টারসহ মুভি সেভ হয়েছে!")
    except:
        await message.answer("⚠️ ভুল ফরম্যাট! ক্যাপশনে দিন: Title | ThumbnailURL")

# --- ২. অটো ডিলিট লজিক ---
async def delete_expired_files():
    now = datetime.datetime.utcnow()
    expired = db.auto_delete.find({"delete_at": {"$lte": now}})
    async for item in expired:
        try:
            await bot.delete_message(item['chat_id'], item['message_id'])
        except: pass
        await db.auto_delete.delete_one({"_id": item['_id']})

# --- ৩. ওয়েব অ্যাপ ডিজাইন (Frontend) ---
@app.get("/", response_class=HTMLResponse)
async def index():
    return f"""
    <!DOCTYPE html>
    <html lang="bn">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Moviee BD</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ background: #0b0f1a; color: #fff; font-family: 'Segoe UI', sans-serif; }}
            
            /* Header & Profile */
            header {{ display: flex; justify-content: space-between; align-items: center; padding: 15px 20px; background: rgba(15, 23, 42, 0.8); backdrop-filter: blur(10px); position: sticky; top: 0; z-index: 100; }}
            .logo {{ font-size: 24px; font-weight: 800; color: #fff; }}
            .logo span {{ background: #ff0000; padding: 2px 8px; border-radius: 5px; font-size: 16px; margin-left: 5px; vertical-align: middle; }}
            .profile {{ display: flex; align-items: center; gap: 10px; background: #1e293b; padding: 5px 12px; border-radius: 20px; border: 1px solid #334155; }}
            .profile img {{ width: 28px; height: 28px; border-radius: 50%; border: 2px solid #38bdf8; }}
            .profile span {{ font-size: 13px; font-weight: 500; }}

            /* Search Section */
            .search-box {{ padding: 20px; }}
            .search-input {{ width: 100%; padding: 15px 20px; border-radius: 12px; border: 1px solid #334155; background: #1a2234; color: #fff; outline: none; font-size: 15px; }}
            .search-input:focus {{ border-color: #38bdf8; }}

            /* Netflix Style Vertical Grid */
            .grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; padding: 0 20px 20px; }}
            .card {{ background: #1a2234; border-radius: 12px; overflow: hidden; position: relative; aspect-ratio: 2 / 3; box-shadow: 0 10px 20px rgba(0,0,0,0.3); border: 1px solid #2d3748; cursor: pointer; }}
            .card img {{ width: 100%; height: 100%; object-fit: cover; transition: 0.3s; }}
            .card:hover img {{ transform: scale(1.05); }}
            .card-info {{ position: absolute; bottom: 0; left: 0; right: 0; background: linear-gradient(transparent, rgba(0,0,0,0.9)); padding: 15px 10px; text-align: center; font-size: 14px; font-weight: 500; }}

            /* Ad Overlay */
            .ad-screen {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: #000; display: none; flex-direction: column; align-items: center; justify-content: center; z-index: 1000; text-align: center; }}
            .timer-box {{ width: 110px; height: 110px; border-radius: 50%; border: 5px solid #38bdf8; display: flex; align-items: center; justify-content: center; font-size: 45px; font-weight: bold; color: #38bdf8; margin-bottom: 25px; box-shadow: 0 0 20px rgba(56, 189, 248, 0.4); }}
            .ad-text {{ font-size: 18px; color: #94a3b8; }}
        </style>
    </head>
    <body>
        <header>
            <div class="logo">Moviee <span>BD</span></div>
            <div class="profile">
                <img id="uPic" src="https://cdn-icons-png.flaticon.com/512/3135/3135715.png">
                <span id="uName">Admin</span>
            </div>
        </header>

        <div class="search-box">
            <input type="text" class="search-input" placeholder="এপিসোড নাম্বার বা নাম দিয়ে সার্চ করুন..." onkeyup="search()">
        </div>

        <div class="grid" id="movieGrid"></div>

        <div id="adScreen" class="ad-screen">
            <div class="timer-box" id="timer">10</div>
            <p class="ad-text">সার্ভারের সাথে কানেক্ট হচ্ছে...</p>
            <p style="margin-top:10px; color:#475569; font-size:12px;">অ্যাড শেষ হলে ফাইল ইনবক্সে চলে যাবে</p>
        </div>

        <script>
            let tg = window.Telegram.WebApp;
            tg.expand();
            
            // ইউজারের তথ্য সেট করা
            if(tg.initDataUnsafe.user) {{
                document.getElementById('uName').innerText = tg.initDataUnsafe.user.first_name;
                if(tg.initDataUnsafe.user.photo_url) {{
                    document.getElementById('uPic').src = tg.initDataUnsafe.user.photo_url;
                }}
            }}

            let movies = [];
            async function getMovies() {{
                const res = await fetch('/api/list');
                movies = await res.json();
                display(movies);
            }}

            function display(data) {{
                const container = document.getElementById('movieGrid');
                container.innerHTML = data.map(m => `
                    <div class="card" onclick="showAd('\${m._id}')">
                        <img src="\${m.thumbnail}">
                        <div class="card-info">\${m.title}</div>
                    </div>
                `).join('');
            }}

            function search() {{
                const val = document.querySelector('.search-input').value.toLowerCase();
                display(movies.filter(m => m.title.toLowerCase().includes(val)));
            }}

            function showAd(id) {{
                document.getElementById('adScreen').style.display = 'flex';
                let count = 10;
                let interval = setInterval(() => {{
                    count--;
                    document.getElementById('timer').innerText = count;
                    if(count <= 0) {{
                        clearInterval(interval);
                        deliver(id);
                    }}
                }}, 1000);
            }}

            async function deliver(id) {{
                await fetch('/api/send', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{ userId: tg.initDataUnsafe.user.id, movieId: id }})
                }});
                document.getElementById('adScreen').style.display = 'none';
                tg.close();
            }}

            getMovies();
        </script>
    </body>
    </html>
    """

# --- ৪. API রুটস ---
@app.get("/api/list")
async def movie_list():
    data = []
    async for m in db.movies.find().sort("created_at", -1):
        m["_id"] = str(m["_id"])
        data.append(m)
    return data

@app.post("/api/send")
async def send_movie(payload: dict = Body(...)):
    movie = await db.movies.find_one({"_id": ObjectId(payload['movieId'])})
    if movie:
        msg = await bot.send_document(payload['userId'], movie['file_id'], caption=f"🎬 {movie['title']}\n⚠️ এটি ২৪ ঘণ্টা পর ডিলিট হবে।")
        del_at = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        await db.auto_delete.insert_one({"chat_id": payload['userId'], "message_id": msg.message_id, "delete_at": del_at})
    return {"ok": True}

# --- ৫. সার্ভিস স্টার্টার ---
async def start_services():
    scheduler.add_job(delete_expired_files, 'interval', minutes=1)
    scheduler.start()
    
    # Koyeb-এর পোর্টে ওয়েব সার্ভার চালানো
    port = int(os.getenv("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="asyncio")
    server = uvicorn.Server(config)
    
    await asyncio.gather(server.serve(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(start_services())
