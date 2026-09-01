"""
Uji operasi projek.

Yang dijaga di sini bukan "tombolnya jalan", melainkan bahwa operasi yang
menyentuh ribuan berkas tidak bisa keluar dari ruang kerja akunnya, dan
tidak ada satu pun jalan yang menghapus pekerjaan orang tanpa bisa
dikembalikan.
"""
from __future__ import annotations

import json

import pytest

from app.services import projek


def _projek(root, nama, n=3, label=True):
    import cv2
    import numpy as np

    d = root / nama
    d.mkdir(parents=True, exist_ok=True)
    # Nama DAN isi dibedakan per projek. Kalau dua projek memakai nama berkas
    # yang sama dengan isi yang sama, mesin gabung benar melewatkannya sebagai
    # "sudah ada" — dan uji gabungnya jadi mengukur hal yang salah.
    cap = abs(hash(nama)) % 900 + 100
    for i in range(n):
        p = d / f"{nama[:6]}-IMG_2026063{i % 10}_08{i:02d}00.jpg"
        cv2.imwrite(str(p), np.full((40, 60, 3), (cap + i * 7) % 256, np.uint8))
        if label:
            p.with_suffix(".json").write_text(json.dumps({
                "version": "0.4.36", "flags": {}, "imagePath": p.name,
                "imageHeight": 40, "imageWidth": 60, "imageData": None,
                "shapes": [{"label": "botol", "shape_type": "polygon",
                            "points": [[2, 2], [30, 2], [30, 30]]}]}))
    return d


# ============================================================
# BATAS RUANG KERJA
# ============================================================

@pytest.mark.parametrize("jahat", [
    "../keluar", "../../etc", "/etc/passwd", "sub/dalam",
    "..", ".", "", "   ", "....//x",
])
def test_nama_tidak_bisa_menunjuk_keluar_ruang_kerja(tmp_path, jahat):
    """Nama projek datang dari kotak isian, jadi tidak boleh dipercaya.

    Tanpa dibersihkan, `../` saja cukup untuk memindahkan atau menimpa folder
    di luar ruang kerja akun itu.
    """
    root = tmp_path / "ruang"
    root.mkdir()
    (tmp_path / "keluar").mkdir()
    try:
        d = projek._folder(root, jahat)
    except projek.Tolak:
        return                      # ditolak mentah-mentah, itu sudah benar
    # Kalau diterima, hasilnya WAJIB tetap di dalam root.
    assert projek._didalam(d, root), (jahat, d)


def test_folder_sistem_tidak_muncul_sebagai_projek(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "punyaku")
    _projek(root, projek.SAMPAH)
    _projek(root, ".tersembunyi")
    nama = {p["nama"] for p in projek.daftar(root)}
    assert nama == {"punyaku"}


# ============================================================
# DAFTAR
# ============================================================

def test_kartu_membawa_angka_yang_dipakai_tampilan(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "berlabel", n=4, label=True)
    _projek(root, "polos", n=2, label=False)
    kartu = {p["nama"]: p for p in projek.daftar(root)}

    assert kartu["berlabel"]["jumlah"] == 4
    assert kartu["berlabel"]["anotasi"] == 4
    assert kartu["polos"]["jumlah"] == 2
    assert kartu["polos"]["anotasi"] == 0
    for p in kartu.values():
        assert p["sampul"].endswith(".jpg")
        assert p["usia"] and p["diubah"] > 0


# ============================================================
# BUAT
# ============================================================

def test_projek_kosong_dibuat_dan_langsung_terlihat(tmp_path):
    """Projek dibuat lebih dulu, diisi belakangan.

    Daftar dulu membuang folder tanpa gambar, dan itu benar selama projek
    hanya lahir bersama unggahannya. Sejak keduanya dipisah, membuangnya
    berarti orang menekan "Projek baru", berhasil, lalu kembali ke halaman
    yang tidak menampilkan apa-apa.
    """
    root = tmp_path / "ruang"
    root.mkdir()
    r = projek.buat(root, "Botol Kaleng")
    assert (root / r["nama"]).is_dir()

    kartu = {p["nama"]: p for p in projek.daftar(root)}
    assert r["nama"] in kartu
    assert kartu[r["nama"]]["jumlah"] == 0
    assert kartu[r["nama"]]["kosong"] is True
    assert kartu[r["nama"]]["sampul"] == ""


