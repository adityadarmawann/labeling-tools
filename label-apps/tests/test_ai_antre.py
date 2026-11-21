"""Satu antrean untuk semua pekerjaan AI yang memakai GPU.

Dulu ada TIGA semaphore terpisah (pre-label, zero-shot, gabungan) dan DUA peta
"satu job per akun" (satu per router). Akibatnya satu orang bisa menjalankan
pre-label dan auto-label bersamaan: dua model ultralytics, plus SAM/YOLO-World
dan GroundingDINO kalau ensemble, termuat di GPU yang sama pada saat yang sama.
Pada kartu 8 GB itu jalan terpendek menuju kehabisan VRAM di tengah pekerjaan
orang, dan yang gagal belum tentu yang belakangan dimulai.
"""
from __future__ import annotations

import pytest

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL
from app.services import ai_antre, autolabel_gabung, autolabel_zero, prelabel


def test_semua_jalur_berbagi_satu_antrean():
    g = ai_antre.giliran
    assert prelabel._giliran is g
    assert autolabel_zero._giliran is g
    assert autolabel_gabung._giliran is g


def test_klaim_satu_per_akun_lintas_fitur():
    ai_antre.lepas("uji")
    assert ai_antre.klaim("uji", "pre-label") is None
    # Fitur LAIN pun ditolak, dan yang dikembalikan nama pemegangnya -- dulu
    # pesannya menyebut fitur yang keliru.
    assert ai_antre.klaim("uji", "auto-label") == "pre-label"
    assert ai_antre.sedang_jalan("uji") == "pre-label"
    ai_antre.lepas("uji", "pre-label")
    assert ai_antre.sedang_jalan("uji") == ""
    assert ai_antre.klaim("uji", "auto-label") is None
    ai_antre.lepas("uji")


def test_lepas_tak_mencabut_klaim_milik_job_lain():
    """Job lama yang selesai belakangan tak boleh mencabut klaim job baru."""
    ai_antre.lepas("uji2")
    ai_antre.klaim("uji2", "pre-label")
    ai_antre.lepas("uji2", "auto-label")            # bukan pemegangnya
    assert ai_antre.sedang_jalan("uji2") == "pre-label"
    ai_antre.lepas("uji2", "pre-label")
    assert ai_antre.sedang_jalan("uji2") == ""


def test_akun_berbeda_tidak_saling_mengunci():
    ai_antre.lepas("a"); ai_antre.lepas("b")
    assert ai_antre.klaim("a", "pre-label") is None
    assert ai_antre.klaim("b", "auto-label") is None
    ai_antre.lepas("a"); ai_antre.lepas("b")


def test_serentak_ambil_yang_terkecil(monkeypatch):
    """Env lama masih dibaca supaya pemasangan lama tak berubah diam-diam, dan
    yang TERKECIL menang: menaikkan batas harus disengaja."""
    monkeypatch.setenv("LABELAPP_PRELABEL_SERENTAK", "4")
    monkeypatch.setenv("LABELAPP_AUTOLABEL_SERENTAK", "2")
    assert ai_antre._env("LABELAPP_PRELABEL_SERENTAK",
                         "LABELAPP_AUTOLABEL_SERENTAK") == 2
    monkeypatch.delenv("LABELAPP_PRELABEL_SERENTAK")
    monkeypatch.delenv("LABELAPP_AUTOLABEL_SERENTAK")
    assert ai_antre._env("LABELAPP_TAK_ADA") == 1


def test_rute_autolabel_ditolak_saat_prelabel_jalan(klien, lingkungan):
    """Penolakan lintas fitur sampai ke antarmuka, dengan nama yang benar."""
    masuk(klien, "paul", PW_PAUL)
    d = lingkungan["ruang"] / "antre1"
    buat_dataset(d, 2, 0)
    klien.post(f"/setsrc?path={d}")
    ai_antre.lepas("paul")
    ai_antre.klaim("paul", "pre-label")
    try:
        r = klien.post("/api/autolabel/mulai",
                       json={"batch": "", "sumber": [{"jenis": "terlatih",
                                                      "nomor": 1}]})
        j = r.json()
        assert j["ok"] is False
        assert "pre-label" in j["error"]
    finally:
        ai_antre.lepas("paul")


