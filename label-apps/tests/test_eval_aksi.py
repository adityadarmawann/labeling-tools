"""
Uji EVALUASI classifier aksi (Langkah 10).

Yang dijaga di sini bukan prediksi GPU-nya (ditangguhkan — kartu sibuk; butuh
torch), melainkan DUA hal yang menjadi seluruh inti langkah ini:

  1. MATEMATIKA PENGUKURAN benar pada contoh yang bisa dihitung tangan.
  2. PENJAGA KEWARASAN bekerja — "alat ukur harus diuji pada model buruk"
     (MEMORY). Alat ukur yang melaporkan model rusak sebagai "baik" lebih
     berbahaya daripada tidak ada alat ukur: ia memberi izin mengirim model yang
     pasti gagal. Maka diuji eksplisit: model degenerat (satu kelas untuk semua)
     TIDAK boleh ~100%, kelas tanpa dukungan TIDAK boleh dihitung sempurna (0/0),
     masukan kosong TIDAK boleh ZeroDivision maupun "100%".

Plus plumbing: skema status, peluncuran subproses (di-stub), gerbang rute, dan
bahwa aplikasi + modul eval impor BERSIH tanpa torch/matplotlib.

Semua murni numpy — TIDAK ada matplotlib/torch/sklearn/jaringan.
"""
from __future__ import annotations

import importlib
import importlib.util

import pytest

from app.services import eval_aksi, latih_aksi
from conftest import (PW_ANGGI, PW_PAUL, klien_baru, masuk,
                      pustaka_berat_saat_impor)


# ============================================================
# 1. hitung_metrik — KEBENARAN pada contoh terhitung-tangan
# ============================================================

def test_metrik_contoh_kecil_benar():
    """Contoh 3 kelas seimbang (3 klip/kelas) yang bisa dihitung tangan:

        A -> A,A,A      baris A [3,0,0]
        B -> A,A,B      baris B [2,1,0]
        C -> A,C,C      baris C [1,0,2]

    akurasi = (3+1+2)/9 ; recall A=1, B=1/3, C=2/3 ; rata-kelas = 2/3.
    Pasangan tertukar teratas: B->A (2), lalu C->A (1).
    """
    kelas = ["A", "B", "C"]
    y_true = ["A", "A", "A", "B", "B", "B", "C", "C", "C"]
    y_pred = ["A", "A", "A", "A", "A", "B", "A", "C", "C"]
    m = eval_aksi.hitung_metrik(y_true, y_pred, kelas)

    assert m["n"] == 9
    assert m["akurasi"] == pytest.approx(6 / 9, abs=1e-3)
    assert m["matriks"] == [[3, 0, 0], [2, 1, 0], [1, 0, 2]]
    assert m["per_kelas"]["A"]["recall"] == pytest.approx(1.0)
    assert m["per_kelas"]["B"]["recall"] == pytest.approx(1 / 3)
    assert m["per_kelas"]["C"]["recall"] == pytest.approx(2 / 3)
    assert m["per_kelas"]["B"]["support"] == 3
    assert m["akurasi_rerata_kelas"] == pytest.approx((1 + 1 / 3 + 2 / 3) / 3,
                                                      abs=1e-3)
    # Pasangan paling sering tertukar — menuntun perbaikan berikutnya.
    assert m["pasangan_bingung"][0]["dari"] == "B"
    assert m["pasangan_bingung"][0]["ke"] == "A"
    assert m["pasangan_bingung"][0]["jml"] == 2
    assert m["pasangan_bingung"][1]["dari"] == "C"
    assert m["pasangan_bingung"][1]["ke"] == "A"
    assert m["pasangan_bingung"][1]["jml"] == 1
    assert m["pasangan_bingung"][1]["porsi"] == pytest.approx(1 / 3, abs=1e-3)
    assert not m["degenerate"]
    assert not m["kelas_tanpa_dukungan"]


def test_metrik_prediktor_sempurna_1():
    kelas = ["A", "B", "C"]
    y = ["A", "A", "B", "B", "C", "C"]
    m = eval_aksi.hitung_metrik(y, list(y), kelas)
    assert m["akurasi"] == 1.0
    assert m["akurasi_rerata_kelas"] == 1.0
    assert m["tingkat"] == "baik"
    assert not m["degenerate"]


# ============================================================
# 2. PENJAGA KEWARASAN — inti Langkah 10 (model buruk ≠ "baik")
# ============================================================

