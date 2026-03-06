from typing import Optional
import fitz
import pdfplumber
from ocr import ocr_page

def extract_text_pymupdf(doc: fitz.Document, page_index: int) -> str:
    page = doc.load_page(page_index)
    return page.get_text("text") or ""

def extract_text_pdfplumber(pdf_path: str, page_index: int) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_index]
        return page.extract_text() or ""

def is_probably_scanned(text: str) -> bool:
    # very simple heuristic: if too little text, it's likely image-based
    t = (text or "").strip()
    return len(t) < 40

def extract_page_text(doc: fitz.Document, pdf_path: str, page_index: int, enable_ocr: bool) -> str:
    # 1) Try PyMuPDF
    t1 = extract_text_pymupdf(doc, page_index)
    if not is_probably_scanned(t1):
        return t1

    # 2) Try pdfplumber
    t2 = extract_text_pdfplumber(pdf_path, page_index)
    if not is_probably_scanned(t2):
        return t2

    # 3) OCR
    if enable_ocr:
        return ocr_page(doc, page_index)

    # Return best we have
    return t2 if len(t2.strip()) > len(t1.strip()) else t1

def extract_section_text(doc: fitz.Document, pdf_path: str, start_page_1based: int, end_page_1based: int, enable_ocr: bool) -> str:
    out = []
    for p in range(start_page_1based - 1, end_page_1based):
        out.append(extract_page_text(doc, pdf_path, p, enable_ocr))
    return "\n".join(out).strip()