"""
Uji IMPORTER dataset klip aksi jadi (impor_aksi).

Yang dijaga: dataset folder-per-kelas (train/val) yang sudah jadi di LUAR HIGOLAB
menjadi satu VERSI `.versi/vN/` yang (a) berbentuk sama dengan keluaran build
(aksi.yaml + MANIFES jenis 'aksi' + train/valid folder-per-kelas), (b) TERDAFTAR
lewat versi.buat sehingga muncul di pemilih versi aksi UI, (c) menolak masukan
yang melanggar invarian valid-bersih, dan (d) menyetel kelas aksi projek.

Tak butuh ffmpeg/torch: importer hanya menyalin berkas + menulis metadata.
"""
from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import pytest

from app.services import impor_aksi, projek, tugas, versi
from tests.conftest import PW_PAUL, masuk
from tests.test_aksi_label import _buat_video_aksi, _klip
from tests.test_video_unggah import _buat_projek


def _dataset(root: Path, train: dict[str, int], val: dict[str, int]) -> tuple[Path, Path]:
    """Dataset jadi tiruan: root/train/<k>/*.mp4 + root/val/<k>/*.mp4 (mp4 tiruan).

    train & val memakai srcid BERBEDA, seperti split anti-bocor sungguhan (klip
    asli train dan val berasal dari video sumber yang berlainan) — jadi namanya
    tak bentrok saat disalin ke satu folder klip/."""
    td, vd = root / "train", root / "val"
    for base, counts, sid in ((td, train, "abcDEF01234"), (vd, val, "xyzGHI98765")):
        for k, n in counts.items():
            (base / k).mkdir(parents=True, exist_ok=True)
            for i in range(n):
                (base / k / f"{k}_{sid}_{i:04d}.mp4").write_bytes(b"x")
    return td, vd


def _projek_video(root: Path, nama: str = "vid") -> Path:
    return Path(projek.buat(root, nama, "video")["path"])


# ──────────────────────────────── jalur senang ────────────────────────────
def test_impor_bangun_versi_siap_latih(tmp_path):
    td, vd = _dataset(tmp_path / "ds", {"shoot": 3, "pass": 3}, {"shoot": 2, "pass": 2})
    proj = _projek_video(tmp_path / "ws")

    r = impor_aksi.impor(proj, td, vd, oleh="paul")

    assert r["nomor"] == 1 and r["kelas"] == 2
    assert r["jumlah"] == {"train": 6, "valid": 4}
    assert r["jenis"] == "aksi"

    vdir = proj / ".versi" / "v1"
    # aksi.yaml: kelas + layout folder-per-kelas yang dibaca ketiga backend
    yaml = (vdir / "aksi.yaml").read_text()
    assert "names: ['pass', 'shoot']" in yaml or "names: ['shoot', 'pass']" in yaml
    assert "train: train" in yaml and "val: valid" in yaml
    # klip tersalin ke train/ + valid/ folder-per-kelas
    assert len(list((vdir / "train" / "shoot").glob("*.mp4"))) == 3
    assert len(list((vdir / "valid" / "pass").glob("*.mp4"))) == 2
    # MANIFES jenis aksi
    man = json.loads((vdir / "MANIFES.json").read_text())
    assert man["jenis"] == "aksi" and len(man["berkas"]) == 10
    # kelas aksi projek ter-set
    data = tugas.baca(proj, "paul")
    assert sorted(data["aksi"]["kelas"]) == ["pass", "shoot"]


