"""Text-to-Speech engine with chunking support for edge-tts."""

import asyncio
import os
import re
import logging

import edge_tts

from config import TTS_CHUNK_SIZE, TTS_CHUNK_TIMEOUT, TTS_CHUNK_RETRIES

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


async def _synthesize_chunk(
    chunk: str,
    voice_id: str,
    outfile: object,
    chunk_num: int,
    total_chunks: int,
) -> None:
    """Synthesize a single text chunk with timeout and retry logic.

    Retries up to TTS_CHUNK_RETRIES times if edge-tts hangs or fails.
    Each attempt has a TTS_CHUNK_TIMEOUT second timeout.
    """
    last_error: Exception | None = None

    for attempt in range(1, TTS_CHUNK_RETRIES + 1):
        try:
            logger.info(
                "Chunk %d/%d — attempt %d, %d chars",
                chunk_num, total_chunks, attempt, len(chunk),
            )
            communicate = edge_tts.Communicate(chunk, voice_id)

            async def _stream_audio() -> None:
                async for msg in communicate.stream():
                    if msg["type"] == "audio":
                        outfile.write(msg["data"])

            await asyncio.wait_for(_stream_audio(), timeout=TTS_CHUNK_TIMEOUT)
            logger.info("Chunk %d/%d — done", chunk_num, total_chunks)
            return  # success

        except asyncio.TimeoutError:
            last_error = TimeoutError(
                f"Chunk {chunk_num}/{total_chunks} timed out after "
                f"{TTS_CHUNK_TIMEOUT}s (attempt {attempt}/{TTS_CHUNK_RETRIES})"
            )
            logger.warning(str(last_error))
        except Exception as e:
            last_error = e
            logger.warning(
                "Chunk %d/%d — error on attempt %d: %s",
                chunk_num, total_chunks, attempt, e,
            )

        # Brief pause before retry
        if attempt < TTS_CHUNK_RETRIES:
            await asyncio.sleep(2)

    # All retries exhausted
    raise RuntimeError(
        f"Failed to synthesize chunk {chunk_num}/{total_chunks} "
        f"after {TTS_CHUNK_RETRIES} attempts"
    ) from last_error


async def synthesize_chapter(
    text: str,
    voice_id: str,
    output_path: str,
) -> str:
    """Convert chapter text to a single MP3 file.

    Splits text into manageable chunks, converts each with edge-tts
    (with timeout and retry per chunk), and concatenates the raw MP3
    bytes (MP3 frames are independent so binary concatenation works).

    Returns the output file path.
    """
    chunks = _split_into_chunks(text)
    logger.info(
        "Synthesizing %d chunk(s) for %s", len(chunks), os.path.basename(output_path)
    )

    with open(output_path, "wb") as outfile:
        for i, chunk in enumerate(chunks, start=1):
            await _synthesize_chunk(chunk, voice_id, outfile, i, len(chunks))

    return output_path
