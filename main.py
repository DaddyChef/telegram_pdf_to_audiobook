import os
import asyncio
import logging
import sys
from os import getenv
from dotenv import load_dotenv

import edge_tts
from aiogram import Bot, Dispatcher, Router, html, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile

# Load environment variables
load_dotenv()
TOKEN = getenv("BOT_TOKEN")

# Initialize Dispatcher and Router
dp = Dispatcher()
router = Router()

PREVIEW_VOICES = [
    "en-CA-LiamNeural",
    "en-US-AvaNeural",
    "en-AU-NatashaNeural",
    "en-US-RogerNeural",
    "en-GB-RyanNeural",
    "en-GB-ThomasNeural"
]

@router.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    await message.answer(f"Hello, {html.bold(message.from_user.full_name)}! Use /select_voice to hear examples.")

@router.message(Command("select_voice"))
async def cmd_select_voice(message: types.Message):
    await message.answer("Generating voice previews... please wait a moment. 🎧")

    for voice_id in PREVIEW_VOICES:
        name = voice_id.split("-")[-1].replace("Neural", "")
        text = f"Hello, I'm {name}, I'll read a book for you."
        
        # To prevent file conflicts, we use the message_id in the filename
        filename = f"{voice_id}_{message.message_id}.mp3"

        try:
            communicate = edge_tts.Communicate(text, voice_id)
            await communicate.save(filename)

            audio_file = FSInputFile(filename)
            await message.answer_voice(
                audio_file, 
                caption=f"Voice: {voice_id}\nTo use this, type /set {voice_id}"
            )

            if os.path.exists(filename):
                os.remove(filename)

        except Exception as e:
            logging.error(f"Error generating voice {voice_id}: {e}")
            await message.answer(f"Could not generate preview for {voice_id}")

    await message.answer("Choose your favorite and use /set [name]!")

async def main() -> None:
    # IMPORTANT: Attach the router to the dispatcher
    dp.include_router(router)
    
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    
    print("Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nBot stopped manually. 👋")