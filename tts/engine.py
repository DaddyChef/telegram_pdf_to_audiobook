"""Text-to-Speech engine with chunking support for edge-tts."""

import io
import os
import re
import logging
from typing import AsyncIterator

import edge_tts

from config import TTS_CHUNK_SIZE

logger = logging.getLogger(__name__)


def _split_into_chunks(text: str, max_chars: int = TTS_CHUNK_SIZE) -> list[str]:
    """Split text into chunks at sentence boundaries.

    Tries to split at '. ', '? ', '! ' first, then at newlines,
    then at spaces as a last resort.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break

        # Try to find a sentence boundary within the limit
        segment = remaining[:max_chars]

        # Look for sentence-ending punctuation followed by space
        split_pos = -1
        for pattern in [r"[.!?]\s", r"\n", r"\s"]:
            matches = list(re.finditer(pattern, segment))
            if matches:
                # Use the last match position
                last_match = matches[-1]
                split_pos = last_match.end()
                break

        if split_pos <= 0:
            # No good split point found — force split at max_chars
            split_pos = max_chars

        chunk = remaining[:split_pos].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_pos:].strip()

    return [c for c in chunks if c]


async def synthesize_chapter(
    text: str,
    voice_id: str,
    output_path: str,
    on_progress: None = None,
) -> str:
    """Convert chapter text to a single MP3 file.

    Splits text into manageable chunks, converts each with edge-tts,
    and concatenates the raw MP3 bytes (MP3 frames are independent so
    binary concatenation works correctly).

    Returns the output file path.
    """
    chunks = _split_into_chunks(text)
    logger.info(
        "Synthesizing %d chunk(s) for %s", len(chunks), os.path.basename(output_path)
    )

    with open(output_path, "wb") as outfile:
        for i, chunk in enumerate(chunks):
            try:
                communicate = edge_tts.Communicate(chunk, voice_id)
                async for msg in communicate.stream():
                    if msg["type"] == "audio":
                        outfile.write(msg["data"])
            except Exception:
                logger.exception("TTS error on chunk %d/%d", i + 1, len(chunks))
                raise

    return output_path
