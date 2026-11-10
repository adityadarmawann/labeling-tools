"""Konteks saringan kisi Dataset harus terbawa ke kanvas.

Keluhan nyata pelabel: menyaring kisi, membuka satu gambar untuk diperbaiki,
lalu menekan berikutnya dan mendarat di gambar yang TIDAK ada di saringan tadi.
Terukur sebelum diperbaiki: kisi menampilkan 00, 02, 04 sementara kanvas
melompat dari 00 ke 01.

Halaman job sudah membawa konteksnya sejak dulu (makro konteks() di job.html);
kisi Dataset tidak membawa satu parameter pun. Modul services/konteks_kisi.py
menyatukan aturannya supaya tidak ada salinan kedua yang diam-diam berbeda.
"""
from __future__ import annotations

import json
import pathlib
import re
import urllib.parse as up

from conftest import buat_dataset, masuk
from tests.test_data import PW_PAUL
from app.services import konteks_kisi


def _tulis(ip: pathlib.Path, perlu_dicek: bool) -> None:
    isi = {"version": "0.4.3", "flags": {}, "imagePath": ip.name,
           "imageHeight": 64, "imageWidth": 64,
           "shapes": [{"label": "botol", "text": "", "group_id": None,
                       "shape_type": "rectangle", "flags": {},
                       "points": [[4, 4], [20, 20]]}]}
    if perlu_dicek:
        isi["perlu_dicek"] = True
    ip.with_suffix(".json").write_text(json.dumps(isi))


def _siapkan(klien, ruang, nama="ctx", n=6):
    d = buat_dataset(ruang / nama, n, 0)
    klien.post(f"/setsrc?path={d}")
    ips = sorted(d.glob("*.jpg"))
    # Selang-seling: genap = belum diverifikasi, ganjil = terverifikasi,
    # yang terakhir dibiarkan belum dilabeli.
    for i, ip in enumerate(ips[:-1]):
        _tulis(ip, perlu_dicek=(i % 2 == 0))
    klien.post("/api/dataset/masukkan", json={"gambar": [str(p) for p in ips]})
    return d, ips


def _nama_dari(html: str, pola: str) -> list[str]:
    return [pathlib.Path(up.unquote(x)).name
            for x in re.findall(pola, html)]


def test_tautan_kartu_membawa_saringan(klien, lingkungan):
    """Tautannya sendiri yang harus membawa konteks. Kalau hanya rutenya yang
    bisa membacanya, pengguna tak punya cara memakainya."""
    masuk(klien, "paul", PW_PAUL)
    d, _ = _siapkan(klien, lingkungan["ruang"], "ctx-tautan")
    h = klien.get("/", params={"f": "dicek"}).text
    tautan = re.findall(r'href="(/label\?path=[^"]+)"', h)
    assert tautan, "tak ada tautan ke kanvas"
    assert all("f=dicek" in t for t in tautan), tautan[:2]


def test_panah_kanvas_mengikuti_saringan(klien, lingkungan):
    """Inti keluhannya, diukur ujung ke ujung lewat tautan asli kisinya."""
    masuk(klien, "paul", PW_PAUL)
    d, ips = _siapkan(klien, lingkungan["ruang"], "ctx-panah")

    h = klien.get("/", params={"f": "dicek"}).text
    urut_kisi = _nama_dari(h, r'/label\?path=([^"&]+)')
    assert len(urut_kisi) >= 2, urut_kisi

    tautan = re.findall(r'href="(/label\?path=[^"]+)"', h)[0]
    kanvas = klien.get(tautan.replace("&amp;", "&")).text
    lanjut = re.search(r'"next"\s*:\s*"([^"]+)"', kanvas)
    assert lanjut, "kanvas tak menyebut tetangga berikutnya"
    assert pathlib.Path(up.unquote(lanjut.group(1))).name == urut_kisi[1]


