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

from .config import ANN_EXT, ARSIP_EXT, IMG_EXT, META_EXT

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


# ext4 memberi 255 byte per nama. Sisanya disediakan untuk akhiran sementara
# yang dipakai penulis atomik di aplikasi ini (".part", ".tmp"), supaya berkas
# yang namanya lolos di sini tidak gagal ditulis satu langkah kemudian.
MAKS_NAMA = 230
MAKS_DALAM = 6          # kedalaman subfolder yang diterima saat unggah folder
MAKS_KOMPONEN = 80      # panjang nama subfolder


def safe_filename(s: str, arsip: bool = False) -> str:
    """
    Ambil nama berkas saja dari kiriman klien, buang seluruh komponen path,
    dan tolak ekstensi di luar daftar. Mengembalikan "" kalau tidak layak.

    `arsip=True` ikut mengizinkan .zip. Sengaja TIDAK dinyalakan secara bawaan:
    arsip hanya boleh masuk lewat unggahan berkas tunggal yang memang akan
    dibongkar, bukan lewat isi arsip itu sendiri — kalau tidak, satu zip bisa
    memuat zip lain dan pembongkarannya jadi berlapis tanpa batas.

    Nama yang kepanjangan dipotong pada BATANGnya, ekstensinya dipertahankan.
    Memotong nama secara buta ikut memakan ekstensi, dan berkasnya lalu tertolak
    di baris `boleh` di bawah — yang berarti gambar hilang diam-diam alih-alih
    masuk dengan nama lebih pendek. Ekspor Roboflow persis mengenai kasus itu:
    namanya `<asli>.rf.<32 digit hash>.jpg`, mudah lewat dari 80 karakter.
    """
    base = os.path.basename(str(s or "").replace("\\", "/"))
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-.")
    if not base or base.startswith("."):
        return ""
    boleh = IMG_EXT + ANN_EXT + META_EXT + (ARSIP_EXT if arsip else ())
    sfx = Path(base).suffix
    if sfx.lower() not in boleh:
        return ""
    if len(base) > MAKS_NAMA:
        base = (base[:MAKS_NAMA - len(sfx)].rstrip("-.") or "berkas") + sfx
    return base


def safe_relpath(s: str, arsip: bool = False) -> str:
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
            bagian.append(k)
    if not bagian:
        return ""
    # Komponen terakhir diserahkan utuh ke safe_filename: hanya fungsi itu yang
    # tahu mana ekstensinya, jadi hanya di sana pemotongan boleh terjadi.
    berkas = safe_filename(bagian[-1], arsip=arsip)
    if not berkas:
        return ""
    # `k` sudah dibersihkan dari '-' dan '.' di ujungnya dan diawali karakter
    # yang sah, sehingga rstrip di sini tidak mungkin mengosongkannya.
    folder = [k[:MAKS_KOMPONEN].rstrip("-.") for k in bagian[:-1]][-MAKS_DALAM:]
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


# ---------------------------------------------------------------- peran & email
#
# Berkas akun tumbuh, jadi tiap tambahan harus SELALU punya nilai bawaan yang
# masuk akal. Akun lama tidak punya "admin" maupun "email"; membacanya tidak
# boleh gagal, dan yang tidak punya "admin" jelas bukan admin.


def daftar_sendiri(users_file, nama: str, sandi: str, email: str,
                   langsung: bool = False) -> str:
    """
    Buat akun yang MENUNGGU persetujuan admin.

    Tanpa verifikasi email, tidak ada yang membuktikan alamat yang diketik
    benar-benar milik pendaftarnya. Persetujuan admin yang menggantikan
    pembuktian itu: akunnya ada, sandinya sudah dipilih sendiri, tapi belum
    bisa dipakai masuk sampai seseorang yang berwenang melihatnya.

    Dibuat begini juga supaya aman kalau ternyata portnya terbuka ke luar
    jaringan kantor — pendaftaran liar berakhir sebagai daftar tunggu, bukan
    sebagai akses.
    """
    users = load_users(users_file)
    akun = user_slug(nama)
    if akun in users:
        raise ValueError(f"akun '{akun}' sudah ada")
    users[akun] = {
        "hash": hash_password(sandi), "nama": (nama or "").strip() or akun,
        "email": (email or "").strip(), "admin": False,
        "menunggu": not langsung,
        "dibuat": __import__("datetime").date.today().isoformat(),
        "oleh": "daftar sendiri",
    }
    save_users(users_file, users)
    return akun


