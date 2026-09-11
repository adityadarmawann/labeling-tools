"""
Uji lapisan training.

Yang dijaga di sini bukan "kodenya jalan" melainkan tiga hal yang kalau
bergeser diam-diam akan menghasilkan model yang angkanya bagus dan gagal di
ruang detektor sungguhan:

  1. Angka v14 tidak boleh berubah tanpa ada yang sadar. Itu angka yang
     dibayar mahal — v13 melaporkan mAP50-95 0,9499 lalu benar 0 dari 7 pada
     foto RVM sungguhan.
  2. Setelan warna waktu-latih harus sejalan dengan versinya. v14 menyatakan
     keduanya harus dibuka bersama; membuka salah satu saja membatalkan
     perbaikannya.
  3. Kemajuan harus terbaca dari disk apa adanya, termasuk saat berkasnya
     sedang separuh ditulis.
"""
from __future__ import annotations

import json
import os

import pytest

from app.services import latih


# ============================================================
# ANGKA v14
# ============================================================

def test_angka_inti_v14_tidak_bergeser():
    """Parameter yang membedakan v14 dari v13, dikunci apa adanya.

    Kalau ada yang menurunkannya lagi, di sinilah ketahuan — bukan enam jam
    kemudian lewat mAP yang tinggi tapi tidak berarti.
    """
    p = latih.PRESET_V14
    # Inti v14: warna DIBUKA. v13 memakai hsv_h=0.0 dan hsv_s=0.20.
    assert p["hsv_h"] == 0.030, "hsv_h adalah parameter terpenting v14"
    assert p["hsv_s"] == 0.70
    assert p["hsv_v"] == 0.50
    assert p["bgr"] == 0.10
    # Diturunkan dari 0.6 karena fase crop/zoom sudah mengecilkan objek
    # dengan cara yang benar.
    assert p["mosaic"] == 0.3
    assert p["copy_paste"] == 0.0, "menempel antar-gambar merusak konteks RVM"
    assert p["scale"] == 0.3
    assert p["optimizer"] == "AdamW" and p["lr0"] == 0.0003


def test_erasing_tidak_ada_di_preset():
    """erasing sengaja tidak dipakai.

    Ia hanya dibaca classify_augmentations() (jalur ClassificationDataset),
    jadi untuk task=segment tidak berpengaruh sama sekali. Menuliskannya
    hanya menipu pembaca berikutnya seolah ada oklusi padahal tidak ada.
    """
    assert "erasing" not in latih.PRESET_V14


# ============================================================
# SINKRONISASI WARNA
# ============================================================

def _katalog():
    from app.services import olah
    return olah.katalog_json()["aug"]


def test_versi_dengan_warna_dibuka_disetujui():
    v = {"resep": {}}                  # resep kosong = seluruh bawaan menyala
    h = latih.periksa_warna(v, _katalog())
    assert h["dibuka"] is True
    assert h["tingkat"] == "ok"
    assert h["saran"]["hsv_h"] == latih.PRESET_V14["hsv_h"]
    assert len(h["op_nyala"]) >= 2


def test_versi_dengan_warna_dimatikan_diperingatkan():
    """Ini keadaan v13, dan peringatannya harus menyebut akibatnya."""
    mati = {o: {"aktif": False} for o in latih.OP_WARNA}
    h = latih.periksa_warna({"resep": {"aug": mati}}, _katalog())
    assert h["dibuka"] is False
    assert h["tingkat"] == "awas"
    assert h["op_nyala"] == []
    # Peringatan yang cuma berkata "tidak cocok" tidak menolong siapa pun.
    assert "0 dari 7" in h["pesan"], h["pesan"]
    assert "buat ulang" in h["pesan"].lower(), h["pesan"]


def test_satu_operasi_warna_saja_belum_cukup():
    """Satu saklar menyala bukan berarti warnanya dibuka.

    Ambangnya dua, bukan satu: grayscale sendirian (misalnya) menghapus warna
    tanpa pernah menggeser ronanya, jadi model masih bisa bersandar pada rona
    di gambar yang tidak di-grayscale.
    """
    mati = {o: {"aktif": False} for o in latih.OP_WARNA}
    mati["hue_sat"] = {"aktif": True}
    h = latih.periksa_warna({"resep": {"aug": mati}}, _katalog())
    assert h["op_nyala"] == ["hue_sat"]
    assert h["dibuka"] is False


def test_terang_dan_gamma_tidak_dihitung_sebagai_warna():
    """Keduanya mengubah kecerahan tanpa menyentuh rona.

    Model yang memakai rona sebagai pintasan tetap aman memakainya, jadi
    menghitungnya sebagai 'warna dibuka' akan memberi rasa aman yang palsu.
    """
    assert "terang_kontras" not in latih.OP_WARNA
    assert "gamma" not in latih.OP_WARNA


# ============================================================
# PARAMETER YANG DIMINTA ORANG
# ============================================================

def test_par_kosong_memakai_seluruh_preset():
    par, galat = latih._saring_par({})
    assert not galat
    assert par == latih.PRESET_V14


def test_par_yang_diminta_menimpa_preset():
    par, galat = latih._saring_par({"epochs": 10, "batch": 8})
    assert not galat
    assert par["epochs"] == 10 and par["batch"] == 8
    assert par["hsv_h"] == latih.PRESET_V14["hsv_h"], "sisanya harus tetap v14"


@pytest.mark.parametrize("kunci,nilai", [
    ("epochs", 0), ("epochs", 5000), ("batch", 0), ("batch", 999),
    ("imgsz", 64), ("hsv_h", 2.0), ("mosaic", 1.5), ("lr0", 10.0),
])
def test_angka_di_luar_batas_ditolak(kunci, nilai):
    """Salah ketik yang lolos baru ketahuan enam jam kemudian."""
    _, galat = latih._saring_par({kunci: nilai})
    assert galat and kunci in galat[0], galat


