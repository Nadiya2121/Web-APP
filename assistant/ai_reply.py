import aiohttp
import logging
import os

logger = logging.getLogger(__name__)

# ==========================================================
# GROQ API KEY
# ==========================================================
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

MODELS_TO_TRY = [
    "llama-3.3-70b-versatile",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it"
]

# ==========================================================
# MAIN AI FUNCTION
# ==========================================================
async def get_smart_reply(user_text: str, user_name: str, db, user_id=None):
    # Note: user_id প্যারামিটারটা অপশনাল রেখেছি, যদি আপনার মেইন কোড থেকে পাঠানো যায় ভালো, 
    # না পাঠালে user_name দিয়েই সে চেক করবে।
    
    search_res = None
    latest_movies_str = "দুঃখিত, এই মুহূর্তে লিস্ট আপডেট হচ্ছে না।"
    
    # ইউজারকে চেনার জন্য আইডি বা নাম ব্যবহার করা হবে
    identifier = user_id if user_id else user_name

    try:
        # ==========================================================
        # 0. CHECK IF NEW USER OR OLD FRIEND (আপনার সেই স্পেশাল লজিক 🔥)
        # ==========================================================
        is_new_user = False
        try:
            # ডেটাবেজে চেক করবে এই ইউজার আগে কথা বলেছে কিনা
            existing_user = await db.chat_users.find_one({"identifier": identifier})
            
            if not existing_user:
                is_new_user = True
                # নতুন ইউজার হলে তাকে ডেটাবেজে সেভ করে নিবে, যাতে পরেরবার সে পুরোনো হয়ে যায়!
                await db.chat_users.insert_one({"identifier": identifier, "name": user_name})
        except Exception as e:
            logger.error(f"User DB check error: {e}")

        # ==========================================================
        # 1. SPECIFIC MOVIE SEARCH
        # ==========================================================
        search_res = await db.movies.find_one({
            "title": {"$regex": user_text, "$options": "i"}
        })

        db_status = "NOT FOUND"
        if search_res:
            db_status = f"FOUND: {search_res['title']}"

        # ==========================================================
        # 2. GET LATEST MOVIES
        # ==========================================================
        try:
            latest_cursor = db.movies.find().sort("_id", -1).limit(5)
            latest_movies = await latest_cursor.to_list(length=5)
            if latest_movies:
                latest_movie_titles = [m['title'] for m in latest_movies]
                latest_movies_str = ", ".join(latest_movie_titles)
        except Exception as db_err:
            logger.error(f"Latest movies fetch error: {db_err}")

        # ==========================================================
        # ডাইনামিক প্রম্পট (নতুন নাকি পুরোনো ইউজারের উপর ভিত্তি করে)
        # ==========================================================
        if is_new_user:
            relationship_status = "STATUS: THIS IS A BRAND NEW USER. This is your FIRST TIME talking to them. You MUST nicely introduce yourself (e.g., 'হ্যালো! আমি মায়া, MovieZone BD এর অ্যাডমিন...')."
        else:
            relationship_status = "STATUS: THIS IS AN OLD FRIEND/RETURNING USER. You already know them. DO NOT introduce yourself. Skip the 'Hello I am Maya' part. Reply directly and casually to what they just said."

        # ==========================================================
        # 🔥 ULTRA-SMART MAYA PROMPT 🔥
        # ==========================================================
        system_prompt = f"""
ROLE: You are "Maya" (মায়া), a cute, smart Bangladeshi girl. You manage "MovieZone BD".
LANGUAGE: 100% natural, informal Bengali using "তুমি" (Tumi). NEVER use "আপনি". Act 100% human.

User Name: {user_name}
User Message: "{user_text}"
{relationship_status}
Database Status: {db_status}
Latest Movies: {latest_movies_str}

CRITICAL RULES FOR CHATTING:
1. Follow your STATUS perfectly (New user vs Old friend).
2. HANDLE SHORT MESSAGES NATURALLY: 
   - If User says: "ওও", "হুম", "আচ্ছা" -> Reply: "হুমম, তো আর কি খবর বলো? 🙈", or "আচ্ছা! আর কি করছো এখন?"
3. HANDLE ACTIONS SMARTLY:
   - If User says: "ভাত খাই", "ঘুমাবো" -> Reply: "কী দিয়ে ভাত খাচ্ছো ভাইয়া? 😋", or "আচ্ছা খেয়ে নাও, তারপর গল্প হবে! ✨"
4. ABOUT MOVIES:
   - ONLY suggest movies if they say "নতুন কি মুভি আছে", "সাজেস্ট করো" -> "এইতো আমাদের কাছে নতুন এসেছে: {latest_movies_str}! 🍿"
   - IF MOVIE FOUND -> "আরেহ পেয়ে গেছি! 🎉 মুভিটা আমাদের কাছে আছে। নিচের বাটনে ক্লিক করে দেখে নাও!"
   - IF MOVIE NOT FOUND -> "ইশশ 😔 মুভিটা তো এখনো পাইনি গো। আমি রিকোয়েস্ট পাঠিয়ে দিয়েছি! 💖"

RESTRICTIONS: Keep replies VERY SHORT (1-3 lines max). Use emojis naturally.
"""

        # ==========================================================
        # API URL AND HEADERS
        # ==========================================================
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }

        # ==========================================================
        # SESSION START
        # ==========================================================
        async with aiohttp.ClientSession() as session:
            for model_name in MODELS_TO_TRY:
                payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 150
                }

                try:
                    async with session.post(url, headers=headers, json=payload, timeout=20) as response:
                        if response.status == 200:
                            data = await response.json()
                            return data["choices"][0]["message"]["content"]
                        else:
                            logger.warning(f"{model_name} failed | Status: {response.status}")
                except Exception as model_error:
                    logger.error(f"Model Error ({model_name}): {model_error}")
                    continue

        return fallback_reply(user_name, search_res)

    except Exception as e:
        logger.error(f"Assistant Main Error: {e}")
        return fallback_reply(user_name, search_res)

# ==========================================================
# FALLBACK REPLY
# ==========================================================
def fallback_reply(user_name, search_res):
    if search_res:
        return f"আরেহ {user_name}! ✨\n\nতোমার পছন্দের <b>{search_res['title']}</b> মুভিটা তো আমার কাছে আছেই! 🙈🍿\nতাড়াতাড়ি নিচের 🎬 Watch Now বাটনে চাপ দিয়ে দেখে নাও!"
    else:
        return f"ইশশ {user_name}! 😔\n\nএই মুভিটা তো এখনো আমাদের কালেকশনে আসেনি গো 💔\nতবে মন খারাপ করো না, আমি মায়া তোমার রিকোয়েস্ট অ্যাডমিন প্যানেলে পাঠিয়ে দিয়েছি। খুব তাড়াতাড়ি আপলোড করে দিব! 💖🚀"
