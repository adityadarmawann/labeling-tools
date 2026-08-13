"""
Perlengkapan uji.

Aturan yang dipegang berkas ini: **pengujian tidak boleh menulis apa pun ke
dalam folder aplikasi.** Seluruh berkas akun, dataset, unggahan, dan thumbnail
dibuat di direktori sementara milik pytest, dan setelan diarahkan ke sana lewat
environment sebelum app.config dibaca.

Ini bukan kerapian belaka. Pernah terjadi: berkas akun uji tertinggal di folder
aplikasi, lalu skrip penyala melihatnya dan menjalankan server sungguhan yang
terbuka ke jaringan dengan password yang ada di catatan pengujian. Isolasi di
sini yang membuat kejadian itu tidak bisa terulang — dan
test_folder_aplikasi_tidak_tersentuh yang menjaganya tetap begitu.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Tidak diperiksa saat memotret folder aplikasi: besar, berubah sendiri, dan
# bukan milik aplikasi.
ABAIKAN = {".venv", "__pycache__", ".pytest_cache", ".git", ".ruff_cache"}


def _potret_folder_aplikasi() -> dict[str, tuple[int, int]]:
    """Nama, ukuran, dan waktu ubah setiap berkas di folder aplikasi."""
    out: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in ABAIKAN]
        for f in filenames:
            p = Path(dirpath) / f
            try:
                st = p.stat()
            except OSError:
                continue
            out[str(p.relative_to(ROOT))] = (st.st_size, st.st_mtime_ns)
    return out


@pytest.fixture(autouse=True)
def folder_aplikasi_tak_berubah():
    """
    Penjaga yang berlaku untuk SETIAP tes: folder aplikasi harus persis sama
    sebelum dan sesudah tes berjalan.

    Sengaja tidak menuntut folder ini kosong dari users.json — di pemakaian
    nyata berkas akun memang tinggal di sini. Yang dilarang adalah pengujian
    membuat, mengubah, atau menghapusnya. Itu yang pernah terjadi: berkas akun
    uji tertinggal di sini, lalu start.sh melihatnya dan menyalakan server
    sungguhan yang terbuka ke jaringan dengan password dari catatan pengujian.
    """
    sebelum = _potret_folder_aplikasi()
    yield
    sesudah = _potret_folder_aplikasi()
    baru = sorted(set(sesudah) - set(sebelum))
    hilang = sorted(set(sebelum) - set(sesudah))
    berubah = sorted(k for k in set(sebelum) & set(sesudah)
                     if sebelum[k] != sesudah[k])
    assert not (baru or hilang or berubah), (
        "tes ini menyentuh folder aplikasi — semua berkas uji harus dibuat di "
        f"tmp_path.\n  berkas baru   : {baru}\n  berkas hilang : {hilang}\n"
        f"  berkas berubah: {berubah}")

PW_PAUL = "sandi-uji-paul-1"
PW_ANGGI = "sandi-uji-anggi-1"


def buat_dataset(d: Path, n_img: int, n_ann: int) -> Path:
    """Dataset labelme kecil: n_img gambar, n_ann di antaranya punya anotasi."""
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n_img):
        im = np.full((60, 80, 3), 40 + i * 20, np.uint8)
        cv2.circle(im, (30 + i * 5, 30), 15, (30, 200, 160), -1)
        ip = d / f"{d.name}-{i:02d}.jpg"
        cv2.imwrite(str(ip), im)
        if i < n_ann:
            ip.with_suffix(".json").write_text(json.dumps({
                "version": "0.4.36", "flags": {},
                "shapes": [{"label": "botol" if i % 2 == 0 else "kaleng",
                            "shape_type": "polygon",
                            "points": [[5, 5], [70, 5], [70, 50], [5, 50]]}],
                "imagePath": ip.name, "imageData": None,
                "imageHeight": 60, "imageWidth": 80,
            }))
    return d


@pytest.fixture
def lingkungan(tmp_path, monkeypatch):
    """Setelan aplikasi yang seluruhnya menunjuk ke tmp_path."""
    from app.config import get_settings
    from app.security import hash_password

    users = tmp_path / "users.json"
    users.write_text(json.dumps({
        "paul": {"hash": hash_password(PW_PAUL), "nama": "Paul"},
        "anggi": {"hash": hash_password(PW_ANGGI), "nama": "Anggi"},
    }))

    roots = tmp_path / "roots"
    buat_dataset(roots / "ds-alpha", 4, 2)      # 2 berlabel, 2 belum
    buat_dataset(roots / "ds-beta", 3, 1)       # 1 berlabel, 2 belum

    for k, v in {
        "USERS_FILE": users,
        "DATASETS_ROOT": roots,
        "UPLOADS_ROOT": tmp_path / "unggahan",
        "THUMB_ROOT": tmp_path / "thumb",
        "MAX_UPLOAD_MB": "1",
    }.items():
        monkeypatch.setenv("LABELAPP_" + k, str(v))

    # get_settings di-cache per proses; bersihkan supaya tiap tes memakai
    # tmp_path miliknya sendiri, bukan milik tes sebelumnya.
    get_settings.cache_clear()
    yield {"tmp": tmp_path, "users": users, "roots": roots}
    get_settings.cache_clear()


@pytest.fixture
def aplikasi(lingkungan):
    from app.main import create_app
    from app.session import store

    store._data.clear()
    yield create_app()
    store._data.clear()


@pytest.fixture
def klien(aplikasi):
    """
    Klien dari jaringan. TestClient memakai host 'testclient', jadi
    is_local() bernilai False — persis seperti anggota tim yang mengakses
    dari laptopnya.
    """
    from fastapi.testclient import TestClient

    with TestClient(aplikasi) as c:
        yield c


@pytest.fixture
def klien_lokal(aplikasi):
    """Klien yang menyamar sebagai permintaan dari mesin server sendiri."""
    from fastapi.testclient import TestClient

    with TestClient(aplikasi, client=("127.0.0.1", 50000)) as c:
        yield c


def masuk(klien, nama: str, pw: str):
    r = klien.post("/login", data={"user": nama, "pw": pw}, follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    return klien


def klien_baru(aplikasi, nama: str, pw: str):
    """Klien terpisah dengan cookie sendiri, berbagi aplikasi yang sama.
    Dipakai untuk menguji dua akun yang aktif bersamaan."""
    from fastapi.testclient import TestClient

    return masuk(TestClient(aplikasi), nama, pw)
