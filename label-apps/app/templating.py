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


STATIC_DIR = Path(__file__).resolve().parent / "static"


def statik(nama: str) -> str:
    """
    URL berkas static beserta cap versinya, mis. `/static/label.js?v=1a2b3c4d`.

    Berkas static dikirim tanpa header Cache-Control, jadi peramban menebak
    sendiri berapa lama ia boleh menyimpannya — dan tebakannya sering "lama".
    Akibatnya halaman memuat HTML terbaru bersama JavaScript LAMA: tombol baru
    muncul tetapi diam saja saat diklik, dan satu-satunya jalan keluar adalah
    menyuruh setiap pemakai menekan Ctrl+Shift+R. Itu menambal gejalanya.

    Dengan cap versi dari waktu-ubah berkasnya, URL-nya berubah sendiri setiap
    kali berkasnya berubah, sehingga peramban wajib mengambil yang baru — dan
    selama berkasnya TIDAK berubah, ia tetap boleh memakai salinan cache-nya.
    """
    f = STATIC_DIR / nama
    try:
        cap = f"{int(f.stat().st_mtime):x}"
    except OSError:
        cap = "0"
    return f"/static/{nama}?v={cap}"


def imgpath(item: dict) -> str:
    """Path absolut gambar, sudah di-quote untuk dipakai di query string."""
    return quote(str(item["img"].resolve()))


templates.env.filters["imgpath"] = imgpath
templates.env.filters["urlquote"] = quote
# Dipakai sebagai fungsi di templat: {{ statik("label.js") }}
templates.env.globals["statik"] = statik
