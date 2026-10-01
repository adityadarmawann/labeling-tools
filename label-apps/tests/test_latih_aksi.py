"""
Uji lapisan pelatihan CLASSIFIER AKSI (Langkah 9).

Yang dijaga di sini BUKAN trainingnya (butuh GPU + menit, diuji terpisah di
server sungguhan), melainkan PLUMBING-nya — bagian yang kalau bergeser diam-diam
membuat training aksi jatuh tanpa jejak atau, lebih buruk, ikut mematikan
training gambar yang berbagi kartu:

  1. Registri membekukan kelas + backend, dan daur hidupnya (siapkan/daftar/
     status/buang) berperilaku seperti jalur gambar.
  2. siap_latih_aksi() JUJUR per backend — form hanya menawarkan yang pustakanya
     benar-benar ada, dan rute menolak backend yang kurang dengan pesan jelas.
  3. Subproses diluncurkan dengan argv yang benar + start_new_session, mulai-ganda
     ditolak, dan KUNCI GPU-nya TERPISAH dari jalur gambar (lajur sendiri).
  4. Seluruh aplikasi — termasuk trainer per backend — bisa diimpor TANPA torch.
  5. Gerbang rute: bukan-anggota / bukan-pemilik-Editor / bukan-video / bukan-aksi
     ditolak; memulai menuntut versi aksi yang SUDAH terbangun.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import shutil
import sys

import pytest

from app.services import latih_aksi, versi
from conftest import PW_ANGGI, PW_PAUL, klien_baru, masuk


# ============================================================
# pembantu: versi klip aksi + registri di disk (tanpa ffmpeg/torch)
# ============================================================

def _pasang_versi_aksi(proj, nomor=1, kelas=("shoot", "pass", "diam")):
    """Versi beku .versi/vN/ seperti keluaran klip_olah: train/ + valid/ folder
    per kelas (mp4 tiruan cukup — tes ini tak memutar), aksi.yaml + MANIFES.json
    ber-jenis 'aksi' (sumber kelas yang dibekukan siapkan)."""
    vd = proj / ".versi" / f"v{nomor}"
    for c in kelas:
        (vd / "train" / c).mkdir(parents=True, exist_ok=True)
        (vd / "train" / c / f"{c}_vA_0001.mp4").write_bytes(b"x")
        (vd / "valid" / c).mkdir(parents=True, exist_ok=True)
        (vd / "valid" / c / f"{c}_vB_0001.mp4").write_bytes(b"x")
    (vd / "aksi.yaml").write_text(
        "train: train\nval: valid\n"
        f"nc: {len(kelas)}\n"
        "names: [" + ", ".join(f"'{c}'" for c in kelas) + "]\nnegatif: ~\n",
        encoding="utf-8")
    (vd / "MANIFES.json").write_text(json.dumps({
        "versi": nomor, "jenis": "aksi",
        "aksi": {"kelas": list(kelas), "merge": {}, "negatif": ""},
        "berkas": [],
    }, ensure_ascii=False), encoding="utf-8")
    return vd


def _daftar_versi_aksi(proj, nomor=1, kelas=("shoot", "pass", "diam")):
    versi.buat(proj, "paul", "80:20", [], {},
               {"split": {"train": len(kelas), "valid": len(kelas)},
                "kelas": len(kelas)}, nomor=nomor,
               hasil={"jenis": "aksi",
                      "jumlah": {"train": len(kelas), "valid": len(kelas)},
                      "kelas": len(kelas), "n": 2 * len(kelas)})


# ============================================================
# 1. REGISTRI — daur hidup (pure service, tmp_path)
# ============================================================

def test_siapkan_membekukan_kelas_dan_backend(tmp_path):
    _pasang_versi_aksi(tmp_path, 1, ("shoot", "pass", "diam"))
    isi = latih_aksi.siapkan(tmp_path, versi_nomor=1, backend="videomae",
                             par={"epochs": 5, "batch": 4}, oleh="uji")
    assert isi["backend"] == "videomae"
    assert isi["kelas"] == ["shoot", "pass", "diam"]
    assert isi["par"]["epochs"] == 5 and isi["par"]["batch"] == 4
    assert isi["keadaan"] == "antre"
    # Berkas di disk berawalan A (hidup berdampingan dengan L* gambar).
    assert latih_aksi.berkas_latih(tmp_path, isi["nomor"]).name \
        == f"A{isi['nomor']}.json"
    # BEKU: hapus versinya, kelas tetap terbaca dari rekaman training.
    shutil.rmtree(tmp_path / ".versi" / "v1")
    assert latih_aksi.baca(tmp_path, isi["nomor"])["kelas"] == \
        ["shoot", "pass", "diam"]


def test_siapkan_tolak_backend_asing(tmp_path):
    _pasang_versi_aksi(tmp_path, 1)
    with pytest.raises(ValueError):
        latih_aksi.siapkan(tmp_path, versi_nomor=1, backend="transformer-ajaib",
                           par={}, oleh="uji")


def test_penomoran_A_naik_dan_terpisah_dari_L(tmp_path):
    assert latih_aksi.nomor_berikut(tmp_path) == 1
    _pasang_versi_aksi(tmp_path, 1)
    a = latih_aksi.siapkan(tmp_path, versi_nomor=1, backend="posec3d",
                           par={}, oleh="uji")
    assert a["nomor"] == 1
    assert latih_aksi.nomor_berikut(tmp_path) == 2
    # Training GAMBAR L1 di folder .latih yang sama tak mengganggu penomoran A.
    from app.services import latih
    latih._tulis(tmp_path, 1, {"nomor": 1, "nama": "gambar"})
    assert latih_aksi.nomor_berikut(tmp_path) == 2, "L* tak boleh menggeser A*"
    assert latih.nomor_berikut(tmp_path) == 2


def test_saring_par_menjepit_dan_bawaan(tmp_path):
    par, galat = latih_aksi._saring_par("videomae", {})
    assert not galat and par == latih_aksi.BACKEND["videomae"]["par"]
    par, galat = latih_aksi._saring_par("videomae", {"epochs": 10})
    assert not galat and par["epochs"] == 10
    _, galat = latih_aksi._saring_par("videomae", {"epochs": 0})
    assert galat and "epochs" in galat[0]
    _, galat = latih_aksi._saring_par("videomae", {"batch": 999})
    assert galat and "batch" in galat[0]


def test_daftar_dan_status_transisi(tmp_path, monkeypatch):
    _pasang_versi_aksi(tmp_path, 1)
    isi = latih_aksi.siapkan(tmp_path, versi_nomor=1, backend="slowfast",
                             par={"epochs": 3}, oleh="uji")
    n = isi["nomor"]
    # Tandai 'jalan' dengan pid hidup palsu.
    latih_aksi.perbarui(tmp_path, n, keadaan="jalan", pid=424242)
    monkeypatch.setattr(latih_aksi, "hidup", lambda pid: True)
    d = latih_aksi.daftar(tmp_path)
    assert len(d) == 1 and d[0]["keadaan"] == "jalan"
    assert d[0]["backend_nama"] == "SlowFast-R50"

    # Proses lenyap tanpa best.pt -> 'hilang'; dengan best.pt -> 'selesai'.
    monkeypatch.setattr(latih_aksi, "hidup", lambda pid: False)
    assert latih_aksi.status(tmp_path, n)["keadaan"] == "hilang"
    latih_aksi.perbarui(tmp_path, n, keadaan="jalan", pid=424242)
    w = latih_aksi.dir_latih(tmp_path, n) / "weights"
    w.mkdir(parents=True, exist_ok=True)
    (w / "best.pt").write_bytes(b"x")
    assert latih_aksi.status(tmp_path, n)["keadaan"] == "selesai"
    assert latih_aksi.status(tmp_path, n)["punya_bobot"] is True


def test_buang_menyapu_berkas_tanpa_menyentuh_L(tmp_path):
    _pasang_versi_aksi(tmp_path, 1)
    a = latih_aksi.siapkan(tmp_path, versi_nomor=1, backend="posec3d",
                           par={}, oleh="uji")
    n = a["nomor"]
    w = latih_aksi.dir_latih(tmp_path, n) / "weights"
    w.mkdir(parents=True)
    (w / "best.pt").write_bytes(b"x")
    (latih_aksi._dir(tmp_path) / f"A{n}.log").write_text("log")
    # Training GAMBAR L1 harus selamat saat A dihapus.
    from app.services import latih
    latih._tulis(tmp_path, 1, {"nomor": 1})

    assert latih_aksi.buang(tmp_path, n) is True
    assert latih_aksi.baca(tmp_path, n) is None
    assert not latih_aksi.dir_latih(tmp_path, n).exists()
    assert not (latih_aksi._dir(tmp_path) / f"A{n}.log").exists()
    assert latih.baca(tmp_path, 1) is not None, "L1 ikut terhapus saat A dihapus"


def test_buang_tolak_yang_masih_jalan(tmp_path, monkeypatch):
    _pasang_versi_aksi(tmp_path, 1)
    a = latih_aksi.siapkan(tmp_path, versi_nomor=1, backend="videomae",
                           par={}, oleh="uji")
    latih_aksi.perbarui(tmp_path, a["nomor"], keadaan="jalan", pid=99)
    monkeypatch.setattr(latih_aksi, "hidup", lambda pid: True)
    with pytest.raises(ValueError, match="masih berjalan"):
        latih_aksi.buang(tmp_path, a["nomor"])


def test_kelas_versi_aksi_dari_manifes_lalu_yaml(tmp_path):
    vd = _pasang_versi_aksi(tmp_path, 2, ("a", "b"))
    assert latih_aksi.kelas_versi_aksi(tmp_path, 2) == ["a", "b"]
    # Tanpa MANIFES, jatuh ke aksi.yaml names.
    (vd / "MANIFES.json").unlink()
    assert latih_aksi.kelas_versi_aksi(tmp_path, 2) == ["a", "b"]
    assert latih_aksi.kelas_versi_aksi(tmp_path, 99) == []


# ============================================================
# 2. KESIAPAN BACKEND — JUJUR di kedua venv
# ============================================================

def test_siap_latih_aksi_jujur_per_backend():
    s = latih_aksi.siap_latih_aksi()
    assert set(s) == {"videomae", "slowfast", "posec3d"}
    for key, info in latih_aksi.BACKEND.items():
        libs_ada = all(importlib.util.find_spec(p) is not None
                       for p in info["butuh"])
        assert s[key]["siap"] == libs_ada, (key, s[key])
        if not s[key]["siap"]:
            # Alasan yang cuma "tidak bisa" tak menolong — ia menyebut jalannya.
            assert "belum terpasang" in s[key]["alasan"]
            assert s[key]["kurang"], key


def test_posec3d_tak_menuntut_mm_deps():
    """PoseC3D standalone — pustaka wajibnya hanya torch + ultralytics, TANPA
    mmcv/mmaction2/mmpose (rencana G#1)."""
    butuh = latih_aksi.BACKEND["posec3d"]["butuh"]
    assert set(butuh) == {"torch", "ultralytics"}
    assert not any("mm" in b for b in butuh)


# ============================================================
# 3. PELUNCURAN SUBPROSES + KUNCI TERPISAH
# ============================================================

def test_jalankan_meluncurkan_subproses_benar(tmp_path, monkeypatch):
    _pasang_versi_aksi(tmp_path, 1)
    isi = latih_aksi.siapkan(tmp_path, versi_nomor=1, backend="posec3d",
                             par={}, oleh="uji")
    tangkap = {}

    class FakeP:
        pid = 4242

    def fake_popen(cmd, **kw):
        tangkap["cmd"] = cmd
        tangkap["kw"] = kw
        return FakeP()

    monkeypatch.setattr(latih_aksi.subprocess, "Popen", fake_popen)
    latih_aksi.jalankan(tmp_path, isi["nomor"])

    assert tangkap["cmd"][1:3] == ["-m", "app.services.latih_aksi_jalan"]
    assert tangkap["cmd"][3] == str(tmp_path.resolve())
    assert tangkap["cmd"][4] == str(isi["nomor"])
    assert tangkap["kw"].get("start_new_session") is True, \
        "subproses harus sesi sendiri supaya tak ikut mati saat server restart"
    assert latih_aksi.baca(tmp_path, isi["nomor"])["pid"] == 4242


def test_kunci_gpu_aksi_terpisah_dari_gambar():
    """Lajur GPU sendiri: training aksi tak boleh mengantre di kunci yang sama
    dengan training gambar, supaya keduanya tak saling mematikan (rencana G#9)."""
    from app.services import latih_aksi_jalan, latih_jalan
    assert str(latih_aksi_jalan.KUNCI_GPU) != str(latih_jalan.KUNCI_GPU)
    assert "aksi" in latih_aksi_jalan.KUNCI_GPU.name


def test_runner_punya_penjaga_vram():
    """Penjaga VRAM wajib ada (port periksa_vram) — melindungi training lain."""
    from app.services import latih_aksi_jalan
    assert hasattr(latih_aksi_jalan, "periksa_vram")
    assert latih_aksi_jalan.MIN_VRAM_BEBAS_MB > 0


# ============================================================
# 4. IMPOR TANPA TORCH
# ============================================================

def test_seluruh_modul_aksi_impor_tanpa_torch():
    """Trainer per backend WAJIB import-guard bersih: impor berat (torch/
    transformers/pytorchvideo/av/ultralytics) ada di dalam fungsi, bukan di
    tingkat modul — itulah yang membuat server CPU bisa memuat seluruh aplikasi."""
    for m in ("app.services.latih_aksi", "app.services.latih_aksi_jalan",
              "app.services.latih_aksi_umum", "app.services.latih_aksi_videomae",
              "app.services.latih_aksi_slowfast", "app.services.latih_aksi_posec3d",
              "app.services.posec3d_model"):
        importlib.import_module(m)
    # Mengimpornya tak boleh menarik torch dkk ke dalam proses.
    for berat in ("torch", "transformers", "pytorchvideo", "av"):
        assert berat not in sys.modules, f"{berat} ikut terimpor di tingkat modul"


def test_aplikasi_impor_tanpa_torch():
    from app.main import create_app
    create_app()
    assert "torch" not in sys.modules


# ============================================================
# 5. RUTE — gerbang + kesiapan
# ============================================================

def _buat_video_aksi(klien, nama, kelas=("shoot", "pass", "diam")):
    r = klien.post(f"/api/projek/baru?nama={nama}&jenis=video")
    assert r.status_code == 200 and r.json()["ok"], r.text[:200]
    r = klien.post(f"/api/tugas/aksi?ds={nama}",
                   json={"aksi": {"kelas": list(kelas)}})
    assert r.json()["ok"], r.text[:200]


@pytest.fixture
def backend_dipaksa_siap(monkeypatch):
    """Anggap satu backend (videomae) siap, apa pun venv-nya.

    Rute memeriksa kesiapan backend LEBIH DULU daripada versinya — perilaku yang
    benar. Tetapi tes di bawah menguji gerbang VERSI/mulai-ganda, bukan kesiapan,
    dan harus memberi jawaban sama di venv CPU (tanpa pustaka) maupun GPU."""
    asli = latih_aksi.siap_latih_aksi

    def palsu():
        s = asli()
        s["videomae"] = {**s["videomae"], "siap": True, "alasan": "",
                         "kurang": []}
        return s

    monkeypatch.setattr(latih_aksi, "siap_latih_aksi", palsu)


def test_mulai_tolak_bukan_anggota(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    r = anggi.post("/api/aksi/latih/mulai?ds=paul/aksi",
                   json={"versi": 1, "backend": "videomae"}).json()
    assert r["ok"] is False


def test_mulai_tolak_pelabel_bukan_editor(klien, aplikasi, lingkungan):
    """boleh_unggah = pemilik/Editor. Pelabel tak berhak melatih."""
    from app.services import tugas

    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    tugas.undang(proj, "paul", "anggi", peran="pelabel", akses="semua")
    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    r = anggi.post("/api/aksi/latih/mulai?ds=paul/aksi",
                   json={"versi": 1, "backend": "videomae"}).json()
    assert r["ok"] is False
    assert "berhak" in r["error"] or "anggota" in r["error"]


def test_mulai_tolak_projek_bukan_video(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    r = klien.post("/api/projek/baru?nama=gbr&jenis=image")
    assert r.json()["ok"]
    r = klien.post("/api/aksi/latih/mulai?ds=gbr",
                   json={"versi": 1, "backend": "videomae"}).json()
    assert r["ok"] is False
    assert "video" in r["error"]


def test_mulai_tolak_video_tanpa_kelas_aksi(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    r = klien.post("/api/projek/baru?nama=vid&jenis=video")
    assert r.json()["ok"]
    r = klien.post("/api/aksi/latih/mulai?ds=vid",
                   json={"versi": 1, "backend": "videomae"}).json()
    assert r["ok"] is False
    assert "kelas aksi" in r["error"]


def test_mulai_tolak_backend_pustaka_kurang(klien, lingkungan):
    """Backend yang pustakanya tak ada ditolak dengan pesan jelas — SEBELUM
    registri dibuat (folder .latih tak terisi training yang tak pernah jalan)."""
    siap = latih_aksi.siap_latih_aksi()
    kurang = [k for k, v in siap.items() if not v["siap"]]
    if not kurang:
        pytest.skip("semua backend terpasang di venv ini")
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    r = klien.post("/api/aksi/latih/mulai?ds=aksi",
                   json={"versi": 1, "backend": kurang[0]}).json()
    assert r["ok"] is False
    assert "belum terpasang" in r["error"]
    assert latih_aksi.nomor_berikut(proj) == 1, "registri terisi padahal ditolak"


def test_mulai_tolak_backend_asing(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    r = klien.post("/api/aksi/latih/mulai?ds=aksi",
                   json={"versi": 1, "backend": "ajaib"}).json()
    assert r["ok"] is False
    assert "backend" in r["error"]


def test_mulai_butuh_versi_terbangun(klien, lingkungan, backend_dipaksa_siap):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    # Belum ada versi -> ditolak jelas.
    r = klien.post("/api/aksi/latih/mulai?ds=aksi",
                   json={"versi": 1, "backend": "videomae"}).json()
    assert r["ok"] is False
    assert "terbangun" in r["error"] or "bukan dataset" in r["error"]


def test_mulai_sukses_lalu_tolak_mulai_ganda(klien, lingkungan,
                                             backend_dipaksa_siap, monkeypatch):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    _pasang_versi_aksi(proj, 1)
    _daftar_versi_aksi(proj, 1)

    # Jangan benar-benar meluncurkan subproses: catat saja.
    diluncurkan = []
    monkeypatch.setattr(latih_aksi, "jalankan",
                        lambda ds, n: diluncurkan.append(n)
                        or latih_aksi.perbarui(ds, n, keadaan="jalan", pid=123))
    monkeypatch.setattr(latih_aksi, "hidup", lambda pid: True)

    r = klien.post("/api/aksi/latih/mulai?ds=aksi",
                   json={"versi": 1, "backend": "videomae",
                         "par": {"epochs": 2}}).json()
    assert r["ok"] is True, r
    assert diluncurkan == [r["nomor"]]
    # Kelas dibekukan dari aksi.yaml versinya.
    rek = latih_aksi.baca(proj, r["nomor"])
    assert rek["kelas"] == ["shoot", "pass", "diam"]
    assert rek["par"]["epochs"] == 2

    # Mulai lagi selagi yang pertama berjalan -> ditolak (satu lajur GPU).
    r2 = klien.post("/api/aksi/latih/mulai?ds=aksi",
                    json={"versi": 1, "backend": "videomae"}).json()
    assert r2["ok"] is False
    assert "berjalan" in r2["error"]


def test_daftar_dan_bahan_rute(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    r = klien.get("/api/aksi/latih/daftar?ds=aksi").json()
    assert r["ok"] is True and r["daftar"] == [] and "statistik" in r
    b = klien.get("/api/aksi/latih/bahan?ds=aksi").json()
    assert b["ok"] is True
    assert set(b["backend"]) == {"videomae", "slowfast", "posec3d"}
    assert b["versi"] == []            # belum ada versi aksi
    assert b["batas"]["epochs"] == [1, 1000]


def test_bobot_rute_hanya_best_atau_last(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    assert klien.get("/aksi/latih/bobot?ds=aksi&nomor=1&jenis=ngawur") \
        .status_code == 400
    # best/last yang belum ada -> 404, bukan 500.
    assert klien.get("/aksi/latih/bobot?ds=aksi&nomor=1&jenis=best") \
        .status_code == 404


def test_bobot_rute_tolak_bukan_anggota(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    r = anggi.get("/aksi/latih/bobot?ds=paul/aksi&nomor=1&jenis=best")
    assert r.status_code in (403, 404)
