"""
Foto latar ruang detektor milik projek.

Kenapa fitur ini ada, dalam satu angka: pada v13, sesudah augmentasi 50,3%
piksel data latih adalah putih polos, dan memindahkan objek yang sama ke latar
putih membuat akurasi melonjak dari 0/7 jadi 3/7. Latarnya yang jadi soal,
bukan objeknya. Aplikasi membawa 9 pelat bawaan, tetapi pelat itu berasal dari
SATU ruang detektor yang belum tentu ruang milik yang sedang melabeli.
"""
import io
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services import latar, olah
from tests.test_data import masuk, PW_PAUL


def _foto_ruang(bantalan=True, rona=(150, 80, 130), n=(480, 640)) -> bytes:
    """Foto 'ruang kosong' berderau, opsional dengan pita letterbox putih."""
    im = np.zeros((n[0], n[1], 3), np.uint8)
    im[:, :] = rona
    rng = np.random.default_rng(0)
    im = np.clip(im.astype(int) + rng.integers(-20, 20, im.shape),
                 0, 255).astype(np.uint8)
    if bantalan:
        im[:90] = 255
        im[-90:] = 255
    return cv2.imencode(".png", im)[1].tobytes()


# ============================================================
# PEMBUAT PELAT
# ============================================================

def test_satu_foto_jadi_sembilan_pelat(tmp_path):
    """Tiga posisi potongan x tiga tingkat terang, sama seperti v14."""
    im = cv2.imdecode(np.frombuffer(_foto_ruang(), np.uint8), cv2.IMREAD_COLOR)
    pelat = latar.buat_pelat(im)
    assert len(pelat) == 9
    assert all(p.shape == (latar.SISI, latar.SISI, 3) for p in pelat)


def test_bantalan_letterbox_dibuang(tmp_path):
    """Kalau pita polos ikut jadi pelat, kita menempelkan kembali bantalan
    polos yang justru sedang dihilangkan dari dataset."""
    im = cv2.imdecode(np.frombuffer(_foto_ruang(bantalan=True), np.uint8),
                      cv2.IMREAD_COLOR)
    dipotong_im, dipotong = latar.buang_bantalan(im)
    assert dipotong, "pita putih di atas-bawah tidak terdeteksi"
    assert dipotong_im.shape[0] < im.shape[0]
    # Tidak satu pun pelat boleh jadi bidang rata: itu tanda bantalan lolos.
    for p in latar.buat_pelat(im):
        assert p.std() > 3, "ada pelat yang polos — bantalan ikut terbawa"


def test_warna_lampu_dinetralkan(tmp_path):
    """Pelat yang masih membawa warna lampunya akan BERTUMPUK dengan warna
    lampu yang ditambahkan augmentasi: pelat ungu x lampu hijau = warna yang
    tidak pernah ada di RVM mana pun."""
    im = cv2.imdecode(np.frombuffer(_foto_ruang(bantalan=False), np.uint8),
                      cv2.IMREAD_COLOR)
    def selisih(a):
        mu = a.reshape(-1, 3).mean(0)
        return float(mu.max() - mu.min())
    assert selisih(im) > 30, "foto ujinya harus berona kuat dulu"
    assert selisih(latar.netralkan(im)) < 3, "gray-world tidak menetralkan"


def test_terangnya_benar_benar_bervariasi(tmp_path):
    """Kalau semua pelat sama terang, model menghafal latarnya."""
    im = cv2.imdecode(np.frombuffer(_foto_ruang(), np.uint8), cv2.IMREAD_COLOR)
    terang = sorted(float(p.mean()) for p in latar.buat_pelat(im))
    assert terang[-1] - terang[0] > 20, terang


# ============================================================
# SIMPANAN PER PROJEK
# ============================================================

