"""
Auto-label batch ZERO-SHOT (YOLO-World/SAM 3 lewat autolabel.dari_teks).

Yang dijaga: format tulis sama dengan simpan manual (lewat prelabel.tulis_prediksi),
gambar berlabel manusia TAK ditimpa, gambar tanpa deteksi TIDAK jadi latar, dan
auto-label DITOLAK saat training lokal aktif.
"""
from __future__ import annotations

import json

import pytest

from conftest import buat_dataset, PW_PAUL
from app.services import autolabel, autolabel_zero, tugas


# ---- temuan palsu dari autolabel.dari_teks ----------------------------------
def _temuan_kotak(*_a, **_k):
    """Satu kotak kelas 'botol' per gambar."""
    return [autolabel.Temuan(label="botol", points=[[10, 10], [50, 50]],
                             bbox=(10, 10, 50, 50), skor=0.9, shape_type="rectangle")]


def _temuan_poligon(*_a, **_k):
    return [autolabel.Temuan(label="botol",
                             points=[[10, 10], [50, 10], [50, 50], [10, 50]],
                             bbox=(10, 10, 50, 50), skor=0.8, shape_type="polygon")]


def _temuan_kosong(*_a, **_k):
    raise autolabel.TidakAdaObjek("tak ada yang cocok")


# ---- konversi ---------------------------------------------------------------
def test_bentuk_dari_temuan_kotak_dan_poligon():
    sh = autolabel_zero._bentuk_dari_temuan(_temuan_kotak())
    assert len(sh) == 1 and sh[0]["shape_type"] == "rectangle"
    assert sh[0]["label"] == "botol"
    assert sh[0]["points"] == [[10.0, 10.0], [50.0, 50.0]]

    sh = autolabel_zero._bentuk_dari_temuan(_temuan_poligon())
    assert len(sh) == 1 and sh[0]["shape_type"] == "polygon"
    assert len(sh[0]["points"]) == 4


def test_bentuk_buang_yang_cacat():
    # poligon < 3 titik dan kotak < 2 titik dibuang (bukan error).
    t = [autolabel.Temuan("a", [[1, 1]], (1, 1, 2, 2), 0.5, "polygon"),
         autolabel.Temuan("b", [[1, 1]], (1, 1, 2, 2), 0.5, "rectangle")]
    assert autolabel_zero._bentuk_dari_temuan(t) == []


def test_pilih_model():
    assert autolabel_zero.pilih_model("poligon") == "sam3:latest"
    assert autolabel_zero.pilih_model("kotak") == "yoloworld:latest"
    assert autolabel_zero.pilih_model("") == "yoloworld:latest"


# ---- loop jalankan ----------------------------------------------------------
def _siapkan(tmp_path, monkeypatch, n_img, n_ann, dari_teks, latih_aktif=False):
    d = buat_dataset(tmp_path / "proj", n_img, n_ann)
    nama = sorted(p.name for p in d.glob("*.jpg"))
    tugas.tugaskan(d, "darma", "darma", nama)
    data = tugas.baca(d, "darma")
    tid = next(iter(data["tugas"]))

    monkeypatch.setattr(autolabel_zero.autolabel, "dari_teks", dari_teks)
    monkeypatch.setattr(autolabel_zero.gpu_sinyal, "latih_aktif",
                        lambda: latih_aktif)
    monkeypatch.setattr(tugas, "baca_projek", lambda ds, u: data)
    monkeypatch.setattr(tugas, "tolak_tulis", lambda ds, akun, img: None)
    return d, tid


def test_jalankan_isi_prediksi(tmp_path, monkeypatch):
    d, tid = _siapkan(tmp_path, monkeypatch, 3, 0, _temuan_kotak)
    hasil = autolabel_zero.jalankan(d, tmp_path, tid, "darma", 1000, 0.1,
                                    "yoloworld:latest", ["botol"])
    assert hasil["total"] == 3 and hasil["diproses"] == 3 and hasil["objek"] == 3
    for ip in d.glob("*.jpg"):
        jp = ip.with_suffix(".json")
        assert jp.exists(), ip.name
        sh = json.loads(jp.read_text())["shapes"]
        assert len(sh) == 1 and sh[0]["label"] == "botol"
        assert sh[0]["shape_type"] == "rectangle"


