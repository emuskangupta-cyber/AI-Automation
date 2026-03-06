import os
import shutil
import json
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException
import subprocess

UPLOAD_DIR = Path("input")
UPLOAD_DIR.mkdir(exist_ok=True)

OUTPUT_JSON = Path("output/json/topic_subtopic_content.json")

app = FastAPI()


@app.get("/")
def home():
    return {"message": "PDF processing server running"}


@app.post("/upload-pdf/")
async def upload_pdf(file: UploadFile = File(...)):

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

    return {
        "filename": file.filename,
        "result": data
    }