def test_impor_isi_klip_mentah_browsable(tmp_path):
    """Impor harus mengisi klip MENTAH (asli, bukan _aug_/_bal_) ke klip/<batch>/
    + label + masuk dataset, supaya tiap video bisa dibuka di Label Aksi."""
    from app.services import klip_tag, tugas
    td, vd = _dataset(tmp_path / "ds", {"shoot": 3, "pass": 2}, {"shoot": 1, "pass": 1})
    # klip augmentasi/balance di train -> harus DILEWATI dari klip mentah
    (td / "shoot" / "shoot_abcDEF01234_aug_0001.mp4").write_bytes(b"x")
    (td / "shoot" / "shoot_abcDEF01234_bal_0001.mp4").write_bytes(b"x")
    proj = _projek_video(tmp_path / "ws")

    r = impor_aksi.impor(proj, td, vd, oleh="paul")

    assert r["klip_mentah"] == 7          # train 3+2 + val 1+1 (aug/bal dilewati)
    bdir = proj / "klip" / r["batch"]
    mp4s = sorted(p.name for p in bdir.glob("*.mp4"))
    assert len(mp4s) == 7
    assert not any("_aug_" in n or "_bal_" in n for n in mp4s)
    # label + keanggotaan dataset tersimpan
    kd = klip_tag.baca(proj)["klip"]
    assert len(kd) == 7
    data = tugas.baca(proj, "paul")
    for rel, rec in kd.items():
        assert rec["label"] in ("shoot", "pass")
        assert rel.startswith("klip/")
        assert tugas.sudah_dimasukkan(data, rel)
    # versi beku tetap hanya klip asli di valid (invarian), tak terpengaruh
    assert (proj / ".versi" / "v1" / "aksi.yaml").is_file()


def test_impor_muncul_di_daftar_versi_aksi(tmp_path):
    """Versi hasil impor harus lolos saringan _versi_aksi (hasil.jenis=='aksi')."""
    td, vd = _dataset(tmp_path / "ds", {"a": 2, "b": 2}, {"a": 1, "b": 1})
    proj = _projek_video(tmp_path / "ws")
    impor_aksi.impor(proj, td, vd, oleh="paul")

    aksi = [v for v in versi.daftar(proj)
            if (v.get("hasil") or {}).get("jenis") == "aksi"]
    assert len(aksi) == 1
    assert aksi[0]["nomor"] == 1 and aksi[0]["kelas"] == 2


# ──────────────────────────────── invarian & penolakan ────────────────────
def test_impor_tolak_valid_kotor(tmp_path):
    """Klip _aug_/_bal_ di valid = angka validasi menipu -> ditolak keras."""
    td, vd = _dataset(tmp_path / "ds", {"shoot": 2, "pass": 2}, {"shoot": 1, "pass": 1})
    (vd / "shoot" / "shoot_abcDEF01234_aug_0001.mp4").write_bytes(b"x")
    proj = _projek_video(tmp_path / "ws")
    with pytest.raises(impor_aksi.ImporTolak, match="augmentasi di valid"):
        impor_aksi.impor(proj, td, vd, oleh="paul")
    # gagal bersih: tak meninggalkan versi separuh
    assert not (proj / ".versi" / "v1").exists()


def test_impor_auto_kelas_buang_train_saja(tmp_path):
    """Kelas yang hanya ada di train (mis. 'stand') otomatis dilewati —
    tak ada val = tak bisa diukur (seperti saran README action-v14)."""
    td, vd = _dataset(tmp_path / "ds",
                      {"shoot": 2, "pass": 2, "stand": 2},
                      {"shoot": 1, "pass": 1})
    proj = _projek_video(tmp_path / "ws")
    r = impor_aksi.impor(proj, td, vd, oleh="paul")
    assert r["kelas"] == 2                        # stand dibuang
    assert not (proj / ".versi" / "v1" / "train" / "stand").exists()


def test_impor_tolak_projek_bukan_video(tmp_path):
    td, vd = _dataset(tmp_path / "ds", {"a": 2, "b": 2}, {"a": 1, "b": 1})
    img = Path(projek.buat(tmp_path / "ws", "gbr", "image")["path"])
    with pytest.raises(impor_aksi.ImporTolak, match="bukan projek video"):
        impor_aksi.impor(img, td, vd, oleh="paul")


def test_impor_kelas_eksplisit_dan_negatif(tmp_path):
    td, vd = _dataset(tmp_path / "ds",
                      {"shoot": 2, "pass": 2, "diam": 2},
                      {"shoot": 1, "pass": 1, "diam": 1})
    proj = _projek_video(tmp_path / "ws")
    r = impor_aksi.impor(proj, td, vd, oleh="paul",
                         kelas=["shoot", "pass", "diam"], negatif="diam")
    assert r["kelas"] == 3 and r["negatif"] == "diam"
    data = tugas.baca(proj, "paul")
    assert data["aksi"]["negatif"] == "diam"
    # negatif yang bukan salah satu kelas ditolak
    proj2 = _projek_video(tmp_path / "ws2")
    with pytest.raises(impor_aksi.ImporTolak, match="negatif"):
        impor_aksi.impor(proj2, td, vd, oleh="paul", negatif="tak_ada")


