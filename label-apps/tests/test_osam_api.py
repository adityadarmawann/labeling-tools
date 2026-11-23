"""Satu pintu ke osam, supaya dua generasi API-nya tidak bocor ke mana-mana.

osam mengganti API publiknya di 0.12: `apis.generate` dengan request pydantic
berganti `Client(...).segment(...)`. Yang membuat perpindahan itu
layak dikerjakan adalah SAM 3: di 0.5 ia hanya menerima dua titik sudut
sebagai kotak dan menolak klik tunggal dengan

    only two point prompts (left-top, right-bottom) are supported

sedangkan di 0.12 ia punya decoder titik sendiri. Diukur pada 6 gambar
berlabel manusia di projek coca-cola, satu klik di tengah objek:

    sam2:small    IoU rata 0,84   terburuk 0,32   0,30 dtk
    sam3:latest   IoU rata 0,94   terburuk 0,84   1,29 dtk klik pertama,
                                                  0,01 dtk klik berikutnya

Yang menentukan bukan rata-ratanya melainkan kasus terburuknya: sam2:small
sempat jatuh ke 0,32 pada objek yang sulit, SAM 3 tidak pernah di bawah 0,84.

Uji di sini memakai osam TIRUAN, bukan yang terpasang, supaya kedua cabang
tetap teruji di venv mana pun dan supaya tak ada unduhan bobot 1,8 GB saat
suite berjalan.
"""
from __future__ import annotations

import sys
import types as pytypes

import pytest

from app.services import osam_api


class KlienTiruan:
    def __init__(self, device=None, max_loaded_models=None, in_process=False,
                 gagal=None):
        self.device = device
        self.max_loaded_models = max_loaded_models
        self.in_process = in_process
        self.panggilan: list[tuple] = []
        self.tertutup = False
        self._gagal = gagal

    def segment(self, model, rgb, **prompt):
        self.panggilan.append((model, prompt))
        if self._gagal is not None:
            raise self._gagal
        return [f"anotasi {model} di {self.device}"]

    def list(self):
        return [pytypes.SimpleNamespace(name="sam3:latest",
                                        prompt_kinds=["points", "box", "texts"]),
                pytypes.SimpleNamespace(name="yoloworld:latest",
                                        prompt_kinds=["texts"])]

    def close(self):
        self.tertutup = True


@pytest.fixture
def osam_baru(monkeypatch):
    """Pasang modul osam tiruan ber-API 0.12, dan kosongkan cache modul."""
    dibuat: list[KlienTiruan] = []
    gagal_pada: dict[str, BaseException] = {}

    def Client(host=None, *, device=None, max_loaded_models=None,
               in_process=False, **_):
        if device in gagal_pada and isinstance(gagal_pada[device], BaseException):
            raise gagal_pada[device]
        c = KlienTiruan(device=device, max_loaded_models=max_loaded_models,
                        in_process=in_process)
        dibuat.append(c)
        return c

    palsu = pytypes.ModuleType("osam")
    palsu.Client = Client
    palsu.__version__ = "0.12.0"
    monkeypatch.setitem(sys.modules, "osam", palsu)
    monkeypatch.setattr(osam_api, "_klien", {}, raising=False)
    monkeypatch.setattr(osam_api, "_jenis_prompt", {}, raising=False)
    return pytypes.SimpleNamespace(modul=palsu, dibuat=dibuat,
                                   gagal_pada=gagal_pada)


@pytest.fixture
def osam_lama(monkeypatch):
    """Modul osam tiruan ber-API 0.5: ada `apis`, tidak ada `Client`."""
    palsu = pytypes.ModuleType("osam")
    palsu.apis = pytypes.ModuleType("osam.apis")
    palsu.__version__ = "0.5.0"
    monkeypatch.setitem(sys.modules, "osam", palsu)
    monkeypatch.setattr(osam_api, "_klien", {}, raising=False)
    monkeypatch.setattr(osam_api, "_jenis_prompt", {}, raising=False)
    return palsu


def test_baru_membedakan_generasi(osam_baru):
    assert osam_api.baru() is True
    assert osam_api.versi() == "0.12.0"


def test_baru_false_di_osam_lama(osam_lama):
    assert osam_api.baru() is False


def test_klien_dibuat_sekali_per_perangkat(osam_baru):
    a = osam_api.klien(False)
    b = osam_api.klien(False)
    assert a is b, "klien CPU harus dipakai ulang, bukan dibuat ulang"
    c = osam_api.klien(True)
    assert c is not a, "GPU dan CPU adalah klien berbeda"
    assert (a.device, c.device) == ("cpu", "cuda")
    assert a.in_process is False, (
        "model harus berjalan di proses terpisah: ONNX Runtime tidak "
        "mengembalikan VRAM selama prosesnya hidup, jadi di dalam proses app "
        "memori itu tak pernah bisa dilepas")
    assert a.max_loaded_models == osam_api.MODEL_RESIDEN


