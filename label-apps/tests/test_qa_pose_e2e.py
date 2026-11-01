"""
QA end-to-end pipeline keypoint (Langkah 6) — TANPA training (GPU sibuk).

Menelusuri rantai nyata: buat projek pose -> template -> label (save) ->
baca balik (scanner) -> ekspor YOLO-pose -> build versi (aug). Memastikan
slot keypoint, visibilitas, bbox, dan group_id sinkron tiap tahap.
"""
from __future__ import annotations

import io
import pathlib
import zipfile

import numpy as np

from app.services import buatversi, export, scanner, tugas


def _pt(nm, x, y, v, gid):
    return {"label": nm, "shape_type": "point", "points": [[x, y]],
            "group_id": gid, "flags": {"v": v}, "text": "", "titipan": {}}


def _rect(nm, x0, y0, x1, y1, gid):
    return {"label": nm, "shape_type": "rectangle", "points": [[x0, y0], [x1, y1]],
            "group_id": gid, "flags": {}, "text": "", "titipan": {"kp_auto": True}}


def test_qa_pose_pipeline(klien, lingkungan):
    from tests.test_data import masuk, PW_PAUL
    from tests.test_projek import _projek as buat_projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_projek(ruang, "qa-pose", n=2, label=False)
    klien.post(f"/setsrc?path={d}")

    # 1. Template: 3 keypoint dengan pasangan cermin kiri-kanan.
    r = klien.post("/api/tugas/skeleton?ds=qa-pose", json={"skeleton": {
        "kelas": "orang", "titik": ["hidung", "mata_kiri", "mata_kanan"],
        "edge": [[0, 1], [0, 2]], "flip_idx": [0, 2, 1]}}).json()
    assert r["ok"]
    assert tugas.baca(d, "paul")["jenis_anotasi"] == "kerangka"

    g = sorted(str(q) for q in d.glob("*.jpg"))
    # 2. Label: gambar 0 = DUA instance (grup 0 & 1), gambar 1 = satu + 1 absen.
    klien.post("/api/simpan", json={"path": g[0], "shapes": [
        _pt("hidung", 10, 10, 2, 0), _pt("mata_kiri", 6, 7, 2, 0),
        _pt("mata_kanan", 14, 7, 1, 0), _rect("orang", 4, 5, 16, 12, 0),
        _pt("hidung", 30, 10, 2, 1), _pt("mata_kiri", 26, 7, 2, 1),
        _pt("mata_kanan", 34, 7, 2, 1), _rect("orang", 24, 5, 36, 12, 1),
    ]})
    klien.post("/api/simpan", json={"path": g[1], "shapes": [
        _pt("hidung", 20, 20, 2, 0), _pt("mata_kiri", 16, 17, 2, 0),
        # mata_kanan TIDAK ditaruh -> absen; bbox tak boleh menggembung ke (0,0)
        _rect("orang", 14, 15, 24, 24, 0),
    ]})

    # 3. Baca balik via scanner: group_id + flags.v bertahan.
    shp, W, H = scanner.read_json(pathlib.Path(g[0]).with_suffix(".json"))
    byk = [s for s in shp if s["type"] == "point"]
    assert len(byk) == 6 and all(s["group_id"] in (0, 1) for s in byk)
    assert any(s["flags"].get("v") == 1 for s in byk)   # occluded bertahan

    # 4. Ekspor YOLO-pose: 3 instance total, tiap baris K=3 (9 angka kp).
    z = zipfile.ZipFile(io.BytesIO(klien.get("/ekspor?format=yolo-pose").content))
    yaml = z.read("data.yaml").decode()
    assert "kpt_shape: [3, 3]" in yaml and "flip_idx: [0, 2, 1]" in yaml
    lines = []
    for n in z.namelist():
        if "/labels/" in n and n.endswith(".txt"):
            lines += [l for l in z.read(n).decode().splitlines() if l.strip()]
    assert len(lines) == 3, lines                       # 2 + 1 instance
    for ln in lines:
        p = ln.split()
        assert p[0] == "0" and len(p) == 5 + 3 * 3       # fixed-K
    # Baris gambar-1: mata_kanan (slot 2) absen -> 0 0 0, dan bbox TAK menyentuh 0.
    baris1 = next(ln for ln in lines if ln.split()[11:14] == ["0.000000", "0.000000", "0"])
    bb = [float(x) for x in baris1.split()[1:5]]
    assert bb[0] > 0.1 and bb[1] > 0.1                   # bbox dari titik v>=1 saja

    # 5. Build versi pose dari item NYATA (save->scanner->build) + augmentasi.
    items = []
    for ip in [pathlib.Path(x) for x in g]:
        s, w, h = scanner.read_json(ip.with_suffix(".json"))
        items.append({"img": ip, "W": w, "H": h, "shapes": s})
    skel = tugas.baca(d, "paul")["skeleton"]
    peta = {pathlib.Path(g[0]).stem: "train", pathlib.Path(g[1]).stem: "valid"}
    job = buatversi.Pekerjaan(d, 1, items, {}, {"volume": {"per_gambar": 3}},
                              peta, kunci="t", jenis="kerangka", skeleton=skel)
    ring = job.jalankan()
    dirv = buatversi.dir_versi(d, 1)
    vyaml = (dirv / "data.yaml").read_text()
    assert "kpt_shape: [3, 3]" in vyaml and "names: ['orang']" in vyaml
    # Tiap baris label di versi juga K=3 (konsisten dengan kpt_shape).
    for sp in ("train", "valid"):
        for lp in (dirv / sp / "labels").glob("*.txt"):
            for ln in lp.read_text().splitlines():
                if ln.strip():
                    assert len(ln.split()) == 5 + 3 * 3, (sp, ln)
    # Train di-augment (gambar0 asli + aug); valid apa adanya (1 berkas).
    assert len(list((dirv / "train" / "labels").glob("*.txt"))) >= 2
    assert len(list((dirv / "valid" / "labels").glob("*.txt"))) == 1
    assert ring["jenis"] == "kerangka" and ring["keypoint"] == 3


