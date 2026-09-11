"""
Batas beban: berapa pembuatan versi boleh berjalan bersamaan, dan berapa utas
tiap pekerjaan boleh memakai.

Kenapa ini diuji sama seriusnya dengan hasil piksel: alat ini dipakai satu tim,
dan penjaga di rute /api/versi/mulai hanya menolak pembuatan versi kedua DARI
AKUN YANG SAMA. Lima orang berbeda bisa menekan "Buat" bersamaan, dan terukur
di mesin pengembangan (6 inti fisik / 12 utas) satu pembuatan versi memakai
2,22 inti di jalur CPU dan 3,65 inti di jalur GPU. Lima serentak meminta 18
inti dari 12 yang ada — bukan antre, melainkan saling merebut: setiap
pekerjaan jadi jauh lebih lambat daripada kalau dikerjakan berurutan, dan
pelabelan orang lain yang sedang berlangsung ikut tersendat.
"""
from __future__ import annotations

import threading
import time

import pytest

from app.services import buatversi


def test_giliran_menahan_yang_melebihi_batas():
    """Yang berjalan bersamaan tidak boleh lebih dari SERENTAK."""
    n = buatversi.SERENTAK
    sedang = 0
    puncak = 0
    kunci = threading.Lock()
    lepas = threading.Event()

    def kerja(i):
        nonlocal sedang, puncak
        with buatversi.Giliran(f"uji-{i}"):
            with kunci:
                sedang += 1
                puncak = max(puncak, sedang)
            lepas.wait(2.0)
            with kunci:
                sedang -= 1

    utas = [threading.Thread(target=kerja, args=(i,)) for i in range(n + 3)]
    for t in utas:
        t.start()
    # Beri waktu supaya yang kebagian benar-benar masuk sebelum diperiksa.
    time.sleep(0.35)
    assert buatversi.menunggu() == 3, (
        f"{buatversi.menunggu()} menunggu, seharusnya 3 — yang kelebihan "
        "tidak tertahan")
    lepas.set()
    for t in utas:
        t.join(5.0)
        assert not t.is_alive(), "ada pekerjaan yang tidak pernah dapat giliran"
    assert puncak <= n, f"{puncak} berjalan bersamaan, batasnya {n}"
    for i in range(n + 3):
        buatversi.bersihkan_maju(f"uji-{i}")


def test_yang_antre_melaporkan_dirinya_sedang_menunggu():
    """Antarmuka harus bisa berkata 'menunggu giliran', bukan diam.

    Pekerjaan yang tampak macet padahal cuma antre itu persis yang membuat
    orang menekan tombolnya berkali-kali — dan tiap tekanan menambah antrean.
    """
    n = buatversi.SERENTAK
    lepas = threading.Event()
    mulai = threading.Event()

    def penahan(i):
        with buatversi.Giliran(f"tahan-{i}"):
            mulai.set()
            lepas.wait(2.0)

    utas = [threading.Thread(target=penahan, args=(i,)) for i in range(n)]
    for t in utas:
        t.start()
    mulai.wait(2.0)

    hasil = {}

    def antre():
        with buatversi.Giliran("yang-antre"):
            hasil["dapat"] = True

    ta = threading.Thread(target=antre)
    ta.start()
    time.sleep(0.35)
    maju = buatversi.kemajuan("yang-antre")
    assert maju.get("jalan") is True, maju
    assert "enunggu" in (maju.get("fase_nama") or ""), maju
    assert maju.get("antre", 0) >= 1, maju

    lepas.set()
    for t in utas + [ta]:
        t.join(5.0)
    assert hasil.get("dapat"), "yang antre tidak pernah kebagian giliran"
    for k in [f"tahan-{i}" for i in range(n)] + ["yang-antre"]:
        buatversi.bersihkan_maju(k)


def test_batas_utas_menyisakan_inti_untuk_melayani():
    """Jatah utas harus menyisakan sekurangnya satu inti.

    Yang disisakan itu bukan kemewahan: permintaan web dan pelabelan otomatis
    adalah yang ditunggu orang di depan layar, dan keduanya tidak boleh kalah
    oleh pekerjaan latar yang tidak ditunggu siapa pun.
    """
    import os

    b = buatversi.batasi_utas()
    inti = os.cpu_count() or 4
    assert b["utas_per_pekerjaan"] >= 1
    assert b["serentak"] >= 1
    assert b["utas_per_pekerjaan"] * b["serentak"] < inti, (
        f"{b['serentak']} x {b['utas_per_pekerjaan']} utas mengisi penuh "
        f"{inti} inti — tidak ada sisa untuk melayani permintaan")


def test_batas_utas_benar_benar_dipasang_ke_cv2():
    """Bukan sekadar angka yang dilaporkan — cv2 harus benar-benar dibatasi."""
    import cv2

    b = buatversi.batasi_utas()
    assert cv2.getNumThreads() == b["utas_per_pekerjaan"]


def test_batas_bisa_diatur_lewat_env(monkeypatch):
    monkeypatch.setenv("LABELAPP_UTAS", "3")
    assert buatversi.batasi_utas()["utas_per_pekerjaan"] == 3
    monkeypatch.delenv("LABELAPP_UTAS")
    buatversi.batasi_utas()          # kembalikan ke bawaan