def calon_admin(users: dict) -> str | None:
    """
    Akun yang paling pantas jadi admin kalau BELUM ADA satu pun.

    Yang didahulukan akun yang dibuat lewat terminal atau oleh admin lain,
    bukan yang mendaftar sendiri: yang punya akses server jelas pemiliknya.
    Di antara yang setara, yang paling tua.
    """
    if not users or any(u.get("admin") for u in users.values()):
        return None

    def urutan(item):
        akun, rec = item
        sendiri = 1 if rec.get("oleh") == "daftar sendiri" else 0
        return (sendiri, str(rec.get("dibuat") or ""), akun)

    return sorted(users.items(), key=urutan)[0][0]


def pastikan_ada_admin(users_file) -> str | None:
    """
    Kalau tidak ada admin sama sekali, angkat satu dan SIMPAN.

    Disimpan, bukan sekadar disimpulkan saat dibaca. Versi pertama memakai
    aturan "kalau akunnya cuma satu, dia admin" — dan aturan itu gugur
    seketika begitu orang pertama mendaftar sendiri. Diukur: darma admin saat
    sendirian, lalu kehilangan haknya tanpa pernah menyerahkannya, dan tidak
    ada satu pun akun yang bisa memperbaikinya dari halaman web.
    """
    users = load_users(users_file)
    akun = calon_admin(users)
    if not akun:
        return None
    users[akun]["admin"] = True
    save_users(users_file, users)
    return akun


def is_admin(users: dict, akun: str) -> bool:
    """
    Benar kalau akun itu admin.

    Kalau belum ada admin sama sekali, calon_admin() yang menentukan — dan
    itu TIDAK bergantung pada jumlah akun, supaya haknya tidak hilang begitu
    orang lain mendaftar.
    """
    rec = users.get(akun)
    if not rec:
        return False
    if rec.get("admin"):
        return True
    return calon_admin(users) == akun


def email_akun(users: dict, akun: str) -> str:
    return str((users.get(akun) or {}).get("email") or "")


def cari_email(users: dict, email: str) -> str | None:
    """Slug akun yang emailnya cocok, tanpa peduli besar-kecil huruf."""
    e = str(email or "").strip().lower()
    if not e:
        return None
    for akun, rec in users.items():
        if str(rec.get("email") or "").strip().lower() == e:
            return akun
    return None


def menunggu_setujuan(users: dict, akun: str) -> bool:
    """Akun yang mendaftar sendiri dan belum disetujui admin.

    Hanya ada kalau LABELAPP_DAFTAR_LANGSUNG dimatikan — dan itu memang
    bawaannya, karena menyalakannya bergantung pada firewall yang membatasi
    siapa saja yang bisa menjangkau aplikasi ini.
    """
    return bool((users.get(akun) or {}).get("menunggu"))


def authenticate(users: dict, nama: str, pw: str) -> str | None:
    """
    Kembalikan slug akun kalau password benar, None kalau tidak.

    Akun yang menunggu persetujuan diperlakukan seperti password salah:
    sandinya benar, tapi belum boleh masuk. Yang membedakan keduanya
    disampaikan halaman login, bukan fungsi ini.
    """
    akun = user_slug(nama)
    rec = users.get(akun)
    if not rec or not verify_password(pw, rec.get("hash", "")):
        return None
    if rec.get("menunggu"):
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
    lama = users.get(akun) or {}
    users[akun] = {**lama, "hash": hash_password(pw),
                   "nama": nama.strip() or akun}
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