def test_klaim_tak_tertinggal_saat_permintaan_ditolak(klien, lingkungan):
    """Jalur penolakan isi-bodi TIDAK boleh meninggalkan klaim: kalau
    tertinggal, akun itu terkunci dari semua fitur AI sampai proses dimatikan."""
    masuk(klien, "paul", PW_PAUL)
    d = lingkungan["ruang"] / "antre2"
    buat_dataset(d, 2, 0)
    klien.post(f"/setsrc?path={d}")
    ai_antre.lepas("paul")
    r = klien.post("/api/autolabel/mulai", json={"batch": "", "sumber": [{}]})
    assert r.json()["ok"] is False                   # tak ada sumber yang sah
    assert ai_antre.sedang_jalan("paul") == ""       # dan klaimnya bersih


# ------------------------------------------- klaim yang pekerjanya sudah mati
def test_klaim_basi_tak_mengunci_akun_selamanya():
    """Dilaporkan dari prod: auto-label dijawab "masih ada auto-label yang
    berjalan" padahal tak ada apa pun yang berjalan, dan satu-satunya jalan
    keluar adalah menyalakan ulang server.

    Sebabnya klaim dulu cuma nama fitur, tanpa cara apa pun memeriksa
    keadaannya. Satu pekerja yang berhenti tanpa sempat melepas (thread mati
    di luar blok try, tugas asyncio dibatalkan, galat di jalur tak terduga)
    meninggalkan nama itu di sana untuk selamanya.
    """
    from app.services import ai_antre
    ai_antre.lepas("uji-basi")
    hidup = {"ya": True}
    assert ai_antre.klaim("uji-basi", "auto-label", lambda: hidup["ya"]) is None
    # selagi pekerjanya hidup, klaim kedua memang harus ditolak
    assert ai_antre.klaim("uji-basi", "pre-label", lambda: True) == "auto-label"
    # begitu pekerjanya mati, klaimnya tak boleh menghalangi lagi
    hidup["ya"] = False
    assert ai_antre.klaim("uji-basi", "pre-label", lambda: True) is None
    ai_antre.lepas("uji-basi")


def test_sedang_jalan_tak_melaporkan_yang_sudah_mati():
    """Kalau tidak, antarmuka terus menampilkan pekerjaan yang sudah tak ada."""
    from app.services import ai_antre
    ai_antre.lepas("uji-lapor")
    ai_antre.klaim("uji-lapor", "auto-label", lambda: False)
    assert ai_antre.sedang_jalan("uji-lapor") == ""
    ai_antre.lepas("uji-lapor")


def test_tanpa_pemeriksa_tetap_seperti_dulu():
    """Pemanggil lama yang tak menitipkan pemeriksa tak boleh berubah arti."""
    from app.services import ai_antre
    ai_antre.lepas("uji-lama")
    assert ai_antre.klaim("uji-lama", "auto-label") is None
    assert ai_antre.klaim("uji-lama", "pre-label") == "auto-label"
    assert ai_antre.sedang_jalan("uji-lama") == "auto-label"
    ai_antre.lepas("uji-lama")


def test_pemeriksa_yang_rusak_dianggap_mati():
    """Lebih baik salah melepas daripada mengunci akun selamanya."""
    from app.services import ai_antre
    ai_antre.lepas("uji-rusak")

    def meledak():
        raise RuntimeError("pemeriksa rusak")

    ai_antre.klaim("uji-rusak", "auto-label", meledak)
    assert ai_antre.sedang_jalan("uji-rusak") == ""
    ai_antre.lepas("uji-rusak")


def test_rute_spawn_menitipkan_pemeriksa():
    """Penjaga supaya pemeriksanya tak terlepas lagi dari jalur nyatanya."""
    import pathlib
    for f in ("app/routers/autolabel.py", "app/routers/prelabel.py"):
        src = pathlib.Path(f).read_text()
        assert "t.done()" in src, f
        assert "ai_antre.klaim(" in src and "masih_jalan" in src, f


def test_batalkan_membereskan_klaim_basi():
    """Benderanya dibaca pekerja; tanpa pekerja, tak ada yang membacanya."""
    import pathlib
    src = pathlib.Path("app/routers/autolabel.py").read_text()
    i = src.index('@router.post("/api/autolabel/batal")')
    blok = src[i:i + 900]
    assert "ai_antre.sedang_jalan(sess.user)" in blok


def test_lingkup_kosong_dilaporkan_sebagai_sebab():
    """Dilaporkan dari prod: bilah kemajuan berhenti di "0 dari 0" tanpa
    sepatah kata, dan orangnya mengira fiturnya rusak lalu mencoba lagi."""
    import inspect
    from app.services import autolabel_gabung, autolabel_zero
    for f in (autolabel_gabung.jalankan, autolabel_zero.jalankan):
        src = inspect.getsource(f)
        assert "if not target:" in src, f.__module__
        assert "tidak ada gambar yang cocok di lingkup ini" in src, f.__module__
