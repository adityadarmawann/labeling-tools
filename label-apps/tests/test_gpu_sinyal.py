"""
Pembebasan VRAM SAM saat training + penanda GPU antar-proses (gpu_sinyal).

Tujuannya: di GPU 8 GB, sesi SAM (sam2:small ~4 GB) yang residen di proses app
tidak boleh membuat training OOM. Dua mekanisme diuji di sini:
  1. gpu_sinyal: penanda "training memegang GPU", tahan terhadap penanda basi.
  2. autolabel.bebaskan_gpu() melepas sesi SAM; _boleh_gpu_osam & MobileSAM
     memilih CPU selama training aktif.
"""
from __future__ import annotations

import os
import pathlib

import pytest

from app.services import gpu_sinyal, autolabel


@pytest.fixture(autouse=True)
def _penanda_terisolasi(tmp_path, monkeypatch):
    """Penanda GPU ada di /tmp dan dibagi seluruh mesin. Saat suite berjalan
    paralel (atau ada training nyata), itu bisa saling mengganggu. Arahkan ke
    berkas sementara per-test supaya test ini tak pernah menyentuh penanda asli."""
    monkeypatch.setattr(gpu_sinyal, "_MARKER", tmp_path / "gpu-aktif.json")


def _bersihkan():
    gpu_sinyal.lepas_latih()


def test_penanda_roundtrip_dan_basi():
    _bersihkan()
    assert gpu_sinyal.latih_aktif() is False
    gpu_sinyal.tandai_latih(os.getpid())        # proses hidup
    assert gpu_sinyal.latih_aktif() is True
    gpu_sinyal.lepas_latih()
    assert gpu_sinyal.latih_aktif() is False
    # PID yang pasti sudah mati -> penanda dianggap basi, dibersihkan.
    gpu_sinyal.tandai_latih(2 ** 30)
    assert gpu_sinyal.latih_aktif() is False
    _bersihkan()


def test_penanda_pid_init_dianggap_basi():
    # Marker ber-pid 1 (init) pernah terjadi: training menulisnya lalu mati di
    # reboot. init SELALU hidup, jadi tanpa penjagaan latih_aktif macet True
    # selamanya (SAM dipaksa CPU, auto-label terblokir). pid <= 1 harus basi.
    import json
    _bersihkan()
    gpu_sinyal._MARKER.write_text(json.dumps({"pid": 1}))
    assert gpu_sinyal.latih_aktif() is False
    assert not gpu_sinyal._MARKER.exists()          # basi -> ikut dibersihkan
    gpu_sinyal._MARKER.write_text(json.dumps({"pid": 0}))
    assert gpu_sinyal.latih_aktif() is False
    _bersihkan()


def test_tandai_latih_tak_pernah_tulis_pid_init(monkeypatch):
    """pid 1 (init) tak pernah training, dan kini penandanya TIDAK ditulis
    sama sekali.

    Dulu ia tetap ditulis memakai pid proses sendiri. Niatnya baik, akibatnya
    tidak: di jalur peluncuran training, pid sendiri adalah proses APLIKASI,
    jadi penandanya bertahan selama aplikasi hidup dan seluruh mesin mengira
    ada training yang tak pernah selesai.
    """
    _bersihkan()
    gpu_sinyal.tandai_latih(1)
    assert not gpu_sinyal._MARKER.exists()
    assert gpu_sinyal.latih_aktif() is False
    _bersihkan()


def test_osam_gpu_eligible_dan_hybrid_sam3():
    # Diukur di stack sekarang: sam2, YOLO-World, DAN SAM 3 semua boleh GPU.
    for m in ("sam2:small", "yoloworld:latest", "sam3:latest"):
        assert m in autolabel.OSAM_GPU_KECIL
    # SAM 3 memuncak ~6,8 GB -> butuh VRAM bebas lebih besar dari default.
    assert autolabel.OSAM_GPU_BEBAS["sam3:latest"] > autolabel.OSAM_GPU_MIN_BEBAS

    class _B:
        def __init__(self, u):
            self.url = u
    # Hybrid SAM 3: hanya image_encoder di GPU; language_encoder + decoder CPU.
    assert autolabel._blob_boleh_gpu("sam3:latest",
                                     _B("https://x/sam3_image_encoder.onnx")) is True
    assert autolabel._blob_boleh_gpu("sam3:latest",
                                     _B("https://x/sam3_language_encoder.onnx")) is False
    assert autolabel._blob_boleh_gpu("sam3:latest",
                                     _B("https://x/sam3_decoder.onnx")) is False
    # Model non-SAM3: semua sesinya boleh GPU.
    assert autolabel._blob_boleh_gpu("yoloworld:latest",
                                     _B("https://x/yoloworld.onnx")) is True


