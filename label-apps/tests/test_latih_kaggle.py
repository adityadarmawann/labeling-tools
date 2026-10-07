"""
Backend training Kaggle (app/services/latih_kaggle.py) — diuji TANPA jaringan.

Satu-satunya sentuhan Kaggle adalah _kg(); seluruh tes memalsukannya, jadi
suite tidak pernah memanggil kaggle.com. Yang dijaga: resolusi akun/pool,
pembangkitan skrip kernel (termasuk guard pose), penyerapan output ke .latih/,
ledger kuota + rotasi akun, akuntansi epoch KUMULATIF lintas-leg, dan
percabangan backend di latih.siapkan()/jalankan().
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.services import latih
from app.services import latih_kaggle as k


# ============================================================
# AKUN, KESIAPAN, NAMA (murni)
# ============================================================

def test_akun_tunggal_dari_token_file(tmp_path):
    tf = tmp_path / "tok.txt"
    tf.write_text("KGAT_abc123def456ghi789jkl\nbaris lain diabaikan\n")
    s = NS(kaggle_akun_file=None, kaggle_user="budi", kaggle_token_file=tf)
    pool = k.akun_pool(s)
    assert pool == [{"user": "budi", "token": "KGAT_abc123def456ghi789jkl"}]


def test_pool_json_banyak_akun(tmp_path):
    tok = tmp_path / "t1"; tok.write_text("KGAT_satu1234567890abcdef")
    f = tmp_path / "akun.json"
    f.write_text(json.dumps([
        {"user": "a", "token_file": str(tok)},
        {"user": "b", "token": "KGAT_dua1234567890abcdef"},
        {"user": "", "token": "tanpa-user-dibuang"},
        {"user": "a", "token": "KGAT_duplikat-dibuang"},
    ]))
    s = NS(kaggle_akun_file=f, kaggle_user="", kaggle_token_file=None)
    pool = k.akun_pool(s)
    assert [a["user"] for a in pool] == ["a", "b"], "user kosong & duplikat dibuang"
    assert pool[0]["token"] == "KGAT_satu1234567890abcdef"


def test_siap_mati_tanpa_akun(tmp_path):
    s = NS(kaggle_akun_file=None, kaggle_user="", kaggle_token_file=None)
    ok, alasan = k.siap(s)
    assert ok is False and "akun" in alasan.lower()


def test_nama_dataset_dan_kernel_stabil():
    assert k.nama_dataset("/x/Skle Court", 3) == "higolab-skle-court-v3"
    assert k.nama_kernel("/x/skle", 5, 0) == "higolab-skle-l5-leg0"
    assert k.nama_kernel("/x/skle", 5, 2) == "higolab-skle-l5-leg2"


# ============================================================
# SKRIP KERNEL (murni) — guard pose & penanda resume
# ============================================================

def test_skrip_guard_pose_dan_waktu():
    s = k.skrip_latih("pose", "yolov8n-pose.pt",
                      {"epochs": 100, "imgsz": 640, "mosaic": 0.9, "fliplr": 0.5},
                      run="run-l1", batas_jam=11.0, resume=False)
    assert 'TUGAS = "pose"' in s
    assert 'PAR["flipud"] = 0.0' in s           # flip vertikal dimatikan utk pose
    assert 'PAR["fliplr"] = 0.0' in s           # dijaga flip_idx identitas
    assert 'PAR["mosaic"] = 0.5' in s           # mosaic dijepit <=0.5
    assert "BATAS_DETIK" in s and "trainer.stop = True" in s   # stop wall-clock
    assert "RESUME = False" in s


def test_skrip_resume_cari_checkpoint():
    s = k.skrip_latih("detect", "yolov8n.pt", {"epochs": 50},
                      run="run-l9", batas_jam=11.0, resume=True)
    assert "RESUME = True" in s
    assert "last.pt" in s and "LANJUT_DARI" in s


# ============================================================
# SERAP KELUARAN → .latih/L<n>/ (murni)
# ============================================================

def _tulis_run(out_dir: Path, run: str, rows: int):
    r = out_dir / run
    (r / "weights").mkdir(parents=True)
    hdr = "epoch,time,metrics/mAP50(B)\n"
    body = "".join(f"{i+1},{(i+1)*5.0},0.6\n" for i in range(rows))
    (r / "results.csv").write_text(hdr + body)
    (r / "weights" / "best.pt").write_bytes(b"PK\x03\x04b")
    (r / "weights" / "last.pt").write_bytes(b"PK\x03\x04l")
    (r / "results.png").write_bytes(b"\x89PNG")


def test_serap_meratakan_ke_dir_latih(tmp_path):
    out = tmp_path / "out"; out.mkdir()
    _tulis_run(out, "run-l1", 7)
    dl = tmp_path / "latih-L1"
    ep = k.serap_keluaran(out, "run-l1", dl)
    assert ep == 7
    assert (dl / "results.csv").exists()
    assert (dl / "weights" / "best.pt").exists()
    assert (dl / "weights" / "last.pt").exists()
    assert (dl / "results.png").exists()


def test_serap_fallback_output_diratakan(tmp_path):
    """Kalau Kaggle meratakan output (results.csv tak di bawah <run>), serap
    tetap menemukannya lewat rglob."""
    out = tmp_path / "out"
    _tulis_run(out, "folder-lain", 3)
    dl = tmp_path / "latih-L2"
    assert k.serap_keluaran(out, "run-yang-tak-ada", dl) == 3
    assert (dl / "weights" / "best.pt").exists()


# ============================================================
# LEDGER KUOTA & ROTASI (murni)
# ============================================================

def test_ledger_kuota_melewati_akun_penuh(tmp_path):
    pool = [{"user": "a", "token": "ta"}, {"user": "b", "token": "tb"}]
    # a sudah memakai hampir seluruh jatah -> satu leg lagi menembus KUOTA.
    k._catat_pakai(tmp_path, "a", k.KUOTA_MINGGU_JAM - 1)
    pilih = k._pilih_akun(tmp_path, pool)
    assert pilih["user"] == "b", "akun yang penuh dilewati"


def test_tandai_habis_lalu_rotasi(tmp_path):
    pool = [{"user": "a", "token": "ta"}, {"user": "b", "token": "tb"}]
    assert k._pilih_akun(tmp_path, pool)["user"] == "a"
    k._tandai_habis(tmp_path, "a")
    assert k._pilih_akun(tmp_path, pool)["user"] == "b"
    k._tandai_habis(tmp_path, "b")
    assert k._pilih_akun(tmp_path, pool) is None, "semua habis -> None"


# ============================================================
# PERCABANGAN BACKEND di latih.py
# ============================================================

def _versi_yaml(ds: Path, nomor: int, kelas):
    d = ds / ".versi" / f"v{nomor}"
    (d / "train" / "images").mkdir(parents=True, exist_ok=True)
    (d / "data.yaml").write_text(
        "path: .\ntrain: train/images\nval: valid/images\n"
        f"nc: {len(kelas)}\nnames: [" + ", ".join(f"'{c}'" for c in kelas) + "]\n")


def test_siapkan_menyimpan_backend(tmp_path):
    _versi_yaml(tmp_path, 1, ["a", "b"])
    isi = latih.siapkan(tmp_path, nama="x", versi_nomor=1, tugas="detect",
                        bobot="y.pt", par={"epochs": 3}, oleh="uji", backend="kaggle")
    assert latih.baca(tmp_path, isi["nomor"])["backend"] == "kaggle"
    # bawaan & nilai asing -> lokal
    isi2 = latih.siapkan(tmp_path, nama="x", versi_nomor=1, tugas="detect",
                         bobot="y.pt", par={"epochs": 3}, oleh="uji")
    assert latih.baca(tmp_path, isi2["nomor"])["backend"] == "lokal"
    isi3 = latih.siapkan(tmp_path, nama="x", versi_nomor=1, tugas="detect",
                         bobot="y.pt", par={"epochs": 3}, oleh="uji", backend="aneh")
    assert latih.baca(tmp_path, isi3["nomor"])["backend"] == "lokal"


def test_jalankan_memilih_modul_worker(tmp_path, monkeypatch):
    direkam = {}

    class FakePopen:
        def __init__(self, perintah, **kw):
            direkam["perintah"] = perintah
            self.pid = 4242

    monkeypatch.setattr(latih.subprocess, "Popen", FakePopen)

    latih._tulis(tmp_path, 1, {"nomor": 1, "versi": 1, "backend": "kaggle",
                               "par": {"epochs": 3}, "keadaan": "antre"})
    latih.jalankan(tmp_path, 1)
    assert "app.services.latih_kaggle" in direkam["perintah"]

    latih._tulis(tmp_path, 2, {"nomor": 2, "versi": 1, "backend": "lokal",
                               "par": {"epochs": 3}, "keadaan": "antre"})
    latih.jalankan(tmp_path, 2)
    assert "app.services.latih_jalan" in direkam["perintah"]


def test_status_memuat_backend_dan_kaggle(tmp_path):
    latih._tulis(tmp_path, 1, {
        "nomor": 1, "versi": 1, "par": {"epochs": 3}, "keadaan": "tertunda",
        "backend": "kaggle", "kaggle": {"akun": "a", "leg": 2, "epochs_done": 7}})
    s = latih.status(tmp_path, 1)
    assert s["backend"] == "kaggle"
    assert s["kaggle"]["leg"] == 2 and s["kaggle"]["epochs_done"] == 7


# ============================================================
# ORKESTRATOR BERBILAH main() — dengan _kg palsu
# ============================================================

class FakeKg:
    """Kaggle CLI palsu. Mensimulasikan datasets/kernels per-leg menurut
    `legs`; untuk 'kernels output' ia MENULIS results.csv + bobot ke folder -p,
    persis seperti CLI asli, supaya serap_keluaran bekerja apa adanya."""

    def __init__(self, nomor, legs):
        self.nomor, self.legs, self.i = nomor, legs, -1
        self.push = 0

    def __call__(self, args, token, timeout=0, masuk=None):
        a = list(args)
        if a[:2] == ["datasets", "status"]:
            return 0, "ready"
        if a[:2] in (["datasets", "create"], ["datasets", "version"]):
            return 0, "created"
        if a[:2] == ["kernels", "push"]:
            self.i += 1
            self.push += 1
            return 0, "pushed"
        leg = self.legs[self.i]
        if a[:2] == ["kernels", "status"]:
            if leg.get("quota"):
                return 0, 'status "KernelWorkerStatus.ERROR" GPU quota exceeded'
            if leg.get("error"):
                return 0, 'status "KernelWorkerStatus.ERROR"'
            return 0, 'status "KernelWorkerStatus.COMPLETE"'
        if a[:2] == ["kernels", "output"]:
            if leg.get("quota") or leg.get("no_output"):
                return 1, "output tidak tersedia"
            d = Path(a[a.index("-p") + 1])
            _tulis_run(d, f"run-l{self.nomor}", leg.get("epochs", 1))
            return 0, "downloaded"
        return 0, ""


@pytest.fixture
def siapkan_main(tmp_path, monkeypatch):
    """Rangkai projek+versi+rekaman, patch seam jaringan & akun. Kembalikan
    fungsi untuk menjalankan main() pada skenario leg tertentu."""
    _versi_yaml(tmp_path, 1, ["a", "b"])
    monkeypatch.setattr(k, "_tidur", lambda d: None)
    monkeypatch.setattr("app.config.get_settings", lambda: NS())

    def jalankan(nomor, target, legs, pool, kag_awal=None, ckpt_rows=0):
        latih._tulis(tmp_path, nomor, {
            "nomor": nomor, "versi": 1, "tugas": "detect", "bobot": "yolov8n.pt",
            "par": {"epochs": target}, "keadaan": "antre", "backend": "kaggle",
            "kaggle": dict(kag_awal or {})})
        # Checkpoint lokal (meniru leg sebelumnya) supaya jalur resume punya
        # last.pt + results.csv untuk disambung.
        if ckpt_rows:
            _tulis_run(tmp_path, "pra", ckpt_rows)
            k.serap_keluaran(tmp_path, "pra", latih.dir_latih(tmp_path, nomor))
        monkeypatch.setattr(k, "akun_pool", lambda s: list(pool))
        monkeypatch.setattr(k, "_kg", FakeKg(nomor, legs))
        monkeypatch.setattr("sys.argv", ["latih_kaggle", str(tmp_path), str(nomor)])
        rc = k.main()
        return rc, latih.baca(tmp_path, nomor)

    return tmp_path, jalankan


def test_main_satu_leg_selesai(siapkan_main):
    tmp_path, jalankan = siapkan_main
    rc, rek = jalankan(1, target=5, legs=[{"epochs": 5}],
                       pool=[{"user": "a", "token": "ta"}])
    assert rc == 0
    assert rek["keadaan"] == "selesai"
    assert rek["kaggle"]["epochs_done"] == 5 and rek["kaggle"]["leg"] == 1
    assert (latih.dir_latih(tmp_path, 1) / "weights" / "best.pt").exists()


def test_main_multi_leg_akumulasi_epoch(siapkan_main):
    tmp_path, jalankan = siapkan_main
    # leg1 berhenti di 4 (batas waktu), leg2 melanjutkan 6 -> total 10.
    rc, rek = jalankan(2, target=10, legs=[{"epochs": 4}, {"epochs": 6}],
                       pool=[{"user": "a", "token": "ta"}])
    assert rc == 0 and rek["keadaan"] == "selesai"
    assert rek["kaggle"]["leg"] == 2
    assert rek["kaggle"]["epochs_done"] == 10, "epoch harus KUMULATIF lintas-leg"


def test_main_rotasi_akun_saat_kuota_habis(siapkan_main):
    tmp_path, jalankan = siapkan_main
    # akun a kehabisan kuota di leg pertama -> rotasi ke b, b menyelesaikan.
    rc, rek = jalankan(3, target=5, legs=[{"quota": True}, {"epochs": 5}],
                       pool=[{"user": "a", "token": "ta"}, {"user": "b", "token": "tb"}])
    assert rc == 0 and rek["keadaan"] == "selesai"
    assert rek["kaggle"]["akun"] == "b", "leg sukses dijalankan akun cadangan"
    assert k._akun_habis(tmp_path, "a") is True


def test_main_semua_kuota_habis_jadi_tertunda(siapkan_main):
    tmp_path, jalankan = siapkan_main
    rc, rek = jalankan(4, target=5, legs=[{"quota": True}],
                       pool=[{"user": "a", "token": "ta"}])
    assert rc == 0
    assert rek["keadaan"] == "tertunda"
    assert "kuota" in (rek.get("galat") or "").lower()


def test_main_resume_dari_tertunda(siapkan_main):
    """Job yang tertunda di epoch 4 disambung: MELANJUTKAN dari checkpoint lokal
    (bukan mulai dari nol) sampai target 10, satu leg lagi."""
    tmp_path, jalankan = siapkan_main
    rc, rek = jalankan(5, target=10, legs=[{"epochs": 6}],
                       pool=[{"user": "a", "token": "ta"}],
                       kag_awal={"epochs_done": 4, "leg": 1}, ckpt_rows=4)
    assert rc == 0 and rek["keadaan"] == "selesai"
    assert rek["kaggle"]["epochs_done"] == 10, "4 (lama) + 6 (leg sambungan)"
    assert rek["kaggle"]["leg"] == 2


def test_reset_habis_membersihkan_penanda(tmp_path):
    k._tandai_habis(tmp_path, "a")
    assert k._akun_habis(tmp_path, "a") is True
    k.reset_habis(tmp_path)
    assert k._akun_habis(tmp_path, "a") is False
