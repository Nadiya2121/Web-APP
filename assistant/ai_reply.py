import aiohttp
import logging

logger = logging.getLogger(__name__)

# Groq API Key (ফ্রিতে নিতে পারবেন: console.groq.com থেকে)
GROQ_API_KEY = "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"

async def get_smart_reply(user_text: str, user_name: str, db) -> str:
    """
    ডাটাবেস চেক করে এবং Groq API (Llama-3) ব্যবহার করে স্মার্ট রিপ্লাই জেনারেট করবে।
    """
    try:
        # ১. আগে ডাটাবেসে চেক করা হচ্ছে মুভিটা আছে কি না
        search_res = await db.movies.find_one({"title": {"$regex": user_text, "$options": "i"}})
        
        db_status = "The requested movie was NOT found in the database."
        if search_res:
            db_status = f"GOOD NEWS! The movie '{search_res['title']}' IS AVAILABLE in our database."

        # ২. AI এর জন্য কড়া নির্দেশ (Prompt)
        system_prompt = f"""
        You are 'MovieZone BD Assistant', a smart AI bot for a Telegram movie channel.
        Your job is to talk to users ONLY about movies, series, anime, and bot features.
        
        User's Name: {user_name}
        Database Status: {db_status}
        
        STRICT RULES:
        1. Always reply in sweet and friendly Bengali (Bangla) language.
        2. Keep replies short (maximum 2-3 sentences).
        3. If the user talks about anything other than movies (politics, personal questions, math, slang), politely say you only discuss movies.
        4. If the 'Database Status' says AVAILABLE, tell the user happily that the movie is available and ask them to click the '🎬 Watch Now' button below.
        5. If NOT AVAILABLE, tell them nicely that it's not here right now, but you have forwarded their request to the Admin.
        """

        # ৩. Groq API এ রিকোয়েস্ট পাঠানো
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "llama3-70b-8192", # মেটার সবচেয়ে পাওয়ারফুল ফ্রি মডেল
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text}
            ],
            "temperature": 0.6,
            "max_tokens": 150
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['choices'][0]['message']['content']
                else:
                    logger.error(f"Groq API Error: {await response.text()}")
                    return fallback_reply(search_res)

    except Exception as e:
        logger.error(f"Assistant Error: {e}")
        return fallback_reply(search_res)

def fallback_reply(search_res):
    """যদি কোনো কারণে API কাজ না করে, তবে এই ডিফল্ট মেসেজ যাবে"""
    if search_res:
        return f"খুশির খবর! আপনি যা খুঁজছেন (<b>{search_res['title']}</b>) তা আমাদের কাছে আছে! 🥳\nনিচের <b>'🎬 Watch Now'</b> বাটনে ক্লিক করে এখনই মুভিটি উপভোগ করুন।"
    else:
        return "দুঃখিত 😔, এই মুহূর্তে মুভিটি ডাটাবেসে পাওয়া যায়নি। তবে আমি আপনার রিকোয়েস্ট অ্যাডমিন ভাইয়াকে পাঠিয়ে দিয়েছি! 😇"