def test_rencana_laporkan_masalah_tanpa_menulis(tmp_path):
    td, vd = _dataset(tmp_path / "ds", {"a": 2, "stand": 1}, {"a": 1})
    rc = impor_aksi.rencana(td, vd)
    assert rc["kelas"] == ["a"]                   # hanya a di dua split
    assert "stand" in rc["train_saja"]
    assert rc["jumlah"] == {"train": 2, "valid": 1}


# ──────────────────────────────── temukan_dataset ─────────────────────────
def test_temukan_dataset_bersarang_dan_alias_val(tmp_path):
    # Zip membungkus satu folder atas; valid bernama 'valid'
    base = tmp_path / "nest" / "v1"
    for s in ("train", "valid"):
        (base / s / "a").mkdir(parents=True)
        (base / s / "a" / "a.mp4").write_bytes(b"x")
    assert impor_aksi.temukan_dataset(tmp_path / "nest") == (base / "train", base / "valid")
    # Datar di root, split validasi bernama 'val'
    flat = tmp_path / "flat"
    for s in ("train", "val"):
        (flat / s / "a").mkdir(parents=True)
        (flat / s / "a" / "a.mp4").write_bytes(b"x")
    assert impor_aksi.temukan_dataset(flat) == (flat / "train", flat / "val")


def test_temukan_dataset_tak_ketemu(tmp_path):
    (tmp_path / "train" / "a").mkdir(parents=True)   # train saja, tanpa val
    (tmp_path / "train" / "a" / "a.mp4").write_bytes(b"x")
    assert impor_aksi.temukan_dataset(tmp_path) is None


# ──────────────────────────────── rute .zip (HTTP) ────────────────────────
def _zip_dataset(train: dict[str, int], val: dict[str, int], atas: str = "ds") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for split, counts in (("train", train), ("valid", val)):
            for k, n in counts.items():
                for i in range(n):
                    z.writestr(f"{atas}/{split}/{k}/{k}_x{i}.mp4", b"x")
    return buf.getvalue()


def _poll_impor(klien, ds, maks=200):
    k = {}
    for _ in range(maks):
        k = klien.get(f"/api/aksi/impor/kemajuan?ds={ds}").json()
        if k.get("selesai") or k.get("galat"):
            break
        time.sleep(0.05)
    return k


