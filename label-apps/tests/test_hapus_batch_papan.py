"""Kartu batch basi tidak boleh menetap di layar.

Dilaporkan dari prod: sebuah batch dihapus, berhasil, 3380 gambarnya pindah
ke tempat sampah. Kartunya tetap di papan karena halamannya tidak dimuat
ulang. Klik berikutnya dijawab "tidak ada gambar di batch itu yang bisa kamu
hapus" -- benar, memang sudah terhapus -- dan karena gagal pun tidak memuat
ulang, kartunya tetap di situ lagi. Dari layar, batch itu tampak tidak bisa
dihapus selamanya.

Jadi KEGAGALAN juga harus memuat ulang: gagal di sini hampir selalu berarti
papannya sudah tidak sesuai keadaan server.
"""
from __future__ import annotations

import pathlib
import re

JS = pathlib.Path("app/static/anotasi.js").read_text()


def _blok_hapus_batch() -> str:
    """Isi handler .an-hapus-batch saja, bukan seluruh berkas."""
    i = JS.index("an-hapus-batch")
    j = JS.index("async function kirim(", i)
    return JS[i:j]


def test_gagal_pun_memuat_ulang():
    blok = _blok_hapus_batch()
    # Tak boleh ada lagi jalan keluar yang cuma men-toast lalu berhenti.
    assert "toast(j.error); b.disabled = false; return;" not in blok
    assert blok.count("location.reload()") >= 2


def test_pesan_bertahan_melewati_muat_ulang():
    """Memuat ulang menghapus toast biasa sebelum sempat terbaca, jadi alasan
    gagalnya harus dititipkan dulu."""
    assert "function toastSesudahMuat(" in JS
    blok = _blok_hapus_batch()
    assert "toastSesudahMuat(j.error)" in blok
    # Dan ditampilkan lagi sesudah halaman baru siap.
    assert "sessionStorage.getItem(KUNCI_PESAN)" in JS
    assert "sessionStorage.removeItem(KUNCI_PESAN)" in JS


def test_titipan_dibuang_sesudah_ditampilkan():
    """Kalau tidak dibuang, pesannya muncul lagi di tiap muat ulang
    berikutnya, termasuk yang tak ada hubungannya."""
    i = JS.index("const KUNCI_PESAN")
    j = JS.index("// ---- buka/tutup menu", i)
    blok = JS[i:j]
    assert re.search(r"removeItem\(KUNCI_PESAN\)", blok)


def test_mode_privat_tak_menelan_pesan():
    """sessionStorage bisa melempar (mode privat). Pesannya tetap harus
    terlihat, walau cuma sekejap sebelum halaman berganti."""
    i = JS.index("function toastSesudahMuat(")
    blok = JS[i:i + 400]
    assert "catch" in blok and "toast(m)" in blok


def test_keberhasilan_tetap_melapor_jumlahnya():
    blok = _blok_hapus_batch()
    assert "gambar dibuang ke tempat sampah" in blok
    assert "dilindungi" in blok