def test_jalankan_tanpa_deteksi_tak_jadi_latar(tmp_path, monkeypatch):
    d, tid = _siapkan(tmp_path, monkeypatch, 3, 0, _temuan_kosong)
    hasil = autolabel_zero.jalankan(d, tmp_path, tid, "darma", 1000, 0.1,
                                    "yoloworld:latest", ["botol"])
    assert hasil["diproses"] == 0 and hasil["dilewati"] == 3
    assert not [p for p in d.glob("*.json") if not p.name.startswith(".")]


def test_jalankan_tak_menimpa_berlabel(tmp_path, monkeypatch):
    d, tid = _siapkan(tmp_path, monkeypatch, 3, 1, _temuan_kotak)
    berlabel = sorted(d.glob("*.jpg"))[0].with_suffix(".json")
    sebelum = berlabel.read_text()
    hasil = autolabel_zero.jalankan(d, tmp_path, tid, "darma", 1000, 0.1,
                                    "yoloworld:latest", ["botol"])
    assert hasil["diproses"] == 2
    assert berlabel.read_text() == sebelum
    assert json.loads(berlabel.read_text())["shapes"][0]["shape_type"] == "polygon"


def test_jalankan_batas(tmp_path, monkeypatch):
    d, tid = _siapkan(tmp_path, monkeypatch, 5, 0, _temuan_kotak)
    hasil = autolabel_zero.jalankan(d, tmp_path, tid, "darma", 2, 0.1,
                                    "yoloworld:latest", ["botol"])
    assert hasil["total"] == 2 and hasil["diproses"] == 2


def test_jalankan_tandai_perlu_dicek(tmp_path, monkeypatch):
    d, tid = _siapkan(tmp_path, monkeypatch, 2, 0, _temuan_kotak)
    autolabel_zero.jalankan(d, tmp_path, tid, "darma", 1000, 0.1,
                            "yoloworld:latest", ["botol"])
    for ip in d.glob("*.jpg"):
        j = json.loads(ip.with_suffix(".json").read_text())
        assert j.get("perlu_dicek") is True          # hasil AI -> perlu dicek


def test_jalankan_batch_scope(tmp_path, monkeypatch):
    # buat_dataset tak memberi tag batch -> semua gambar batch "" (tanpa nama).
    d, tid = _siapkan(tmp_path, monkeypatch, 3, 0, _temuan_kotak)
    hasil = autolabel_zero.jalankan(d, tmp_path, "", "darma", 1000, 0.1,
                                    "yoloworld:latest", ["botol"], batch="")
    assert hasil["total"] == 3 and hasil["diproses"] == 3
    # Batch scope tak butuh job: semua gambar "belum" di batch terisi.
    for ip in d.glob("*.jpg"):
        assert ip.with_suffix(".json").exists()


def test_gambar_belum_batch_hanya_yang_belum(tmp_path, monkeypatch):
    from app.services import scanner
    d, tid = _siapkan(tmp_path, monkeypatch, 3, 1, _temuan_kotak)  # 1 sudah berlabel
    items, _ = scanner.scan(d)
    belum = tugas.gambar_belum_batch(items, d, "")
    assert len(belum) == 2                           # yang berlabel tak ikut


def test_jalankan_ditolak_saat_training(tmp_path, monkeypatch):
    d, tid = _siapkan(tmp_path, monkeypatch, 3, 0, _temuan_kotak, latih_aktif=True)
    with pytest.raises(autolabel_zero.TrainingSedangJalan):
        autolabel_zero.jalankan(d, tmp_path, tid, "darma", 1000, 0.1,
                                "yoloworld:latest", ["botol"])
    # Tak ada anotasi yang ditulis.
    assert not [p for p in d.glob("*.json") if not p.name.startswith(".")]


def test_jalankan_model_tak_dikenal(tmp_path, monkeypatch):
    d, tid = _siapkan(tmp_path, monkeypatch, 2, 0, _temuan_kotak)
    with pytest.raises(ValueError):
        autolabel_zero.jalankan(d, tmp_path, tid, "darma", 1000, 0.1,
                                "model-ngawur", ["botol"])


def test_jalankan_prompt_kosong(tmp_path, monkeypatch):
    d, tid = _siapkan(tmp_path, monkeypatch, 2, 0, _temuan_kotak)
    with pytest.raises(ValueError):
        autolabel_zero.jalankan(d, tmp_path, tid, "darma", 1000, 0.1,
                                "yoloworld:latest", [])