def test_rute_impor_zip_bangun_versi(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-imp", "video")
    data = _zip_dataset({"shoot": 2, "pass": 2}, {"shoot": 1, "pass": 1})

    r = klien.post("/api/aksi/impor?ds=aksi-imp", content=data).json()
    assert r.get("ok") and r.get("mulai"), r
    k = _poll_impor(klien, "aksi-imp")
    assert k.get("selesai") and not k.get("galat"), k
    assert k.get("nomor") == 1

    proj = lingkungan["ruang"] / "aksi-imp"
    vd = proj / ".versi" / "v1"
    assert (vd / "aksi.yaml").is_file()
    assert len(list((vd / "train" / "shoot").glob("*.mp4"))) == 2
    assert len(list((vd / "valid" / "pass").glob("*.mp4"))) == 1
    assert not (proj / "_impor_tmp").exists()        # staging + zip dibersihkan
    aksi = [v for v in versi.daftar(proj)
            if (v.get("hasil") or {}).get("jenis") == "aksi"]
    assert len(aksi) == 1 and aksi[0]["kelas"] == 2


def test_rute_impor_tolak_projek_image(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "img-imp", "image")
    data = _zip_dataset({"a": 2, "b": 2}, {"a": 1, "b": 1})
    r = klien.post("/api/aksi/impor?ds=img-imp", content=data).json()
    assert r.get("ok") is False, r


# ──────────────────────── rute impor dari PATH server (admin) ──────────────
def _set_admin(lingkungan, nama, nilai):
    p = lingkungan["users"]
    d = json.loads(p.read_text())
    d[nama]["admin"] = nilai
    p.write_text(json.dumps(d))


def test_rute_impor_path_admin_folder(klien, lingkungan, tmp_path):
    _set_admin(lingkungan, "paul", True)
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-path", "video")
    _dataset(tmp_path / "srv", {"shoot": 2, "pass": 2}, {"shoot": 1, "pass": 1})

    r = klien.post("/api/aksi/impor-path?ds=aksi-path",
                   json={"path": str(tmp_path / "srv")}).json()
    assert r.get("ok") and r.get("mulai"), r
    k = _poll_impor(klien, "aksi-path")
    assert k.get("selesai") and not k.get("galat"), k
    vd = lingkungan["ruang"] / "aksi-path" / ".versi" / "v1"
    assert (vd / "aksi.yaml").is_file()
    assert len(list((vd / "train" / "shoot").glob("*.mp4"))) == 2


def test_rute_impor_path_tolak_non_admin(klien, lingkungan, tmp_path):
    _set_admin(lingkungan, "paul", False)
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-path2", "video")
    _dataset(tmp_path / "srv2", {"a": 2, "b": 2}, {"a": 1, "b": 1})
    r = klien.post("/api/aksi/impor-path?ds=aksi-path2",
                   json={"path": str(tmp_path / "srv2")}).json()
    assert r.get("ok") is False and "admin" in r.get("error", ""), r
    assert not (lingkungan["ruang"] / "aksi-path2" / ".versi" / "v1").exists()


def test_rute_impor_path_dari_zip_server(klien, lingkungan, tmp_path):
    _set_admin(lingkungan, "paul", True)
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-path3", "video")
    zp = tmp_path / "ds.zip"
    zp.write_bytes(_zip_dataset({"x": 2, "y": 2}, {"x": 1, "y": 1}))
    r = klien.post("/api/aksi/impor-path?ds=aksi-path3",
                   json={"path": str(zp)}).json()
    assert r.get("ok"), r
    k = _poll_impor(klien, "aksi-path3")
    assert k.get("selesai") and not k.get("galat"), k
    assert k.get("nomor") == 1


def test_rute_impor_path_tunjuk_folder_train(klien, lingkungan, tmp_path):
    # Menunjuk langsung ke folder 'train' -> pakai induknya (toleran).
    _set_admin(lingkungan, "paul", True)
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-path4", "video")
    _dataset(tmp_path / "srv4", {"a": 2, "b": 2}, {"a": 1, "b": 1})
    r = klien.post("/api/aksi/impor-path?ds=aksi-path4",
                   json={"path": str(tmp_path / "srv4" / "train")}).json()
    assert r.get("ok"), r
    k = _poll_impor(klien, "aksi-path4")
    assert k.get("selesai") and not k.get("galat"), k


def test_rute_impor_path_tak_ada(klien, lingkungan):
    _set_admin(lingkungan, "paul", True)
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "aksi-path5", "video")
    r = klien.post("/api/aksi/impor-path?ds=aksi-path5",
                   json={"path": "/tidak/ada/di/mana/pun"}).json()
    assert r.get("ok") is False and "tak ada" in r.get("error", ""), r


# ──────────── halaman Unggah data: panel "Ambil dari folder" sadar-jenis ────
def test_unggah_video_admin_panel_klip(klien, lingkungan):
    _set_admin(lingkungan, "paul", True)
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-unggah", "video")
    html = klien.get("/unggah?ds=vid-unggah").text
    assert 'data-jenis="video"' in html
    assert "Ambil dataset klip dari folder" in html   # mode video
    assert "Muat dari server" in html


def test_unggah_video_non_admin_sembunyikan_panel(klien, lingkungan):
    _set_admin(lingkungan, "paul", False)
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-unggah2", "video")
    html = klien.get("/unggah?ds=vid-unggah2").text
    # impor path server = khusus admin -> panel server disembunyikan untuk video
    assert "Ambil dataset klip dari folder" not in html


