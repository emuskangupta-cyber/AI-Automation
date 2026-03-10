import os
import re
import shutil
import json
import uuid
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
import subprocess

UPLOAD_DIR  = Path("input")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_JSON   = Path("output/json/topic_subtopic_content.json")
SERIES_JSON   = Path("output/json/lecture_series.json")

lecture_jobs = {}   # job_id → {status, progress, step, error, output_path}

app = FastAPI()


# ════════════════════════════════════════════════════════════════
#  EXISTING ENDPOINTS  (unchanged)
# ════════════════════════════════════════════════════════════════

@app.get("/")
def home():
    return {"message": "PDF processing server running"}


@app.post("/upload-pdf/")
async def upload_pdf(file: UploadFile = File(...)):
    """Upload PDF → extract subtopics + MCQs → return JSON."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files allowed")

    file_path = UPLOAD_DIR / file.filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    env = os.environ.copy()
    env["PDF_PATH"] = str(file_path)
    subprocess.run(["python", "src/main.py"], check=True, env=env)

    if not OUTPUT_JSON.exists():
        raise HTTPException(status_code=500, detail="JSON output not generated")

    data = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    return {"filename": file.filename, "result": data}


# ════════════════════════════════════════════════════════════════
#  NEW ENDPOINT 1 — Preview full lecture series plan
# ════════════════════════════════════════════════════════════════

@app.post("/plan-series/")
async def plan_series(
    file: UploadFile = File(...),
    minutes_per_lecture: int = 60
):
    """
    Upload PDF → extract subtopics → compute how many lectures,
    which subtopics go in each lecture → return + save lecture_series.json.

    Does NOT generate any videos. Use this first to review the plan,
    then call /generate-lecture/?lecture_num=N to generate a specific one.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files allowed")

    # Save uploaded file
    file_path = UPLOAD_DIR / file.filename
    with open(file_path, "wb") as buf:
        shutil.copyfileobj(file.file, buf)

    # Step 1 — Run existing pipeline to extract subtopics
    env = os.environ.copy()
    env["PDF_PATH"] = str(file_path)
    subprocess.run(["python", "src/main.py"], check=True, env=env)

    if not OUTPUT_JSON.exists():
        raise HTTPException(status_code=500, detail="Subtopic extraction failed")

    # Step 2 — Preview full lecture plan (saves lecture_series.json)
    import sys
    sys.path.insert(0, "src")
    from lecture_planner import preview_plan

    lectures = preview_plan(
        json_path           = str(OUTPUT_JSON),
        minutes_per_lecture = minutes_per_lecture,
        save_path           = str(SERIES_JSON)
    )

    series = json.loads(SERIES_JSON.read_text(encoding="utf-8"))

    return {
        "message":            f"Plan ready: {series['total_lectures']} lectures across entire book",
        "total_lectures":     series["total_lectures"],
        "minutes_per_lecture": series["minutes_per_lecture"],
        "total_words":        series["total_words"],
        "estimated_total_mins": series["estimated_total_mins"],
        "lectures": [
            {
                "lecture_num":   lec["lecture_num"],
                "total_mins":    lec["total_mins"],
                "section_count": lec["section_count"],
                "sections": [
                    {
                        "order":         s["order"],
                        "title":         s["title"],
                        "topic":         s["topic"],
                        "duration_str":  s["duration_str"],
                        "merged_from":   s["merged_from"],
                    }
                    for s in lec["sections"]
                ]
            }
            for lec in series["lectures"]
        ]
    }


# ════════════════════════════════════════════════════════════════
#  NEW ENDPOINT 2 — Get saved series plan (no re-processing)
# ════════════════════════════════════════════════════════════════

@app.get("/series-plan/")
def get_series_plan():
    """
    Returns the saved lecture_series.json.
    Call /plan-series/ first to generate it.
    """
    if not SERIES_JSON.exists():
        raise HTTPException(
            status_code=404,
            detail="No series plan found. Call POST /plan-series/ first."
        )
    return json.loads(SERIES_JSON.read_text(encoding="utf-8"))


# ════════════════════════════════════════════════════════════════
#  NEW ENDPOINT 3 — Generate a specific lecture video
# ════════════════════════════════════════════════════════════════

