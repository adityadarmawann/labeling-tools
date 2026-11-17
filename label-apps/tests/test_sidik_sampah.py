"""Membuang gambar ke sampah HARUS mengubah tanda tangan folder.

Dilaporkan dari prod, dan ini akar dari "batch tidak bisa dihapus". Membuang
satu batch MEMINDAHKAN berkasnya ke `_sampah-gambar/` di dalam projek yang
sama. sidik_disk dulu menelusuri seluruh pohon kecuali folder bertitik, jadi
`_sampah-gambar/` ikut terhitung dan ketiga komponen tanda tangannya tidak
berubah sama sekali: jumlah berkas sama (berkasnya masih di dalam pohon),
total byte sama, dan mtime terbaru sama karena memindah mempertahankan mtime.

Akibatnya jalur cepat di Session.load memakai cache lama apa adanya. Gambar
yang sudah dibuang tetap hidup di ingatan sesi: kartunya menolak hilang dari
papan, "Hapus batch" menjawab 0 terhapus selamanya, dan menyalakan ulang
server pun tak menolong karena cachenya ada di disk.

Terukur di prod pada projek botol-kaleng-tetra-mlp-cup-1: sesudah 5108 gambar
dibuang, sidik disk dan sidik di cache sama persis,
(33806, 1794457678089098476, 2008381659).
"""
from __future__ import annotations

import os
import shutil

from conftest import buat_dataset
from app.config import FOLDER_INTERNAL
from app.session import sidik_disk


def test_pindah_ke_sampah_mengubah_sidik(tmp_path):
    d = buat_dataset(tmp_path / "s1", 4, 0)
    sebelum = sidik_disk(d)

    sampah = d / "_sampah-gambar" / "20261112-000000" / "images"
    sampah.mkdir(parents=True)
    ip = sorted(d.glob("*.jpg"))[0]
    # Dipindah, bukan disalin: mtime-nya ikut, persis seperti buang_gambar.
    shutil.move(str(ip), str(sampah / ip.name))

    assert sidik_disk(d) != sebelum


def test_mtime_memang_bertahan_saat_dipindah(tmp_path):
    """Dasar dari bug ini: kalau memindah mengubah mtime, komponen ketiga
    tanda tangan akan menangkapnya dan tak ada yang perlu diperbaiki."""
    d = buat_dataset(tmp_path / "s2", 1, 0)
    ip = sorted(d.glob("*.jpg"))[0]
    sebelum = os.stat(ip).st_mtime_ns
    tujuan = d / "_sampah-gambar" / "images"
    tujuan.mkdir(parents=True)
    shutil.move(str(ip), str(tujuan / ip.name))
    assert os.stat(tujuan / ip.name).st_mtime_ns == sebelum


def test_isi_sampah_tak_ikut_terhitung(tmp_path):
    """Menambah berkas DI DALAM sampah bukan perubahan dataset, jadi tak boleh
    memicu pemindaian ulang."""
    d = buat_dataset(tmp_path / "s3", 2, 0)
    sebelum = sidik_disk(d)
    sampah = d / "_sampah-gambar" / "lama"
    sampah.mkdir(parents=True)
    (sampah / "apa-saja.jpg").write_bytes(b"x" * 4096)
    assert sidik_disk(d) == sebelum


def test_semua_folder_internal_dilewati(tmp_path):
    d = buat_dataset(tmp_path / "s4", 1, 0)
    sebelum = sidik_disk(d)
    for nama in FOLDER_INTERNAL:
        f = d / nama
        f.mkdir(parents=True, exist_ok=True)
        (f / "isi.bin").write_bytes(b"y" * 1024)
    assert sidik_disk(d) == sebelum


def test_folder_bertitik_tetap_dilewati(tmp_path):
    """Perilaku lama yang harus bertahan: .versi/ menyimpan hasil versi, dan
    menghitungnya membuat tiap pembuatan versi memicu pindai penuh."""
    d = buat_dataset(tmp_path / "s5", 1, 0)
    sebelum = sidik_disk(d)
    v = d / ".versi" / "v1" / "train" / "images"
    v.mkdir(parents=True)
    (v / "hasil.jpg").write_bytes(b"z" * 2048)
    assert sidik_disk(d) == sebelum


def test_gambar_asli_tetap_terhitung(tmp_path):
    """Jangan sampai perbaikannya membuat sidik berhenti melihat apa pun."""
    d = buat_dataset(tmp_path / "s6", 2, 0)
    sebelum = sidik_disk(d)
    (d / "baru.jpg").write_bytes(b"w" * 3000)
    assert sidik_disk(d) != sebelum
