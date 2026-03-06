from typing import List, Dict

def build_sections_from_entries(entries: List[Dict], total_pages: int) -> List[Dict]:
    """
    entries: [{title, page}] page is 1-based
    total_pages: document total pages (0-based count)
    Output: sections [{title, start_page, end_page}] (1-based inclusive)
    """
    if not entries:
        return []

    # sort by page
    entries = sorted(entries, key=lambda x: x["page"])
    sections = []
    for i, e in enumerate(entries):
        start_page = int(e["page"])
        title = e["title"]

        if start_page < 1:
            continue
        if start_page > total_pages:
            continue

        if i + 1 < len(entries):
            next_start = int(entries[i + 1]["page"])
            end_page = max(start_page, next_start - 1)
        else:
            end_page = total_pages

        sections.append({
            "title": title,
            "start_page": start_page,
            "end_page": end_page
        })

    # remove weird overlaps (basic cleanup)
    cleaned = []
    last_end = 0
    for s in sections:
        if s["start_page"] <= last_end:
            s["start_page"] = last_end + 1
        if s["start_page"] <= s["end_page"]:
            cleaned.append(s)
            last_end = s["end_page"]

    return cleaned