def test_kunci_asing_diabaikan_bukan_menjatuhkan():
    par, galat = latih._saring_par({"kunci_ngawur": 1, "epochs": 3})
    assert not galat
    assert "kunci_ngawur" not in par
    assert par["epochs"] == 3


# ============================================================
# MEMBACA KEMAJUAN DARI DISK
# ============================================================

def test_results_csv_dibaca_beserta_nilai_terbaiknya(tmp_path):
    (tmp_path / "results.csv").write_text(
        "epoch,time,train/box_loss,metrics/mAP50-95(M),metrics/mAP50(B)\n"
        "1,10.0,1.2,0.10,0.20\n"
        "2,20.0,1.0,0.40,0.50\n"
        "3,30.0,0.9,0.25,0.45\n")
    h = latih.baca_hasil_csv(tmp_path)
    assert h["epoch"] == 3
    # Yang ditampilkan nilai TERAKHIR, yang disimpan juga yang TERBAIK:
    # epoch terakhir belum tentu yang terbaik, dan bobot best.pt mengikuti
    # yang terbaik, bukan yang terakhir.
    assert h["metrik"]["mAP50-95 mask"] == 0.25
    assert h["terbaik"]["mAP50-95 mask"] == 0.40
    assert h["detik"] == 30.0


def test_baris_yang_sedang_separuh_ditulis_dilewati(tmp_path):
    """results.csv dibaca selagi Ultralytics menulisnya.

    Baris terakhir bisa terpotong di tengah. Melewatinya jauh lebih baik
    daripada memunculkan angka ngawur di layar untuk satu detik.
    """
    (tmp_path / "results.csv").write_text(
        "epoch,time,metrics/mAP50-95(M)\n"
        "1,10.0,0.10\n"
        "2,20.0\n")                      # terpotong
    h = latih.baca_hasil_csv(tmp_path)
    assert h["epoch"] == 1
    assert h["metrik"]["mAP50-95 mask"] == 0.10


def test_tanpa_results_csv_tidak_meledak(tmp_path):
    h = latih.baca_hasil_csv(tmp_path)
    assert h["epoch"] == 0 and h["metrik"] == {}


def test_proses_yang_sudah_mati_tidak_dianggap_hidup():
    assert latih.hidup(0) is False
    assert latih.hidup(None) is False
    assert latih.hidup(-5) is False
    # PID milik proses ini sendiri hidup, TETAPI cmdline-nya bukan training,
    # jadi harus ditolak: pid dipakai ulang sistem, dan tanpa pemeriksaan itu
    # training yang sudah mati terlihat berjalan selamanya.
    assert latih.hidup(os.getpid()) is False


# ============================================================
# BERKAS DAN PENOMORAN
# ============================================================

def test_penomoran_naik_dan_isi_bertahan(tmp_path):
    assert latih.nomor_berikut(tmp_path) == 1
    latih._tulis(tmp_path, 1, {"nomor": 1, "nama": "a"})
    assert latih.nomor_berikut(tmp_path) == 2
    latih._tulis(tmp_path, 7, {"nomor": 7, "nama": "b"})
    assert latih.nomor_berikut(tmp_path) == 8, "harus dari nomor TERBESAR"
    assert latih.baca(tmp_path, 1)["nama"] == "a"
    assert latih.baca(tmp_path, 99) is None


def test_manifes_rusak_dibaca_sebagai_kosong(tmp_path):
    latih.berkas_latih(tmp_path, 3).parent.mkdir(parents=True, exist_ok=True)
    latih.berkas_latih(tmp_path, 3).write_text("{ bukan json")
    assert latih.baca(tmp_path, 3) is None


def test_siapkan_menolak_tugas_yang_tidak_dikenal(tmp_path):
    with pytest.raises(ValueError):
        latih.siapkan(tmp_path, nama="x", versi_nomor=1, tugas="klasifikasi",
                      bobot="y.pt", par={}, oleh="uji")


def test_siapkan_membekukan_periksa_warna(tmp_path):
    """Alasan sebuah training disetel begitu harus tetap terbaca.

    Versinya bisa dihapus nanti; kalau hasil periksa_warna tidak ikut
    dibekukan, tidak ada lagi cara mengetahui kenapa hsv_h-nya sekian.
    """
    w = latih.periksa_warna({"resep": {}}, _katalog())
    isi = latih.siapkan(tmp_path, nama="x", versi_nomor=1, tugas="segment",
                        bobot="y.pt", par={"epochs": 5}, oleh="uji", warna=w)
    di_disk = latih.baca(tmp_path, isi["nomor"])
    assert di_disk["warna"]["tingkat"] == "ok"
    assert di_disk["warna"]["pesan"]


def test_nama_kosong_diberi_nama_bawaan(tmp_path):
    isi = latih.siapkan(tmp_path, nama="   ", versi_nomor=1, tugas="segment",
                        bobot="y.pt", par={}, oleh="uji")
    assert isi["nama"] == f"Latihan {isi['nomor']}"


def test_bobot_tersedia_selalu_menawarkan_sesuatu():
    """Tanpa berkas lokal pun orang harus bisa mulai melatih."""
    b = latih.bobot_tersedia()
    assert b, "tidak ada satu pun pilihan bobot"
    assert any(x["tugas"] == "segment" for x in b)
    assert all({"nama", "path", "tugas", "lokal"} <= set(x) for x in b)


def test_statistik_tidak_meledak_tanpa_pustakanya():
    s = latih.statistik()
    assert isinstance(s, dict)
