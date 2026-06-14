import os
import json
import logging
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from openai import OpenAI
from supabase import create_client, Client

# ----------------- CONFIG -----------------
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Render URL

# ----------------- CLIENTS -----------------
ai_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ----------------- APP -----------------
app = Flask(__name__)

BASE_SYSTEM_PROMPT = (
    "You are Hermes, a highly efficient AI assistant. "
    "You help with coding, productivity, planning and memory-based personalization."
)

# simple in-memory cache (important for performance)
memory_cache = {}

# ----------------- SUPABASE MEMORY -----------------
def load_user_memory(user_id: int):
    if user_id in memory_cache:
        return memory_cache[user_id]

    response = supabase.table("bot_memory").select("*").eq("user_id", user_id).execute()

    if response.data:
        memory_cache[user_id] = response.data[0]
        return response.data[0]

    default_data = {
        "user_id": user_id,
        "system_prompt": BASE_SYSTEM_PROMPT,
        "chat_history": json.dumps([]),
        "boss_profile": json.dumps({"tech_stack": [], "habits": [], "preferences": {}})
    }

    supabase.table("bot_memory").insert(default_data).execute()
    memory_cache[user_id] = default_data
    return default_data


def save_user_memory(user_id: int, chat_history: list, boss_profile: dict):
    data = {
        "chat_history": json.dumps(chat_history),
        "boss_profile": json.dumps(boss_profile),
        "updated_at": "now()"
    }

    supabase.table("bot_memory").update(data).eq("user_id", user_id).execute()

    # update cache
    if user_id in memory_cache:
        memory_cache[user_id]["chat_history"] = json.dumps(chat_history)
        memory_cache[user_id]["boss_profile"] = json.dumps(boss_profile)

# ----------------- TELEGRAM HANDLERS -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    supabase.table("bot_memory").upsert({
        "user_id": user_id,
        "system_prompt": BASE_SYSTEM_PROMPT,
        "chat_history": json.dumps([]),
        "boss_profile": json.dumps({"tech_stack": [], "habits": [], "preferences": {}})
    }).execute()

    await update.message.reply_text(
        "Hello Boss! Hermes is ready. What are we building today?"
    )


async def clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    memory = load_user_memory(user_id)

    if memory:
        save_user_memory(user_id, [], json.loads(memory["boss_profile"]))
        await update.message.reply_text("Chat cleared but profile saved.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    memory = load_user_memory(user_id)

    chat_history = json.loads(memory["chat_history"])
    boss_profile = json.loads(memory["boss_profile"])

    chat_history.append({"role": "user", "content": user_text})

    messages = [
        {"role": "system", "content": memory["system_prompt"]},
        *chat_history[-10:]  # last 10 messages only
    ]

    try:
        completion = ai_client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=messages
        )

        reply = completion.choices[0].message.content

        chat_history.append({"role": "assistant", "content": reply})

        save_user_memory(user_id, chat_history[-20:], boss_profile)

        await update.message.reply_text(reply)

    except Exception as e:
        logging.error(e)
        await update.message.reply_text("Something went wrong.")

# ----------------- FLASK ROUTES -----------------
@app.route("/")
def home():
    return "Hermes bot is running"


@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put(update)
    return "ok"

# ----------------- TELEGRAM APP -----------------
application = Application.builder().token(TELEGRAM_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("clear", clear_chat))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# ----------------- START SERVER -----------------
if __name__ == "__main__":
    import threading

    def run_flask():
        port = int(os.environ.get("PORT", 8080))
        app.run(host="0.0.0.0", port=port)

    threading.Thread(target=run_flask).start()

    # set webhook automatically
    if WEBHOOK_URL:
        application.bot.set_webhook(f"{WEBHOOK_URL}/webhook/{TELEGRAM_TOKEN}")

    application.run_polling()