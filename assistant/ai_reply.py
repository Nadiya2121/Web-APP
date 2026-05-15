import aiohttp
import logging
import os
import re
import pytz
import random

from datetime import datetime

# ==========================================================
# LOGGING
# ==========================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==========================================================
# API KEYS (MULTIPLE KEYS SUPPORT FOR UNLIMITED FEEL)
# ==========================================================
# এখন আপনি চাইলে কমা (,) দিয়ে একাধিক API Key ব্যবহার করতে পারবেন।
# উদাহরণ: os.getenv("GROQ_API_KEYS") -> "key1,key2,key3"
keys_env = os.getenv("GROQ_API_KEYS", os.getenv("GROQ_API_KEY", ""))
API_KEYS = [k.strip() for k in keys_env.split(",") if k.strip()]

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

        now = datetime.now(pytz.timezone('Asia/Dhaka'))

        current_time = now.strftime("%I:%M %p")
        current_day = now.strftime("%A")

        # ==========================================================
        # SAFE USER TEXT
        # ==========================================================
        safe_text = re.escape(user_text.strip())
        clean_user_text = user_text.strip()

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
        # CHAT HISTORY (Optimized to 4 to save Huge API Tokens)
        # ==========================================================
        chat_history_str = ""

        try:

            history_cursor = (
                db.messages
                .find({"user_id": identifier})
                .sort("_id", -1)
                .limit(4)  # <-- Changed to 4 to save Rate Limits significantly
            )

            history_list = await history_cursor.to_list(length=4)

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
        # INTENT DETECTION (অযথা ডাটাবেজ সার্চ বন্ধ করা)
        # ==========================================================
        casual_words = ["hi", "hello", "হাই", "হ্যালো", "কেমন আছো", "হুম", "না", "হ্যাঁ", "ok", "ওকে", "কি অবস্থা", "কী", "কি"]
        is_casual_chat = clean_user_text.lower() in casual_words or len(clean_user_text) <= 2

        # ==========================================================
        # MOVIE SEARCH (Optimized with Text Search)
        # ==========================================================
        if not is_casual_chat:
            try:

                search_res = await db.movies.find_one({
                    "$text": {
                        "$search": clean_user_text
                    }
                })

            except Exception as search_error:

                logger.error(
                    f"Movie Search Error: {search_error}"
                )
        else:
            logger.info("Casual chat detected. Skipped database movie search.")

        # ==========================================================
        # DYNAMIC MOVIE INSTRUCTION (মিথ্যা বলা থেকে আটকানো ও রিলিজ লজিক)
        # ==========================================================
        if search_res:
            movie_title = search_res['title']
            db_instruction = f"""
Good News: The movie IS FOUND in our database! The exact title is '{movie_title}'.
Your task: Give a tiny, engaging review of this movie and happily tell the user to click the 'Watch Now' button below.
"""
        elif is_casual_chat:
            db_instruction = """
The user is just chatting casually or giving a short reply. Respond to their conversation naturally. Do NOT talk about missing movies unless they specifically asked for one.
"""
        else:
            db_instruction = """
Bad News: The movie is NOT FOUND in our database. 
Your task:
1. First, check your own AI knowledge: Is the user asking for an UPCOMING or UNRELEASED movie/series in the real world?
2. If YES (Unreleased): Playfully tease the user in Bengali. Tell them the movie hasn't even been released yet in theaters or OTT! (Example: "আরে ভাই, এই মুভি তো এখনো রিলিজই হয়নি! আগে রিলিজ তো হতে দিন, তারপর অ্যাডমিন বসকে বলব অ্যাড করে দিতে! 😆")
3. If NO (Already Released but missing in DB): Politely tell them in Bengali that it's not available right now, but you have requested the 'Admin Boss' (এডমিন বস / এডমিন ভাই) to upload it if possible. NEVER ask them to click 'Watch Now'.
"""

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
        # SYSTEM PROMPT (SMART & STRICT)
        # ==========================================================
        system_prompt = f"""
You are Maya, a smart, sweet, logical, and funny Bangladeshi virtual assistant for MovieZone BD.

Current Time: {current_time}
Current Day: {current_day}
User Name: {user_name}

Conversation Memory (Previous Chats):
{chat_history_str}

CRITICAL RULES:
1. CONTEXT IS KING: Read the "Conversation Memory" carefully. If the user is answering a question YOU just asked (like saying "না", "হ্যাঁ", "দেখিনি"), or continuing a story, reply logically to that conversation! DO NOT treat small conversational words as a movie search.
2. Speak naturally in standard conversational Bengali (e.g., ভাইয়া, আরে, ওমা). Keep it short, smart and engaging. NEVER sound robotic.

3. STRICT MOVIE STATUS (MUST FOLLOW):
{db_instruction}

4. LATEST MOVIES SUGGESTION: If a requested movie is released but missing in our DB, you can softly suggest these newly added movies: {latest_movies_str}

5. FOR 18+ / ADULT QUERIES: Playfully roast them (Example: "আস্তাগফিরুল্লাহ! এসব কী ভাই? ভালো হয়ে যান! 😒 আমরা ফ্যামিলি ফ্রেন্ডলি!").
"""

        # ==========================================================
        # API CONFIG
        # ==========================================================
        url = "https://api.groq.com/openai/v1/chat/completions"

        # Randomly select an API key to balance the load and prevent rate limits
        current_api_key = random.choice(API_KEYS) if API_KEYS else ""

        headers = {
            "Authorization": f"Bearer {current_api_key}",
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
                "max_tokens": 1024,
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
                search_res,
                user_text
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
            search_res,
            user_text
        )


# ==========================================================
# FALLBACK REPLY (UPDATED)
# ==========================================================
def fallback_reply(user_name, search_res, user_text=""):

    if search_res:

        return (
            f"আরে {user_name}! 🍿✨\n\n"
            f"{search_res['title']} "
            f"তো আমার কাছেই আছে 😎\n"
            f"নিচের Watch Now বাটনে চাপ দাও!"
        )

    # API Limit শেষ হলে বা সার্ভার ডাউন হলে ফানি রিপ্লাই:
    words = user_text.strip().split()
    if len(words) <= 3 or user_text.strip() in ["না", "হ্যাঁ", "হুম", "ok", "hi", "hello", "হাই", "হ্যালো", "কী"]:
        return (
            f"উফফ {user_name} ভাইয়া! এত মানুষ একসাথে মেসেজ দিচ্ছে যে আমার মাথা ঘুরছে! 😵‍💫 "
            f"একটু আমাকে রেস্ট দাও, ১ মিনিট পর আবার মেসেজ দাও তো প্লিজ! 🥺"
        )

    # বড় মুভির নাম সার্চ দিলে এবং API লিমিট শেষ থাকলে:
    return (
        f"ইশশ {user_name} 😔💔\n\n"
        f"এই মুভিটা এখনো পাই নাই...\n"
        f"তবে আমি এডমিন ভাই/বসকে বলে দিয়েছি, সম্ভব হলে তাড়াতাড়ি অ্যাড করে দিবে! 🚀"
    )