def test_klien_gpu_gagal_jatuh_ke_cpu(osam_baru):
    osam_baru.gagal_pada["cuda"] = RuntimeError("CUDA tidak ada")
    c = osam_api.klien(True)
    assert c.device == "cpu", "GPU tak terpakai harus jatuh ke CPU, bukan galat"


def test_segmen_meneruskan_prompt_apa_adanya(osam_baru):
    hasil = osam_api.segmen("sam3:latest", "gambar", gpu=False,
                            points=[(3.0, 4.0)], point_labels=[1])
    assert hasil == ["anotasi sam3:latest di cpu"]
    model, prompt = osam_api.klien(False).panggilan[0]
    assert model == "sam3:latest"
    assert prompt == {"points": [(3.0, 4.0)], "point_labels": [1]}


def test_segmen_ulangi_di_cpu_saat_vram_habis(osam_baru, monkeypatch):
    """Kehabisan VRAM harus diulang di CPU: lambat, tetapi tetap memberi hasil."""
    kena = KlienTiruan(device="cuda", gagal=RuntimeError(
        "Failed to allocate memory for requested buffer of size 1719926784"))
    cpu = KlienTiruan(device="cpu")

    monkeypatch.setattr(osam_api, "klien",
                        lambda gpu: kena if gpu else cpu)
    hasil = osam_api.segmen("sam3:latest", "gambar", gpu=True, box=(1, 2, 3, 4))
    assert hasil == ["anotasi sam3:latest di cpu"]
    assert len(kena.panggilan) == 1 and len(cpu.panggilan) == 1


def test_segmen_tak_mengulang_galat_lain(osam_baru, monkeypatch):
    """Prompt yang salah akan salah juga di CPU; mengulangnya hanya menutupi sebab."""
    kena = KlienTiruan(device="cuda", gagal=ValueError("prompt tidak diterima"))
    cpu = KlienTiruan(device="cpu")
    monkeypatch.setattr(osam_api, "klien", lambda gpu: kena if gpu else cpu)
    with pytest.raises(ValueError, match="prompt tidak diterima"):
        osam_api.segmen("sam3:latest", "gambar", gpu=True, texts=["botol"])
    assert cpu.panggilan == [], "galat bukan soal memori tak boleh diulang"


def test_jenis_prompt_dibaca_dari_mesinnya(osam_baru):
    assert osam_api.jenis_prompt("sam3:latest") == frozenset(
        {"points", "box", "texts"})
    assert osam_api.jenis_prompt("yoloworld:latest") == frozenset({"texts"})
    assert osam_api.jenis_prompt("tak-ada") == frozenset()


def test_jenis_prompt_kosong_di_osam_lama(osam_lama):
    """Di 0.5 tak ada daftarnya, dan itu harus kosong, bukan melempar galat."""
    assert osam_api.jenis_prompt("sam3:latest") == frozenset()


def test_tutup_melepas_semua_klien(osam_baru):
    cpu = osam_api.klien(False)
    gpu = osam_api.klien(True)
    assert osam_api.tutup() is True
    assert (cpu.tertutup, gpu.tertutup) == (True, True)
    assert osam_api.tutup() is False, "tak ada klien -> tak ada yang dilepas"
    assert osam_api.klien(False) is not cpu, "klien baru dibuat sesudah ditutup"


def test_residen_dicatat_dan_dilepas(osam_baru):
    """Penjaga VRAM perlu tahu model mana yang sedang dipegang, supaya ia bisa
    melepas yang lain alih-alih diam-diam pindah ke CPU."""
    assert osam_api.residen() is None
    osam_api.segmen("sam2:small", "gambar", gpu=False, points=[(1.0, 1.0)],
                    point_labels=[1])
    assert osam_api.residen() == "sam2:small"
    osam_api.tutup()
    assert osam_api.residen() is None


def test_hanya_satu_model_residen():
    """Dua model residen membuat yang kedua sering tak kebagian VRAM lalu
    jatuh ke CPU, dan membuat jejak RAM satu proses membengkak sampai 10,7 GB
    di mesin 31 GB berswap 2 GB."""
    assert osam_api.MODEL_RESIDEN == 1


def test_melepas_mengembalikan_ram_ke_sistem(osam_baru, monkeypatch):
    """Melepas objeknya saja menyerahkan memori ke alokator C, bukan ke
    sistem, sehingga RSS prosesnya tetap tinggi."""
    dipanggil = []
    monkeypatch.setattr(osam_api, "_kembalikan_ram",
                        lambda: dipanggil.append(True))
    osam_api.klien(False)
    assert osam_api.tutup() is True
    assert dipanggil == [True]

    dipanggil.clear()
    assert osam_api.tutup() is False
    assert dipanggil == [], "tak ada yang dilepas -> tak perlu merapikan"
