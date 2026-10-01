"""
Uji kelas aksi tingkat projek + label per-klip (Langkah 4).

Tiga hal yang dijaga, padanan test_skeleton.py untuk projek VIDEO aksi:
  1. tugas._sah_aksi menyaring konsisten (nama unik, target merge wajib ada,
     kelas negatif wajib salah satu kelas), set_aksi/aksi_aktif round-trip, dan
     jumlah kelas BEBAS per projek. Plus rute: pemilik set, anggota baca.
  2. klip_tag (.klip.json): label satu klip tersimpan & terbaca lagi.
  3. kunci klip menembus RBAC & kurasi dataset yang SUDAH ada tanpa kode baru:
     tugas.di_dataset/masukkan/keluarkan key-agnostic, boleh_labeli dengan kunci
     klip + batch, dan klip_scan melewati klip/_ditolak.
"""
from __future__ import annotations

from pathlib import Path

from app.services import klip_scan, klip_tag, tugas


def _projek(tmp_path, nama="p"):
    d = tmp_path / nama
    d.mkdir(parents=True, exist_ok=True)
    return d


# ==================================================== _sah_aksi

def test_sah_aksi_menyaring_konsisten():
    raw = {
        "kelas": ["shoot", "pass", "shoot", "", "  dribble  "],  # dup & kosong buang
        "merge": {"lempar": "shoot",        # target ada -> dipakai
                  "oper": "tak_ada",        # target bukan kelas -> dibuang
                  "shoot": "shoot"},        # sumber==target -> dibuang
        "warna": ["#f00", "#0f0", "#00f", "#fff"],   # dipotong ke K
        "negatif": "diam",                  # bukan kelas -> dikosongkan
    }
    ak = tugas._sah_aksi(raw)
    assert ak["kelas"] == ["shoot", "pass", "dribble"]
    assert ak["merge"] == {"lempar": "shoot"}
    assert len(ak["warna"]) == 3
    assert ak["negatif"] == ""


def test_sah_aksi_negatif_kelas_dikenal_dipertahankan():
    ak = tugas._sah_aksi({"kelas": ["shoot", "diam"], "negatif": "diam"})
    assert ak["negatif"] == "diam"


def test_sah_aksi_data_rusak_jadi_kosong():
    assert tugas._sah_aksi(None)["kelas"] == []
    assert tugas._sah_aksi("bukan dict")["kelas"] == []
    assert tugas._sah_aksi({"kelas": "bukan list"})["kelas"] == []
    assert tugas._sah_aksi({"kelas": ["a"], "merge": "bukan dict"})["merge"] == {}


# ==================================================== set_aksi / aksi_aktif

def test_set_aksi_round_trip_dan_persist(tmp_path):
    d = _projek(tmp_path)
    r = tugas.set_aksi(d, {"kelas": ["shoot", "pass", "dribble"],
                           "merge": {"lempar": "shoot"}, "negatif": "pass"},
                       pemilik="own")
    assert r["ok"]
    data = tugas.baca(d, "own")
    assert data["aksi"]["kelas"] == ["shoot", "pass", "dribble"]
    assert data["aksi"]["merge"] == {"lempar": "shoot"}
    assert data["aksi"]["negatif"] == "pass"
    assert tugas.aksi_aktif(data) is True


def test_aksi_tak_aktif_bawaan(tmp_path):
    d = _projek(tmp_path)
    assert tugas.aksi_aktif(tugas.baca(d, "own")) is False


def test_jumlah_kelas_bebas_per_projek(tmp_path):
    """Inti: tiap projek punya daftar kelasnya sendiri — bukan daftar tetap."""
    basket = _projek(tmp_path, "basket")
    daur = _projek(tmp_path, "daur")
    tugas.set_aksi(basket, {"kelas": ["shoot", "pass", "dribble",
                                      "block", "steal", "jump"]}, pemilik="own")
    tugas.set_aksi(daur, {"kelas": ["buang", "pilah"]}, pemilik="own")
    assert len(tugas.baca(basket, "own")["aksi"]["kelas"]) == 6
    assert len(tugas.baca(daur, "own")["aksi"]["kelas"]) == 2


# ==================================================== rute HTTP

def test_rute_aksi_pemilik_set_anggota_baca(klien, aplikasi, lingkungan):
    import pathlib

    from conftest import klien_baru
    from tests.test_data import masuk, PW_ANGGI, PW_PAUL
    from tests.test_projek import _projek as buat_projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = buat_projek(ruang, "aksi-uji", n=2, label=False)
    klien.post(f"/setsrc?path={d}")
    tugas.undang(d, "paul", "anggi", peran="pelabel", akses="semua")

    # Pemilik menyetel daftar kelas.
    r = klien.post("/api/tugas/aksi?ds=aksi-uji", json={"aksi": {
        "kelas": ["shoot", "pass"], "negatif": "pass"}}).json()
    assert r["ok"] and r["aksi"]["kelas"] == ["shoot", "pass"]

    # Anggota (pelabel) boleh MEMBACA (butuh daftar kelas untuk melabeli klip).
    anggi = klien_baru(aplikasi, "anggi", PW_ANGGI)
    anggi.get("/?ds=paul/aksi-uji")
    g = anggi.get("/api/tugas/aksi?ds=paul/aksi-uji").json()
    assert g["ok"] and g["aksi"]["kelas"] == ["shoot", "pass"]
    assert g["boleh_ubah"] is False

    # Tapi tak boleh MENGUBAH.
    r2 = anggi.post("/api/tugas/aksi?ds=paul/aksi-uji",
                    json={"aksi": {"kelas": ["x"]}}).json()
    assert r2["ok"] is False and "pemilik" in r2["error"]


