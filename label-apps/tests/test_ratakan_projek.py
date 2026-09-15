"""
Uji perkakas pemeliharaan tools/ratakan_projek.py.

Meratakan projek yang terlanjur bersplit HARUS memindah berkas DAN menulis ulang
setiap referensi path di .tugas.json dan .tag.json — kalau tidak, dataset dan
tugasnya jadi yatim. Di sinilah itu dijaga.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np

# tools/ bukan paket; muat berkasnya langsung.
_spec = importlib.util.spec_from_file_location(
    "ratakan_projek",
    Path(__file__).resolve().parent.parent / "tools" / "ratakan_projek.py")
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)


def _projek_bersplit(P: Path, n=(4, 1, 1)) -> Path:
    """Projek YOLO bersplit + .tugas.json/.tag.json yang menunjuk path split."""
    P.mkdir(parents=True, exist_ok=True)
    dataset, tugas_gambar, tag = [], [], {}
    for s, k in zip(("train", "valid", "test"), n):
        (P / s / "images").mkdir(parents=True, exist_ok=True)
        (P / s / "labels").mkdir(parents=True, exist_ok=True)
        for i in range(k):
            nama = f"{s}{i}.jpg"
            cv2.imwrite(str(P / s / "images" / nama),
                        np.full((32, 32, 3), (40 + i * 20) % 255, np.uint8))
            (P / s / "labels" / f"{s}{i}.txt").write_text("0 0.5 0.5 0.3 0.3\n")
            rel = f"{s}/images/{nama}"
            dataset.append(rel)
            tugas_gambar.append(rel)
            tag[rel] = {"tag": [], "batch": "uji"}
    (P / "data.yaml").write_text("names: [botol, kaleng]\n")
    (P / ".tugas.json").write_text(json.dumps({
        "versi": 1, "pemilik": "darma", "anggota": {},
        "tugas": {"t1": {"gambar": tugas_gambar}},
        "dataset": dataset, "undangan": {}, "kurasi": True, "jenis_anotasi": ""}))
    (P / ".tag.json").write_text(json.dumps({"versi": 1, "gambar": tag}))
    return P


def test_meratakan_memindah_berkas_dan_menulis_ulang_semua_referensi(tmp_path):
    from app.services import scanner

    P = _projek_bersplit(tmp_path / "proj")
    hasil = rp.migrasi(P, apply=True)
    assert hasil["status"] == "ok", hasil
    assert hasil["berkas_dipindah"] == 12          # 6 gambar + 6 label
    assert hasil["ref_tugas_ditulis_ulang"] == 12  # 6 dataset + 6 tugas
    assert hasil["ref_tag_ditulis_ulang"] == 6

    # struktur: datar, split lenyap
    assert not any((P / s).exists() for s in ("train", "valid", "test"))
    assert {p.name for p in (P / "images").glob("*.jpg")} == \
        {"train0.jpg", "train1.jpg", "train2.jpg", "train3.jpg",
         "valid0.jpg", "test0.jpg"}

    # scanner: sudah dianotasi, tanpa penanda split, kelas utuh
    its, names = scanner.scan(P)
    assert len(its) == 6 and all(it.get("ann") for it in its)
    assert all(not it.get("split") for it in its)
    assert [names[i] for i in sorted(names)] == ["botol", "kaleng"]

    # referensi ditulis ulang ke bentuk datar DAN menunjuk berkas yang ada
    tug = json.loads((P / ".tugas.json").read_text())
    disk = {f"images/{p.name}" for p in (P / "images").glob("*.jpg")}
    assert set(tug["dataset"]) == disk
    assert set(tug["tugas"]["t1"]["gambar"]) == disk
    tag = json.loads((P / ".tag.json").read_text())
    assert all(k.startswith("images/") for k in tag["gambar"])

    # cadangan asli tersimpan
    cad = P / ".pra-ratakan"
    assert (cad / ".tugas.json").is_file() and (cad / "split-asal.json").is_file()
    manifes = json.loads((cad / "split-asal.json").read_text())
    assert manifes["train0.jpg"] == "train" and manifes["test0.jpg"] == "test"


def test_aman_diulang(tmp_path):
    P = _projek_bersplit(tmp_path / "proj")
    rp.migrasi(P, apply=True)
    lagi = rp.migrasi(P, apply=True)
    assert lagi["status"] == "sudah-datar" and lagi["berkas_dipindah"] == 0


def test_uji_kering_tak_menulis(tmp_path):
    P = _projek_bersplit(tmp_path / "proj")
    hasil = rp.migrasi(P, apply=False)
    assert hasil["status"] == "uji-kering"
    assert (P / "train").exists() and not (P / "images").exists()
    # .tugas.json masih menunjuk path split
    tug = json.loads((P / ".tugas.json").read_text())
    assert all("/images/" in x and x.split("/")[0] in ("train", "valid", "test")
               for x in tug["dataset"])


def test_bentrok_nama_antar_split_ditolak(tmp_path):
    P = _projek_bersplit(tmp_path / "proj")
    # bikin nama yang sama di train DAN valid dengan isi berbeda
    cv2.imwrite(str(P / "train" / "images" / "dup.jpg"),
                np.zeros((32, 32, 3), np.uint8))
    (P / "train" / "labels" / "dup.txt").write_text("0 0.5 0.5 0.2 0.2\n")
    cv2.imwrite(str(P / "valid" / "images" / "dup.jpg"),
                np.full((32, 32, 3), 200, np.uint8))
    (P / "valid" / "labels" / "dup.txt").write_text("1 0.5 0.5 0.2 0.2\n")
    hasil = rp.migrasi(P, apply=True)
    assert hasil["status"] == "BENTROK" and "dup.jpg" in hasil["contoh"]
    # tak ada yang dipindah — split masih utuh
    assert (P / "train" / "images" / "dup.jpg").exists()
    assert not (P / "images").exists()


def test_bukan_projek_ditolak(tmp_path):
    d = tmp_path / "cuma-folder"
    (d / "train" / "images").mkdir(parents=True)
    (d / "train" / "labels").mkdir(parents=True)
    assert rp.migrasi(d, apply=True)["status"] == "BUKAN-PROJEK"