def test_bebaskan_gpu_melepas_sesi_mobilesam():
    _bersihkan()
    # Pura-pura ada sesi MobileSAM residen; bebaskan harus melepasnya.
    autolabel._sesi_mobilesam = object()
    assert autolabel.bebaskan_gpu() is True
    assert autolabel._sesi_mobilesam is None
    # Tak ada yang residen -> aman, tak ada efek.
    assert autolabel.bebaskan_gpu() is False


def test_osam_ke_cpu_saat_training_aktif(monkeypatch):
    _bersihkan()
    # Paksa seolah mode GPU supaya yang memutus hanyalah penanda training.
    monkeypatch.setattr(autolabel.olah_gpu, "MODE", "gpu", raising=False)
    gpu_sinyal.tandai_latih(os.getpid())
    try:
        # Model kecil yang normalnya boleh GPU -> harus False karena training aktif.
        assert autolabel._boleh_gpu_osam("sam2:small") is False
    finally:
        _bersihkan()


def test_ambang_vram_sam3_ikut_versi_osam(monkeypatch):
    """Ambang 7,2 GB itu milik bobot float32 di osam 0.5, bukan hukum alam.

    Diukur di mesin ini, proses segar, satu model saja, di luar yang sudah
    dipakai proses lain: sam2:small ~2,2 GB dan sam3:latest ~2,8 GB dengan
    bobot float16 di osam 0.12. Membawa ambang lama ke sana membuat SAM 3
    praktis selalu jatuh ke CPU, dan di CPU ia terukur 67 detik per klik
    lewat jalur aplikasi.
    """
    from app.services import autolabel

    assert autolabel.OSAM_GPU_BEBAS["sam3:latest"] > autolabel.OSAM_GPU_MIN_BEBAS
    # Di osam 0.12 ambangnya PER MODEL, diukur, dan jauh lebih rendah.
    baru_ = autolabel.OSAM_GPU_BEBAS_OSAM_BARU["sam3:latest"]
    assert baru_ < autolabel.OSAM_GPU_BEBAS["sam3:latest"] / 2
    # Model kecil tak boleh ikut menanggung ambang milik model besar: satu
    # angka rata selalu ditentukan yang terbesar, dan itu yang membuat
    # sam2:small dikirim ke CPU saat VRAM bebas 3,35 GB padahal ia cuma
    # butuh 2,2 GB.
    assert autolabel.OSAM_GPU_MIN_BEBAS_BARU < baru_
    for kecil in ("sam2:small", "sam2:tiny", "efficientsam:10m"):
        butuh = autolabel.OSAM_GPU_BEBAS_OSAM_BARU.get(
            kecil, autolabel.OSAM_GPU_MIN_BEBAS_BARU)
        assert butuh <= autolabel.OSAM_GPU_MIN_BEBAS_BARU, kecil
    assert (autolabel.OSAM_GPU_BEBAS_OSAM_BARU["sam2:large"]
            > autolabel.OSAM_GPU_BEBAS_OSAM_BARU["sam3:latest"]), \
        "sam2:large yang paling lapar (3,2 GB), ambangnya harus paling tinggi"

    monkeypatch.setattr(autolabel.olah_gpu, "MODE", "gpu")
    monkeypatch.setattr(autolabel.gpu_sinyal, "latih_aktif", lambda: False)

    kelas = type("T", (), {})
    torch_palsu = kelas()
    torch_palsu.cuda = kelas()
    torch_palsu.cuda.is_available = staticmethod(lambda: True)
    # 6,0 GB bebas: cukup untuk ambang umum 4,7 GB, tak cukup untuk 7,2 GB.
    torch_palsu.cuda.mem_get_info = staticmethod(lambda: (6_000_000_000,
                                                          8_200_000_000))
    import sys
    monkeypatch.setitem(sys.modules, "torch", torch_palsu)

    monkeypatch.setattr(autolabel.osam_api, "baru", lambda: False)
    assert autolabel._boleh_gpu_osam("sam3:latest") is False

    monkeypatch.setattr(autolabel.osam_api, "baru", lambda: True)
    assert autolabel._boleh_gpu_osam("sam3:latest") is True, \
        "dengan float16 ia harus boleh naik GPU pada sisa VRAM yang sama"
    assert autolabel._boleh_gpu_osam("sam2:small") is True


