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
try:
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = client['movie_database']
except Exception as e:
    print(f"❌ MongoDB Connection Error: {e}")

# অ্যাডমিন ফাইল আইডি সাময়িক রাখার জন্য
admin_temp = {}

# --- ১. বটের কাজ (অ্যাডমিন এবং ইউজার আলাদা গাইড) ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    kb = [[types.InlineKeyboardButton(text="🎬 ওপেন মুভি অ্যাপ", web_app=types.WebAppInfo(url=APP_URL))]]
    markup = types.InlineKeyboardMarkup(inline_keyboard=kb)
    
    if message.from_user.id == ADMIN_ID:
        text = (
            "👋 **হ্যালো অ্যাডমিন!**\n\n"
            "নতুন মুভি অ্যাড করার নিয়ম:\n"
            "১. প্রথমে মুভি ফাইলটি (Video/Document) এখানে পাঠান।\n"
            "২. ফাইল পাওয়ার পর আমি নাম এবং পোস্টার লিঙ্ক চাইবো তখন সেটি দিবেন।"
        )
    else:
        text = (
            f"👋 হ্যালো **{message.from_user.first_name}**!\n\n"
            "🎬 আমাদের মুভি অ্যাপে আপনাকে স্বাগতম।\n"
            "মুভি পেতে নিচের বাটনে ক্লিক করে অ্যাপটি ওপেন করুন।"
        )
    await message.answer(text, reply_markup=markup, parse_mode="Markdown")

