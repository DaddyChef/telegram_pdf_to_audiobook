"""Chapter detection and text splitting for PDF audiobooks."""

import re
from dataclasses import dataclass

import fitz  # PyMuPDF

from pdf.extractor import PageText

# Patterns that indicate introductory sections
_INTRO_PATTERNS = re.compile(
    r"(?i)^\s*(preface|foreword|forward|introduction|prologue)\s*$"
)

# Patterns that indicate chapter headings
_CHAPTER_PATTERNS = re.compile(
    r"(?i)"
    r"(?:"
    r"^chapter\s+[\divxlcdm]+[.:]?\s*"  # "Chapter 1", "Chapter IV"
    r"|^part\s+[\divxlcdm]+[.:]?\s*"  # "Part 1"
    r"|^section\s+[\divxlcdm]+[.:]?\s*"  # "Section 3"
    r"|^epilogue\s*$"
    r"|^afterword\s*$"
    r"|^conclusion\s*$"
    r")",
    re.MULTILINE,
)

# Pages that are likely back-matter (index, bibliography, etc.) — skip from audio
_BACKMATTER_PATTERNS = re.compile(
    r"(?i)^\s*(index|bibliography|references|glossary|acknowledgements|acknowledgments|about\s+the\s+author)\s*$"
)


@dataclass
class Chapter:
    """A detected chapter / section of the book."""

    title: str
    start_page: int  # 1-based, inclusive
    end_page: int  # 1-based, inclusive
    text: str
    is_intro: bool = False


def _get_toc_chapters(pdf_path: str) -> list[tuple[str, int]]:
    """Try to read the PDF Table of Contents.

    Returns list of (title, page_number_1based) sorted by page number.
    """
    doc = fitz.open(pdf_path)
    toc = doc.get_toc()  # list of [level, title, page]
    doc.close()

    if not toc:
        return []

    # Keep only top-level entries (level 1) or level 2 if no level 1
    min_level = min(entry[0] for entry in toc)
    entries = [(entry[1].strip(), entry[2]) for entry in toc if entry[0] == min_level]

    # Deduplicate and sort by page
    seen: set[int] = set()
    result: list[tuple[str, int]] = []
    for title, page in entries:
        if page not in seen:
            seen.add(page)
            result.append((title, page))
    result.sort(key=lambda x: x[1])
    return result