def test_tambah_lalu_buang_membangun_ulang_pelatnya(tmp_path):
    d = tmp_path / "projek"
    d.mkdir()
    r = latar.tambah(d, "ruang.png", _foto_ruang())
    assert r["ok"] and r["n_foto"] == 1 and r["n_pelat"] == 9
    assert latar.ringkas(d)["n_pelat"] == 9
    # Dibuang: pelatnya ikut hilang, tidak ditinggal yatim. Pelat yatim tetap
    # dipakai augmentasi tanpa satu pun jejak asalnya.
    r = latar.buang(d, latar.ringkas(d)["foto"][0]["nama"])
    assert r["ok"] and r["n_pelat"] == 0
    assert latar.daftar_pelat(d) == []


def test_batas_tiga_foto_ditegakkan(tmp_path):
    d = tmp_path / "projek"
    d.mkdir()
    for i in range(latar.MAKS_FOTO):
        assert latar.tambah(d, f"r{i}.png", _foto_ruang())["ok"]
    r = latar.tambah(d, "keempat.png", _foto_ruang())
    assert not r["ok"] and "batas" in r["error"]
    assert len(latar.daftar_asli(d)) == latar.MAKS_FOTO


def test_berkas_rusak_ditolak_dengan_sebabnya(tmp_path):
    d = tmp_path / "projek"
    d.mkdir()
    assert not latar.tambah(d, "a.png", b"bukan gambar")["ok"]
    assert not latar.tambah(d, "a.txt", _foto_ruang())["ok"]
    assert latar.daftar_asli(d) == []


def test_folder_latar_bertitik_supaya_tidak_terbaca_sebagai_gambar(tmp_path):
    """Kalau foto latar terbaca sebagai gambar dataset, ia ikut dilabeli dan
    ikut terekspor — padahal isinya justru ruang kosong."""
    from app.services import scanner

    d = tmp_path / "projek"
    d.mkdir()
    cv2.imwrite(str(d / "asli.jpg"), np.full((60, 80, 3), 90, np.uint8))
    latar.tambah(d, "ruang.png", _foto_ruang())
    items, _ = scanner.scan(d)
    assert [i["img"].name for i in items] == ["asli.jpg"]
    assert latar.FOLDER.startswith(".")


# ============================================================
# DIPAKAI SAAT MEMBUAT VERSI
# ============================================================

def test_pelat_projek_ditambahkan_ke_bawaan_bukan_menggantikan(tmp_path):
    """Latar yang lebih beragam membuat model lebih sulit menghafal satu
    ruangan; membuang pelat bawaan berarti dataset kecil kehilangan variasi
    yang sudah ada tanpa mendapat gantinya."""
    d = tmp_path / "projek"
    d.mkdir()
    bawaan = len(olah.muat_pelat())
    assert bawaan > 0, "pelat bawaan harus ikut terbundel"
    assert len(olah.pelat_projek(d)) == bawaan
    latar.tambah(d, "ruang.png", _foto_ruang())
    assert len(olah.pelat_projek(d)) == bawaan + 9


# ============================================================
# RUTE
# ============================================================

def _projek(klien):
    ruang = Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = ruang / "latarku"
    d.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(d / "g0.jpg"), np.full((60, 80, 3), 90, np.uint8))
    (d / "g0.json").write_text(json.dumps({
        "version": "0.4.36", "flags": {}, "imagePath": "g0.jpg",
        "imageHeight": 60, "imageWidth": 80, "imageData": None,
        "shapes": [{"label": "botol", "shape_type": "polygon",
                    "points": [[5, 5], [40, 5], [40, 40], [5, 40]]}]}))
    klien.post(f"/setsrc?path={d}")
    return d


