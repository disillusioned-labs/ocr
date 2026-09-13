"""Magic-byte inspection, digital-born branch, and image limits."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..ocr.base import Line
from ..settings import Settings


class UnsupportedFileType(Exception):
    pass


class ResourceLimit(Exception):
    pass


@dataclass
class Inspected:
    mime: str  # image/* or application/pdf
    pages: int = 1


def sniff(content: bytes) -> str:
    if content.startswith(b"%PDF"):
        return "application/pdf"
    if content.startswith(b"\x89PNG"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content[:3] == b"GIF":
        return "image/gif"
    if content.startswith(b"II*\x00") or content.startswith(b"MM\x00*"):
        return "image/tiff"
    if content[:4] == b"BM\x8a\x00" or content[:2] == b"BM":
        return "image/bmp"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    raise UnsupportedFileType("magic bytes do not match a supported image or PDF")


def inspect(content: bytes, settings: Settings) -> Inspected:
    mime = sniff(content)
    if len(content) > settings.max_file_bytes:
        raise ResourceLimit(f"file exceeds OCR_MAX_FILE_BYTES ({settings.max_file_bytes})")
    pages = 1
    if mime == "application/pdf":
        import fitz

        with fitz.open(stream=content, filetype="pdf") as doc:
            pages = doc.page_count
        if pages > settings.max_pages:
            raise ResourceLimit(f"PDF has {pages} pages, limit is {settings.max_pages}")
    return Inspected(mime=mime, pages=pages)


def page_count(content: bytes) -> int:
    import fitz

    with fitz.open(stream=content, filetype="pdf") as doc:
        return doc.page_count


def digitalborn_lines(content: bytes) -> list[Line] | None:
    """PDF text layer straight to Lines (confidence 1.0) - no OCR call, no quota."""
    import fitz

    if not content.startswith(b"%PDF"):
        return None
    with fitz.open(stream=content, filetype="pdf") as doc:
        lines: list[Line] = []
        for page_index, page in enumerate(doc, start=1):
            if not page.get_text().strip():
                return None
            for x0, y0, x1, y1, text, *_ in page.get_text("blocks"):
                text = text.strip()
                if text:
                    lines.append(
                        Line(
                            text=text.replace("\n", " "),
                            score=1.0,
                            bbox=(int(x0), int(y0), int(x1), int(y1)),
                            page=page_index,
                        )
                    )
        return lines or None


def downscale_if_needed(content: bytes, settings: Settings) -> bytes:
    """Return (possibly re-encoded) bytes whose largest side is within OCR_MAX_IMAGE_DIM."""
    img = Image.open(io.BytesIO(content))
    largest = max(img.size)
    if largest <= settings.max_image_dim:
        return content
    scale = settings.max_image_dim / largest
    img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()


def pdf_page_images(content: bytes, settings: Settings, dpi: int = 200) -> list[bytes]:
    import fitz

    images: list[bytes] = []
    with fitz.open(stream=content, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            images.append(downscale_if_needed(pix.tobytes("png"), settings))
    return images


def file_suffix(mime: str) -> str:
    return {
        "application/pdf": ".pdf",
        "image/png": ".png",
        "image/jpeg": ".jpg",
    }.get(mime, "")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
