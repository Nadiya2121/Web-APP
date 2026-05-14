import aiohttp
import logging
import os

logger = logging.getLogger(__name__)

# আপনার Groq API Key এখানে দিন
GROQ_API_KEY = "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"

# 🛑 অটো-ফিক্স সিস্টেম: যদি একটা মডেল ডাউন থাকে, সে অটোমেটিক পরেরটা ট্রাই করবে!
MODELS_TO_TRY = [
    "llama3-8b-8192",         # ফাস্ট মডেল (প্রথমে ট্রাই করবে)
    "llama3-70b-8192",        # পাওয়ারফুল মডেল (আগেরটা কাজ না করলে এটা করবে)
    "mixtral-8x7b-32768",     # ব্যাকআপ মডেল
    "gemma2-9b-it"            # লাস্ট ব্যাকআপ
]

async def get_smart_reply(user_text: str, user_name: str, db) -> str:
    """
    ইউজারের সাথে মজা করবে, মুভি খুঁজবে এবং নিজে নিজেই API এরর ফিক্স করবে!
    """
    try:
        # ১. আগে ডাটাবেসে চেক করা হচ্ছে মুভিটা আছে কি না
        search_res = await db.movies.find_one({"title": {"$regex": user_text, "$options": "i"}})
        
        db_status = "NOT FOUND"
        if search_res:
            db_status = f"FOUND! Movie Title is: {search_res['title']}"

        # ২. AI এর জন্য মজার পার্সোনালিটি (Prompt)
        system_prompt = f"""
        You are a super fun, witty, and humorous AI Assistant for a Telegram movie bot called 'MovieZone BD'. 
        User's Name: {user_name}
        Database Check: {db_status}
        
        RULES FOR YOU:
        1. Always reply in sweet, fluent, and friendly Bengali (Bangla) language. Use casual words like "ভাই", "বস", "দোস্ত", "প্যারা নাই".
        2. Be VERY humorous! Crack jokes, use lots of emojis, and banter with the user. If they ask "কেমন আছো?", give a funny, dramatic reply before asking them what movie they want.
        3. If Database Check is 'FOUND!', act super excited and hyped! Tell them to grab some popcorn and click the '🎬 Watch Now' button below.
        4. If Database Check is 'NOT FOUND', act dramatically sad (like crying or broken heart), make a small joke to cheer them up, and tell them you have sent the request to the Admin Boss.
        5. Keep replies punchy and short (maximum 3-4 lines). Do NOT write long essays.
        """

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }

        # ৩. 🛑 অটো-ফিক্স লুপ: একটা ফেইল করলে আরেকটা ট্রাই করবে
        for model_name in MODELS_TO_TRY:
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text}
                ],
                "temperature": 0.8, # ক্রিয়েটিভিটি বাড়িয়ে দেওয়া হলো যাতে সুন্দর মজা করতে পারে
                "max_tokens": 200
            }

            try:
                # ৫ সেকেন্ডের বেশি সময় নিলে পরের মডেলে চলে যাবে
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=payload, timeout=5) as response:
                        if response.status == 200:
                            data = await response.json()
                            return data['choices'][0]['message']['content']
                        else:
                            logger.warning(f"Model {model_name} failed. Auto-trying next model...")
                            continue # কাজ না করলে পরের মডেল ট্রাই করবে
            except Exception:
                continue # নেটওয়ার্ক বা টাইমআউট এরর আসলেও পরের মডেলে যাবে

        # যদি কপাল অনেক খারাপ হয় এবং ৪টা মডেলই একসাথে ফেইল মারে, তখন এই লাইন কাজ করবে
        return fallback_reply(user_name, search_res)

    except Exception as e:
        logger.error(f"Assistant Error: {e}")
        return fallback_reply(user_name, search_res)


def fallback_reply(user_name, search_res):
    """যদি কোনো কারণে সব API কাজ না করে, তবে এই অফলাইন ডিফল্ট মেসেজ যাবে"""
    if search_res:
        return f"আরে বস {user_name}! 🔥 আপনি যা খুঁজছেন (<b>{search_res['title']}</b>) তা আমাদের কাছে আছে! 🥳\nনিচের <b>'🎬 Watch Now'</b> বাটনে একটা কোপ দিন আর মুভি দেখা শুরু করেন!"
    else:
        return f"ইশশ {user_name} ভাই 😔, এই মুহূর্তে মুভিটা স্টকে নাই। তবে প্যারা নাই, আপনার রিকোয়েস্ট আমি অ্যাডমিন বসের কাছে পাঠায় দিছি! খুব জলদি পেয়ে যাবেন। 😇"
