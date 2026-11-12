"""Auto-label dua jalan: mask produk dulu, kotak logo menyusul.

Satu batch sering berisi satu jenis produk, dan yang diinginkan dua keluaran
dengan geometri berbeda: segmentasi presisi untuk objeknya, kotak untuk
logonya. Satu jalan tak bisa menghasilkan keduanya -- hasilnya dipaksa ke satu
bentuk mengikuti format projek -- jadi jalannya dua kali.

Dua hal yang dulu membuat jalan kedua mustahil:

  SASARAN  Auto-label hanya menyentuh gambar yang BELUM beranotasi. Sesudah
           jalan pertama, seluruh gambar batch sudah punya bentuk, jadi jalan
           kedua menemukan nol sasaran.
  PENULIS  tulis_prediksi mengganti seluruh daftar bentuk. Andai jalan kedua
           sampai ke gambarnya pun, hasil jalan pertama terhapus.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL
from app.services import prelabel, scanner, tugas


def _bentuk(label, x0, y0, x1, y1, jenis="rectangle"):
    ttk = ([[x0, y0], [x1, y1]] if jenis == "rectangle"
           else [[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
    return {"label": label, "text": "", "points": ttk, "group_id": None,
            "shape_type": jenis, "flags": {}}


# ------------------------------------------------------------- sasaran
def _item(sev_shapes, dicek=False):
    return {"shapes": sev_shapes, "issues": [], "perlu_dicek": dicek}


def test_sasaran_belum_hanya_gambar_kosong():
    kosong, isi = _item([]), _item([_bentuk("a", 0, 0, 1, 1)])
    assert tugas._boleh_sasaran(kosong, "belum") is True
    assert tugas._boleh_sasaran(isi, "belum") is False


def test_sasaran_dicek_membuka_hasil_ai():
    ai = _item([_bentuk("a", 0, 0, 1, 1)], dicek=True)
    manusia = _item([_bentuk("a", 0, 0, 1, 1)], dicek=False)
    assert tugas._boleh_sasaran(ai, "dicek") is True
    assert tugas._boleh_sasaran(manusia, "dicek") is False
    # Yang kosong tetap ikut: ia selalu boleh.
    assert tugas._boleh_sasaran(_item([]), "dicek") is True


def test_sasaran_semua_termasuk_kerja_manusia():
    manusia = _item([_bentuk("a", 0, 0, 1, 1)], dicek=False)
    assert tugas._boleh_sasaran(manusia, "semua") is True


def test_sasaran_tak_dikenal_jatuh_ke_yang_paling_aman(tmp_path):
    d = buat_dataset(tmp_path / "s", 2, 0)
    items, _ = scanner.scan(d)
    a = tugas.gambar_belum_batch(items, d, "", "ngawur")
    b = tugas.gambar_belum_batch(items, d, "", "belum")
    assert [x["img"].name for x in a] == [x["img"].name for x in b]


# ------------------------------------------------------------- penulis
def test_tambah_menyimpan_yang_lama(tmp_path):
    d = buat_dataset(tmp_path / "t", 1, 0)
    ip = sorted(d.glob("*.jpg"))[0]
    it = {"img": ip, "W": 100, "H": 100}
    prelabel.tulis_prediksi(it, d, {}, [_bentuk("botol", 10, 10, 60, 90,
                                                "polygon")])
    prelabel.tulis_prediksi(it, d, {}, [_bentuk("logo", 20, 30, 40, 45)],
                            tambah=True)
    sh = json.loads(ip.with_suffix(".json").read_text())["shapes"]
    assert sorted(s["label"] for s in sh) == ["botol", "logo"]
    assert {s["shape_type"] for s in sh} == {"polygon", "rectangle"}


def test_tanpa_tambah_tetap_mengganti(tmp_path):
    """Perilaku lama dipertahankan sebagai bawaan."""
    d = buat_dataset(tmp_path / "t2", 1, 0)
    ip = sorted(d.glob("*.jpg"))[0]
    it = {"img": ip, "W": 100, "H": 100}
    prelabel.tulis_prediksi(it, d, {}, [_bentuk("botol", 10, 10, 60, 90)])
    prelabel.tulis_prediksi(it, d, {}, [_bentuk("logo", 20, 30, 40, 45)])
    sh = json.loads(ip.with_suffix(".json").read_text())["shapes"]
    assert [s["label"] for s in sh] == ["logo"]


def test_menjalankan_resep_sama_dua_kali_tak_menggandakan(tmp_path):
    """Tanpa pembanding, menjalankan resep yang sama dua kali menumpuk dua
    salinan tiap objek di tempat yang sama persis, dan memisahkannya kembali
    jauh lebih lama daripada melabeli dari nol."""
    d = buat_dataset(tmp_path / "t3", 1, 0)
    ip = sorted(d.glob("*.jpg"))[0]
    it = {"img": ip, "W": 100, "H": 100}
    b = [_bentuk("botol", 10, 10, 60, 90)]
    prelabel.tulis_prediksi(it, d, {}, b)
    prelabel.tulis_prediksi(it, d, {}, b, tambah=True)
    sh = json.loads(ip.with_suffix(".json").read_text())["shapes"]
    assert len(sh) == 1


def test_kelas_berbeda_di_tempat_sama_justru_dipertahankan():
    """Mask produk dan kotak logo memang bertumpuk; itu yang dicari."""
    lama = [_bentuk("botol", 10, 10, 60, 90, "polygon")]
    baru = [_bentuk("logo", 12, 12, 58, 88)]          # nyaris menutupi
    assert len(prelabel.gabung_bentuk(lama, baru)) == 2


def test_geometri_berbeda_di_tempat_sama_tak_dibuang():
    """Jalan pertama poligon presisi, jalan kedua kotak, kelas sama.

    Kalau jenis bentuk tak ikut jadi kunci, kotak jalan kedua dianggap
    duplikat poligon jalan pertama dan hilang tanpa suara, padahal user
    memilih "kotak" secara sadar di dialog.
    """
    lama = [_bentuk("botol", 10, 10, 60, 90, "polygon")]
    baru = [_bentuk("botol", 10, 10, 60, 90, "rectangle")]
    gab = prelabel.gabung_bentuk(lama, baru)
    assert len(gab) == 2
    assert {b["shape_type"] for b in gab} == {"polygon", "rectangle"}


def test_jumlah_yang_dilaporkan_adalah_yang_benar_benar_masuk(tmp_path):
    """Pencacah jangan mengaku menambah objek yang tak pernah ditulis.

    Dengan `tambah=True` sebagian bentuk baru bisa dibuang karena
    menduplikasi yang sudah ada. Pemanggil yang menghitung dari panjang
    daftar usulan akan melaporkan angka yang tak ada di berkas, persis
    jenis kebohongan yang sudah diperbaiki pada pencacah "dilewati".
    """
    d = buat_dataset(tmp_path / "t4", 1, 0)
    ip = sorted(d.glob("*.jpg"))[0]
    it = {"img": ip, "W": 100, "H": 100}
    b = [_bentuk("botol", 10, 10, 60, 90)]

    masuk: list[int] = []
    prelabel.tulis_prediksi(it, d, {}, b, ditambah=masuk)
    assert masuk == [1]

    # usulan yang sama persis: nol yang benar-benar masuk
    masuk2: list[int] = []
    prelabel.tulis_prediksi(it, d, {}, b, tambah=True, ditambah=masuk2)
    assert masuk2 == [0]
    sh = json.loads(ip.with_suffix(".json").read_text())["shapes"]
    assert len(sh) == 1

    # satu baru, satu duplikat -> dilaporkan 1, bukan 2
    masuk3: list[int] = []
    prelabel.tulis_prediksi(
        it, d, {}, [b[0], _bentuk("botol", 70, 70, 95, 95)],
        tambah=True, ditambah=masuk3)
    assert masuk3 == [1]
    sh = json.loads(ip.with_suffix(".json").read_text())["shapes"]
    assert len(sh) == 2


def test_objek_sama_kelas_sama_tapi_jauh_tetap_ditambah():
    lama = [_bentuk("botol", 0, 0, 20, 20)]
    baru = [_bentuk("botol", 70, 70, 95, 95)]
    assert len(prelabel.gabung_bentuk(lama, baru)) == 2


# ------------------------------------------------------- nama kelas per jalan
def test_namai_memaksa_satu_nama_kelas():
    """Taksonomi model ("botol") bukan nama yang dipakai dataset, dan prompt
    yang bagus untuk deteksi buruk sebagai nama kelas."""
    from app.services.autolabel_gabung import Deteksi, namai
    d = [Deteksi(bbox=(0, 0, 9, 9), label="botol"),
         Deteksi(bbox=(5, 5, 9, 9), label="bottle cap")]
    assert [x.label for x in namai(d, "botol-pet-ades")] == \
        ["botol-pet-ades", "botol-pet-ades"]


def test_namai_kosong_membiarkan_label_asli():
    from app.services.autolabel_gabung import Deteksi, namai
    d = [Deteksi(bbox=(0, 0, 9, 9), label="botol")]
    for kosong in ("", "   ", None):
        assert [x.label for x in namai(d, kosong)] == ["botol"]


def test_namai_tak_mengubah_geometri():
    from app.services.autolabel_gabung import Deteksi, namai
    d = [Deteksi(bbox=(1, 2, 3, 4), label="a", pts=[[1, 2], [3, 4]], skor=0.7)]
    g = namai(d, "b")[0]
    assert (g.bbox, g.pts, g.skor) == ((1, 2, 3, 4), [[1, 2], [3, 4]], 0.7)


def test_nama_kelas_dibersihkan_di_rute():
    """Nama kelas masuk ke berkas label dan data.yaml training; baris baru
    atau tab akan merusak keduanya."""
    from app.routers.autolabel import _kelas_nama
    assert _kelas_nama("  logo  ades \n") == "logo ades"
    assert _kelas_nama("a\tb") == "a b"
    assert _kelas_nama(None) == ""
    assert len(_kelas_nama("x" * 300)) == 80


def test_dialog_menawarkan_nama_kelas():
    h = pathlib.Path("app/templates/anotasi.html").read_text()
    assert 'id="az-kelas-nama"' in h
    js = pathlib.Path("app/static/anotasi.js").read_text()
    # dikirim untuk SETIAP sumber, bukan cuma satu
    assert js.count("kelas_nama: kelasNama") == 4


# ------------------------------------------------------------- rute + UI
def test_rute_menerima_sasaran(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d = buat_dataset(lingkungan["ruang"] / "dua-jalan", 2, 0)
    klien.post(f"/setsrc?path={d}")
    j = klien.post("/api/autolabel/mulai",
                   json={"batch": "", "sasaran": "dicek",
                         "sumber": [{"jenis": "terlatih", "nomor": 1}]}).json()
    # Diterima bentuk bodinya; sumbernya boleh gagal belakangan.
    assert "sasaran" not in str(j.get("error", ""))


def test_dialog_menawarkan_pilihan_sasaran():
    akar = pathlib.Path(__file__).resolve().parents[1]
    html = (akar / "app" / "templates" / "anotasi.html").read_text()
    js = (akar / "app" / "static" / "anotasi.js").read_text()
    assert 'id="az-sasaran"' in html
    for nilai in ("belum", "dicek", "semua"):
        assert f'value="{nilai}"' in html, nilai
    assert "sasaran" in js and "batas, iou, sasaran" in js
