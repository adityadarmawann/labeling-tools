"""Gerbang login, isolasi antar akun, dan penjagaan endpoint desktop."""
from __future__ import annotations

import re
from pathlib import Path

from conftest import PW_ANGGI, PW_PAUL, ROOT, klien_baru, masuk


# ---------------------------------------------------------------- gerbang login

def test_halaman_tanpa_sesi_dialihkan_ke_login(klien):
    for jalan in ("/", "/pilih", "/view?path=/x.jpg"):
        r = klien.get(jalan, follow_redirects=False)
        assert r.status_code == 303, jalan
        # Tujuan semula ikut dibawa. Tanpa itu tautan undangan lenyap di
        # tengah alurnya sendiri; lihat login_redirect di app/deps.py.
        assert r.headers["location"].startswith("/login")


def test_endpoint_json_tanpa_sesi_menjawab_401(klien):
    assert klien.post("/rescan").status_code == 401
    assert klien.post("/setsrc?path=/tmp").status_code == 401
    assert klien.put("/upload?ds=x&name=a.png", content=b"x").status_code == 401


def test_password_salah_ditolak(klien):
    r = klien.post("/login", data={"user": "paul", "pw": "ngawur"},
                   follow_redirects=False)
    assert r.status_code == 401


def test_nama_akun_tidak_peka_huruf_besar(aplikasi):
    for tulisan in ("paul", "Paul", "PAUL", " paul "):
        klien_baru(aplikasi, tulisan, PW_PAUL)      # assert ada di masuk()


