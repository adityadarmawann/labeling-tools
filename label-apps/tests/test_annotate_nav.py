"""
Regresi navigasi kanvas (annotate._nav).

Dulu `_nav` memanggil `items.index(it)` untuk mencari posisi gambar yang sedang
dibuka. Tiap item memuat bentuk ber-`pts` ARRAY NUMPY, dan list.index memakai
`==` yang pada array numpy melempar
"ValueError: The truth value of an array with more than one element is
ambiguous" — jadi halaman Anotasi 500 untuk projek yang bentuknya tersimpan
sebagai array (terlihat sungguhan di log prod: darma/action-basket). `it` selalu
objek yang SAMA dari sess.items, maka pencarian identitas (`is`) benar + cepat +
tak pernah menyentuh perbandingan array.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np

from app.routers import annotate


def _sess(items):
    # _nav (cabang daftar penuh) hanya butuh .lock + .items; src=None supaya
    # job diabaikan dan jalur items.index lama yang dipakai.
    return SimpleNamespace(lock=threading.Lock(), items=items, src=None)


def test_nav_item_ber_array_numpy_tak_crash():
    a = {"img": "a.jpg", "shapes": [{"pts": np.array([[1., 2.], [3., 4.]])}]}
    b = {"img": "b.jpg", "shapes": [{"pts": np.array([[5., 6.]])}]}
    c = {"img": "c.jpg", "shapes": []}
    sess = _sess([a, b, c])
    prev, nxt, pos, daftar = annotate._nav(sess, b, job="")
    assert prev is a and nxt is c
    assert pos == (2, 3)
    assert daftar == [a, b, c]


def test_nav_ujung_dan_tunggal():
    a = {"img": "a.jpg", "shapes": [{"pts": np.array([[1., 2.]])}]}
    b = {"img": "b.jpg", "shapes": []}
    sess = _sess([a, b])
    prev, nxt, pos, _ = annotate._nav(sess, a, job="")      # pertama: tak ada prev
    assert prev is None and nxt is b and pos == (1, 2)
    prev, nxt, pos, _ = annotate._nav(sess, b, job="")      # terakhir: tak ada next
    assert prev is a and nxt is None and pos == (2, 2)
    prev, nxt, pos, _ = annotate._nav(_sess([a]), a, job="")  # satu item
    assert prev is None and nxt is None and pos == (1, 1)


def test_nav_it_tak_ada_tak_crash():
    a = {"img": "a.jpg", "shapes": [{"pts": np.array([[1., 2.]])}]}
    lain = {"img": "x.jpg", "shapes": [{"pts": np.array([[9., 9.]])}]}
    prev, nxt, pos, daftar = annotate._nav(_sess([a]), lain, job="")
    assert prev is None and nxt is None and pos == (0, 1) and daftar == [a]


def test_nav_beku_jumlah_tetap_saat_item_dilabeli(monkeypatch):
    """Masuk dari "belum dianotasi" (mis. 600) -> panah tetap menyusuri 600 walau
    gambar dilabeli dan keluar dari filter live. Jumlah TAK menyusut & posisi tak
    hilang sampai keluar ke papan lalu buka lagi (bekukan ulang)."""
    from pathlib import Path
    from app.services import tugas

    mk = lambda n: {"img": Path(n), "shapes": []}
    a, b, cc = mk("a.jpg"), mk("b.jpg"), mk("c.jpg")
    semua = [a, b, cc]
    byp = {str(x["img"].resolve()): x for x in semua}
    sess = SimpleNamespace(lock=threading.Lock(), items=semua, src=Path("/proj"),
                           nav_beku=None, find=lambda p: byp.get(p))
    monkeypatch.setattr(tugas, "baca_projek", lambda *a, **k: {})
    subset = [a, b, cc]                                   # "belum" awal = 3
    monkeypatch.setattr(tugas, "gambar_konteks_job", lambda *a, **k: list(subset))

    # masuk labeler di b -> BEKUKAN snapshot (3)
    prev, nxt, pos, daftar = annotate._nav(
        sess, b, job="J", saring="belum", uploads_root=None, bekukan=True)
    assert pos == (2, 3) and prev is a and nxt is cc
    assert len(sess.nav_beku["nama"]) == 3

    # b DILABELI -> subset live menyusut jadi [a, cc]; snapshot TETAP 3
    subset[:] = [a, cc]
    prev, nxt, pos, daftar = annotate._nav(
        sess, b, job="J", saring="belum", uploads_root=None)   # bekukan=False
    assert pos == (2, 3), f"jumlah menyusut, mestinya tetap 3: {pos}"
    assert prev is a and nxt is cc and len(daftar) == 3

    # maju ke cc -> masih dalam snapshot 3
    prev, nxt, pos, _ = annotate._nav(
        sess, cc, job="J", saring="belum", uploads_root=None)
    assert pos == (3, 3) and prev is b and nxt is None

    # ganti konteks (saring beda) -> snapshot disegarkan ke subset live sekarang
    prev, nxt, pos, _ = annotate._nav(
        sess, a, job="J", saring="sudah", uploads_root=None)
    assert pos[1] == 2                                   # subset live kini [a, cc]


def test_gambar_404_kalau_berkas_hilang_di_tengah_jalan(klien, lingkungan):
    """Frame ADA saat sesi memindai, lalu terhapus/terpindah (dibuang ke sampah,
    re-ekstraksi) SEBELUM /gambar membacanya. Dulu read_bytes melempar
    FileNotFoundError -> 500 'server error' tiap pindah ke gambar berikutnya
    (terlihat di log prod court-badminton saat churn ekstraksi). Harus 404
    bersih; navigasi lain tetap jalan."""
    import pathlib

    from tests.test_data import masuk, PW_PAUL
    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "hilang-ditengah", n=2, label=False)
    klien.post(f"/setsrc?path={d}")
    g = sorted(d.glob("*.jpg"))
    path = str(g[0])
    assert klien.get("/gambar", params={"path": path}).status_code == 200
    g[0].unlink()                              # terhapus di tengah labeling
    r = klien.get("/gambar", params={"path": path})
    assert r.status_code == 404, f"harus 404 bersih, bukan {r.status_code}"
    # Frame KEDUA yang masih ada tetap terlayani -> satu frame hilang tak
    # mematikan navigasi.
    assert klien.get("/gambar", params={"path": str(g[1])}).status_code == 200