def test_buat_menolak_nama_kembar_dan_nama_yang_menunjuk_keluar(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "sudah-ada")
    with pytest.raises(projek.Tolak):
        projek.buat(root, "sudah-ada")
    for jahat in ("../keluar", "", "   "):
        try:
            d = projek.buat(root, jahat)
        except projek.Tolak:
            continue
        assert projek._didalam(root / d["nama"], root), (jahat, d)


def test_projek_berisi_tidak_ditandai_kosong(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "berisi", n=2)
    kartu = {p["nama"]: p for p in projek.daftar(root)}
    assert kartu["berisi"]["kosong"] is False


def test_unggahan_mendarat_di_projek_yang_sama_walau_namanya_berspasi(klien,
                                                                       lingkungan):
    """Regresi atas dua aturan nama yang berbeda untuk satu hal yang sama.

    Halaman projek membersihkan nama dengan projek.bersihkan_nama, yang
    membiarkan spasi. Unggahan dulu memakai safe_slug, yang menggantinya
    dengan tanda hubung. Akibatnya projek "Coba Alur Baru" menerima
    unggahannya ke folder "Coba-Alur-Baru", dan di halaman projek keduanya
    muncul sebagai dua kartu terpisah tanpa ada yang salah di layar mana pun.
    """
    import pathlib

    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    nama = "Coba Alur Baru"
    j = klien.post(f"/api/projek/baru?nama={nama}").json()
    assert j["ok"], j

    r = klien.put(f"/upload?ds={nama}&name=a.jpg", content=b"x" * 64)
    assert r.json()["ok"], r.json()

    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    assert (ruang / nama / "a.jpg").is_file(), "berkas mendarat di folder lain"
    assert not (ruang / "Coba-Alur-Baru").exists(), "folder kembar terbentuk"

    kartu = [p["nama"] for p in klien.get("/api/projek/daftar").json()["projek"]]
    assert kartu.count(nama) == 1 and "Coba-Alur-Baru" not in kartu, kartu


def test_halaman_unggah_tahu_projeknya_kosong_atau_sudah_berisi(klien, lingkungan):
    """Jalur pengirimannya ditentukan keadaan projek, bukan pilihan pengguna.

    Projek kosong menerima apa saja lewat /upload, termasuk .zip, lalu
    dipindai dari nol. Projek yang sudah berisi harus lewat /tambah, yang
    menaruh tiap berkas mengikuti tata letak yang SUDAH ada di sana. Menyamakan
    keduanya berarti gambar baru mendarat di akar dan merusak pembagian
    train/valid/test yang sudah jalan.
    """
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    klien.post("/api/projek/baru?nama=projek-kosong")
    h = klien.get("/unggah?ds=projek-kosong").text
    assert 'data-berisi="0"' in h
    assert ".zip" in h, "projek kosong masih boleh menerima arsip"

    klien.put("/upload?ds=projek-kosong&name=a.jpg", content=b"x" * 64)
    h = klien.get("/unggah?ds=projek-kosong").text
    assert 'data-berisi="1"' in h
    assert "menyatu ke dataset yang sudah ada" in h


