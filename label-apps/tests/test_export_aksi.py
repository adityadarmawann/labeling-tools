"""
Uji ekspor versi klip aksi (Langkah 8): export.zip_aksi + rute
GET /api/aksi/versi/unduh.

Dua lapis:
  1. PACKAGING MURNI (selalu jalan, TANPA ffmpeg): sebuah versi beku .versi/vN/
     dipalsukan di disk, lalu zip_aksi membungkusnya. Yang dijaga: tata letak
     folder-per-kelas train/valid utuh, aksi.yaml + MANIFES.json ikut, valid/
     NOL _aug_/_bal_ (anti-bocor + valid-bersih terbawa apa adanya), dan versi
     bukan-aksi / tak ada ditolak jelas lewat ValueError.
  2. RUTE (via klien, TANPA ffmpeg): versi palsu + registri dipasang, lalu
     anggota (pemilik & pelabel) dapat 200 + application/zip + Content-Disposition;
     bukan anggota ditolak; versi hilang / bukan-aksi -> 404.
  3. BUILD NYATA (digerbang klip.siap_video): bangun versi kecil dengan
     klip_olah lalu zip_aksi hasil aslinya — bukti ekspor cocok dengan apa yang
     benar-benar ditulis build.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from app.services import export, klip, klip_olah, klip_scan, versi
from tests.conftest import PW_ANGGI, PW_PAUL, klien_baru, masuk


# ============================================================
# pembantu: versi klip aksi PALSU di disk (tanpa ffmpeg)
# ============================================================

def _pasang_versi_aksi(proj: Path, nomor: int = 1) -> Path:
    """Tulis sebuah versi beku .versi/vN/ seperti yang dihasilkan klip_olah,
    tetapi dengan berkas .mp4 tiruan (byte apa adanya cukup — zip_aksi hanya
    menyalin byte, tak memutar). Train punya asli + _aug_; valid asli saja.
    Mengembalikan direktori versinya."""
    vd = proj / ".versi" / f"v{nomor}"
    isi = {
        "train/shoot/shoot_vA_0001.mp4": b"train-shoot-asli",
        "train/shoot/shoot_vA_0001_aug_hflip_00.mp4": b"train-shoot-aug",
        "train/pass/pass_vA_0001.mp4": b"train-pass-asli",
        "valid/shoot/shoot_vB_0001.mp4": b"valid-shoot-asli",
        "valid/pass/pass_vB_0001.mp4": b"valid-pass-asli",
    }
    for rel, data in isi.items():
        p = vd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (vd / "aksi.yaml").write_text(
        "train: train\nval: valid\nnc: 2\nnames: ['shoot', 'pass']\n"
        "negatif: ~\n", encoding="utf-8")
    (vd / "MANIFES.json").write_text(json.dumps({
        "versi": nomor, "jenis": "aksi",
        "aksi": {"kelas": ["shoot", "pass"], "merge": {}, "negatif": ""},
        "berkas": [{"berkas": r, "split": r.split("/")[0],
                    "asal": "aug" if "_aug_" in r else "asli"} for r in isi],
    }, ensure_ascii=False), encoding="utf-8")
    return vd


def _daftar_versi_aksi(proj: Path, nomor: int = 1):
    """Daftarkan versi aksi di registri versi.py, supaya rute unduh menemukannya.
    `hasil.jenis == 'aksi'` adalah penanda yang dipakai rute untuk membedakan
    versi klip dari versi gambar."""
    versi.buat(
        proj, "paul", "80:20",
        ["shoot_vA_0001.mp4", "valid_shoot.mp4"],
        {"shoot_vA_0001.mp4": "train", "valid_shoot.mp4": "valid"},
        {"split": {"train": 3, "valid": 2}, "kelas": 2, "objek": 5},
        nomor=nomor,
        hasil={"jenis": "aksi", "jumlah": {"train": 3, "valid": 2}, "kelas": 2,
               "n": 5})


def _entri_zip(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.namelist()


# ============================================================
# 1. PACKAGING MURNI — tanpa ffmpeg
# ============================================================

def test_zip_aksi_tata_letak_folder_per_kelas(tmp_path):
    vd = _pasang_versi_aksi(tmp_path / "proj")
    nama = _entri_zip(export.zip_aksi(vd))

    # Metadata ikut.
    assert "aksi.yaml" in nama and "MANIFES.json" in nama
    # Struktur folder-per-kelas utuh di kedua split.
    assert "train/shoot/shoot_vA_0001.mp4" in nama
    assert "train/pass/pass_vA_0001.mp4" in nama
    assert "valid/shoot/shoot_vB_0001.mp4" in nama
    assert "valid/pass/pass_vB_0001.mp4" in nama
    # Setiap .mp4 ada di bawah <split>/<kelas>/.
    for n in nama:
        if n.endswith(".mp4"):
            bagian = n.split("/")
            assert bagian[0] in ("train", "valid")
            assert bagian[1] in ("shoot", "pass")
            assert len(bagian) == 3


def test_zip_aksi_valid_hanya_asli(tmp_path):
    """valid/ tak boleh memuat satu pun klip augmentasi (_aug_/_bal_) — invarian
    valid-bersih dibawa apa adanya dari build, dan diperiksa kembali di sini."""
    vd = _pasang_versi_aksi(tmp_path / "proj")
    nama = _entri_zip(export.zip_aksi(vd))
    for n in nama:
        if n.startswith("valid/"):
            assert "_aug_" not in n and "_bal_" not in n, n
    # train MEMANG memuat augmentasi (bukti augmentasi tak hilang dari ekspor).
    assert any(n.startswith("train/") and "_aug_" in n for n in nama)


def test_zip_aksi_byte_utuh(tmp_path):
    """Isi berkas disalin apa adanya (bukan diproses/di-reencode)."""
    vd = _pasang_versi_aksi(tmp_path / "proj")
    with zipfile.ZipFile(io.BytesIO(export.zip_aksi(vd))) as z:
        assert z.read("train/shoot/shoot_vA_0001.mp4") == b"train-shoot-asli"
        assert json.loads(z.read("MANIFES.json"))["jenis"] == "aksi"


def test_zip_aksi_tolak_bukan_versi_aksi(tmp_path):
    # Tak ada MANIFES / direktori tak ada -> ditolak.
    with pytest.raises(ValueError):
        export.zip_aksi(tmp_path / "tak-ada")
    kosong = tmp_path / "kosong"
    kosong.mkdir()
    with pytest.raises(ValueError):
        export.zip_aksi(kosong)
    # MANIFES ada tetapi jenisnya bukan "aksi" (mis. versi gambar).
    bukan = tmp_path / "bukan"
    bukan.mkdir()
    (bukan / "MANIFES.json").write_text(json.dumps({"jenis": "yolo"}))
    with pytest.raises(ValueError):
        export.zip_aksi(bukan)


def test_format_aksi_terpisah_dari_format_gambar():
    """aksi-klip SENGAJA tak masuk FORMAT gambar (biar tak muncul di dropdown
    ekspor gambar lalu jatuh ke 'format tidak dikenal' di zip_dataset)."""
    assert "aksi-klip" in export.FORMAT_AKSI
    assert "aksi-klip" not in export.FORMAT


# ============================================================
# 2. RUTE GET /api/aksi/versi/unduh — tanpa ffmpeg
# ============================================================

def _buat_video_aksi(klien, nama, kelas=("shoot", "pass")):
    r = klien.post(f"/api/projek/baru?nama={nama}&jenis=video")
    assert r.status_code == 200 and r.json()["ok"], r.text[:200]
    r = klien.post(f"/api/tugas/aksi?ds={nama}",
                   json={"aksi": {"kelas": list(kelas)}})
    assert r.json()["ok"], r.text[:200]


def test_unduh_pemilik_dapat_zip(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    _pasang_versi_aksi(proj, 1)
    _daftar_versi_aksi(proj, 1)

    r = klien.get("/api/aksi/versi/unduh", params={"ds": "aksi", "nomor": 1})
    assert r.status_code == 200, r.text[:200]
    assert r.headers["content-type"] == "application/zip"
    assert 'attachment; filename="aksi-aksi-v1.zip"' in \
        r.headers["content-disposition"]
    # ZIP yang terkirim memang arsip klip aksi.
    nama = _entri_zip(r.content)
    assert "aksi.yaml" in nama and "train/shoot/shoot_vA_0001.mp4" in nama


def test_unduh_pelabel_anggota_boleh(klien, aplikasi, lingkungan):
    """ANGGOTA (bahkan pelabel, bukan cuma pemilik) boleh mengunduh keluaran
    ekspor — gerbang BACA, bukan unggah."""
    from app.services import tugas

    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    _pasang_versi_aksi(proj, 1)
    _daftar_versi_aksi(proj, 1)
    tugas.undang(proj, "paul", "anggi", peran="pelabel", akses="semua")

    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    r = anggi.get("/api/aksi/versi/unduh", params={"ds": "paul/aksi", "nomor": 1})
    assert r.status_code == 200, r.text[:200]
    assert r.headers["content-type"] == "application/zip"


def test_unduh_bukan_anggota_ditolak(klien, aplikasi, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    _pasang_versi_aksi(proj, 1)
    _daftar_versi_aksi(proj, 1)

    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    r = anggi.get("/api/aksi/versi/unduh", params={"ds": "paul/aksi", "nomor": 1})
    assert r.status_code in (403, 404), r.status_code
    assert r.headers["content-type"] != "application/zip"


def test_unduh_versi_tak_ada_404(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    r = klien.get("/api/aksi/versi/unduh", params={"ds": "aksi", "nomor": 99})
    assert r.status_code == 404, r.text[:200]
    # nomor tak sah juga 404.
    assert klien.get("/api/aksi/versi/unduh",
                     params={"ds": "aksi", "nomor": 0}).status_code == 404


def test_unduh_versi_bukan_aksi_404(klien, lingkungan):
    """Versi GAMBAR (hasil.jenis != 'aksi') di projek ini tak bisa diunduh lewat
    pintu aksi."""
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi")
    proj = lingkungan["ruang"] / "aksi"
    # Versi tanpa penanda aksi (seolah versi gambar lama).
    versi.buat(proj, "paul", "80:10:10", ["a.jpg"], {"a.jpg": "train"},
               {"split": {"train": 1}, "kelas": 1}, nomor=1, hasil={})
    r = klien.get("/api/aksi/versi/unduh", params={"ds": "aksi", "nomor": 1})
    assert r.status_code == 404, r.text[:200]


# ============================================================
# 3. BUILD NYATA -> zip_aksi — digerbang ffmpeg
# ============================================================
_SIAP, _ALASAN = klip.siap_video()


@pytest.mark.skipif(not _SIAP, reason=f"ffmpeg tak siap: {_ALASAN}")
def test_build_lalu_zip_aksi(tmp_path):
    """Bangun versi kecil sungguhan dengan klip_olah, lalu zip_aksi hasilnya:
    ekspor harus cocok dengan berkas yang benar-benar ditulis build (valid
    bersih, folder-per-kelas)."""
    from tests.test_klip_versi import _bibit_projek

    d = tmp_path / "proj"
    aksi = _bibit_projek(d)
    items = klip_scan.pindai(d)["items"]
    klip_olah.jalankan_versi(d, 1, items, aksi,
                             {"varian": 1, "rasio_val": 0.25, "fps": 10,
                              "ukuran": [64, 64]},
                             kunci="uji-ekspor-aksi", oleh="paul")
    k = klip_olah.kemajuan("uji-ekspor-aksi")
    assert k.get("selesai") and not k.get("galat"), k

    nama = _entri_zip(export.zip_aksi(klip_olah.dir_versi(d, 1)))
    assert "aksi.yaml" in nama and "MANIFES.json" in nama
    mp4 = [n for n in nama if n.endswith(".mp4")]
    assert any(n.startswith("train/") for n in mp4)
    assert any(n.startswith("valid/") for n in mp4)
    # valid bersih bahkan pada build nyata.
    for n in mp4:
        if n.startswith("valid/"):
            assert "_aug_" not in n and "_bal_" not in n, n
    # folder-per-kelas: <split>/<kelas>/<file>.
    for n in mp4:
        assert len(n.split("/")) == 3
        assert n.split("/")[1] in aksi["kelas"]
