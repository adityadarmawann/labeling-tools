"""
Uji pelabelan klip + penyajian klip + wiring nav (Langkah 6).

Yang dijaga:
  1. POST /api/aksi/label — RBAC klip dipakai ulang apa adanya: owner/editor/
     labeler-menyeluruh bisa melabeli klip mana pun; labeler-spesifik hanya
     batch dalam scope-nya dan ditolak di luar; bukan anggota ditolak. Label
     wajib kelas yang dikenal; "" mengosongkan. .klip.json mencerminkan tulisan;
     angka ikut berubah.
  2. GET /klip — anggota boleh ambil klip; bukan anggota ditolak; path keluar
     (`..`, absolut, _ditolak) ditolak; permintaan Range menjawab 206 dengan
     Content-Range yang benar dan byte yang tepat.
  3. GET /aksi merender untuk projek video+aksi (owner lihat klip + tombol
     kelas); /anotasi projek image tetap jalan (tak ada regresi); sidebar
     memunculkan entri aksi HANYA untuk video+aksi.
"""
from __future__ import annotations

import subprocess

import pytest

from app.services import klip, klip_tag, tugas
from tests.conftest import PW_ANGGI, PW_PAUL, klien_baru, masuk


# ------------------------------------------------------------ pembantu

def _buat_video_aksi(klien, nama, kelas=("shoot", "pass")):
    """Projek VIDEO dengan daftar kelas aksi tersetel, lewat HTTP."""
    r = klien.post(f"/api/projek/baru?nama={nama}&jenis=video")
    assert r.status_code == 200 and r.json()["ok"], r.text[:200]
    r = klien.post(f"/api/tugas/aksi?ds={nama}",
                   json={"aksi": {"kelas": list(kelas)}})
    assert r.json()["ok"], r.text[:200]


def _klip(proj, rel, data=b"klip-byte-contoh"):
    """Buat satu berkas klip di disk (b-byte apa adanya cukup untuk rute yang
    tak menyajikan byte; untuk /klip dipakai byte deterministik yang panjang)."""
    p = proj / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# ==================================================== POST /api/aksi/label

