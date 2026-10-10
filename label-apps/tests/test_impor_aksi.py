"""
Uji IMPORTER dataset klip aksi jadi (impor_aksi).

Yang dijaga: dataset folder-per-kelas (train/val) yang sudah jadi di LUAR HIGOLAB
menjadi satu VERSI `.versi/vN/` yang (a) berbentuk sama dengan keluaran build
(aksi.yaml + MANIFES jenis 'aksi' + train/valid folder-per-kelas), (b) TERDAFTAR
lewat versi.buat sehingga muncul di pemilih versi aksi UI, (c) menolak masukan
yang melanggar invarian valid-bersih, dan (d) menyetel kelas aksi projek.

Tak butuh ffmpeg/torch: importer hanya menyalin berkas + menulis metadata.
"""
from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import pytest

from app.services import impor_aksi, projek, tugas, versi
from tests.conftest import PW_PAUL, masuk
from tests.test_video_unggah import _buat_projek


def _dataset(root: Path, train: dict[str, int], val: dict[str, int]) -> tuple[Path, Path]:
    """Dataset jadi tiruan: root/train/<k>/*.mp4 + root/val/<k>/*.mp4 (mp4 tiruan)."""
    td, vd = root / "train", root / "val"
    for base, counts in ((td, train), (vd, val)):
        for k, n in counts.items():
            (base / k).mkdir(parents=True, exist_ok=True)
            for i in range(n):
                (base / k / f"{k}_abcDEF01234_{i:04d}.mp4").write_bytes(b"x")
    return td, vd


def _projek_video(root: Path, nama: str = "vid") -> Path:
    return Path(projek.buat(root, nama, "video")["path"])


# ──────────────────────────────── jalur senang ────────────────────────────
def test_impor_bangun_versi_siap_latih(tmp_path):
    td, vd = _dataset(tmp_path / "ds", {"shoot": 3, "pass": 3}, {"shoot": 2, "pass": 2})
    proj = _projek_video(tmp_path / "ws")

    r = impor_aksi.impor(proj, td, vd, oleh="paul")

    assert r["nomor"] == 1 and r["kelas"] == 2
    assert r["jumlah"] == {"train": 6, "valid": 4}
    assert r["jenis"] == "aksi"

    vdir = proj / ".versi" / "v1"
    # aksi.yaml: kelas + layout folder-per-kelas yang dibaca ketiga backend
    yaml = (vdir / "aksi.yaml").read_text()
    assert "names: ['pass', 'shoot']" in yaml or "names: ['shoot', 'pass']" in yaml
    assert "train: train" in yaml and "val: valid" in yaml
    # klip tersalin ke train/ + valid/ folder-per-kelas
    assert len(list((vdir / "train" / "shoot").glob("*.mp4"))) == 3
    assert len(list((vdir / "valid" / "pass").glob("*.mp4"))) == 2
    # MANIFES jenis aksi
    man = json.loads((vdir / "MANIFES.json").read_text())
    assert man["jenis"] == "aksi" and len(man["berkas"]) == 10
    # kelas aksi projek ter-set
    data = tugas.baca(proj, "paul")
    assert sorted(data["aksi"]["kelas"]) == ["pass", "shoot"]


def test_impor_muncul_di_daftar_versi_aksi(tmp_path):
    """Versi hasil impor harus lolos saringan _versi_aksi (hasil.jenis=='aksi')."""
    td, vd = _dataset(tmp_path / "ds", {"a": 2, "b": 2}, {"a": 1, "b": 1})
    proj = _projek_video(tmp_path / "ws")
    impor_aksi.impor(proj, td, vd, oleh="paul")

    aksi = [v for v in versi.daftar(proj)
            if (v.get("hasil") or {}).get("jenis") == "aksi"]
    assert len(aksi) == 1
    assert aksi[0]["nomor"] == 1 and aksi[0]["kelas"] == 2


# ──────────────────────────────── invarian & penolakan ────────────────────
def test_impor_tolak_valid_kotor(tmp_path):
    """Klip _aug_/_bal_ di valid = angka validasi menipu -> ditolak keras."""
    td, vd = _dataset(tmp_path / "ds", {"shoot": 2, "pass": 2}, {"shoot": 1, "pass": 1})
    (vd / "shoot" / "shoot_abcDEF01234_aug_0001.mp4").write_bytes(b"x")
    proj = _projek_video(tmp_path / "ws")
    with pytest.raises(impor_aksi.ImporTolak, match="augmentasi di valid"):
        impor_aksi.impor(proj, td, vd, oleh="paul")
    # gagal bersih: tak meninggalkan versi separuh
    assert not (proj / ".versi" / "v1").exists()


