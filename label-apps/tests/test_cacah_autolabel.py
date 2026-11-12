"""Berapa gambar yang akan dikerjakan, disebut SEBELUM dijalankan.

"Berhenti setelah 1000 gambar" tak berarti apa-apa kalau lingkupnya cuma
berisi 12, dan satu-satunya cara tahu lingkupnya kosong adalah menjalankannya
dulu lalu membaca "0 diproses".

Yang dijaga di sini bukan angkanya saja, melainkan bahwa angkanya dihitung
dengan pemilih yang SAMA PERSIS dengan pekerjanya. Kalau antarmuka menurunkan
aturannya sendiri, dialog boleh menjanjikan gambar yang tak akan dikerjakan
dan tak ada satu pun yang terlihat salah.
"""
from __future__ import annotations

import inspect
import json
import pathlib

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL
from app.services import autolabel_gabung, scanner, tag as svc_tag, tugas


def _tulis(ip: pathlib.Path, perlu_dicek=False):
    isi = {"version": "0.4.3", "flags": {}, "imagePath": ip.name,
           "imageHeight": 64, "imageWidth": 64,
           "shapes": [{"label": "botol", "text": "", "group_id": None,
                       "shape_type": "rectangle", "flags": {},
                       "points": [[4, 4], [20, 20]]}]}
    if perlu_dicek:
        isi["perlu_dicek"] = True
    ip.with_suffix(".json").write_text(json.dumps(isi))


def _siap(tmp_path, nama="cacah"):
    """4 gambar: 1 kosong, 1 hasil AI belum diverifikasi, 2 sudah diperiksa."""
    d = buat_dataset(tmp_path / nama, 4, 0)
    ips = sorted(d.glob("*.jpg"))
    _tulis(ips[1], perlu_dicek=True)
    _tulis(ips[2])
    _tulis(ips[3])
    items, _ = scanner.scan(d)
    data = tugas.baca(d)
    return d, items, data


# --------------------------------------------------- satu pemilih, bukan dua
def test_pekerja_memakai_pemilih_bersama():
    """Kalau pemetaan sasaran -> saring disalin di dua tempat, keduanya boleh
    menyimpang tanpa ada yang tahu."""
    src = inspect.getsource(autolabel_gabung.jalankan)
    assert "tugas.target_autolabel(" in src
    assert "gambar_belum_batch" not in src
    assert "gambar_konteks_job" not in src


def test_cacah_memakai_pemilih_yang_sama(tmp_path):
    d, items, data = _siap(tmp_path, "sama")
    c = tugas.cacah_autolabel(items, data, d, "paul", batch="")
    for s in tugas.SASARAN:
        target = tugas.target_autolabel(items, data, d, batch="", sasaran=s)
        assert c[s]["total"] == len(target), s


# ------------------------------------------------------------- angkanya benar
def test_tiga_sasaran_dihitung_sekaligus(tmp_path):
    d, items, data = _siap(tmp_path, "tiga")
    c = tugas.cacah_autolabel(items, data, d, "paul", batch="")
    assert set(c) == set(tugas.SASARAN)
    assert c["belum"]["total"] == 1          # yang kosong saja
    assert c["dicek"]["total"] == 2          # + hasil AI belum diverifikasi
    assert c["semua"]["total"] == 4          # seluruhnya


def test_pemilik_boleh_menulis_semuanya(tmp_path):
    d, items, data = _siap(tmp_path, "boleh")
    c = tugas.cacah_autolabel(items, data, d, "paul", batch="")
    for s in tugas.SASARAN:
        assert c[s]["boleh"] == c[s]["total"], s


def test_gambar_orang_lain_tak_dihitung_boleh(tmp_path):
    """Gambar yang tak boleh ditulis tetap masuk lingkup lalu dilewati saat
    jalan; dialog harus menyebutnya di depan, bukan di hasil.

    Projeknya harus DIORGANISASI dulu: selama belum ada tugas maupun anggota,
    siapa pun yang bisa membukanya memang boleh menyuntingnya, dan aturan hak
    tulisnya belum berlaku sama sekali.
    """
    d, items, data = _siap(tmp_path, "luar")
    nama = sorted(p.name for p in d.glob("*.jpg"))
    tugas.tugaskan(d, "paul", "paul", nama, judul="punya-paul")
    data = tugas.baca(d)

    c = tugas.cacah_autolabel(items, data, d, "paul", batch="")
    assert c["semua"]["boleh"] == c["semua"]["total"] == 4

    c2 = tugas.cacah_autolabel(items, data, d, "orang-lain", batch="")
    assert c2["semua"]["total"] == 4          # tetap masuk lingkup
    assert c2["semua"]["boleh"] == 0          # tetapi tak satu pun bisa ditulis


def test_tugas_asing_menghasilkan_kosong(tmp_path):
    """{} berarti antarmuka diam saja, bukan menampilkan nol yang keliru."""
    d, items, data = _siap(tmp_path, "asing")
    assert tugas.cacah_autolabel(items, data, d, "paul", tid="tidak-ada") == {}


def test_batch_lain_tak_ikut_terhitung(tmp_path):
    d, items, data = _siap(tmp_path, "batchlain")
    kunci = [svc_tag.kunci_gambar(d, it["img"]) for it in items[:2]]
    svc_tag.pasang(d, kunci, batch="B")
    items, _ = scanner.scan(d)
    c = tugas.cacah_autolabel(items, data, d, "paul", batch="B")
    assert c["semua"]["total"] == 2


# ---------------------------------------------------------------- lewat rute
def test_rute_info_mengirim_cacah(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "info-cacah", 3, 0)
    ips = sorted(d.glob("*.jpg"))
    _tulis(ips[0], perlu_dicek=True)
    klien.post(f"/setsrc?path={d}")

    j = klien.get("/api/autolabel/info", params={"batch": ""}).json()
    assert j.get("ok"), j
    c = j.get("cacah") or {}
    assert c["belum"]["total"] == 2
    assert c["dicek"]["total"] == 3
    assert c["semua"]["total"] == 3


def test_rute_info_tanpa_lingkup_tetap_jalan(klien, lingkungan):
    """Dialog lama (tanpa parameter) tak boleh jadi galat."""
    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_dataset(ruang / "info-polos", 2, 0)
    klien.post(f"/setsrc?path={d}")
    j = klien.get("/api/autolabel/info").json()
    assert j.get("ok"), j
    assert "cacah" in j


# --------------------------------------------------------------- antarmuka
def test_dialog_menampilkan_cacah_dan_alasan():
    js = pathlib.Path("app/static/anotasi.js").read_text()
    h = pathlib.Path("app/templates/anotasi.html").read_text()
    assert "azCacah" in js and "az-sasaran-ket" in js
    # lingkup ikut dikirim, bukan menghitung seluruh projek
    assert "q.set('tid'" in js and "q.set('batch'" in js
    # alasan Mulai mati duduk di sebelah tombolnya
    assert 'id="az-sebab"' in h
    assert "Tidak ada gambar yang cocok" in js
    assert "Pilih dulu satu sumber" in js


def test_cacah_dipakai_mematikan_mulai():
    js = pathlib.Path("app/static/anotasi.js").read_text()
    i = js.index("function azMulaiSiap(")
    blok = js[i:js.index("async function bukaAutolabel(", i)]
    assert "mulai.disabled = !!sebab" in blok
    assert "c.boleh === 0" in blok
