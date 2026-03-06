from typing import Optional
import fitz
import pytesseract
from PIL import Image
import io

def ocr_page(doc: fitz.Document, page_index: int, dpi: int = 250, lang: str = "eng") -> str:
    """
    OCR a single PDF page using Tesseract.
    page_index is 0-based.
    """
    page = doc.load_page(page_index)
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    text = pytesseract.image_to_string(img, lang=lang)
    return text or ""