def test_impor_auto_kelas_buang_train_saja(tmp_path):
    """Kelas yang hanya ada di train (mis. 'stand') otomatis dilewati —
    tak ada val = tak bisa diukur (seperti saran README action-v14)."""
    td, vd = _dataset(tmp_path / "ds",
                      {"shoot": 2, "pass": 2, "stand": 2},
                      {"shoot": 1, "pass": 1})
    proj = _projek_video(tmp_path / "ws")
    r = impor_aksi.impor(proj, td, vd, oleh="paul")
    assert r["kelas"] == 2                        # stand dibuang
    assert not (proj / ".versi" / "v1" / "train" / "stand").exists()


def test_impor_tolak_projek_bukan_video(tmp_path):
    td, vd = _dataset(tmp_path / "ds", {"a": 2, "b": 2}, {"a": 1, "b": 1})
    img = Path(projek.buat(tmp_path / "ws", "gbr", "image")["path"])
    with pytest.raises(impor_aksi.ImporTolak, match="bukan projek video"):
        impor_aksi.impor(img, td, vd, oleh="paul")


def test_impor_kelas_eksplisit_dan_negatif(tmp_path):
    td, vd = _dataset(tmp_path / "ds",
                      {"shoot": 2, "pass": 2, "diam": 2},
                      {"shoot": 1, "pass": 1, "diam": 1})
    proj = _projek_video(tmp_path / "ws")
    r = impor_aksi.impor(proj, td, vd, oleh="paul",
                         kelas=["shoot", "pass", "diam"], negatif="diam")
    assert r["kelas"] == 3 and r["negatif"] == "diam"
    data = tugas.baca(proj, "paul")
    assert data["aksi"]["negatif"] == "diam"
    # negatif yang bukan salah satu kelas ditolak
    proj2 = _projek_video(tmp_path / "ws2")
    with pytest.raises(impor_aksi.ImporTolak, match="negatif"):
        impor_aksi.impor(proj2, td, vd, oleh="paul", negatif="tak_ada")


def test_rencana_laporkan_masalah_tanpa_menulis(tmp_path):
    td, vd = _dataset(tmp_path / "ds", {"a": 2, "stand": 1}, {"a": 1})
    rc = impor_aksi.rencana(td, vd)
    assert rc["kelas"] == ["a"]                   # hanya a di dua split
    assert "stand" in rc["train_saja"]
    assert rc["jumlah"] == {"train": 2, "valid": 1}


# ──────────────────────────────── temukan_dataset ─────────────────────────
def test_temukan_dataset_bersarang_dan_alias_val(tmp_path):
    # Zip membungkus satu folder atas; valid bernama 'valid'
    base = tmp_path / "nest" / "v1"
    for s in ("train", "valid"):
        (base / s / "a").mkdir(parents=True)
        (base / s / "a" / "a.mp4").write_bytes(b"x")
    assert impor_aksi.temukan_dataset(tmp_path / "nest") == (base / "train", base / "valid")
    # Datar di root, split validasi bernama 'val'
    flat = tmp_path / "flat"
    for s in ("train", "val"):
        (flat / s / "a").mkdir(parents=True)
        (flat / s / "a" / "a.mp4").write_bytes(b"x")
    assert impor_aksi.temukan_dataset(flat) == (flat / "train", flat / "val")


def test_temukan_dataset_tak_ketemu(tmp_path):
    (tmp_path / "train" / "a").mkdir(parents=True)   # train saja, tanpa val
    (tmp_path / "train" / "a" / "a.mp4").write_bytes(b"x")
    assert impor_aksi.temukan_dataset(tmp_path) is None


# ──────────────────────────────── rute .zip (HTTP) ────────────────────────
def _zip_dataset(train: dict[str, int], val: dict[str, int], atas: str = "ds") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for split, counts in (("train", train), ("valid", val)):
            for k, n in counts.items():
                for i in range(n):
                    z.writestr(f"{atas}/{split}/{k}/{k}_x{i}.mp4", b"x")
    return buf.getvalue()


def _poll_impor(klien, ds, maks=200):
    k = {}
    for _ in range(maks):
        k = klien.get(f"/api/aksi/impor/kemajuan?ds={ds}").json()
        if k.get("selesai") or k.get("galat"):
            break
        time.sleep(0.05)
    return k


