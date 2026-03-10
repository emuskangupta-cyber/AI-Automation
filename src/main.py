import json
import re
from pathlib import Path
import fitz
import os

from utils import ensure_dir, safe_filename
from toc_finder import (
    extract_toc_entries,
    extract_pages_text,
    find_index_start_page,
    find_toc_pages_by_keyword,
)
from section_builder import build_sections_from_entries
from extractor import extract_section_text
from index_parser import parse_index_text
from mcq_generator import save_mcqs_per_subtopic
from mcq_generator import attach_mcqs_to_topic_json

# ======================
# CONFIG
# ======================
PDF_PATH = os.getenv("PDF_PATH", "input/mybook.pdf")
ENABLE_OCR = True

INDEX_PAGES_1BASED = [3, 4]
AUTO_INDEX_PAGES_COUNT = 2

TOPIC_SUBTOPIC_JSON = "output/json/topic_subtopic_content.json"

AUTO_DETECT_PAGE_OFFSET = True
FALLBACK_PAGE_OFFSET = 0
OFFSET_SCAN_PAGES = 40
OFFSET_FALLBACK_SCAN = 120

SNAP_HEADING_WINDOW = 10
ANCHOR_SEARCH_PAGES = 250
MIN_CONTENT_CHARS = 400

MCQ_PER_SUBTOPIC = 10
# ======================


