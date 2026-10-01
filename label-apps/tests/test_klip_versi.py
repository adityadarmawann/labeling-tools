"""
Uji aug + balancer + pembekuan versi klip (Langkah 7), services/klip_olah.py.

Dua lapis:
  1. MATEMATIKA MURNI (selalu jalan, TANPA ffmpeg): pembelahan anti-bocor per
     video sumber, rencana balancing (sasaran median + cap 3x), dan sasaran
     pemulihan porsi negatif. Ini yang menjaga invarian walau CI tak punya ffmpeg.
  2. BUILD NYATA (digerbang klip.siap_video, di-skip bersih tanpa ffmpeg):
     membangun versi kecil dari projek berbibit lalu memeriksa keempat invarian
     sakral di berkas yang benar-benar ditulis:
       - anti-bocor : tak ada video sumber yang klipnya ada di train DAN valid
       - valid bersih: valid/ nol klip _aug_/_bal_ (cek MANIFES asal + nama)
       - cap balance : tak ada kelas melebihi 3x median di train
       - pulih negatif: porsi kelas negatif tak tergerus jadi sisa
       - tata letak  : .versi/vN/{train,valid}/<kelas>/*.mp4 + aksi.yaml + MANIFES
       - versi terdaftar di versi.daftar; klip SUMBER tak tersentuh
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.services import klip, klip_olah, klip_scan, klip_tag, versi


# ============================================================
# 1. MATEMATIKA MURNI — tanpa ffmpeg
# ============================================================

def test_bagi_split_tak_bocor_per_video_sumber():
    """Satu video sumber = satu grup utuh: seluruh klipnya di sisi yang SAMA.
    Inilah invarian anti-bocor, dan ia harus berlaku lintas kelas."""
    records = []
    # 5 video sumber, tiap video menyumbang klip LINTAS kelas (persis kasus
    # video 10 menit yang dipotong jadi shoot+pass+dribble sekaligus).
    for v in range(5):
        for k in range(4):
            kelas = ("shoot", "pass", "dribble")[k % 3]
            records.append({"rel": f"klip/b/{kelas}_vid{v}_t{k:04d}.mp4",
                            "srcid": f"vid{v}"})
    peta = klip_olah.bagi_split(records, rasio_val=0.2, seed=42)
    assert len(peta) == len(records)
    # Kumpulkan split per video sumber: tiap video hanya boleh satu split.
    per_vid = {}
    for r in records:
        per_vid.setdefault(r["srcid"], set()).add(peta[r["rel"]])
    for vid, splits in per_vid.items():
        assert len(splits) == 1, f"{vid} bocor ke {splits}"
    # Deterministik: ulang -> sama persis.
    assert klip_olah.bagi_split(records, 0.2, seed=42) == peta
    # valid tak kosong & tak menelan segalanya.
    nilai = set(peta.values())
    assert "valid" in nilai and "train" in nilai


def test_bagi_split_valid_tak_pernah_kosong_satu_video():
    """Walau satu video sumber saja, valid tetap dijamin ada (bukan 0)."""
    records = [{"rel": f"klip/b/x_v_t{i:04d}.mp4", "srcid": "v"} for i in range(3)]
    peta = klip_olah.bagi_split(records, rasio_val=0.2, seed=1)
    # Satu grup -> semua ke valid (jaminan "valid tak kosong"); yang penting tak
    # bocor (semua satu sisi) dan valid terisi.
    assert set(peta.values()) <= {"train", "valid"}
    assert "valid" in set(peta.values())


def test_rencana_kelas_sasaran_median_dan_cap_3x():
    """Sasaran = median; tak ada kelas yang boleh menembus 3x median MAUPUN
    3x jumlah aslinya (koreksi bug aug-balance-v4 yang mengizinkan 4x asli)."""
    hitung = {"a": 4, "b": 10, "c": 20}        # median = 10
    asli = {"a": 4, "b": 10, "c": 20}
    rencana, target = klip_olah.rencana_kelas(hitung, asli, cap=3.0,
                                              sasaran="median")
    assert target == 10
    # a kurang 6 -> ditambal ke 10 (10 <= 3*10 dan <= 3*4=12). b,c sudah >= median.
    assert rencana.get("a") == 6
    assert "b" not in rencana and "c" not in rencana
    # Jumlah akhir tiap kelas tak melebihi 3x median.
    for kelas, n in hitung.items():
        akhir = n + rencana.get(kelas, 0)
        assert akhir <= 3 * target


def test_rencana_kelas_cap_cegah_kelas_kecil_membengkak():
    """Kelas dengan asli sangat kecil tak boleh digelembungkan melebihi 3x
    aslinya, walau median jauh di atasnya."""
    hitung = {"kecil": 2, "besar": 30}         # median = 16
    asli = {"kecil": 2, "besar": 30}
    rencana, target = klip_olah.rencana_kelas(hitung, asli, cap=3.0)
    # kecil: median=16 menuntut +14, tapi 3x asli=6 -> jumlah akhir dijepit 6.
    akhir = 2 + rencana.get("kecil", 0)
    assert akhir <= 3 * asli["kecil"], akhir
    assert akhir <= 6


def test_sasaran_negatif_memulihkan_porsi():
    """Porsi negatif yang tergerus dipulihkan ke porsi asal, dijepit cap."""
    # train 100 klip, negatif kini 5 (5%), porsi asal 25%. Pos = 95.
    butuh = klip_olah.sasaran_negatif(100, 5, porsi_asal=0.25, cap_atap=1000)
    # sasaran neg ~ 0.25*95/0.75 ≈ 31 -> butuh ≈ 26.
    assert 20 <= butuh <= 32, butuh
    # Kalau sudah cukup, nol.
    assert klip_olah.sasaran_negatif(100, 40, 0.25, 1000) == 0
    # Dijepit cap_atap.
    assert klip_olah.sasaran_negatif(100, 5, 0.25, 10) <= 10 - 5


def test_resep_sah_menjepit_nilai_liar():
    r = klip_olah.resep_sah({"varian": 99, "rasio_val": 0.9, "cap": 0.1,
                             "ukuran": [223, 101], "fps": -5})
    assert r["varian"] == 8 and r["rasio_val"] == 0.5 and r["cap"] == 1.0
    assert r["ukuran"] == [224, 102] and r["fps"] == 1


def test_source_key_dan_token_stabil():
    """Klip dari video yang sama -> token yang sama, apa pun penanda potongannya."""
    a = klip_olah.video_token(klip_olah.source_key("shoot_vidA_t0001"))
    b = klip_olah.video_token(klip_olah.source_key("shoot_vidA_t0050"))
    assert a == b
    c = klip_olah.video_token(klip_olah.source_key("shoot_vidB_t0001"))
    assert c != a


# ============================================================
# 2. BUILD NYATA — digerbang ffmpeg
# ============================================================
_SIAP, _ALASAN = klip.siap_video()
pytestmark_build = pytest.mark.skipif(not _SIAP, reason=f"ffmpeg tak siap: {_ALASAN}")


def _buat_klip(path: Path, dur=0.4, w=48, h=48, fps=10):
    """Klip synth kecil & cepat lewat ffmpeg lavfi (durasi/fps pasti)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg, _, enc, opts = klip.detect_ffmpeg()
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", f"testsrc=duration={dur}:size={w}x{h}:rate={fps}",
           "-c:v", enc, *opts, "-pix_fmt", "yuv420p", "-an", "-y", str(path)]
    assert subprocess.run(cmd, capture_output=True, timeout=120).returncode == 0