def test_degenerat_satu_kelas_tak_pernah_bagus():
    """Model yang menebak satu kelas untuk SEMUA klip pada set seimbang 3-kelas
    harus memberi akurasi ~1/3 DAN rata-kelas ~1/3 — BUKAN 100%. Ini kasus v13
    (MEMORY): alat ukur yang bilang model begini "baik" memberi izin palsu."""
    kelas = ["A", "B", "C"]
    y_true = ["A", "A", "A", "A", "B", "B", "B", "B", "C", "C", "C", "C"]
    y_pred = ["A"] * 12
    m = eval_aksi.hitung_metrik(y_true, y_pred, kelas)

    assert m["akurasi"] == pytest.approx(1 / 3, abs=1e-3)
    assert m["akurasi_rerata_kelas"] == pytest.approx(1 / 3, abs=1e-3)
    assert m["degenerate"] is True
    assert m["tingkat"] == "buruk"
    # Recall kelas yang TAK PERNAH ditebak tetap dihitung (0,0), bukan dibuang —
    # itulah yang menjaga rata-kelas tetap jujur.
    assert m["per_kelas"]["B"]["recall"] == 0.0
    assert m["per_kelas"]["C"]["recall"] == 0.0
    assert m["per_kelas"]["B"]["support"] == 4


def test_kelas_tanpa_dukungan_tak_dihitung_sempurna():
    """Kelas D tanpa satu pun klip valid (support 0) = 0/0 TIDAK terdefinisi.
    Dikeluarkan dari rata-kelas, TIDAK dihitung 1,0 (yang akan mengerek skor).

        A,B benar; C setengah benar; D tak ada sama sekali.
        rata-kelas yang benar = mean(1, 1, 0.5) = 0.8333 (atas A,B,C saja).
        Kalau D keliru dihitung 100%: mean(1,1,0.5,1)=0.875 — MENGEMBUNG.
    """
    kelas = ["A", "B", "C", "D"]
    y_true = ["A", "A", "B", "B", "C", "C"]
    y_pred = ["A", "A", "B", "B", "C", "A"]
    m = eval_aksi.hitung_metrik(y_true, y_pred, kelas)

    assert m["per_kelas"]["D"]["recall"] is None
    assert m["per_kelas"]["D"]["support"] == 0
    assert "D" in m["kelas_tanpa_dukungan"]
    assert m["akurasi_rerata_kelas"] == pytest.approx((1 + 1 + 0.5) / 3, abs=1e-3)
    # Bukti negatif: TIDAK sama dengan angka yang mengembung karena D=100%.
    assert m["akurasi_rerata_kelas"] != pytest.approx((1 + 1 + 0.5 + 1) / 4,
                                                      abs=1e-3)


def test_masukan_kosong_tak_meledak_tak_100():
    """Masukan kosong: tak boleh ZeroDivision, tak boleh melaporkan 100%."""
    m = eval_aksi.hitung_metrik([], [], ["A", "B"])
    assert m["n"] == 0
    assert m["akurasi"] is None          # BUKAN 1.0 / 100%
    assert m["akurasi_rerata_kelas"] is None
    assert m["tingkat"] == "kosong"
    assert m["matriks"] == [[0, 0], [0, 0]]
    # juga: daftar kelas kosong tak meledak.
    assert eval_aksi.hitung_metrik([], [], [])["akurasi"] is None


def test_satu_kelas_dominan_akurasi_tinggi_tetap_buruk():
    """Akurasi keseluruhan bisa tinggi saat satu kelas mendominasi, padahal
    model tak mengenali kelas minoritas sama sekali. Rata-kelas yang menangkap
    itu — dan vonisnya tak boleh "baik"."""
    kelas = ["mayor", "minor"]
    # 9 mayor (semua benar) + 1 minor (salah ke mayor). Akurasi 9/10 = 90%.
    y_true = ["mayor"] * 9 + ["minor"]
    y_pred = ["mayor"] * 10
    m = eval_aksi.hitung_metrik(y_true, y_pred, kelas)
    assert m["akurasi"] == pytest.approx(0.9)
    # tapi rata-kelas = (1 + 0)/2 = 0.5, dan model degenerat -> buruk.
    assert m["akurasi_rerata_kelas"] == pytest.approx(0.5)
    assert m["tingkat"] == "buruk"