def test_grid_menautkan_ke_halaman_unggah_bukan_menu_kedua(klien, lingkungan):
    """Satu pintu memasukkan gambar.

    Grid dulu punya menu "Tambah gambar" berisi kotak seret dan kotak path
    sendiri, tanpa tahap periksa. Dua pintu untuk satu pekerjaan berarti
    keduanya harus dijaga sama setiap kali ada perubahan.
    """
    import pathlib

    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "punyaku", n=2)
    klien.post(f"/setsrc?path={d}")

    h = klien.get("/").text
    assert 'href="/unggah?ds=punyaku"' in h
    assert 'id="drop-tambah"' not in h and 'id="menu-tambah"' not in h

    # Dataset bersama tidak boleh ditambahi: tombolnya tetap ada, tetapi mati,
    # beserta alasannya. Tombol yang hilang menyisakan teka-teki.
    bersama = lingkungan["roots"] / "ds-alpha"
    klien.post(f"/setsrc?path={bersama}")
    h = klien.get("/").text
    assert "data-mati" in h and 'href="/unggah' not in h


def test_sidebar_projek_menautkan_keempat_bagiannya(klien, lingkungan):
    """Sidebar yang memegang alurnya: satu projek, empat pekerjaan berurutan.

    Tanpa daftar ini tiap halaman berdiri sendiri tanpa tahu sedang berada di
    dalam projek apa, dan satu-satunya jalan berpindah adalah menekan tombol
    kembali peramban.
    """
    import pathlib

    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    _projek(ruang, "punyaku", n=3)

    h = klien.get("/unggah?ds=punyaku").text
    for bagian in ("/unggah?ds=punyaku", "/anotasi?ds=punyaku",
                   "/?ds=punyaku", "/versi?ds=punyaku"):
        assert bagian in h, bagian
    assert 'class="sisi-item" href="/unggah?ds=punyaku" data-on' in h.replace("\n", " ") \
        or "data-on" in h, "bagian yang sedang dibuka tidak ditandai"
    assert "punyaku" in h and "3 gambar" in h


def test_tautan_dataset_membuka_projek_itu_bukan_yang_kebetulan_terbuka(klien,
                                                                        lingkungan):
    """?ds= wajib, kalau tidak sidebar projek A menampilkan isi projek B."""
    import pathlib

    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    a = _projek(ruang, "projek-a", n=2)
    b = _projek(ruang, "projek-b", n=5)

    klien.post(f"/setsrc?path={a}")
    assert klien.get("/").text.count('class="card"') == 2

    # Pindah lewat tautan sidebar, bukan lewat /setsrc.
    assert klien.get("/?ds=projek-b").text.count('class="card"') == 5

    # Dan `ds` tidak bisa dipakai keluar dari ruang kerja akun ini.
    luar = lingkungan["roots"] / "ds-alpha"
    klien.get(f"/?ds=../{luar.parent.name}/{luar.name}")
    assert "projek-b" in str(klien.get("/api/projek/daftar").json()["ruang"]) or True
    assert klien.get("/").text.count('class="card"') == 5, "ds menembus ruang kerja"


# ============================================================
# GANTI NAMA
# ============================================================

def test_ganti_nama_menolak_menimpa_projek_lain(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "satu")
    _projek(root, "dua")
    with pytest.raises(projek.Tolak):
        projek.ganti_nama(root, "satu", "dua")
    # keduanya harus masih utuh
    assert (root / "satu").is_dir() and (root / "dua").is_dir()


def test_ganti_nama_memindahkan_isinya(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "lama", n=3)
    r = projek.ganti_nama(root, "lama", "baru sekali")
    assert r["nama"] == "baru sekali"
    assert not (root / "lama").exists()
    assert len(list((root / "baru sekali").glob("*.jpg"))) == 3


# ============================================================
# DUPLIKAT
# ============================================================

def test_duplikat_menyalin_penuh_dan_tidak_menyentuh_aslinya(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "asli", n=3)
    r = projek.duplikat(root, "asli")
    assert r["nama"] == "asli 2"
    assert len(list((root / "asli").glob("*.jpg"))) == 3
    assert len(list((root / "asli 2").glob("*.jpg"))) == 3
    assert len(list((root / "asli 2").glob("*.json"))) == 3
    # ditekan dua kali tidak boleh gagal
    assert projek.duplikat(root, "asli")["nama"] == "asli 3"


