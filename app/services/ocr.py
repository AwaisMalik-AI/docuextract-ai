import io
import logging
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


class OCRPreprocessor:
    """Image preprocessing for improved OCR accuracy on scanned documents."""

    @staticmethod
    def preprocess(image_bytes: bytes) -> np.ndarray:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Could not decode image")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        denoised = cv2.fastNlMeansDenoising(gray, h=10)

        coords = np.column_stack(np.where(denoised > 0))
        if len(coords) > 100:
            angle = cv2.minAreaRect(coords)[-1]
            if angle < -45:
                angle = 90 + angle
            if abs(angle) > 0.5:
                (h, w) = denoised.shape[:2]
                center = (w // 2, h // 2)
                matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
                denoised = cv2.warpAffine(denoised, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

        enhanced = cv2.adaptiveThreshold(denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        return enhanced


class OCREngine:
    """Tesseract-based OCR with preprocessing pipeline."""

    def __init__(self, languages: str = "eng"):
        try:
            import pytesseract
            self.pytesseract = pytesseract
        except ImportError:
            raise RuntimeError("pytesseract is required: pip install pytesseract")
        self.languages = languages
        self.preprocessor = OCRPreprocessor()

    def extract_text(self, file_bytes: bytes, content_type: str) -> dict:
        start = time.time()
        pages_text = []

        if content_type == "application/pdf":
            pages_text = self._process_pdf(file_bytes)
        else:
            pages_text = [self._process_image(file_bytes)]

        full_text = "\n\n--- PAGE BREAK ---\n\n".join(pages_text)
        elapsed_ms = int((time.time() - start) * 1000)

        return {
            "text": full_text,
            "page_count": len(pages_text),
            "pages": pages_text,
            "processing_time_ms": elapsed_ms,
        }

    def _process_pdf(self, pdf_bytes: bytes) -> list[str]:
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise RuntimeError("PyMuPDF is required for PDF processing: pip install PyMuPDF")

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                pages.append(text.strip())
            else:
                pix = page.get_pixmap(dpi=300)
                img_bytes = pix.tobytes("png")
                pages.append(self._process_image(img_bytes))
        doc.close()
        return pages

    def _process_image(self, image_bytes: bytes) -> str:
        try:
            processed = self.preprocessor.preprocess(image_bytes)
            pil_image = Image.fromarray(processed)
        except Exception:
            pil_image = Image.open(io.BytesIO(image_bytes))

        text = self.pytesseract.image_to_string(pil_image, lang=self.languages)
        return text.strip()


def get_ocr_engine() -> OCREngine:
    from app.core.config import get_settings
    settings = get_settings()
    return OCREngine(languages=settings.OCR_LANGUAGES)