def test_ambang_baru_tak_menjatuhkan_sam_ke_cpu(monkeypatch):
    """Sisa VRAM 4,5 GB adalah keadaan NYATA di prod, bukan karangan.

    Proses prod sendiri memegang ~3,3 GB untuk konteks CUDA torch, jadi yang
    tersisa 4,5 GB. Dengan ambang lama 4,7 GB, SAM tak pernah sekali pun naik
    GPU di sana: log prod mencatat "Initialized inference sessions on 'cpu'"
    untuk SAM 3 maupun sam2:small, dan satu gambar makan 19 detik.
    """
    import sys

    from app.services import autolabel

    monkeypatch.setattr(autolabel.olah_gpu, "MODE", "gpu")
    monkeypatch.setattr(autolabel.gpu_sinyal, "latih_aktif", lambda: False)
    monkeypatch.setattr(autolabel, "OSAM_GPU", False)
    kelas = type("T", (), {})
    t = kelas(); t.cuda = kelas()
    t.cuda.is_available = staticmethod(lambda: True)
    t.cuda.mem_get_info = staticmethod(lambda: (4_500_000_000, 8_200_000_000))
    monkeypatch.setitem(sys.modules, "torch", t)

    monkeypatch.setattr(autolabel.osam_api, "baru", lambda: True)
    for m in ("sam2:small", "sam2:latest", "sam2:large", "sam3:latest"):
        assert autolabel._boleh_gpu_osam(m) is True, m


def test_semua_model_pilihan_boleh_naik_gpu():
    """Model yang bisa dipilih orang tetapi tak ada di daftar GPU berarti
    memilih CPU tanpa ada yang memberi tahu. Itu yang terjadi pada sam2:large
    dan sam2:latest: keduanya ditawarkan sejak kemarin, keduanya selalu di
    CPU, dan yang terasa adalah "kok sama saja lambatnya"."""
    from app.services import autolabel

    lewat_osam = {m for m in autolabel.MODEL_DIIZINKAN if m != "mobilesam"}
    tertinggal = lewat_osam - autolabel.OSAM_GPU_KECIL
    assert not tertinggal, f"tak pernah bisa naik GPU: {sorted(tertinggal)}"


def test_saklar_paksa_benar_benar_memaksa(monkeypatch):
    """LABELAPP_OSAM_GPU=1 menjanjikan "semua model osam ke GPU".

    Dulu ia hanya melewati penjaga ukuran, sedangkan ambang VRAM tetap
    menjatuhkannya ke CPU: saklar yang tidak benar-benar memaksa. Penjaga
    training tetap berlaku, dan itu disengaja.
    """
    import sys

    from app.services import autolabel

    monkeypatch.setattr(autolabel.olah_gpu, "MODE", "gpu")
    monkeypatch.setattr(autolabel, "OSAM_GPU", True)
    kelas = type("T", (), {})
    t = kelas(); t.cuda = kelas()
    t.cuda.is_available = staticmethod(lambda: True)
    t.cuda.mem_get_info = staticmethod(lambda: (500_000_000, 8_200_000_000))
    monkeypatch.setitem(sys.modules, "torch", t)

    monkeypatch.setattr(autolabel.gpu_sinyal, "latih_aktif", lambda: False)
    assert autolabel._boleh_gpu_osam("sam3:latest") is True, \
        "VRAM sesak pun, saklar paksa harus tetap GPU"

    monkeypatch.setattr(autolabel.gpu_sinyal, "latih_aktif", lambda: True)
    assert autolabel._boleh_gpu_osam("sam3:latest") is False, \
        "training yang sedang jalan tetap menang atas saklar paksa"


