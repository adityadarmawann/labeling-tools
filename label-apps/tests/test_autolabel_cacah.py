"""Pencacah hasil auto-label: "dilewati" dipecah jadi sebabnya.

Satu angka gabungan tidak memberi petunjuk apa pun. Gambar yang modelnya
memang tak menemukan apa-apa berarti ganti prompt atau model; gambar yang tak
boleh ditulis berarti minta akses. Dua tindakan yang sama sekali berbeda, dan
dulu dilaporkan dengan kalimat yang sama persis -- terbukti saat audit, ketika
sebuah jalan uji melaporkan "8 dilewati" tanpa petunjuk sedikit pun bahwa
sebabnya izin.

`dilewati` dipertahankan sebagai JUMLAH keduanya supaya pembaca lama (dan
antarmuka lama) tetap benar.
"""
from __future__ import annotations

import pathlib

from conftest import buat_dataset
from app.services import autolabel_gabung as ag
from app.services import autolabel_zero, prelabel, tugas
from tests.test_autolabel_gabung import _box_shape, _siapkan as _siapkan_gabung
from tests.test_autolabel_zero import _siapkan as _siapkan_zero
from tests.test_autolabel_zero import _temuan_kosong, _temuan_kotak


def _kunci(h: dict) -> None:
    assert h["dilewati"] == h["kosong"] + h["ditolak"], h


# ------------------------------------------------------------- zero-shot
def test_zero_tanpa_deteksi_masuk_kosong(tmp_path, monkeypatch):
    d, tid = _siapkan_zero(tmp_path, monkeypatch, 3, 0, _temuan_kosong)
    h = autolabel_zero.jalankan(d, tmp_path, tid, "darma", 1000, 0.1,
                                "yoloworld:latest", ["botol"])
    assert h["kosong"] == 3 and h["ditolak"] == 0
    _kunci(h)


def test_zero_tanpa_izin_masuk_ditolak(tmp_path, monkeypatch):
    d, tid = _siapkan_zero(tmp_path, monkeypatch, 3, 0, _temuan_kotak)
    monkeypatch.setattr(tugas, "tolak_tulis", lambda ds, akun, img: "tak berhak")
    h = autolabel_zero.jalankan(d, tmp_path, tid, "darma", 1000, 0.1,
                                "yoloworld:latest", ["botol"])
    assert h["ditolak"] == 3 and h["kosong"] == 0 and h["diproses"] == 0
    _kunci(h)
    # Tak ada anotasi yang ditulis: izin benar-benar dihormati.
    assert not [p for p in d.glob("*.json") if not p.name.startswith(".")]


# ------------------------------------------------------------- gabungan
def test_gabung_tanpa_deteksi_masuk_kosong(tmp_path, monkeypatch):
    pred = lambda m, it, conf, kelas, skor=None: []
    d, tid = _siapkan_gabung(tmp_path, monkeypatch, 2, prediksi_satu=pred)
    h = ag.jalankan(d, tmp_path, "darma", 1000,
                    [{"jenis": "terlatih", "nomor": 1}], tid=tid)
    assert h["kosong"] == 2 and h["ditolak"] == 0
    _kunci(h)


def test_gabung_tanpa_izin_masuk_ditolak(tmp_path, monkeypatch):
    pred = lambda m, it, conf, kelas, skor=None: [_box_shape(1, 1, 9, 9, "kaleng")]
    d, tid = _siapkan_gabung(tmp_path, monkeypatch, 2, prediksi_satu=pred)
    monkeypatch.setattr(tugas, "tolak_tulis", lambda ds, akun, img: "tak berhak")
    h = ag.jalankan(d, tmp_path, "darma", 1000,
                    [{"jenis": "terlatih", "nomor": 1}], tid=tid)
    assert h["ditolak"] == 2 and h["kosong"] == 0
    _kunci(h)


# ------------------------------------------------------------- pre-label
def test_prelabel_memisahkan_sebab(tmp_path, monkeypatch):
    from tests.test_prelabel_pose import _model_pose_kosong, _siapkan as _sp
    d, tid = _sp(tmp_path, monkeypatch, 2, _model_pose_kosong)
    h = prelabel.jalankan(d, tmp_path, tid, 1, "darma", 1000, 0.25)
    assert h["kosong"] == 2 and h["ditolak"] == 0
    _kunci(h)

    monkeypatch.setattr(prelabel.tugas, "tolak_tulis",
                        lambda ds, akun, img: "tak berhak")
    h2 = prelabel.jalankan(d, tmp_path, tid, 1, "darma", 1000, 0.25)
    assert h2["ditolak"] == 2 and h2["kosong"] == 0
    _kunci(h2)


# ------------------------------------------------------------- antarmuka
def test_ui_menjelaskan_sebab_dilewati():
    js = (pathlib.Path(__file__).resolve().parents[1]
          / "app" / "static" / "anotasi.js").read_text()
    assert "function rincianLewat" in js
    assert "tanpa objek ditemukan" in js and "tanpa izin tulis" in js
    # Dipakai di KEDUA ringkasan (pre-label dan auto-label), bukan satu saja.
    # Polanya pemanggilan ("+ rincianLewat(m)"), bukan namanya saja: nama itu
    # juga muncul di baris definisinya.
    assert js.count("+ rincianLewat(m)") == 2
