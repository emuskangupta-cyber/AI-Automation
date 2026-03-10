"""
lecture_planner.py
------------------
Two modes:

1. PREVIEW mode  (preview_plan)
   - Reads topic_subtopic_content.json
   - Shows complete lecture series: how many lectures, which subtopics in each
   - Saves lecture_series.json  (full series overview)
   - Does NOT start any video generation

2. PLAN mode  (plan_lecture)
   - Picks groups for a specific lecture number
   - Saves lecture_plan.json  (used by video pipeline)
"""

import json
import re
import sys
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────────────
MINUTES_PER_LECTURE     = 60
MIN_WORDS_PER_GROUP     = 200
MAX_WORDS_PER_GROUP     = 800
MAX_SUBTOPICS_PER_GROUP = 3
SKIP_IF_BELOW_WORDS     = 10
INTRO_SECS              = 6
OUTRO_SECS              = 4
MIN_SECS_PER_GROUP      = 300   # 5 min minimum per group
MAX_SECS_PER_GROUP      = 600   # 10 min maximum per group
# ─────────────────────────────────────────────────────────────────────────────


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text or ""))


def merge_subtopics(all_subtopics: list) -> list:
    groups = []
    i = 0
    while i < len(all_subtopics):
        st      = all_subtopics[i]
        content = (st.get("content") or "").strip()
        wc      = word_count(content)

        if wc < SKIP_IF_BELOW_WORDS:
            i += 1
            continue

        topic_name      = st.get("topic", "")
        merged_titles   = [st.get("title", "")]
        merged_contents = [content]
        merged_wc       = wc
        start_page      = st.get("start_page")
        end_page        = st.get("end_page")
        j               = i + 1

        while (
            j < len(all_subtopics)
            and merged_wc < MIN_WORDS_PER_GROUP
            and len(merged_titles) < MAX_SUBTOPICS_PER_GROUP
        ):
            nxt = all_subtopics[j]
            nc  = (nxt.get("content") or "").strip()
            nw  = word_count(nc)
            if nxt.get("topic", "") != topic_name:
                break
            if nw < SKIP_IF_BELOW_WORDS:
                j += 1
                continue
            merged_titles.append(nxt.get("title", ""))
            merged_contents.append(nc)
            merged_wc  += nw
            end_page    = nxt.get("end_page", end_page)
            j          += 1
            if merged_wc >= MAX_WORDS_PER_GROUP:
                break

        if len(merged_titles) > 1:
            merged_title   = " + ".join(t.strip() for t in merged_titles)
            merged_content = "\n\n".join(merged_contents)
        else:
            merged_title   = merged_titles[0]
            merged_content = merged_contents[0]

        groups.append({
            "topic":       topic_name,
            "title":       merged_title,
            "content":     merged_content,
            "raw_words":   merged_wc,
            "merged_from": merged_titles,
            "start_page":  start_page,
            "end_page":    end_page,
        })
        i = j

    return groups


def assign_durations(groups: list, usable_secs: float) -> list:
    """Assign duration_secs to each group proportionally, with min/max clamps."""
    total_words = sum(g["raw_words"] for g in groups)
    result = []
    for g in groups:
        proportion = g["raw_words"] / max(total_words, 1)
        raw_secs   = proportion * usable_secs * len(groups)
        dur        = max(MIN_SECS_PER_GROUP, min(MAX_SECS_PER_GROUP, raw_secs))
        result.append({**g, "duration_secs": round(dur)})
    return result


def split_into_lectures(groups: list, minutes_per_lecture: int = MINUTES_PER_LECTURE) -> list:
    """Pack groups into lectures. Each lecture fills ~minutes_per_lecture minutes."""
    usable_secs  = (minutes_per_lecture * 60) - INTRO_SECS - OUTRO_SECS
    total_words  = sum(g["raw_words"] for g in groups)

    lectures     = []
    current      = []
    current_secs = 0.0
    lecture_num  = 1

    for g in groups:
        proportion = g["raw_words"] / max(total_words, 1)
        raw_secs   = proportion * usable_secs * len(groups)
        dur        = max(MIN_SECS_PER_GROUP, min(MAX_SECS_PER_GROUP, raw_secs))

        if current_secs + dur <= usable_secs + 30:
            current.append({**g, "duration_secs": round(dur)})
            current_secs += dur
        else:
            if current:
                lectures.append({
                    "lecture_num":  lecture_num,
                    "total_secs":   INTRO_SECS + round(current_secs) + OUTRO_SECS,
                    "total_mins":   round((INTRO_SECS + current_secs + OUTRO_SECS) / 60, 1),
                    "groups":       current
                })
                lecture_num += 1
            current      = [{**g, "duration_secs": round(dur)}]
            current_secs = dur

    if current:
        lectures.append({
            "lecture_num":  lecture_num,
            "total_secs":   INTRO_SECS + round(current_secs) + OUTRO_SECS,
            "total_mins":   round((INTRO_SECS + current_secs + OUTRO_SECS) / 60, 1),
            "groups":       current
        })

    return lectures