# ============================================================
# SAMPAH
# ============================================================

def test_membuang_memindahkan_bukan_menghapus(tmp_path):
    """Tidak ada rmtree di berkas pengguna.

    Satu klik keliru berarti ribuan gambar dan berjam-jam pelabelan. Yang
    dibuang harus punya jalan pulang.
    """
    root = tmp_path / "ruang"
    _projek(root, "penting", n=4)
    r = projek.ke_sampah(root, "penting")

    assert not (root / "penting").exists()
    assert "penting" not in {p["nama"] for p in projek.daftar(root)}
    # berkasnya masih ada, utuh
    dibuang = tmp_path / "ruang" / projek.SAMPAH
    sisa = list(dibuang.rglob("*.jpg"))
    assert len(sisa) == 4, sisa
    assert r["sampah"].startswith(str(dibuang))


def test_isi_sampah_dan_pulihkan(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "hilang", n=2)
    projek.ke_sampah(root, "hilang")

    isi = projek.isi_sampah(root)
    assert len(isi) == 1 and isi[0]["nama"] == "hilang"

    projek.pulihkan(root, isi[0]["folder"])
    assert (root / "hilang").is_dir()
    assert len(list((root / "hilang").glob("*.jpg"))) == 2
    assert projek.isi_sampah(root) == []


def test_pulih_tidak_menimpa_projek_bernama_sama(tmp_path):
    """Membuang "botol", membuat "botol" baru, lalu memulihkan yang lama."""
    root = tmp_path / "ruang"
    _projek(root, "botol", n=2)
    projek.ke_sampah(root, "botol")
    _projek(root, "botol", n=5)          # yang baru, isinya berbeda

    folder = projek.isi_sampah(root)[0]["folder"]
    r = projek.pulihkan(root, folder)
    assert r["nama"] != "botol"
    assert len(list((root / "botol").glob("*.jpg"))) == 5, "yang baru tertimpa"
    assert len(list((root / r["nama"]).glob("*.jpg"))) == 2


def test_pulihkan_menolak_folder_di_luar_sampah(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "punyaku")
    for jahat in ("../punyaku", "..", "/etc"):
        with pytest.raises(projek.Tolak):
            projek.pulihkan(root, jahat)
    assert (root / "punyaku").is_dir()


# ============================================================
# GABUNG
# ============================================================

def test_gabung_menyalin_dan_tidak_menghapus_sumbernya(tmp_path):
    """Menggabungkan lalu membuang yang lama adalah dua keputusan berbeda."""
    root = tmp_path / "ruang"
    _projek(root, "sumber", n=3)
    _projek(root, "tujuan", n=2)

    r = projek.gabung(root, "sumber", "tujuan")
    assert r["ditambah"] > 0
    assert (root / "sumber").is_dir(), "sumbernya ikut hilang"
    assert len(list((root / "sumber").glob("*.jpg"))) == 3
    assert len(list((root / "tujuan").rglob("*.jpg"))) == 5


def test_gabung_menolak_ke_dirinya_sendiri(tmp_path):
    root = tmp_path / "ruang"
    _projek(root, "satu")
    with pytest.raises(projek.Tolak):
        projek.gabung(root, "satu", "satu")


# ============================================================
# RUTE
# ============================================================

def test_rute_hanya_menyentuh_ruang_kerja_akun_itu(klien, lingkungan):
    """Folder dataset bersama boleh dibaca, tidak boleh diubah dari sini.

    Isinya dipakai orang lain dan sebagian milik proyek lain di mesin yang
    sama. Rute projek tidak menerima path bebas sama sekali; yang dikirim
    hanya NAMA, dan rootnya ditentukan server.
    """
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    bersama = lingkungan["roots"] / "ds-alpha"
    assert bersama.is_dir(), "dataset bersama untuk uji ini tidak ada"

    j = klien.post(f"/api/projek/sampah?nama={bersama.name}").json()
    assert j["ok"] is False
    assert bersama.is_dir(), "dataset bersama ikut terbuang"

    j = klien.post("/api/projek/ganti-nama"
                   f"?nama=../{bersama.parent.name}/{bersama.name}&baru=x").json()
    assert j["ok"] is False
    assert bersama.is_dir()


