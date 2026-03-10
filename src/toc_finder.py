import re
from typing import List, Dict, Tuple, Optional
import fitz  # PyMuPDF

TOC_KEYWORDS = ["table of contents", "contents", "index"]


def get_builtin_toc(doc: fitz.Document) -> List[Dict]:
    toc = doc.get_toc(simple=False)
    out = []
    for item in toc:
        level, title, page = item[0], item[1], item[2]
        if page is None or page <= 0:
            continue
        out.append({"level": int(level), "title": str(title).strip(), "page": int(page)})
    return out


def extract_pages_text(doc: fitz.Document, page_indexes_0based: List[int]) -> str:
    texts = []
    for pi in page_indexes_0based:
        if 0 <= pi < doc.page_count:
            texts.append(doc.load_page(pi).get_text("text") or "")
    return "\n\n----- NEXT INDEX PAGE -----\n\n".join(texts).strip()


def find_index_start_page(doc: fitz.Document, max_scan_pages: int = 30) -> Optional[int]:
    """
    Tries to locate the real Index page (not Preface/Ack).
    Heuristic: page contains 'index' and also 'chapter' somewhere.
    Returns 0-based page index or None.
    """
    scan = min(max_scan_pages, doc.page_count)

    for i in range(scan):
        text = (doc.load_page(i).get_text("text") or "").lower()

        # must contain index
        if "index" not in text:
            continue

        # avoid "Index" word appearing in random places
        # prefer pages that actually look like TOC/index
        if "chapter" in text:
            return i

    return None


def find_toc_pages_by_keyword(doc: fitz.Document, max_scan_pages: int = 30) -> List[int]:
    """
    Generic TOC finder by keywords.
    (May catch Preface page if it contains the word Index somewhere)
    """
    found = []
    scan = min(max_scan_pages, doc.page_count)

    for i in range(scan):
        text = (doc.load_page(i).get_text("text") or "").lower()
        for kw in TOC_KEYWORDS:
            if kw in text:
                found.append(i)
                break

    if not found:
        return []

    first = found[0]
    candidates = [first, first + 1, first + 2]
    candidates = [p for p in candidates if 0 <= p < doc.page_count]
    return sorted(set(candidates))


def parse_toc_text_to_entries(toc_text: str) -> List[Tuple[str, int]]:
    """
    Basic parser: title + lots of spaces/dots + page number.
    (Not perfect for every PDF, but fine.)
    """
    entries = []
    lines = [ln.strip() for ln in toc_text.splitlines() if ln.strip()]
    pattern = re.compile(r"^(?P<title>.+?)\s*(\.{2,}|\s{2,}|\t+)\s*(?P<page>\d+)\s*$")

    for ln in lines:
        m = pattern.match(ln)
        if m:
            title = m.group("title").strip()
            page = int(m.group("page"))
            if title and page > 0:
                entries.append((title, page))

    return entries


def extract_toc_entries(doc: fitz.Document) -> Dict:
    """
    Used for chapter splitting (sections).
    Not for saving index pages (that's handled in main.py).
    """
    builtin = get_builtin_toc(doc)
    if builtin:
        return {"method": "builtin_toc", "entries": builtin, "toc_text": "", "toc_page_indexes": []}

    toc_pages = find_toc_pages_by_keyword(doc)
    if toc_pages:
        raw_text = extract_pages_text(doc, toc_pages)
        first_text = doc.load_page(toc_pages[0]).get_text("text") or ""
        parsed = parse_toc_text_to_entries(first_text)
        entries = [{"title": t, "page": p} for (t, p) in parsed] if parsed else []
        return {
            "method": "toc_pages_found",
            "toc_page_indexes": toc_pages,
            "toc_text": raw_text,
            "entries": entries,
        }

    return {"method": "none", "entries": [], "toc_text": "", "toc_page_indexes": []}