def test_model_lain_dilepas_sebelum_menyerah_ke_cpu(monkeypatch):
    """VRAM yang ditahan model LAIN bukan alasan sah untuk pindah ke CPU.

    Memuat ulang dibayar sekali beberapa detik; CPU dibayar puluhan detik
    setiap gambar (terukur: sam3:latest 28 dtk per gambar di CPU, 1,3 dtk di
    GPU).
    """
    import sys

    from app.services import autolabel

    monkeypatch.setattr(autolabel.olah_gpu, "MODE", "gpu")
    monkeypatch.setattr(autolabel.gpu_sinyal, "latih_aktif", lambda: False)
    monkeypatch.setattr(autolabel, "OSAM_GPU", False)
    monkeypatch.setattr(autolabel.osam_api, "baru", lambda: True)

    keadaan = {"bebas": 1_000_000_000, "dilepas": False}

    def tutup():
        keadaan["dilepas"] = True
        keadaan["bebas"] = 6_000_000_000     # model lain melepaskan VRAM-nya
        return True

    monkeypatch.setattr(autolabel.osam_api, "tutup", tutup)
    monkeypatch.setattr(autolabel.osam_api, "residen", lambda: "sam2:large")

    kelas = type("T", (), {})
    t = kelas(); t.cuda = kelas()
    t.cuda.is_available = staticmethod(lambda: True)
    t.cuda.empty_cache = staticmethod(lambda: None)
    t.cuda.mem_get_info = staticmethod(
        lambda: (keadaan["bebas"], 8_200_000_000))
    monkeypatch.setitem(sys.modules, "torch", t)

    assert autolabel._boleh_gpu_osam("sam3:latest") is True
    assert keadaan["dilepas"] is True


def test_model_yang_sama_tak_dilepas_percuma(monkeypatch):
    """Kalau yang residen justru model yang diminta, melepasnya hanya membuat
    ia dimuat ulang tanpa alasan."""
    import sys

    from app.services import autolabel

    monkeypatch.setattr(autolabel.olah_gpu, "MODE", "gpu")
    monkeypatch.setattr(autolabel.gpu_sinyal, "latih_aktif", lambda: False)
    monkeypatch.setattr(autolabel, "OSAM_GPU", False)
    monkeypatch.setattr(autolabel.osam_api, "baru", lambda: True)
    monkeypatch.setattr(autolabel.osam_api, "residen", lambda: "sam3:latest")

    dilepas = []
    monkeypatch.setattr(autolabel.osam_api, "tutup",
                        lambda: dilepas.append(True))
    kelas = type("T", (), {})
    t = kelas(); t.cuda = kelas()
    t.cuda.is_available = staticmethod(lambda: True)
    t.cuda.mem_get_info = staticmethod(lambda: (1_000_000_000, 8_200_000_000))
    monkeypatch.setitem(sys.modules, "torch", t)

    assert autolabel._boleh_gpu_osam("sam3:latest") is False
    assert dilepas == [], "jangan melepas model yang justru sedang diminta"