def _detect_font_size_headings(
    pdf_path: str, total_pages: int
) -> list[tuple[str, int]]:
    """Detect headings by looking for text with significantly larger font sizes.

    If the initial threshold produces too many headings (more than 1 per 3 pages),
    we progressively raise the font-size threshold to keep only the highest-level
    headings, producing a reasonable chapter count.
    """
    doc = fitz.open(pdf_path)

    # First pass: collect all font sizes to find the median body size
    all_sizes: list[float] = []
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if len(text) > 3:  # skip tiny fragments
                        all_sizes.append(span["size"])

    if not all_sizes:
        doc.close()
        return []

    all_sizes.sort()
    median_size = all_sizes[len(all_sizes) // 2]

    # Second pass: collect ALL candidate headings with their font sizes
    raw_headings: list[tuple[str, int, float]] = []  # (text, page_1based, font_size)
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                line_text = "".join(s.get("text", "") for s in spans).strip()
                if not line_text or len(line_text) > 100:
                    continue
                max_span_size = max(s["size"] for s in spans)
                if max_span_size > median_size * 1.3 and len(line_text) > 2:
                    raw_headings.append((line_text, page_idx + 1, max_span_size))

    doc.close()

    if not raw_headings:
        return []

    # Collect distinct font sizes used by headings (sorted descending)
    heading_sizes = sorted({round(h[2], 1) for h in raw_headings}, reverse=True)

    # Reasonable max: ~1 chapter per 10 pages (a 200-page book ≈ 20 chapters)
    max_reasonable = max(5, total_pages // 10)

    # Try progressively looser thresholds: start with only the largest
    # heading size, then add the next-largest, etc.  This ensures we prefer
    # top-level chapter headings over sub-section headings.
    best: list[tuple[str, int]] = []
    for num_sizes in range(1, len(heading_sizes) + 1):
        allowed_sizes = set(heading_sizes[:num_sizes])
        min_allowed = min(allowed_sizes)
        filtered = [
            (text, page) for text, page, sz in raw_headings
            if round(sz, 1) >= min_allowed
        ]
        # Deduplicate by page (keep first heading per page)
        seen_pages: set[int] = set()
        deduped: list[tuple[str, int]] = []
        for title, page in filtered:
            if page not in seen_pages:
                seen_pages.add(page)
                deduped.append((title, page))

        if len(deduped) >= 2:
            if len(deduped) <= max_reasonable:
                best = deduped  # accept this tier and try adding one more
            else:
                # Adding this tier made it too large — keep previous best
                break

    return best


def _detect_regex_headings(pages: list[PageText]) -> list[tuple[str, int]]:
    """Detect chapter headings using regex patterns on page text."""
    headings: list[tuple[str, int]] = []
    for pt in pages:
        if pt.is_blank:
            continue
        for line in pt.text.split("\n")[:5]:  # Only check first 5 lines of each page
            line_stripped = line.strip()
            if _CHAPTER_PATTERNS.match(line_stripped):
                headings.append((line_stripped, pt.page_num))
                break  # One heading per page
    return headings


def _detect_intro(pages: list[PageText]) -> tuple[str, int] | None:
    """Check if early pages contain an introduction/preface/prologue heading."""
    # Only look at the first 15% of the book or first 20 pages
    search_limit = min(20, max(5, len(pages) // 7))
    for pt in pages[:search_limit]:
        if pt.is_blank:
            continue
        for line in pt.text.split("\n")[:5]:
            if _INTRO_PATTERNS.match(line.strip()):
                return (line.strip(), pt.page_num)
    return None


def _is_backmatter_page(pt: PageText) -> bool:
    """Check if a page is likely back-matter (index, bibliography, etc.)."""
    if pt.is_blank:
        return True
    first_lines = "\n".join(pt.text.split("\n")[:3])
    return bool(_BACKMATTER_PATTERNS.search(first_lines))


def _find_content_end(pages: list[PageText]) -> int:
    """Find the last page that contains actual book content (not back-matter).

    Returns the 1-based page number of the last content page.
    """
    # Walk backwards from the end, skipping blank/backmatter pages
    for i in range(len(pages) - 1, -1, -1):
        if not pages[i].is_blank and not _is_backmatter_page(pages[i]):
            return pages[i].page_num
    return pages[-1].page_num if pages else 1


def _build_chapters_from_headings(
    headings: list[tuple[str, int]],
    pages: list[PageText],
    content_end: int,
) -> list[Chapter]:
    """Given heading positions, build Chapter objects with text."""
    if not headings:
        return []

    page_map = {p.page_num: p for p in pages}
    chapters: list[Chapter] = []

    for i, (title, start_page) in enumerate(headings):
        if i + 1 < len(headings):
            end_page = headings[i + 1][1] - 1
        else:
            end_page = content_end

        # Clamp
        end_page = min(end_page, content_end)
        if start_page > end_page:
            continue

        # Collect text
        text_parts: list[str] = []
        actual_start = None
        actual_end = None
        for pn in range(start_page, end_page + 1):
            pt = page_map.get(pn)
            if pt and not pt.is_blank:
                text_parts.append(pt.text)
                if actual_start is None:
                    actual_start = pn
                actual_end = pn

        if not text_parts:
            continue

        is_intro = bool(_INTRO_PATTERNS.match(title))
        chapters.append(
            Chapter(
                title=title,
                start_page=actual_start or start_page,
                end_page=actual_end or end_page,
                text="\n\n".join(text_parts),
                is_intro=is_intro,
            )
        )

    return chapters


def _build_fixed_chunks(
    pages: list[PageText],
    content_end: int,
    chunk_size: int = 20,
) -> list[Chapter]:
    """Fallback: split into fixed page-count chunks."""
    content_pages = [
        p for p in pages if not p.is_blank and p.page_num <= content_end
    ]
    if not content_pages:
        return []

    chapters: list[Chapter] = []
    for i in range(0, len(content_pages), chunk_size):
        chunk = content_pages[i : i + chunk_size]
        text = "\n\n".join(p.text for p in chunk)
        chapters.append(
            Chapter(
                title="",
                start_page=chunk[0].page_num,
                end_page=chunk[-1].page_num,
                text=text,
                is_intro=False,
            )
        )
    return chapters


def detect_chapters(
    pdf_path: str, pages: list[PageText]
) -> tuple[list[Chapter], bool]:
    """Detect chapters in a PDF.

    Returns (chapters, chapters_found) where chapters_found indicates whether
    semantic chapter headings were detected (True) or the fallback was used (False).

    Strategy (prioritised):
    1. PDF Table of Contents
    2. Font-size based heading detection
    3. Regex pattern matching
    4. Fixed 20-page chunks (fallback)
    """
    content_end = _find_content_end(pages)

    # Detect intro section
    intro = _detect_intro(pages)

    # --- Strategy 1: PDF ToC ---
    toc_headings = _get_toc_chapters(pdf_path)
    if len(toc_headings) >= 2:
        chapters = _build_chapters_from_headings(toc_headings, pages, content_end)
        if chapters:
            return _prepend_intro(chapters, intro, pages, content_end), True

    # --- Strategy 2: Font-size headings ---
    font_headings = _detect_font_size_headings(pdf_path, len(pages))
    if len(font_headings) >= 2:
        chapters = _build_chapters_from_headings(font_headings, pages, content_end)
        if chapters:
            return _prepend_intro(chapters, intro, pages, content_end), True

    # --- Strategy 3: Regex headings ---
    regex_headings = _detect_regex_headings(pages)
    if len(regex_headings) >= 2:
        chapters = _build_chapters_from_headings(regex_headings, pages, content_end)
        if chapters:
            return _prepend_intro(chapters, intro, pages, content_end), True

    # --- Strategy 4: Fixed chunks (fallback) ---
    chunks = _build_fixed_chunks(pages, content_end)

    # If we found an intro, separate it from the first chunk
    if intro and chunks:
        intro_page = intro[1]
        # Split the first chunk if the intro starts there
        new_chunks: list[Chapter] = []
        for ch in chunks:
            if ch.start_page <= intro_page <= ch.end_page and not ch.is_intro:
                # This chunk contains the intro page — separate it
                page_map = {p.page_num: p for p in pages}
                intro_texts: list[str] = []
                rest_texts: list[str] = []
                intro_end = intro_page  # intro is just the starting page by default

                for pn in range(ch.start_page, ch.end_page + 1):
                    pt = page_map.get(pn)
                    if pt and not pt.is_blank:
                        if pn <= intro_page:
                            intro_texts.append(pt.text)
                        else:
                            rest_texts.append(pt.text)

                if intro_texts:
                    new_chunks.append(
                        Chapter(
                            title=intro[0],
                            start_page=ch.start_page,
                            end_page=intro_page,
                            text="\n\n".join(intro_texts),
                            is_intro=True,
                        )
                    )
                if rest_texts:
                    first_rest_page = intro_page + 1
                    # Find actual first non-blank page
                    for pn in range(intro_page + 1, ch.end_page + 1):
                        pt = page_map.get(pn)
                        if pt and not pt.is_blank:
                            first_rest_page = pn
                            break
                    new_chunks.append(
                        Chapter(
                            title="",
                            start_page=first_rest_page,
                            end_page=ch.end_page,
                            text="\n\n".join(rest_texts),
                            is_intro=False,
                        )
                    )
            else:
                new_chunks.append(ch)
        chunks = new_chunks

    return chunks, False


def _prepend_intro(
    chapters: list[Chapter],
    intro: tuple[str, int] | None,
    pages: list[PageText],
    content_end: int,
) -> list[Chapter]:
    """If an intro was detected and isn't already the first chapter, prepend it."""
    if not intro:
        return chapters

    intro_title, intro_page = intro

    # Check if the first chapter already covers the intro
    if chapters and chapters[0].start_page <= intro_page:
        # The first chapter already includes the intro page — mark it
        if _INTRO_PATTERNS.match(chapters[0].title):
            chapters[0].is_intro = True
        return chapters

    # Build an intro chapter from page 1 (or first content page) to just before
    # the first real chapter
    page_map = {p.page_num: p for p in pages}
    first_chapter_page = chapters[0].start_page if chapters else content_end + 1

    intro_texts: list[str] = []
    actual_start = None
    actual_end = None
    for pn in range(1, first_chapter_page):
        pt = page_map.get(pn)
        if pt and not pt.is_blank:
            intro_texts.append(pt.text)
            if actual_start is None:
                actual_start = pn
            actual_end = pn

    if intro_texts:
        intro_chapter = Chapter(
            title=intro_title,
            start_page=actual_start or 1,
            end_page=actual_end or first_chapter_page - 1,
            text="\n\n".join(intro_texts),
            is_intro=True,
        )
        return [intro_chapter] + chapters

    return chapters


def format_filename(
    book_name: str,
    chapter: Chapter,
    index: int,
    chapters_found: bool,
) -> str:
    """Generate the audio filename for a chapter.

    Naming conventions:
    - Introduction: "{book} Introduction, {start}-{end}p"
    - Scenario A (chapters found): "{book} Chapter {N}, {start}-{end}p"
    - Scenario B (no chapters): "{book} {start}-{end}"
    """
    # Sanitise book name for filesystem
    safe_name = re.sub(r'[<>:"/\\|?*]', "", book_name).strip()
    if not safe_name:
        safe_name = "Audiobook"

    start = chapter.start_page
    end = chapter.end_page

    if chapter.is_intro:
        return f"{safe_name} Introduction, {start}-{end}p"

    if chapters_found:
        # Use the chapter title if it's meaningful, otherwise "Chapter {index}"
        title = chapter.title.strip()
        if title:
            # Clean up the title for filename use
            clean_title = re.sub(r'[<>:"/\\|?*]', "", title).strip()
            return f"{safe_name} {clean_title}, {start}-{end}p"
        else:
            return f"{safe_name} Chapter {index}, {start}-{end}p"
    else:
        return f"{safe_name} {start}-{end}"