def _bibit_projek(d: Path) -> dict:
    """Projek video+aksi dengan klip berlabel: 3 kelas (negatif 'diam'),
    4 video sumber, klip lintas kelas. Mengembalikan aksi{} untuk build."""
    d.mkdir(parents=True, exist_ok=True)
    # (kelas, srcid, berapa klip). Dirancang timpang supaya balancing & cap
    # benar-benar bekerja, dan tiap video sumber menyumbang >1 klip.
    rencana = [
        ("shoot", "vidA", 2), ("pass", "vidA", 1), ("diam", "vidA", 2),
        ("shoot", "vidB", 2), ("pass", "vidB", 1), ("diam", "vidB", 1),
        ("shoot", "vidC", 1), ("pass", "vidC", 1), ("diam", "vidC", 1),
        ("shoot", "vidD", 2), ("diam", "vidD", 2),
    ]
    t = 0
    for kelas, srcid, n in rencana:
        for _ in range(n):
            t += 1
            rel = f"klip/b1/{kelas}_{srcid}_t{t:04d}.mp4"
            _buat_klip(d / rel)
            klip_tag.set_label(d, rel, kelas, batch="b1", srcid=srcid)
    return {"kelas": ["shoot", "pass", "diam"], "merge": {}, "warna": [],
            "negatif": "diam"}


def _token(srcid: str) -> str:
    return klip_olah.video_token(srcid)