def test_unggah_image_panel_tetap_lama(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "img-unggah", "image")
    html = klien.get("/unggah?ds=img-unggah").text
    assert 'data-jenis="image"' in html
    assert "Ambil dari folder di server" in html       # jalur gambar lama utuh


# ──────────────────── tab VERSI klip sendiri (/aksi/versi) ─────────────────
def test_aksi_versi_halaman_video(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-ver", "video")
    r = klien.get("/aksi/versi?ds=vid-ver")
    assert r.status_code == 200, r.text[:200]
    html = r.text
    assert "Versi dataset klip" in html
    assert 'id="vs-mulai"' in html and 'id="wz"' in html         # wizard buat versi
    assert "ak-impor-file" in html                               # impor .zip
    assert "akv-awal" in html                                    # data awal
    assert "/aksi/versi?ds=" in html                             # sidebar Versi -> tab video


def test_aksi_versi_image_dialihkan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "img-ver", "image")
    r = klien.get("/aksi/versi?ds=img-ver", follow_redirects=False)
    assert r.status_code == 303
    assert "/versi?ds=img-ver" in r.headers.get("location", "")


def test_aksi_labeling_tak_lagi_punya_kontrol_versi(klien, lingkungan):
    """Halaman Label Aksi tak lagi menampung kontrol versi/impor/training
    (pindah ke tab Versi + Training) — tinggal menunjuk ke sana."""
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-lbl", "video")
    html = klien.get("/aksi?ds=vid-lbl").text
    assert "ak-buat-versi" not in html and "ak-impor-file" not in html
    assert "ak-tr-backend" not in html and "ak-latih" not in html
    assert "/aksi/versi?ds=" in html and "/aksi/latih?ds=" in html  # pointer ke tab


# ──────────────────── tab TRAINING klip sendiri (/aksi/latih) ──────────────
def test_aksi_latih_halaman_video(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-lat", "video")
    r = klien.get("/aksi/latih?ds=vid-lat")
    assert r.status_code == 200, r.text[:200]
    html = r.text
    assert "Training model aksi" in html
    assert "ak-tr-backend" in html and 'id="ak-tr-jalan"' in html   # form + kartu run
    assert 'id="tr-mesin"' in html                                  # bar mesin GPU/RAM
    assert "akl-awal" in html
    assert "/aksi/latih?ds=" in html                 # sidebar Training -> tab video


def test_aksi_latih_image_dialihkan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "img-lat", "image")
    r = klien.get("/aksi/latih?ds=img-lat", follow_redirects=False)
    assert r.status_code == 303
    assert "/latih?ds=img-lat" in r.headers.get("location", "")


# ──────────────────── tab DATASET klip sendiri (/aksi/dataset) ─────────────
def test_aksi_dataset_halaman_video(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-ds", "video")
    html = klien.get("/aksi/dataset?ds=vid-ds").text
    assert "Dataset klip" in html
    assert "akd-grid" in html and "akd-awal" in html
    assert "/aksi/dataset?ds=" in html               # sidebar Dataset -> tab video


def test_aksi_dataset_image_dialihkan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "img-ds", "image")
    r = klien.get("/aksi/dataset?ds=img-ds", follow_redirects=False)
    assert r.status_code == 303
    assert "/?ds=img-ds" in r.headers.get("location", "")


def test_aksi_thumb_404_aman(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "vid-th", "video")
    # klip tak ada -> 404 (kartu pakai placeholder)
    assert klien.get("/aksi/thumb?ds=vid-th&rel=klip/b1/tak-ada.mp4").status_code == 404
    # path keluar klip/ ditolak -> 404
    assert klien.get("/aksi/thumb?ds=vid-th&rel=../rahasia.mp4").status_code == 404


def test_aksi_grid_labeling_video(klien, lingkungan):
    """Label Aksi kini GRID + overlay, bukan pemutar berurutan."""
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi-grid", kelas=("shoot", "pass"))
    html = klien.get("/aksi?ds=aksi-grid").text
    assert "ak-grid" in html                       # grid klip
    assert 'id="ak-lbl"' in html                   # overlay label
    assert "ak-saring" in html and "ak-dsf" in html  # filter belum/sudah + dataset
    assert "ak-awal" in html and "Label Aksi" in html
    assert "shoot" in html                          # kelas tertanam di data awal


