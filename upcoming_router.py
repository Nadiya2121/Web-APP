import os
import datetime
import aiohttp
import copy
from fastapi import APIRouter, Body
from fastapi.responses import HTMLResponse
from bson import ObjectId
from cachetools import TTLCache

upcoming_router = APIRouter()

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "7dc544d9253bccc3cfecc1c677f69819")  # এখানে আপনার API Key বসাবেন

tmdb_cache = TTLCache(maxsize=5, ttl=10800)

LANG_MAP = {
    "en": "Hollywood", "hi": "Bollywood", "ta": "Tamil", 
    "te": "Telugu", "bn": "Bengali"
}

async def fetch_tmdb_upcoming():
    if "movies" in tmdb_cache:
        return copy.deepcopy(tmdb_cache["movies"])
    
    if not TMDB_API_KEY or TMDB_API_KEY == "YOUR_TMDB_API_KEY_HERE":
        return []

    today = datetime.datetime.utcnow().date()
    next_30_days = today + datetime.timedelta(days=30)
    
    url = f"https://api.themoviedb.org/3/discover/movie"
    params = {
        "api_key": TMDB_API_KEY,
        "primary_release_date.gte": today.strftime("%Y-%m-%d"),
        "primary_release_date.lte": next_30_days.strftime("%Y-%m-%d"),
        "with_original_language": "en|hi|ta|te|bn",
        "sort_by": "primary_release_date.asc"
    }
    
    movies = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                data = await resp.json()
                for m in data.get("results", []):
                    lang = m.get("original_language", "en")
                    if lang in LANG_MAP:
                        movies.append({
                            "_id": f"tmdb_{m['id']}",
                            "title": m["title"],
                            "release_date": m["release_date"],
                            "language": LANG_MAP[lang],
                            "photo_url": f"https://image.tmdb.org/t/p/w500{m['poster_path']}" if m.get("poster_path") else "https://via.placeholder.com/500x750?text=No+Image",
                            "is_custom": False
                        })
        tmdb_cache["movies"] = movies
    except Exception as e:
        print(f"TMDB Fetch Error: {e}")
    return movies

@upcoming_router.get("/upcoming", response_class=HTMLResponse)
async def upcoming_page():
    with open("upcoming.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

@upcoming_router.get("/api/upcoming/movies")
async def get_upcoming_movies():
    # 🛑 সার্কুলার ইমপোর্ট ফিক্স করার জন্য লোকাল ইমপোর্ট করা হলো
    from main import db

    tmdb_movies = await fetch_tmdb_upcoming()
    
    today_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    custom_movies_cursor = db.upcoming_custom.find({"release_date": {"$gte": today_str}})
    custom_movies = []
    async for c in custom_movies_cursor:
        custom_movies.append({
            "_id": str(c["_id"]),
            "title": c["title"],
            "release_date": c["release_date"],
            "language": c["language"],
            "photo_url": c["photo_url"],
            "is_custom": True
        })
    
    all_movies = tmdb_movies + custom_movies
    all_movies.sort(key=lambda x: x["release_date"])
    
    return {"movies": all_movies}

@upcoming_router.post("/api/upcoming/custom")
async def add_custom_upcoming(data: dict = Body(...)):
    # 🛑 সার্কুলার ইমপোর্ট ফিক্স করার জন্য লোকাল ইমপোর্ট করা হলো
    from main import db, admin_cache, validate_tg_data

    uid = data.get("uid", 0)
    if uid not in admin_cache or not validate_tg_data(data.get("initData", "")):
        return {"ok": False, "msg": "Unauthorized"}
        
    await db.upcoming_custom.insert_one({
        "title": data["title"],
        "release_date": data["release_date"],
        "language": data["language"],
        "photo_url": data["photo_url"]
    })
    return {"ok": True}

@upcoming_router.delete("/api/upcoming/custom/{movie_id}")
async def delete_custom_upcoming(movie_id: str, data: dict = Body(...)):
    # 🛑 সার্কুলার ইমপোর্ট ফিক্স করার জন্য লোকাল ইমপোর্ট করা হলো
    from main import db, admin_cache, validate_tg_data

    uid = data.get("uid", 0)
    if uid not in admin_cache or not validate_tg_data(data.get("initData", "")):
        return {"ok": False, "msg": "Unauthorized"}
        
    await db.upcoming_custom.delete_one({"_id": ObjectId(movie_id)})
    return {"ok": True}
