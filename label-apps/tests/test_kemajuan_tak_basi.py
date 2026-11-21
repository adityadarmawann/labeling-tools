"""Jalan BARU tak boleh memperlihatkan hasil jalan sebelumnya.

Dilaporkan dari prod: auto-label ditekan, layar langsung berkata "Selesai.
0 gambar diisi, 0 objek", tetapi anotasi terus bertambah di latar. Terukur
sesudahnya: 580 berkas benar-benar ditulis pada menit yang sama.

Sebabnya catat_maju MENGGABUNG (d.update), bukan mengganti. Saat jalan baru
mulai, pencacahnya memang disetel nol, tetapi bendera akhir jalan SEBELUMNYA
(selesai, galat, dibatalkan, training) tetap tinggal. Antarmuka memeriksa
`selesai` lebih dulu, jadi ia menggambar hasil akhir palsu lalu berhenti
memantau, dan pekerjaan yang sesungguhnya berjalan tanpa terlihat.
"""
from __future__ import annotations

import inspect

import pytest

from app.services import autolabel_gabung, autolabel_zero, prelabel


MODUL = (("autolabel_gabung", autolabel_gabung),
         ("autolabel_zero", autolabel_zero),
         ("prelabel", prelabel))


@pytest.mark.parametrize("nama,mod", MODUL)
def test_saluran_dikosongkan_sebelum_jalan_baru(nama, mod):
    src = inspect.getsource(mod.jalankan)
    i = src.index("catat_maju(kunci, jalan=True")
    sebelum = src[:i]
    assert "bersihkan_maju(kunci)" in sebelum, nama


@pytest.mark.parametrize("nama,mod", MODUL)
def test_bendera_akhir_benar_benar_hilang(nama, mod):
    """Inti perbaikannya, diuji pada perilaku dan bukan pada teks kodenya."""
    kunci = f"uji-maju-{nama}"
    mod.catat_maju(kunci, jalan=False, selesai=True, diproses=580, objek=1234)
    assert mod.kemajuan(kunci).get("selesai") is True
    mod.bersihkan_maju(kunci)
    mod.catat_maju(kunci, jalan=True, persen=0.0, diproses=0, objek=0, total=0)
    m = mod.kemajuan(kunci)
    assert m.get("selesai") is None      # tak lagi mengaku sudah selesai
    assert m.get("jalan") is True
    assert m.get("diproses") == 0
    mod.bersihkan_maju(kunci)


@pytest.mark.parametrize("nama,mod", MODUL)
def test_tanpa_pengosongan_bendera_lama_bertahan(nama, mod):
    """Menunjukkan persis apa yang dulu terjadi, supaya perbaikannya tak
    pelan-pelan dibatalkan orang yang tak tahu sebabnya."""
    kunci = f"uji-basi-{nama}"
    mod.catat_maju(kunci, jalan=False, selesai=True, diproses=580, objek=1234)
    mod.catat_maju(kunci, jalan=True, persen=0.0, diproses=0, objek=0, total=0)
    m = mod.kemajuan(kunci)
    assert m.get("selesai") is True      # inilah layar "Selesai, 0 objek"
    assert m.get("diproses") == 0
    mod.bersihkan_maju(kunci)


@pytest.mark.parametrize("galat", ["galat", "dibatalkan", "training"])
def test_semua_bendera_akhir_ikut_hilang(galat):
    """Bukan cuma `selesai`: galat dan dibatalkan sama-sama menghentikan
    pemantauan di antarmuka."""
    kunci = "uji-bendera"
    autolabel_zero.catat_maju(kunci, jalan=False, **{galat: True})
    autolabel_zero.bersihkan_maju(kunci)
    autolabel_zero.catat_maju(kunci, jalan=True)
    assert autolabel_zero.kemajuan(kunci).get(galat) is None
    autolabel_zero.bersihkan_maju(kunci)