def test_projek_kerangka_lewat_route_abaikan_format_kotak(klien, lingkungan):
    """REGRESI (court-basketball): projek kerangka yang JUGA memuat kotak (court
    keypoint + kotak nomor jersey) dulu terbangun jadi DETEKSI lewat route, karena
    pemilih format per-versi default ke "kotak" -> semua keypoint hilang, lalu
    training pose gagal "No kpt_shape". Kini versi_mulai memaksa jenis kerangka
    untuk projek kerangka: data.yaml pose walau resep.format='kotak'."""
    import time
    from tests.test_data import masuk, PW_PAUL
    from tests.test_projek import _projek as buat_projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_projek(ruang, "pose-campur", n=2, label=False)
    klien.post(f"/setsrc?path={d}")
    assert klien.post("/api/tugas/skeleton?ds=pose-campur", json={"skeleton": {
        "kelas": "court", "titik": ["a", "b", "c"],
        "edge": [[0, 1], [0, 2]], "flip_idx": [0, 2, 1]}}).json()["ok"]

    # Tiap gambar: keypoint court (grup 0) + satu kotak "nomor" (grup 1) yang
    # meniru nomor jersey. Build deteksi akan menjadikan "nomor" sebuah KELAS
    # dan court cuma satu kotak; build pose membuang "nomor" dan menahan keypoint.
    for ip in sorted(str(q) for q in d.glob("*.jpg")):
        klien.post("/api/simpan", json={"path": ip, "shapes": [
            _pt("a", 10, 10, 2, 0), _pt("b", 6, 7, 2, 0), _pt("c", 14, 7, 2, 0),
            _rect("court", 4, 5, 16, 12, 0), _rect("nomor", 40, 40, 50, 50, 1),
        ]})

    # resep.format='kotak' DISENGAJA -> harus diabaikan untuk projek kerangka.
    r = klien.post("/api/versi/mulai?split=50,50,0",
                   json={"resep": {"format": "kotak"}}).json()
    assert r.get("ok"), r
    for _ in range(600):
        k = klien.get("/api/versi/kemajuan").json()
        if k.get("selesai") or k.get("galat") or k.get("batal"):
            break
        time.sleep(0.1)
    assert k.get("selesai"), k
    vyaml = (buatversi.dir_versi(d, r["nomor"]) / "data.yaml").read_text()
    assert "kpt_shape: [3, 3]" in vyaml            # POSE (bukan deteksi)
    assert "names: ['court']" in vyaml             # "nomor" jatuh, tak jadi kelas
    for lp in (buatversi.dir_versi(d, r["nomor"]) / "train" / "labels").glob("*.txt"):
        for ln in lp.read_text().splitlines():
            if ln.strip():
                assert len(ln.split()) == 5 + 3 * 3, ln   # tiap baris K=3 keypoint
    # estimasi menandai projek kerangka -> wizard sembunyikan pemilih format.
    assert klien.post("/api/versi/estimasi?split=50,50,0",
                      json={"resep": {}}).json().get("kerangka") is True