# ============================================================
# 3. matriks_png — matplotlib lazy, melempar bersih kalau tak ada
# ============================================================

def test_png_melempar_bersih_tanpa_matplotlib():
    """Suite CPU TIDAK memanggil matriks_png untuk menggambar — hanya memastikan
    ia melempar dengan pesan jelas kalau matplotlib absen (venv CPU)."""
    ada = importlib.util.find_spec("matplotlib") is not None
    if ada:
        pytest.skip("matplotlib ada di venv ini — jalur gambar diuji di GPU")
    with pytest.raises(RuntimeError, match="matplotlib"):
        eval_aksi.matriks_png([[1, 0], [0, 1]], ["A", "B"], None)


def test_modul_eval_impor_tanpa_matplotlib_atau_torch():
    """Modul eval + aplikasi WAJIB impor tanpa menarik matplotlib/torch ke
    proses server (impor berat ada DI DALAM fungsi).

    Diukur di proses bersih (lihat pustaka_berat_saat_impor): sys.modules proses
    tes ini bisa sudah tercemar torch/matplotlib oleh tes lain di venv GPU.
    """
    tercemar = pustaka_berat_saat_impor(
        ["app.services.eval_aksi", "app.services.eval_aksi_jalan"],
        ["matplotlib", "torch", "transformers", "pytorchvideo"],
        buat_app=True)
    assert not tercemar, f"{tercemar} ikut terimpor di tingkat modul"


# ============================================================
# 4. PLUMBING — status + peluncuran subproses (di-stub)
# ============================================================

def _pasang_training_selesai(proj, nomor=1, backend="videomae"):
    """Registri training A<n> + best.pt di disk (tanpa torch) — cukup untuk
    menguji gerbang/peluncuran eval tanpa benar-benar melatih/mengevaluasi."""
    isi = {"nomor": nomor, "nama": "uji", "versi": 1, "backend": backend,
           "kelas": ["A", "B"], "par": {"epochs": 1}, "oleh": "uji",
           "keadaan": "selesai"}
    latih_aksi._tulis(proj, nomor, isi)
    w = latih_aksi.dir_latih(proj, nomor) / "weights"
    w.mkdir(parents=True, exist_ok=True)
    (w / "best.pt").write_bytes(b"x")
    return isi


def test_jalankan_meluncurkan_subproses_benar(tmp_path, monkeypatch):
    _pasang_training_selesai(tmp_path, 1)
    tangkap = {}

    class FakeP:
        pid = 7777

    def fake_popen(cmd, **kw):
        tangkap["cmd"] = cmd
        tangkap["kw"] = kw
        return FakeP()

    monkeypatch.setattr(eval_aksi.subprocess, "Popen", fake_popen)
    out = eval_aksi.jalankan(tmp_path, 1)

    assert tangkap["cmd"][1:3] == ["-m", "app.services.eval_aksi_jalan"]
    assert tangkap["cmd"][3] == str(tmp_path.resolve())
    assert tangkap["cmd"][4] == "1"
    assert tangkap["kw"].get("start_new_session") is True
    assert out["pid"] == 7777
    # Status di disk menandai antre dengan pid yang benar.
    h = eval_aksi.baca_hasil(tmp_path, 1)
    assert h["keadaan"] == "antre" and h["pid"] == 7777


def test_jalankan_tolak_tanpa_best(tmp_path):
    """Training tanpa best.pt (belum selesai) tak bisa dievaluasi — ditolak
    jelas, bukan subproses yang jatuh di baris muat model."""
    latih_aksi._tulis(tmp_path, 1, {"nomor": 1, "versi": 1,
                                    "backend": "videomae", "keadaan": "jalan"})
    with pytest.raises(ValueError, match="best.pt"):
        eval_aksi.jalankan(tmp_path, 1)


def test_jalankan_tolak_mulai_ganda(tmp_path, monkeypatch):
    _pasang_training_selesai(tmp_path, 1)
    # Tandai eval sedang berjalan dengan pid hidup palsu.
    eval_aksi.tulis_hasil(tmp_path, 1, {"keadaan": "jalan", "pid": 123})
    monkeypatch.setattr(eval_aksi, "hidup", lambda pid: True)
    with pytest.raises(ValueError, match="masih berjalan"):
        eval_aksi.jalankan(tmp_path, 1)