# ==================================================== label per-klip (.klip.json)

def test_klip_label_round_trip(tmp_path):
    d = _projek(tmp_path)
    rel = "klip/b1/shoot_vid_t0005.mp4"
    r = klip_tag.set_label(d, rel, "shoot", batch="b1", srcid="vid")
    assert r["ok"] and r["berlabel"] is True

    got = klip_tag.untuk(d, rel)
    assert got["label"] == "shoot" and got["batch"] == "b1"
    assert got["srcid"] == "vid"
    assert klip_tag.berlabel(klip_tag.baca(d), rel) is True


def test_klip_label_kosong_lepas_tapi_batch_bertahan(tmp_path):
    d = _projek(tmp_path)
    rel = "klip/b1/x.mp4"
    klip_tag.set_label(d, rel, "shoot", batch="b1", srcid="vid")
    klip_tag.set_label(d, rel, "")                  # lepas labelnya
    got = klip_tag.untuk(d, rel)
    assert got["label"] == ""                       # belum dilabeli lagi
    assert got["batch"] == "b1" and got["srcid"] == "vid"   # metadata asal tetap


def test_kunci_klip_relatif(tmp_path):
    d = _projek(tmp_path)
    kp = d / "klip" / "b1" / "x.mp4"
    assert klip_tag.kunci_klip(d, kp) == "klip/b1/x.mp4"


# ==================================================== kunci klip menembus RBAC/dataset

def test_kunci_klip_lewat_dataset_tugas(tmp_path):
    """tugas.di_dataset/masukkan/keluarkan key-agnostic: kunci klip lewat apa
    adanya, tanpa kode dataset khusus klip."""
    d = _projek(tmp_path)
    k = "klip/b1/shoot_vid_t0005.mp4"
    # Sebelum kurasi: semua dianggap di dataset (warisan).
    assert tugas.di_dataset(tugas.baca(d, "own"), k) is True
    tugas.masukkan(d, [k], pemilik="own")
    data = tugas.baca(d, "own")
    assert tugas.di_dataset(data, k) is True
    assert tugas.sudah_dimasukkan(data, k) is True
    tugas.keluarkan(d, [k], pemilik="own")
    assert tugas.sudah_dimasukkan(tugas.baca(d, "own"), k) is False


def test_boleh_labeli_kunci_klip_per_scope(tmp_path):
    """RBAC klip = RBAC gambar: boleh_labeli dengan kunci klip + batch, tanpa
    kode baru. Owner/editor/labeler-semua boleh semua; labeler-spesifik hanya
    batch dalam scope-nya."""
    d = _projek(tmp_path)
    kb1 = "klip/b1/x.mp4"
    kb2 = "klip/b2/y.mp4"
    tugas.undang(d, "own", "ed", peran="editor")
    tugas.undang(d, "own", "labs", peran="pelabel", akses="semua")
    tugas.undang(d, "own", "labx", peran="pelabel", akses="spesifik", batch=["b1"])
    data = tugas.baca(d, "own")

    # Owner & Editor: semua klip.
    assert tugas.boleh_labeli(data, "own", kb1, "b1") is True
    assert tugas.boleh_labeli(data, "ed", kb2, "b2") is True
    # Labeler menyeluruh: semua klip.
    assert tugas.boleh_labeli(data, "labs", kb2, "b2") is True
    # Labeler spesifik: hanya batch dalam scope.
    assert tugas.boleh_labeli(data, "labx", kb1, "b1") is True
    assert tugas.boleh_labeli(data, "labx", kb2, "b2") is False
    # Bukan anggota: tidak boleh.
    assert tugas.boleh_labeli(data, "orang-luar", kb1, "b1") is False


# ==================================================== klip_scan

def _sentuh(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def test_klip_scan_lewati_ditolak(tmp_path):
    d = _projek(tmp_path)
    _sentuh(d / "klip" / "b1" / "shoot_vid_t0005.mp4")
    _sentuh(d / "klip" / "b1" / "pass_vid_t0010.mp4")
    _sentuh(d / "klip" / "_ditolak" / "buang.mp4")       # harus dilewati
    _sentuh(d / "klip" / "_bad" / "cacat.mp4")           # harus dilewati
    _sentuh(d / "klip" / "b1" / "catatan.txt")           # bukan video -> dilewati

    klip_tag.set_label(d, "klip/b1/shoot_vid_t0005.mp4", "shoot", batch="b1")

    r = klip_scan.pindai(d)
    assert r["semua"] == {"klip/b1/shoot_vid_t0005.mp4",
                          "klip/b1/pass_vid_t0010.mp4"}
    assert "klip/_ditolak/buang.mp4" not in r["semua"]
    assert "klip/_bad/cacat.mp4" not in r["semua"]
    # Label & batch & srcid ikut terbaca.
    assert r["berlabel"] == {"klip/b1/shoot_vid_t0005.mp4"}
    assert r["batch_dari"]["klip/b1/pass_vid_t0010.mp4"] == "b1"  # dari tata letak
    byrel = {it["rel"]: it for it in r["items"]}
    assert byrel["klip/b1/shoot_vid_t0005.mp4"]["label"] == "shoot"
    assert byrel["klip/b1/shoot_vid_t0005.mp4"]["srcid"] == "vid"  # ditebak dari nama


def test_klip_scan_projek_tanpa_klip(tmp_path):
    d = _projek(tmp_path)
    r = klip_scan.pindai(d)
    assert r["semua"] == set() and r["items"] == []
