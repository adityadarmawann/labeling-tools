"""
Uji penyaring klip berdasar objek (Langkah 5), services/klip_filter.py.

CPU-only lewat metode "warna" (cv2 + numpy, tanpa ultralytics/torch/jaringan).
Klip sintetis dibuat dengan cv2: sebagian frame memuat lingkaran ORANYE terang
(objek "ada"), sebagian polos (objek "tak ada"). Kalau cv2 tak bisa menulis/
membaca mp4 di lingkungan ini, seluruh modul di-SKIP bersih — bukan gagal.

Yang dijaga:
  - metode warna menyimpan klip berblob, menolak yang polos;
  - ambang min_frame & langkah sampling `setiap` benar-benar dihormati;
  - pratinjau (dry-run) TIDAK memindah apa pun, pass-rate benar;
  - terapkan memindah yang ditolak ke klip/_ditolak/<batch>/ (asli hilang dari
    klip/<batch>/, ada di _ditolak, bisa dipulihkan), menulis .filter.json, dan
    klip_scan.pindai tak lagi melihatnya;
  - sasaran DAPAT DIKONFIGURASI: ganti rentang HSV -> klip mana yang lolos ikut
    berubah (bukti tak di-hardcode);
  - siap_filter("yolo", ...) jujur melaporkan ultralytics tak ada, dan jalur
    yolo TIDAK dijalankan di CI;
  - rute HTTP /api/video/filter untuk pratinjau & terapkan.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services import klip_filter, klip_scan, klip_tag

W = H = 224
FPS = 25
ORANYE = (0, 140, 255)      # BGR; HSV ~ H16 S255 V255 -> dalam rentang bawaan
HIJAU = (0, 255, 0)         # BGR; HSV ~ H60 -> di LUAR rentang oranye bawaan


def _tulis_klip(path: Path, warna_bgr=None, n: int = 24, radius: int = 40) -> bool:
    """Tulis mp4 sintetis. Blob lingkaran `warna_bgr` di tiap frame kalau diberi;
    selain itu latar abu polos (saturasi rendah -> tak terdeteksi warna)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    if not vw.isOpened():
        return False
    for _ in range(n):
        frame = np.full((H, W, 3), 30, np.uint8)
        if warna_bgr is not None:
            cv2.circle(frame, (W // 2, H // 2), radius, warna_bgr, -1)
        vw.write(frame)
    vw.release()
    return path.is_file() and path.stat().st_size > 0


def _bisa_mp4() -> bool:
    """Probe sekali: lingkungan ini bisa menulis DAN membaca kembali mp4?"""
    tmp = Path(tempfile.mkdtemp(prefix="klipfilter-probe-"))
    p = tmp / "probe.mp4"
    if not _tulis_klip(p, warna_bgr=ORANYE, n=10):
        return False
    cap = cv2.VideoCapture(str(p))
    ok = cap.isOpened()
    jml = 0
    if ok:
        while True:
            r, _ = cap.read()
            if not r:
                break
            jml += 1
    cap.release()
    return ok and jml > 0


_BISA = _bisa_mp4()
pytestmark = pytest.mark.skipif(
    not _BISA, reason="cv2 tak bisa menulis/membaca mp4 di lingkungan ini")


def _proj_dengan(tmp_path, **klip) -> Path:
    """Projek dengan klip {nama_relatif_di_b1: warna_bgr|None}."""
    d = tmp_path / "proj"
    for nama, warna in klip.items():
        assert _tulis_klip(d / "klip" / "b1" / f"{nama}.mp4", warna_bgr=warna)
    return d


# ======================================================= metode warna

def test_warna_simpan_blob_tolak_polos(tmp_path):
    d = _proj_dengan(tmp_path, blob=ORANYE, polos=None)
    lap = klip_filter.pratinjau(d, "b1", {"metode": "warna"})
    assert lap["total"] == 2
    assert lap["klip"]["klip/b1/blob.mp4"]["keep"] is True
    assert lap["klip"]["klip/b1/polos.mp4"]["keep"] is False
    assert lap["pass_rate"] == 0.5
    # Hitungan per-kelas: objek "ada" hanya di klip berblob.
    assert lap["per_kelas"].get("warna") == 1


def test_min_frame_dan_setiap_dihormati(tmp_path):
    # Satu klip SELURUHNYA berblob.
    d = _proj_dengan(tmp_path, blob=ORANYE)
    rel = "klip/b1/blob.mp4"

    # min_frame kecil -> lolos.
    lolos = klip_filter.pratinjau(d, "b1", {"metode": "warna", "min_frame": 2,
                                            "setiap": 4})
    assert lolos["klip"][rel]["keep"] is True
    assert lolos["klip"][rel]["frame_objek"] >= 2

    # Ambang min_frame mustahil -> ditolak, walau objek ADA (ambang dihormati).
    ketat = klip_filter.pratinjau(d, "b1", {"metode": "warna", "min_frame": 100,
                                            "setiap": 4})
    assert ketat["klip"][rel]["keep"] is False

    # Langkah sampling raksasa -> cuma 1 frame diperiksa -> tak capai min_frame 2.
    jarang = klip_filter.pratinjau(d, "b1", {"metode": "warna", "min_frame": 2,
                                             "setiap": 1000})
    assert jarang["klip"][rel]["frame_cek"] == 1
    assert jarang["klip"][rel]["keep"] is False


def test_pratinjau_tak_memindah_apa_pun(tmp_path):
    d = _proj_dengan(tmp_path, blob=ORANYE, polos=None)
    lap = klip_filter.pratinjau(d, "b1", {"metode": "warna"})
    assert lap["pratinjau"] is True
    # Kedua berkas masih di tempatnya; tak ada folder _ditolak dibuat.
    assert (d / "klip" / "b1" / "blob.mp4").is_file()
    assert (d / "klip" / "b1" / "polos.mp4").is_file()
    assert not (d / "klip" / "_ditolak").exists()
    assert not (d / "klip" / "b1" / ".filter.json").exists()


# ======================================================= terapkan

def test_terapkan_pindah_ditolak_dan_dapat_dipulihkan(tmp_path):
    d = _proj_dengan(tmp_path, blob=ORANYE, polos=None)
    lap = klip_filter.terapkan(d, "b1", {"metode": "warna"})

    assert lap["pratinjau"] is False
    assert lap["dipindah"] == ["klip/b1/polos.mp4"]

    # Yang lolos tetap; yang ditolak PINDAH (bukan dihapus) ke _ditolak/<batch>/.
    assert (d / "klip" / "b1" / "blob.mp4").is_file()
    assert not (d / "klip" / "b1" / "polos.mp4").exists()
    pulih = d / "klip" / "_ditolak" / "b1" / "polos.mp4"
    assert pulih.is_file() and pulih.stat().st_size > 0      # bisa dipulihkan

    # Laporan .filter.json tertulis di folder batch.
    lapfile = d / "klip" / "b1" / ".filter.json"
    assert lapfile.is_file()
    import json
    isi = json.loads(lapfile.read_text())
    assert isi["disimpan"] == ["klip/b1/blob.mp4"]
    assert isi["ditolak_klip"] == ["klip/b1/polos.mp4"]
    assert isi["metode"] == "warna" and "konfig" in isi and isi["waktu"]

    # klip_scan tak lagi melihat klip yang ditolak (ia melewati _ditolak).
    pindai = klip_scan.pindai(d)
    assert pindai["semua"] == {"klip/b1/blob.mp4"}


def test_terapkan_semua_lolos_tak_memindah(tmp_path):
    d = _proj_dengan(tmp_path, a=ORANYE, b=ORANYE)
    lap = klip_filter.terapkan(d, "b1", {"metode": "warna"})
    assert lap["dipindah"] == [] and lap["ditolak"] == 0
    assert not (d / "klip" / "_ditolak").exists()


# ======================================================= sasaran dapat dikonfig

def test_sasaran_dapat_dikonfig_membalik_lolos(tmp_path):
    # Klip berblob HIJAU: dengan rentang oranye bawaan -> DITOLAK.
    d = _proj_dengan(tmp_path, hijau=HIJAU)
    rel = "klip/b1/hijau.mp4"
    bawaan = klip_filter.pratinjau(d, "b1", {"metode": "warna"})
    assert bawaan["klip"][rel]["keep"] is False
    assert bawaan["pass_rate"] == 0.0

    # Arahkan HSV ke hijau -> klip yang sama kini LOLOS. Bukti sasaran tak
    # di-hardcode "bola oranye": satu-satunya yang berubah adalah rentang HSV.
    hijau = klip_filter.pratinjau(d, "b1", {"metode": "warna", "hsv": {
        "bawah": [40, 100, 100], "atas": [80, 255, 255],
        "min_area": 150, "circularity": 0.5}})
    assert hijau["klip"][rel]["keep"] is True
    assert hijau["pass_rate"] == 1.0


# ======================================================= konfig_sah

def test_konfig_sah_menjepit_dan_lengkapi():
    k = klip_filter.konfig_sah({"metode": "aneh", "conf": 5, "min_frame": 0,
                                "setiap": -3, "kelas": ["ball", 2, "", True]})
    assert k["metode"] == "warna"            # tak dikenal -> warna
    assert k["conf"] == 1.0                   # dijepit ke [0,1]
    assert k["min_frame"] == 1 and k["setiap"] == 1
    assert k["kelas"] == ["ball", 2]          # kosong & bool dibuang
    assert set(k["hsv"]) == {"bawah", "atas", "min_area", "circularity"}


# ======================================================= siap_filter (yolo digerbang)

def test_siap_filter_warna_selalu_bisa():
    bisa, _ = klip_filter.siap_filter("warna")
    assert bisa is True


def test_siap_filter_yolo_jujur_tanpa_ultralytics():
    # Di .venv CPU tak ada ultralytics: siap_filter harus menjawab False dengan
    # alasan yang menyebut ultralytics — dan jalur yolo TAK pernah dijalankan.
    import importlib.util
    if importlib.util.find_spec("ultralytics") is not None:
        pytest.skip("ultralytics ada; uji kejujuran gerbang hanya relevan di CPU")
    bisa, alasan = klip_filter.siap_filter("yolo", "best-object-basketball.pt")
    assert bisa is False and "ultralytics" in alasan


# ======================================================= rute HTTP

def test_rute_filter_pratinjau_lalu_terapkan(klien, lingkungan):
    from tests.conftest import PW_PAUL, masuk
    from tests.test_video_unggah import _buat_projek

    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-filter", "video")
    proj = lingkungan["ruang"] / "aksi-filter"
    assert _tulis_klip(proj / "klip" / "b1" / "blob.mp4", warna_bgr=ORANYE)
    assert _tulis_klip(proj / "klip" / "b1" / "polos.mp4", warna_bgr=None)

    # Dry-run: menghitung, tak memindah.
    r = klien.post("/api/video/filter?ds=aksi-filter&batch=b1&pratinjau=true",
                   json={"metode": "warna"}).json()
    assert r["ok"] and r["total"] == 2 and r["pass_rate"] == 0.5
    assert (proj / "klip" / "b1" / "polos.mp4").is_file()    # belum dipindah

    # Terapkan: polos pindah ke _ditolak.
    r2 = klien.post("/api/video/filter?ds=aksi-filter&batch=b1&pratinjau=false",
                    json={"metode": "warna"}).json()
    assert r2["ok"] and r2["dipindah"] == ["klip/b1/polos.mp4"]
    assert not (proj / "klip" / "b1" / "polos.mp4").exists()
    assert (proj / "klip" / "_ditolak" / "b1" / "polos.mp4").is_file()


def test_rute_filter_tolak_bukan_anggota(klien, aplikasi, lingkungan):
    from tests.conftest import PW_ANGGI, PW_PAUL, klien_baru, masuk
    from tests.test_video_unggah import _buat_projek

    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-priv", "video")

    orang_luar = klien_baru(aplikasi, "anggi", PW_ANGGI)
    r = orang_luar.post("/api/video/filter?ds=paul/aksi-priv&batch=b1",
                        json={"metode": "warna"}).json()
    assert r["ok"] is False and "error" in r
