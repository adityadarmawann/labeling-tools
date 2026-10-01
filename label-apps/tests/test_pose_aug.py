"""
Uji inti augmentasi pose (Langkah 5a) — di CPU, TANPA training.

Yang dijaga: keypoint ikut ter-transform; horizontal flip memakai flip_idx
(tukar identitas kiri-kanan + cermin x); titik yang keluar bingkai jadi absen;
dan penulisan label YOLO-pose dari instance ter-aug.
"""
from __future__ import annotations

import numpy as np

from app.services import olah


def _img(w=100, h=80):
    return np.zeros((h, w, 3), np.uint8)


def _inst(kelas, kp):
    return {"kelas": kelas, "kp": [tuple(t) for t in kp]}


def test_identitas_tanpa_pipeline_tanpa_flip():
    inst = _inst(0, [(0.2, 0.3, 2), (0.7, 0.4, 1), (0.0, 0.0, 0)])
    img2, out = olah.augmentasi_pose_sekali(_img(), [inst], None, flip_idx=None)
    assert img2.shape == (80, 100, 3)
    kp = out[0]["kp"]
    assert kp[0] == (0.2, 0.3, 2)
    assert kp[1] == (0.7, 0.4, 1)
    assert kp[2] == (0.0, 0.0, 0)            # absen tetap absen


def test_flip_horizontal_pakai_flip_idx():
    # flip_idx [1,0]: slot0<->slot1 (mis. pasangan kiri-kanan).
    inst = _inst(0, [(0.2, 0.3, 2), (0.7, 0.4, 1)])
    _, out = olah.augmentasi_pose_sekali(
        _img(), [inst], None, flip_idx=[1, 0], p_flip=1.0)
    kp = out[0]["kp"]
    # slot0 kini isi dari slot1 lama, x dicermin: (1-0.7, 0.4, 1)
    assert kp[0] == (0.3, 0.4000000000000001, 1) or abs(kp[0][0] - 0.3) < 1e-6
    assert abs(kp[0][0] - 0.3) < 1e-6 and kp[0][2] == 1
    # slot1 kini isi dari slot0 lama: (1-0.2, 0.3, 2)
    assert abs(kp[1][0] - 0.8) < 1e-6 and kp[1][2] == 2


def test_flip_identitas_tanpa_pasangan():
    # flip_idx identitas [0,1]: hanya mencermin x, slot tetap.
    inst = _inst(0, [(0.2, 0.3, 2), (0.7, 0.4, 2)])
    _, out = olah.augmentasi_pose_sekali(
        _img(), [inst], None, flip_idx=[0, 1], p_flip=1.0)
    kp = out[0]["kp"]
    assert abs(kp[0][0] - 0.8) < 1e-6         # 1-0.2
    assert abs(kp[1][0] - 0.3) < 1e-6         # 1-0.7


def test_instance_tanpa_titik_terlihat_dibuang():
    inst = _inst(0, [(0.0, 0.0, 0), (0.0, 0.0, 0)])
    hasil = olah.augmentasi_pose_sekali(_img(), [inst], None)
    assert hasil is None


def test_tulis_label_pose_format(tmp_path):
    inst = _inst(0, [(0.2, 0.3, 2), (0.0, 0.0, 0), (0.5, 0.6, 1)])
    p = tmp_path / "x.txt"
    n = olah.tulis_label_pose(p, [inst], K=3)
    assert n == 1
    parts = p.read_text().strip().split()
    assert parts[0] == "0"
    assert len(parts) == 5 + 3 * 3
    # bbox dari titik v>=1 (0.2,0.3)&(0.5,0.6): cx=0.35 cy=0.45
    assert abs(float(parts[1]) - 0.35) < 1e-6
    assert abs(float(parts[2]) - 0.45) < 1e-6
    assert parts[5:8] == ["0.200000", "0.300000", "2"]
    assert parts[8:11] == ["0.000000", "0.000000", "0"]   # absen di-pad
    assert parts[11:14] == ["0.500000", "0.600000", "1"]


def test_bangun_versi_pose(tmp_path):
    """Build versi projek pose: data.yaml kpt_shape/flip_idx + label pose +
    augmentasi di train (aug-balancer keypoint-aware)."""
    import cv2
    from app.services import buatversi

    d = tmp_path / "proj"
    d.mkdir()
    for i in range(2):
        cv2.imwrite(str(d / f"img{i}.jpg"),
                    (np.random.rand(60, 60, 3) * 255).astype(np.uint8))

    def _item(i):
        pt = lambda nm, x, y, v: {"label": nm, "type": "point", "group_id": 0,
                                  "flags": {"v": v},
                                  "pts": np.array([[x, y]], np.float32)}
        return {"img": d / f"img{i}.jpg", "W": 60, "H": 60, "shapes": [
            pt("a", 12, 12, 2), pt("b", 24, 36, 1),
            {"label": "botol", "type": "rectangle", "group_id": 0, "flags": {},
             "pts": np.array([[6, 6], [30, 42]], np.float32)}]}

    items = [_item(0), _item(1)]
    skel = {"kelas": "botol", "titik": ["a", "b"], "edge": [[0, 1]],
            "flip_idx": [0, 1], "tata": []}
    peta = {"img0": "train", "img1": "valid"}
    resep = {"volume": {"per_gambar": 3}}      # train: asli + 2 aug
    job = buatversi.Pekerjaan(d, 1, items, {}, resep, peta, kunci="t",
                              jenis="kerangka", skeleton=skel)
    r = job.jalankan()

    dirv = buatversi.dir_versi(d, 1)
    yaml = (dirv / "data.yaml").read_text()
    assert "kpt_shape: [2, 3]" in yaml
    assert "flip_idx: [0, 1]" in yaml
    assert "names: ['botol']" in yaml
    # train = img0 asli + augmentasinya (>1 berkas); valid = img1 asli (1).
    train = list((dirv / "train" / "labels").glob("*.txt"))
    valid = list((dirv / "valid" / "labels").glob("*.txt"))
    assert len(train) >= 2 and len(valid) == 1, (len(train), len(valid))
    p = sorted(train)[0].read_text().strip().split()
    assert p[0] == "0" and len(p) == 5 + 2 * 3   # cls + bbox + K*3
    assert r["jenis"] == "kerangka" and r["keypoint"] == 2
