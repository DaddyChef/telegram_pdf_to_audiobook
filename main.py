import os
import asyncio
import logging
import sys
from os import getenv
from dotenv import load_dotenv

import edge_tts
from aiogram import Bot, Dispatcher, Router, html, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- CONFIGURATION ---
load_dotenv()
TOKEN = getenv("BOT_TOKEN")

TEMP_DIR = "temp"
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

dp = Dispatcher()
router = Router()

# Data structure for voices
LANGUAGES = {
    "Ukrainian": ["uk-UA-OstapNeural", "uk-UA-PolinaNeural"],
    "English": ["en-CA-LiamNeural", "en-US-RogerNeural", "en-GB-SoniaNeural"],
    "Italian": ["it-IT-DiegoNeural", "it-IT-ElsaNeural", "it-IT-IsabellaNeural"]
}

# Storage for 1,000+ users (In-memory for now)
USER_SETTINGS = {}

# --- HANDLERS ---

@router.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    await message.answer(
        f"👋 Hello, {html.bold(message.from_user.full_name)}!\n\n"
        f"I can convert your PDF files into audiobooks.\n"
        f"1️⃣ Use /select_language to pick a voice.\n"
        f"2️⃣ Send me a PDF file."
    )

@router.message(Command("select_language"))
async def cmd_select_language(message: Message):
    builder = InlineKeyboardBuilder()
    for lang_name in LANGUAGES.keys():
        builder.button(text=lang_name, callback_data=f"lang:{lang_name}")
    builder.adjust(1)
    await message.answer("<b>🌍 Select a language:</b>", reply_markup=builder.as_markup())

@router.callback_query(F.data == "back_to_langs")
@router.callback_query(F.data.startswith("lang:"))
async def callbacks_show_voices(callback: CallbackQuery):
    # If 'Back' is pressed, refresh the language list
    if callback.data == "back_to_langs":
        builder = InlineKeyboardBuilder()
        for lang_name in LANGUAGES.keys():
            builder.button(text=lang_name, callback_data=f"lang:{lang_name}")
        builder.adjust(1)
        
        try:
            # Edit current message if it's text
            await callback.message.edit_text("<b>🌍 Select a language:</b>", reply_markup=builder.as_markup())
        except Exception:
            # If current message is a Voice message, delete it and send fresh text
            await callback.message.delete()
            await callback.message.answer("<b>🌍 Select a language:</b>", reply_markup=builder.as_markup())
        
        await callback.answer()
        return

    # Show voices for selected language
    lang_selected = callback.data.split(":")[1]
    voices = LANGUAGES.get(lang_selected, [])
    
    builder = InlineKeyboardBuilder()
    for v_id in voices:
        parts = v_id.split("-")
        builder.button(text=f"{parts[-1].replace('Neural', '')} {parts[1]}", callback_data=f"preview:{v_id}")
    
    builder.row(types.InlineKeyboardButton(text="⬅️ Back to Languages", callback_data="back_to_langs"))
    builder.adjust(2)
    
    await callback.message.edit_text(
        f"Displaying voices for <b>{lang_selected}</b>:", 
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data.startswith("preview:"))
async def preview_voice(callback: CallbackQuery):
    await callback.answer()
    voice_id = callback.data.split(":")[1]
    name = voice_id.split("-")[-1].replace("Neural", "")
    lang_code = voice_id.split("-")[0].lower()

    # Fully localized greetings
    greetings = {
        "uk": f"Вітаю, я {name}. Я прочитаю вашу книгу для вас!",
        "it": f"Ciao, sono {name}. Leggerò il tuo libro per te!",
        "en": f"Hello, I am {name}. I will read a book for you!"
    }
    
    # Get the translation or default to English if the language isn't found
    text = greetings.get(lang_code, f"Hello, I am {name}. I will read a book for you!")
    
    filename = os.path.join(TEMP_DIR, f"prev_{callback.from_user.id}.mp3")
    status = await callback.message.answer("⏳ <i>Generating sample...</i>")

    try:
        communicate = edge_tts.Communicate(text, voice_id)
        await communicate.save(filename)
        
        builder = InlineKeyboardBuilder()
        builder.button(text=f"✅ Use {name}", callback_data=f"confirm:{voice_id}")
        builder.button(text="🔄 Use another voice", callback_data="back_to_langs")
        builder.adjust(1) 
        
        # Cleanup the menu to show the Audio Decision
        await callback.message.delete()
        await status.delete()

        audio = FSInputFile(filename)
        await callback.message.answer_voice(
            audio, 
            caption=f"<b>Listen to {name}:</b>\nKeep this voice or try another?",
            reply_markup=builder.as_markup()
        )
    except Exception as e:
        logging.error(f"Preview error: {e}")
        await callback.message.answer("❌ Error generating preview.")
    finally:
        if os.path.exists(filename):
            os.remove(filename)

@router.callback_query(F.data.startswith("confirm:"))
async def confirm_voice(callback: CallbackQuery):
    voice_id = callback.data.split(":")[1]
    USER_SETTINGS[callback.from_user.id] = voice_id
    
    # Edit caption and remove buttons
    await callback.message.edit_caption(
        caption=f"🎯 <b>Voice Confirmed:</b> <code>{voice_id}</code>\n\n"
                f"🚀 <b>You can upload your PDF file now!</b>\n"
                f"<i>Simply attach the file and send it to this chat.</i>",
        reply_markup=None 
    )
    await callback.answer("Voice saved! ✅")

@router.message(F.document.mime_type == "application/pdf")
async def handle_pdf(message: Message, bot: Bot):
    user_id = message.from_user.id
    
    # --- ADDED: SIZE CHECK ---
    # 20 MB in bytes = 20 * 1024 * 1024
    MAX_FILE_SIZE = 50 * 1024 * 1024 
    
    if message.document.file_size > MAX_FILE_SIZE:
        await message.reply(
            "⚠️ <b>File too large!</b>\n"
            "Telegram limits bot downloads to <b>20 MB</b>. "
            "Please send a smaller PDF or compress your file."
        )
        return
    if user_id not in USER_SETTINGS:
        await message.reply("⚠️ <b>Please select a voice first!</b>\nUse /select_language to choose your narrator.")
        return

    file_name = message.document.file_name
    file_size_kb = message.document.file_size / 1024
    size_str = f"{file_size_kb:.1f} KB" if file_size_kb < 1000 else f"{file_size_kb/1024:.2f} MB"

    status_msg = await message.answer(
        f"📥 <b>File Received!</b>\n━━━━━━━━━━━━━━\n"
        f"📄 <b>Name:</b> <code>{file_name}</code>\n"
        f"⏳ <b>Status:</b> 📥 <i>Downloading...</i>"
    )

    try:
        file = await bot.get_file(message.document.file_id)
        local_path = os.path.join(TEMP_DIR, f"{user_id}_{file_name}")
        await bot.download_file(file.file_path, local_path)

        await status_msg.edit_text(
            f"🚀 <b>Generation Started!</b>\n━━━━━━━━━━━━━━\n"
            f"🎙 <b>Voice:</b> <code>{USER_SETTINGS[user_id]}</code>\n"
            f"🔊 <b>Status:</b> <i>Preparing text conversion...</i>"
        )
        
    except Exception as e:
        logging.error(f"PDF error: {e}")
        await status_msg.edit_text("❌ <b>Error:</b> Something went wrong during the upload.")

# --- MAIN ---

async def main() -> None:
    dp.include_router(router)
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.delete_webhook(drop_pending_updates=True)
    print("Bot is online and ready for users!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nBot stopped. 👋")