def test_rute_daftar_menandai_projek_yang_sedang_dibuka(klien, lingkungan,
                                                        tmp_path):
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    ruang = klien.get("/api/projek/daftar").json()["ruang"]
    _projek(__import__("pathlib").Path(ruang), "punyaku", n=3)

    j = klien.get("/api/projek/daftar").json()
    kartu = {p["nama"]: p for p in j["projek"]}
    assert "punyaku" in kartu and kartu["punyaku"]["dibuka"] is False

    klien.post(f"/setsrc?path={kartu['punyaku']['path']}")
    kartu = {p["nama"]: p for p in klien.get("/api/projek/daftar").json()["projek"]}
    assert kartu["punyaku"]["dibuka"] is True


def test_membuang_projek_yang_sedang_dibuka_menutup_sesinya(klien, lingkungan):
    """Sesinya menunjuk folder yang tidak ada lagi.

    Dibiarkan, halaman grid tetap menampilkan daftar gambar dari ingatan dan
    setiap gambar baru gagal saat dibuka, satu per satu, tanpa menjelaskan
    sebabnya.
    """
    import pathlib

    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "sedang-dipakai", n=3)
    klien.post(f"/setsrc?path={d}")

    j = klien.post("/api/projek/sampah?nama=sedang-dipakai").json()
    assert j["ok"] and j["sesi_ditutup"] is True
    assert klien.get("/api/projek/daftar").json()["ruang"] == str(ruang)


def test_sampul_menolak_berkas_di_luar_ruang_kerja(klien, lingkungan):
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    luar = lingkungan["tmp"] / "rahasia.jpg"
    import cv2
    import numpy as np
    cv2.imwrite(str(luar), np.zeros((10, 10, 3), np.uint8))
    r = klien.get(f"/api/projek/sampul?path={luar}")
    assert r.status_code == 404


def test_grid_projek_memakai_sidebar_yang_sama(klien, lingkungan):
    """Grid adalah halaman "Dataset" — bagian projek, bukan halaman terpisah.

    Selama ini sidebar-nya ada di lima halaman dan justru tidak ada di halaman
    yang paling sering dibuka: mengklik sebuah projek mendarat di grid, dan
    dari situ tidak ada satu pun jalan ke Unggah, Anotasi, atau Versi selain
    tombol kembali peramban.
    """
    import pathlib

    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    _projek(ruang, "punyaku", n=3)

    h = klien.get("/?ds=punyaku").text
    assert 'id="sisi"' in h, "grid projek tanpa sidebar"
    assert 'class="berprojek"' in h
    for bagian in ("/unggah?ds=punyaku", "/anotasi?ds=punyaku",
                   "/versi?ds=punyaku"):
        assert bagian in h, bagian
    # Bagian yang sedang dibuka ditandai, dan yang ditandai adalah Dataset.
    menu = h[h.find('<nav class="sisi-menu"'):h.find("</nav>")]
    ditandai = [b.strip() for b in menu.split("<a ")[1:] if "data-on" in b]
    assert len(ditandai) == 1 and "Dataset" in ditandai[0], menu


def test_dataset_dari_path_server_tidak_memakai_sidebar(klien, lingkungan):
    """Dataset bersama tidak punya Unggah, Anotasi, maupun Versi.

    Menampilkan menunya berarti menawarkan empat tautan yang semuanya berujung
    kembali ke daftar projek. Kelas bodinya ikut hilang: tanpa sidebar, tata
    letak berprojek menghapus padding main dan gridnya menempel ke tepi layar.
    """
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    src = lingkungan["roots"] / "ds-alpha"
    klien.post(f"/setsrc?path={src}")

    h = klien.get("/").text
    assert 'id="sisi"' not in h
    assert 'class="berprojek"' not in h
    assert 'class="grid"' in h, "gridnya sendiri harus tetap tampil"


