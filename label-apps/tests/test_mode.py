"""
Uji pemisahan dev dan prod.

Dua mode berjalan di mesin yang sama, dan yang satu dipakai tim sementara yang
lain dipakai untuk mencoba-coba. Satu setelan yang tanpa sengaja sama sudah
cukup untuk membuat percobaan di dev menimpa anotasi tim, atau membuat akun uji
bisa dipakai masuk ke produksi.

Berkasnya kecil dan jarang disentuh, justru itu sebabnya kekeliruan di sini
tidak ketahuan sampai kerusakannya terjadi.
"""
from __future__ import annotations

from pathlib import Path

import pytest

AKAR = Path(__file__).resolve().parent.parent

# Setelan yang menunjuk tempat penyimpanan. Kalau salah satunya sama, dev dan
# prod menulis ke berkas yang sama.
TERPISAH = ("PORT", "USERS_FILE", "DATASETS_ROOT", "UPLOADS_ROOT")

# Setelan yang mengubah PERILAKU, bukan tempat. Keduanya harus menyebutkan
# setelan yang sama persis — kalau dev membiarkannya kosong, ia memakai nilai
# bawaan yang berbeda dari prod, dan alur yang diuji di dev bukan alur yang
# nanti berjalan di produksi.
PERILAKU = ("GOOGLE_DOMAIN", "DAFTAR_SENDIRI", "DAFTAR_LANGSUNG")


def _baca(nama: str) -> dict[str, str]:
    isi = {}
    for baris in (AKAR / "env" / f"{nama}.env").read_text().splitlines():
        baris = baris.strip()
        if baris.startswith("LABELAPP_") and "=" in baris:
            k, v = baris[len("LABELAPP_"):].split("=", 1)
            isi[k] = v.strip()
    return isi


@pytest.fixture(scope="module")
def dev() -> dict:
    return _baca("dev")


@pytest.fixture(scope="module")
def prod() -> dict:
    return _baca("prod")


@pytest.mark.parametrize("kunci", TERPISAH)
def test_dev_dan_prod_tidak_berbagi_tempat(dev, prod, kunci):
    assert kunci in dev and kunci in prod, f"{kunci} harus disebut di keduanya"
    assert dev[kunci] != prod[kunci], (
        f"LABELAPP_{kunci} sama di dev dan prod: {dev[kunci]!r}")


def test_folder_dev_tidak_bersarang_di_folder_prod(dev, prod):
    """Berbeda tulisan saja tidak cukup; `./dev-data` di dalam folder prod
    tetap berarti dev menulis ke wilayah tim."""
    for kunci in ("DATASETS_ROOT", "UPLOADS_ROOT"):
        d = Path(dev[kunci]).expanduser()
        d = (AKAR / d).resolve() if not d.is_absolute() else d.resolve()
        p = Path(prod[kunci]).expanduser().resolve()
        assert not d.is_relative_to(p), f"{kunci} dev ({d}) ada di dalam prod ({p})"
        assert not p.is_relative_to(d), f"{kunci} prod ({p}) ada di dalam dev ({d})"


def test_autologin_tidak_pernah_ada_di_prod(prod):
    """Masuk tanpa password hanya pantas di dev.

    Kodenya memang menolak autologin dari jaringan (deps.sesi_otomatis), jadi
    ini lapis kedua — tapi lapis pertama tidak boleh dipakai sebagai alasan
    untuk menaruhnya di berkas produksi sama sekali.
    """
    assert not prod.get("DEV_AUTOLOGIN"), "prod.env memuat DEV_AUTOLOGIN"


@pytest.mark.parametrize("kunci", PERILAKU)
def test_setelan_perilaku_disebut_di_kedua_mode(dev, prod, kunci):
    assert kunci in prod, f"LABELAPP_{kunci} hilang dari prod.env"
    assert kunci in dev, (
        f"LABELAPP_{kunci} ada di prod tapi tidak di dev; dev akan memakai "
        f"nilai bawaan dan berperilaku lain dari produksi")
    assert dev[kunci] == prod[kunci], (
        f"LABELAPP_{kunci} beda: dev={dev[kunci]!r} prod={prod[kunci]!r}")