def test_aksi_thumb_poster_klip_nyata(klien, lingkungan):
    """Poster nyata via ffmpeg (bukan jalur 404): klip asli -> JPG > 0 byte."""
    import shutil
    ref = Path("/home/paul/computer-vision/campus-league/basketball/dataset/"
               "01-action/Video-MAE/action-v12/v2.1-dan-spacejam-aug/train/dribble")
    src = next(iter(ref.glob("*.mp4")), None) if ref.is_dir() else None
    if src is None:
        pytest.skip("klip referensi tak tersedia")
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "th-real", kelas=("dribble",))
    proj = lingkungan["ruang"] / "th-real"
    rel = "klip/b1/" + src.name
    dst = proj / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    r = klien.get(f"/aksi/thumb?ds=th-real&rel={rel}")
    assert r.status_code == 200, r.text[:120]
    assert r.headers.get("content-type") == "image/jpeg"
    assert len(r.content) > 500                    # poster nyata, bukan kosong


def test_aksi_keluarkan_dari_dataset(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "aksi-ds", kelas=("shoot", "pass"))
    proj = lingkungan["ruang"] / "aksi-ds"
    rel = "klip/b1/shoot_vid_t0001.mp4"
    _klip(proj, rel)
    klien.post("/api/aksi/label", params={"ds": "aksi-ds", "klip": rel, "label": "shoot"})
    assert klien.post("/api/aksi/dataset?ds=aksi-ds", json={"klip": [rel]}).json()["ok"]
    assert tugas.sudah_dimasukkan(tugas.baca(proj, "paul"), rel)
    # keluarkan
    r = klien.post("/api/aksi/dataset/keluarkan?ds=aksi-ds", json={"klip": [rel]}).json()
    assert r["ok"] and r.get("dikeluarkan", 0) >= 1, r
    assert not tugas.sudah_dimasukkan(tugas.baca(proj, "paul"), rel)


# ============================================ Fase 4b: papan + bagi tugas klip

def test_aksi_papan_halaman_video(klien, lingkungan):
    """Papan anotasi klip (paritas /anotasi): tiga kolom + sub-nav."""
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "vid-papan", kelas=("shoot", "pass"))
    html = klien.get("/aksi/papan?ds=vid-papan").text
    assert "an-papan" in html                          # tiga kolom
    assert "Papan anotasi klip" in html
    assert "aksi_papan.js" in html
    assert "aksi-subnav" in html                        # strip sub-tab Grid/Papan/Bagi
    assert "Belum ditugaskan" in html and "Dikerjakan" in html


def test_aksi_papan_image_dialihkan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "img-papan", "image")
    r = klien.get("/aksi/papan?ds=img-papan", follow_redirects=False)
    assert r.status_code == 303
    assert "/anotasi?ds=img-papan" in r.headers.get("location", "")


def test_aksi_bagi_halaman_video(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "vid-bagi", kelas=("shoot",))
    proj = lingkungan["ruang"] / "vid-bagi"
    _klip(proj, "klip/b1/shoot_vid_t0001.mp4")
    _klip(proj, "klip/b1/shoot_vid_t0002.mp4")
    html = klien.get("/aksi/bagi?ds=vid-bagi").text
    assert "bg-isi" in html and "aksi_bagi.js" in html
    assert "aksi-subnav" in html
    assert 'id="bg-jumlah"' in html and "menunggu dibagi" in html
    # ubin memakai poster DIAM (/aksi/thumb), bukan <video>
    assert "/aksi/thumb?ds=" in html


def test_aksi_bagi_image_dialihkan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_projek(klien, "img-bagi", "image")
    r = klien.get("/aksi/bagi?ds=img-bagi", follow_redirects=False)
    assert r.status_code == 303
    assert "/bagi?ds=img-bagi" in r.headers.get("location", "")