def normalize_text_for_json(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_topic_json(obj: list) -> list:
    if not isinstance(obj, list):
        return obj

    for topic in obj:
        if not isinstance(topic, dict):
            continue
        for st in topic.get("subtopics", []) or []:
            if not isinstance(st, dict):
                continue

            st["content"] = normalize_text_for_json(st.get("content", ""))

            mcqs = st.get("mcqs") or st.get("questions")
            if isinstance(mcqs, list):
                for q in mcqs:
                    if not isinstance(q, dict):
                        continue
                    q["question"] = normalize_text_for_json(q.get("question", ""))
                    q["source_sentence"] = normalize_text_for_json(q.get("source_sentence", ""))

                    if isinstance(q.get("options"), list):
                        q["options"] = [normalize_text_for_json(x) for x in q["options"]]

                    if "answer" in q:
                        q["answer"] = normalize_text_for_json(q.get("answer", ""))

    return obj


def clamp_page_range(start_page_1based: int, end_page_1based: int, total_pages: int):
    start_page_1based = max(1, min(int(start_page_1based), total_pages))
    end_page_1based = max(1, min(int(end_page_1based), total_pages))
    if end_page_1based < start_page_1based:
        end_page_1based = start_page_1based
    return start_page_1based, end_page_1based


def get_heading_token(title: str) -> str | None:
    m = re.match(r"^\s*(\d+(?:\.\d+)+)", (title or "").strip())
    return m.group(1) if m else None


def get_page_text(doc: fitz.Document, page_1based: int) -> str:
    idx = page_1based - 1
    if idx < 0 or idx >= doc.page_count:
        return ""
    return doc.load_page(idx).get_text("text") or ""


def looks_like_index_or_toc(text: str) -> bool:
    return len((text or "").strip()) < MIN_CONTENT_CHARS


def page_contains_token(doc: fitz.Document, page_1based: int, token: str, excluded_pages_1based: set[int]) -> bool:
    if page_1based in excluded_pages_1based:
        return False
    text = get_page_text(doc, page_1based)
    if not text:
        return False
    if looks_like_index_or_toc(text):
        return False

    t = text.lower()
    tok = token.lower()
    return (tok in t) or (tok + ".") in t


def find_token_in_first_pages(doc: fitz.Document, token: str, excluded_pages_1based: set[int], scan_pages: int = 250) -> int | None:
    scan = min(scan_pages, doc.page_count)
    for i in range(scan):
        p = i + 1
        if p in excluded_pages_1based:
            continue
        text = get_page_text(doc, p)
        if not text:
            continue
        if looks_like_index_or_toc(text):
            continue
        t = text.lower()
        tok = token.lower()
        if tok in t or (tok + ".") in t:
            return p
    return None


def detect_page_offset(doc: fitz.Document, index_text: str, scan_pages: int = 40) -> int:
    nums = []
    for m in re.finditer(r"\b(\d{1,4})\b", index_text):
        try:
            v = int(m.group(1))
            if 0 < v < 5000:
                nums.append(v)
        except ValueError:
            pass

    if nums:
        book_min = min(nums)
        scan = min(scan_pages, doc.page_count)
        candidates = []
        for i in range(scan):
            text = (doc.load_page(i).get_text("text") or "").strip()
            if not text:
                continue
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            tail = "\n".join(lines[-6:]) if len(lines) >= 6 else "\n".join(lines)
            if re.search(rf"(^|\s){book_min}(\s|$)", tail):
                candidates.append(i + 1)
        if candidates:
            return min(candidates) - book_min

    scan = min(OFFSET_FALLBACK_SCAN, doc.page_count)
    for i in range(scan):
        t = (doc.load_page(i).get_text("text") or "").lower()
        if "chapter 1" in t:
            return (i + 1) - 1

    return 0


def map_book_page_to_pdf_page(book_page: int, offset: int) -> int:
    return max(1, int(book_page) + int(offset))


def refine_offset_with_anchor(doc: fitz.Document, parsed_topics: list, base_offset: int, excluded_pages_1based: set[int]) -> int:
    for t in parsed_topics:
        for st in t.get("subtopics", []):
            title = (st.get("title") or "").strip()
            book_page = int(st.get("page", 0))
            token = get_heading_token(title)
            if token and book_page > 0:
                found = find_token_in_first_pages(doc, token, excluded_pages_1based, scan_pages=ANCHOR_SEARCH_PAGES)
                if found:
                    return found - book_page
                return base_offset
    return base_offset


def snap_start_page_by_heading(doc: fitz.Document, expected_start: int, token: str, window: int, excluded_pages_1based: set[int]) -> int:
    if not token:
        return expected_start
    best = None
    for delta in range(-window, window + 1):
        p = expected_start + delta
        if p < 1 or p > doc.page_count:
            continue
        if page_contains_token(doc, p, token, excluded_pages_1based):
            dist = abs(delta)
            if best is None or dist < best[0]:
                best = (dist, p)
                if dist == 0:
                    break
    return best[1] if best else expected_start


def extract_between_tokens(text: str, start_token: str, next_token: str | None) -> str:
    if not text:
        return ""

    t = text

    start_re = re.compile(rf"(^|\n)\s*{re.escape(start_token)}(\.|\s)", re.IGNORECASE)
    m = start_re.search(t)
    if not m:
        return t.strip()

    start_idx = m.start()

    if not next_token:
        return t[start_idx:].strip()

    next_re = re.compile(rf"(^|\n)\s*{re.escape(next_token)}(\.|\s)", re.IGNORECASE)
    m2 = next_re.search(t, pos=start_idx + 1)
    if not m2:
        return t[start_idx:].strip()

    end_idx = m2.start()
    return t[start_idx:end_idx].strip()


def build_topic_subtopic_objects(doc, pdf_path, parsed_topics, enable_ocr, page_offset, excluded_pages_1based):
    flat = []
    for tp in parsed_topics:
        topic_name = tp.get("topic", "Unknown Topic")
        for st in tp.get("subtopics", []):
            title = (st.get("title") or "").strip()
            book_page = int(st.get("page", 0))
            token = get_heading_token(title)
            if title and book_page > 0 and token:
                flat.append({
                    "topic": topic_name,
                    "title": title,
                    "book_page": book_page,
                    "token": token
                })

    if not flat:
        return []

    flat.sort(key=lambda x: x["book_page"])

    for item in flat:
        expected = map_book_page_to_pdf_page(item["book_page"], page_offset)
        item["start_pdf"] = snap_start_page_by_heading(
            doc, expected, item["token"], SNAP_HEADING_WINDOW, excluded_pages_1based
        )

    enriched = []

    for i, item in enumerate(flat):
        start_page = item["start_pdf"]

        if i + 1 < len(flat):
            next_start_page = flat[i + 1]["start_pdf"]
            end_page = max(start_page, next_start_page)
            next_token = flat[i + 1]["token"]
        else:
            end_page = min(doc.page_count, start_page + 3)
            next_token = None

        start_page, end_page = clamp_page_range(start_page, end_page, doc.page_count)

        raw_block = extract_section_text(doc, pdf_path, start_page, end_page, enable_ocr=enable_ocr)

        content = extract_between_tokens(raw_block, item["token"], next_token)

        enriched.append({
            "topic": item["topic"],
            "title": item["title"],
            "book_start_page": item["book_page"],
            "start_page": start_page,
            "end_page": end_page,
            "content": content
        })

    grouped = {}
    for it in enriched:
        grouped.setdefault(it["topic"], []).append({
            "title": it["title"],
            "book_start_page": it["book_start_page"],
            "start_page": it["start_page"],
            "end_page": it["end_page"],
            "content": it["content"]
        })

    return [{"topic": k, "subtopics": v} for k, v in grouped.items()]


def run():
    pdf_path = Path(PDF_PATH)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    ensure_dir("output")
    ensure_dir("output/text")
    ensure_dir("output/json")

    doc = fitz.open(str(pdf_path))
    total_pages = doc.page_count

    out_index_txt = Path("output/index.txt")
    out_index_json = Path("output/index.json")

    index_meta = {
        "pdf": str(pdf_path),
        "total_pages": total_pages,
        "index_source": None,
        "index_pages_1based": None,
    }

    excluded_pages_1based = set()

    if INDEX_PAGES_1BASED:
        pages_0based = [p - 1 for p in INDEX_PAGES_1BASED]
        index_text = extract_pages_text(doc, pages_0based)
        index_meta["index_source"] = "manual_pages"
        index_meta["index_pages_1based"] = INDEX_PAGES_1BASED
        excluded_pages_1based.update(INDEX_PAGES_1BASED)
    else:
        start = find_index_start_page(doc) or 0
        pages_0based = list(range(start, min(start + AUTO_INDEX_PAGES_COUNT, total_pages)))
        index_text = extract_pages_text(doc, pages_0based)
        index_meta["index_source"] = "auto_index_find"
        index_meta["index_pages_1based"] = [p + 1 for p in pages_0based]
        excluded_pages_1based.update([p + 1 for p in pages_0based])

    toc_pages_0based = find_toc_pages_by_keyword(doc, max_scan_pages=30) or []
    excluded_pages_1based.update([p + 1 for p in toc_pages_0based])

    out_index_txt.write_text(index_text, encoding="utf-8")
    out_index_json.write_text(json.dumps(index_meta, indent=2, ensure_ascii=False), encoding="utf-8")

    parsed_topics = parse_index_text(index_text)

    if AUTO_DETECT_PAGE_OFFSET:
        detected_offset = detect_page_offset(doc, index_text, scan_pages=OFFSET_SCAN_PAGES)
        if detected_offset == 0 and FALLBACK_PAGE_OFFSET != 0:
            detected_offset = FALLBACK_PAGE_OFFSET
    else:
        detected_offset = FALLBACK_PAGE_OFFSET

    refined_offset = refine_offset_with_anchor(doc, parsed_topics, detected_offset, excluded_pages_1based)

    print(f"✅ Base offset detected: {detected_offset}")
    print(f"✅ Refined offset used: {refined_offset}")
    print(f"✅ Excluded pages (1-based): {sorted(excluded_pages_1based)}")

    topic_subtopic_result = build_topic_subtopic_objects(
        doc=doc,
        pdf_path=str(pdf_path),
        parsed_topics=parsed_topics,
        enable_ocr=ENABLE_OCR,
        page_offset=refined_offset,
        excluded_pages_1based=excluded_pages_1based
    )

    topic_subtopic_result = attach_mcqs_to_topic_json(
        topic_subtopic_result,
        n_per_subtopic=MCQ_PER_SUBTOPIC
    )

    topic_subtopic_result = clean_topic_json(topic_subtopic_result)

    Path(TOPIC_SUBTOPIC_JSON).write_text(
        json.dumps(topic_subtopic_result, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"✅ Topic/Subtopic JSON saved: {TOPIC_SUBTOPIC_JSON}")

    summary = save_mcqs_per_subtopic(
        topic_subtopic_json_path=TOPIC_SUBTOPIC_JSON,
        out_dir="output/mcq",
        n_per_subtopic=MCQ_PER_SUBTOPIC
    )
    print("✅ MCQ generation summary:", summary)
    print("\n✅ Done!")


if __name__ == "__main__":
    run()