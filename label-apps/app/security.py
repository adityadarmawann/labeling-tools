"""
Akun dan password.

Password disimpan sebagai pbkdf2-sha256 di berkas JSON, bukan basis data —
jumlah anggota tim kecil dan berkas jauh lebih mudah di-backup serta diperiksa
dengan mata. Kalau nanti perlu peran (admin/anotator) atau jejak audit,
berkas ini yang naik jadi tabel.
"""
from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path

from .config import ANN_EXT, IMG_EXT

ITERATIONS = 200_000
COOKIE_NAME = "labelapp_sid"


# ---------------------------------------------------------------- nama aman

def safe_slug(s: str) -> str:
    """Ubah teks bebas menjadi nama folder yang aman: tanpa '/', tanpa '..'."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(s or "").strip()).strip("-.")
    return s[:64] or "tanpa-nama"


def user_slug(s: str) -> str:
    """
    Nama akun selalu huruf kecil. Tanpa ini, akun dibuat 'Budi' tapi diketik
    'budi' saat login akan ditolak — jebakan yang tidak perlu.
    """
    return safe_slug(s).lower()


def safe_filename(s: str) -> str:
    """
    Ambil nama berkas saja dari kiriman klien, buang seluruh komponen path,
    dan tolak ekstensi di luar daftar. Mengembalikan "" kalau tidak layak.
    """
    base = os.path.basename(str(s or "").replace("\\", "/"))
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-.")
    if not base or base.startswith("."):
        return ""
    if Path(base).suffix.lower() not in IMG_EXT + ANN_EXT:
        return ""
    return base[:120]


MAKS_DALAM = 6          # kedalaman subfolder yang diterima saat unggah folder


def safe_relpath(s: str) -> str:
    """
    Path relatif dari unggahan folder -> path yang aman, subfoldernya utuh.

    Dipakai karena struktur folder itu BERMAKNA: pemindai mengenali dataset
    YOLO dari adanya `images/` dan `labels/`. Kalau semua diratakan menjadi
    nama berkas saja, dataset YOLO yang diunggah tidak akan terbaca.

    Setiap komponen disterilkan sendiri-sendiri. Path yang memuat `..`
    DITOLAK, bukan ditafsirkan: unggahan folder yang sah tidak pernah
    memuatnya, jadi menolak lebih jelas daripada diam-diam mengubah maksudnya
    menjadi path lain.

    Mengembalikan "" kalau tidak layak.
    """
    bagian = []
    for k in re.split(r"[\\/]+", str(s or "")):
        k = k.strip()
        if k == "..":
            return ""
        if k in ("", "."):
            continue
        k = re.sub(r"[^A-Za-z0-9._-]+", "-", k).strip("-.")
        if k:
            bagian.append(k[:80])
    if not bagian:
        return ""
    berkas = safe_filename(bagian[-1])
    if not berkas:
        return ""
    folder = bagian[:-1][-MAKS_DALAM:]
    return "/".join(folder + [berkas])


# ---------------------------------------------------------------- password

def hash_password(pw: str, salt: str | None = None, iters: int = ITERATIONS) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), iters)
    return f"pbkdf2_sha256${iters}${salt}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, iters, salt, want = str(stored).split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), want)


# ---------------------------------------------------------------- berkas akun

def load_users(path: Path) -> dict:
    if not path or not Path(path).exists():
        return {}
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"\n  {path} bukan JSON yang sah — {e}\n") from e
    return d if isinstance(d, dict) else {}


def save_users(path: Path, users: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(users, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    os.chmod(path, 0o600)


def authenticate(users: dict, nama: str, pw: str) -> str | None:
    """Kembalikan slug akun kalau password benar, None kalau tidak."""
    akun = user_slug(nama)
    rec = users.get(akun)
    if not rec or not verify_password(pw, rec.get("hash", "")):
        return None
    return akun


def add_user(users_file: Path, nama: str) -> None:
    """
    Buat akun atau ganti passwordnya. Password diminta lewat prompt, tidak
    lewat argumen, supaya tidak tertinggal di riwayat shell.
    """
    users = load_users(users_file)
    akun = user_slug(nama)
    print(f"  {'Ganti password' if akun in users else 'Akun baru'} : {akun}")
    pw = getpass.getpass("  Password        : ")
    if len(pw) < 8:
        raise SystemExit("\n  Password minimal 8 karakter.\n")
    if pw != getpass.getpass("  Ulangi          : "):
        raise SystemExit("\n  Password tidak sama.\n")
    users[akun] = {"hash": hash_password(pw), "nama": nama.strip() or akun}
    save_users(users_file, users)
    print(f"\n  Tersimpan di {users_file} ({len(users)} akun).\n")


def remove_user(users_file: Path, nama: str) -> None:
    users = load_users(users_file)
    akun = user_slug(nama)
    if akun not in users:
        raise SystemExit(f"\n  Akun '{akun}' tidak ada di {users_file}\n")
    users.pop(akun)
    save_users(users_file, users)
    print(f"\n  Akun '{akun}' dihapus ({len(users)} akun tersisa).\n")