def test_alur_unggah_lewat_rute(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d = _projek(klien)

    r = klien.get("/api/latar").json()
    assert r["ok"] and r["foto"] == [] and r["n_bawaan"] > 0

    r = klien.put("/api/latar?name=ruang.png", content=_foto_ruang()).json()
    assert r["ok"] and r["n_pelat"] == 9, r

    r = klien.get("/api/latar").json()
    assert len(r["foto"]) == 1 and r["n_pelat"] == 9

    # Pratinjau memberi PELAT jadi, bukan foto aslinya: yang perlu dinilai
    # mata hasil olahannya.
    g = klien.get(f"/api/latar/pratinjau?nama={r['foto'][0]['nama']}")
    assert g.status_code == 200 and g.headers["content-type"] == "image/png"
    im = cv2.imdecode(np.frombuffer(g.content, np.uint8), cv2.IMREAD_COLOR)
    assert im.shape == (latar.SISI, latar.SISI, 3), im.shape

    r = klien.post(f"/api/latar/buang?nama={r['foto'][0]['nama']}").json()
    assert r["ok"] and r["n_pelat"] == 0


def test_rute_menolak_berkas_kosong_dan_bukan_gambar(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _projek(klien)
    assert not klien.put("/api/latar?name=a.png", content=b"").json()["ok"]
    assert not klien.put("/api/latar?name=a.png",
                         content=b"bukan gambar").json()["ok"]


def test_tanpa_dataset_terbuka_ditolak(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    assert not klien.get("/api/latar").json()["ok"]
    assert not klien.put("/api/latar?name=a.png",
                         content=_foto_ruang()).json()["ok"]


def test_formulirnya_ada_di_langkah_4_wizard(klien, lingkungan):
    """Di langkah 4 karena di sinilah pelatnya dipakai: fase crop/zoom dan
    skala yang menempel objek ke pelat latar."""
    masuk(klien, "paul", PW_PAUL)
    d = _projek(klien)
    h = klien.get(f"/versi?ds={d.name}").text
    blok = h.split('data-langkah="4"')[1].split('data-langkah="5"')[0]
    assert 'id="wz-latar"' in blok
    assert 'id="lt-berkas"' in blok and 'id="lt-daftar"' in blok
    assert "kosong" in blok, "formulirnya harus menyebut syarat ruang kosong"


def test_pelat_projek_benar_benar_terpakai_di_gambar_hasil(klien, lingkungan):
    """Bukti terakhir, dan satu-satunya yang benar-benar berarti.

    Semua yang di atas bisa lolos sementara pelatnya tidak pernah sampai ke
    satu piksel pun gambar hasil. Di sini foto latarnya dibuat HIJAU PEKAT --
    warna yang tidak ada di pelat bawaan maupun di gambar sumber -- lalu versi
    dibuat sungguhan dan gambar hasilnya diperiksa: kalau hijau itu muncul,
    pelat projek memang dipakai menempel objek.
    """
    from tests.test_versi_buat import _tunggu, _mulai

    masuk(klien, "paul", PW_PAUL)
    ruang = Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = ruang / "latar-pakai"
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(5)
    for i in range(40):
        im = (rng.random((240, 320, 3)) * 60 + 30).astype(np.uint8)
        # Objek BERTEKSTUR, bukan bidang polos: penjaga mutu
        # olah.objek_terbaca() memang menolak objek yang jadi polos, dan
        # objek uji tanpa tekstur membuat fase crop/zoom tidak pernah
        # menghasilkan apa pun -- gagal karena datanya, bukan karena kodenya.
        im[60:150, 80:170] = np.clip(
            np.array((40, 40, 220)) + rng.integers(-45, 45, (90, 90, 3)),
            0, 255).astype(np.uint8)
        cv2.imwrite(str(d / f"g{i}.jpg"), im)
        (d / f"g{i}.json").write_text(json.dumps({
            "version": "0.4.36", "flags": {}, "imagePath": f"g{i}.jpg",
            "imageHeight": 240, "imageWidth": 320, "imageData": None,
            "shapes": [{"label": "botol", "shape_type": "polygon",
                        "points": [[80, 60], [170, 60], [170, 150], [80, 150]]}]}))
    klien.post(f"/setsrc?path={d}")

    # Latar hijau pekat, dan TANPA dinetralkan supaya warnanya bertahan --
    # netralkan() memang tugasnya membuang rona seperti ini.
    hijau = np.zeros((480, 640, 3), np.uint8)
    hijau[:, :] = (20, 200, 20)
    hijau = np.clip(hijau.astype(int) + rng.integers(-8, 8, hijau.shape),
                    0, 255).astype(np.uint8)
    ad = latar.folder(d) / latar.ASLI
    ad.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(ad / "hijau.png"), hijau)
    for i, p in enumerate(latar.buat_pelat(hijau, netral=False)):
        pd = latar.folder(d) / latar.PELAT
        pd.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(pd / f"hijau_p{i:02d}.png"), p)
    assert len(latar.daftar_pelat(d)) == 9

    r = _mulai(klien, {"volume": {"per_gambar": 1},
                       "fase": {"crop_zoom": {"aktif": True, "porsi": 1.0}}})
    assert r["ok"], r
    assert _tunggu(klien).get("selesai")

    # Fase crop/zoom yang menempel ke pelat menulis berkas _p5crop / _p5zoom.
    v = d / ".versi" / "v1" / "train" / "images"
    tempel = [p for p in v.glob("*.jpg") if "_p5" in p.name]
    assert tempel, "tidak ada gambar hasil tempel; fase crop/zoom tidak jalan"
    hijau_terlihat = 0
    for p in tempel:
        im = cv2.imread(str(p)).astype(np.int16)
        # Piksel yang jelas-jelas hijau: G jauh di atas B dan R.
        h = ((im[:, :, 1] - im[:, :, 0] > 60) & (im[:, :, 1] - im[:, :, 2] > 60))
        if h.mean() > 0.05:
            hijau_terlihat += 1
    assert hijau_terlihat, (
        f"tidak satu pun dari {len(tempel)} gambar tempel memuat latar hijau — "
        "pelat projek tidak sampai ke gambar hasil")


# ============================================================
# MODE WARNA: DINETRALKAN vs APA ADANYA
# ============================================================

def _rona(a) -> float:
    """Seberapa berona, TIDAK ikut berubah saat gambarnya diredupkan.

    Diukur sebagai RASIO antarkanal, bukan selisihnya. Meredupkan 0,70x
    mengalikan ketiga kanal dengan angka yang sama, jadi selisih absolutnya
    ikut mengecil (70 -> 49) padahal ronanya tidak bergeser sedikit pun.
    Memakai selisih membuat "diredupkan" salah terbaca sebagai "warnanya
    diubah" — persis kebalikan dari yang sedang diperiksa.

    1,0 berarti abu-abu; makin besar makin berona.
    """
    mu = a.reshape(-1, 3).mean(0)
    return float(mu.max() / max(mu.min(), 1e-6))


def test_mode_asli_mempertahankan_warna_mode_netral_membuangnya():
    """Dua mode harus benar-benar berbeda hasilnya, bukan cuma beda label."""
    im = cv2.imdecode(np.frombuffer(_foto_ruang(bantalan=False), np.uint8),
                      cv2.IMREAD_COLOR)
    asal = _rona(im)
    assert asal > 1.5, f"foto ujinya harus berona kuat dulu ({asal:.2f})"
    netral = latar.buat_pelat(im, netral=True)
    apa_adanya = latar.buat_pelat(im, netral=False)
    assert len(netral) == len(apa_adanya) == 9
    # Dinetralkan: ronanya hilang.
    assert max(_rona(p) for p in netral) < 1.15, [
        round(_rona(p), 2) for p in netral]
    # Apa adanya: ronanya bertahan, dan TIDAK ada variasi suhu yang menggesernya
    # ke arah berbeda-beda.
    rona_asli = [_rona(p) for p in apa_adanya]
    assert min(rona_asli) > asal * 0.95, [round(v, 2) for v in rona_asli]


def test_mode_asli_tetap_meredupkan_dan_menerangkan():
    """Yang diminta 'jangan ubah warnanya', bukan 'jangan ubah apa pun'."""
    im = cv2.imdecode(np.frombuffer(_foto_ruang(bantalan=False), np.uint8),
                      cv2.IMREAD_COLOR)
    terang = sorted(float(p.mean()) for p in latar.buat_pelat(im, netral=False))
    assert terang[-1] - terang[0] > 20, terang


def test_mode_ikut_tersimpan_dan_terbaca_kembali(tmp_path):
    d = tmp_path / "projek"
    d.mkdir()
    assert latar.tambah(d, "a.png", _foto_ruang(), "asli")["ok"]
    assert latar.tambah(d, "b.png", _foto_ruang(), "netral")["ok"]
    mode = {f["mode"] for f in latar.ringkas(d)["foto"]}
    assert mode == {"asli", "netral"}, mode
    # Mode ngawur jatuh ke bawaan, bukan menggagalkan unggahan.
    assert latar.tambah(d, "c.png", _foto_ruang(), "ngawur")["ok"]
    assert latar.mode_dari("latar-3-netral.png") == "netral"


def test_mode_dikirim_lewat_rute(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    _projek(klien)
    assert klien.put("/api/latar?name=a.png&mode=asli",
                     content=_foto_ruang()).json()["ok"]
    assert klien.get("/api/latar").json()["foto"][0]["mode"] == "asli"


# ============================================================
# SEBARAN KELAS DI ATAS PELAT
# ============================================================

def _pekerjaan(n_pelat: int):
    from app.services.buatversi import Pekerjaan

    p = Pekerjaan.__new__(Pekerjaan)
    p.pelat = [np.zeros((8, 8, 3), np.uint8) for _ in range(n_pelat)]
    p._giliran = {}
    return p


KOTAK = [0.2, 0.2, 0.8, 0.2, 0.8, 0.8, 0.2, 0.8]


def test_tiap_kelas_tersebar_rata_ke_seluruh_pelat():
    """Kelas yang selalu mendarat di pelat yang sama adalah pintasan.

    Model belajar "ruangan ini berarti kelas itu" — bentuk kegagalan yang sama
    dengan v13 memakai warna sebagai pintasan kelas. Diukur pada 18 pelat dan
    5 kelas yang jumlah sampelnya timpang, karena kelas minoritaslah yang
    paling rawan: dengan pemilihan acak, kelas bersampel 6 hanya menyentuh 4
    dari 18 pelat.
    """
    from collections import Counter

    n_pelat = 18
    p = _pekerjaan(n_pelat)
    for kelas, n in ((0, 120), (1, 60), (2, 24), (3, 12), (4, 6)):
        c = Counter(p._pelat_giliran([(kelas, KOTAK)]) % n_pelat
                    for _ in range(n))
        # Tidak ada pelat yang dipakai dua kali sebelum semuanya kebagian.
        assert max(c.values()) - min(c.values()) <= 1, (kelas, dict(c))
        assert len(c) == min(n, n_pelat), (kelas, len(c))


def test_kelas_berbeda_tidak_berbagi_pelat_yang_sama():
    """Kalau semua kelas mulai dari nol, tiap langkah mereka berbagi pelat yang
    sama persis dan pemerataannya jadi semu."""
    p = _pekerjaan(18)
    for _ in range(4):
        baris = [p._pelat_giliran([(k, KOTAK)]) % 18 for k in range(5)]
        assert len(set(baris)) == 5, baris


def test_kelas_terbesar_di_gambar_yang_menentukan_gilirannya():
    """Gambar dengan satu botol besar dan satu tutup kecil pada dasarnya
    gambar botol; itu kelas yang perlu diratakan."""
    p = _pekerjaan(18)
    besar = [0.1, 0.1, 0.9, 0.1, 0.9, 0.9, 0.1, 0.9]
    kecil = [0.0, 0.0, 0.1, 0.0, 0.1, 0.1, 0.0, 0.1]
    # Kelas 7 mendominasi, jadi gilirannya yang maju — bukan kelas 2.
    p._pelat_giliran([(2, kecil), (7, besar)])
    assert 7 in p._giliran and 2 not in p._giliran, p._giliran


def test_tanpa_pelat_atau_tanpa_label_jatuh_ke_acak():
    """None berarti 'pilih acak', dan itu jalan keluar yang benar saat tidak
    ada yang bisa diratakan."""
    p = _pekerjaan(0)
    assert p._pelat_giliran([(0, KOTAK)]) is None
    p2 = _pekerjaan(18)
    assert p2._pelat_giliran([]) is None
