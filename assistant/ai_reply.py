import aiohttp
import logging
import os
import re

from datetime import datetime

# ==========================================================
# LOGGING
# ==========================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==========================================================
# API KEY
# ==========================================================
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# ==========================================================
# MODELS
# ==========================================================
MODELS_TO_TRY = [
    "llama-3.3-70b-versatile",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it"
]

# ==========================================================
# GLOBAL SESSION
# ==========================================================
session_instance = None


async def get_session():
    global session_instance

    if session_instance is None or session_instance.closed:
        timeout = aiohttp.ClientTimeout(total=30)

        session_instance = aiohttp.ClientSession(
            timeout=timeout
        )

    return session_instance


# ==========================================================
# MAIN FUNCTION
# ==========================================================
async def get_smart_reply(
    user_text: str,
    user_name: str,
    db,
    user_id=None
):

    search_res = None

    try:

        identifier = str(user_id) if user_id else user_name

        now = datetime.now()

        current_time = now.strftime("%I:%M %p")
        current_day = now.strftime("%A")

        # ==========================================================
        # SAFE USER TEXT
        # ==========================================================
        safe_text = re.escape(user_text.strip())

        # ==========================================================
        # USER PROFILE
        # ==========================================================
        user_profile = await db.chat_users.find_one_and_update(
            {
                "identifier": identifier
            },
            {
                "$set": {
                    "name": user_name,
                    "last_seen": now
                },
                "$inc": {
                    "chat_count": 1
                }
            },
            upsert=True,
            return_document=True
        )

        # ==========================================================
        # CHAT HISTORY
        # ==========================================================
        chat_history_str = ""

        try:

            history_cursor = (
                db.messages
                .find({"user_id": identifier})
                .sort("_id", -1)
                .limit(6)
            )

            history_list = await history_cursor.to_list(length=6)

            history_list.reverse()

            history_texts = []

            for item in history_list:

                user_msg = item.get("text", "")
                bot_reply = item.get("reply", "")

                history_texts.append(
                    f"User: {user_msg}\nMaya: {bot_reply}"
                )

            chat_history_str = "\n".join(history_texts)

        except Exception as history_error:

            logger.error(
                f"History Error: {history_error}"
            )

        # ==========================================================
        # MOVIE SEARCH
        # ==========================================================
        try:

            search_res = await db.movies.find_one({
                "title": {
                    "$regex": safe_text,
                    "$options": "i"
                }
            })

        except Exception as search_error:

            logger.error(
                f"Movie Search Error: {search_error}"
            )

        db_status = "NOT_FOUND"

        if search_res:
            db_status = f"FOUND: {search_res['title']}"

        # ==========================================================
        # LATEST MOVIES
        # ==========================================================
        latest_movies_str = ""

        try:

            latest_cursor = (
                db.movies
                .find()
                .sort("_id", -1)
                .limit(5)
            )

            latest_movies = await latest_cursor.to_list(length=5)

            if latest_movies:

                latest_movies_str = ", ".join([
                    movie.get("title", "")
                    for movie in latest_movies
                ])

        except:
            pass

        # ==========================================================
        # USER STATUS
        # ==========================================================
        chat_count = user_profile.get("chat_count", 1)

        is_new_user = chat_count <= 1

        # ==========================================================
        # SYSTEM PROMPT
        # ==========================================================
        system_prompt = f"""
You are Maya, a friendly and funny Bangladeshi assistant from MovieZone BD.

Current Time: {current_time}
Current Day: {current_day}

User Name: {user_name}

User Type:
{"New User" if is_new_user else "Regular User"}

Conversation Memory:
{chat_history_str}

Movie Database:
{db_status}

Latest Movies:
{latest_movies_str}

RULES:

1. Speak naturally in Bengali.
2. Be funny, sweet and human-like.
3. Keep replies short and smart.
4. Use emojis naturally.
5. You can talk about movies, life, fun, coding, love, games and general topics.
6. If movie exists:
   - Get excited
   - Tell user to click Watch Now
7. If movie doesn't exist:
   - Say request sent
8. Do NOT ask about movies in every reply.
9. Avoid repeating same phrases.
10. Stay casual and natural.
"""

        # ==========================================================
        # API CONFIG
        # ==========================================================
        url = "https://api.groq.com/openai/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }

        session = await get_session()

        final_reply = None

        # ==========================================================
        # MODEL FAILOVER
        # ==========================================================
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
                "max_tokens": 250,
                "user": identifier
            }

            try:

                async with session.post(
                    url,
                    headers=headers,
                    json=payload
                ) as response:

                    if response.status == 200:

                        data = await response.json()

                        final_reply = (
                            data["choices"][0]
                            ["message"]
                            ["content"]
                        )

                        break

                    else:

                        error_text = await response.text()

                        logger.warning(
                            f"{model_name} failed | "
                            f"{response.status} | "
                            f"{error_text}"
                        )

            except Exception as model_error:

                logger.error(
                    f"Model Error {model_name}: "
                    f"{model_error}"
                )

                continue

        # ==========================================================
        # FALLBACK
        # ==========================================================
        if not final_reply:

            return fallback_reply(
                user_name,
                search_res
            )

        # ==========================================================
        # CLEANUP RESPONSE
        # ==========================================================
        final_reply = (
            final_reply
            .replace("**", "")
            .replace("__", "")
            .replace("###", "")
            .strip()
        )

        # ==========================================================
        # SAVE CHAT MEMORY
        # ==========================================================
        try:

            await db.messages.insert_one({
                "user_id": identifier,
                "text": user_text,
                "reply": final_reply,
                "timestamp": now
            })

            # ==========================================================
            # KEEP ONLY LAST 20 MESSAGES
            # ==========================================================
            old_messages = (
                db.messages
                .find({"user_id": identifier})
                .sort("_id", -1)
            )

            old_messages = await old_messages.to_list(length=100)

            if len(old_messages) > 20:

                ids_to_delete = [
                    msg["_id"]
                    for msg in old_messages[20:]
                ]

                await db.messages.delete_many({
                    "_id": {
                        "$in": ids_to_delete
                    }
                })

        except Exception as save_error:

            logger.error(
                f"Memory Save Error: {save_error}"
            )

        return final_reply

    except Exception as overall_error:

        logger.error(
            f"Critical Error: {overall_error}"
        )

        return fallback_reply(
            user_name,
            search_res
        )


# ==========================================================
# FALLBACK REPLY
# ==========================================================
def fallback_reply(user_name, search_res):

    if search_res:

        return (
            f"আরে {user_name}! 🍿✨\n\n"
            f"{search_res['title']} "
            f"তো আমার কাছেই আছে 😎\n"
            f"নিচের Watch Now বাটনে চাপ দাও!"
        )

    return (
        f"ইশশ {user_name} 😔💔\n\n"
        f"এই মুভিটা এখনো পাই নাই...\n"
        f"তবে তোমার রিকোয়েস্ট "
        f"সার্ভার টিমের কাছে পাঠিয়ে দিলাম 🚀"
    )
