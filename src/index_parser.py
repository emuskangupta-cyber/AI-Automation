import re
from typing import List, Dict, Optional


def _clean_line(line: str) -> str:
    line = line.replace("\u00a0", " ")
    line = re.sub(r"\s+", " ", line).strip()
    return line


IGNORE_PATTERN = re.compile(
    r"^(index|contents|table of contents|chapter\s+\d+)$",
    re.IGNORECASE
)

# Subtopic patterns:
# 1) "1.2 Something 12"
SUBTOPIC_SAME_LINE = re.compile(r"^(?P<title>.+?)\s+(?P<page>\d{1,4})$")

# 2) "2.1 Introduction" (page may appear next line)
SUBTOPIC_NUMBERED_ONLY = re.compile(r"^(?P<title>\d+(\.\d+)+\.?\s+.+)$")


def parse_index_text(index_text: str) -> List[Dict]:
    """
    Updated parser supports:
    ✅ multi-line TOPICS
    ✅ multi-line SUBTOPICS where page number is on next line
    ✅ ignores noise lines (Index, Chapter 1, etc.)

    Returns:
    [
      {
        "topic": "DNA replication",
        "subtopics": [{"title": "1.1 Introduction", "page": 1}, ...]
      }
    ]
    """

    raw_lines = [_clean_line(l) for l in index_text.splitlines()]
    raw_lines = [l for l in raw_lines if l]  # remove empties

    topic_blocks: List[Dict] = []
    current_topic: Optional[str] = None
    current_subtopics: List[Dict] = []

    # Buffers to join multi-line topic/subtopic
    topic_buffer: List[str] = []
    pending_subtopic_title: Optional[str] = None  # subtopic title waiting for page on next line

    def flush_topic():
        nonlocal current_topic, topic_buffer
        if topic_buffer:
            joined = " ".join(topic_buffer).strip()
            topic_buffer = []
            if joined:
                current_topic = joined

    def finalize_current_topic():
        nonlocal current_topic, current_subtopics, topic_blocks
        if current_topic and current_subtopics:
            topic_blocks.append({"topic": current_topic, "subtopics": current_subtopics})
        current_subtopics = []

    for line in raw_lines:
        # Ignore noise
        if IGNORE_PATTERN.match(line):
            continue

        # If we were waiting for a page number for a subtopic
        if pending_subtopic_title is not None:
            if line.isdigit():
                # line is page number
                page = int(line)
                if current_topic is None:
                    flush_topic()
                if current_topic is None:
                    current_topic = "Unknown Topic"

                current_subtopics.append({"title": pending_subtopic_title, "page": page})
                pending_subtopic_title = None
                continue
            else:
                # still part of title (multi-line subtopic)
                pending_subtopic_title = (pending_subtopic_title + " " + line).strip()
                continue

        # Check subtopic on same line (title + page)
        m = SUBTOPIC_SAME_LINE.match(line)
        if m:
            title = m.group("title").strip()
            page = int(m.group("page"))

            # subtopic should contain digits like "1.2" etc.
            if re.search(r"\d", title):
                if current_topic is None:
                    flush_topic()
                if current_topic is None:
                    current_topic = "Unknown Topic"

                current_subtopics.append({"title": title, "page": page})
                continue

        # Check numbered subtopic without page (page on next line)
        if SUBTOPIC_NUMBERED_ONLY.match(line):
            pending_subtopic_title = line.strip()
            continue

        # Otherwise treat as TOPIC line
        # If we already have topic and subtopics, new topic starts -> finalize old
        if current_topic is not None and current_subtopics:
            finalize_current_topic()
            current_topic = None

        # Collect topic in buffer (supports multi-line topic names)
        topic_buffer.append(line)

        # If buffer grows too much, we flush anyway (safety)
        if len(topic_buffer) > 5:
            flush_topic()

    # End of loop flush pending buffers
    if pending_subtopic_title is not None:
        # If we never found a page, store with page -1 (or skip)
        # Here we skip to avoid wrong data
        pending_subtopic_title = None

    # finalize topic buffer
    if current_topic is None:
        flush_topic()

    # finalize last topic
    if current_topic and current_subtopics:
        topic_blocks.append({"topic": current_topic, "subtopics": current_subtopics})

    return topic_blocks