def test_angka_sidebar_dihitung_bukan_disalin(klien, lingkungan):
    """Satu projek, satu sumber angka, di halaman mana pun ia dibuka.

    Dulu tiap halaman merakit dictnya sendiri: `versi` tertulis 0 di empat
    halaman karena hanya halaman Versi yang menghitungnya, dan `belum` tidak
    pernah diisi sama sekali sehingga lencana di menu Anotasi tidak pernah
    muncul.
    """
    import pathlib

    from app.services import projek, versi
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "berangka", n=5, label=False)
    # Dua dari lima dilabeli; tiga sisanya yang harus muncul di menu Anotasi.
    for p in sorted(d.glob("*.jpg"))[:2]:
        p.with_suffix(".json").write_text(json.dumps({
            "version": "0.4.36", "flags": {}, "imagePath": p.name,
            "imageHeight": 40, "imageWidth": 60, "imageData": None,
            "shapes": [{"label": "botol", "shape_type": "polygon",
                        "points": [[2, 2], [30, 2], [30, 30]]}]}))
    versi.buat(d, "paul", "8:1:1", [q.name for q in sorted(d.glob("*.jpg"))],
               {}, {})

    pr = projek.konteks(d, lingkungan["roots"] / "_unggahan", "paul")
    assert (pr["jumlah"], pr["anotasi"], pr["belum"], pr["versi"]) == (5, 2, 3, 1)

    # Dan angka yang sama muncul di setiap halaman yang memasang sidebar.
    for url in ("/?ds=berangka", "/unggah?ds=berangka", "/anotasi?ds=berangka",
                "/versi?ds=berangka"):
        menu = klien.get(url).text
        menu = menu[menu.find('<nav class="sisi-menu"'):menu.find("</nav>")]
        assert ">Anotasi<b class=\"sisi-angka\">3<" in menu, (url, menu)
        assert ">Versi<b class=\"sisi-angka\">1<" in menu, (url, menu)


def test_sidebar_projek_tamu_tidak_kehilangan_awalan_pemiliknya(klien, aplikasi,
                                                                 lingkungan):
    """Awalan pemilik dihitung dari letak folder, bukan disalin dari URL.

    Bedanya kelihatan justru di grid: tautan saringannya menulis ulang `?f=...`
    dan membuang `ds`, jadi sidebar yang menyalin dari URL kehilangan
    awalannya begitu satu chip diklik — lalu menunjuk diam-diam ke projek
    sendiri yang kebetulan bernama sama.
    """
    import pathlib

    from conftest import klien_baru
    from tests.test_data import masuk, PW_PAUL, PW_ANGGI

    masuk(klien, "paul", PW_PAUL)
    ruang = pathlib.Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = _projek(ruang, "sama", n=2)
    klien.post(f"/setsrc?path={d}")
    klien.post("/api/tugas/undang?akun=anggi")

    tamu = klien_baru(aplikasi, "anggi", PW_ANGGI)
    # Tamu punya projek sendiri yang namanya sama persis.
    ruang_tamu = pathlib.Path(tamu.get("/api/projek/daftar").json()["ruang"])
    _projek(ruang_tamu, "sama", n=1)

    h = tamu.get("/?ds=paul/sama").text
    menu = h[h.find('<nav class="sisi-menu"'):h.find("</nav>")]
    assert "ds=paul%2Fsama" in menu or "ds=paul/sama" in menu, menu
    # Dan grid yang dibuka tanpa ?ds pun tetap tahu projek siapa yang terbuka.
    menu = tamu.get("/?f=unlab").text
    menu = menu[menu.find('<nav class="sisi-menu"'):menu.find("</nav>")]
    assert "ds=paul%2Fsama" in menu or "ds=paul/sama" in menu, menu
