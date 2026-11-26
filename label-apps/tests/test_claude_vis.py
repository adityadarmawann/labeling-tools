"""Claude sebagai sumber "Deteksi dari teks".

Yang diuji di sini bukan kepintarannya, melainkan apa yang terjadi pada
JAWABANNYA. Jawaban model bahasa datang sebagai teks, dan empat hal yang
rutin salah tak akan terlihat sebagai galat, hanya sebagai label yang aneh:
JSON terbungkus kalimat pengantar, kelas yang dikarang sendiri, kotak
terbalik, dan koordinat di luar gambar.

Tanpa jaringan sama sekali: kliennya diganti, dan penguraiannya memang
dipisah supaya bisa diuji apa adanya.
"""
from __future__ import annotations

import json
import types

import cv2
import numpy as np
import pytest

from app.services import claude_vis


class _Setelan:
    def __init__(self, kunci="rahasia", sisi=1024, model="claude-sonnet-5"):
        self.anthropic_api_key = kunci
        self.anthropic_sisi_maks = sisi
        self.anthropic_model = model


KELAS = ["frestea-apel-250ml", "frestea-teh-hijau-350ml", "sprite-lemon-390"]


# ------------------------------------------------- saklar hidup/mati

def test_kunci_kosong_berarti_fitur_mati():
    assert claude_vis.aktif(_Setelan(kunci="")) is False
    assert claude_vis.aktif(_Setelan(kunci="   ")) is False
    assert claude_vis.aktif(_Setelan(kunci="sk-xxx")) is True


def test_tanpa_kunci_menolak_dengan_jelas(tmp_path):
    ip = tmp_path / "a.jpg"
    cv2.imwrite(str(ip), np.full((40, 40, 3), 200, np.uint8))
    with pytest.raises(claude_vis.ClaudeGagal, match="kunci API"):
        claude_vis.deteksi(ip, KELAS, settings=_Setelan(kunci=""))


def test_tanpa_nama_kelas_menolak(tmp_path):
    ip = tmp_path / "a.jpg"
    cv2.imwrite(str(ip), np.full((40, 40, 3), 200, np.uint8))
    with pytest.raises(claude_vis.ClaudeGagal, match="nama kelas"):
        claude_vis.deteksi(ip, ["  "], settings=_Setelan())


# ------------------------------------------------- membaca jawabannya

def _jawab(isi) -> str:
    return json.dumps(isi)


def test_kotak_diskalakan_ke_ukuran_asli():
    """Skalanya 0..1000, bukan piksel, karena gambar dikecilkan saat dikirim."""
    hasil = claude_vis._baca_jawaban(
        _jawab([{"label": KELAS[0], "box": [0, 0, 500, 250], "confidence": 0.9}]),
        KELAS, W=640, H=480, ambang=0.1, maks=10)
    assert hasil == [{"label": KELAS[0], "bbox": (0, 0, 320, 120), "skor": 0.9}]


def test_json_terbungkus_kalimat_dan_pagar_tetap_terbaca():
    teks = ("Berikut hasilnya:\n```json\n"
            + _jawab([{"label": KELAS[1], "box": [100, 100, 200, 200],
                       "confidence": 0.8}])
            + "\n```\nSemoga membantu.")
    hasil = claude_vis._baca_jawaban(teks, KELAS, 1000, 1000, 0.1, 10)
    assert len(hasil) == 1 and hasil[0]["label"] == KELAS[1]


def test_kelas_karangan_dibuang():
    """Satu sinonim yang lolos akan jadi kelas BARU di dataset."""
    hasil = claude_vis._baca_jawaban(
        _jawab([{"label": "green tea bottle", "box": [1, 1, 500, 500],
                 "confidence": 0.99},
                {"label": KELAS[0], "box": [1, 1, 500, 500],
                 "confidence": 0.5}]),
        KELAS, 100, 100, 0.1, 10)
    assert [d["label"] for d in hasil] == [KELAS[0]]


def test_nama_kelas_tak_peka_huruf_besar_kecil():
    hasil = claude_vis._baca_jawaban(
        _jawab([{"label": KELAS[0].upper(), "box": [1, 1, 500, 500],
                 "confidence": 0.5}]), KELAS, 100, 100, 0.1, 10)
    assert hasil[0]["label"] == KELAS[0], "harus dikembalikan ke ejaan projek"


def test_kotak_terbalik_dibetulkan_bukan_dibuang():
    hasil = claude_vis._baca_jawaban(
        _jawab([{"label": KELAS[0], "box": [800, 600, 200, 100],
                 "confidence": 0.7}]), KELAS, 1000, 1000, 0.1, 10)
    assert hasil[0]["bbox"] == (200, 100, 800, 600)


def test_koordinat_di_luar_gambar_dijepit():
    hasil = claude_vis._baca_jawaban(
        _jawab([{"label": KELAS[0], "box": [-50, -50, 1500, 1500],
                 "confidence": 0.7}]), KELAS, 640, 480, 0.1, 10)
    assert hasil[0]["bbox"] == (0, 0, 640, 480)


