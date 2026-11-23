"""Klik dan prompt teks harus benar di atas osam 0.12, bukan hanya 0.5.

Bentuk promptnya berbeda, dan bedanya tidak akan terlihat sebagai galat
sintaks: di 0.5 sebuah kotak dikirim sebagai dua titik berlabel 2 dan 3,
sedangkan 0.12 punya medan `box` sendiri dan hanya menerima label 0 atau 1.
Mengirim label 2 ke 0.12 ditolak mentah-mentah, dan mengirim kotak sebagai
titik berlabel 1 LEBIH BURUK daripada ditolak: ia tetap memberi mask, hanya
mask yang salah, yaitu dua klik acak di sudut objek.

Karena itu yang diuji di sini bukan "tidak melempar galat" melainkan BENTUK
panggilan yang sampai ke osam.

osam-nya tiruan, supaya uji ini berlaku di venv yang masih 0.5 maupun yang
sudah 0.12, dan supaya suite tak mengunduh bobot 1,8 GB.
"""
from __future__ import annotations

import sys
import types as pytypes

import numpy as np
import pytest

from app.services import autolabel, osam_api


class KlienTiruan:
    def __init__(self, device=None, **_):
        self.device = device
        self.panggilan: list[tuple] = []

    def segment(self, model, rgb, **prompt):
        self.panggilan.append((model, prompt))
        h, w = 24, 30
        return [pytypes.SimpleNamespace(
            bounding_box=pytypes.SimpleNamespace(xmin=10, ymin=20,
                                                 xmax=10 + w - 1,
                                                 ymax=20 + h - 1),
            mask=np.ones((h, w), dtype=bool), score=0.9, text="botol")]

    def list(self):
        return [pytypes.SimpleNamespace(name="sam2:small", size=1,
                                        prompt_kinds=["points", "box"]),
                pytypes.SimpleNamespace(name="sam3:latest", size=None,
                                        prompt_kinds=["points", "box", "texts"])]

    def close(self):
        pass


@pytest.fixture
def osam12(monkeypatch, tmp_path):
    """osam tiruan ber-API 0.12, plus satu gambar sungguhan di disk."""
    import cv2
    klien = KlienTiruan(device="cpu")
    palsu = pytypes.ModuleType("osam")
    palsu.Client = lambda *a, **k: klien
    palsu.__version__ = "0.12.0"
    monkeypatch.setitem(sys.modules, "osam", palsu)
    monkeypatch.setattr(osam_api, "_klien", {}, raising=False)
    monkeypatch.setattr(osam_api, "_jenis_prompt", {}, raising=False)
    monkeypatch.setattr(autolabel, "_panas", type(autolabel._panas)(),
                        raising=False)
    monkeypatch.setattr(autolabel, "_boleh_gpu_osam", lambda model: False)
    img = tmp_path / "gambar.jpg"
    cv2.imwrite(str(img), np.full((80, 90, 3), 200, np.uint8))
    return pytypes.SimpleNamespace(klien=klien, img=img)


# ---------------------------------------------------- penerjemah prompt

def test_kotak_jadi_medan_box_bukan_titik():
    p = autolabel._prompt_osam_baru([[5.0, 6.0], [25.0, 36.0]], [2, 3])
    assert p == {"box": (5.0, 6.0, 25.0, 36.0)}
    assert "points" not in p, "label 2 dan 3 tak boleh lolos sebagai titik"


def test_titik_tetap_titik_dengan_labelnya():
    p = autolabel._prompt_osam_baru([[1.0, 2.0], [3.0, 4.0]], [1, 0])
    assert p == {"points": [(1.0, 2.0), (3.0, 4.0)], "point_labels": [1, 0]}


def test_titik_dan_kotak_sekaligus():
    """Klik penyempurna di dalam kotak: keduanya dikirim bersama."""
    p = autolabel._prompt_osam_baru([[5.0, 6.0], [25.0, 36.0], [10.0, 10.0]],
                                    [2, 3, 1])
    assert p["box"] == (5.0, 6.0, 25.0, 36.0)
    assert p["points"] == [(10.0, 10.0)] and p["point_labels"] == [1]


def test_kotak_sebelah_ditolak():
    with pytest.raises(autolabel.TidakAdaObjek, match="satu sudut"):
        autolabel._prompt_osam_baru([[5.0, 6.0]], [2])


def test_prompt_kosong_ditolak():
    with pytest.raises(autolabel.TidakAdaObjek, match="kosong"):
        autolabel._prompt_osam_baru([], [])


# ---------------------------------------------------- jalur sungguhan

def test_dari_titik_mengirim_titik(osam12):
    u = autolabel.dari_titik(osam12.img, [[12.0, 14.0]], [1], model="sam2:small")
    model, prompt = osam12.klien.panggilan[-1]
    assert model == "sam2:small"
    assert prompt == {"points": [(12.0, 14.0)], "point_labels": [1]}
    assert u.bbox == (10, 20, 39, 43)
    assert len(u.points) >= 3, "mask harus jadi poligon"


def test_dari_kotak_mengirim_box(osam12):
    autolabel.dari_kotak(osam12.img, 4, 5, 44, 55, model="sam2:small")
    _, prompt = osam12.klien.panggilan[-1]
    assert prompt == {"box": (4.0, 5.0, 44.0, 55.0)}


def test_dari_teks_mengirim_texts(osam12):
    hasil = autolabel.dari_teks(osam12.img, ["botol"], model="sam3:latest",
                                ambang=0.2, maks=7)
    model, prompt = osam12.klien.panggilan[-1]
    assert model == "sam3:latest"
    assert prompt == {"texts": ["botol"], "score_threshold": 0.2,
                      "max_annotations": 7}
    assert hasil[0].label == "botol"


def test_pemanasan_dicatat_dan_klik_berikutnya_tahu(osam12):
    assert autolabel.panaskan(osam12.img, "sam2:small") is True
    assert autolabel.panaskan(osam12.img, "sam2:small") is True
    assert len(osam12.klien.panggilan) == 1, \
        "gambar yang sudah di-encode tak boleh dipanggil ulang"
    u = autolabel.dari_titik(osam12.img, [[12.0, 14.0]], [1], model="sam2:small")
    assert u.dari_cache is True


def test_bebaskan_gpu_mengosongkan_catatan_panas(osam12):
    autolabel.panaskan(osam12.img, "sam2:small")
    autolabel.bebaskan_gpu()
    assert autolabel.panaskan(osam12.img, "sam2:small") is True
    assert len(osam12.klien.panggilan) == 2, \
        "sesudah VRAM dibebaskan, embeddingnya ikut lenyap dan harus dihitung lagi"


def test_status_unduhan_dari_daftar_model(osam12):
    assert autolabel._sudah_terunduh("sam2:small") is True
    assert autolabel._sudah_terunduh("sam3:latest") is False, \
        "size kosong berarti bobotnya belum ada"


def test_penambal_sesi_tak_dipasang_di_api_baru(osam12, monkeypatch):
    """0.12 memilih perangkat lewat argumen; modul yang dulu ditambal tak ada."""
    monkeypatch.setattr(autolabel, "_osam_dipaksa", False)
    autolabel._siapkan_osam()           # tak boleh melempar ImportError
    assert autolabel._osam_dipaksa is True