def test_label_owner_dan_klip_json(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    _klip(proj, "klip/b1/shoot_vid_t0001.mp4")

    r = klien.post("/api/aksi/label", params={
        "ds": "aksi", "klip": "klip/b1/shoot_vid_t0001.mp4", "label": "shoot"})
    j = r.json()
    assert j["ok"] and j["label"] == "shoot" and j["berlabel"] is True, j
    # Angka ikut: 1 klip, 1 berlabel.
    assert j["klip"] == "klip/b1/shoot_vid_t0001.mp4"
    assert j["berlabel"] is True
    # .klip.json mencerminkan tulisan, dengan batch dari tata letak & srcid tebakan.
    got = klip_tag.untuk(proj, "klip/b1/shoot_vid_t0001.mp4")
    assert got["label"] == "shoot" and got["batch"] == "b1"
    assert got["srcid"] == "vid"


def test_label_kelas_asing_ditolak_dan_kosong_menghapus(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    _klip(proj, "klip/b1/x.mp4")

    # Kelas yang tak dikenal ditolak.
    r = klien.post("/api/aksi/label", params={
        "ds": "aksi", "klip": "klip/b1/x.mp4", "label": "lompat"}).json()
    assert r["ok"] is False and "bukan kelas" in r["error"], r

    # Set lalu kosongkan: kembali belum-dilabeli.
    klien.post("/api/aksi/label", params={
        "ds": "aksi", "klip": "klip/b1/x.mp4", "label": "pass"})
    r = klien.post("/api/aksi/label", params={
        "ds": "aksi", "klip": "klip/b1/x.mp4", "label": ""}).json()
    assert r["ok"] and r["berlabel"] is False, r
    assert klip_tag.untuk(proj, "klip/b1/x.mp4")["label"] == ""


def test_label_klip_tak_ada_ditolak(klien, lingkungan):
    """Rute menolak kunci sembarang (tak boleh menulis .klip.json untuk klip
    yang tak ada di projek) — penjaga sama dengan tag.py menyaring lewat find."""
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    r = klien.post("/api/aksi/label", params={
        "ds": "aksi", "klip": "klip/b1/hantu.mp4", "label": "shoot"}).json()
    assert r["ok"] is False, r


def test_label_editor_boleh_apa_pun(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    _klip(proj, "klip/b2/y.mp4")
    tugas.undang(proj, "paul", "anggi", peran="editor")

    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    r = anggi.post("/api/aksi/label", params={
        "ds": "paul/aksi", "klip": "klip/b2/y.mp4", "label": "pass"}).json()
    assert r["ok"] and r["label"] == "pass", r


def test_label_labeler_semua_boleh_apa_pun(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    _klip(proj, "klip/b2/y.mp4")
    tugas.undang(proj, "paul", "anggi", peran="pelabel", akses="semua")

    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    r = anggi.post("/api/aksi/label", params={
        "ds": "paul/aksi", "klip": "klip/b2/y.mp4", "label": "shoot"}).json()
    assert r["ok"], r


def test_label_labeler_spesifik_scope(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    _klip(proj, "klip/b1/dalam.mp4")
    _klip(proj, "klip/b2/luar.mp4")
    tugas.undang(proj, "paul", "anggi", peran="pelabel", akses="spesifik",
                 batch=["b1"])

    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    # Dalam scope (b1): boleh.
    r = anggi.post("/api/aksi/label", params={
        "ds": "paul/aksi", "klip": "klip/b1/dalam.mp4", "label": "shoot"}).json()
    assert r["ok"], r
    # Di luar scope (b2): ditolak, label tak berubah.
    r = anggi.post("/api/aksi/label", params={
        "ds": "paul/aksi", "klip": "klip/b2/luar.mp4", "label": "shoot"}).json()
    assert r["ok"] is False, r
    assert klip_tag.untuk(proj, "klip/b2/luar.mp4")["label"] == ""


def test_label_bukan_anggota_ditolak(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    _klip(proj, "klip/b1/x.mp4")

    # Anggi TIDAK diundang: projeknya tak teratasi untuknya.
    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    r = anggi.post("/api/aksi/label", params={
        "ds": "paul/aksi", "klip": "klip/b1/x.mp4", "label": "shoot"}).json()
    assert r["ok"] is False, r
    assert klip_tag.untuk(proj, "klip/b1/x.mp4")["label"] == ""


# ==================================================== GET /klip (Range)

def test_klip_sajikan_range_206(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    isi = bytes((i * 7) % 256 for i in range(5000))
    _klip(proj, "klip/b1/x.mp4", isi)

    # Permintaan penuh: 200 + Accept-Ranges supaya peramban tahu seek didukung.
    r = klien.get("/klip", params={"ds": "aksi", "name": "klip/b1/x.mp4"})
    assert r.status_code == 200
    assert r.headers.get("accept-ranges") == "bytes"
    assert r.content == isi

    # Permintaan Range: 206 + Content-Range tepat + byte yang tepat.
    r = klien.get("/klip", params={"ds": "aksi", "name": "klip/b1/x.mp4"},
                  headers={"Range": "bytes=10-19"})
    assert r.status_code == 206, r.text[:120]
    assert r.headers["content-range"] == f"bytes 10-19/{len(isi)}"
    assert r.headers["content-length"] == "10"
    assert r.content == isi[10:20]


def test_klip_bukan_anggota_dan_path_keluar_ditolak(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    _klip(proj, "klip/b1/x.mp4")
    _klip(proj, "klip/_ditolak/buang.mp4")
    (proj / "_sumber").mkdir(exist_ok=True)
    (proj / "_sumber" / "rahasia.mp4").write_bytes(b"rahasia")

    # Bukan anggota: ditolak.
    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    assert anggi.get("/klip", params={
        "ds": "paul/aksi", "name": "klip/b1/x.mp4"}).status_code in (403, 404)

    # Path keluar klip/ (lewat ..), absolut, dan ke _ditolak: semua ditolak.
    for nama in ("klip/../_sumber/rahasia.mp4", "/etc/passwd",
                 "klip/_ditolak/buang.mp4", "klip/b1/hantu.mp4"):
        assert klien.get("/klip", params={"ds": "aksi", "name": nama}
                         ).status_code == 404, nama


# --- penyajian mp4 nyata (digerbang ffmpeg) ---------------------------------
_SIAP, _ALASAN = klip.siap_video()


@pytest.mark.skipif(not _SIAP, reason=f"ffmpeg tak siap: {_ALASAN}")
def test_klip_mp4_nyata_range(klien, lingkungan):
    """mp4 asli (synth ffmpeg) tetap disajikan 206 dengan byte yang tepat —
    yang dibutuhkan <video> untuk scrub."""
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    kp = proj / "klip" / "b1" / "nyata.mp4"
    kp.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg, _, enc, opts = klip.detect_ffmpeg()
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", "testsrc=duration=1:size=64x64:rate=10",
           "-c:v", enc, *opts, "-pix_fmt", "yuv420p", "-an", "-y", str(kp)]
    assert subprocess.run(cmd, capture_output=True, timeout=120).returncode == 0

    raw = kp.read_bytes()
    r = klien.get("/klip", params={"ds": "aksi", "name": "klip/b1/nyata.mp4"},
                  headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["content-type"] == "video/mp4"
    assert r.content == raw[:100]


# ==================================================== GET /aksi + nav

def test_aksi_render_dan_subset(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi", kelas=("shoot", "pass", "dribble"))
    proj = lingkungan["ruang"] / "aksi"
    _klip(proj, "klip/b1/shoot_vid_t0001.mp4")
    _klip(proj, "klip/b1/pass_vid_t0002.mp4")

    r = klien.get("/aksi?ds=aksi")
    assert r.status_code == 200, r.text[:200]
    html = r.text
    # Kelas tampil, data awal tertanam, dan sidebar memunculkan Label Aksi.
    assert "dribble" in html and "ak-awal" in html
    assert "Label Aksi" in html and "/aksi?ds=" in html


def test_aksi_keterangan_scope(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    _klip(proj, "klip/b1/a.mp4")
    _klip(proj, "klip/b2/b.mp4")
    tugas.undang(proj, "paul", "anggi", peran="pelabel", akses="spesifik",
                 batch=["b1"])

    # Owner: seluruh klip.
    r = klien.get("/api/aksi/keterangan?ds=aksi").json()
    assert r["ok"] and {k["rel"] for k in r["klips"]} == {
        "klip/b1/a.mp4", "klip/b2/b.mp4"}

    # Labeler spesifik: hanya batch dalam scope.
    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    r = anggi.get("/api/aksi/keterangan?ds=paul/aksi").json()
    assert r["ok"] and {k["rel"] for k in r["klips"]} == {"klip/b1/a.mp4"}


def test_aksi_bukan_projek_aksi_dialihkan(klien, lingkungan):
    """Projek video tanpa kelas aksi (atau image) dialihkan ke /anotasi, bukan
    halaman tombol kosong."""
    masuk(klien, "paul", PW_PAUL)
    klien.post("/api/projek/baru?nama=vid&jenis=video")
    r = klien.get("/aksi?ds=vid", follow_redirects=False)
    assert r.status_code == 303 and "/anotasi" in r.headers["location"]


def test_anotasi_image_tak_regresi_dan_sidebar(klien, lingkungan):
    """Projek image: /anotasi tetap jalan, dan sidebar TIDAK memunculkan entri
    aksi — wiring nav additif, tak merusak projek image."""
    masuk(klien, "paul", PW_PAUL)
    klien.post("/api/projek/baru?nama=img&jenis=image")
    r = klien.get("/anotasi?ds=img")
    assert r.status_code == 200, r.text[:200]
    assert "/aksi?ds=" not in r.text
    assert "Anotasi" in r.text
