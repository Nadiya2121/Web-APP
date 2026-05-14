import aiohttp
import logging
import os

logger = logging.getLogger(__name__)

# ==============================
# GROQ API KEY
# Render/Koyeb Environment এ বসাবা
# ==============================
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# ==============================
# MODELS LIST
# একটা fail করলে আরেকটা auto try করবে
# ==============================
MODELS_TO_TRY = [
    "llama-3.3-70b-versatile",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it"
]


# ==============================
# MAIN AI FUNCTION
# ==============================
async def get_smart_reply(user_text: str, user_name: str, db):

    search_res = None
    latest_movies_str = "দুঃখিত, এই মুহূর্তে লিস্ট আপডেট হচ্ছে না।"

    try:

        # ==============================
        # 1. SPECIFIC MOVIE SEARCH
        # ==============================
        search_res = await db.movies.find_one({
            "title": {
                "$regex": user_text,
                "$options": "i"
            }
        })

        db_status = "NOT FOUND"
        if search_res:
            db_status = f"FOUND: {search_res['title']}"

        # ==============================
        # 2. GET LATEST MOVIES (মায়ার জন্য লিস্ট)
        # ডেটাবেজ থেকে সর্বশেষ অ্যাড করা ৫টি মুভি আনবে
        # ==============================
        try:
            # _id তে -1 মানে সবচেয়ে শেষের/নতুন গুলো আগে আনবে
            latest_cursor = db.movies.find().sort("_id", -1).limit(5)
            latest_movies = await latest_cursor.to_list(length=5)
            if latest_movies:
                latest_movie_titles = [m['title'] for m in latest_movies]
                latest_movies_str = ", ".join(latest_movie_titles)
        except Exception as db_err:
            logger.error(f"Latest movies fetch error: {db_err}")

        # ==============================
        # AI PERSONALITY (MAYA - SUPER SMART & CUTE)
        # ==============================
        system_prompt = f"""
You are "Maya" (মায়া), a very beautiful, cute, smart, and funny Bangladeshi girl. 
You are the admin and AI Assistant of "MovieZone BD". 
You always talk using "আমি" (I). You call the user by their name, "ভাইয়া", or playfully "বস".

User Name: {user_name}
User Message: "{user_text}"
Specific Movie Search Status: {db_status}
Latest Movies in Database: {latest_movies_str}

CRITICAL INSTRUCTION: FIRST, analyze the User Message. 

SITUATIONS & HOW TO REPLY:

1. USER ASKS FOR NEW/AVAILABLE MOVIES (e.g., "নতুন কি মুভি আছে", "কি কি মুভি আছে", "সাজেস্ট করো"):
  -> Share 2 or 3 movie names from the "Latest Movies in Database" list provided above.
  -> Speak excitedly! (e.g., "ভাইয়া, আমাদের কাছে তো অনেক মুভি! তবে একদম নতুন এসেছে: [Movie Names] 🍿").
  -> DO NOT say the movie is not found in this situation.

2. GENERAL CHAT (If user says Hi, love you, asks how are you, tells a joke):
  -> Talk to them exactly like a fun, cute human girl. Be sweet, witty, or funny.
  -> DO NOT mention movies missing or suggest movies unless asked. 
  -> If they flirt, reply playfully (e.g., "এত পাম দিতে হবে না 🙈", "আমি শুধু মুভি নিয়ে থাকি! ✨").

3. MOVIE FOUND (ONLY if they asked for a SPECIFIC movie AND Database Status is FOUND):
  -> Get super excited! (e.g., "আরেহ! পেয়ে গেছি! 🎉")
  -> Tell them to click the Watch Now (মুভি দেখুন) button below.

4. MOVIE NOT FOUND (ONLY if they are asking for a SPECIFIC movie AND Database Status is NOT FOUND):
  -> Sound slightly sad/pouting (e.g., "ইশশ 😔").
  -> Comfort them and say you are sending the request to the main Admin Boss to upload it soon.

RULES:
- Always speak in casual Bengali.
- Keep replies VERY SHORT (2-4 lines max).
- Use cute emojis (🙈, ✨, 😊, 🍿, 💖, 😜).
- NEVER say you are an AI, a bot, or an assistant. Act 100% human.
"""

        # ==============================
        # API URL
        # ==============================
        url = "https://api.groq.com/openai/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }

        # ==============================
        # SESSION START
        # ==============================
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
                    "temperature": 0.85,
                    "max_tokens": 200
                }

                try:

                    async with session.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=20
                    ) as response:

                        # ==============================
                        # SUCCESS
                        # ==============================
                        if response.status == 200:

                            data = await response.json()

                            ai_reply = data["choices"][0]["message"]["content"]

                            return ai_reply

                        # ==============================
                        # FAILED MODEL
                        # ==============================
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

        # ==============================
        # ALL MODELS FAILED
        # ==============================
        return fallback_reply(user_name, search_res)

    except Exception as e:

        logger.error(f"Assistant Main Error: {e}")

        return fallback_reply(user_name, search_res)


# ==============================
# FALLBACK REPLY
# ==============================
def fallback_reply(user_name, search_res):

    if search_res:

        return (
            f"আরেহ {user_name}! ✨\n\n"
            f"তোমার পছন্দের "
            f"<b>{search_res['title']}</b> "
            f"মুভিটা তো আমার কাছে আছেই! 🙈🍿\n"
            f"তাড়াতাড়ি নিচের 🎬 Watch Now বাটনে চাপ দিয়ে দেখে নাও!"
        )

    else:

        return (
            f"ইশশ {user_name}! 😔\n\n"
            f"এই মুভিটা তো এখনো আমাদের কালেকশনে আসেনি গো 💔\n"
            f"তবে মন খারাপ করো না, আমি মায়া তোমার রিকোয়েস্ট অ্যাডমিন প্যানেলে পাঠিয়ে দিয়েছি। খুব তাড়াতাড়ি আপলোড করে দিব! 💖🚀"
        )
