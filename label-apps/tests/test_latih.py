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


# ============================================================
# RUTE
# ============================================================
#
# Yang diuji di sini penjagaannya, bukan trainingnya: melatih sungguhan
# memakan menit dan menuntut GPU, jadi ia diuji terpisah lewat server
# sungguhan. Yang TIDAK boleh lolos ke sini adalah rute yang bisa dipakai
# membaca berkas di luar folder training, dan rute kelola yang terbuka untuk
# orang yang bukan pemilik projek.

from conftest import PW_PAUL, buat_dataset, masuk


def _projek(klien, lingkungan):
    d = lingkungan["ruang"] / "pl"
    d.mkdir(parents=True, exist_ok=True)
    buat_dataset(d, 3, 2)
    klien.post(f"/setsrc?path={d}")
    return d


def test_halaman_latih_butuh_sesi(klien):
    r = klien.get("/latih?ds=apa-saja", follow_redirects=False)
    assert r.status_code in (303, 307), r.status_code


def test_daftar_kosong_untuk_projek_baru(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _projek(klien, lingkungan)
    r = klien.get("/api/latih/daftar").json()
    assert r["ok"] is True
    assert r["daftar"] == []
    assert "statistik" in r


def test_bahan_form_membawa_preset_dan_batas(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _projek(klien, lingkungan)
    r = klien.get("/api/latih/bahan").json()
    assert r["ok"] is True
    # Formnya tidak boleh digambar setengah: semua bahannya datang sekaligus.
    assert r["preset"]["hsv_h"] == 0.030
    assert r["batas"]["epochs"] == [1, 1000]
    assert r["bobot"], "harus selalu ada pilihan bobot"
    assert r["tugas"] == ["segment", "detect"]


def test_mulai_menolak_versi_yang_tidak_ada(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _projek(klien, lingkungan)
    r = klien.post("/api/latih/mulai", json={"versi": 99}).json()
    assert r["ok"] is False
    assert "99" in r["error"]


def test_mulai_menolak_batch_terlalu_panjang(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _projek(klien, lingkungan)
    r = klien.post("/api/latih/mulai",
                   json={"versi": 1, "batch": [{} for _ in range(20)]}).json()
    assert r["ok"] is False


@pytest.mark.parametrize("nama", [
    "../../../../etc/passwd", ".ssh", "a/b.png", "..\\windows\\win.ini",
])
def test_rute_grafik_menolak_keluar_dari_foldernya(klien, lingkungan, nama):
    """Tanpa penjagaan ini, parameter `nama` membuat rute ini bisa membaca
    berkas apa pun yang bisa dijangkau proses server."""
    masuk(klien, "paul", PW_PAUL)
    _projek(klien, lingkungan)
    assert klien.get(f"/latih/grafik?nomor=1&nama={nama}").status_code == 404


def test_rute_bobot_hanya_menerima_best_atau_last(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _projek(klien, lingkungan)
    assert klien.get("/latih/bobot?nomor=1&jenis=ngawur").status_code == 400
    # best/last yang belum ada tetap 404, bukan 500.
    assert klien.get("/latih/bobot?nomor=1&jenis=best").status_code == 404


def test_rincian_training_yang_tidak_ada(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _projek(klien, lingkungan)
    r = klien.get("/api/latih/rincian?nomor=404").json()
    assert r["ok"] is False


# ============================================================
# SETELAN WARNA TIDAK BISA DISETEL DARI LUAR
# ============================================================
#
# Keempat angka warna (hsv_h, hsv_s, hsv_v, bgr) ditentukan SEPENUHNYA oleh
# mode warna versinya, dan tidak lagi ditawarkan sebagai kotak isian. Bukan
# kerapian: keempatnya harus sejalan dengan cara versinya diaugmentasi, dan
# kombinasi yang tidak sejalan menghasilkan model yang angkanya bagus lalu
# gagal di ruang detektor — persis kegagalan v13 (mAP50-95 0,9499, benar 0
# dari 7).
#
# Selama masih ada jalan menyetelnya dari luar — permintaan buatan sendiri,
# batch yang disalin dari percobaan lain, satu angka yang diubah tanpa tahu
# pasangannya — jalan itu cepat atau lambat akan dipakai.

@pytest.mark.parametrize("mode,hsv_h,bgr", [
    ("bentuk", 0.030, 0.10),
    ("warna", 0.0, 0.0),
])
def test_setelan_warna_ditentukan_mode_bukan_pemanggil(tmp_path, mode, hsv_h, bgr):
    isi = latih.siapkan(
        tmp_path, nama="x", versi_nomor=1, tugas="segment", bobot="y.pt",
        # Angka warna yang dikirim sengaja NGAWUR — harus diabaikan seluruhnya.
        par={"epochs": 7, "hsv_h": 0.4, "hsv_s": 0.01, "hsv_v": 0.02, "bgr": 0.9},
        oleh="uji", warna={"mode": mode})
    par = isi["par"]
    assert par["hsv_h"] == hsv_h, "hsv_h dari pemanggil tidak boleh dipakai"
    assert par["bgr"] == bgr, "bgr dari pemanggil tidak boleh dipakai"
    assert par["hsv_s"] != 0.01 and par["hsv_v"] != 0.02
    # Yang BUKAN setelan warna tetap boleh diatur orang.
    assert par["epochs"] == 7


def test_dua_mode_menghasilkan_setelan_yang_berlawanan(tmp_path):
    a = latih.siapkan(tmp_path, nama="a", versi_nomor=1, tugas="segment",
                      bobot="y.pt", par={}, oleh="uji",
                      warna={"mode": "bentuk"})["par"]
    b = latih.siapkan(tmp_path, nama="b", versi_nomor=1, tugas="segment",
                      bobot="y.pt", par={}, oleh="uji",
                      warna={"mode": "warna"})["par"]
    assert a["hsv_h"] > 0 and b["hsv_h"] == 0
    assert a["bgr"] > 0 and b["bgr"] == 0
    # hsv_v sama di kedua mode: terang harus selalu jadi petunjuk yang tidak
    # bisa diandalkan, ruang detektor kadang terang kadang remang.
    assert a["hsv_v"] == b["hsv_v"]


def test_mode_tidak_disebut_memakai_bawaan(tmp_path):
    isi = latih.siapkan(tmp_path, nama="x", versi_nomor=1, tugas="segment",
                        bobot="y.pt", par={}, oleh="uji")
    from app.services import mode_warna as mw
    assert isi["par"]["hsv_h"] == mw.par_latih(mw.BAWAAN)["hsv_h"]


def test_mode_boleh_dipilih_per_percobaan(klien, lingkungan, monkeypatch):
    """Satu antrean bisa membandingkan dua mode dari versi yang SAMA.

    Itu alasan pilihannya ada di halaman Training, bukan hanya di wizard
    versi: yang sedang diputuskan orang di sini adalah model seperti apa yang
    mau dilatih, dan membandingkan dua jawaban atas pertanyaan itu tidak boleh
    menuntut membangun ulang datasetnya.
    """
    from app.services import mode_warna as mw

    masuk(klien, "paul", PW_PAUL)
    d = _projek(klien, lingkungan)

    # Versi tiruan yang cukup untuk rutenya: data.yaml ada, mode `bentuk`.
    from app.services import buatversi, versi as svc_versi
    dv = buatversi.dir_versi(d, 1)
    dv.mkdir(parents=True, exist_ok=True)
    (dv / "data.yaml").write_text("nc: 1\nnames: ['a']\n")
    svc_versi.buat(d, "paul", "8:1:1", [], {}, {"split": {}, "kelas": 1},
                   "", resep=mw.tulis_ke_resep({}, mw.BENTUK), nomor=1)

    # Diluncurkan dengan mode yang BERBEDA dari versinya.
    dijalankan = []
    monkeypatch.setattr(latih, "jalankan",
                        lambda ds, n: dijalankan.append(n) or {})
    r = klien.post("/api/latih/mulai", json={
        "versi": 1,
        "batch": [{"nama": "A", "bobot": "x.pt", "mode_warna": "bentuk",
                   "par": {"epochs": 2}},
                  {"nama": "B", "bobot": "x.pt", "mode_warna": "warna",
                   "par": {"epochs": 2}}],
    }).json()
    assert r["ok"] is True, r
    assert len(r["dibuat"]) == 2

    a = latih.baca(d, r["dibuat"][0])
    b = latih.baca(d, r["dibuat"][1])
    assert a["par"]["hsv_h"] == 0.030 and a["par"]["bgr"] == 0.10
    assert b["par"]["hsv_h"] == 0.0 and b["par"]["bgr"] == 0.0
    # Mode yang dipilih ikut dibekukan, beserta mode ASAL versinya — supaya
    # nanti masih bisa diketahui bahwa keduanya berbeda dan kenapa.
    assert b["warna"]["mode"] == "warna"
    assert b["warna"].get("asal_versi") == "bentuk"


# ============================================================
# HAPUS HARUS BENAR-BENAR MENGHAPUS
# ============================================================
#
# Bobot YOLO sekitar 6,5 MB per training, dan satu projek bisa punya belasan
# percobaan. Tombol Hapus yang cuma menghilangkan kartunya dari layar berarti
# disk terus terisi oleh berkas yang tidak bisa ditemukan lagi dari mana pun.
#
# Versi sebelumnya menyebut berkas yang dihapus satu per satu, dan ketika
# evaluasi produksi ditambahkan belakangan, L<n>.eval.json serta L<n>.eval.log
# tertinggal sebagai berkas yatim.

def test_hapus_menyapu_seluruh_berkas_training(tmp_path):
    n = 3
    d = latih.dir_latih(tmp_path, n)
    (d / "weights").mkdir(parents=True)
    (d / "weights" / "best.pt").write_bytes(b"x" * 4096)
    (d / "weights" / "last.pt").write_bytes(b"x" * 4096)
    (d / "results.csv").write_text("epoch\n1\n")
    (d / "results.png").write_bytes(b"png")
    latih._tulis(tmp_path, n, {"nomor": n, "keadaan": "selesai", "pid": 0})
    akar = latih._dir(tmp_path)
    for nama in (f"L{n}.log", f"L{n}.eval.json", f"L{n}.eval.log",
                 f"L{n}.json.tmp"):
        (akar / nama).write_text("x")

    sebelum = sum(p.stat().st_size for p in akar.rglob("*") if p.is_file())
    assert sebelum > 8000, "berkas ujinya sendiri tidak terbentuk"

    assert latih.buang(tmp_path, n) is True

    sisa = sorted(p.name for p in akar.rglob("*") if p.is_file())
    assert sisa == [], f"berkas tertinggal setelah dihapus: {sisa}"
    assert not d.exists(), "folder trainingnya masih ada"


def test_hapus_tidak_menyentuh_training_lain(tmp_path):
    """Pola L<n>.* tidak boleh mengenai L1 saat menghapus L11."""
    for n in (1, 11):
        d = latih.dir_latih(tmp_path, n)
        (d / "weights").mkdir(parents=True)
        (d / "weights" / "best.pt").write_bytes(b"x")
        latih._tulis(tmp_path, n, {"nomor": n, "keadaan": "selesai", "pid": 0})
        (latih._dir(tmp_path) / f"L{n}.eval.json").write_text("{}")

    latih.buang(tmp_path, 1)
    assert latih.baca(tmp_path, 11) is not None, "L11 ikut terhapus saat L1 dihapus"
    assert (latih.dir_latih(tmp_path, 11) / "weights" / "best.pt").exists()
    assert (latih._dir(tmp_path) / "L11.eval.json").exists()
    assert latih.baca(tmp_path, 1) is None


def test_training_yang_masih_berjalan_tidak_bisa_dihapus(tmp_path, monkeypatch):
    """Menghapus bobot di bawah proses yang sedang menulisnya."""
    latih._tulis(tmp_path, 5, {"nomor": 5, "keadaan": "jalan", "pid": 424242})
    monkeypatch.setattr(latih, "hidup", lambda pid: True)
    with pytest.raises(ValueError, match="masih berjalan"):
        latih.buang(tmp_path, 5)
    assert latih.baca(tmp_path, 5) is not None
