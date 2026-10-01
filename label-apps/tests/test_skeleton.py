"""
Uji template skeleton (keypoint/pose) tingkat projek — langkah 3k-a.

Yang dijaga: template disimpan & disaring konsisten (edge menunjuk titik yang
ada, flip_idx panjangnya = jumlah titik), jumlah/nama keypoint BEBAS per projek
(court 33, botol 4 — bukan angka tetap), dan menyimpan template menandai projek
jadi jenis 'kerangka'. Plus rute: pemilik boleh set, anggota boleh baca.
"""
from __future__ import annotations

import pytest

from app.services import tugas


def _projek(tmp_path, nama="p"):
    d = tmp_path / nama
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------ _sah_skeleton

def test_sah_skeleton_menyaring_konsisten():
    raw = {
        "kelas": "  court  ",
        "titik": ["tl", "tr", "tl", "", "  bl  "],     # dup & kosong dibuang
        "edge": [[0, 1], [1, 0], [0, 0], [0, 9], [1, 2]],  # dup/self/luar dibuang
        "flip_idx": [1, 0, 2],                          # panjang 3 == K -> dipakai
        "warna": ["#fff", "#000", "#abc", "#xxx"],      # dipotong ke K
    }
    sk = tugas._sah_skeleton(raw)
    assert sk["kelas"] == "court"
    assert sk["titik"] == ["tl", "tr", "bl"]            # K = 3
    assert sk["edge"] == [[0, 1], [1, 2]]
    assert sk["flip_idx"] == [1, 0, 2]
    assert len(sk["warna"]) == 3


def test_flip_idx_salah_panjang_jadi_identitas():
    sk = tugas._sah_skeleton({"titik": ["a", "b", "c"], "flip_idx": [1, 0]})
    assert sk["flip_idx"] == []                         # != K -> dikosongkan
    sk2 = tugas._sah_skeleton({"titik": ["a", "b"], "flip_idx": [1, 5]})
    assert sk2["flip_idx"] == []                        # nilai di luar rentang


def test_sah_skeleton_data_rusak_jadi_kosong():
    assert tugas._sah_skeleton(None)["titik"] == []
    assert tugas._sah_skeleton("bukan dict")["titik"] == []
    assert tugas._sah_skeleton({"titik": "bukan list"})["titik"] == []


# ------------------------------------------------------------ set_skeleton

def test_set_skeleton_tersimpan_dan_tandai_kerangka(tmp_path):
    d = _projek(tmp_path)
    r = tugas.set_skeleton(d, {"kelas": "court", "titik": ["a", "b", "c"],
                               "edge": [[0, 1], [1, 2]], "flip_idx": [0, 1, 2]},
                           pemilik="own")
    assert r["ok"]
    data = tugas.baca(d, "own")
    assert data["skeleton"]["titik"] == ["a", "b", "c"]
    assert data["skeleton"]["edge"] == [[0, 1], [1, 2]]
    assert tugas.skeleton_aktif(data) is True
    # Punya titik -> otomatis jenis 'kerangka'.
    assert data["jenis_anotasi"] == "kerangka"


def test_kosongkan_skeleton_lepas_dari_kerangka(tmp_path):
    d = _projek(tmp_path)
    tugas.set_skeleton(d, {"titik": ["a", "b"]}, pemilik="own")
    assert tugas.baca(d, "own")["jenis_anotasi"] == "kerangka"
    tugas.set_skeleton(d, {"titik": []}, pemilik="own")      # dikosongkan
    data = tugas.baca(d, "own")
    assert tugas.skeleton_aktif(data) is False
    assert data["jenis_anotasi"] == ""                  # lepas dari kerangka


def test_jumlah_keypoint_bebas_per_projek(tmp_path):
    """Inti: tiap projek punya K sendiri — bukan angka tetap 33."""
    botol = _projek(tmp_path, "botol")
    court = _projek(tmp_path, "court")
    tugas.set_skeleton(botol, {"kelas": "botol",
                               "titik": ["tutup", "leher", "badan", "dasar"]},
                       pemilik="own")
    tugas.set_skeleton(court, {"kelas": "court",
                               "titik": [f"k{i}" for i in range(33)]},
                       pemilik="own")
    assert len(tugas.baca(botol, "own")["skeleton"]["titik"]) == 4
    assert len(tugas.baca(court, "own")["skeleton"]["titik"]) == 33


def test_kerangka_jenis_anotasi_sah():
    assert "kerangka" in tugas.JENIS_ANOTASI


# ------------------------------------------------------------ rute HTTP

