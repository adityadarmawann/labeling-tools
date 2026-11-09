"""
Penanda "perlu dicek": hasil auto-label (prelabel/autolabel_zero) ditulis dengan
field top-level .json `perlu_dicek=True`, tampil sebagai badge + filter di grid,
dan DIBERSIHKAN begitu manusia menyimpan lewat /api/simpan (verifikasi).
"""
from __future__ import annotations

import json

from conftest import buat_dataset, PW_PAUL, masuk
from app.services import scanner, tugas
from app.routers import annotate


def _tulis_json(ip, perlu_dicek):
    ip.with_suffix(".json").write_text(json.dumps({
        "version": "5.5.0", "flags": {},
        "shapes": [{"label": "botol", "text": "", "points": [[1, 1], [9, 9]],
                    "group_id": None, "shape_type": "rectangle", "flags": {}}],
        "imagePath": ip.name, "imageData": None,
        "imageHeight": 60, "imageWidth": 80,
        "perlu_dicek": perlu_dicek}), encoding="utf-8")


# ---- lapisan scanner ---------------------------------------------------------
def test_scanner_baca_perlu_dicek(tmp_path):
    d = buat_dataset(tmp_path / "proj", 2, 0)
    ips = sorted(d.glob("*.jpg"))
    _tulis_json(ips[0], True)
    _tulis_json(ips[1], False)
    items, _ = scanner.scan(d)
    byname = {it["img"].name: it for it in items}
    assert scanner.perlu_dicek(byname[ips[0].name]) is True
    assert scanner.perlu_dicek(byname[ips[1].name]) is False
    # meta_simpan juga menyurfacekannya (dipakai badge per ubin).
    assert scanner.meta_simpan(byname[ips[0].name])["perlu_dicek"] is True
    assert scanner.meta_simpan(byname[ips[1].name])["perlu_dicek"] is False


# ---- filter grid "dicek" -----------------------------------------------------
def test_gambar_konteks_job_saring_dicek(tmp_path):
    d = buat_dataset(tmp_path / "proj", 3, 0)
    ips = sorted(d.glob("*.jpg"))
    _tulis_json(ips[0], True)          # perlu dicek
    _tulis_json(ips[1], False)         # sudah terverifikasi (berlabel biasa)
    # ips[2] tetap belum (tanpa json)
    nama = [p.name for p in ips]
    tugas.tugaskan(d, "darma", "darma", nama)
    data = tugas.baca(d, "darma")
    tid = next(iter(data["tugas"]))
    items, _ = scanner.scan(d)

    dicek = tugas.gambar_konteks_job(items, data, d, tid, saring="dicek")
    assert [it["img"].name for it in dicek] == [ips[0].name]   # hanya yang perlu dicek
    sudah = tugas.gambar_konteks_job(items, data, d, tid, saring="sudah")
    assert {it["img"].name for it in sudah} == {ips[0].name, ips[1].name}
    belum = tugas.gambar_konteks_job(items, data, d, tid, saring="belum")
    assert [it["img"].name for it in belum] == [ips[2].name]


# ---- bersih saat simpan manual ----------------------------------------------
def test_kunci_atas_memuat_perlu_dicek():
    # Jaminan: perlu_dicek 'milik kita' -> tak diteruskan apa adanya saat simpan.
    assert "perlu_dicek" in annotate.KUNCI_ATAS


def test_grid_filter_dicek(klien, lingkungan):
    import pathlib
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "grid", 3, 0)
    klien.post(f"/setsrc?path={d}")
    ips = sorted(d.glob("*.jpg"))
    g = [str(p) for p in ips]
    tid = klien.post("/api/tugas/bagi",
                     json={"pelabel": "paul", "gambar": g}).json()["id"]
    _tulis_json(ips[0], True)       # perlu dicek (hasil auto-label)
    _tulis_json(ips[1], False)      # sudah terverifikasi manusia
    # ips[2] tetap belum (tanpa json)
    r = klien.get(f"/tugas/{tid}", params={"ds": d.name, "saring": "dicek"})
    assert r.status_code == 200
    html = r.text
    # Namanya "Belum diverifikasi", BUKAN "Perlu dicek": kata itu sudah dipakai
    # halaman Dataset untuk arti lain sama sekali (anotasi bermasalah menurut
    # pemindai), dan dua arti yang memakai kata yang sama persis membuat orang
    # mengira keduanya menghitung hal yang sama.
    assert "Belum diverifikasi" in html          # tab tersedia
    assert "Terverifikasi" in html               # pasangannya ikut muncul
    assert ips[0].name in html                   # yang belum diverifikasi tampil
    assert ips[1].name not in html               # terverifikasi -> terfilter
    assert ips[2].name not in html               # belum -> terfilter


