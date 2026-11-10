"""Sinkronisasi: perubahan yang ditulis jalur AI harus terlihat sesi lain.

Tiga penulis anotasi (pre-label, zero-shot, gabungan) tidak memanggil
tandai_berubah, padahal setiap penulis lain memanggilnya. Akibatnya pelabel
yang sedang bekerja di kanvas TIDAK AKAN PERNAH melihat hasil auto-label
selama ia memakai panah kiri/kanan: segarkan() keluar lebih awal kalau
pencacah perubahan tidak naik. Ia lalu melabeli ulang dari nol lalu menyimpan,
dan simpanannya menimpa hasil AI.

Terbukti saat menulis uji konteks kisi: dua uji gagal dengan subset kosong,
dan sebabnya persis ini.
"""
from __future__ import annotations

import pathlib

from conftest import buat_dataset
from app.services import prelabel, scanner
from app import session as sesi


def test_jalur_ai_menaikkan_pencacah_perubahan(tmp_path):
    d = buat_dataset(tmp_path / "sink", 1, 0)
    ip = sorted(d.glob("*.jpg"))[0]
    awal = sesi.cap_sekarang(d)
    prelabel._beritahu(d, ip)
    assert sesi.cap_sekarang(d) > awal


def test_beritahu_tak_menjatuhkan_job_saat_gagal(tmp_path):
    """Satu job auto-label berjam-jam tak boleh mati gara-gara pemberitahuan."""
    prelabel._beritahu(None, None)              # tak melempar


def test_ketiga_orkestrator_memberi_tahu():
    akar = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"
    for nama in ("prelabel.py", "autolabel_zero.py", "autolabel_gabung.py"):
        teks = (akar / nama).read_text()
        assert "_beritahu(" in teks, nama


def test_sidik_diambil_sebelum_memproses():
    """Diambil sesudah, ia merekam keadaan disk yang mungkin memuat perubahan
    yang TIDAK ikut disusulkan, lalu periksa_disk menyimpulkan foldernya tak
    berubah dan pemindaian penuh dilewati -- berkas itu basi selamanya."""
    teks = (pathlib.Path(__file__).resolve().parents[1]
            / "app" / "session.py").read_text()
    blok = teks.split("def segarkan", 1)[1].split("def ", 1)[0]
    i_ambil = blok.index("sidik_awal = sidik_disk(")
    i_pakai = blok.index("self._sidik = sidik_awal")
    assert i_ambil < i_pakai
    assert "self._sidik = sidik_disk(" not in blok


def test_kunci_thumbnail_menyertakan_label_yolo(tmp_path):
    """Pada projek YOLO, anotasinya di labels/*.txt dan .json tak pernah ada:
    menandai latar menulis .txt kosong tanpa menyentuh satu pun berkas yang
    dulu terhitung, jadi thumbnail lama terus dilayani untuk SEMUA akun."""
    img = tmp_path / "images" / "a.jpg"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"x")
    lab = tmp_path / "labels" / "a.txt"
    lab.parent.mkdir(parents=True)
    lab.write_text("0 0.5 0.5 0.2 0.2\n")
    it = {"img": img, "labels": lab, "yolo": True}
    k1 = scanner.kunci_isi(it)
    lab.write_text("")                           # jadi latar
    import os
    st = lab.stat()
    os.utime(lab, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert scanner.kunci_isi(it) != k1


def test_kunci_thumbnail_tanpa_label_tetap_jalan(tmp_path):
    img = tmp_path / "b.jpg"
    img.write_bytes(b"x")
    assert scanner.kunci_isi({"img": img})


def test_tombol_latar_hanya_untuk_yang_berhak():
    akar = pathlib.Path(__file__).resolve().parents[1]
    t = (akar / "app" / "templates" / "index.html").read_text()
    r = (akar / "app" / "routers" / "review.py").read_text()
    assert "p in boleh_latar" in t
    assert '"boleh_latar": boleh_latar' in r
    assert "tugas.tolak_tulis(sess.src, sess.user, it[\"img\"])" in r
