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
