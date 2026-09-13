import os
import json
import base64
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters

import anthropic
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]  # paste the whole JSON key as one env var
GOOGLE_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]  # the long ID in the sheet's URL

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(GOOGLE_SHEET_ID)

CATEGORIES = ["Fuel/Gas", "Food & Meals", "Tools & Equipment", "Vehicle Maintenance", "Office Supplies", "Miscellaneous"]

EXTRACTION_PROMPT = """You are a professional accountant with 20 years of experience.
Read the expense information (from text and/or a receipt photo) and return ONLY a JSON
object, no other text, with these exact fields:

{
  "date": "YYYY-MM-DD (use today if not stated)",
  "category": "one of: Fuel/Gas, Food & Meals, Tools & Equipment, Vehicle Maintenance, Office Supplies, Miscellaneous",
  "company": "the company or vendor name, e.g. ADNOC",
  "amount": numeric amount only, no currency symbol,
  "comments": "short remark, empty string if none"
}

The message may be in Arabic or English or mixed - understand both."""


def get_month_sheet_name(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%B")  # e.g. "September"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    caption = update.message.caption or update.message.text or ""

    content = [{"type": "text", "text": f"Expense note from owner: {caption}"}]

    if update.message.photo:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        b64_image = base64.b64encode(photo_bytes).decode("utf-8")
        content.insert(0, {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64_image},
        })

    await update.message.reply_text("Got it, reading the details now...")

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=EXTRACTION_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        raw = response.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
    except Exception as e:
        logger.exception("Extraction failed")
        await update.message.reply_text(f"Sorry, I couldn't read that expense clearly. Error: {e}")
        return

    if data.get("category") not in CATEGORIES:
        data["category"] = "Miscellaneous"

    month_name = get_month_sheet_name(data["date"])

    try:
        ws = sheet.worksheet(month_name)
    except gspread.WorksheetNotFound:
        await update.message.reply_text(
            f"No tab found for {month_name} in the sheet yet — please create it first, or tell me to set it up."
        )
        return

    ws.append_row([
        data["date"],
        data["category"],
        data["company"],
        data["amount"],
        data.get("comments", ""),
        "",  # receipt link placeholder — photo storage wired up separately
    ])

    await update.message.reply_text(
        f"Saved ✅\n"
        f"Date: {data['date']}\n"
        f"Category: {data['category']}\n"
        f"Company: {data['company']}\n"
        f"Amount: {data['amount']} AED\n"
        f"Comments: {data.get('comments') or '—'}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! Send me a receipt photo or a text like 'gas 150 AED at ADNOC' and I'll log it for you."
    )


def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, handle_message))
    app.run_polling()


if __name__ == "__main__":
    main()
