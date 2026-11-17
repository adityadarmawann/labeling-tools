"""Kegagalan masuk: POST lalu redirect lalu GET.

Gejala yang dilaporkan: halaman dev memunculkan "Confirm Form Resubmission"
dan ERR_CACHE_MISS. Sebabnya kegagalan masuk dirender LANGSUNG dari POST, jadi
peramban menyimpan halaman itu sebagai hasil sebuah POST: menekan muat ulang
atau tombol kembali menagih kiriman ulang, dan Chrome yang tak menyimpan badan
POST menolaknya sama sekali.

Yang salah bentuk responsnya, bukan pemeriksaannya. Pembatasan laju dan
pembedaan "belum disetujui" vs "sandi salah" tetap berlaku.
"""
from __future__ import annotations

import pathlib
import re

from tests.test_data import PW_PAUL


def test_sandi_salah_redirect_bukan_render(klien):
    r = klien.post("/login", data={"user": "paul", "pw": "salah"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert "e=salah" in r.headers["location"]
    assert "<form" not in r.text                 # tak ada HTML dari POST


def test_halaman_tujuan_menampilkan_pesannya(klien):
    r = klien.post("/login", data={"user": "paul", "pw": "salah"},
                   follow_redirects=False)
    h = klien.get(r.headers["location"]).text
    assert "Akun atau password salah" in h
    assert 'value="paul"' in h                   # akunnya tak perlu diketik lagi


def test_login_benar_tetap_bekerja(klien):
    r = klien.post("/login", data={"user": "paul", "pw": PW_PAUL},
                   follow_redirects=False)
    assert r.status_code == 303
    assert "e=" not in r.headers["location"]


def test_next_dipertahankan_lewat_redirect(klien):
    r = klien.post("/login", data={"user": "paul", "pw": "salah",
                                   "next": "/anotasi"},
                   follow_redirects=False)
    assert "next=%2Fanotasi" in r.headers["location"], r.headers["location"]
    h = klien.get(r.headers["location"]).text
    assert "/anotasi" in h


def test_teks_galat_tak_bisa_disuntik_lewat_query(klien):
    """Pesannya dioper sebagai KODE, bukan teks. Teks bebas di query berarti
    siapa pun bisa membuat tautan yang menampilkan kalimat apa pun di halaman
    masuk kita sendiri."""
    h = klien.get("/login", params={"e": "Akun Anda diblokir, hubungi 0812..."}).text
    assert "diblokir" not in h
    assert "0812" not in h


def test_kode_galat_tak_dikenal_diabaikan(klien):
    h = klien.get("/login", params={"e": "ngawur"}).text
    assert "ngawur" not in h


def test_tak_ada_post_yang_merender_html_lagi():
    """Mengunci polanya, bukan cuma satu rutenya: rute POST berikutnya yang
    merender HTML akan mengulang gejala yang sama persis."""
    akar = pathlib.Path(__file__).resolve().parents[1] / "app" / "routers"
    pelanggar = []
    for f in sorted(akar.glob("*.py")):
        rute = None
        for i, ln in enumerate(f.read_text().splitlines(), 1):
            m = re.match(r'@router\.(post|put|delete)\("([^"]+)"', ln.strip())
            if m:
                rute = f"{f.name} {m.group(1).upper()} {m.group(2)}"
            elif ln.startswith("@router.get"):
                rute = None
            elif rute and "TemplateResponse(" in ln:
                pelanggar.append(f"{rute} (baris {i})")
    # Dikecualikan: dua penolakan /daftar yang terjadi SEBELUM apa pun
    # diproses (pendaftaran ditutup, mode hanya-undangan). Keduanya tak
    # melahirkan akun dan tak membawa isian, jadi memuat ulangnya cuma menolak
    # lagi -- dan 403 di sana keterangan yang berguna. Yang TIDAK boleh ada
    # lagi: POST yang memproses isian lalu merender hasilnya.
    assert len(pelanggar) == 2, pelanggar
    assert all("/daftar" in x for x in pelanggar), pelanggar


# --------------------------------------------------------- pendaftaran
def test_daftar_gagal_tak_membawa_sandi_di_url(klien, lingkungan):
    """Sandi di query berarti sandi di riwayat peramban, di log proxy, dan di
    header Referer halaman berikutnya."""
    r = klien.post("/daftar", data={"user": "budi", "pw": "rahasia-ku",
                                    "pw2": "beda-sekali"},
                   follow_redirects=False)
    assert r.status_code == 303
    lokasi = r.headers["location"]
    assert "rahasia-ku" not in lokasi and "beda-sekali" not in lokasi, lokasi
    assert "akun=budi" in lokasi                 # yang bukan rahasia tetap dibawa


def test_daftar_teks_layanan_hanya_dengan_kodenya(klien):
    """Teks bebas diterima HANYA bersama kode "layanan": tautan buatan orang
    lain tak bisa menyuntik kalimat tanpa ikut menyebut kodenya."""
    tanpa = klien.get("/daftar", params={"pesan": "Hubungi 0812 untuk aktivasi"}).text
    assert "0812" not in tanpa
    dengan = klien.get("/daftar", params={"e": "layanan",
                                          "pesan": "Nama akun itu sudah ada."}).text
    assert "sudah ada" in dengan


def test_pendaftaran_berhasil_juga_lewat_redirect(klien, lingkungan):
    """Dirender langsung dari POST, menekan muat ulang MENGIRIM ULANG
    pendaftarannya: percobaan kedua ditolak "nama akun itu sudah ada", dan
    orang yang baru saja berhasil justru melihat pesan kegagalan."""
    r = klien.post("/daftar", data={"user": "tio", "pw": "sandi-tio-12",
                                    "pw2": "sandi-tio-12"},
                   follow_redirects=False)
    assert r.status_code == 303
    lokasi = r.headers["location"]
    assert "ok=tio" in lokasi, lokasi
    h = klien.get(lokasi).text
    assert "tio" in h
    # Dan halamannya bisa dimuat ulang berkali-kali tanpa efek apa pun.
    assert klien.get(lokasi).status_code == 200
