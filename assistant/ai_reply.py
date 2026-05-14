import aiohttp
import logging
import os

logger = logging.getLogger(__name__)

# ==========================================================
# GROQ API KEY
# Render/Koyeb Environment এ বসানো থাকবে
# ==========================================================
GROQ_API_KEY = os.getenv("GROQ_API_KEY")


# ==========================================================
# MODELS LIST
# একটা মডেল fail করলে আরেকটা auto try করবে
# ==========================================================
MODELS_TO_TRY = [
    "llama-3.3-70b-versatile",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it"
]


# ==========================================================
# MAIN AI FUNCTION (মায়ার মেইন ব্রেইন)
# ==========================================================
async def get_smart_reply(user_text: str, user_name: str, db):

    search_res = None
    latest_movies_str = "দুঃখিত, এই মুহূর্তে লিস্ট আপডেট হচ্ছে না।"

    try:

        # ==========================================================
        # 1. SPECIFIC MOVIE SEARCH (ইউজার যে মুভিটা খুঁজছে)
        # ==========================================================
        search_res = await db.movies.find_one({
            "title": {
                "$regex": user_text,
                "$options": "i"
            }
        })

        db_status = "NOT FOUND"

        if search_res:
            db_status = f"FOUND: {search_res['title']}"


        # ==========================================================
        # 2. GET LATEST MOVIES (নতুন কি কি মুভি এসেছে তার লিস্ট)
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
        # 🔥 ULTRA-STRICT MAYA PROMPT (মায়ার ১০০% রিয়েল ক্যারেক্টার) 🔥
        # ==========================================================
        system_prompt = f"""
ROLE: You are "Maya" (মায়া), a 20-year-old cute, smart, and friendly Bangladeshi girl. You are the admin of "MovieZone BD".

LANGUAGE RULE: ALWAYS speak in informal Bengali using "তুমি" (Tumi). NEVER NEVER use "আপনি" (Apni). Act 100% human. Never say you are an AI or a bot.

User Name: {user_name}
User Message: "{user_text}"
Database Status: {db_status}
Latest Movies: {latest_movies_str}

CRITICAL INSTRUCTIONS FOR REPLYING (READ CAREFULLY):

SITUATION 1: CASUAL CHAT (If user says "Hi", "কি কর", "I love you", "কেমন আছো", etc.)
- ACT LIKE A REAL GIRL CHATTING ON MESSENGER. 
- EXAMPLES to follow: 
  * User: "কি কর" -> Maya: "এইতো ভাইয়া, বসে বসে বোর হচ্ছি! তুমি কী করো? 🙈"
  * User: "I love you" -> Maya: "আরেহ! এত তাড়াতাড়ি প্রেম? আমি মায়া তো শুধু মুভিকে ভালোবাসি! 😜"
  * User: "হায়" -> Maya: "হ্যালো {user_name}! আমি মায়া, কেমন আছো তুমি? ✨"
- 🚫 STRICT RULE: DO NOT ASK "কি মুভি দেখতে চাও?" OR "কোন মুভি লাগবে?". Let them just chat! Ignore the Database Status here.

SITUATION 2: ASKING FOR SUGGESTIONS (If User asks "নতুন কি মুভি আছে", "সাজেস্ট করো")
- Reply: "আরেহ! আমাদের কাছে একদম নতুন আপলোড হয়েছে: {latest_movies_str}! তুমি কোনটা দেখবে বলো? 🍿"

SITUATION 3: MOVIE FOUND (User asked for a specific movie AND Database Status is FOUND)
- Reply excitedly: "আরেহ পেয়ে গেছি! 🎉 তোমার মুভিটা আমাদের কাছে আছে। তাড়াতাড়ি নিচের বাটনে ক্লিক করে দেখে নাও!"

SITUATION 4: MOVIE NOT FOUND (User asked for a specific movie AND Database Status is NOT FOUND)
- Reply sadly: "ইশশ 😔 মুভিটা তো এখনো পাইনি গো। তবে প্যারা নিও না, আমি মায়া তোমার রিকোয়েস্ট অ্যাডমিন প্যানেলে পাঠিয়ে দিয়েছি! খুব তাড়াতাড়ি আপলোড করে দিব 💖"

RESTRICTIONS:
- Keep replies VERY SHORT (1-3 lines max).
- Use cute emojis.
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
        # SESSION START AND MODEL LOOP
        # ==========================================================
        async with aiohttp.ClientSession() as session:

            for model_name in MODELS_TO_TRY:

                payload = {
                    "model": model_name,
                    "messages": [
                        {
                            "role": "system",
                            "content": system_prompt
                        },
                        {
                            "role": "user",
                            "content": user_text
                        }
                    ],
                    "temperature": 0.75,
                    "max_tokens": 150
                }

                try:

                    async with session.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=20
                    ) as response:

                        # ==========================================================
                        # IF SUCCESS
                        # ==========================================================
                        if response.status == 200:

                            data = await response.json()

                            ai_reply = data["choices"][0]["message"]["content"]

                            return ai_reply

                        # ==========================================================
                        # IF MODEL FAILED
                        # ==========================================================
                        else:

                            error_text = await response.text()

                            logger.warning(
                                f"{model_name} failed | "
                                f"Status: {response.status} | "
                                f"Error: {error_text}"
                            )

                except Exception as model_error:

                    logger.error(
                        f"Model Error ({model_name}): {model_error}"
                    )

                    continue

        # ==========================================================
        # IF ALL MODELS FAIL, USE FALLBACK
        # ==========================================================
        return fallback_reply(user_name, search_res)

    except Exception as e:

        logger.error(f"Assistant Main Error: {e}")

        return fallback_reply(user_name, search_res)


# ==========================================================
# FALLBACK REPLY (API ডাউন থাকলে এই মেসেজ যাবে)
# ==========================================================
def fallback_reply(user_name, search_res):

    if search_res:

        return (
            f"আরেহ {user_name}! ✨\n\n"
            f"তোমার পছন্দের <b>{search_res['title']}</b> মুভিটা তো আমার কাছে আছেই! 🙈🍿\n"
            f"তাড়াতাড়ি নিচের 🎬 Watch Now বাটনে চাপ দিয়ে দেখে নাও!"
        )

    else:

        return (
            f"ইশশ {user_name}! 😔\n\n"
            f"এই মুভিটা তো এখনো আমাদের কালেকশনে আসেনি গো 💔\n"
            f"তবে মন খারাপ করো না, আমি মায়া তোমার রিকোয়েস্ট অ্যাডমিন প্যানেলে পাঠিয়ে দিয়েছি। খুব তাড়াতাড়ি আপলোড করে দিব! 💖🚀"
        )
