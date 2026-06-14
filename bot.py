import os
import json
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
from supabase import create_client, Client

# Enable logging to catch errors on Render
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Retrieve Environment Variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Initialize OpenRouter Client
ai_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# Initialize Supabase Client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Define the base personality of the agent
BASE_SYSTEM_PROMPT = (
    "You are Hermes, a highly efficient, intelligent, and professional AI Assistant. "
    "Your goal is to assist your boss with productivity, brainstorming, coding, and daily routines. "
    "Keep your responses structured and actionable. Adapt to your boss's preferences, tech stack, "
    "and daily habits over time based on the provided profile context."
)

# Helper function to load data from Supabase
def load_user_memory(user_id: int):
    try:
        response = supabase.table("bot_memory").select("*").eq("user_id", user_id).execute()
        if response.data:
            return response.data[0]
        else:
            # Create a new record if user doesn't exist in database
            default_data = {
                "user_id": user_id,
                "system_prompt": BASE_SYSTEM_PROMPT,
                "chat_history": json.dumps([]),
                "boss_profile": json.dumps({"tech_stack": [], "habits": [], "preferences": {}})
            }
            supabase.table("bot_memory").insert(default_data).execute()
            return default_data
    except Exception as e:
        logging.error(f"Error loading memory: {e}")
        return None

# Helper function to save data back to Supabase
def save_user_memory(user_id: int, chat_history: list, boss_profile: dict):
    try:
        supabase.table("bot_memory").update({
            "chat_history": json.dumps(chat_history),
            "boss_profile": json.dumps(boss_profile),
            "updated_at": "now()"
        }).eq("user_id", user_id).execute()
    except Exception as e:
        logging.error(f"Error saving memory: {e}")

# Command handler for /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Reset/Initialize data in Supabase
    supabase.table("bot_memory").upsert({
        "user_id": user_id,
        "system_prompt": BASE_SYSTEM_PROMPT,
        "chat_history": json.dumps([]),
        "boss_profile": json.dumps({"tech_stack": [], "habits": [], "preferences": {}})
    }).execute()
    
    await update.message.reply_text(
        "হ্যালো বস! আমি আপনার মেমোরি-আপগ্রেডেড পার্সোনাল অ্যাসিস্ট্যান্ট Hermes। "
        "এখন থেকে আমাদের সব কথোপকথন এবং আপনার কাজের অভ্যাস আমি মনে রাখব। আজ কী নিয়ে কাজ করছেন?"
    )

# Command handler to clear conversational context but keep profile info
async def clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    memory = load_user_memory(user_id)
    if memory:
        save_user_memory(user_id, [], json.loads(memory["boss_profile"]))
        await update.message.reply_text("চ্যাট হিস্ট্রি পরিষ্কার করা হয়েছে, তবে আপনার প্রোফাইল ও অভ্যাসগুলো আমার মনে আছে, বস!")

# Main message processor
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    
    # Show typing indicator in Telegram
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    # Fetch long-term memory from database
    memory = load_user_memory(user_id)
    if not memory:
        await update.message.reply_text("মেমোরি লোড করতে সমস্যা হয়েছে। দয়া করে আবার চেষ্টা করুন।")
        return

    # Parse JSON strings from database
    chat_history = json.loads(memory["chat_history"])
    boss_profile = json.loads(memory["boss_profile"])
    system_prompt = memory["system_prompt"]
    
    # Construct an enriched system prompt injected with the user's saved profile
    enriched_system_prompt = (
        f"{system_prompt}\n\n"
        f"CURRENT BOSS PROFILE (Use this to adapt your intelligence and responses over time):\n"
        f"- Tech Stack/Projects: {boss_profile.get('tech_stack', [])}\n"
        f"- Core Habits/Routines: {boss_profile.get('habits', [])}\n"
        f"- Direct Preferences: {boss_profile.get('preferences', {})}\n"
        f"If the user shares new habits, stack updates, or preferences, implicitly update your knowledge "
        f"and acknowledge it professionally in your demeanor."
    )
    
    # Prepare messages payload for OpenRouter API
    api_messages = [{"role": "system", "content": enriched_system_prompt}]
    
    # Append past chat history (Keep last 10 messages to optimize context token limit)
    for msg in chat_history[-10:]:
        api_messages.append(msg)
        
    # Append the current incoming message
    api_messages.append({"role": "user", "content": user_text})
    
    try:
        # Request response from OpenRouter Hermes 3
        response = ai_client.chat.completions.create(
            model="nousresearch/hermes-3-llama-3.1-405b:free",
            messages=api_messages
        )
        
        bot_response = response.choices[0].message.content
        
        # Internal Agent Layer: Update the boss profile if Hermes detects new meta-data
        lower_text = user_text.lower()
        if "project" in lower_text or "learning" in lower_text or "using" in lower_text:
            for tech in ["next.js", "react", "python", "javascript", "supabase", "tailwind", "css", "html"]:
                if tech in lower_text and tech not in boss_profile["tech_stack"]:
                    boss_profile["tech_stack"].append(tech)
                    
        if "usually" in lower_text or "every day" in lower_text or "habit" in lower_text or "at night" in lower_text:
            if user_text not in boss_profile["habits"]:
                boss_profile["habits"].append(user_text)

        # Append current exchange to conversational history
        chat_history.append({"role": "user", "content": user_text})
        chat_history.append({"role": "assistant", "content": bot_response})
        
        # Save updated conversation and learned habits back to Supabase
        save_user_memory(user_id, chat_history, boss_profile)
        
        # Send final reply back to user on Telegram
        await update.message.reply_text(bot_response)
        
    except Exception as e:
        logging.error(f"API or processing error: {e}")
        await update.message.reply_text("দুঃখিত বস, রিকোয়েস্ট প্রসেস করতে সাময়িক সমস্যা হচ্ছে।")

def main():
    # Build Telegram bot application
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear", clear_chat))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run the bot server via polling mechanism
    application.run_polling()

if __name__ == '__main__':
    main()