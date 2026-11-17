"""Angka tiap saringan dihitung atas hasil saringan LAIN yang sedang aktif.

Tanpa facet: orang menyaring split "valid", membuka menu kelas, melihat
"botol 842", mencentangnya, dan mendapat NOL hasil -- karena 842 itu angka
seluruh dataset. Tidak ada apa pun di layar yang menjelaskannya.

Yang juga dijaga: dimensi sebuah facet TIDAK ikut menyaring dirinya sendiri.
Kalau ikut, mencentang satu kelas membuat semua kelas lain menunjukkan nol dan
daftar itu berhenti bisa dipakai memilih.
"""
from __future__ import annotations

import json
import pathlib
import re

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL
from app.services import konteks_kisi as K


def _fakta(sev="ok", kelas=None, objek=None, dicek=False, tag=(), batch="",
           split="", turunan="asli", nama="a.jpg", aku=False, issues=()):
    objek = objek or {k: 1 for k in (kelas or [])}
    return {"it": {"issues": list(issues), "shapes": [1] if objek else []},
            "sev": sev, "kelas": set(objek), "objek": objek,
            "n_obj": sum(objek.values()), "dicek": dicek, "aku": aku,
            "tag": set(tag), "batch": batch, "split": split,
            "turunan": turunan, "nama": nama}


def _p(**ubah):
    p = K.baca({})
    p.update(ubah)
    return p


def test_kelas_dihitung_hanya_dalam_split_yang_dipilih():
    fk = [_fakta(kelas=["botol"], split="train"),
          _fakta(kelas=["botol"], split="train"),
          _fakta(kelas=["botol"], split="valid")]
    fc = K.facet(fk, _p(split=["valid"]))
    assert fc["kelas_gambar"]["botol"] == 1          # bukan 3


def test_dimensi_sendiri_tidak_menyaring_dirinya():
    """Mencentang 'botol' tidak boleh membuat 'kaleng' jadi nol: daftar itu
    masih harus bisa dipakai menambah atau menukar pilihan."""
    fk = [_fakta(kelas=["botol"]), _fakta(kelas=["kaleng"])]
    fc = K.facet(fk, _p(kelas=["botol"]))
    assert fc["kelas_gambar"] == {"botol": 1, "kaleng": 1}
    # Tetapi hasil yang TAMPIL memang cuma yang cocok.
    assert len(fc["tampil"]) == 1


def test_keadaan_dihitung_dalam_saringan_kelas():
    fk = [_fakta(sev="ok", kelas=["botol"]),
          _fakta(sev="stop", kelas=[], objek={}),
          _fakta(sev="ok", kelas=["kaleng"])]
    fc = K.facet(fk, _p(kelas=["botol"]))
    assert fc["keadaan"]["sudah"] == 1               # bukan 2
    assert fc["keadaan"]["all"] == 1


def test_tag_dan_unggahan_saling_memengaruhi():
    fk = [_fakta(kelas=["a"], tag=["pagi"], batch="u1"),
          _fakta(kelas=["a"], tag=["sore"], batch="u1"),
          _fakta(kelas=["a"], tag=["pagi"], batch="u2")]
    fc = K.facet(fk, _p(batch="u1"))
    assert fc["tag"] == {"pagi": 1, "sore": 1}
    fc2 = K.facet(fk, _p(tag=["pagi"]))
    assert fc2["batch"] == {"u1": 1, "u2": 1}


def test_pencarian_nama_ikut_mempersempit_angka():
    fk = [_fakta(kelas=["botol"], nama="pagi-01.jpg"),
          _fakta(kelas=["botol"], nama="sore-01.jpg")]
    fc = K.facet(fk, _p(cari="pagi"))
    assert fc["kelas_gambar"]["botol"] == 1


def test_objek_dan_gambar_dihitung_terpisah():
    fk = [_fakta(objek={"botol": 3}), _fakta(objek={"botol": 1})]
    fc = K.facet(fk, _p())
    assert fc["kelas_gambar"]["botol"] == 2
    assert fc["kelas_objek"]["botol"] == 4
    assert fc["n_obj_tampil"] == 4


def test_tanpa_kelas_ikut_ber_facet():
    fk = [_fakta(sev="bg", objek={}, split="train"),
          _fakta(sev="bg", objek={}, split="valid"),
          _fakta(sev="stop", objek={}, split="valid")]
    fc = K.facet(fk, _p(split=["valid"]))
    assert fc["tanpa"] == {"latar": 1, "unlab": 1}


def test_predikat_facet_sama_dengan_yang_menyaring():
    """Dua salinan aturan saring selalu berakhir berbeda, dan bedanya senyap:
    yang satu menghitung, yang lain menampilkan."""
    fk = [_fakta(sev="ok", kelas=["botol"], dicek=True),
          _fakta(sev="ok", kelas=["botol"]),
          _fakta(sev="stop", objek={})]
    for nilai in ("all", "sudah", "unlab", "dicek", "verif", "bg"):
        fc = K.facet(fk, _p(f=nilai))
        assert fc["keadaan"][nilai] == len(fc["tampil"]), nilai


# ------------------------------------------------------------- ujung ke ujung
def test_menu_kelas_menyusut_mengikuti_saringan_lain(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "facet-e2e", 4, 0)
    klien.post(f"/setsrc?path={d}")
    ips = sorted(d.glob("*.jpg"))
    for i, ip in enumerate(ips):
        ip.with_suffix(".json").write_text(json.dumps({
            "version": "0.4.3", "flags": {}, "imagePath": ip.name,
            "imageHeight": 64, "imageWidth": 64,
            "perlu_dicek": i < 2,
            "shapes": [{"label": "botol", "text": "", "group_id": None,
                        "shape_type": "rectangle", "flags": {},
                        "points": [[4, 4], [20, 20]]}]}))
    klien.get("/")

    polos = klien.get("/").text
    assert 'gambar memuat kelas ini">4</b>' in polos
    # Disaring ke yang belum diverifikasi: angkanya harus ikut menyusut.
    saring = klien.get("/", params={"f": "dicek"}).text
    assert 'gambar memuat kelas ini">2</b>' in saring
