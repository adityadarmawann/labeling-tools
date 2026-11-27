"""Unggahan yang seluruhnya duplikat tak boleh dilaporkan berhasil.

Kejadian nyata di prod, projek darma/coca-cola 27 November 2026:

    10:24 - 10:26   990 x PUT /tambah  -> 200, hasil "sudah-ada"
    10:26:50        log: nama batch 'coca-cola-zero-sugar-vanilla-390ml-001'
                         tidak terpasang: tak ada gambar tanpa batch
    10:43 - 10:45   990 x PUT /tambah  -> 200 (kiriman yang sama diulang)
    10:45:05        log yang sama lagi

Foldernya bernama "coca-cola-vanilla-390ml", tetapi 990 berkas di dalamnya
bernama "coca-cola-original-390ml_g20261119-*" dan sudah ada di projek sejak
20 November, identik byte per byte. Server benar melewatkan semuanya. Yang
salah pelaporannya: /tambah menjawab ok:true untuk berkas yang DILEWATI, dan
unggah.js hanya melihat ok, sehingga bilah mencapai 100%, pesannya "Selesai,
12.986 gambar di dataset" (itu total projek, bukan yang baru), lalu tombol
"Bagi tugas" aktif ke halaman yang tak punya gambar baru untuk dibagi.

45 MB dikirim dua kali tanpa satu pun tanda bahwa nol gambar bertambah.
"""
from __future__ import annotations

import pathlib
import re

from app.services import tambah

JS = (pathlib.Path(__file__).resolve().parents[1]
      / "app" / "static" / "unggah.js").read_text()


# ---------------------------------------------------------------- sisi server

def test_isi_identik_dilewati(tmp_path):
    """Dasar perilakunya: nama sama + isi sama berarti dilewati, bukan baru."""
    dest = tmp_path / "a.jpg"
    dest.write_bytes(b"gambar")
    tmp = tmp_path / "a.jpg.part"
    tmp.write_bytes(b"gambar")

    akhir, hasil = tambah.pasang(tmp, dest)

    assert hasil == "sudah-ada"
    assert akhir is None            # tak ada berkas baru yang mendarat
    assert not tmp.exists()
    assert dest.read_bytes() == b"gambar"


def test_isi_beda_disimpan_dengan_nama_lain(tmp_path):
    """Nama sama tapi isi beda TIDAK boleh dilewati: itu gambar lain."""
    dest = tmp_path / "a.jpg"
    dest.write_bytes(b"lama")
    tmp = tmp_path / "a.jpg.part"
    tmp.write_bytes(b"baru sama sekali")

    akhir, hasil = tambah.pasang(tmp, dest)

    assert hasil == "senama"
    assert akhir == tmp_path / "a-2.jpg"
    assert akhir.read_bytes() == b"baru sama sekali"
    assert dest.read_bytes() == b"lama"     # yang lama tak tertimpa


def test_berkas_benar_benar_baru(tmp_path):
    dest = tmp_path / "a.jpg"
    tmp = tmp_path / "a.jpg.part"
    tmp.write_bytes(b"gambar")

    akhir, hasil = tambah.pasang(tmp, dest)

    assert hasil == "baru"
    assert akhir == dest


# ------------------------------------------------------------------- sisi JS

def test_js_mencacah_nasib_terpisah():
    """"Terkirim" tidak sama dengan "bertambah"; keduanya dicacah terpisah."""
    assert "let baru = 0, dilewati = 0, senama = 0;" in JS
    assert "if (j.hasil === 'sudah-ada') dilewati++;" in JS
    assert "else if (j.hasil === 'senama') { senama++; baru++; }" in JS
    # /upload menolak duplikat dengan ok:false + sudah_ada, bukan lewat hasil.
    assert "if (j.sudah_ada) dilewati++;" in JS


def test_js_menolak_melaporkan_selesai_kalau_nol_baru():
    """Gerbangnya harus BERHENTI, bukan sekadar menambah catatan kecil."""
    i = JS.find("if (!baru && dilewati && !arsip.length && !video.length)")
    assert i > 0, "gerbang nol-baru hilang"
    blok = JS[i:i + 700]
    assert "pr.gagal(" in blok
    assert "Tidak ada gambar baru" in blok
    assert "return;" in blok
    # Gerbangnya harus SEBELUM pemindaian dan penandaan, bukan sesudahnya.
    assert i < JS.find("Memindai isi dataset")


def test_js_pesan_akhir_menyebut_yang_baru_dan_yang_dilewati():
    """Angka total projek sendirian membuat kiriman nihil tampak berhasil."""
    assert "masuk" in JS and "duplikat" in JS
    assert "nama baru" in JS
    assert "Sudah ditambahkan ke batch" in JS


def test_js_toast_batch_menyebut_sebab_duplikat():
    assert "semua berkas yang dikirim sudah ada di" in JS


def test_teks_ui_tanpa_em_dash():
    """Aturan tetap: strip panjang khas AI tak dipakai di teks yang dilihat."""
    # Komentar blok dibuang dulu (barisan lanjutannya tak berawalan apa pun),
    # lalu sisa tiap baris dipotong di // supaya komentar baris ikut lepas.
    tanpa_blok = re.sub(r"/\*.*?\*/", "", JS, flags=re.S)
    for n, baris in enumerate(tanpa_blok.splitlines(), 1):
        if "—" not in baris:
            continue
        ket = baris.find("//")
        kode = baris[:ket] if ket >= 0 else baris
        assert "—" not in kode, \
            f"em dash di teks UI, unggah.js baris {n}: {baris.strip()[:80]}"