@pytestmark_build
def test_build_versi_klip_memenuhi_semua_invarian(tmp_path):
    d = tmp_path / "proj"
    aksi = _bibit_projek(d)
    resep = {"varian": 1, "rasio_val": 0.25, "cap": 3.0,
             "fps": 10, "ukuran": [64, 64]}

    pindai = klip_scan.pindai(d)
    items = pindai["items"]
    assert sum(1 for it in items if it["label"]) == 16

    # Jalankan build sinkron (Giliran ditunggu di dalam; versi.buat dipanggil).
    kunci = "uji-klip-versi"
    klip_olah.jalankan_versi(d, 1, items, aksi, resep, kunci=kunci, oleh="paul")
    k = klip_olah.kemajuan(kunci)
    assert k.get("selesai") and not k.get("galat"), k

    v = d / ".versi" / "v1"
    # --- tata letak ---
    assert (v / "aksi.yaml").is_file() and (v / "MANIFES.json").is_file()
    import json
    man = json.loads((v / "MANIFES.json").read_text())
    assert man["jenis"] == "aksi" and man["aksi"]["negatif"] == "diam"
    yml = (v / "aksi.yaml").read_text()
    assert "names: ['shoot', 'pass', 'diam']" in yml and "negatif: diam" in yml

    train_mp4 = list((v / "train").rglob("*.mp4"))
    valid_mp4 = list((v / "valid").rglob("*.mp4"))
    assert train_mp4 and valid_mp4, "train & valid harus terisi"
    # Layout folder-per-kelas: tiap klip di bawah <split>/<kelas>/.
    for p in train_mp4 + valid_mp4:
        assert p.parent.parent.name in ("train", "valid")
        assert p.parent.name in ("shoot", "pass", "diam")

    # --- valid bersih: NOL _aug_/_bal_ (nama berkas DAN MANIFES asal) ---
    for p in valid_mp4:
        assert "_aug_" not in p.stem and "_bal_" not in p.stem, p.name
    for m in man["berkas"]:
        if m["split"] == "valid":
            assert m["asal"] == "asli", m

    # --- anti-bocor: token video sumber tak ada di train DAN valid ---
    split_token: dict[str, set] = {}
    for p in train_mp4 + valid_mp4:
        tok = p.stem.split("_")[1]               # <kelas>_<token>_...
        split_token.setdefault(tok, set()).add(p.parent.parent.name)
    for tok, splits in split_token.items():
        assert len(splits) == 1, f"token {tok} bocor ke {splits}"
    # Minimal 2 video sumber ikut (bukti pengelompokan diuji di skala nyata).
    assert len(split_token) >= 2

    # --- cap balance: tak ada kelas melebihi 3x median di train ---
    import numpy as np
    per_kelas = {}
    for kelas in aksi["kelas"]:
        per_kelas[kelas] = sum(1 for _ in (v / "train" / kelas).glob("*.mp4"))
    hidup = [n for n in per_kelas.values() if n > 0]
    median = int(np.median(hidup))
    for kelas, n in per_kelas.items():
        assert n <= 3 * median + 0, f"{kelas}={n} > 3x median {median}"

    # --- pulih negatif: porsi 'diam' di train tak tergerus jadi sisa ---
    total_train = sum(per_kelas.values())
    porsi_diam = per_kelas["diam"] / max(total_train, 1)
    assert porsi_diam >= 0.15, f"porsi negatif tergerus: {porsi_diam:.0%}"

    # --- versi terdaftar ---
    daftar = versi.daftar(d)
    assert daftar and daftar[0]["nomor"] == 1, daftar
    assert daftar[0]["hasil"]["jenis"] == "aksi"

    # --- klip SUMBER tak tersentuh ---
    assert len(list((d / "klip" / "b1").glob("*.mp4"))) == 16
    assert (d / ".klip.json").is_file()


@pytestmark_build
def test_build_tanpa_klip_berlabel_gagal_jelas(tmp_path):
    """Build tanpa satu pun klip berlabel menjawab galat jelas, bukan versi
    kosong senyap."""
    d = tmp_path / "kosong"
    d.mkdir()
    aksi = {"kelas": ["shoot", "pass"], "merge": {}, "warna": [], "negatif": ""}
    klip_olah.jalankan_versi(d, 1, [], aksi, {}, kunci="uji-kosong", oleh="paul")
    k = klip_olah.kemajuan("uji-kosong")
    assert k.get("galat") and "berlabel" in k["galat"], k
    assert not (d / ".versi" / "v1").exists()


@pytestmark_build
def test_buang_hasil_menghapus_folder_versi(tmp_path):
    d = tmp_path / "proj"
    aksi = _bibit_projek(d)
    items = klip_scan.pindai(d)["items"]
    klip_olah.jalankan_versi(d, 1, items, aksi,
                             {"varian": 0, "fps": 10, "ukuran": [64, 64]},
                             kunci="uji-buang", oleh="paul")
    assert (d / ".versi" / "v1").is_dir()
    klip_olah.buang_hasil(d, 1)
    assert not (d / ".versi" / "v1").exists()
    # Sumber tetap utuh.
    assert len(list((d / "klip" / "b1").glob("*.mp4"))) == 16
