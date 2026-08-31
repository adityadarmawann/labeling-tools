"""
Satu instance Jinja2 dipakai semua router.

Dipisah ke modulnya sendiri supaya router tidak saling impor hanya untuk
mendapatkan objek templates, dan supaya filter kustom terdaftar di satu tempat.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi.templating import Jinja2Templates

from .services.render import warna_kelas

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
    """Path absolut gambar, sudah di-quote untuk dipakai di QUERY STRING."""
    return quote(str(item["img"].resolve()))


def pathmentah(item: dict) -> str:
    """
    Path absolut gambar apa adanya, untuk atribut HTML seperti data-path.

    imgpath TIDAK boleh dipakai di sini. Nilainya sudah di-quote untuk query
    string, dan JavaScript yang membacanya lewat dataset mengirimkannya mentah
    di dalam bodi JSON — tidak ada yang meng-unquote-nya lagi. Projek bernama
    "Rantai 171819" karena itu mengirim path berisi %20, dan server menjawab
    "tidak satu pun gambar itu ada di projek ini" untuk gambar yang jelas ada.

    Terungkap hanya karena projek ujinya bernama dengan spasi; seluruh projek
    tanpa spasi melewati jalur yang sama tanpa gejala apa pun.
    """
    return str(item["img"].resolve())


templates.env.filters["imgpath"] = imgpath
templates.env.filters["pathmentah"] = pathmentah
# Identitas objek item, dipakai templat grid untuk mencari penugasan gambar itu
# di peta yang dihitung router. Memakai id() daripada path supaya tidak ada
# pembentukan string ribuan kali per halaman.
templates.env.filters["id"] = id
templates.env.filters["urlquote"] = quote
# Dipakai sebagai fungsi di templat: {{ statik("label.js") }}
templates.env.globals["statik"] = statik
# Warna kelas di templat, rumusnya sama dengan kanvas dan thumbnail.
templates.env.filters["warnakelas"] = warna_kelas
