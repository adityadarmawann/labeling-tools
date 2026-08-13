"""
Satu instance Jinja2 dipakai semua router.

Dipisah ke modulnya sendiri supaya router tidak saling impor hanya untuk
mendapatkan objek templates, dan supaya filter kustom terdaftar di satu tempat.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi.templating import Jinja2Templates

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def imgpath(item: dict) -> str:
    """Path absolut gambar, sudah di-quote untuk dipakai di query string."""
    return quote(str(item["img"].resolve()))


templates.env.filters["imgpath"] = imgpath
templates.env.filters["urlquote"] = quote