def test_di_bawah_ambang_dan_kotak_terlalu_kecil_dibuang():
    hasil = claude_vis._baca_jawaban(
        _jawab([{"label": KELAS[0], "box": [1, 1, 500, 500], "confidence": 0.05},
                {"label": KELAS[0], "box": [10, 10, 11, 11], "confidence": 0.9}]),
        KELAS, 100, 100, ambang=0.1, maks=10)
    assert hasil == []


def test_diurutkan_skor_dan_dibatasi_maks():
    isi = [{"label": KELAS[0], "box": [i, i, i + 500, i + 500],
            "confidence": i / 10} for i in range(1, 6)]
    hasil = claude_vis._baca_jawaban(_jawab(isi), KELAS, 1000, 1000, 0.0, 2)
    assert [d["skor"] for d in hasil] == [0.5, 0.4]


def test_jawaban_bukan_json_menghasilkan_kosong():
    """Lebih baik tak menemukan apa-apa daripada menebak-nebak isinya."""
    assert claude_vis._baca_jawaban("maaf, saya tidak bisa membantu",
                                    KELAS, 100, 100, 0.1, 10) == []
    assert claude_vis._baca_jawaban("[bukan json]", KELAS, 100, 100, 0.1, 10) == []


# ------------------------------------------------- gambar yang dikirim

def test_gambar_dikecilkan_tapi_ukuran_asli_dipertahankan(tmp_path):
    """Kotak jawabannya harus merujuk ukuran ASLI, bukan ukuran kirim."""
    ip = tmp_path / "besar.jpg"
    cv2.imwrite(str(ip), np.full((2000, 3000, 3), 180, np.uint8))
    b64, W, H = claude_vis._gambar_untuk_kirim(ip, 1024)
    assert (W, H) == (3000, 2000), "ukuran asli yang dilaporkan"
    import base64
    buf = np.frombuffer(base64.b64decode(b64), np.uint8)
    kecil = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    assert max(kecil.shape[:2]) == 1024


def test_deteksi_memakai_klien_yang_diganti(tmp_path, monkeypatch):
    """Alur utuh tanpa jaringan: gambar dikirim, jawaban diurai, kotak
    diskalakan ke ukuran asli."""
    ip = tmp_path / "a.jpg"
    cv2.imwrite(str(ip), np.full((400, 800, 3), 200, np.uint8))

    dikirim = {}

    class _Pesan:
        def create(self, **kw):
            dikirim.update(kw)
            isi = _jawab([{"label": KELAS[2], "box": [0, 0, 1000, 500],
                           "confidence": 0.77}])
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(text=isi)])

    monkeypatch.setattr(claude_vis, "_klien",
                        lambda s: types.SimpleNamespace(messages=_Pesan()))
    hasil = claude_vis.deteksi(ip, KELAS, settings=_Setelan(), ambang=0.1)
    assert hasil == [{"label": KELAS[2], "bbox": (0, 0, 800, 200), "skor": 0.77}]
    assert dikirim["model"] == "claude-sonnet-5"
    isi_pesan = dikirim["messages"][0]["content"]
    assert isi_pesan[0]["type"] == "image"
    for k in KELAS:
        assert k in isi_pesan[1]["text"], "daftar kelas harus disodorkan"


def test_panggilan_gagal_dijelaskan_bukan_meledak(tmp_path, monkeypatch):
    ip = tmp_path / "a.jpg"
    cv2.imwrite(str(ip), np.full((40, 40, 3), 200, np.uint8))

    class _Pesan:
        def create(self, **kw):
            raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(claude_vis, "_klien",
                        lambda s: types.SimpleNamespace(messages=_Pesan()))
    with pytest.raises(claude_vis.ClaudeGagal, match="401"):
        claude_vis.deteksi(ip, KELAS, settings=_Setelan())


# ------------------------------------------------- menyatu dengan auto-label

def test_pilihan_claude_muncul_hanya_saat_kuncinya_ada(monkeypatch):
    """Menawarkan pilihan yang pasti gagal saat ditekan lebih buruk daripada
    tidak menawarkannya. Aturan yang sama dipakai model SAM yang tak didukung
    osam terpasang."""
    from app.services import autolabel

    assert "claude" not in autolabel.model_teks(_Setelan(kunci=""))
    assert "claude" in autolabel.model_teks(_Setelan(kunci="sk-x"))
    # Yang lain tak boleh ikut hilang atau berubah.
    for m in ("yoloworld:latest", "sam3:latest"):
        assert m in autolabel.model_teks(_Setelan(kunci=""))


def test_daftar_di_antarmuka_mengikuti_saklarnya(monkeypatch):
    from app.services import autolabel

    monkeypatch.setattr(autolabel, "model_teks",
                        lambda settings=None: dict(autolabel.MODEL_TEKS))
    assert "claude" not in {t["model"] for t in autolabel.info()["teks"]}

    gabung = {**autolabel.MODEL_TEKS, **autolabel.MODEL_TEKS_BERSYARAT}
    monkeypatch.setattr(autolabel, "model_teks", lambda settings=None: gabung)
    teks = {t["model"]: t for t in autolabel.info()["teks"]}
    assert "claude" in teks
    assert teks["claude"]["terunduh"] is True, "tak ada bobot untuk diunduh"
    assert teks["claude"]["unduh_mb"] == 0
    assert "berbayar" in teks["claude"]["catatan"], \
        "biayanya harus disebut SEBELUM tombolnya ditekan"