def test_rute_skeleton_pemilik_set_anggota_baca(klien, aplikasi, lingkungan):
    import pathlib

    from conftest import klien_baru
    from tests.test_data import masuk, PW_ANGGI, PW_PAUL
    from tests.test_projek import _projek as buat_projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_projek(ruang, "pose-uji", n=2, label=False)
    klien.post(f"/setsrc?path={d}")
    tugas.undang(d, "paul", "anggi", peran="pelabel", akses="semua")

    # Pemilik menyetel template.
    r = klien.post("/api/tugas/skeleton?ds=pose-uji", json={"skeleton": {
        "kelas": "botol", "titik": ["tutup", "leher", "dasar"],
        "edge": [[0, 1], [1, 2]], "flip_idx": [0, 1, 2]}}).json()
    assert r["ok"] and r["skeleton"]["titik"] == ["tutup", "leher", "dasar"]

    # Anggota (pelabel) boleh MEMBACA (butuh nama keypoint untuk melabeli).
    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    anggi.get("/?ds=paul/pose-uji")
    g = anggi.get("/api/tugas/skeleton?ds=paul/pose-uji").json()
    assert g["ok"] and g["jenis"] == "kerangka"
    assert g["skeleton"]["titik"] == ["tutup", "leher", "dasar"]
    assert g["boleh_ubah"] is False

    # Tapi tak boleh MENGUBAH.
    r2 = anggi.post("/api/tugas/skeleton?ds=paul/pose-uji",
                    json={"skeleton": {"titik": ["x"]}}).json()
    assert r2["ok"] is False and "pemilik" in r2["error"]


def test_skeleton_tanpa_ds_pakai_projek_terbuka(klien, lingkungan):
    """Dari kanvas /label, rute dipanggil tanpa ds -> pakai sess.src."""
    import pathlib

    from tests.test_data import masuk, PW_PAUL
    from tests.test_projek import _projek as buat_projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_projek(ruang, "pose-src", n=2, label=False)
    klien.post(f"/setsrc?path={d}")
    r = klien.post("/api/tugas/skeleton", json={"skeleton": {
        "kelas": "x", "titik": ["a", "b"], "edge": [[0, 1]],
        "flip_idx": [1, 0]}}).json()
    assert r["ok"] and r["skeleton"]["titik"] == ["a", "b"]
    g = klien.get("/api/tugas/skeleton").json()
    assert g["ok"] and g["skeleton"]["flip_idx"] == [1, 0]


def test_label_page_bawa_template_dan_tombol_editor(klien, lingkungan):
    """Halaman /label menyertakan template keypoint di data awal + tombol editor
    untuk pemilik."""
    import pathlib

    from tests.test_data import masuk, PW_PAUL
    from tests.test_projek import _projek as buat_projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_projek(ruang, "pose-label", n=2, label=False)
    klien.post(f"/setsrc?path={d}")
    tugas.set_skeleton(d, {"kelas": "botol",
                           "titik": ["tutup", "leher", "dasar"],
                           "edge": [[0, 1], [1, 2]]}, pemilik="paul")
    g = sorted(str(q) for q in d.glob("*.jpg"))
    h = klien.get(f"/label?path={g[0]}").text
    assert "tutup" in h and "leher" in h            # template di #data-awal
    assert 'id="btn-skeleton-tpl"' in h             # tombol editor (pemilik)


def test_pose_simpan_muat_round_trip(klien, lingkungan):
    """Anotasi keypoint (titik + visibilitas + group_id + bbox) bertahan lewat
    /api/simpan -> .json -> dibuka lagi. Menunggangi skema shape yang ada."""
    import json
    import pathlib

    from tests.test_data import masuk, PW_PAUL
    from tests.test_projek import _projek as buat_projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_projek(ruang, "pose-rt", n=1, label=False)
    klien.post(f"/setsrc?path={d}")
    tugas.set_skeleton(d, {"kelas": "botol", "titik": ["a", "b"],
                           "edge": [[0, 1]]}, pemilik="paul")
    g = sorted(str(q) for q in d.glob("*.jpg"))[0]
    pt = lambda nm, x, y, v: {"label": nm, "shape_type": "point",
                              "points": [[x, y]], "group_id": 0,
                              "flags": {"v": v}, "text": "", "titipan": {}}
    shapes = [pt("a", 8, 8, 2), pt("b", 8, 18, 1),
              {"label": "botol", "shape_type": "rectangle",
               "points": [[4, 4], [18, 22]], "group_id": 0,
               "flags": {}, "text": "", "titipan": {"kp_auto": True}}]
    assert klien.post("/api/simpan", json={"path": g, "shapes": shapes}).json()["ok"]

    # .json yang ditulis mempertahankan tipe, visibilitas, group_id, bbox.
    data = json.loads(pathlib.Path(g).with_suffix(".json").read_text())
    byl = {s["label"]: s for s in data["shapes"]}
    assert byl["a"]["shape_type"] == "point" and byl["a"]["group_id"] == 0
    assert byl["a"]["flags"]["v"] == 2 and byl["b"]["flags"]["v"] == 1
    assert byl["botol"]["shape_type"] == "rectangle" and byl["botol"]["group_id"] == 0
    # Dibuka lagi ke kanvas: keypoint + visibilitas ikut ke data awal.
    h = klien.get(f"/label?path={g}").text
    assert '"v"' in h and '"group_id"' in h
