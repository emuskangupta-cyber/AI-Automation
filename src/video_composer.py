"""
video_composer.py — FIXED
--------------------------
Root cause fixes:
1. Title cards now include a silent audio track so all clips have audio streams
2. concat_clips re-encodes instead of stream-copy so audio is never dropped
3. make_clip explicitly maps both video and audio streams
4. import re moved to top
"""

import json
import os
import re
import subprocess
from pathlib import Path

FINAL_VIDEO = "output/lecture_final.mp4"


def audio_duration(path: str) -> float:
    r = subprocess.run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path
    ], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def make_clip(slides_dir: str, audio_path: str, out: str):
    """Combine PNG slides + MP3 audio into a single MP4 clip with audio."""
    slides = sorted(Path(slides_dir).glob("*.png"))
    if not slides:
        raise FileNotFoundError(f"No PNGs in {slides_dir}")
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    dur = audio_duration(audio_path)
    per = dur / len(slides)

    # Build image concat list
    concat = Path(out).parent / f"_img_{Path(out).stem}.txt"
    lines  = []
    for s in slides:
        lines += [f"file '{s.resolve()}'", f"duration {per:.4f}"]
    # repeat last frame to avoid ffmpeg truncation
    lines.append(f"file '{slides[-1].resolve()}'")
    concat.write_text("\n".join(lines), encoding="utf-8")

    result = subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat),  # video: image slideshow
        "-i", audio_path,                                   # audio: mp3
        "-map", "0:v:0",                                    # explicitly map video
        "-map", "1:a:0",                                    # explicitly map audio
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
               "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black",
        "-r", "24", "-pix_fmt", "yuv420p",
        "-shortest",   # end when shortest stream ends (audio)
        out
    ], capture_output=True, text=True)

    concat.unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg make_clip failed:\n{result.stderr[-1000:]}")

    print(f"   ✅ Clip: {Path(out).name}  ({dur:.1f}s)")


def title_card(text: str, secs: float, out: str, color: str = "00d4ff"):
    """
    Generate a title card MP4 WITH a silent audio track.
    This is critical — without audio stream, concat drops audio from all clips.
    """
    safe_text = text.replace("'", "").replace(":", " ").replace('"', '')

    # video + silent audio combined
    cmd = [
        "ffmpeg", "-y",
        # video source: solid colour
        "-f", "lavfi", "-i", f"color=0x0a0d14:size=1920x1080:rate=24:duration={secs}",
        # audio source: silence
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={secs}",
        "-map", "0:v", "-map", "1:a",
        "-vf", (
            f"drawtext=text='{safe_text}':"
            f"fontsize=54:fontcolor=0x{color}:"
            "x=(w-text_w)/2:y=(h-text_h)/2:"
            "fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        ),
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-t", str(secs),
        out
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        # Fallback: no custom font path
        cmd[cmd.index("fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")] = \
            f"drawtext=text='{safe_text}':fontsize=54:fontcolor=0x{color}:x=(w-text_w)/2:y=(h-text_h)/2"
        # Rebuild without fontfile
        vf = f"drawtext=text='{safe_text}':fontsize=54:fontcolor=0x{color}:x=(w-text_w)/2:y=(h-text_h)/2"
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=0x0a0d14:size=1920x1080:rate=24:duration={secs}",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={secs}",
            "-map", "0:v", "-map", "1:a",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            "-pix_fmt", "yuv420p",
            "-t", str(secs),
            out
        ], check=True)


def concat_clips(clips: list, out: str):
    """
    Concatenate all clips into final video.
    Re-encodes audio (NOT stream copy) so audio is never silently dropped.
    All input clips must have both video AND audio streams (title_card now ensures this).
    """
    lst = Path(out).parent / "_final_list.txt"
    lst.write_text(
        "\n".join(f"file '{Path(c).resolve()}'" for c in clips),
        encoding="utf-8"
    )

    result = subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",          # re-encode audio — never drops it
        "-pix_fmt", "yuv420p",
        out
    ], capture_output=True, text=True)

    lst.unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg concat failed:\n{result.stderr[-2000:]}")

    size_mb = Path(out).stat().st_size / (1024 * 1024)
    print(f"   ✅ Concatenated {len(clips)} clips → {Path(out).name} ({size_mb:.1f} MB)")


def compose_lecture(
    plan_path: str = "output/json/lecture_plan.json",
    clips_dir: str = "output/clips",
    final_out: str = FINAL_VIDEO,
    title:     str = "AI Lecture"
) -> str:

    if not Path(plan_path).exists():
        raise FileNotFoundError(f"Not found: {plan_path}")

    Path(clips_dir).mkdir(parents=True, exist_ok=True)
    Path(final_out).parent.mkdir(parents=True, exist_ok=True)

    data      = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    subtopics = data.get("subtopics", [])
    all_clips = []

    # ── Intro card ────────────────────────────────────────────
    intro = str(Path(clips_dir) / "00_intro.mp4")
    print("🎬 Generating intro card…")
    title_card(title.replace("'", ""), 5, intro)
    all_clips.append(intro)

    # ── Per-group clips ───────────────────────────────────────
    for i, st in enumerate(subtopics):
        safe = re.sub(r"[^\w\-]", "_", st.get("title", f"group_{i}"))[:50]
        clip = str(Path(clips_dir) / f"{i+1:02d}_{safe}.mp4")

        # Reuse existing clip
        if Path(clip).exists() and Path(clip).stat().st_size > 10000:
            print(f"⏭  Exists: {Path(clip).name}")
            st["clip_path"] = clip
            all_clips.append(clip)
            continue

        audio  = st.get("audio_path")
        slides = st.get("slides_dir")

        if not audio or not Path(str(audio)).exists():
            print(f"⚠  [{i+1}] No audio — skipping: {st.get('title')}")
            continue
        if not slides or not Path(str(slides)).exists():
            print(f"⚠  [{i+1}] No slides — skipping: {st.get('title')}")
            continue

        # Check PNGs actually exist
        png_files = sorted(Path(slides).glob("*.png"))
        if not png_files:
            print(f"⚠  [{i+1}] No PNG files in {slides} — skipping")
            continue

        print(f"\n🎬 [{i+1}/{len(subtopics)}] {st.get('title')}  ({len(png_files)} slides)")
        try:
            make_clip(slides, audio, clip)
            st["clip_path"] = clip
            all_clips.append(clip)
        except Exception as e:
            print(f"   ❌ Failed: {e}")

    # ── Outro card ────────────────────────────────────────────
    outro = str(Path(clips_dir) / "99_outro.mp4")
    print("\n🎬 Generating outro card…")
    title_card("Thank You", 3, outro, color="00ff9d")
    all_clips.append(outro)

    content_clips = len(all_clips) - 2  # exclude intro and outro
    if content_clips == 0:
        raise RuntimeError("No content clips generated — check audio_path and slides_dir in lecture_plan.json")

    print(f"\n🎞  Concatenating {len(all_clips)} clips ({content_clips} content + intro + outro)…")
    concat_clips(all_clips, final_out)

    data["subtopics"]  = subtopics
    data["final_video"] = final_out
    Path(plan_path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    size_mb = Path(final_out).stat().st_size / (1024 * 1024)
    print(f"\n✅ Final video ready: {final_out}  ({size_mb:.1f} MB)")
    return final_out


if __name__ == "__main__":
    compose_lecture()