def preview_plan(
    json_path:           str = "output/json/topic_subtopic_content.json",
    minutes_per_lecture: int = MINUTES_PER_LECTURE,
    save_path:           str = "output/json/lecture_series.json"
) -> list:
    """
    PREVIEW MODE:
    - Reads full index
    - Computes all lectures across entire book
    - Prints full plan to terminal
    - Saves lecture_series.json
    """
    if not Path(json_path).exists():
        print(f"❌ Not found: {json_path}")
        print("   Run PDF extraction first: python src/main.py")
        return []

    data = json.loads(Path(json_path).read_text(encoding="utf-8"))

    all_subtopics = []
    for topic_obj in data:
        topic_name = topic_obj.get("topic", "Unknown")
        for st in topic_obj.get("subtopics", []):
            entry          = dict(st)
            entry["topic"] = topic_name
            all_subtopics.append(entry)

    total_raw_words = sum(word_count(s.get("content", "")) for s in all_subtopics)
    groups          = merge_subtopics(all_subtopics)
    lectures        = split_into_lectures(groups, minutes_per_lecture)

    # ── PRINT TO TERMINAL ────────────────────────────────────────────────────
    W = 72
    print("\n" + "═" * W)
    print(f"  📚  COMPLETE LECTURE SERIES PLAN")
    print("═" * W)
    print(f"  Raw subtopics  : {len(all_subtopics)}")
    print(f"  Total words    : {total_raw_words:,}")
    print(f"  Groups (merged): {len(groups)}")
    print(f"  Total lectures : {len(lectures)}  ({minutes_per_lecture} min each)")
    print(f"  Total duration : ~{len(lectures) * minutes_per_lecture} min  (~{round(len(lectures)*minutes_per_lecture/60,1)} hours)")
    print("═" * W)

    for lec in lectures:
        lnum  = lec["lecture_num"]
        lmins = lec["total_mins"]
        lgrps = lec["groups"]
        total = len(lectures)

        print(f"\n  ┌{'─'*(W-2)}┐")
        header = f"  LECTURE {lnum:02d}/{total:02d}   {lmins} min   ({len(lgrps)} sections)"
        print(f"  │{header:<{W-2}}│")
        print(f"  ├{'─'*(W-2)}┤")

        current_topic = None
        for idx, g in enumerate(lgrps):
            topic = g["topic"]
            if topic != current_topic:
                current_topic = topic
                print(f"  │  📖 {topic[:W-8]:<{W-8}}│")

            mins     = g["duration_secs"] // 60
            secs_rem = g["duration_secs"] % 60
            time_str = f"{mins}m{secs_rem:02d}s"
            wc_str   = f"{g['raw_words']}w"

            if len(g["merged_from"]) > 1:
                label = "(merged) " + g["title"]
                print(f"  │    [{idx+1:02d}] {label[:W-22]:<{W-22}} {time_str:>6} {wc_str:>5}  │")
                for sub in g["merged_from"]:
                    print(f"  │          • {sub[:W-14]:<{W-14}}│")
            else:
                print(f"  │    [{idx+1:02d}] {g['title'][:W-22]:<{W-22}} {time_str:>6} {wc_str:>5}  │")

        print(f"  └{'─'*(W-2)}┘")

    print(f"\n{'═'*W}")
    print(f"  ✅ {len(lectures)} lectures  ×  ~{minutes_per_lecture} min  =  ~{len(lectures)*minutes_per_lecture} min total")
    print(f"{'═'*W}")
    print(f"\n  To generate a specific lecture video, use:")
    print(f"  POST /generate-lecture/?lecture_num=1")
    print(f"  (valid range: 1 – {len(lectures)})\n")

    # ── SAVE lecture_series.json ─────────────────────────────────────────────
    series = {
        "total_lectures":     len(lectures),
        "minutes_per_lecture": minutes_per_lecture,
        "total_raw_subtopics": len(all_subtopics),
        "total_words":         total_raw_words,
        "total_groups":        len(groups),
        "estimated_total_mins": len(lectures) * minutes_per_lecture,
        "lectures": [
            {
                "lecture_num":  lec["lecture_num"],
                "total_mins":   lec["total_mins"],
                "total_secs":   lec["total_secs"],
                "section_count": len(lec["groups"]),
                "sections": [
                    {
                        "order":        idx + 1,
                        "topic":        g["topic"],
                        "title":        g["title"],
                        "merged_from":  g["merged_from"],
                        "raw_words":    g["raw_words"],
                        "duration_secs": g["duration_secs"],
                        "duration_str": f"{g['duration_secs']//60}m{g['duration_secs']%60:02d}s",
                    }
                    for idx, g in enumerate(lec["groups"])
                ]
            }
            for lec in lectures
        ]
    }

    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    Path(save_path).write_text(
        json.dumps(series, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  💾 Saved full series plan → {save_path}\n")

    return lectures


def plan_lecture(
    json_path:      str = "output/json/topic_subtopic_content.json",
    target_minutes: int = MINUTES_PER_LECTURE,
    output_path:    str = "output/json/lecture_plan.json",
    lecture_num:    int = 1
) -> dict:
    """
    PLAN MODE:
    Pick groups for lecture_num, save lecture_plan.json for video pipeline.
    """
    if not Path(json_path).exists():
        raise FileNotFoundError(f"Not found: {json_path}")

    data = json.loads(Path(json_path).read_text(encoding="utf-8"))

    all_subtopics = []
    for topic_obj in data:
        topic_name = topic_obj.get("topic", "Unknown")
        for st in topic_obj.get("subtopics", []):
            entry          = dict(st)
            entry["topic"] = topic_name
            all_subtopics.append(entry)

    groups   = merge_subtopics(all_subtopics)
    lectures = split_into_lectures(groups, target_minutes)

    if lecture_num < 1 or lecture_num > len(lectures):
        raise ValueError(f"Lecture {lecture_num} out of range. Valid: 1–{len(lectures)}")

    chosen = lectures[lecture_num - 1]
    plan   = []
    used   = 0.0

    for order, g in enumerate(chosen["groups"]):
        dur   = g["duration_secs"]
        start = INTRO_SECS + round(used)
        plan.append({
            "order":         order,
            "topic":         g["topic"],
            "title":         g["title"],
            "content":       g["content"],
            "merged_from":   g["merged_from"],
            "raw_words":     g["raw_words"],
            "duration_secs": dur,
            "start_secs":    start,
            "end_secs":      start + dur,
            "start_page":    g.get("start_page"),
            "end_page":      g.get("end_page"),
            "script":        None,
            "script_path":   None,
            "audio_path":    None,
            "slides_dir":    None,
            "clip_path":     None,
        })
        used += dur

    total_secs = INTRO_SECS + round(used) + OUTRO_SECS
    result = {
        "lecture_num":    lecture_num,
        "total_lectures": len(lectures),
        "target_minutes": target_minutes,
        "total_groups":   len(plan),
        "total_secs":     total_secs,
        "total_mins":     round(total_secs / 60, 1),
        "subtopics":      plan,
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\n✅ Lecture {lecture_num}/{len(lectures)}: {len(plan)} sections → {result['total_mins']} min")
    print("─" * 65)
    for p in plan:
        tag  = f"  ← {len(p['merged_from'])} merged" if len(p["merged_from"]) > 1 else ""
        mins = p["duration_secs"] // 60
        secs = p["duration_secs"] % 60
        print(f"  [{p['order']+1:02d}] {p['title'][:45]:<45} {mins}m{secs:02d}s  {p['raw_words']}w{tag}")
    print("─" * 65)
    print(f"✅ Saved: {output_path}\n")
    return result


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--preview" in args or "-p" in args or not args:
        preview_plan()

    elif "--lecture" in args:
        idx = args.index("--lecture")
        num = int(args[idx + 1]) if idx + 1 < len(args) else 1
        plan_lecture(lecture_num=num)