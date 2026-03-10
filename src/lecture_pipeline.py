import os
from pathlib import Path

from lecture_planner import plan_lecture
from script_generator import generate_script
from slide_generator import generate_slides
from tts_generator import generate_voice

import asyncio


def run_lecture_pipeline(topic_json_path):

    lectures = plan_lecture(topic_json_path)

    out_dir = Path("output/lecture")
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, subtopics in enumerate(lectures):

        lecture_dir = out_dir / f"lecture_{i+1}"
        lecture_dir.mkdir(exist_ok=True)

        script = generate_script(subtopics)

        script_path = lecture_dir / "lecture_script.txt"
        script_path.write_text(script)

        slides_path = lecture_dir / "slides.pptx"
        generate_slides(script, slides_path)

        audio_path = lecture_dir / "voice.mp3"
        asyncio.run(generate_voice(script, audio_path))

        print("Lecture generated:", lecture_dir)