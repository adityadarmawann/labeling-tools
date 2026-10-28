"""
Uji alur unggah video -> ekstrak frame pada projek IMAGE, lewat HTTP.

Yang dijaga: pada projek image, video diekstrak jadi gambar lalu videonya
dibuang; /ekstrak tetap menolak projek video (jalur itu khusus image). Jalur
projek VIDEO (video mendarat di _sumber/, tidak diekstrak) diuji terpisah di
test_video_aksi_unggah.py.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from tests.conftest import PW_PAUL, masuk


def _video_bytes(tmp_path, n=60, fps=30.0, ukuran=(320, 240)):
    p = tmp_path / "klip.mp4"
    vw = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"mp4v"), fps, ukuran)
    assert vw.isOpened()
    w, h = ukuran
    for i in range(n):
        g = np.tile(np.linspace(0, 255, w, dtype=np.uint8), (h, 1))
        f = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        rng = np.random.default_rng(i)
        for _ in range(6):
            x, y = int(rng.integers(0, w - 40)), int(rng.integers(0, h - 40))
            c = tuple(int(v) for v in rng.integers(0, 255, 3))
            cv2.rectangle(f, (x, y), (x + 40, y + 40), c, -1)
        cv2.putText(f, str(i), (30, 170), cv2.FONT_HERSHEY_SIMPLEX, 4,
                    (255, 255, 255), 6)
        vw.write(f)
    vw.release()
    return p.read_bytes()


def _buat_projek(klien, nama, jenis):
    r = klien.post(f"/api/projek/baru?nama={nama}&jenis={jenis}")
    assert r.status_code == 200, r.text[:300]
    j = r.json()
    assert j["ok"], j
    return j


def _tunggu_ekstrak(klien, ds, video, timeout=25.0):
    """Ekstraksi kini DI LATAR (thread): poll /api/ekstrak/status sampai video
    itu 'selesai'/'gagal', lalu kembalikan rekamannya."""
    import time
    batas = time.time() + timeout
    while time.time() < batas:
        st = klien.get(f"/api/ekstrak/status?ds={ds}").json().get("status", {})
        rec = st.get(video) or {}
        if rec.get("keadaan") in ("selesai", "gagal"):
            return rec
        time.sleep(0.1)
    return {}


def test_video_ke_projek_image_diekstrak_dan_dihapus(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-img", "image")
    data = _video_bytes(lingkungan["tmp"])

    assert klien.put("/upload?ds=vid-img&name=klip.mp4",
                     content=data).json()["video"] is True

    # /ekstrak MEMULAI ekstraksi di latar dan balas segera.
    j = klien.post("/ekstrak?ds=vid-img&name=klip.mp4&fps=2").json()
    assert j["ok"] and j.get("latar") is True, j

    rec = _tunggu_ekstrak(klien, "vid-img", "klip.mp4")
    assert rec.get("keadaan") == "selesai" and rec.get("n", 0) > 0, rec

    proj = lingkungan["ruang"] / "vid-img"
    assert not (proj / "klip.mp4").exists()                 # video dibuang
    assert len(list(proj.glob("*.jpg"))) == rec["n"]        # frame mendarat

    # Spanduk "selesai" bisa ditutup supaya tak menetap tiap halaman dibuka.
    st = klien.get("/api/ekstrak/status?ds=vid-img").json()["status"]
    assert "klip.mp4" in st                                  # masih tercatat
    assert klien.post("/api/ekstrak/tutup?ds=vid-img&video=klip.mp4").json()["ok"]
    st2 = klien.get("/api/ekstrak/status?ds=vid-img").json()["status"]
    assert "klip.mp4" not in st2                             # sudah hilang
    # Menutup lagi aman (idempoten), dan frame-nya TIDAK ikut terhapus.
    assert klien.post("/api/ekstrak/tutup?ds=vid-img&video=klip.mp4").json()["ok"]
    assert len(list(proj.glob("*.jpg"))) == rec["n"]


def test_video_kedua_ke_projek_flat_yang_sudah_berisi(klien, lingkungan):
    """Projek flat yang SUDAH berisi frame (dari video pertama) tetap menerima
    video berikutnya lewat /upload + /ekstrak -- 'semua fitur gambar bisa'."""
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-multi", "image")
    data = _video_bytes(lingkungan["tmp"])
    proj = lingkungan["ruang"] / "vid-multi"

    assert klien.put("/upload?ds=vid-multi&name=a.mp4", content=data).json()["ok"]
    assert klien.post("/ekstrak?ds=vid-multi&name=a.mp4&fps=2").json()["ok"]
    r1 = _tunggu_ekstrak(klien, "vid-multi", "a.mp4")
    assert r1.get("keadaan") == "selesai", r1
    n1 = len(list(proj.glob("*.jpg")))
    assert n1 == r1["n"]

    # Projek kini "berisi" + flat; video KEDUA harus tetap bisa.
    assert klien.put("/upload?ds=vid-multi&name=b.mp4", content=data).json()["ok"]
    assert klien.post("/ekstrak?ds=vid-multi&name=b.mp4&fps=2").json()["ok"]
    r2 = _tunggu_ekstrak(klien, "vid-multi", "b.mp4")
    assert r2.get("keadaan") == "selesai", r2
    assert not (proj / "b.mp4").exists()                    # video kedua dibuang
    assert len(list(proj.glob("*.jpg"))) == n1 + r2["n"]    # frame bertambah


def test_preset_tak_sah_jatuh_ke_bawaan(klien, lingkungan):
    # Preset ngaco -> jatuh ke bawaan di dalam ekstraksi; di sini cukup dipastikan
    # ekstraksinya tetap jalan & selesai (fallback preset diuji di test_ekstraksi).
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-preset", "image")
    data = _video_bytes(lingkungan["tmp"])
    klien.put("/upload?ds=vid-preset&name=klip.mp4", content=data)

    j = klien.post("/ekstrak?ds=vid-preset&name=klip.mp4&preset=ngaco").json()
    assert j["ok"] and j.get("latar") is True, j
    rec = _tunggu_ekstrak(klien, "vid-preset", "klip.mp4")
    assert rec.get("keadaan") == "selesai", rec


def test_ekstrak_pada_projek_video_ditolak(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-vid2", "video")
    # Taruh video langsung di foldernya (lewati /upload) untuk menguji guard
    # /ekstrak-nya sendiri.
    proj = lingkungan["ruang"] / "vid-vid2"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "klip.mp4").write_bytes(_video_bytes(lingkungan["tmp"]))

    r = klien.post("/ekstrak?ds=vid-vid2&name=klip.mp4")
    j = r.json()
    assert j["ok"] is False and "image" in j["error"], j


def test_ekstrak_tanpa_video_memberi_pesan_jelas(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-kosong", "image")
    r = klien.post("/ekstrak?ds=vid-kosong&name=tidakada.mp4")
    j = r.json()
    assert j["ok"] is False and "tidak ada" in j["error"], j


def test_hapus_batch_frame_ekstrak_kartu_hilang(klien, lingkungan):
    """Frame hasil ekstraksi video (batch 'Tanpa nama unggahan') bisa dihapus
    lewat papan, dan KARTUNYA ikut hilang -- bukan cuma isinya. `ds` dikirim
    supaya tetap jalan walau sesi kehilangan projek yang terbuka (sehabis
    restart/deploy)."""
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-hapus", "image")
    klien.put("/upload?ds=vid-hapus&name=IMG.mp4", content=_video_bytes(lingkungan["tmp"]))
    klien.post("/ekstrak?ds=vid-hapus&name=IMG.mp4&fps=2")
    rec = _tunggu_ekstrak(klien, "vid-hapus", "IMG.mp4")
    assert rec["n"] > 0
    proj = lingkungan["ruang"] / "vid-hapus"

    # Papan menampilkan kartu 'Tanpa nama unggahan' sebelum dihapus.
    assert "Tanpa nama unggahan" in klien.get("/anotasi?ds=vid-hapus").text

    # Hapus lewat rute papan -- dengan ds, tanpa membuka projek dulu (meniru
    # sesi yang baru di-restart).
    klien.get("/logout"); masuk(klien, "paul", PW_PAUL)        # sesi segar, src kosong
    j = klien.post("/api/tugas/hapus-batch",
                   json={"batch": "", "ds": "vid-hapus"}).json()
    assert j["ok"] and j["dibuang"] == rec["n"], j
    assert len(list(proj.glob("*.jpg"))) == 0                   # isinya hilang
    # Dan kartunya ikut hilang dari papan.
    assert "Tanpa nama unggahan" not in klien.get("/anotasi?ds=vid-hapus").text
    # Spanduk status ekstraksi ("selesai") ikut terbersihkan oleh Hapus batch --
    # tanpa perlu menekan tombol x terpisah.
    st = klien.get("/api/ekstrak/status?ds=vid-hapus").json()["status"]
    assert "IMG.mp4" not in st, st


def test_ekstraksi_menyegarkan_papan_tanpa_reload(klien, lingkungan):
    """Sesudah ekstraksi selesai, papan Anotasi (SESI YANG SAMA, tanpa
    logout/muat ulang penuh) harus menampilkan frame baru. Ini menjaga bug
    'sidebar 4453 tapi kartu kosong': ekstraksi memanggil tandai_berubah supaya
    segarkan() mendeteksi daftar gambar yang berubah."""
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-segar", "image")
    # Buka papan DULU -> sesi memuat projek (0 gambar), cap tercatat.
    assert "Tanpa nama unggahan" not in klien.get("/anotasi?ds=vid-segar").text
    klien.put("/upload?ds=vid-segar&name=IMG.mp4", content=_video_bytes(lingkungan["tmp"]))
    klien.post("/ekstrak?ds=vid-segar&name=IMG.mp4&fps=2")
    rec = _tunggu_ekstrak(klien, "vid-segar", "IMG.mp4")
    assert rec["n"] > 0
    # Papan kini harus menampilkan kartu frame baru via segarkan(), bukan tetap
    # kosong seperti sebelum perbaikan.
    html = klien.get("/anotasi?ds=vid-segar").text
    assert "Tanpa nama unggahan" in html, "papan tak menyegar setelah ekstraksi"


def test_papan_tangkap_frame_ditulis_langsung_ke_disk(klien, lingkungan):
    """Papan Anotasi HARUS menampilkan gambar yang muncul di disk walau TAK ada
    sinyal in-app (mis. frame ekstraksi yang ditulis proses lain). Dulu papan
    pakai segarkan() yang cuma lihat penanda in-app, jadi kartu kosong padahal
    sidebar/grid (yang pakai periksa_disk) sudah menghitung gambarnya."""
    import cv2, numpy as np
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-disk", "image")
    proj = lingkungan["ruang"] / "vid-disk"
    # Buka papan DULU -> sesi memuat projek (0 gambar), tanda tangan disk tercatat.
    assert "Tanpa nama unggahan" not in klien.get("/anotasi?ds=vid-disk").text
    # Tulis frame LANGSUNG ke disk, meniru ekstraksi proses lain -- TANPA lewat
    # rute/tandai_berubah.
    for i in range(5):
        cv2.imwrite(str(proj / f"IMG-{i:03d}.jpg"),
                    np.random.default_rng(i).integers(0, 255, (40, 60, 3), np.uint8))
    # Papan (sesi yang SAMA) harus kini menampilkannya via periksa_disk.
    assert "Tanpa nama unggahan" in klien.get("/anotasi?ds=vid-disk").text
    # Dan halaman /bagi (tempat membagikan) juga, bukan cuma papan.
    assert klien.get("/bagi?ds=vid-disk").status_code == 200
