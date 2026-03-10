"""
script_generator.py  — FIXED
------------------------------
Fixes:
1. Target word count = duration_secs / 60 * 130 (was being underestimated)
2. Tells Claude explicitly: write EXACTLY N words — this controls audio/video length
3. Handles merged subtopics — passes all merged content to Claude
4. Validates script length — retries if too short
"""

import json
import os
import time
from pathlib import Path
import anthropic

WORDS_PER_MIN  = 130
MODEL          = "claude-sonnet-4-20250514"
MIN_WORD_RATIO = 0.7   # retry if script < 70% of target words

SYSTEM = """You are an experienced university professor delivering a live lecture.
Convert the provided textbook content into a natural spoken lecture script.

STRICT RULES:
- Output ONLY the spoken words. No markdown, no bullet points, no headers, no numbering.
- Use natural speech: contractions, "Now", "Notice that", "Let's look at", "This is important"
- Short to medium sentences — easy to follow when heard
- Spoken transitions: "Building on that...", "So what does this mean?", "Here's the key point"
- Do NOT start with "In this lecture" or "Today we will cover"
- Do NOT end with "In summary" or "Thank you"
- You MUST write close to the target word count — this directly controls the video length
- Keep explaining, give examples, elaborate — fill the full time"""


def generate_script(
    topic:        str,
    title:        str,
    content:      str,
    duration_secs: int,
    merged_from:  list = None,
    retries:      int  = 2
) -> str:

    target_words = round((duration_secs / 60) * WORDS_PER_MIN)

    if merged_from and len(merged_from) > 1:
        coverage = f"This section covers these subtopics: {', '.join(merged_from)}"
    else:
        coverage = f"Subtopic: {title}"

    prompt = f"""Topic: {topic}
{coverage}
Target word count: {target_words} words (this = {duration_secs//60} minutes of spoken audio)

Textbook content:
---
{content[:5000]}
---

Write a spoken lecture script of EXACTLY ~{target_words} words.
Keep talking, elaborate, give examples — you must fill {duration_secs//60} full minutes:"""

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    for attempt in range(retries + 1):
        try:
            resp   = client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=SYSTEM,
                messages=[{"role": "user", "content": prompt}]
            )
            script = resp.content[0].text.strip()
            wc     = len(script.split())

            print(f"   → Attempt {attempt+1}: {wc} words (target: {target_words})")

            # Accept if at least 70% of target
            if wc >= target_words * MIN_WORD_RATIO:
                return script

            # Too short — retry with stronger instruction
            if attempt < retries:
                print(f"   ⚠ Too short ({wc} words) — retrying with more emphasis…")
                prompt = prompt.replace(
                    "Keep talking, elaborate",
                    f"IMPORTANT: Your last attempt was only {wc} words. You MUST write {target_words} words. Keep elaborating"
                )
                time.sleep(1)

        except Exception as e:
            print(f"   ❌ API error attempt {attempt+1}: {e}")
            if attempt < retries:
                time.sleep(3)

    # Fallback — use raw content repeated/expanded
    print(f"   ⚠ Using raw content as fallback for: {title}")
    return content[:6000]


def generate_all_scripts(
    plan_path:   str = "output/json/lecture_plan.json",
    scripts_dir: str = "output/scripts"
) -> str:

    if not Path(plan_path).exists():
        raise FileNotFoundError(f"lecture_plan.json not found: {plan_path}")

    Path(scripts_dir).mkdir(parents=True, exist_ok=True)
    data      = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    subtopics = data.get("subtopics", [])

    if not subtopics:
        raise ValueError("No subtopics in lecture_plan.json — run lecture_planner.py first")

    print(f"\n✍  Generating scripts for {len(subtopics)} groups…")

    for i, st in enumerate(subtopics):
        title        = st.get("title", f"group_{i}")
        duration_secs = st.get("duration_secs", 300)
        target_words  = round((duration_secs / 60) * WORDS_PER_MIN)

        safe      = title.replace(" ", "_").replace("/", "-").replace("+", "plus")[:60]
        out_file  = Path(scripts_dir) / f"{i:02d}_{safe}.txt"

        if out_file.exists():
            existing_wc = len(out_file.read_text(encoding="utf-8").split())
            if existing_wc >= target_words * MIN_WORD_RATIO:
                print(f"⏭  [{i+1}/{len(subtopics)}] Exists ({existing_wc}w): {out_file.name}")
                st["script_path"] = str(out_file)
                st["script"]      = out_file.read_text(encoding="utf-8")
                continue
            else:
                print(f"🔁 [{i+1}/{len(subtopics)}] Re-generating (too short: {existing_wc}w, need {target_words}w)")

        print(f"\n✍  [{i+1}/{len(subtopics)}] {title}")
        print(f"   Duration: {duration_secs}s → target {target_words} words")

        script = generate_script(
            topic         = st.get("topic", ""),
            title         = title,
            content       = st.get("content", ""),
            duration_secs = duration_secs,
            merged_from   = st.get("merged_from", [title])
        )

        out_file.write_text(script, encoding="utf-8")
        st["script"]      = script
        st["script_path"] = str(out_file)
        wc = len(script.split())
        print(f"   ✅ Saved: {out_file.name} ({wc} words)")
        time.sleep(0.5)

    data["subtopics"] = subtopics
    Path(plan_path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n✅ All scripts saved → {scripts_dir}")
    return plan_path


if __name__ == "__main__":
    generate_all_scripts()