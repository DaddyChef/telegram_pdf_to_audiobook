"""Inline keyboard builders for the Telegram bot."""

from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import LANGUAGES


def language_keyboard() -> InlineKeyboardBuilder:
    """Build keyboard with available languages."""
    builder = InlineKeyboardBuilder()
    for lang_name in LANGUAGES:
        builder.button(text=lang_name, callback_data=f"lang:{lang_name}")
    builder.adjust(1)
    return builder


def voice_keyboard(lang_name: str) -> InlineKeyboardBuilder:
    """Build keyboard with voices for a given language."""
    voices = LANGUAGES.get(lang_name, [])
    builder = InlineKeyboardBuilder()
    for v_id in voices:
        parts = v_id.split("-")
        builder.button(
            text=f"{parts[-1].replace('Neural', '')} {parts[1]}",
            callback_data=f"preview:{v_id}",
        )
    builder.row(
        InlineKeyboardButton(text="⬅️ Back to Languages", callback_data="back_to_langs")
    )
    builder.adjust(2)
    return builder


def preview_confirm_keyboard(voice_id: str) -> InlineKeyboardBuilder:
    """Build keyboard for confirming or changing a previewed voice."""
    name = voice_id.split("-")[-1].replace("Neural", "")
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Use {name}", callback_data=f"confirm:{voice_id}")
    builder.button(text="Use another voice", callback_data="back_to_langs")
    builder.adjust(1)
    return builder
