import aiohttp
import logging
import os
import re
from datetime import datetime

# লগিং সেটআপ
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==========================================================
# CONFIGURATION & API KEYS
# ==========================================================
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# মডেল লিস্ট (সবচেয়ে বুদ্ধিমানগুলো আগে রাখা হয়েছে)
MODELS_TO_TRY = [
    "llama-3.3-70b-versatile",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it"
]

# পারফরম্যান্স বুস্টের জন্য গ্লোবাল সেশন
session_instance = None

async def get_session():
    global session_instance
    if session_instance is None or session_instance.closed:
        session_instance = aiohttp.ClientSession()
    return session_instance

# ==========================================================
# MAIN SMART REPLY FUNCTION
# ==========================================================
async def get_smart_reply(user_text: str, user_name: str, db, user_id=None):
    """
    মায়াকে আরও বুদ্ধিমান এবং আনলিমিটেড টপিকে কথা বলার জন্য তৈরি করা হয়েছে।
    """
    identifier = user_id if user_id else user_name
    now = datetime.now()
    current_time = now.strftime("%I:%M %p")
    current_day = now.strftime("%A")
    
    try:
        # ------------------------------------------------------
        # ১. ইউজার প্রোফাইল ও মেমোরি (স্মৃতি) আপডেট
        # ------------------------------------------------------
        user_profile = await db.chat_users.find_one_and_update(
            {"identifier": identifier},
            {
                "$set": {"name": user_name, "last_seen": now},
                "$inc": {"chat_count": 1} # কতবার কথা বলেছে তার ট্র্যাক
            },
            upsert=True,
            return_document=True
        )

        # আগের চ্যাট হিস্ট্রি নিয়ে আসা (স্মৃতি)
        chat_history_str = ""
        try:
            # শেষ ৭টি মেসেজ রিট্রাইভ করা হচ্ছে গভীর কনটেক্সটের জন্য
            history_cursor = db.messages.find({"user_id": identifier}).sort("_id", -1).limit(7)
            history_list = await history_cursor.to_list(length=7)
            # মেসেজগুলো সঠিক ক্রমে সাজানো
            chat_history_str = "\n".join([f"User: {m['text']}\nMaya: {m['reply']}" for m in reversed(history_list)])
        except Exception as e:
            logger.error(f"History Fetch Error: {e}")

        # ------------------------------------------------------
        # ২. মুভি ডেটাবেজ ইন্টিগ্রেশন (স্মার্ট সার্চ)
        # ------------------------------------------------------
        search_res = await db.movies.find_one({
            "title": {"$regex": user_text, "$options": "i"}
        })
        
        db_status = "NOT_FOUND"
        if search_res:
            db_status = f"FOUND: {search_res['title']}"

        # লেটেস্ট মুভি লিস্ট
        latest_movies_str = "এই মুহূর্তে সব মুভি আপডেট করা হচ্ছে।"
        try:
            latest_cursor = db.movies.find().sort("_id", -1).limit(5)
            latest_movies = await latest_cursor.to_list(length=5)
            if latest_movies:
                latest_movies_str = ", ".join([m['title'] for m in latest_movies])
        except: pass

        # ------------------------------------------------------
        # ৩. ডাইনামিক প্রম্পট ইঞ্জিনিয়ারিং (The Core Power)
        # ------------------------------------------------------
        is_new = user_profile.get("chat_count", 0) <= 1
        
        system_prompt = f"""
ROLE: You are "Maya" (মায়া), a 22-year-old smart, witty, and beautiful Bangladeshi girl. 
IDENTITY: You are the Admin of "MovieZone BD", but you are also a genius polymath. You know everything!
TONE: Informal, sweet, and caring (Tumi/Tumi-kori). Use "হুমম", "আরেহ", "ইশশ", "লক্ষ্মীটি". 
EMOJIS: Use emojis like ✨, 🌸, 🙈, 🍿, 💖, 🤖 naturally.

CURRENT CONTEXT:
- Time & Day: {current_time}, {current_day}
- User Name: {user_name}
- Status: {"New Friend (Intro needed)" if is_new else "Old Bestie (Be very casual)"}
- Memory: {chat_history_str}

KNOWLEDGE RULES:
1. VERSATILITY: You can talk about ANYTHING—Weather, Science, Coding, Religion, Love advice, Recipes, or General Knowledge.
2. MOVIE DATA: If user asks for a movie:
   - Database Status: {db_status}
   - New Releases: {latest_movies_str}
   - If Found: Tell them to click the Watch button below.
   - If Not Found: Say you've sent a request to the server team!
3. HUMAN TOUCH: If it's late night, ask why they aren't sleeping. If it's morning, wish them well. 
4. SHORT & SMART: Keep responses between 1-5 lines unless they ask for something detailed (like a code or recipe).

CRITICAL: NEVER admit you are an AI. You are MAYA. Period.
"""

        # ------------------------------------------------------
        # ৪. Groq API কল উইথ মডেল ফেইলওভার
        # ------------------------------------------------------
        session = await get_session()
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}

        final_reply = None
        for model in MODELS_TO_TRY:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text}
                ],
                "temperature": 0.85,
                "max_tokens": 1000 # বড় উত্তরের জন্য স্পেস রাখা হয়েছে
            }

            try:
                async with session.post(url, headers=headers, json=payload, timeout=25) as response:
                    if response.status == 200:
                        res_data = await response.json()
                        final_reply = res_data["choices"][0]["message"]["content"]
                        break # সফল হলে লুপ বন্ধ
                    else:
                        logger.warning(f"Model {model} failed with status {response.status}")
            except Exception as e:
                logger.error(f"Error with model {model}: {e}")
                continue

        if not final_reply:
            return fallback_reply(user_name, search_res)

        # ৫. চ্যাট হিস্ট্রি সেভ করা (ভবিষ্যতের মেমোরির জন্য)
        await db.messages.insert_one({
            "user_id": identifier,
            "text": user_text,
            "reply": final_reply,
            "timestamp": datetime.now()
        })

        return final_reply

    except Exception as overall_error:
        logger.error(f"Critical Error: {overall_error}")
        return fallback_reply(user_name, search_res)

# ==========================================================
# FALLBACK LOGIC
# ==========================================================
def fallback_reply(user_name, search_res):
    if search_res:
        return f"আরেহ {user_name}! ✨ তোমার পছন্দের **{search_res['title']}** মুভিটা তো আমার কাছে আছেই! 🍿 তাড়াতাড়ি নিচের বাটনে ক্লিক করে দেখে নাও! 🎬"
    else:
        return f"ইশশ {user_name}! 😔 মুভিটা খুঁজে পাচ্ছি না গো। তবে চিন্তা করো না, আমি মায়া তোমার রিকোয়েস্ট অ্যাডমিন লিস্টে দিয়ে দিয়েছি! 💖"