@app.post("/generate-lecture/")
async def generate_lecture(
    file: UploadFile = File(...),
    target_minutes: int = 60,
    lecture_num:    int = 1,
    background_tasks: BackgroundTasks = None
):
    """
    Upload PDF + choose which lecture number to generate.
    Returns job_id — poll /lecture-status/{job_id} for progress.

    Tip: Call /plan-series/ first to see what's in each lecture number.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files allowed")

    file_path = UPLOAD_DIR / file.filename
    with open(file_path, "wb") as buf:
        shutil.copyfileobj(file.file, buf)

    job_id = str(uuid.uuid4())[:8]
    lecture_jobs[job_id] = {
        "status":      "queued",
        "progress":    0,
        "step":        "waiting to start",
        "lecture_num": lecture_num,
        "error":       None,
        "output_path": None
    }

    background_tasks.add_task(
        _run_lecture_job,
        job_id         = job_id,
        pdf_path       = str(file_path),
        target_minutes = target_minutes,
        lecture_num    = lecture_num
    )

    return {
        "job_id":      job_id,
        "lecture_num": lecture_num,
        "status":      "queued",
        "message":     f"Generating lecture {lecture_num}. Poll: /lecture-status/{job_id}"
    }


# ════════════════════════════════════════════════════════════════
#  NEW ENDPOINT 4 — Poll job status
# ════════════════════════════════════════════════════════════════

@app.get("/lecture-status/{job_id}")
def lecture_status(job_id: str):
    job = lecture_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ════════════════════════════════════════════════════════════════
#  NEW ENDPOINT 5 — Download finished video
# ════════════════════════════════════════════════════════════════

@app.get("/download-lecture/{job_id}")
def download_lecture(job_id: str):
    job = lecture_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(
            status_code=400,
            detail=f"Not ready: {job['status']} ({job['progress']}%)"
        )
    if not job["output_path"] or not Path(job["output_path"]).exists():
        raise HTTPException(status_code=500, detail="Output file missing")
    return FileResponse(job["output_path"], media_type="video/mp4", filename=f"lecture_{job['lecture_num']}.mp4")


# ════════════════════════════════════════════════════════════════
#  NEW ENDPOINT 6 — List all jobs
# ════════════════════════════════════════════════════════════════

@app.get("/lecture-jobs/")
def list_jobs():
    return {
        "total": len(lecture_jobs),
        "jobs": {
            jid: {k: v for k, v in job.items() if k != "output_path"}
            for jid, job in lecture_jobs.items()
        }
    }


# ════════════════════════════════════════════════════════════════
#  BACKGROUND WORKER
# ════════════════════════════════════════════════════════════════

def _run_lecture_job(job_id: str, pdf_path: str, target_minutes: int, lecture_num: int = 1):
    import sys
    sys.path.insert(0, "src")

    from lecture_planner  import plan_lecture
    from script_generator import generate_all_scripts
    from tts_generator    import generate_all_audio
    from slide_generator  import generate_all_slides
    from video_composer   import compose_lecture

    def upd(status=None, progress=None, step=None):
        if status:   lecture_jobs[job_id]["status"]   = status
        if progress: lecture_jobs[job_id]["progress"] = progress
        if step:     lecture_jobs[job_id]["step"]     = step

    # Output paths scoped per lecture number
    plan_path  = f"output/json/lecture_{lecture_num:02d}_plan.json"
    clips_dir  = f"output/lecture_{lecture_num:02d}/clips"
    scripts_dir = f"output/lecture_{lecture_num:02d}/scripts"
    audio_dir  = f"output/lecture_{lecture_num:02d}/audio"
    slides_dir = f"output/lecture_{lecture_num:02d}/slides"
    final_out  = f"output/lecture_{lecture_num:02d}/lecture_{lecture_num:02d}_final.mp4"

    upd(status="running", progress=5, step="extracting PDF content")

    try:
        # Stage 0 — extract subtopics
        env = os.environ.copy()
        env["PDF_PATH"] = pdf_path
        subprocess.run(["python", "src/main.py"], check=True, env=env)
        upd(progress=20, step=f"planning lecture {lecture_num}")

        # Stage 1 — pick subtopics for this lecture number
        plan_lecture(
            json_path      = "output/json/topic_subtopic_content.json",
            target_minutes = target_minutes,
            output_path    = plan_path,
            lecture_num    = lecture_num
        )
        upd(progress=30, step="generating scripts")

        # Stage 2 — Claude API → spoken scripts
        generate_all_scripts(plan_path=plan_path, scripts_dir=scripts_dir)
        upd(progress=50, step="generating audio")

        # Stage 3 — edge-tts → MP3
        generate_all_audio(plan_path=plan_path, audio_dir=audio_dir)
        upd(progress=65, step="generating slides")

        # Stage 4 — PPTX → PNG slides
        generate_all_slides(plan_path=plan_path, slides_dir=slides_dir)
        upd(progress=80, step="composing final video")

        # Stage 5 — FFmpeg → MP4
        final_path = compose_lecture(
            plan_path = plan_path,
            clips_dir = clips_dir,
            final_out = final_out,
            title     = f"Lecture {lecture_num}"
        )

        lecture_jobs[job_id]["status"]      = "done"
        lecture_jobs[job_id]["progress"]    = 100
        lecture_jobs[job_id]["step"]        = "complete"
        lecture_jobs[job_id]["output_path"] = final_path
        print(f"✅ Job {job_id} — Lecture {lecture_num} complete → {final_path}")

    except Exception as e:
        lecture_jobs[job_id]["status"] = "error"
        lecture_jobs[job_id]["step"]   = "failed"
        lecture_jobs[job_id]["error"]  = str(e)
        print(f"❌ Job {job_id} failed: {e}")