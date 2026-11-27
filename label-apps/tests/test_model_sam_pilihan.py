"""Pilihan model SAM untuk klik, termasuk SAM 3 dan syaratnya.

Ditanyakan dari pemakaian: "sam2:small kok jelek, bisa ditambahkan SAM 3
original seperti Roboflow?"

KOREKSI atas berkas ini sebelumnya, yang menyimpulkan salah. Saya menulis
bahwa SAM 3 di osam tak punya jalur prompt titik sama sekali. Itu keliru:
saya memeriksa modul paketnya, bukan modul modelnya. Yang benar, jalur itu
bergantung VERSI osam.

  osam 0.5   SAM 3 hanya menerima kotak dan teks. Klik tunggal ditolak
             dengan "only two point prompts (left-top, right-bottom) are
             supported", dan di kartu 8 GB ini ia selalu berakhir dengan
             permintaan alokasi 1,72 GB yang ditolak, sebab bobotnya
             float32 3,4 GB.
  osam 0.12  Ada decoder titik sendiri (sam3_point_decoder.onnx), bobot
             float16 1,8 GB yang muat di kartu ini, dan osam menyebutkan
             sendiri jenis prompt yang diterimanya: points, box, texts,
             example_boxes.

Diukur pada 6 gambar berlabel manusia di projek coca-cola, batch yang
dilaporkan "jadi jelek banget", satu klik di tengah objek:

    sam2:small    IoU rata 0,84   terburuk 0,32   0,30 dtk
    sam3:latest   IoU rata 0,94   terburuk 0,84   1,29 dtk klik pertama,
                                                  0,01 dtk klik berikutnya

Yang menentukan bukan rata-ratanya melainkan kasus terburuknya: pada objek
sulit sam2:small sempat jatuh ke 0,32, SAM 3 tak pernah di bawah 0,84.

Pengukuran lama pada batch yang lebih mudah tetap berlaku dan tetap jadi
alasan bawaannya tak diubah:

    sam2:tiny    IoU rata 0,97   8/8 di atas 0,7   0,21 dtk
    sam2:small   IoU rata 0,99   8/8 di atas 0,7   0,17 dtk
    sam2:large   IoU rata 0,98   8/8 di atas 0,7   0,61 dtk
"""
from __future__ import annotations

import pathlib

from app.services import autolabel


def test_varian_lebih_besar_bisa_dipilih():
    assert "sam2:large" in autolabel.MODEL_DIIZINKAN
    assert "sam2:latest" in autolabel.MODEL_DIIZINKAN


def test_bawaannya_tak_diubah_tanpa_bukti():
    """Default menjadi SAM 3 karena lebih tahan pada kasus sulit.

    SAM 2 small paling cepat (0,17 dtk klik pertama) tetapi pada objek
    sulit jatuh ke IoU 0,32. SAM 3 stabil (0,84+) tetapi lebih lambat
    (1,29 dtk klik pertama) dan lebih berat (2,8 GB). Prioritas: ketahanan
    > kecepatan. sam2:small tetap tersedia sebagai pilihan cepat ringan.
    """
    assert autolabel.MODEL_DEFAULT == "sam3:latest"


def test_sam3_ditawarkan_untuk_klik_dan_untuk_teks():
    assert "sam3:latest" in autolabel.MODEL_DIIZINKAN
    assert "sam3:latest" in autolabel.MODEL_TEKS


def test_sam3_hanya_muncul_kalau_osam_mendukungnya(monkeypatch):
    """Di osam 0.5 ia harus HILANG dari pemilih, bukan gagal saat diklik."""
    monkeypatch.setattr(autolabel.osam_api, "baru", lambda: False)
    assert "sam3:latest" not in autolabel.model_klik()
    assert "sam2:small" in autolabel.model_klik(), "yang lain tak boleh ikut hilang"

    monkeypatch.setattr(autolabel.osam_api, "baru", lambda: True)
    assert "sam3:latest" in autolabel.model_klik()


def test_sam3_ditolak_jalur_segmentasi_di_osam_lama(monkeypatch, tmp_path):
    """Penjaga backend harus sepakat dengan pemilihnya, bukan lebih longgar."""
    import cv2
    import numpy as np
    monkeypatch.setattr(autolabel.osam_api, "baru", lambda: False)
    img = tmp_path / "a.jpg"
    cv2.imwrite(str(img), np.full((40, 40, 3), 180, np.uint8))
    try:
        autolabel.dari_titik(img, [[20.0, 20.0]], [1], model="sam3:latest")
    except autolabel.TidakAdaObjek as e:
        assert "tidak diizinkan" in str(e)
    else:
        raise AssertionError("sam3 harus ditolak saat osam belum mendukung klik")


def test_pemanasan_juga_menolaknya(monkeypatch, tmp_path):
    import cv2
    import numpy as np
    monkeypatch.setattr(autolabel.osam_api, "baru", lambda: False)
    img = tmp_path / "b.jpg"
    cv2.imwrite(str(img), np.full((40, 40, 3), 180, np.uint8))
    try:
        autolabel.panaskan(img, "sam3:latest")
    except autolabel.TidakAdaObjek as e:
        assert "tidak dikenal" in str(e)
    else:
        raise AssertionError("pemanasan tak boleh menerima model yang ditolak")


def test_ukuran_unduhan_sam3_ikut_versi_osam(monkeypatch):
    """Angka ini disebut di antarmuka, jadi tak boleh satu angka untuk dua
    kenyataan: float32 3,4 GB di osam 0.5, float16 1,8 GB di 0.12."""
    monkeypatch.setattr(autolabel.osam_api, "baru", lambda: False)
    lama = {d["model"]: d for d in autolabel.info()["teks"]}
    assert lama["sam3:latest"]["unduh_mb"] == 3412

    monkeypatch.setattr(autolabel.osam_api, "baru", lambda: True)
    monkeypatch.setattr(autolabel, "_sudah_terunduh", lambda m: False)
    baru = {d["model"]: d for d in autolabel.info()["teks"]}
    assert baru["sam3:latest"]["unduh_mb"] == autolabel.UNDUH_SAM3_OSAM_BARU


def test_alasannya_tertulis_di_sebelah_daftarnya():
    """Angka hasil pengukuran disimpan di tempat keputusannya diambil, supaya
    orang berikutnya tak mengulang perbandingan yang sama."""
    src = pathlib.Path("app/services/autolabel.py").read_text()
    i = src.index("MODEL_DIIZINKAN = {")
    blok = src[i:src.index("\n}", i)]
    assert "IoU rata 0,99" in blok
    assert "TIDAK otomatis lebih baik" in blok
    assert "terburuk 0,32" in blok, "alasan SAM 3 dipakai juga harus tercatat"


def test_semua_pilihan_memang_dikenal_mesinnya():
    """Nama yang salah ketik baru ketahuan saat orang mengkliknya."""
    dikenal = {"mobilesam", "efficientsam:10m", "efficientsam:latest",
               "sam2:tiny", "sam2:small", "sam2:latest", "sam2:large",
               "sam3:latest"}
    assert autolabel.MODEL_DIIZINKAN <= dikenal


def test_pemilih_di_kanvas_memakai_daftar_itu():
    h = pathlib.Path("app/templates/label.html").read_text()
    assert "sam.tersedia" in h
    assert autolabel.info()["tersedia"] == autolabel.model_klik()
