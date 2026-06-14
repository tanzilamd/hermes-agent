import os
import json
import logging
from threading import Thread
from flask import Flask
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

# Create a dummy web server to satisfy Render's port check for Free Web Services
app = Flask('')

@app.route('/')
def home():
    return "Hermes Agent is alive and running!"

def run_web_server():
    # Render automatically provides a PORT environment variable
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

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
        "Hello Boss! I am Hermes, your memory-upgraded personal AI assistant. "
        "From now on, I will securely remember our conversations, your projects, and daily routines. "
        "What are we working on today?"
    )

# Command handler to clear conversational context but keep profile info
async def clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    memory = load_user_memory(user_id)
    if memory:
        save_user_memory(user_id, [], json.loads(memory["boss_profile"]))
        await update.message.reply_text(
            "Chat history has been cleared, Boss! However, I still retain your profile details and habits."
        )

# Main message processor
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    
    # Show typing indicator in Telegram
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    # Fetch long-term memory from