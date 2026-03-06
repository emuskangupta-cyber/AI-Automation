import re
import json
import random
from pathlib import Path
from typing import List, Dict

# Small stopword list to avoid extra deps (fast)
STOPWORDS = set("""
a an and are as at be been but by can could did do does doing done for from had has have having he her hers him his how i if in into is it its may might more most must my no not of on or our ours out over should so some such than that the their theirs them then there these they this those to too up was we were what when where which who why will with would you your yours
""".split())

WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]{2,}")  # words length>=3, allow hyphen


def safe_filename(s: str, max_len: int = 120) -> str:
    s = re.sub(r"[^\w\s\-\.]", "", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s[:max_len] if len(s) > max_len else s


def split_sentences(text: str) -> List[str]:
    # Simple sentence split (fast)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    # split on . ? ! (keep basic)
    sents = re.split(r"(?<=[\.\?\!])\s+", text)
    # keep reasonable-length sentences
    out = []
    for s in sents:
        s = s.strip()
        if 40 <= len(s) <= 220:  # good length for MCQ
            out.append(s)
    return out


def extract_keywords(text: str, top_k: int = 50) -> List[str]:
    # word frequency keywords
    words = WORD_RE.findall(text.lower())
    freq = {}
    for w in words:
        if w in STOPWORDS:
            continue
        if len(w) < 3:
            continue
        freq[w] = freq.get(w, 0) + 1

    # sort by frequency then length
    ranked = sorted(freq.items(), key=lambda x: (x[1], len(x[0])), reverse=True)
    keywords = [w for w, _ in ranked[:top_k]]
    return keywords


def make_cloze_question(sentence: str, answer: str) -> str:
    # replace only first occurrence (case-insensitive)
    pattern = re.compile(re.escape(answer), re.IGNORECASE)
    return pattern.sub("______", sentence, count=1)


def build_options(answer: str, pool: List[str], k: int = 4) -> List[str]:
    # 1 correct + 3 distractors
    distractors = [p for p in pool if p.lower() != answer.lower()]
    random.shuffle(distractors)
    opts = [answer] + distractors[:k-1]
    # if not enough distractors, pad with generic
    while len(opts) < k:
        opts.append("None of the above")
    random.shuffle(opts)
    return opts


def generate_mcqs_for_text(text: str, n: int = 10, seed: int = 7) -> List[Dict]:
    random.seed(seed)

    # Speed guard: limit text to keep whole run <2 min
    text = text.strip()
    if len(text) > 12000:
        text = text[:12000]

    sentences = split_sentences(text)
    keywords = extract_keywords(text, top_k=80)

    if not sentences or not keywords:
        return []

    # Build candidate (sentence, keyword) pairs
    pairs = []
    for kw in keywords:
        # find first sentences containing this keyword
        for s in sentences:
            if re.search(rf"\b{re.escape(kw)}\b", s, flags=re.IGNORECASE):
                # avoid very short keyword in cloze
                if len(kw) >= 4:
                    pairs.append((s, kw))
                break

    # Shuffle for variety
    random.shuffle(pairs)

    questions = []
    used = set()

    for (sent, kw) in pairs:
        key = (sent, kw)
        if key in used:
            continue
        used.add(key)

        q_text = make_cloze_question(sent, kw)
        options = build_options(kw, keywords, k=4)

        correct_index = options.index(kw)

        questions.append({
            "question": f"Fill in the blank:\n{q_text}",
            "options": options,
            "answer": kw,
            "answer_index": correct_index
            
        })

        if len(questions) >= n:
            break

    # If still fewer than n, create extra keyword-definition style from remaining keywords
    # (very simple and fast)
    i = 0
    while len(questions) < n and i < len(keywords):
        kw = keywords[i]
        i += 1
        if len(kw) < 4:
            continue
        # Find any sentence for it
        sent = next((s for s in sentences if re.search(rf"\b{re.escape(kw)}\b", s, re.I)), None)
        if not sent:
            continue
        q_text = f"Which term best fits this statement?\n{make_cloze_question(sent, kw)}"
        options = build_options(kw, keywords, k=4)
        questions.append({
            
            "question": q_text,
            "options": options,
            "answer": kw,
            "answer_index": options.index(kw)
        })

    return questions[:n]


def save_mcqs_per_subtopic(topic_subtopic_json_path: str, out_dir: str = "output/mcq", n_per_subtopic: int = 10) -> Dict:
    """
    Reads topic_subtopic_content.json and creates separate files for each subtopic:
      output/mcq/<topic>/<subtopic>.json
      output/mcq/<topic>/<subtopic>.txt

    Returns summary dict.
    """
    out_base = Path(out_dir)
    out_base.mkdir(parents=True, exist_ok=True)

    data = json.loads(Path(topic_subtopic_json_path).read_text(encoding="utf-8"))

    total_subtopics = 0
    total_questions = 0
    empty_subtopics = 0

    for topic_obj in data:
        topic = topic_obj.get("topic", "Unknown Topic")
        subtopics = topic_obj.get("subtopics", [])

        topic_folder = out_base / safe_filename(topic)
        topic_folder.mkdir(parents=True, exist_ok=True)

        for st in subtopics:
            total_subtopics += 1
            title = st.get("title", "Unknown Subtopic")
            content = st.get("content", "") or ""

            DPP = generate_mcqs_for_text(content, n=n_per_subtopic, seed=total_subtopics + 7)

            if not DPP:
                empty_subtopics += 1

            total_questions += len(DPP)

            base_name = safe_filename(title)
            json_path = topic_folder / f"{base_name}.json"
            txt_path = topic_folder / f"{base_name}.txt"

            json_path.write_text(json.dumps({
                "topic": topic,
                "subtopic": title,
                "start_page": st.get("start_page"),
                "end_page": st.get("end_page"),
                "questions": DPP
            }, indent=2, ensure_ascii=False), encoding="utf-8")

            # Optional TXT (easy to read)
            lines = []
            lines.append(f"Topic: {topic}")
            lines.append(f"Subtopic: {title}")
            lines.append("")
            for i, q in enumerate(DPP, start=1):
                lines.append(f"Q{i}. {q['question']}")
                for j, opt in enumerate(q["options"]):
                    tag = " (Correct)" if opt == q["answer"] else ""
                    lines.append(f"   {chr(65+j)}. {opt}{tag}")
                lines.append("")
            txt_path.write_text("\n".join(lines), encoding="utf-8")

    return {
        "out_dir": str(out_base),
        "total_topics": len(data),
        "total_subtopics": total_subtopics,
        "total_questions": total_questions,
        "empty_subtopics": empty_subtopics
    }


# ============================================================
# ✅ NEW: Attach MCQs inside the JSON (does NOT break old flow)
# ============================================================
def attach_mcqs_to_topic_json(topic_data: list, n_per_subtopic: int = 10, seed: int = 7) -> list:
    """
    Adds mcqs[] inside each subtopic object.

    ✅ Does NOT modify or remove old functions
    ✅ Uses same generate_mcqs_for_text() logic
    ✅ You can still call save_mcqs_per_subtopic() as before
    """
    counter = 0
    for topic_obj in topic_data:
        subtopics = topic_obj.get("subtopics", [])
        for st in subtopics:
            counter += 1
            content = (st.get("content") or "").strip()
            DPP = generate_mcqs_for_text(content, n=n_per_subtopic, seed=seed + counter)
            st["DPP"] = DPP
    return topic_data