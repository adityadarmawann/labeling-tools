"""Dua tab, dua projek, satu akun, satu peramban.

Dilaporkan dari pemakaian: "tab 1 aku buka projek botol, tab 2 projek
badminton, kok pas di-refresh selalu ke projek yang terakhir dibuka?"

Sebabnya bukan di peramban. Tautan navigasi SUDAH membawa ?ds= (sidebar dan
subnav projek begitu semua), tetapi server hanya memegang SATU projek termuat
per akun: tiap halaman memanggil sess.load(d) begitu ds-nya berbeda, dan
pemanggilan API dari halaman itu tidak membawa ds sama sekali sehingga
dijawab dari projek yang kebetulan termuat terakhir.

Akibatnya dua hal yang terasa berbeda tetapi berakar sama:

  1. Tab yang lebih lama ditolak saat bekerja, "berkas tidak dikenal di
     dataset ini", karena gambarnya dicari di daftar projek tab sebelah.
  2. /label tidak punya parameter ds sama sekali, jadi refresh di sana selalu
     mengikuti projek terakhir, bukan projek tab itu.

Yang diuji di sini PERILAKU dua tab, bukan bentuk dalamannya: dua projek
harus bisa hidup berdampingan dalam satu sesi, dan tiap permintaan dilayani
oleh projek yang disebut URL-nya.
"""
from __future__ import annotations

import pathlib

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL


def _dua_projek(lingkungan) -> tuple[pathlib.Path, pathlib.Path]:
    ruang = lingkungan["ruang"]
    return (buat_dataset(ruang / "botol", 3, 1),
            buat_dataset(ruang / "badminton", 3, 1))


def test_dua_tab_tidak_saling_menggeser(klien, lingkungan):
    """Tab 1 harus tetap bisa bekerja sesudah tab 2 membuka projek lain."""
    botol, badminton = _dua_projek(lingkungan)
    masuk(klien, "paul", PW_PAUL)

    assert klien.get("/anotasi", params={"ds": "botol"}).status_code == 200
    assert klien.get("/anotasi", params={"ds": "badminton"}).status_code == 200

    r = klien.get("/api/label-data",
                  params={"path": str(botol / "botol-00.jpg"), "ds": "botol"})
    j = r.json()
    assert j.get("ok") is True, j.get("error")
    assert j["nama"] == "botol-00.jpg"


def test_dua_projek_hidup_berdampingan(klien, lingkungan):
    """Bolak-balik antar tab tak boleh membuat salah satunya gugur."""
    botol, badminton = _dua_projek(lingkungan)
    masuk(klien, "paul", PW_PAUL)
    klien.get("/anotasi", params={"ds": "botol"})
    klien.get("/anotasi", params={"ds": "badminton"})

    for _ in range(2):
        a = klien.get("/api/label-data",
                      params={"path": str(botol / "botol-01.jpg"),
                              "ds": "botol"}).json()
        b = klien.get("/api/label-data",
                      params={"path": str(badminton / "badminton-01.jpg"),
                              "ds": "badminton"}).json()
        assert a.get("ok") is True, a.get("error")
        assert b.get("ok") is True, b.get("error")
        assert a["nama"] == "botol-01.jpg"
        assert b["nama"] == "badminton-01.jpg"


def test_kanvas_bertahan_saat_dimuat_ulang(klien, lingkungan):
    """Refresh di /label harus tetap di projek tab itu, bukan yang terakhir."""
    botol, _ = _dua_projek(lingkungan)
    masuk(klien, "paul", PW_PAUL)
    klien.get("/anotasi", params={"ds": "botol"})
    klien.get("/anotasi", params={"ds": "badminton"})

    r = klien.get("/label", params={"path": str(botol / "botol-00.jpg"),
                                    "ds": "botol"}, follow_redirects=False)
    assert r.status_code == 200, f"{r.status_code} -> {r.headers.get('location')}"
    assert "botol-00.jpg" in r.text


def test_tanpa_ds_perilakunya_tak_berubah(klien, lingkungan):
    """Penjaga: permintaan lama yang tak membawa ds tetap dilayani projek
    terakhir, supaya tak ada jalur yang putus saat ds belum ikut terkirim."""
    botol, badminton = _dua_projek(lingkungan)
    masuk(klien, "paul", PW_PAUL)
    klien.get("/anotasi", params={"ds": "botol"})
    klien.get("/anotasi", params={"ds": "badminton"})

    j = klien.get("/api/label-data",
                  params={"path": str(badminton / "badminton-00.jpg")}).json()
    assert j.get("ok") is True, j.get("error")