def test_aksi_bagi_assign_dan_papan_kartu(klien, lingkungan):
    """Bagi klip ke anggota -> job dibuat dengan kunci klip -> papan tampil."""
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "vid-asg", kelas=("shoot",))
    proj = lingkungan["ruang"] / "vid-asg"
    for i in range(1, 5):
        _klip(proj, f"klip/b1/shoot_vid_t000{i}.mp4")
    r = klien.post("/api/aksi/bagi?ds=vid-asg",
                   json={"pelabel": "anggi", "n": 2, "acak": False}).json()
    assert r["ok"] and r["n"] == 2 and r["pelabel"] == "anggi", r
    data = tugas.baca(proj, "paul")
    jobs = [t for t in data["tugas"].values() if t["pelabel"] == "anggi"]
    assert jobs and len(jobs[0]["gambar"]) == 2
    assert all(k.startswith("klip/") for k in jobs[0]["gambar"])
    # anggi otomatis jadi anggota (pelabel), dan kartunya muncul di papan
    assert "anggi" in data["anggota"]
    assert "anggi" in klien.get("/aksi/papan?ds=vid-asg").text


def test_aksi_bagi_pelabel_tak_terdaftar_ditolak(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "vid-asg2", kelas=("shoot",))
    proj = lingkungan["ruang"] / "vid-asg2"
    _klip(proj, "klip/b1/shoot_vid_t0001.mp4")
    r = klien.post("/api/aksi/bagi?ds=vid-asg2",
                   json={"pelabel": "hantu", "n": 1}).json()
    assert not r["ok"] and "terdaftar" in r["error"], r


def test_aksi_bagi_batch_mempersempit(klien, lingkungan):
    """n-based + batch hanya mengambil klip batch itu."""
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "vid-asg3", kelas=("shoot",))
    proj = lingkungan["ruang"] / "vid-asg3"
    _klip(proj, "klip/pagi/shoot_vid_t0001.mp4")
    _klip(proj, "klip/sore/shoot_vid_t0002.mp4")
    r = klien.post("/api/aksi/bagi?ds=vid-asg3",
                   json={"pelabel": "anggi", "n": 99, "batch": "pagi"}).json()
    assert r["ok"] and r["n"] == 1, r
    data = tugas.baca(proj, "paul")
    kunci = [k for t in data["tugas"].values() for k in t["gambar"]]
    assert kunci == ["klip/pagi/shoot_vid_t0001.mp4"], kunci


def test_calon_batch_dari_klip_video(klien, lingkungan):
    """/api/tugas/calon pada projek VIDEO mengambil batch dari klip_scan, bukan
    tag gambar yang memang kosong (scope Labeler spesifik klip)."""
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "vid-calon", kelas=("shoot",))
    proj = lingkungan["ruang"] / "vid-calon"
    _klip(proj, "klip/pagi/shoot_vid_t0001.mp4")
    _klip(proj, "klip/sore/shoot_vid_t0002.mp4")
    klien.get("/aksi/bagi?ds=vid-calon")                # buka -> sess.src terisi
    j = klien.get("/api/tugas/calon").json()
    assert j["ok"], j
    assert set(j["batch_tersedia"]) >= {"pagi", "sore"}, j["batch_tersedia"]


# ======================================= paginasi + saring server-side grid

def test_keterangan_paginasi_server(klien, lingkungan):
    """Grid klip dipaginasi DI SERVER: `per` memotong, angka ringkas tetap total."""
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "vid-pag", kelas=("shoot",))
    proj = lingkungan["ruang"] / "vid-pag"
    for i in range(1, 14):                      # 13 klip
        _klip(proj, f"klip/b1/shoot_vid_t{i:04d}.mp4")
    r = klien.get("/api/aksi/keterangan?ds=vid-pag&per=6&hal=1").json()
    assert r["ok"], r
    assert len(r["klips"]) == 6                 # satu halaman saja
    assert r["n_total"] == 13 and r["n_tampil"] == 13
    assert r["per"] == 6 and r["n_hal"] == 3 and r["hal"] == 1
    # halaman terakhir = sisa (13 = 6+6+1)
    r3 = klien.get("/api/aksi/keterangan?ds=vid-pag&per=6&hal=3").json()
    assert len(r3["klips"]) == 1 and r3["hal"] == 3
    # hal di luar jangkauan dijepit ke halaman terakhir
    r9 = klien.get("/api/aksi/keterangan?ds=vid-pag&per=6&hal=9").json()
    assert r9["hal"] == 3
    # halaman tak tumpang-tindih & meliputi semua
    rels = set()
    for h in (1, 2, 3):
        rels |= {k["rel"] for k in
                 klien.get(f"/api/aksi/keterangan?ds=vid-pag&per=6&hal={h}").json()["klips"]}
    assert len(rels) == 13


