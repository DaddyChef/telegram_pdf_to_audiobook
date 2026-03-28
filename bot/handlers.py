"""Telegram bot handlers for the PDF-to-Audiobook bot."""

import asyncio
import logging
import os
import re

import edge_tts
from aiogram import Bot, Router, html, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, CallbackQuery

from config import (
    TEMP_DIR,
    LANGUAGES,
    GREETINGS,
    MAX_FILE_SIZE,
    FALLBACK_PAGE_CHUNK,
    MAX_UPLOAD_SIZE,
)
from bot.keyboards import (
    language_keyboard,
    voice_keyboard,
    preview_confirm_keyboard,
)
from pdf.extractor import extract_pages
from pdf.chapters import detect_chapters, format_filename, Chapter
from tts.engine import synthesize_chapter

logger = logging.getLogger(__name__)

router = Router()

# In-memory user settings and processing queue
USER_SETTINGS: dict[int, str] = {}

# Simple asyncio queue — one conversion at a time
_conversion_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------


@router.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    await message.answer(
        f"Hello, {html.bold(message.from_user.full_name)}!\n\n"
        f"I can convert your PDF files into audiobooks.\n"
        f"1. Use /select_language to pick a voice.\n"
        f"2. Send me a PDF file."
    )


# ---------------------------------------------------------------------------
# Voice selection flow
# ---------------------------------------------------------------------------


@router.message(Command("select_language"))
async def cmd_select_language(message: Message) -> None:
    kb = language_keyboard()
    await message.answer(
        "<b>Select a language:</b>", reply_markup=kb.as_markup()
    )


@router.callback_query(F.data == "back_to_langs")
@router.callback_query(F.data.startswith("lang:"))
async def callbacks_show_voices(callback: CallbackQuery) -> None:
    if callback.data == "back_to_langs":
        kb = language_keyboard()
        try:
            await callback.message.edit_text(
                "<b>Select a language:</b>", reply_markup=kb.as_markup()
            )
        except Exception:
            await callback.message.delete()
            await callback.message.answer(
                "<b>Select a language:</b>", reply_markup=kb.as_markup()
            )
        await callback.answer()
        return

    lang_selected = callback.data.split(":")[1]
    kb = voice_keyboard(lang_selected)
    await callback.message.edit_text(
        f"Displaying voices for <b>{lang_selected}</b>:",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("preview:"))
async def preview_voice(callback: CallbackQuery) -> None:
    await callback.answer()
    voice_id = callback.data.split(":")[1]
    name = voice_id.split("-")[-1].replace("Neural", "")
    lang_code = voice_id.split("-")[0].lower()

    text = GREETINGS.get(lang_code, GREETINGS["en"]).format(name=name)

    filename = os.path.join(TEMP_DIR, f"prev_{callback.from_user.id}.mp3")
    status = await callback.message.answer("<i>Generating sample...</i>")

    try:
        communicate = edge_tts.Communicate(text, voice_id)
        await communicate.save(filename)

        kb = preview_confirm_keyboard(voice_id)

        await callback.message.delete()
        await status.delete()

        audio = FSInputFile(filename)
        await callback.message.answer_voice(
            audio,
            caption=f"<b>Listen to {name}:</b>\nKeep this voice or try another?",
            reply_markup=kb.as_markup(),
        )
    except Exception as e:
        logger.error("Preview error: %s", e)
        await callback.message.answer("Error generating preview.")
    finally:
        if os.path.exists(filename):
            os.remove(filename)


@router.callback_query(F.data.startswith("confirm:"))
async def confirm_voice(callback: CallbackQuery) -> None:
    voice_id = callback.data.split(":")[1]
    USER_SETTINGS[callback.from_user.id] = voice_id

    await callback.message.edit_caption(
        caption=(
            f"<b>Voice Confirmed:</b> <code>{voice_id}</code>\n\n"
            f"<b>You can upload your PDF file now!</b>\n"
            f"<i>Simply attach the file and send it to this chat.</i>"
        ),
        reply_markup=None,
    )
    await callback.answer("Voice saved!")


# ---------------------------------------------------------------------------
# PDF handling — full conversion pipeline
# ---------------------------------------------------------------------------


def _derive_book_name(file_name: str) -> str:
    """Derive a clean book name from the PDF filename."""
    name = os.path.splitext(file_name)[0]
    # Remove common prefixes like user_id_
    name = re.sub(r"^\d+_", "", name)
    # Replace underscores / hyphens with spaces
    name = name.replace("_", " ").replace("-", " ")
    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name).strip()
    return name if name else "Audiobook"