def test_thumbnail_tab_lama_tetap_terbaca(klien, lingkungan):
    """Thumbnail dipanggil sekali per gambar, dan ia memakai sess.find.

    Kalau pemilih tampilan tidak bekerja, gejalanya bukan pesan galat
    melainkan kisi yang kotak-kotaknya kosong di tab yang lebih lama.
    """
    botol, _ = _dua_projek(lingkungan)
    masuk(klien, "paul", PW_PAUL)
    klien.get("/anotasi", params={"ds": "botol"})
    klien.get("/anotasi", params={"ds": "badminton"})

    r = klien.get("/thumb", params={"path": str(botol / "botol-00.jpg"),
                                    "s": 160, "ds": "botol"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")


def test_lebih_dari_batas_tampilan_tetap_dilayani(klien, lingkungan):
    """Tab ke-lima tak boleh gagal, hanya memindai ulang projeknya.

    Tampilan dibatasi supaya ingatan tidak tumbuh tanpa batas; yang dilepas
    daftar gambarnya, bukan haknya atas projek itu.
    """
    from app.session import MAKS_TAMPILAN
    ruang = lingkungan["ruang"]
    nama = [f"p{i}" for i in range(MAKS_TAMPILAN + 1)]
    for n in nama:
        buat_dataset(ruang / n, 2, 1)
    masuk(klien, "paul", PW_PAUL)
    for n in nama:
        assert klien.get("/anotasi", params={"ds": n}).status_code == 200

    # Projek pertama sudah terlepas dari ingatan; ia harus dimuat lagi, bukan
    # ditolak.
    j = klien.get("/api/label-data",
                  params={"path": str(ruang / nama[0] / f"{nama[0]}-00.jpg"),
                          "ds": nama[0]}).json()
    assert j.get("ok") is True, j.get("error")


def test_halaman_menyebut_projeknya_untuk_sisi_klien(klien, lingkungan):
    """Halaman harus menyebut projeknya sendiri, supaya permintaan yang dibuat
    JS tidak bergantung pada projek mana yang kebetulan termuat terakhir."""
    _dua_projek(lingkungan)
    masuk(klien, "paul", PW_PAUL)
    h = klien.get("/anotasi", params={"ds": "botol"}).text
    assert '<meta name="projek" content="botol">' in h
    assert "window.PROJEK_DS" in h, "pembungkus permintaan harus terpasang"


def test_tautan_kanvas_dari_kisi_membawa_projek(klien, lingkungan):
    """Tanpa ini, membuka kanvas dari kisi akan kehilangan projeknya begitu
    tab sebelah membuka projek lain."""
    import re
    _dua_projek(lingkungan)
    masuk(klien, "paul", PW_PAUL)
    h = klien.get("/", params={"ds": "botol"}).text
    tautan = re.findall(r'href="(/label\?path=[^"]+)"', h)
    assert tautan, "kisi harus punya tautan ke kanvas"
    assert all("ds=botol" in t for t in tautan), tautan[:2]


def test_konteks_kanvas_meneruskan_ds():
    """Penjaga sepadan dengan test_kunci_url_sama_dengan_yang_diteruskan_label_js:
    kunci yang tertinggal di label.js membuat panah kiri/kanan diam-diam
    berpindah projek."""
    js = pathlib.Path("app/static/label.js").read_text()
    blok = js.split("const KONTEKS", 1)[1].split("})();", 1)[0]
    assert "'ds'" in blok


def test_berkas_kelas_terpisah_per_projek(klien, lingkungan):
    """Daftar kelas untuk AnyLabeling disimpan di folder milik AKUN.

    Dengan satu nama berkas untuk semua projek, membuka AnyLabeling dari tab
    A akan menyodorkan kelas projek tab B, dan memuat projek di satu tab akan
    menghapus daftar milik tab lain.
    """
    from app.session import store
    _dua_projek(lingkungan)
    masuk(klien, "paul", PW_PAUL)
    klien.get("/anotasi", params={"ds": "botol"})
    klien.get("/anotasi", params={"ds": "badminton"})

    ak = next(iter(store._data.values()))
    berkas = {str(v.labelfile) for v in ak.tampilan.values() if v.labelfile}
    assert len(berkas) == 2, f"dua projek harus punya berkasnya sendiri: {berkas}"
    assert all(pathlib.Path(b).exists() for b in berkas), berkas


def test_memilih_projek_di_tab_baru_tak_menggeser_tab_lama(klien, lingkungan):
    """Pemilih projek memanggil /setsrc dengan PATH, tanpa menyebut nama
    projek di URL-nya. Kalau projek itu dimuat ke tampilan yang terakhir
    dipakai, tab lain mendapati projeknya berganti tanpa ia berbuat apa pun.
    """
    botol, badminton = _dua_projek(lingkungan)
    masuk(klien, "paul", PW_PAUL)
    klien.get("/anotasi", params={"ds": "botol"})

    r = klien.post("/setsrc", params={"path": str(badminton)})
    assert r.json().get("ok") is True, r.text[:200]

    j = klien.get("/api/label-data",
                  params={"path": str(botol / "botol-00.jpg"),
                          "ds": "botol"}).json()
    assert j.get("ok") is True, j.get("error")


def test_autologin_juga_menghormati_projek_di_url(aplikasi, lingkungan,
                                                  monkeypatch):
    """Jalur autologin dev membuat sesi baru tiap permintaan tanpa kuki.

    Kalau ia tidak ikut melihat ?ds=, sesinya lahir tanpa projek dan rute API
    menjawab "gambar tidak ada di dataset ini" untuk permintaan yang sudah
    menyebut projeknya. Terbukti di server dev, dan tak tertangkap uji lain
    karena uji lain selalu login berkuki.
    """
    from fastapi.testclient import TestClient

    from app.config import get_settings

    botol, _ = _dua_projek(lingkungan)
    monkeypatch.setenv("LABELAPP_DEV_AUTOLOGIN", "paul")
    get_settings.cache_clear()
    try:
        lokal = TestClient(aplikasi, client=("127.0.0.1", 50000))
        j = lokal.get("/api/label-data",
                      params={"path": str(botol / "botol-00.jpg"),
                              "ds": "botol"}).json()
        assert j.get("ok") is True, j.get("error")
    finally:
        get_settings.cache_clear()
