import os
import datetime
import asyncio
import aiohttp
import copy
from fastapi import APIRouter, Body
from fastapi.responses import HTMLResponse
from bson import ObjectId
from cachetools import TTLCache

upcoming_router = APIRouter()

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "7dc544d9253bccc3cfecc1c677f69819")
tmdb_cache = TTLCache(maxsize=5, ttl=10800)

LANG_MAP = {
    "en": "Hollywood", 
    "hi": "Bollywood", 
    "ta": "Tamil", 
    "te": "Telugu", 
    "ml": "Malayalam",
    "bn": "Bengali"
}

# প্রতিটি ভাষার জন্য আলাদা ডাটা ফেচ করার ফাংশন
async def fetch_language_movies(session, lang_code, lang_name, today_str, next_30_days_str):
    url = f"https://api.themoviedb.org/3/discover/movie"
    params = {
        "api_key": TMDB_API_KEY,
        "primary_release_date.gte": today_str,
        "primary_release_date.lte": next_30_days_str,
        "with_original_language": lang_code,
        "sort_by": "popularity.desc",
        "page": 1
    }
    lang_movies = []
    try:
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            for m in data.get("results", [])[:10]:
                if m.get("poster_path"):
                    lang_movies.append({
                        "_id": f"tmdb_{m['id']}",
                        "title": m["title"],
                        "release_date": m["release_date"],
                        "language": lang_name,
                        "photo_url": f"https://image.tmdb.org/t/p/w500{m['poster_path']}",
                        "overview": m.get("overview", "No description available for this movie yet."),
                        "rating": round(m.get("vote_average", 0), 1),
                        "is_custom": False
                    })
    except Exception as e:
        print(f"TMDB Fetch Error for {lang_code}: {e}")
    return lang_movies

async def fetch_tmdb_upcoming():
    if "movies" in tmdb_cache:
        return copy.deepcopy(tmdb_cache["movies"])
    
    if not TMDB_API_KEY or TMDB_API_KEY == "YOUR_TMDB_API_KEY_HERE":
        return []

    today = datetime.datetime.utcnow().date()
    next_30_days = today + datetime.timedelta(days=30)
    today_str = today.strftime("%Y-%m-%d")
    next_30_days_str = next_30_days.strftime("%Y-%m-%d")
    
    movies = []
    
    # 🛑 UPDATE 1: asyncio.gather দিয়ে সব ভাষা প্যারালালি ফেচ করা হলো (সুপার ফাস্ট)
    async with aiohttp.ClientSession() as session:
        tasks = [
            fetch_language_movies(session, lang_code, lang_name, today_str, next_30_days_str)
            for lang_code, lang_name in LANG_MAP.items()
        ]
        results = await asyncio.gather(*tasks)
        for res in results:
            movies.extend(res)

    movies.sort(key=lambda x: x["release_date"])
    tmdb_cache["movies"] = movies
    return movies

@upcoming_router.get("/upcoming", response_class=HTMLResponse)
async def upcoming_page():
    with open("upcoming.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

@upcoming_router.get("/api/upcoming/movies")
async def get_upcoming_movies():
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
            "overview": c.get("overview", "Custom uploaded movie. Stay tuned for details!"),
            "rating": "N/A",
            "is_custom": True
        })
    
    all_movies = tmdb_movies + custom_movies
    all_movies.sort(key=lambda x: x["release_date"])
    
    return {"movies": all_movies}

@upcoming_router.post("/api/upcoming/custom")
async def add_custom_upcoming(data: dict = Body(...)):
    from main import db, admin_cache, validate_tg_data

    uid = data.get("uid", 0)
    if uid not in admin_cache or not validate_tg_data(data.get("initData", "")):
        return {"ok": False, "msg": "Unauthorized"}
        
    await db.upcoming_custom.insert_one({
        "title": data["title"],
        "release_date": data["release_date"],
        "language": data["language"],
        "photo_url": data["photo_url"],
        "overview": data.get("overview", "")
    })
    return {"ok": True}

@upcoming_router.delete("/api/upcoming/custom/{movie_id}")
async def delete_custom_upcoming(movie_id: str, data: dict = Body(...)):
    from main import db, admin_cache, validate_tg_data

    uid = data.get("uid", 0)
    if uid not in admin_cache or not validate_tg_data(data.get("initData", "")):
        return {"ok": False, "msg": "Unauthorized"}
        
    await db.upcoming_custom.delete_one({"_id": ObjectId(movie_id)})
    return {"ok": True}