def test_status_jalan_tanpa_proses_jadi_hilang(tmp_path, monkeypatch):
    _pasang_training_selesai(tmp_path, 1)
    eval_aksi.tulis_hasil(tmp_path, 1, {"keadaan": "jalan", "pid": 424242})
    monkeypatch.setattr(eval_aksi, "hidup", lambda pid: False)
    s = eval_aksi.status(tmp_path, 1)
    assert s["keadaan"] == "hilang"
    # status untuk nomor tanpa hasil eval -> None.
    assert eval_aksi.status(tmp_path, 99) is None


def test_metrik_json_ditulis_dan_terbaca(tmp_path):
    """tulis_hasil/baca_hasil bolak-balik utuh (status dari disk, survive
    restart)."""
    eval_aksi.tulis_hasil(tmp_path, 3, {"keadaan": "selesai",
                                        "metrik": {"akurasi": 0.5}})
    h = eval_aksi.baca_hasil(tmp_path, 3)
    assert h["keadaan"] == "selesai" and h["metrik"]["akurasi"] == 0.5
    assert eval_aksi.berkas_hasil(tmp_path, 3).name == "A3.eval.json"


# ============================================================
# 5. RUTE — gerbang
# ============================================================

def _buat_video_aksi(klien, nama, kelas=("A", "B")):
    r = klien.post(f"/api/projek/baru?nama={nama}&jenis=video")
    assert r.status_code == 200 and r.json()["ok"], r.text[:200]
    r = klien.post(f"/api/tugas/aksi?ds={nama}",
                   json={"aksi": {"kelas": list(kelas)}})
    assert r.json()["ok"], r.text[:200]


def test_eval_mulai_tolak_bukan_anggota(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    r = anggi.post("/api/aksi/eval/mulai?ds=paul/aksi&nomor=1").json()
    assert r["ok"] is False


def test_eval_mulai_tolak_pelabel_bukan_editor(klien, aplikasi, lingkungan):
    """Eval memakai GPU — hak sama dengan memulai training (boleh_unggah)."""
    from app.services import tugas

    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    tugas.undang(proj, "paul", "anggi", peran="pelabel", akses="semua")
    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    r = anggi.post("/api/aksi/eval/mulai?ds=paul/aksi&nomor=1").json()
    assert r["ok"] is False


def test_eval_mulai_tolak_projek_bukan_video(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    r = klien.post("/api/projek/baru?nama=gbr&jenis=image")
    assert r.json()["ok"]
    r = klien.post("/api/aksi/eval/mulai?ds=gbr&nomor=1").json()
    assert r["ok"] is False
    assert "video" in r["error"]


def test_eval_mulai_tolak_training_tak_ada(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    r = klien.post("/api/aksi/eval/mulai?ds=aksi&nomor=1").json()
    assert r["ok"] is False
    assert "tidak ada" in r["error"]


def test_eval_mulai_tolak_training_belum_selesai(klien, lingkungan):
    """Training tanpa best.pt ditolak — tak ada yang bisa diukur."""
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    latih_aksi._tulis(proj, 1, {"nomor": 1, "versi": 1, "backend": "videomae",
                                "keadaan": "jalan"})
    r = klien.post("/api/aksi/eval/mulai?ds=aksi&nomor=1").json()
    assert r["ok"] is False
    assert "best.pt" in r["error"]


def test_eval_mulai_sukses_subproses_distub(klien, lingkungan, monkeypatch):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    _pasang_training_selesai(proj, 1)
    diluncurkan = []
    monkeypatch.setattr(eval_aksi, "jalankan",
                        lambda ds, n: diluncurkan.append(n) or {"pid": 1})
    r = klien.post("/api/aksi/eval/mulai?ds=aksi&nomor=1").json()
    assert r["ok"] is True and r["nomor"] == 1
    assert diluncurkan == [1]


def test_eval_gambar_404_sebelum_ada(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    r = klien.get("/aksi/eval/gambar?ds=aksi&nomor=1")
    assert r.status_code == 404


def test_eval_gambar_tolak_bukan_anggota(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    r = anggi.get("/aksi/eval/gambar?ds=paul/aksi&nomor=1")
    assert r.status_code == 403


def test_eval_kemajuan_tolak_bukan_aksi(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    r = klien.post("/api/projek/baru?nama=vid&jenis=video")
    assert r.json()["ok"]
    r = klien.get("/api/aksi/eval/kemajuan?ds=vid&nomor=1").json()
    assert r["ok"] is False
    assert "aksi" in r["error"]