@dp.message(F.document | F.video)
async def catch_file(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    fid = message.document.file_id if message.document else message.video.file_id
    admin_temp[message.from_user.id] = fid
    await message.answer("✅ ফাইলটি পেয়েছি! এখন মুভির নাম এবং পোস্টার লিঙ্ক দিন।\n\n**ফরম্যাট:** `নাম | থাম্বনেইল লিঙ্ক`")

@dp.message(F.text)
async def save_movie(message: types.Message):
    if message.from_user.id != ADMIN_ID or "|" not in message.text: return
    uid = message.from_user.id
    if uid not in admin_temp: return
    
    try:
        title, thumb = message.text.split("|")
        await db.movies.insert_one({
            "title": title.strip(),
            "thumbnail": thumb.strip(),
            "file_id": admin_temp[uid],
            "created_at": datetime.datetime.utcnow()
        })
        del admin_temp[uid]
        await message.answer("🎉 মুভিটি অ্যাপে যুক্ত করা হয়েছে!")
    except Exception as e:
        await message.answer(f"⚠️ এরর: {e}")

# --- ২. ওয়েব অ্যাপ UI (মনিট্যাগ স্ক্রিপ্টসহ ভিডিওর মতো ডিজাইন) ---

@app.get("/", response_class=HTMLResponse)
async def web_ui():
    html_code = r"""
    <!DOCTYPE html>
    <html lang="bn">
    <head>
        <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>MovieZone BD</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
        
        <!-- আপনার মনিট্যাগ স্ক্রিপ্ট -->
        <script src='//libtl.com/sdk.js' data-zone='10916755' data-sdk='show_10916755'></script>

        <style>
            * { margin:0; padding:0; box-sizing:border-box; }
            body { background:#fff; font-family: sans-serif; color:#333; }
            header { display:flex; justify-content:space-between; align-items:center; padding:15px; border-bottom:1px solid #eee; position:sticky; top:0; background:#fff; z-index:100; }
            .logo { font-size:24px; font-weight:bold; }
            .logo span { background:red; color:#fff; padding:2px 5px; border-radius:5px; margin-left:5px; font-size:16px; }
            .admin-tag { background:#f1f5f9; padding:5px 15px; border-radius:20px; display:flex; align-items:center; border:1px solid #ddd; }
            .admin-tag img { width:25px; height:25px; border-radius:50%; margin-left:10px; border:1px solid #000; }
            
            .search-box { padding:15px; }
            .search-input { width:100%; padding:12px; border-radius:25px; border:2px solid #ddd; outline:none; text-align:center; background:#f9f9f9; }
            
            .grid { padding:0 15px 80px; }
            .card { margin-bottom:20px; border-radius:15px; overflow:hidden; border:3px solid; border-image: linear-gradient(to right, lime, blue) 1; position:relative; cursor:pointer; }
            .card img { width:100%; height:200px; object-fit:cover; display:block; }
            .card-title { padding:10px; font-weight:bold; font-size:14px; color:#555; }
            
            .lock-overlay { position:absolute; top:50%; left:50%; transform:translate(-50%, -50%); background:rgba(0,0,0,0.6); padding:5px 15px; border-radius:20px; color:red; font-weight:bold; font-size:12px; display:flex; align-items:center; }
            .lock-overlay i { margin-right:5px; }

            /* Ad Timer Screen */
            .ad-screen { position:fixed; top:0; left:0; width:100%; height:100%; background:#0f172a; display:none; flex-direction:column; align-items:center; justify-content:center; z-index:2000; color:#fff; }
            .timer-circle { width:100px; height:100px; border-radius:50%; border:5px solid red; display:flex; align-items:center; justify-content:center; font-size:40px; margin-bottom:20px; color:red; font-weight:bold; }
            
            /* Success Modal */
            .modal { position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.8); display:none; align-items:center; justify-content:center; z-index:3000; }
            .modal-content { background:#fff; width:90%; padding:30px; border-radius:15px; text-align:center; color:#333; }
            .age-tag { position:absolute; bottom:10px; right:10px; background:red; color:#fff; padding:5px 10px; border-radius:50%; font-weight:bold; font-size:12px; }
        </style>
    </head>
    <body>
        <header>
            <div class="logo">MovieZone <span>BD</span></div>
            <div class="admin-tag">Admin <img id="admPic" src="https://via.placeholder.com/30"></div>
        </header>

        <div class="search-box">
            <input type="text" class="search-input" placeholder="এপিসোড নাম্বার বা নাম দিয়ে সার্চ করুন..." onkeyup="search()">
        </div>

        <div class="grid" id="movieGrid"></div>

        <div id="adScreen" class="ad-screen">
            <div class="timer-circle" id="timer">15</div>
            <p>সার্ভারের সাথে কানেক্ট হচ্ছে...</p>
        </div>

        <div id="successModal" class="modal">
            <div class="modal-content">
                <i class="fa-solid fa-circle-check" style="font-size:60px; color:green;"></i>
                <h2 style="margin:15px 0;">সফলভাবে সম্পন্ন হয়েছে!</h2>
                <p>ভিডিওটি পেতে ইনবক্স চেক করুন।</p>
                <button onclick="tg.close()" style="background:#00ff88; color:#000; padding:12px 25px; border-radius:8px; border:none; margin-top:20px; font-weight:bold; cursor:pointer;">ইনবক্স চেক করুন</button>
            </div>
        </div>

        <script>
            let tg = window.Telegram.WebApp; tg.expand();
            let movies = [];

            if(tg.initDataUnsafe.user && tg.initDataUnsafe.user.photo_url) {
                document.getElementById('admPic').src = tg.initDataUnsafe.user.photo_url;
            }

            async function load() {
                const r = await fetch('/api/list');
                movies = await r.json();
                render(movies);
            }

            function render(data) {
                const grid = document.getElementById('movieGrid');
                grid.innerHTML = data.map(m => `
                    <div class="card" onclick="startProcess('${m._id}')">
                        <img src="${m.thumbnail}">
                        <div class="lock-overlay"><i class="fa-solid fa-lock"></i> 24H Locked</div>
                        <div class="age-tag">18+</div>
                        <div class="card-title">${m.title} Join : @MovieeBD</div>
                    </div>
                `).join('');
            }

            function search() {
                let q = document.querySelector('.search-input').value.toLowerCase();
                render(movies.filter(m => m.title.toLowerCase().includes(q)));
            }

            function startProcess(id) {
                // ১. আপনার মনিট্যাগ অ্যাড লোড করা
                if (typeof show_10916755 === 'function') {
                    show_10916755();
                }

                // ২. ১৫ সেকেন্ডের টাইমার দেখানো
                document.getElementById('adScreen').style.display = 'flex';
                let t = 15;
                let iv = setInterval(() => {
                    t--; document.getElementById('timer').innerText = t;
                    if(t <= 0) {
                        clearInterval(iv);
                        completeProcess(id);
                    }
                }, 1000);
            }

            async function completeProcess(id) {
                await fetch('/api/send', {
                    method: 'POST', headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({ userId: tg.initDataUnsafe.user.id, movieId: id })
                });
                document.getElementById('adScreen').style.display = 'none';
                document.getElementById('successModal').style.display = 'flex';
            }
            load();
        </script>
    </body>
    </html>
    """
    return html_code

# --- ৩. API রুটস এবং সার্ভিস ---

@app.get("/api/list")
async def list_movies():
    return [ {**m, "_id": str(m["_id"])} async for m in db.movies.find().sort("created_at", -1) ]

@app.post("/api/send")
async def send_file(data: dict = Body(...)):
    m = await db.movies.find_one({"_id": ObjectId(data['movieId'])})
    if m:
        await bot.send_document(data['userId'], m['file_id'], caption=f"🎥 {m['title']}\nJoin : @MovieeBD")
    return {"ok": True}

async def run_services():
    port = int(os.getenv("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="asyncio")
    server = uvicorn.Server(config)
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.gather(server.serve(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(run_services())
