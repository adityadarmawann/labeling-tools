"""
Pembebasan VRAM SAM saat training + penanda GPU antar-proses (gpu_sinyal).

Tujuannya: di GPU 8 GB, sesi SAM (sam2:small ~4 GB) yang residen di proses app
tidak boleh membuat training OOM. Dua mekanisme diuji di sini:
  1. gpu_sinyal: penanda "training memegang GPU", tahan terhadap penanda basi.
  2. autolabel.bebaskan_gpu() melepas sesi SAM; _boleh_gpu_osam & MobileSAM
     memilih CPU selama training aktif.
"""
from __future__ import annotations

import os

import pytest

from app.services import gpu_sinyal, autolabel


@pytest.fixture(autouse=True)
def _penanda_terisolasi(tmp_path, monkeypatch):
    """Penanda GPU ada di /tmp dan dibagi seluruh mesin. Saat suite berjalan
    paralel (atau ada training nyata), itu bisa saling mengganggu. Arahkan ke
    berkas sementara per-test supaya test ini tak pernah menyentuh penanda asli."""
    monkeypatch.setattr(gpu_sinyal, "_MARKER", tmp_path / "gpu-aktif.json")


def _bersihkan():
    gpu_sinyal.lepas_latih()


def test_penanda_roundtrip_dan_basi():
    _bersihkan()
    assert gpu_sinyal.latih_aktif() is False
    gpu_sinyal.tandai_latih(os.getpid())        # proses hidup
    assert gpu_sinyal.latih_aktif() is True
    gpu_sinyal.lepas_latih()
    assert gpu_sinyal.latih_aktif() is False
    # PID yang pasti sudah mati -> penanda dianggap basi, dibersihkan.
    gpu_sinyal.tandai_latih(2 ** 30)
    assert gpu_sinyal.latih_aktif() is False
    _bersihkan()


def test_penanda_pid_init_dianggap_basi():
    # Marker ber-pid 1 (init) pernah terjadi: training menulisnya lalu mati di
    # reboot. init SELALU hidup, jadi tanpa penjagaan latih_aktif macet True
    # selamanya (SAM dipaksa CPU, auto-label terblokir). pid <= 1 harus basi.
    import json
    _bersihkan()
    gpu_sinyal._MARKER.write_text(json.dumps({"pid": 1}))
    assert gpu_sinyal.latih_aktif() is False
    assert not gpu_sinyal._MARKER.exists()          # basi -> ikut dibersihkan
    gpu_sinyal._MARKER.write_text(json.dumps({"pid": 0}))
    assert gpu_sinyal.latih_aktif() is False
    _bersihkan()


def test_tandai_latih_tak_pernah_tulis_pid_init(monkeypatch):
    # Walau dipaksa pid=1, yang tertulis adalah pid proses sendiri (bukan 1).
    import json
    _bersihkan()
    gpu_sinyal.tandai_latih(1)
    d = json.loads(gpu_sinyal._MARKER.read_text())
    assert d["pid"] == os.getpid() and d["pid"] > 1
    _bersihkan()


def test_bebaskan_gpu_melepas_sesi_mobilesam():
    _bersihkan()
    # Pura-pura ada sesi MobileSAM residen; bebaskan harus melepasnya.
    autolabel._sesi_mobilesam = object()
    assert autolabel.bebaskan_gpu() is True
    assert autolabel._sesi_mobilesam is None
    # Tak ada yang residen -> aman, tak ada efek.
    assert autolabel.bebaskan_gpu() is False


def test_osam_ke_cpu_saat_training_aktif(monkeypatch):
    _bersihkan()
    # Paksa seolah mode GPU supaya yang memutus hanyalah penanda training.
    monkeypatch.setattr(autolabel.olah_gpu, "MODE", "gpu", raising=False)
    gpu_sinyal.tandai_latih(os.getpid())
    try:
        # Model kecil yang normalnya boleh GPU -> harus False karena training aktif.
        assert autolabel._boleh_gpu_osam("sam2:small") is False
    finally:
        _bersihkan()
