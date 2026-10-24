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

from app.services import gpu_sinyal, autolabel


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