@router.message(F.document.mime_type == "application/pdf")
async def handle_pdf(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id

    if message.document.file_size > MAX_FILE_SIZE:
        await message.reply(
            "<b>File too large!</b>\n"
            "Telegram limits bot downloads to <b>20 MB</b>. "
            "Please send a smaller PDF or compress your file."
        )
        return

    if user_id not in USER_SETTINGS:
        await message.reply(
            "<b>Please select a voice first!</b>\n"
            "Use /select_language to choose your narrator."
        )
        return

    voice_id = USER_SETTINGS[user_id]
    file_name = message.document.file_name or "document.pdf"
    book_name = _derive_book_name(file_name)

    status_msg = await message.answer(
        f"<b>File Received!</b>\n"
        f"<b>Name:</b> <code>{file_name}</code>\n"
        f"<b>Status:</b> <i>Waiting in queue...</i>"
    )

    async with _conversion_lock:
        pdf_path = os.path.join(TEMP_DIR, f"{user_id}_{file_name}")
        audio_files: list[str] = []

        try:
            # --- Step 1: Download ---
            await status_msg.edit_text(
                f"<b>Processing:</b> <code>{file_name}</code>\n"
                f"<b>Status:</b> <i>Downloading PDF...</i>"
            )
            file = await bot.get_file(message.document.file_id)
            await bot.download_file(file.file_path, pdf_path)

            # --- Step 2: Extract & clean text ---
            await status_msg.edit_text(
                f"<b>Processing:</b> <code>{file_name}</code>\n"
                f"<b>Status:</b> <i>Extracting text & cleaning headers/footers...</i>"
            )
            pages = extract_pages(pdf_path)

            content_pages = [p for p in pages if not p.is_blank]
            if not content_pages:
                await status_msg.edit_text(
                    "<b>Error:</b> No readable text found in this PDF. "
                    "It may be a scanned/image-based PDF."
                )
                return

            # --- Step 3: Detect chapters ---
            await status_msg.edit_text(
                f"<b>Processing:</b> <code>{file_name}</code>\n"
                f"<b>Status:</b> <i>Detecting chapters...</i>"
            )
            chapters, chapters_found = detect_chapters(pdf_path, pages)

            if not chapters:
                await status_msg.edit_text(
                    "<b>Error:</b> Could not extract any content from this PDF."
                )
                return

            total_chapters = len(chapters)
            mode = "chapters" if chapters_found else f"{FALLBACK_PAGE_CHUNK}-page chunks"

            await status_msg.edit_text(
                f"<b>Processing:</b> <code>{file_name}</code>\n"
                f"<b>Detected:</b> {total_chapters} section(s) ({mode})\n"
                f"<b>Status:</b> <i>Starting audio conversion...</i>"
            )

            # --- Step 4: TTS conversion ---
            for idx, chapter in enumerate(chapters, start=1):
                label = format_filename(book_name, chapter, idx, chapters_found)

                await status_msg.edit_text(
                    f"<b>Processing:</b> <code>{file_name}</code>\n"
                    f"<b>Converting:</b> {idx}/{total_chapters} — {label}\n"
                    f"<b>Status:</b> <i>Generating audio...</i>"
                )

                safe_label = re.sub(r'[<>:"/\\|?*]', "_", label)
                audio_path = os.path.join(TEMP_DIR, f"{user_id}_{safe_label}.mp3")

                await synthesize_chapter(
                    text=chapter.text,
                    voice_id=voice_id,
                    output_path=audio_path,
                )
                audio_files.append(audio_path)

                # --- Step 5: Send audio ---
                file_size = os.path.getsize(audio_path)
                if file_size > MAX_UPLOAD_SIZE:
                    await message.answer(
                        f"<b>Warning:</b> <code>{label}</code> exceeds 50 MB "
                        f"and cannot be sent via Telegram. Skipping."
                    )
                    continue

                if file_size == 0:
                    await message.answer(
                        f"<b>Warning:</b> <code>{label}</code> produced empty audio. Skipping."
                    )
                    continue

                audio_file = FSInputFile(audio_path, filename=f"{label}.mp3")
                await message.answer_audio(
                    audio_file,
                    title=label,
                    performer=book_name,
                )

            # --- Done ---
            await status_msg.edit_text(
                f"<b>Audiobook Complete!</b>\n"
                f"<b>Book:</b> <code>{book_name}</code>\n"
                f"<b>Sections:</b> {total_chapters}\n"
                f"<b>Voice:</b> <code>{voice_id}</code>"
            )

        except Exception as e:
            logger.exception("Conversion error for user %s", user_id)
            await status_msg.edit_text(
                f"<b>Error:</b> Something went wrong during conversion.\n"
                f"<code>{type(e).__name__}: {e}</code>"
            )
        finally:
            # Cleanup temp files
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
            for af in audio_files:
                if os.path.exists(af):
                    os.remove(af)