def test_rute_impor_zip_bangun_versi(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-imp", "video")
    data = _zip_dataset({"shoot": 2, "pass": 2}, {"shoot": 1, "pass": 1})

    r = klien.post("/api/aksi/impor?ds=aksi-imp", content=data).json()
    assert r.get("ok") and r.get("mulai"), r
    k = _poll_impor(klien, "aksi-imp")
    assert k.get("selesai") and not k.get("galat"), k
    assert k.get("nomor") == 1

    proj = lingkungan["ruang"] / "aksi-imp"
    vd = proj / ".versi" / "v1"
    assert (vd / "aksi.yaml").is_file()
    assert len(list((vd / "train" / "shoot").glob("*.mp4"))) == 2
    assert len(list((vd / "valid" / "pass").glob("*.mp4"))) == 1
    assert not (proj / "_impor_tmp").exists()        # staging + zip dibersihkan
    aksi = [v for v in versi.daftar(proj)
            if (v.get("hasil") or {}).get("jenis") == "aksi"]
    assert len(aksi) == 1 and aksi[0]["kelas"] == 2


def test_rute_impor_tolak_projek_image(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "img-imp", "image")
    data = _zip_dataset({"a": 2, "b": 2}, {"a": 1, "b": 1})
    r = klien.post("/api/aksi/impor?ds=img-imp", content=data).json()
    assert r.get("ok") is False, r


# ──────────────────────── rute impor dari PATH server (admin) ──────────────
def _set_admin(lingkungan, nama, nilai):
    p = lingkungan["users"]
    d = json.loads(p.read_text())
    d[nama]["admin"] = nilai
    p.write_text(json.dumps(d))


def test_rute_impor_path_admin_folder(klien, lingkungan, tmp_path):
    _set_admin(lingkungan, "paul", True)
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-path", "video")
    _dataset(tmp_path / "srv", {"shoot": 2, "pass": 2}, {"shoot": 1, "pass": 1})

    r = klien.post("/api/aksi/impor-path?ds=aksi-path",
                   json={"path": str(tmp_path / "srv")}).json()
    assert r.get("ok") and r.get("mulai"), r
    k = _poll_impor(klien, "aksi-path")
    assert k.get("selesai") and not k.get("galat"), k
    vd = lingkungan["ruang"] / "aksi-path" / ".versi" / "v1"
    assert (vd / "aksi.yaml").is_file()
    assert len(list((vd / "train" / "shoot").glob("*.mp4"))) == 2


def test_rute_impor_path_tolak_non_admin(klien, lingkungan, tmp_path):
    _set_admin(lingkungan, "paul", False)
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-path2", "video")
    _dataset(tmp_path / "srv2", {"a": 2, "b": 2}, {"a": 1, "b": 1})
    r = klien.post("/api/aksi/impor-path?ds=aksi-path2",
                   json={"path": str(tmp_path / "srv2")}).json()
    assert r.get("ok") is False and "admin" in r.get("error", ""), r
    assert not (lingkungan["ruang"] / "aksi-path2" / ".versi" / "v1").exists()


def test_rute_impor_path_dari_zip_server(klien, lingkungan, tmp_path):
    _set_admin(lingkungan, "paul", True)
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-path3", "video")
    zp = tmp_path / "ds.zip"
    zp.write_bytes(_zip_dataset({"x": 2, "y": 2}, {"x": 1, "y": 1}))
    r = klien.post("/api/aksi/impor-path?ds=aksi-path3",
                   json={"path": str(zp)}).json()
    assert r.get("ok"), r
    k = _poll_impor(klien, "aksi-path3")
    assert k.get("selesai") and not k.get("galat"), k
    assert k.get("nomor") == 1


def test_rute_impor_path_tunjuk_folder_train(klien, lingkungan, tmp_path):
    # Menunjuk langsung ke folder 'train' -> pakai induknya (toleran).
    _set_admin(lingkungan, "paul", True)
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-path4", "video")
    _dataset(tmp_path / "srv4", {"a": 2, "b": 2}, {"a": 1, "b": 1})
    r = klien.post("/api/aksi/impor-path?ds=aksi-path4",
                   json={"path": str(tmp_path / "srv4" / "train")}).json()
    assert r.get("ok"), r
    k = _poll_impor(klien, "aksi-path4")
    assert k.get("selesai") and not k.get("galat"), k


def test_rute_impor_path_tak_ada(klien, lingkungan):
    _set_admin(lingkungan, "paul", True)
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-path5", "video")
    r = klien.post("/api/aksi/impor-path?ds=aksi-path5",
                   json={"path": "/tidak/ada/di/mana/pun"}).json()
    assert r.get("ok") is False and "tak ada" in r.get("error", ""), r