def test_login_sudah_ada_sesi_dialihkan_ke_beranda(klien):
    masuk(klien, "paul", PW_PAUL)
    r = klien.get("/login", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


# ---------------------------------------------------------------- isolasi

def test_dua_akun_punya_dataset_sendiri(aplikasi, lingkungan):
    roots = lingkungan["roots"]
    a = klien_baru(aplikasi, "paul", PW_PAUL)
    b = klien_baru(aplikasi, "anggi", PW_ANGGI)

    assert a.post(f"/setsrc?path={roots / 'ds-alpha'}").json()["n"] == 4
    assert b.post(f"/setsrc?path={roots / 'ds-beta'}").json()["n"] == 3

    ha, hb = a.get("/").text, b.get("/").text
    assert "ds-alpha" in ha and "ds-beta" not in ha
    assert "ds-beta" in hb and "ds-alpha" not in hb
    assert ha.count('class="card"') == 4
    assert hb.count('class="card"') == 3
    assert "<b>paul</b>" in ha
    assert "<b>anggi</b>" in hb


def test_akun_tidak_bisa_melihat_gambar_dataset_akun_lain(aplikasi, lingkungan):
    roots = lingkungan["roots"]
    a = klien_baru(aplikasi, "paul", PW_PAUL)
    b = klien_baru(aplikasi, "anggi", PW_ANGGI)
    a.post(f"/setsrc?path={roots / 'ds-alpha'}")
    b.post(f"/setsrc?path={roots / 'ds-beta'}")

    gambar = roots / "ds-alpha" / "ds-alpha-00.jpg"
    assert a.get(f"/thumb?path={gambar}").status_code == 200
    assert b.get(f"/thumb?path={gambar}").status_code == 404
    assert b.get(f"/view?path={gambar}").status_code == 404


def test_berkas_di_luar_dataset_tidak_bisa_dibaca(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")
    for jahat in ("/etc/passwd", "/etc/shadow", str(ROOT / "app" / "security.py")):
        assert klien.get(f"/thumb?path={jahat}").status_code == 404, jahat


def test_thumbnail_dipisah_per_akun(aplikasi, lingkungan):
    roots, thumb = lingkungan["roots"], lingkungan["tmp"] / "thumb"
    a = klien_baru(aplikasi, "paul", PW_PAUL)
    b = klien_baru(aplikasi, "anggi", PW_ANGGI)
    a.post(f"/setsrc?path={roots / 'ds-alpha'}")
    b.post(f"/setsrc?path={roots / 'ds-beta'}")
    a.get(f"/thumb?path={roots / 'ds-alpha' / 'ds-alpha-00.jpg'}")
    b.get(f"/thumb?path={roots / 'ds-beta' / 'ds-beta-00.jpg'}")

    assert (thumb / "paul").is_dir()
    assert (thumb / "anggi").is_dir()
    assert list((thumb / "paul").glob("*.jpg"))
    assert list((thumb / "anggi").glob("*.jpg"))


def test_logout_hanya_memutus_akun_itu(aplikasi, lingkungan):
    roots, thumb = lingkungan["roots"], lingkungan["tmp"] / "thumb"
    a = klien_baru(aplikasi, "paul", PW_PAUL)
    b = klien_baru(aplikasi, "anggi", PW_ANGGI)
    a.post(f"/setsrc?path={roots / 'ds-alpha'}")
    b.post(f"/setsrc?path={roots / 'ds-beta'}")
    a.get(f"/thumb?path={roots / 'ds-alpha' / 'ds-alpha-00.jpg'}")

    a.get("/logout", follow_redirects=False)
    assert a.get("/", follow_redirects=False).status_code == 303
    assert b.get("/").status_code == 200
    # thumbnail milik paul ikut dibuang, milik anggi tetap
    assert not (thumb / "paul").exists()
    assert (thumb / "anggi").exists()


# ---------------------------------------------------------------- penjagaan lokal

def test_endpoint_desktop_ditolak_dari_jaringan(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    gambar = lingkungan["roots"] / "ds-alpha" / "ds-alpha-00.jpg"
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")

    for jalan in (f"/open?path={gambar}", "/pickdir"):
        r = klien.post(jalan)
        assert r.status_code == 403, jalan
        assert "mesin server" in r.json()["detail"]


def test_endpoint_desktop_lolos_penjagaan_dari_localhost(klien_lokal, lingkungan):
    masuk(klien_lokal, "paul", PW_PAUL)
    gambar = lingkungan["roots"] / "ds-alpha" / "ds-alpha-00.jpg"
    klien_lokal.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")

    r = klien_lokal.post(f"/open?path={gambar}")
    # AnyLabeling belum tentu terpasang; yang diuji adalah tidak lagi 403.
    assert r.status_code == 200
    assert r.json()["ok"] is False or r.json()["ok"] is True


def test_tombol_desktop_disembunyikan_dari_jaringan(klien, klien_lokal, lingkungan):
    src = lingkungan["roots"] / "ds-alpha"
    masuk(klien, "paul", PW_PAUL)
    masuk(klien_lokal, "anggi", PW_ANGGI)
    klien.post(f"/setsrc?path={src}")
    klien_lokal.post(f"/setsrc?path={src}")

    assert "openIn(" not in klien.get("/").text
    assert "openIn(" in klien_lokal.get("/").text

    # Tombol penjelajah berkas pindah dari dialog "Projek baru" ke halaman
    # Unggah data, tetapi aturannya tidak berubah: jendelanya tampil di layar
    # SERVER, jadi ia tidak ada gunanya bagi siapa pun yang membuka lewat
    # jaringan, dan hanya membingungkan kalau tetap ditampilkan.
    klien.post("/api/projek/baru?nama=uji-desktop")
    klien_lokal.post("/api/projek/baru?nama=uji-desktop-lokal")
    assert 'id="ug-server-jelajah"' not in klien.get("/unggah?ds=uji-desktop").text
    assert 'id="ug-server-jelajah"' in klien_lokal.get(
        "/unggah?ds=uji-desktop-lokal").text


def test_dokumentasi_api_tidak_terbuka_tanpa_login(klien, aplikasi):
    """/openapi.json menyebutkan SETIAP rute beserta nama parameternya.

    Bawaan FastAPI membukanya untuk siapa saja yang bisa menjangkau portnya,
    termasuk daftar rute kelola akun, tanpa login sama sekali. Firewall memang
    membatasi siapa yang bisa menjangkau, tetapi itu lapis yang berbeda dan
    tidak berlaku bagi siapa pun yang sudah berada di jaringan itu.
    """
    from fastapi.testclient import TestClient

    tamu = TestClient(aplikasi)
    for jalur in ("/openapi.json", "/docs"):
        r = tamu.get(jalur, follow_redirects=False)
        assert r.status_code in (302, 303, 307), (jalur, r.status_code)
        assert "/login" in r.headers.get("location", ""), jalur

    masuk(klien, "paul", PW_PAUL)
    r = klien.get("/openapi.json")
    assert r.status_code == 200 and "paths" in r.json()
    assert klien.get("/docs").status_code == 200


# ---------------------------------------------------------------- penjaga insiden

def test_setelan_uji_seluruhnya_menunjuk_ke_tmp(klien, lingkungan):
    """
    Pendamping fixture folder_aplikasi_tak_berubah: memastikan aplikasi yang
    diuji benar-benar membaca berkas akun, dataset, unggahan, dan thumbnail
    dari tmp_path — bukan dari folder aplikasi yang berisi akun sungguhan.
    """
    from app.config import get_settings

    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")
    klien.get("/")

    st = get_settings()
    tmp = lingkungan["tmp"]
    for label, p in (("users_file", st.users_file),
                     ("datasets_root", st.datasets_root),
                     ("uploads_root", st.uploads_root),
                     ("thumb_root", st.thumb_root)):
        assert tmp in Path(p).parents or Path(p) == tmp, f"{label} di luar tmp: {p}"
        assert ROOT not in Path(p).parents, f"{label} menunjuk ke folder aplikasi: {p}"


def test_item_dataset_bisa_diklik(klien, lingkungan):
    """
    Path dataset harus lewat data-path, bukan disisipkan ke string JavaScript
    di dalam atribut onclick. Cara itu pernah membuat SELURUH daftar dataset
    tidak bisa diklik: tojson menghasilkan tanda kutip ganda biasa, dan itu
    menutup atribut onclick lebih awal sehingga HTML-nya rusak.
    """
    from html.parser import HTMLParser

    masuk(klien, "paul", PW_PAUL)
    html = klien.get("/pilih").text

    class Ambil(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ds = []

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            # Kartu dataset bersama. Kelasnya berganti dari "ds" jadi
            # "pnama pnama-link" saat halaman pilih dirombak; yang dijaga uji
            # ini bukan nama kelasnya, melainkan bahwa pathnya dibawa
            # data-path dan bukan disisipkan ke string JavaScript.
            if tag == "a" and d.get("data-path"):
                self.ds.append(d)

    p = Ambil()
    p.feed(html)
    assert p.ds, "tidak ada item dataset di halaman"
    for d in p.ds:
        # parser HTML sungguhan berhasil membaca path-nya utuh
        assert d.get("data-path", "").startswith("/"), d
        assert "onclick" not in d, "path jangan disisipkan ke onclick"
    # tidak ada lagi kutip ganda yang menutup atribut lebih awal
    assert 'onclick="setsrc("' not in html


def test_header_proxy_membatalkan_status_lokal(klien_lokal, lingkungan):
    """
    Di belakang reverse proxy pada mesin yang sama, semua permintaan datang
    dari 127.0.0.1. Tanpa pemeriksaan header proxy, endpoint yang membuka
    jendela di layar server akan terbuka untuk siapa saja lewat domain.
    """
    masuk(klien_lokal, "paul", PW_PAUL)
    src = lingkungan["roots"] / "ds-alpha"
    gambar = src / "ds-alpha-00.jpg"
    klien_lokal.post(f"/setsrc?path={src}")

    # tanpa header proxy: dianggap lokal, lolos penjagaan
    assert klien_lokal.post(f"/open?path={gambar}").status_code == 200

    # dengan header proxy: ditolak walau soketnya 127.0.0.1
    for h in ("X-Forwarded-For", "X-Real-IP", "Forwarded",
              "X-Forwarded-Host", "X-Forwarded-Proto"):
        r = klien_lokal.post(f"/open?path={gambar}", headers={h: "103.182.240.26"})
        assert r.status_code == 403, h
        assert "mesin server" in r.json()["detail"], h

    # tombol desktop juga hilang dari HTML lewat proxy
    html = klien_lokal.get("/", headers={"X-Forwarded-For": "103.182.240.26"}).text
    assert "openIn(" not in html


# ---------------------------------------------------------------- autologin dev

def test_autologin_tanpa_setelan_tetap_minta_login(klien_lokal, monkeypatch):
    """Tanpa LABELAPP_DEV_AUTOLOGIN, tidak ada jalan pintas."""
    r = klien_lokal.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_autologin_hanya_dari_mesin_itu_sendiri(aplikasi, lingkungan, monkeypatch):
    """
    Ini penjagaan yang membuat setelan dev aman dipasang: kalau sampai tersalin
    ke produksi, anggota tim dari jaringan TETAP harus login. Yang bisa
    memakainya hanya orang yang sudah berada di mesin server.
    """
    from fastapi.testclient import TestClient

    from app.config import get_settings

    monkeypatch.setenv("LABELAPP_DEV_AUTOLOGIN", "paul")
    get_settings.cache_clear()
    try:
        # dari mesin itu sendiri: langsung masuk tanpa password
        lokal = TestClient(aplikasi, client=("127.0.0.1", 50000))
        r = lokal.get("/", follow_redirects=False)
        assert r.status_code in (200, 303), r.status_code
        if r.status_code == 303:
            assert r.headers["location"] != "/login"

        # dari jaringan: tetap ditolak
        jauh = TestClient(aplikasi)
        r = jauh.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")

        # lewat reverse proxy pada mesin yang sama: juga ditolak
        r = lokal.get("/", follow_redirects=False,
                      headers={"X-Forwarded-For": "103.182.240.26"})
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
    finally:
        get_settings.cache_clear()


def test_autologin_akun_tidak_ada_ditolak(aplikasi, monkeypatch):
    """Nama akun yang tidak terdaftar tidak boleh jadi jalan masuk."""
    from fastapi.testclient import TestClient

    from app.config import get_settings

    monkeypatch.setenv("LABELAPP_DEV_AUTOLOGIN", "hantu-tidak-terdaftar")
    get_settings.cache_clear()
    try:
        lokal = TestClient(aplikasi, client=("127.0.0.1", 50000))
        r = lokal.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
    finally:
        get_settings.cache_clear()


def test_berkas_static_dirujuk_dengan_cap_versi(klien):
    """
    Regresi. Berkas static dikirim tanpa Cache-Control, jadi peramban menebak
    sendiri berapa lama ia boleh menyimpannya — dan tebakannya sering "lama".
    Akibatnya halaman memuat HTML terbaru bersama JavaScript LAMA: tombol baru
    muncul tetapi diam saja saat diklik. Cap versi dari waktu-ubah berkasnya
    membuat URL-nya berubah sendiri setiap kali isinya berubah.
    """
    import re

    from conftest import PW_PAUL, masuk

    masuk(klien, "paul", PW_PAUL)
    html = klien.get("/pilih").text
    rujukan = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
    assets = [u for u in rujukan if u.split("?")[0].endswith((".js", ".css"))]
    assert assets, rujukan
    for u in assets:
        assert re.search(r"\?v=[0-9a-f]+$", u), f"tanpa cap versi: {u}"
        # dan berkasnya tetap bisa diambil dengan cap itu
        assert klien.get(u).status_code == 200, u


def test_cap_versi_berubah_saat_berkasnya_berubah(tmp_path, monkeypatch):
    """Cap yang tidak pernah berubah sama saja dengan tidak ada cap.

    Dikerjakan pada berkas di tmp_path, bukan pada berkas aplikasi: penjaga di
    conftest melarang tes menyentuh folder aplikasi, dan larangan itu benar —
    mengubah waktu-ubah app.js dari dalam tes akan merembet ke tes lain.
    """
    import os
    import re

    from app import templating

    berkas = tmp_path / "app.js"
    berkas.write_text("// uji\n")
    monkeypatch.setattr(templating, "STATIC_DIR", tmp_path)

    awal = templating.statik("app.js")
    assert re.match(r"^/static/app\.js\?v=[0-9a-f]+$", awal), awal

    st = berkas.stat()
    os.utime(berkas, (st.st_mtime + 60, st.st_mtime + 60))
    assert templating.statik("app.js") != awal, "cap tidak berubah padahal berkasnya berubah"

    # berkas yang tidak ada tidak boleh menggagalkan render halaman
    assert templating.statik("tidak-ada.js").endswith("?v=0")


def test_tambah_impor_tidak_bisa_menyedot_projek_akun_lain(klien, aplikasi,
                                                           lingkungan):
    """Rute kembarannya menolak; yang ini dulu menjawab "ok".

    /setsrc, /impor, dan /api/impor/survei semuanya memeriksa boleh_buka.
    /tambah/impor terlewat, dan itu membuatnya jadi satu-satunya jalan
    menyalin projek PRIBADI akun lain ke ruang kerja sendiri — lalu
    mengekspornya sebagai milik sendiri, lengkap dengan berkas anotasinya.
    """
    import pathlib

    from conftest import klien_baru
    from tests.test_data import masuk, PW_ANGGI, PW_PAUL
    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang_paul = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    milikku = _projek(ruang_paul, "punyaku-rahasia", n=2)

    lain = klien_baru(aplikasi, "anggi", PW_ANGGI)
    ruang_anggi = pathlib.Path(lain.get("/api/projek/daftar").json()["ruang"])
    tujuan = _projek(ruang_anggi, "penampung", n=1)
    lain.post(f"/setsrc?path={tujuan}")

    r = lain.post(f"/tambah/impor?path={milikku}").json()
    assert r["ok"] is False, r
    tersalin = [q.name for q in tujuan.glob("*.jpg")]
    assert len(tersalin) == 1, f"projek orang tersedot: {tersalin}"

    # Jalur yang SAH tidak ikut tertutup: mengimpor dari projek sendiri, dan
    # dari folder dataset bersama.
    # label=False supaya yang disalin cuma gambarnya: _projek yang berlabel
    # menyalin .json-nya juga, dan angkanya jadi dua.
    sumberku = _projek(ruang_anggi, "sumber-sendiri", n=1, label=False)
    assert lain.post(f"/tambah/impor?path={sumberku}").json()["ditambah"] == 1
    bersama = lingkungan["roots"] / "ds-beta"
    assert lain.post(f"/tambah/impor?path={bersama}").json()["ok"] is True


def test_markbg_membaca_disk_bukan_ingatan_sesi(klien, aplikasi, lingkungan):
    """Menandai latar tidak boleh menghapus pekerjaan orang.

    Ingatan sesi hanya diperbarui untuk orang yang menyimpannya sendiri, jadi
    sesi orang lain tetap mengira gambarnya kosong. Memeriksa ingatan membuat
    penolakan ini gagal justru pada satu-satunya kasus yang penting: dua orang
    di projek yang sama, dan yang satu menghapus anotasi yang lain tanpa satu
    pun peringatan.
    """
    import json
    import pathlib

    from conftest import klien_baru
    from tests.test_data import masuk, PW_ANGGI, PW_PAUL
    from tests.test_projek import _projek

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "sesi-basi", n=2, label=False)
    klien.post(f"/setsrc?path={d}")
    g = sorted(str(q) for q in d.glob("*.jpg"))
    klien.post("/api/tugas/undang?akun=anggi")
    klien.post("/api/tugas/bagi", json={"pelabel": "anggi", "gambar": g})

    # Sesi anggi memuat projek SEBELUM paul melabeli.
    lain = klien_baru(aplikasi, "anggi", PW_ANGGI)
    lain.get("/anotasi?ds=paul/sesi-basi")
    klien.post("/api/simpan", json={"path": g[0], "shapes": [
        {"label": "botol", "shape_type": "rectangle",
         "points": [[2, 2], [30, 30]]}]})

    jp = pathlib.Path(g[0]).with_suffix(".json")
    for panggil in (lambda: lain.post(f"/markbg?path={g[0]}"),
                    lambda: lain.post("/api/latar", json={"gambar": [g[0]]})):
        r = panggil().json()
        assert r["ok"] is False, r
        assert json.loads(jp.read_text())["shapes"], "anotasi terhapus"

    # Gambar yang memang kosong tetap boleh ditandai latar.
    assert lain.post(f"/markbg?path={g[1]}").json()["ok"] is True
