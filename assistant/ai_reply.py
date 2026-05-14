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
        # AI PERSONALITY
        # ==============================
        system_prompt = f"""
You are MovieZone BD AI Assistant.

User Name: {user_name}
Database Status: {db_status}

RULES:

1. Always reply in friendly Bengali language.
2. Be funny, smart, emotional and entertaining.
3. Use emojis naturally.
4. Keep replies SHORT (2-4 lines max).
5. If movie found:
   - Get excited
   - Mention movie found
   - Tell user to click Watch Now button
6. If movie not found:
   - Sound slightly sad
   - Tell user request sent to admin
7. If user casually chats:
   - Reply humorously
   - Then ask what movie they want
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
                    "temperature": 0.8,
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
            f"আরে {user_name} ভাই 🔥\n\n"
            f"আপনার মুভি "
            f"<b>{search_res['title']}</b> "
            f"আমাদের কাছে available আছে 😎🍿\n"
            f"নিচের 🎬 Watch Now বাটনে চাপ দিন!"
        )

    else:

        return (
            f"ইশশ {user_name} ভাই 😔\n\n"
            f"এই মুভিটা এখনো স্টকে আসে নাই 💔\n"
            f"তবে প্যারা নাই 😎\n"
            f"আপনার রিকোয়েস্ট Admin Boss এর কাছে পাঠিয়ে দেওয়া হয়েছে 🚀"
        )