def test_keterangan_saring_dan_dsf_server(klien, lingkungan):
    """saring (belum/sudah) + dsf (di/belum) + kelas disaring di server."""
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "vid-f", kelas=("shoot", "pass"))
    proj = lingkungan["ruang"] / "vid-f"
    for rel in ("klip/b1/shoot_vid_t0001.mp4", "klip/b1/pass_vid_t0002.mp4",
                "klip/b1/x_vid_t0003.mp4"):
        _klip(proj, rel)
    klien.post("/api/aksi/label", params={"ds": "vid-f",
               "klip": "klip/b1/shoot_vid_t0001.mp4", "label": "shoot"})
    klien.post("/api/aksi/label", params={"ds": "vid-f",
               "klip": "klip/b1/pass_vid_t0002.mp4", "label": "pass"})
    klien.post("/api/aksi/dataset?ds=vid-f",
               json={"klip": ["klip/b1/shoot_vid_t0001.mp4"]})

    semua = klien.get("/api/aksi/keterangan?ds=vid-f").json()
    assert semua["n_total"] == 3 and semua["n_label"] == 2 and semua["n_dataset"] == 1
    # hitung-kelas (konteks semua) = kelas yang terpakai
    assert semua["kelas_hitung"] == {"pass": 1, "shoot": 1}

    belum = klien.get("/api/aksi/keterangan?ds=vid-f&saring=belum").json()
    assert {k["rel"] for k in belum["klips"]} == {"klip/b1/x_vid_t0003.mp4"}
    sudah = klien.get("/api/aksi/keterangan?ds=vid-f&saring=sudah").json()
    assert len(sudah["klips"]) == 2
    di = klien.get("/api/aksi/keterangan?ds=vid-f&dsf=di").json()
    assert {k["rel"] for k in di["klips"]} == {"klip/b1/shoot_vid_t0001.mp4"}
    # hitung-kelas pada tab dataset = kelas yang ADA di dataset saja
    assert di["kelas_hitung"] == {"shoot": 1}
    kelas = klien.get("/api/aksi/keterangan?ds=vid-f&kelas=pass").json()
    assert {k["rel"] for k in kelas["klips"]} == {"klip/b1/pass_vid_t0002.mp4"}


def test_dataset_semua_berlabel_server(klien, lingkungan):
    """'Tambah semua berlabel' dihitung di server (peramban dipaginasi)."""
    masuk(klien, "paul", PW_PAUL)
    _buat_video_aksi(klien, "vid-sb", kelas=("shoot",))
    proj = lingkungan["ruang"] / "vid-sb"
    for i in range(1, 4):
        rel = f"klip/b1/shoot_vid_t{i:04d}.mp4"
        _klip(proj, rel)
        if i < 3:                               # 2 berlabel, 1 belum
            klien.post("/api/aksi/label", params={"ds": "vid-sb", "klip": rel,
                                                  "label": "shoot"})
    r = klien.post("/api/aksi/dataset?ds=vid-sb",
                   json={"semua_berlabel": True}).json()
    assert r["ok"] and r.get("ditambah", 0) == 2, r
    data = tugas.baca(proj, "paul")
    masuk_ds = sum(1 for i in range(1, 4)
                   if tugas.sudah_dimasukkan(data, f"klip/b1/shoot_vid_t{i:04d}.mp4"))
    assert masuk_ds == 2
    # tak ada lagi yang berlabel di luar dataset -> tolak sopan
    r2 = klien.post("/api/aksi/dataset?ds=vid-sb",
                    json={"semua_berlabel": True}).json()
    assert not r2["ok"] and "belum masuk dataset" in r2["error"]