def test_dari_teks_menolak_claude_saat_saklarnya_mati(tmp_path, monkeypatch):
    """Penjaga backend harus sepakat dengan daftarnya, bukan lebih longgar."""
    from app.services import autolabel

    ip = tmp_path / "a.jpg"
    cv2.imwrite(str(ip), np.full((40, 40, 3), 200, np.uint8))
    monkeypatch.setattr(autolabel, "model_teks",
                        lambda settings=None: dict(autolabel.MODEL_TEKS))
    with pytest.raises(autolabel.TidakAdaObjek, match="tidak menerima prompt"):
        autolabel.dari_teks(ip, ["botol"], model="claude")


def test_dari_teks_claude_keluar_dalam_bentuk_yang_sama(tmp_path, monkeypatch):
    """Penggabung, tanda perlu_dicek, dan halaman verifikasi tak boleh perlu
    tahu bahwa sumbernya Claude."""
    from app.services import autolabel

    ip = tmp_path / "a.jpg"
    cv2.imwrite(str(ip), np.full((200, 400, 3), 200, np.uint8))
    gabung = {**autolabel.MODEL_TEKS, **autolabel.MODEL_TEKS_BERSYARAT}
    monkeypatch.setattr(autolabel, "model_teks", lambda settings=None: gabung)
    monkeypatch.setattr(claude_vis, "deteksi",
                        lambda img, kelas, **kw: [
                            {"label": kelas[0], "bbox": (10, 20, 110, 120),
                             "skor": 0.9}])
    hasil = autolabel.dari_teks(ip, ["botol"], model="claude", ambang=0.1)
    assert len(hasil) == 1
    t = hasil[0]
    assert (t.label, t.bbox, t.skor, t.shape_type) == (
        "botol", (10, 20, 110, 120), 0.9, "rectangle")
    assert t.points == [[10, 20], [110, 120]]


def test_kegagalan_claude_jadi_pesan_yang_bisa_dibaca(tmp_path, monkeypatch):
    from app.services import autolabel

    ip = tmp_path / "a.jpg"
    cv2.imwrite(str(ip), np.full((40, 40, 3), 200, np.uint8))
    gabung = {**autolabel.MODEL_TEKS, **autolabel.MODEL_TEKS_BERSYARAT}
    monkeypatch.setattr(autolabel, "model_teks", lambda settings=None: gabung)

    def meledak(img, kelas, **kw):
        raise claude_vis.ClaudeGagal("panggilan Claude gagal: 429 rate limit")

    monkeypatch.setattr(claude_vis, "deteksi", meledak)
    with pytest.raises(autolabel.TidakAdaObjek, match="429"):
        autolabel.dari_teks(ip, ["botol"], model="claude")


# ------------------------------------------------- antarmuka menyesuaikan

def test_kanvas_membawa_catatan_biaya():
    """Tidak semua model berbiaya UNDUHAN. Yang tak mengunduh apa pun tetapi
    memanggil layanan berbayar tiap gambar justru lebih perlu disebut."""
    import pathlib as _p

    h = _p.Path("app/templates/label.html").read_text()
    assert "data-catatan" in h

    js = _p.Path("app/static/label.js").read_text()
    blok = js.split("function biaya()", 1)[1].split("}", 1)[0]
    assert "catatan" in blok, "catatan harus menang atas keterangan unduhan"

    dialog = _p.Path("app/static/anotasi.js").read_text()
    assert "m.catatan" in dialog


def test_halaman_kanvas_menawarkan_claude_hanya_saat_hidup(klien, lingkungan,
                                                           monkeypatch):
    """Penjaga sebenarnya: yang dilihat orang di halaman, bukan isi dict."""
    import re

    from conftest import buat_dataset, masuk
    from tests.test_data import PW_PAUL
    from app.services import autolabel

    d = buat_dataset(lingkungan["ruang"] / "uji-claude", 2, 1)
    masuk(klien, "paul", PW_PAUL)
    klien.post("/setsrc", params={"path": str(d)})
    ip = sorted(d.glob("*.jpg"))[0]

    def pilihan(h):
        blok = re.search(r'<select id="teks-model".*?</select>', h, re.S)
        return re.findall(r'value="([^"]+)"', blok.group(0) if blok else "")

    monkeypatch.setattr(autolabel, "model_teks",
                        lambda settings=None: dict(autolabel.MODEL_TEKS))
    h = klien.get("/label", params={"path": str(ip)}).text
    assert "claude" not in pilihan(h)

    gabung = {**autolabel.MODEL_TEKS, **autolabel.MODEL_TEKS_BERSYARAT}
    monkeypatch.setattr(autolabel, "model_teks", lambda settings=None: gabung)
    h = klien.get("/label", params={"path": str(ip)}).text
    assert "claude" in pilihan(h)
    assert "berbayar" in h, "biayanya disebut di halaman, bukan hanya di API"
