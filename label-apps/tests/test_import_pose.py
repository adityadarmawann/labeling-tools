"""
Uji IMPOR YOLO-pose (Langkah 1-5): cermin dari ekspor di test_export_pose.

Yang dijaga:
  - data.yaml pose dibaca jadi {K, flip_idx}; data.yaml bbox -> {} (tak tersentuh).
  - template auto dari spek: nama slot "01".."33", kelas, flip involusi, warna
    keluarga cermin, tata median dari label.
  - read_yolo mengubah baris 104 kolom jadi 1 rectangle + 33 point (BUKAN
    poligon 51 titik); setiap titik bawa group_id + flags.v; slot v0 tetap ada.
  - round-trip: shape terimpor -> export.baris_yolo_pose == baris sumber (<1e-6).
  - anti-regresi: poligon/seg & bbox dengan kpt=None tak berubah; poligon 51
    titik tanpa K tak dikelompokkan.
  - e2e: unggah+bongkar zip pose kecil -> skeleton_aktif, K/kelas/nama benar,
    membuka split memberi instance rectangle+K-point.

Baris 104 kolom + flip_idx 33 di bawah DISALIN dari dataset court Roboflow asli
(basketball-court-detection-2.v1i.yolov8) — berkas 226 MB-nya TIDAK dipakai di
CI; cukup satu baris nyatanya yang ditempel di sini.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services import export, scanner, tugas

# flip_idx asli court (33), involusi (0<->27, 6<->26, 7<->24, ...).
REAL_FLIP = [27, 28, 29, 30, 31, 32, 26, 24, 25, 21, 22, 23, 18, 19, 20,
             15, 16, 17, 12, 13, 14, 9, 10, 11, 7, 8, 6, 0, 1, 2, 3, 4, 5]

# Satu baris label asli: cls cx cy w h (px py v)*33 = 104 kolom.
REAL_LINE = (
    "0 0.5 0.610927037037037 1 0.7781460185185185 "
    "0.019841249999999998 0.019841296296296294 0 0.019841249999999998 "
    "0.09524203703703704 0 0.019841249999999998 0.39766305555555553 0 "
    "0.019841249999999998 0.6038587037037038 0 0.019841249999999998 "
    "0.9078650925925925 0 0.019841249999999998 0.9801587037037037 0 "
    "0.09176833333333333 0.5007608333333333 0 0.09176833333333333 "
    "0.09630018518518518 0 0.09176833333333333 0.9078650925925925 0 "
    "0.19222265624999998 0.39766305555555553 0 0.19222265624999998 "
    "0.49811731481481475 0 0.19486619791666668 0.6038587037037038 0 "
    "0.31646875 0.019841296296296294 0 0.3138252083333333 0.5007608333333333 0 "
    "0.3138252083333333 0.9801587037037037 0 0.4971934895833333 "
    "0.023199444444444443 0 0.49969604166666665 0.5000062037037037 0 "
    "0.49622906250000004 0.9801587037037037 0 0.07467526041666667 "
    "0.33002888888888887 0 0.021303020833333335 0.6384174074074075 2 "
    "0.6812764583333333 0.9801587037037037 0 0.21497302083333333 "
    "0.5003171296296296 2 0.25222828125 0.5933993518518519 2 0.296531875 "
    "0.7093046296296296 2 0.24643864583333333 0.33652814814814813 2 "
    "0.5031855729166667 0.9119220370370371 2 0.5632048958333333 "
    "0.5310239814814814 2 0.48280822916666666 0.2662689814814815 2 "
    "0.5001772395833333 0.2958046296296296 2 0.5852602604166666 "
    "0.43140046296296297 2 0.7048295833333333 0.6215925925925926 2 0.98015875 "
    "0.9078650925925925 0 0.98015875 0.9801587037037037 0")

YAML_POSE = (
    "train: ../train/images\n"
    "val: ../valid/images\n"
    "test: ../test/images\n\n"
    "kpt_shape: [33, 3]\n"
    f"flip_idx: {REAL_FLIP}\n\n"
    "nc: 1\n"
    "names: ['court']\n")

YAML_BBOX = (
    "train: ../train/images\n"
    "val: ../valid/images\n\n"
    "nc: 2\n"
    "names: ['botol', 'kaleng']\n")


# ------------------------------------------------------------ _pose_dari_yaml

def test_pose_dari_yaml_court(tmp_path):
    p = tmp_path / "data.yaml"
    p.write_text(YAML_POSE)
    spec = scanner._pose_dari_yaml(p)
    assert spec["K"] == 33
    assert spec["flip_idx"] == REAL_FLIP and len(spec["flip_idx"]) == 33


def test_pose_dari_yaml_bbox_kosong(tmp_path):
    """data.yaml deteksi biasa (tanpa kpt_shape) -> {}, jadi tak ada dataset
    bbox/seg yang salah dibaca sebagai pose."""
    p = tmp_path / "data.yaml"
    p.write_text(YAML_BBOX)
    assert scanner._pose_dari_yaml(p) == {}


def test_baca_pose_template_dari_split(tmp_path):
    """Ekspor dibuka di salah satu split tetap menemukan data.yaml akar +
    membawa names kelas."""
    (tmp_path / "data.yaml").write_text(YAML_POSE)
    (tmp_path / "train" / "images").mkdir(parents=True)
    spec = scanner.baca_pose_template(tmp_path / "train" / "images")
    assert spec["K"] == 33 and spec["names"] == {0: "court"}


# ------------------------------------------------------------ template_dari_pose

def test_template_dari_pose_court():
    """K=33 + flip_idx court -> PRESET court: nomor Roboflow, 36 edge, tata
    kanonik, warna keluarga cermin."""
    spec = {"K": 33, "flip_idx": REAL_FLIP, "names": {0: "court"}}
    tpl = tugas.template_dari_pose(spec)
    assert tpl["kelas"] == "court"
    assert tpl["titik"][:3] == ["1", "2", "4"] and tpl["titik"][-1] == "41"  # nomor
    assert len(tpl["titik"]) == 33 and len(set(tpl["titik"])) == 33          # unik
    assert len(tpl["edge"]) == 36                                            # rangka preset
    assert len(tpl["tata"]) == 33 and tpl["tata"][0] == [0.03, 0.09]         # tata kanonik
    assert len(tpl["warna"]) == 33
    # Keluarga cermin ada: ada yang kiri (merah) DAN kanan (teal).
    assert tugas.WARNA_KIRI in tpl["warna"] and tugas.WARNA_KANAN in tpl["warna"]
    # flip involusi bertahan lewat _sah_skeleton (pintu tunggal template).
    sk = tugas._sah_skeleton(tpl)
    assert sk["flip_idx"] == REAL_FLIP
    assert sk["titik"] == tpl["titik"] and len(sk["edge"]) == 36 and len(sk["tata"]) == 33


def test_warna_cermin_kiri_kanan_tengah():
    # flip [0,2,1]: slot0 swa-peta -> tengah; slot1<slot2 pasangan -> kiri/kanan.
    w = tugas._warna_cermin(3, [0, 2, 1])
    assert w[0] == tugas.WARNA_TENGAH
    assert {w[1], w[2]} == {tugas.WARNA_KIRI, tugas.WARNA_KANAN}


def test_warna_cermin_tanpa_flip_semua_tengah():
    assert tugas._warna_cermin(4, []) == [tugas.WARNA_TENGAH] * 4


def test_template_tanpa_nama_kelas_default_objek():
    tpl = tugas.template_dari_pose({"K": 2, "flip_idx": [1, 0]})
    assert tpl["kelas"] == "objek" and tpl["titik"] == ["1", "2"]


def test_template_k17_preset_coco():
    """K=17 -> preset COCO: nama baku + rangka (edge) + flip_idx cermin, lolos
    _sah_skeleton. data.yaml pose orang biasanya K=17, jadi langsung berangka."""
    tpl = tugas.template_dari_pose({"K": 17, "names": {0: "person"}})
    sk = tugas._sah_skeleton(tpl)
    assert sk["kelas"] == "person"
    assert sk["titik"][:3] == ["nose", "left_eye", "right_eye"]
    assert sk["titik"][-1] == "right_ankle" and len(sk["titik"]) == 17
    assert len(sk["edge"]) == 19                      # rangka COCO, lolos validasi
    assert sk["flip_idx"] == [0, 2, 1, 4, 3, 6, 5, 8, 7,
                              10, 9, 12, 11, 14, 13, 16, 15]


def test_template_k17_flip_berkas_diutamakan():
    """flip_idx dari data.yaml menang atas preset COCO (otoritatif)."""
    tpl = tugas.template_dari_pose({"K": 17, "flip_idx": list(range(17))})
    assert tugas._sah_skeleton(tpl)["flip_idx"] == list(range(17))  # identitas sah


def test_template_k33_tanpa_sinyal_court_tetap_generik():
    """K=33 TANPA flip court & TANPA nama keypoint -> slot number, tanpa rangka
    (bukan setiap K=33 itu court)."""
    tpl = tugas.template_dari_pose({"K": 33, "names": {0: "court"}})
    assert tpl["edge"] == [] and tpl["tata"] == [] and tpl["titik"][:2] == ["01", "02"]


# court names[1..33] (nomor Roboflow terurut) buat uji deteksi lewat nama.
_COURT_NAMA = ["1", "2", "4", "5", "7", "8", "9", "10", "11", "12", "13", "14",
               "15", "16", "17", "19", "21", "23", "25", "26", "27", "28", "29",
               "30", "31", "32", "33", "34", "35", "37", "38", "40", "41"]


def test_template_court33_dari_nama_keypoint_tanpa_flip():
    """Court dikenali juga dari nama keypoint di data.yaml (names[1..33]) walau
    flip tak disertakan; flip lalu diisi dari preset."""
    names = {0: "court", **{i + 1: n for i, n in enumerate(_COURT_NAMA)}}
    tpl = tugas.template_dari_pose({"K": 33, "names": names})
    assert len(tpl["edge"]) == 36 and len(tpl["tata"]) == 33
    assert tpl["titik"][:2] == ["1", "2"] and tpl["flip_idx"] == REAL_FLIP


def test_buat_skeleton_court_pakai_tata_preset_bukan_median(tmp_path):
    """Impor court: skeleton auto memakai tata PRESET kanonik, dan
    buat_skeleton_dari_pose TIDAK menimpanya dengan median perspektif."""
    import cv2
    import numpy as np

    d = tmp_path / "court-import"
    (d / "images").mkdir(parents=True)
    (d / "labels").mkdir(parents=True)
    cv2.imwrite(str(d / "images" / "f0.jpg"), np.full((100, 160, 3), 50, np.uint8))
    (d / "data.yaml").write_text(
        "train: images\nval: images\n"
        "kpt_shape: [33, 3]\n"
        f"flip_idx: {REAL_FLIP}\n"
        "nc: 34\nnames: ['court'," + ",".join(f"'{n}'" for n in _COURT_NAMA) + "]\n")
    (d / "labels" / "f0.txt").write_text(REAL_LINE + "\n")

    r = tugas.buat_skeleton_dari_pose(d, "paul")
    assert r["dibuat"] is True and r["K"] == 33
    sk = tugas.baca(d, "paul")["skeleton"]
    assert len(sk["edge"]) == 36 and sk["titik"][:2] == ["1", "2"]
    assert sk["tata"][0] == [0.03, 0.09]          # tata PRESET, bukan median


# ------------------------------------------------------------ tata median

def _rect(gid, x0, y0, x1, y1):
    return {"type": "rectangle", "group_id": gid, "label": "court",
            "pts": np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)}


def _pt(gid, nm, x, y, v):
    return {"type": "point", "group_id": gid, "label": nm, "flags": {"v": v},
            "pts": np.array([[x, y]], np.float32)}


def test_tata_median_per_slot():
    titik = ["a", "b"]
    it = {"shapes": [
        _rect(0, 0, 0, 100, 100), _pt(0, "a", 25, 25, 2), _pt(0, "b", 75, 75, 2),
        _rect(1, 0, 0, 200, 200), _pt(1, "a", 50, 50, 2), _pt(1, "b", 150, 150, 2),
    ]}
    tata = tugas._tata_dari_pose([it], titik)
    assert len(tata) == 2
    # a: (25/100, 50/200)->0.25 di kedua instance; b: 0.75.
    assert abs(tata[0][0] - 0.25) < 1e-6 and abs(tata[0][1] - 0.25) < 1e-6
    assert abs(tata[1][0] - 0.75) < 1e-6 and abs(tata[1][1] - 0.75) < 1e-6
    # Bertahan lewat _sah_skeleton: K pasangan di [0,1].
    sk = tugas._sah_skeleton({"titik": titik, "tata": tata})
    assert len(sk["tata"]) == 2
    assert all(0.0 <= c <= 1.0 for p in sk["tata"] for c in p)


def test_tata_slot_tak_terlihat_fallback():
    titik = ["a", "b", "c"]
    it = {"shapes": [_rect(0, 0, 0, 100, 100),
                     _pt(0, "a", 50, 50, 2), _pt(0, "b", 10, 10, 1),
                     _pt(0, "c", 0, 0, 0)]}      # c absen -> fallback [0.5,0.5]
    tata = tugas._tata_dari_pose([it], titik)
    assert len(tata) == 3 and tata[2] == [0.5, 0.5]


# ------------------------------------------------------------ read_yolo pose

def _tulis_baris(tmp_path, baris):
    tp = tmp_path / "x.txt"
    tp.write_text(baris + "\n")
    return tp


def test_read_yolo_pose_baris_asli(tmp_path):
    tp = _tulis_baris(tmp_path, REAL_LINE)
    titik = scanner.nama_slot_keypoint(33)
    W, H = 1000, 1000
    sh = scanner.read_yolo(tp, W, H, {0: "court"}, kpt={"K": 33, "titik": titik})

    rect = [s for s in sh if s["type"] == "rectangle"]
    poin = [s for s in sh if s["type"] == "point"]
    assert len(rect) == 1 and len(poin) == 33          # 1 rect + 33 titik
    assert not any(s["type"] == "polygon" for s in sh)  # BUKAN poligon 51 titik
    # Rectangle otoritatif: bukan kp_auto.
    assert not rect[0].get("titipan", {}).get("kp_auto")
    gid = rect[0]["group_id"]
    assert gid is not None and all(s["group_id"] == gid for s in poin)

    cols = REAL_LINE.split()
    vs = [int(float(cols[7 + 3 * i])) for i in range(33)]  # kolom visibilitas
    for i, s in enumerate(poin):
        assert s["label"] == titik[i]
        assert "v" in s["flags"] and s["flags"]["v"] == vs[i]
    # Slot v0 TETAP ada (tak dihapus) -> jumlah titik tetap 33.
    assert sum(1 for s in poin if s["flags"]["v"] == 0) == vs.count(0)


def test_read_yolo_round_trip(tmp_path):
    """Baris gaya-HIGOLAB (v0 = 0 0 0) -> impor -> ekspor == sumber (<1e-6)."""
    titik = ["a", "b", "c"]
    W, H = 100, 200
    sumber = ("0 0.400000 0.300000 0.200000 0.200000 "
              "0.350000 0.250000 2 0.450000 0.350000 1 "
              "0.000000 0.000000 0")
    tp = _tulis_baris(tmp_path, sumber)
    sh = scanner.read_yolo(tp, W, H, {0: "x"}, kpt={"K": 3, "titik": titik})
    it = {"W": W, "H": H, "shapes": sh}
    keluar = export.baris_yolo_pose(it, {"titik": titik, "kelas": "x"})
    assert len(keluar) == 1
    a, b = sumber.split(), keluar[0].split()
    assert len(a) == len(b) and a[0] == b[0] == "0"
    assert all(abs(float(x) - float(y)) < 1e-6 for x, y in zip(a[1:], b[1:]))


# ------------------------------------------------------------ anti-regresi

def test_poligon_tetap_poligon_kpt_none(tmp_path):
    tp = _tulis_baris(tmp_path, "0 0.1 0.1 0.5 0.1 0.5 0.5 0.1 0.5")   # 4 titik
    sh = scanner.read_yolo(tp, 100, 100, {0: "botol"})
    assert len(sh) == 1 and sh[0]["type"] == "polygon"
    assert "group_id" not in sh[0]                      # tak dikelompokkan


def test_bbox_tetap_rectangle_kpt_none(tmp_path):
    tp = _tulis_baris(tmp_path, "0 0.5 0.5 0.3 0.3")
    sh = scanner.read_yolo(tp, 100, 100, {0: "botol"})
    assert len(sh) == 1 and sh[0]["type"] == "rectangle"


def test_poligon_51_titik_tanpa_K_tak_dikelompokkan(tmp_path):
    # 51 titik = 102 koordinat + cls = 103 kolom; sah sebagai poligon.
    koords = " ".join(f"{(i % 10) / 10 + 0.01:.3f}" for i in range(102))
    tp = _tulis_baris(tmp_path, "0 " + koords)
    sh = scanner.read_yolo(tp, 100, 100, {0: "x"}, kpt=None)
    assert len(sh) == 1 and sh[0]["type"] == "polygon"
    # Bahkan dengan K=33 diketahui: 103 != 5+3*33=104 -> tetap poligon.
    sh2 = scanner.read_yolo(tp, 100, 100, {0: "x"},
                            kpt={"K": 33, "titik": scanner.nama_slot_keypoint(33)})
    assert len(sh2) == 1 and sh2[0]["type"] == "polygon"


# ------------------------------------------------------------ e2e unggah+bongkar

def _jpg_bytes(warna=120):
    im = np.full((40, 60, 3), warna, np.uint8)
    ok, buf = cv2.imencode(".jpg", im)
    assert ok
    return buf.tobytes()


def _zip_pose_kecil(K=4, flip=(1, 0, 3, 2)):
    """ZIP YOLO-pose kecil & sintetis (bukan berkas 226 MB asli)."""
    baris = (f"0 0.5 0.5 0.4 0.4 "
             f"0.400000 0.400000 2 0.600000 0.400000 2 "
             f"0.400000 0.600000 1 0.000000 0.000000 0")   # K=4, v=[2,2,1,0]
    yaml_pose = (
        "train: ../train/images\n"
        "val: ../valid/images\n"
        "test: ../test/images\n\n"
        f"kpt_shape: [{K}, 3]\n"
        f"flip_idx: {list(flip)}\n\n"
        "nc: 1\n"
        "names: ['court']\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("data.yaml", yaml_pose)
        for split in ("train", "valid", "test"):
            z.writestr(f"{split}/images/{split}-0.jpg", _jpg_bytes())
            z.writestr(f"{split}/labels/{split}-0.txt", baris + "\n")
    return buf.getvalue()


def test_e2e_unzip_pose_auto_template(klien, lingkungan):
    from tests.test_data import masuk, PW_PAUL
    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = lingkungan["ruang"]
    d = _projek(ruang, "court-pose", n=0, label=False)   # projek kosong

    klien.put("/upload?ds=court-pose&name=court.zip", content=_zip_pose_kecil())
    r = klien.post("/unzip?ds=court-pose&name=court.zip").json()
    assert r["ok"], r

    # Template keypoint auto-dibuat dari data.yaml pose.
    data = tugas.baca(d, "paul")
    assert tugas.skeleton_aktif(data)
    sk = data["skeleton"]
    assert len(sk["titik"]) == 4 and sk["titik"] == ["1", "2", "3", "4"]
    assert sk["kelas"] == "court"
    assert sk["flip_idx"] == [1, 0, 3, 2]               # involusi dari berkas
    assert len(sk["tata"]) == 4                          # tata diturunkan label

    # Membuka dataset: tiap instance = 1 rectangle + 4 point ber-group_id sama.
    klien.post("/useupload?ds=court-pose")
    items, _ = scanner.scan(d)
    berpose = [it for it in items if it["shapes"]]
    assert berpose, "tak ada gambar berpose terbaca"
    it = berpose[0]
    rect = [s for s in it["shapes"] if s["type"] == "rectangle"]
    poin = [s for s in it["shapes"] if s["type"] == "point"]
    assert len(rect) == 1 and len(poin) == 4
    assert not any(s["type"] == "polygon" for s in it["shapes"])
    assert {s["label"] for s in poin} == {"1", "2", "3", "4"}
    assert all(s["group_id"] == rect[0]["group_id"] for s in poin)


def test_e2e_unzip_pose_tak_menimpa_skeleton_manual(klien, lingkungan):
    """Impor ulang tak pernah menimpa template manual owner (idempoten)."""
    from tests.test_data import masuk, PW_PAUL
    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = lingkungan["ruang"]
    d = _projek(ruang, "court-manual", n=0, label=False)
    # Owner sudah menyetel template manual lebih dulu.
    tugas.set_skeleton(d, {"kelas": "lapangan", "titik": ["x", "y"]}, pemilik="paul")

    klien.put("/upload?ds=court-manual&name=c.zip", content=_zip_pose_kecil())
    assert klien.post("/unzip?ds=court-manual&name=c.zip").json()["ok"]

    sk = tugas.baca(d, "paul")["skeleton"]
    assert sk["titik"] == ["x", "y"] and sk["kelas"] == "lapangan"  # utuh
