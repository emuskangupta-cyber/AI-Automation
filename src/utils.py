import re
from pathlib import Path

def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p

def safe_filename(name: str, max_len: int = 120) -> str:
    name = name.strip()
    name = re.sub(r"[^\w\s\-\.\(\)]", "", name)  # remove weird chars
    name = re.sub(r"\s+", "_", name)
    return name[:max_len] if len(name) > max_len else name