# ---- rute -------------------------------------------------------------------
def test_rute_kemajuan_dan_info(klien, lingkungan, monkeypatch):
    from conftest import masuk
    masuk(klien, "paul", PW_PAUL)
    r = klien.get("/api/autolabel/kemajuan")
    assert r.status_code == 200 and r.json()["ok"] is True

    d = lingkungan["ruang"] / "mini"
    buat_dataset(d, 2, 0)
    klien.post(f"/setsrc?path={d}")
    r = klien.get("/api/autolabel/info")
    j = r.json()
    assert j["ok"] is True
    assert isinstance(j["teks"], list) and j["teks"]      # ada katalog model teks
    assert j["model_bawaan"] in ("yoloworld:latest", "sam3:latest")


def test_rute_mulai_terima_batch(klien, lingkungan, monkeypatch):
    # Lingkup batch diterima tanpa tid; jalankan di-cegat supaya tak memuat model.
    from conftest import masuk
    from app.services import gpu_sinyal
    masuk(klien, "paul", PW_PAUL)
    d = lingkungan["ruang"] / "batchrt"
    buat_dataset(d, 2, 0)
    klien.post(f"/setsrc?path={d}")
    monkeypatch.setattr(gpu_sinyal, "latih_aktif", lambda: False)
    tangkap = {}

    def fake(ds, up, tid, akun, batas, conf, model, prompt, batal=None, batch=None):
        tangkap["tid"] = tid
        tangkap["batch"] = batch
        return {"total": 0, "diproses": 0, "objek": 0, "dilewati": 0}
    monkeypatch.setattr(autolabel_zero, "jalankan", fake)

    r = klien.post("/api/autolabel/mulai", json={"batch": "", "prompt": "botol"})
    assert r.json()["ok"] is True                # batch diterima tanpa tid


def test_rute_mulai_ditolak_saat_training(klien, lingkungan, monkeypatch):
    from conftest import masuk
    from app.services import gpu_sinyal
    masuk(klien, "paul", PW_PAUL)
    d = lingkungan["ruang"] / "mini2"
    buat_dataset(d, 2, 0)
    klien.post(f"/setsrc?path={d}")
    monkeypatch.setattr(gpu_sinyal, "latih_aktif", lambda: True)
    r = klien.post("/api/autolabel/mulai", json={"tid": "t1"})
    j = r.json()
    assert j["ok"] is False and j.get("training") is True


def test_jalankan_pose_ditolak(tmp_path, monkeypatch):
    """Projek kerangka (pose) ditolak, SAMA seperti jalur gabungan.

    Tanpa penjaga ini zero-shot menulis poligon/kotak lewat tulis_prediksi ke
    dataset keypoint: bentuk yang tak pernah bisa jadi anotasi pose dan harus
    dihapus satu per satu belakangan. Jalur gabungan sudah menjaganya sejak
    awal; yang ini tertinggal, jadi dua pintu ke fitur yang sama berperilaku
    berbeda pada projek yang sama.
    """
    d, tid = _siapkan(tmp_path, monkeypatch, 2, 0, _temuan_kotak)
    monkeypatch.setattr(tugas, "jenis_berlaku", lambda data, items: "kerangka")
    with pytest.raises(ValueError, match="kerangka"):
        autolabel_zero.jalankan(d, tmp_path, tid, "darma", 1000, 0.1,
                                "yoloworld:latest", ["botol"])
    # Tak satu pun anotasi ditulis sebelum menolak.
    assert not [p for p in d.glob("*.json") if not p.name.startswith(".")]


def test_pilih_model_kerangka_bukan_sam3():
    """Kode dan docstring-nya dulu berbeda: docstring menjanjikan
    'poligon/kerangka -> SAM 3', kodenya hanya memeriksa 'poligon'. Yang
    dikunci di sini perilakunya, bukan kalimat docstring-nya."""
    assert autolabel_zero.pilih_model("poligon") == "sam3:latest"
    assert autolabel_zero.pilih_model("kerangka") == "yoloworld:latest"
    assert autolabel_zero.pilih_model("kotak") == "yoloworld:latest"
    assert autolabel_zero.pilih_model("") == "yoloworld:latest"
