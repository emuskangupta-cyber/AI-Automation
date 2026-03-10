"""
tts_generator.py  — FIXED
--------------------------
Fixes:
1. Splits long scripts into chunks (edge-tts fails on long text)
2. Merges chunks with FFmpeg into one MP3
3. Verifies output file is non-empty
4. Falls back to raw content if script missing
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path

VOICE    = "en-US-AriaNeural"   # free Microsoft neural voice
MAX_CHARS = 3000                  # edge-tts limit per call


async def _speak(text: str, out_path: str):
    import edge_tts
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(out_path)


def tts_chunk(text: str, out_path: str):
    asyncio.run(_speak(text, out_path))


def tts_with_merge(text: str, out_path: str):
    # Clean markdown artifacts
    text = re.sub(r"\*+", "", text)
    text = re.sub(r"#+\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        raise ValueError("Empty text")

    if len(text) <= MAX_CHARS:
        tts_chunk(text, out_path)
        _verify(out_path)
        return

    # Split into sentence chunks under MAX_CHARS
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks    = []
    current   = ""
    for s in sentences:
        if len(current) + len(s) + 1 <= MAX_CHARS:
            current = (current + " " + s).strip()
        else:
            if current:
                chunks.append(current)
            current = s
    if current:
        chunks.append(current)

    if len(chunks) == 1:
        tts_chunk(chunks[0], out_path)
        _verify(out_path)
        return

    tmp_dir   = Path(out_path).parent / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_files = []

    for idx, chunk in enumerate(chunks):
        tmp = str(tmp_dir / f"c{idx:03d}.mp3")
        print(f"   chunk {idx+1}/{len(chunks)}…")
        tts_chunk(chunk, tmp)
        if Path(tmp).exists() and Path(tmp).stat().st_size > 500:
            tmp_files.append(tmp)

    if not tmp_files:
        raise RuntimeError("All TTS chunks failed")

    if len(tmp_files) == 1:
        import shutil
        shutil.copy(tmp_files[0], out_path)
    else:
        lst = tmp_dir / "list.txt"
        lst.write_text("\n".join(f"file '{Path(f).resolve()}'" for f in tmp_files))
        os.system(f'ffmpeg -y -f concat -safe 0 -i "{lst}" -c copy "{out_path}" -loglevel error')

    # Cleanup
    for f in tmp_files:
        try: Path(f).unlink()
        except: pass
    try: (tmp_dir / "list.txt").unlink()
    except: pass
    try: tmp_dir.rmdir()
    except: pass

    _verify(out_path)


def _verify(path: str):
    if not Path(path).exists() or Path(path).stat().st_size < 1000:
        raise RuntimeError(f"Audio output missing or too small: {path}")


def generate_all_audio(
    plan_path: str = "output/json/lecture_plan.json",
    audio_dir: str = "output/audio"
):
    if not Path(plan_path).exists():
        raise FileNotFoundError(f"Not found: {plan_path}")

    Path(audio_dir).mkdir(parents=True, exist_ok=True)
    data      = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    subtopics = data.get("subtopics", [])

    print(f"\n🔊 Generating audio for {len(subtopics)} groups (voice: {VOICE})")

    for i, st in enumerate(subtopics):
        title = st.get("title", f"group_{i}")
        safe  = re.sub(r"[^\w\-]", "_", title)[:60]
        apath = str(Path(audio_dir) / f"{i:02d}_{safe}.mp3")

        # Skip if already done
        if Path(apath).exists() and Path(apath).stat().st_size > 2000:
            print(f"⏭  [{i+1}/{len(subtopics)}] Exists: {Path(apath).name}")
            st["audio_path"] = apath
            continue

        # Get script text
        script = None
        if st.get("script") and len(str(st["script"]).strip()) > 50:
            script = str(st["script"]).strip()
        elif st.get("script_path") and Path(st["script_path"]).exists():
            script = Path(st["script_path"]).read_text(encoding="utf-8").strip()
        if not script and st.get("content"):
            script = str(st["content"]).strip()
            print(f"   ⚠ No script — using raw content")

        if not script:
            print(f"   ❌ [{i+1}] Nothing to speak for: {title}")
            st["audio_path"] = None
            continue

        wc = len(script.split())
        print(f"\n🔊 [{i+1}/{len(subtopics)}] {title}  ({wc} words)")

        try:
            tts_with_merge(script, apath)
            size_kb = Path(apath).stat().st_size // 1024
            print(f"   ✅ {Path(apath).name}  ({size_kb} KB)")
            st["audio_path"] = apath
        except Exception as e:
            print(f"   ❌ TTS failed: {e}")
            st["audio_path"] = None

    data["subtopics"] = subtopics
    Path(plan_path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    done = sum(1 for s in subtopics if s.get("audio_path"))
    print(f"\n✅ Audio done: {done}/{len(subtopics)} → {audio_dir}")

    if done == 0:
        print("\n❌ No audio generated. Check:")
        print("   pip install edge-tts")
        print("   Test: python -c \"import asyncio,edge_tts; asyncio.run(edge_tts.Communicate('hello','en-US-AriaNeural').save('test.mp3'))\"")


if __name__ == "__main__":
    generate_all_audio()