def test_tiap_jalan_ke_cpu_menyebut_alasannya(monkeypatch, caplog):
    """Jalan menuju CPU yang DIAM membuat "kok SAM lambat" tak bisa dijawab.

    Terbukti di prod: ada model dimuat di 'cpu' tanpa satu pun baris yang
    menyebut sebabnya, sementara satu-satunya jalur yang mencatat hanyalah
    ambang VRAM. Empat jalur lain diam, dan menebak di antara empat
    kemungkinan bukan diagnosis.
    """
    import logging
    import sys

    from app.services import autolabel

    kelas = type("T", (), {})
    t = kelas(); t.cuda = kelas()
    t.cuda.is_available = staticmethod(lambda: True)
    t.cuda.mem_get_info = staticmethod(lambda: (8_000_000_000, 8_200_000_000))
    monkeypatch.setitem(sys.modules, "torch", t)
    monkeypatch.setattr(autolabel, "OSAM_GPU", False)
    monkeypatch.setattr(autolabel.osam_api, "baru", lambda: True)

    def sebab(siapkan) -> str:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="labelapp.autolabel"):
            siapkan()
            assert autolabel._boleh_gpu_osam("sam2:small") is False
        return " ".join(r.getMessage() for r in caplog.records)

    def mode_bukan_gpu():
        monkeypatch.setattr(autolabel.olah_gpu, "MODE", "cpu")
        monkeypatch.setattr(autolabel.gpu_sinyal, "latih_aktif", lambda: False)
    assert "LABELAPP_OLAH" in sebab(mode_bukan_gpu)

    def training_jalan():
        monkeypatch.setattr(autolabel.olah_gpu, "MODE", "gpu")
        monkeypatch.setattr(autolabel.gpu_sinyal, "latih_aktif", lambda: True)
    assert "training" in sebab(training_jalan)

    def cuda_hilang():
        monkeypatch.setattr(autolabel.gpu_sinyal, "latih_aktif", lambda: False)
        t.cuda.is_available = staticmethod(lambda: False)
    assert "CUDA tak tersedia" in sebab(cuda_hilang)

    def pemeriksaan_meledak():
        t.cuda.is_available = staticmethod(lambda: True)
        def meledak():
            raise RuntimeError("driver hilang")
        t.cuda.mem_get_info = staticmethod(meledak)
    assert "pemeriksaan GPU gagal" in sebab(pemeriksaan_meledak)


def test_model_di_luar_daftar_gpu_juga_menyebut_alasannya(monkeypatch, caplog):
    import logging
    import sys

    from app.services import autolabel

    monkeypatch.setattr(autolabel.olah_gpu, "MODE", "gpu")
    monkeypatch.setattr(autolabel, "OSAM_GPU", False)
    kelas = type("T", (), {})
    t = kelas(); t.cuda = kelas()
    t.cuda.is_available = staticmethod(lambda: True)
    monkeypatch.setitem(sys.modules, "torch", t)
    with caplog.at_level(logging.INFO, logger="labelapp.autolabel"):
        assert autolabel._boleh_gpu_osam("model-asing") is False
    assert any("OSAM_GPU_KECIL" in r.getMessage() for r in caplog.records)


def test_pid_kosong_tak_pernah_jadi_pid_sendiri(monkeypatch, tmp_path):
    """Penanda ini dibaca SELURUH instalasi di mesin, termasuk prod.

    Dulu pid yang kosong diam-diam diganti pid proses sendiri. Di jalur
    peluncuran training, pid sendiri adalah proses APLIKASI, bukan
    trainingnya, jadi penandanya bertahan selama aplikasi hidup dan setiap
    klik SAM di seluruh mesin mengalah ke CPU untuk training yang sudah lama
    selesai. Itu benar-benar terjadi: prod melambat berjam-jam karena penanda
    yang ditulis proses lain.
    """
    from app.services import gpu_sinyal

    monkeypatch.setattr(gpu_sinyal, "_MARKER", tmp_path / "penanda.json")
    gpu_sinyal.tandai_latih(None)
    assert not gpu_sinyal._MARKER.exists(), "pid kosong tak boleh ditulis"
    gpu_sinyal.tandai_latih(0)
    assert not gpu_sinyal._MARKER.exists()
    gpu_sinyal.tandai_latih(1)
    assert not gpu_sinyal._MARKER.exists(), "pid 1 (init) tak pernah training"
    assert gpu_sinyal.latih_aktif() is False

    gpu_sinyal.tandai_latih(os.getpid())        # pid sungguhan -> ditulis
    assert gpu_sinyal._MARKER.exists()
    assert gpu_sinyal.latih_aktif() is True
    gpu_sinyal.lepas_latih()


def test_suite_tak_pernah_menyentuh_penanda_mesin():
    """Penjaga: fixture di conftest harus mengarahkan penanda ke tmp_path.

    Tanpa itu, menjalankan suite di mesin ini membuat PROD mengira ada
    training dan mengirim semua klik SAM ke CPU selama suite berjalan.
    """
    import tempfile

    from app.services import gpu_sinyal

    bawaan = pathlib.Path(tempfile.gettempdir()) / "labelapp-latih-gpu-aktif.json"
    assert gpu_sinyal._MARKER != bawaan, \
        "suite sedang memakai penanda milik seluruh mesin"
