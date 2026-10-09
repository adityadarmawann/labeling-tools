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

import json
from pathlib import Path

import pytest

from app.services import impor_aksi, projek, tugas, versi


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