def test_panah_tak_keluar_dari_saringan_sampai_habis(klien, lingkungan):
    """Ditelusuri sampai ujung: tiap langkah harus tetap di dalam subset."""
    masuk(klien, "paul", PW_PAUL)
    d, ips = _siapkan(klien, lingkungan["ruang"], "ctx-habis")
    h = klien.get("/", params={"f": "dicek"}).text
    subset = _nama_dari(h, r'/label\?path=([^"&]+)')

    jalan = [subset[0]]
    kini = re.findall(r'href="(/label\?path=[^"]+)"', h)[0].replace("&amp;", "&")
    for _ in range(len(subset) + 2):
        teks = klien.get(kini).text
        m = re.search(r'"next"\s*:\s*"([^"]+)"', teks)
        if not m:
            break
        nama = pathlib.Path(up.unquote(m.group(1))).name
        jalan.append(nama)
        kini = f"/label?path={up.quote(up.unquote(m.group(1)))}&f=dicek"
    assert jalan == subset, (jalan, subset)


def test_posisi_dihitung_atas_subset_bukan_projek(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d, ips = _siapkan(klien, lingkungan["ruang"], "ctx-posisi")
    h = klien.get("/", params={"f": "dicek"}).text
    subset = _nama_dari(h, r'/label\?path=([^"&]+)')
    tautan = re.findall(r'href="(/label\?path=[^"]+)"', h)[0].replace("&amp;", "&")
    teks = klien.get(tautan).text
    m = re.search(r'id="lab-urut-n"[^>]*>\s*(\d+)\s*/\s*(\d+)', teks)
    assert m, "penunjuk posisi tak ditemukan"
    assert int(m.group(2)) == len(subset), (m.group(0), subset)


def test_tanpa_saringan_navigasi_tetap_penuh(klien, lingkungan):
    """Perilaku lama dipertahankan saat tak ada saringan aktif."""
    masuk(klien, "paul", PW_PAUL)
    d, ips = _siapkan(klien, lingkungan["ruang"], "ctx-polos")
    teks = klien.get("/label", params={"path": str(ips[0])}).text
    m = re.search(r'id="lab-urut-n"[^>]*>\s*(\d+)\s*/\s*(\d+)', teks)
    assert m and int(m.group(2)) == len(ips), (m.group(0) if m else None, len(ips))


# ------------------------------------------------------------- unit modul
def test_baca_menormalkan_parameter():
    p = konteks_kisi.baca({"f": "dicek", "s": "objek-banyak", "q": "  abc ",
                           "m": "dan", "bt": "sesi-pagi"})
    assert p["f"] == "dicek" and p["urut"] == "objek-banyak"
    assert p["cari"] == "abc" and p["mode"] == "dan" and p["batch"] == "sesi-pagi"


def test_ada_lingkup_membedakan_polos_dari_tersaring():
    assert not konteks_kisi.ada_lingkup(konteks_kisi.baca({}))
    assert konteks_kisi.ada_lingkup(konteks_kisi.baca({"f": "dicek"}))
    assert konteks_kisi.ada_lingkup(konteks_kisi.baca({"s": "objek-banyak"}))
    assert konteks_kisi.ada_lingkup(konteks_kisi.baca({"q": "frame"}))


def test_kunci_url_sama_dengan_yang_diteruskan_label_js():
    """Kunci yang tertinggal di label.js membuat panah PERTAMA benar lalu panah
    kedua diam-diam kembali menyusuri seluruh projek."""
    akar = pathlib.Path(__file__).resolve().parents[1]
    js = (akar / "app" / "static" / "label.js").read_text()
    blok = js.split("const KONTEKS", 1)[1].split("})();", 1)[0]
    for k in konteks_kisi.KUNCI_URL:
        assert f"'{k}'" in blok, f"label.js tak meneruskan {k!r}"


# --------------------------------------------------- halaman Lihat (/view)
def test_lihat_panah_mengikuti_saringan(klien, lingkungan):
    """Alur verifikasi borongan: saring, buka Lihat, panah kanan berulang.
    Dulu putus di langkah kedua karena /view menyusuri sess.items penuh."""
    masuk(klien, "paul", PW_PAUL)
    d, ips = _siapkan(klien, lingkungan["ruang"], "ctx-lihat")
    h = klien.get("/", params={"f": "dicek"}).text
    subset = _nama_dari(h, r'/label\?path=([^"&]+)')
    tautan = re.findall(r'href="(/view\?path=[^"]+)"', h)[0].replace("&amp;", "&")
    assert "f=dicek" in tautan                      # kartunya membawa konteks

    teks = klien.get(tautan).text
    lanjut = re.search(r'id="next"[^>]*href="(/view\?path=[^"]+)"', teks)
    assert lanjut, "panah berikutnya tak ada"
    url = lanjut.group(1).replace("&amp;", "&")
    assert pathlib.Path(up.unquote(url.split("path=")[1].split("&")[0])).name \
        == subset[1]
    assert "f=dicek" in url                         # dan ia meneruskannya lagi


def test_lihat_kembali_mempertahankan_saringan(klien, lingkungan):
    """Tombol Kembali dulu di-hardcode ke "/": saringan, urutan, dan halaman
    hilang semua, dan pemeriksa harus merakit ulang saringannya tiap kali."""
    masuk(klien, "paul", PW_PAUL)
    d, ips = _siapkan(klien, lingkungan["ruang"], "ctx-balik")
    klien.get("/")                      # alur nyata: kisi dibuka lebih dulu
    teks = klien.get("/view", params={"path": str(ips[0]), "f": "dicek",
                                      "s": "objek-banyak"}).text
    m = re.search(r'class="[^"]*lh-back[^"]*"\s+href="([^"]+)"', teks)
    assert m, "tombol kembali tak ditemukan"
    url = m.group(1).replace("&amp;", "&")
    assert "f=dicek" in url and "s=objek-banyak" in url, url


def test_lihat_ke_kanvas_tak_membuang_konteks(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d, ips = _siapkan(klien, lingkungan["ruang"], "ctx-lanjut")
    klien.get("/")
    teks = klien.get("/view", params={"path": str(ips[0]), "f": "dicek"}).text
    m = re.search(r'href="(/label\?path=[^"]+)"', teks)
    assert m and "f=dicek" in m.group(1), m.group(1) if m else None


def test_ke_query_dan_baca_saling_membalik():
    awal = {"f": "dicek", "s": "objek-banyak", "q": "frame 1", "m": "dan",
            "c": ["botol", "kaleng"], "bt": "sesi pagi"}
    p = konteks_kisi.baca(awal)
    q = konteks_kisi.ke_query(p, "?")
    import urllib.parse
    balik = konteks_kisi.baca(
        {k: (v if len(v) > 1 else v[0])
         for k, v in urllib.parse.parse_qs(q.lstrip("?")).items()})
    assert balik == p


def test_ke_query_kosong_saat_tak_ada_saringan():
    assert konteks_kisi.ke_query(konteks_kisi.baca({})) == ""


def test_lihat_tak_pecah_saat_subset_kosong(klien, lingkungan):
    """Gambar yang baru saja keluar dari saringannya tetap bisa dibuka.
    Halaman yang MELEMPAR 500 jauh lebih buruk daripada bilah kemajuan nol."""
    masuk(klien, "paul", PW_PAUL)
    d, ips = _siapkan(klien, lingkungan["ruang"], "ctx-kosong")
    klien.get("/")
    # Saringan yang pasti tak memuat gambar mana pun di projek ini.
    r = klien.get("/view", params={"path": str(ips[0]), "f": "dicek",
                                   "q": "tak-ada-berkas-bernama-begini"})
    assert r.status_code == 200