def test_grid_filter_terverifikasi(klien, lingkungan):
    """Pasangan dari "dicek": sudah dilabeli DAN sudah dilewati manusia.

    Tanpa saringan ini, "sudah dilabeli" mencampur hasil AI mentah dengan yang
    benar-benar selesai diperiksa, dan tidak ada satu pun angka yang menunjukkan
    kemajuan verifikasi.
    """
    import pathlib
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "grid-verif", 4, 0)
    klien.post(f"/setsrc?path={d}")
    ips = sorted(d.glob("*.jpg"))
    tid = klien.post("/api/tugas/bagi",
                     json={"pelabel": "paul",
                           "gambar": [str(p) for p in ips]}).json()["id"]
    # ips[0] SENGAJA tidak dipakai menguji: gambar pertama projek jadi poster
    # di bilah sisi, jadi namanya selalu ada di halaman apa pun saringannya.
    _tulis_json(ips[1], False)      # sudah diverifikasi manusia
    _tulis_json(ips[2], True)       # hasil auto-label, belum diverifikasi
    # ips[3] tetap belum dilabeli

    def kisi(saring: str) -> str:
        """HANYA kisi gambar job-nya. Bilah sisi menampilkan sampul projek,
        dan namanya ikut muncul di halaman apa pun saringannya -- memeriksa
        seluruh HTML membuat tesnya mengukur hal lain."""
        h = klien.get(f"/tugas/{tid}", params={"ds": d.name,
                                               "saring": saring}).text
        assert 'id="jb-kisi"' in h
        return h.split('id="jb-kisi"', 1)[1]

    k = kisi("verif")
    assert ips[1].name in k                      # hanya yang terverifikasi
    assert ips[2].name not in k
    assert ips[3].name not in k

    # dan pasangannya menyaring kebalikannya
    k2 = kisi("dicek")
    assert ips[2].name in k2
    assert ips[1].name not in k2 and ips[3].name not in k2


def test_saring_tak_dikenal_jatuh_ke_semua(klien, lingkungan):
    import pathlib
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "grid-ngawur", 2, 0)
    klien.post(f"/setsrc?path={d}")
    ips = sorted(d.glob("*.jpg"))
    tid = klien.post("/api/tugas/bagi",
                     json={"pelabel": "paul",
                           "gambar": [str(p) for p in ips]}).json()["id"]
    html = klien.get(f"/tugas/{tid}",
                     params={"ds": d.name, "saring": "ngawur"}).text
    for ip in ips:
        assert ip.name in html


def test_simpan_manual_membersihkan_perlu_dicek(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d = lingkungan["ruang"] / "cek"
    buat_dataset(d, 1, 0)
    ip = sorted(d.glob("*.jpg"))[0]
    _tulis_json(ip, True)                      # seolah hasil auto-label
    klien.post(f"/setsrc?path={d}")
    assert json.loads(ip.with_suffix(".json").read_text())["perlu_dicek"] is True
    # Manusia menyimpan -> terverifikasi -> penanda hilang.
    r = klien.post("/api/simpan", json={"path": str(ip), "shapes": [
        {"label": "botol", "shape_type": "rectangle", "points": [[1, 1], [9, 9]],
         "group_id": None, "flags": {}}]})
    assert r.json()["ok"] is True
    jd = json.loads(ip.with_suffix(".json").read_text())
    assert "perlu_dicek" not in jd             # bersih
    assert jd["pelabel"] == "paul"             # dan tercatat manusia yang simpan
