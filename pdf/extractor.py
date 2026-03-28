"""PDF text extraction with frequency-based header/footer cleaning."""

import re
from collections import Counter
from dataclasses import dataclass

import fitz  # PyMuPDF

from config import HEADER_FOOTER_FREQ_THRESHOLD, MIN_PAGE_CHARS


@dataclass
class PageText:
    """Cleaned text for a single PDF page."""

    page_num: int  # 1-based
    text: str
    is_blank: bool = False


@dataclass
class _BlockInfo:
    """Internal representation of a text block with position metadata."""

    text: str
    y_ratio: float  # vertical position as fraction of page height (0 = top, 1 = bottom)
    page_idx: int


def _normalise(text: str) -> str:
    """Collapse whitespace and strip for comparison."""
    return re.sub(r"\s+", " ", text).strip()


# Inline patterns to strip from text content (e.g. "Page 5 of 128")
_INLINE_PAGE_PATTERNS = re.compile(
    r"(?i)\bpage\s+\d{1,5}(?:\s+of\s+\d{1,5})?\b"
)


def _clean_inline_artifacts(text: str) -> str:
    """Remove inline page-number patterns like 'Page 5 of 128' from text."""
    cleaned = _INLINE_PAGE_PATTERNS.sub("", text)
    # Collapse any resulting double-spaces or leading/trailing whitespace per line
    lines = [re.sub(r"  +", " ", line).strip() for line in cleaned.split("\n")]
    return "\n".join(line for line in lines if line)


def _is_page_number(text: str) -> bool:
    """Check if text looks like a standalone page number."""
    stripped = text.strip()
    # Pure digits, roman numerals, or common patterns like "- 5 -", "Page 5"
    if re.fullmatch(r"\d{1,5}", stripped):
        return True
    if re.fullmatch(r"[ivxlcdmIVXLCDM]+", stripped):
        return True
    if re.fullmatch(r"[-–—]\s*\d{1,5}\s*[-–—]", stripped):
        return True
    if re.fullmatch(r"(?i)page\s+\d{1,5}", stripped):
        return True
    return False


def _extract_blocks(doc: fitz.Document) -> list[_BlockInfo]:
    """Extract text blocks with vertical position metadata from every page."""
    blocks: list[_BlockInfo] = []
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        page_height = page.rect.height
        if page_height == 0:
            continue
        raw_blocks = page.get_text("blocks")  # (x0, y0, x1, y1, text, block_no, block_type)
        for b in raw_blocks:
            if b[6] != 0:  # skip image blocks
                continue
            text = b[4].strip()
            if not text:
                continue
            # y_ratio = midpoint of block relative to page height
            y_mid = (b[1] + b[3]) / 2.0
            y_ratio = y_mid / page_height
            blocks.append(_BlockInfo(text=text, y_ratio=y_ratio, page_idx=page_idx))
    return blocks


def _find_recurring_header_footer_texts(
    blocks: list[_BlockInfo], total_pages: int
) -> set[str]:
    """Identify text that recurs in top/bottom zones across many pages.

    A block is in the *top zone* if its y_ratio < 0.12 and in the *bottom zone*
    if y_ratio > 0.88.  We only flag text that appears on more than
    HEADER_FOOTER_FREQ_THRESHOLD of all pages.
    """
    top_counter: Counter[str] = Counter()
    bottom_counter: Counter[str] = Counter()

    # Track which pages each normalised string appears on (to avoid double-counting)
    top_pages: dict[str, set[int]] = {}
    bottom_pages: dict[str, set[int]] = {}

    for blk in blocks:
        norm = _normalise(blk.text)
        if not norm:
            continue
        if blk.y_ratio < 0.12:
            top_pages.setdefault(norm, set()).add(blk.page_idx)
        elif blk.y_ratio > 0.88:
            bottom_pages.setdefault(norm, set()).add(blk.page_idx)

    recurring: set[str] = set()
    threshold = max(2, int(total_pages * HEADER_FOOTER_FREQ_THRESHOLD))

    for norm, pages in top_pages.items():
        if len(pages) >= threshold:
            recurring.add(norm)
    for norm, pages in bottom_pages.items():
        if len(pages) >= threshold:
            recurring.add(norm)

    return recurring


def extract_pages(pdf_path: str) -> list[PageText]:
    """Extract cleaned text from each page of a PDF.

    Steps:
    1. Extract all text blocks with positional info.
    2. Identify recurring header/footer text via frequency analysis.
    3. Strip page numbers from extreme positions.
    4. Return per-page cleaned text.
    """
    doc = fitz.open(pdf_path)
    total_pages = len(doc)

    all_blocks = _extract_blocks(doc)
    recurring = _find_recurring_header_footer_texts(all_blocks, total_pages)

    pages: list[PageText] = []

    for page_idx in range(total_pages):
        page = doc[page_idx]
        page_height = page.rect.height
        raw_blocks = page.get_text("blocks")

        kept_lines: list[str] = []
        for b in raw_blocks:
            if b[6] != 0:
                continue
            text = b[4].strip()
            if not text:
                continue

            y_mid = (b[1] + b[3]) / 2.0
            y_ratio = y_mid / page_height if page_height else 0.5

            norm = _normalise(text)

            # Remove if it matches a recurring header/footer
            if norm in recurring and (y_ratio < 0.12 or y_ratio > 0.88):
                continue

            # Remove standalone page numbers in extreme positions
            if _is_page_number(text) and (y_ratio < 0.10 or y_ratio > 0.90):
                continue

            kept_lines.append(text)

        full_text = "\n".join(kept_lines).strip()
        # Clean inline artefacts like "Page 5 of 128" embedded in text
        full_text = _clean_inline_artifacts(full_text)
        is_blank = len(full_text) < MIN_PAGE_CHARS
        pages.append(PageText(page_num=page_idx + 1, text=full_text, is_blank=is_blank))

    doc.close()
    return pages
