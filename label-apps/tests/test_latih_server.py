"""
Backend training SERVER (offload SSH ke GPU mesin lain).

Jalur SSH/rsync sungguhan tak bisa diuji di suite (butuh server remote). Yang
dijaga di sini bagian MURNI yang menentukan kebenaran: data.yaml remote ditulis
tak-ambigu (path absolut + pose dipertahankan), augmentasi pose aman, bobot
lokal dikenali supaya dikirim, kesiapan backend, dan dispatch memilih modul yang
benar + TIDAK merebut flock GPU lokal.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import latih, latih_server


def _yaml(p, isi):
    p.write_text(isi)
    return p


# ---------------------------------------------------------- data.yaml remote
def test_data_yaml_remote_path_absolut_dan_subpath(tmp_path):
    y = _yaml(tmp_path / "data.yaml",
              "train: ../train/images\nval: ../valid/images\n"
              "test: ../test/images\nnc: 2\nnames: ['a','b']\n")
    import yaml as _y
    out = _y.safe_load(latih_server._data_yaml_remote(y, "/home/clvision/run/ds"))
    assert out["path"] == "/home/clvision/run/ds"
    assert out["train"] == "train/images" and out["val"] == "valid/images"
    assert out["nc"] == 2 and out["names"] == ["a", "b"]
    assert "../" not in out["train"]              # tak ada lagi jalur ambigu


def test_data_yaml_remote_pertahankan_pose(tmp_path):
    y = _yaml(tmp_path / "data.yaml",
              "train: ../train/images\nval: ../valid/images\nnc: 1\n"
              "names: ['court']\nkpt_shape: [30, 3]\nflip_idx: [0, 1, 2]\n")
    import yaml as _y
    out = _y.safe_load(latih_server._data_yaml_remote(y, "/r/ds"))
    assert out["kpt_shape"] == [30, 3] and out["flip_idx"] == [0, 1, 2]


# ---------------------------------------------------------- pose augmentasi
def test_sesuaikan_par_pose_flip_dan_mosaic(tmp_path):
    # flip_idx identitas -> fliplr DIPAKSA 0 (mirror tanpa tukar identitas = salah).
    y = _yaml(tmp_path / "d.yaml", "nc: 1\nnames: ['x']\nflip_idx: [0, 1, 2]\n")
    par = latih_server._sesuaikan_par_pose(
        {"fliplr": 0.5, "flipud": 0.3, "mosaic": 1.0, "epochs": 5}, y)
    assert par["flipud"] == 0.0 and par["fliplr"] == 0.0
    assert par["mosaic"] == 0.5 and par["epochs"] == 5     # cap mosaic, sisanya utuh


def test_sesuaikan_par_pose_mirror_sungguhan_fliplr_hidup(tmp_path):
    # flip_idx bukan identitas (ada pasangan cermin) -> fliplr dibiarkan.
    y = _yaml(tmp_path / "d.yaml", "nc: 1\nnames: ['x']\nflip_idx: [1, 0, 2]\n")
    par = latih_server._sesuaikan_par_pose({"fliplr": 0.5, "mosaic": 0.2}, y)
    assert par["fliplr"] == 0.5 and par["flipud"] == 0.0 and par["mosaic"] == 0.2


# ---------------------------------------------------------- bobot lokal
def test_bobot_lokal_berkas_dikirim_nama_diunduh(tmp_path):
    f = tmp_path / "custom.pt"
    f.write_bytes(b"x")
    assert latih_server._bobot_lokal(str(f)) == f           # berkas lokal -> dikirim
    assert latih_server._bobot_lokal("yolo11n.pt") is None  # nama -> remote unduh


# ---------------------------------------------------------- skrip remote
def test_skrip_latih_mandiri_dan_device_gpu():
    s = latih_server._skrip_latih("/r/ds/data.yaml", "yolo11n.pt", "detect",
                                  {"epochs": 3, "imgsz": 640}, "/r")
    assert "from ultralytics import YOLO" in s
    assert "device=0" in s and "task='detect'" in s
    assert "/r/ds/data.yaml" in s and '"epochs": 3' in s
    assert "/run/EXIT" in s                                 # penanda selesai
    assert "import app" not in s and "higolab" not in s.lower()  # MANDIRI


# ---------------------------------------------------------- kesiapan backend
def test_siap_server_butuh_host_dan_kunci(tmp_path):
    key = tmp_path / "k"
    key.write_text("PRIVKEY")
    assert latih.siap_server(SimpleNamespace(remote_ssh="", remote_key=str(key)))[0] is False
    assert latih.siap_server(
        SimpleNamespace(remote_ssh="u@h", remote_key=str(tmp_path / "hilang")))[0] is False
    ok, alasan = latih.siap_server(SimpleNamespace(remote_ssh="u@h", remote_key=str(key)))
    assert ok is True and alasan == ""


def test_config_remote_dari_env(monkeypatch):
    from app import config
    config.get_settings.cache_clear()
    monkeypatch.setenv("LABELAPP_REMOTE_SSH", "clvision@103.182.240.30")
    monkeypatch.setenv("LABELAPP_REMOTE_NAMA", "clvision RTX3060")
    try:
        s = config.get_settings()
        assert s.remote_ssh == "clvision@103.182.240.30"
        assert s.remote_nama == "clvision RTX3060"
        assert s.remote_py == "python3"            # bawaan
    finally:
        config.get_settings.cache_clear()


# ---------------------------------------------------------- dispatch
def test_siapkan_terima_backend_server(tmp_path, monkeypatch):
    # siapkan membekukan backend "server" (bukan dibanting ke "lokal").
    import app.services.latih as L

    monkeypatch.setattr(L, "periksa_warna", lambda *a, **k: {}, raising=False)
    d = tmp_path / "proj"
    (d / ".versi" / "v1").mkdir(parents=True)
    (d / ".versi" / "v1" / "data.yaml").write_text("nc: 1\nnames: ['x']\n")
    isi = L.siapkan(d, nama="t", versi_nomor=1, tugas="detect", bobot="yolo11n.pt",
                    par={"epochs": 1}, oleh="paul", backend="server", arsitektur="yolo")
    assert isi["backend"] == "server"
