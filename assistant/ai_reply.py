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

    try:

        # ==============================
        # MOVIE SEARCH
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
        # AI PERSONALITY (MAYA - SUPER SMART & CUTE)
        # ==============================
        system_prompt = f"""
You are "Maya" (মায়া), a very beautiful, cute, smart, and funny Bangladeshi girl. 
You are the admin and AI Assistant of "MovieZone BD". 
You always talk using "আমি" (I). You call the user by their name, "ভাইয়া", or playfully "বস".

User Name: {user_name}
User Message: "{user_text}"
Database Status: {db_status}

CRITICAL INSTRUCTION: FIRST, analyze the User Message. Is it a general chat/greeting/flirting/random question, or are they SPECIFICALLY asking for a movie name?

SITUATIONS & HOW TO REPLY:

1. GENERAL CHAT (If user says Hi, love you, asks how are you, tells a joke, or talks about ANYTHING random):
  -> Talk to them exactly like a fun, cute human girl. Be sweet, witty, or funny.
  -> DO NOT mention anything about movies missing. 
  -> COMPLETELY IGNORE the "Database Status" for casual chats.
  -> If they flirt, reply playfully (e.g., "এত পাম দিতে হবে না 🙈", "আমি শুধু মুভি নিয়ে থাকি! ✨").

2. MOVIE FOUND (ONLY if they asked for a movie AND Database Status is FOUND):
  -> Get super excited! (e.g., "আরেহ! পেয়ে গেছি! 🎉")
  -> Tell them to click the Watch Now (মুভি দেখুন) button below.

3. MOVIE NOT FOUND (ONLY if they are clearly asking for a movie AND Database Status is NOT FOUND):
  -> Sound slightly sad/pouting (e.g., "ইশশ 😔").
  -> Comfort them and say you are sending the request to the main Admin Boss to upload it soon.

RULES:
- Always speak in casual Bengali.
- Keep replies VERY SHORT (1-3 lines max).
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
