"""
Uji mesin splitting anti-bocor.

Yang dijaga di sini bukan "kodenya jalan", melainkan satu janji tunggal:
tidak ada foto di valid atau test yang pernah dilihat model saat latihan —
baik lewat foto yang sama persis, sesi pemotretan yang sama, maupun hasil
augmentasi dari foto yang sama.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.services import split


class Nama(str):
    """Pengganti Path yang cukup untuk mesin splitting: ia hanya perlu `.name`."""

    @property
    def name(self) -> str:
        return str(self)


def butir(nama: str, *label: str) -> dict:
    return {"img": Nama(nama), "shapes": [{"label": l} for l in label]}


# ============================================================
# KUNCI SESI
# ============================================================

@pytest.mark.parametrize("nama, diharap", [
    ("IMG_20260630_085207_jpg.rf.8e4u4mvMcV15IqU8WYQI.jpg", "ts:20260630_0852"),
    ("20240506_170457.jpg", "ts:20240506_1704"),
    ("2024-05-06 17.04.57.jpg", "ts:20240506_1704"),
    ("foto-tanpa-waktu.jpg", "file:foto-tanpa-waktu"),
])
def test_kunci_sesi_hanya_mengenali_penanda_waktu(nama, diharap):
    assert split.kunci_sesi(nama, "menit") == diharap


def test_augmentasi_dan_salinan_ikut_sesi_foto_aslinya():
    """`foo_aug1` dan `foo - Copy` adalah foto yang sama dengan `foo`.

    Kalau ketiganya dianggap berkas berbeda, satu bisa mendarat di train dan
    satu di valid — dan model dinilai memakai versi lain dari gambar yang
    sudah dia pelajari.
    """
    dasar = "IMG_20260630_085207_jpg.rf.abc123"
    kunci = {split.kunci_sesi(n, "menit") for n in (
        f"{dasar}.jpg", f"{dasar}_aug1.jpg", f"{dasar}_bal4_216.jpg",
        f"{dasar} - Copy.jpg", f"{dasar} (2).jpg")}
    assert len(kunci) == 1, kunci


def test_granularitas_dipilih_paling_kasar_yang_masih_bisa_dibelah():
    """Yang kasar lebih aman; yang halus hanya dipakai kalau terpaksa.

    Dua skrip acuan mematok granularitas dan dua-duanya jadi salah di dataset
    yang lain: per-jam mustahil untuk dataset yang seluruhnya diambil dalam
    satu jam, per-menit memecah sesi yang sebenarnya satu.
    """
    # Terbentang berhari-hari: per-hari sudah cukup.
    berhari = [f"IMG_202606{d:02d}_{j:02d}0000.jpg"
               for d in range(1, 26) for j in range(4)]
    assert split.pilih_granularitas(berhari)[0] == "hari"

    # Seluruhnya satu jam, seperti paragon: hanya per-menit yang memberi grup.
    sejam = [f"IMG_20260630_08{m:02d}{d:02d}.jpg"
             for m in range(30, 60) for d in range(5)]
    assert split.pilih_granularitas(sejam)[0] == "menit"

    # Sehari penuh, 200 sesi. Per-hari MUAT di bawah ambang 40% lama, tapi
    # grupnya (240 gambar) jauh lebih besar daripada kuota valid (200), jadi
    # rasionya pasti melompat. Per-jam yang benar di sini.
    sehari = [f"IMG_202606{1 + s // 24:02d}_{s % 24:02d}{k:02d}00.jpg"
              for s in range(200) for k in range(10)]
    assert split.pilih_granularitas(sehari, (0.8, 0.1, 0.1))[0] == "jam"


# ============================================================
# PENCARIAN KEMBARAN
# ============================================================

def _kasar(acuan, uji, amb):
    """Sekali hitung, tanpa petak — acuan kebenaran untuk versi berpetak."""
    A = np.array(list(acuan.values()), dtype=np.uint8)
    return {j for j, h in uji.items()
            if split._BIT[np.bitwise_xor(A, h[None, :])].sum(1).min() <= amb}


def test_hash_256_bit():
    """32 byte. Pada 8 byte, kembaran sungguhan dan foto berbeda tumpang
    tindih (maks 14 vs min 11 — terukur di dua dataset), jadi tidak ada
    ambang yang benar."""
    assert split.BIT_HASH == 256
    h = split._hash_dari(np.arange(256, dtype=np.uint8).reshape(16, 16))
    assert h.nbytes == 32


def test_pencarian_berpetak_sama_dengan_sekali_hitung(monkeypatch):
    """Memecah perhitungan jadi petak tidak boleh mengubah hasilnya.

    Petaknya sengaja dikecilkan supaya jalur berpetak benar-benar dilalui;
    dengan ukuran bawaan, dataset uji sekecil ini muat dalam satu petak dan
    kesalahan penjahitan antar-petak tidak akan pernah terlihat.
    """
    monkeypatch.setattr(split, "SEL_PER_PETAK", 64)
    rng = np.random.default_rng(20260821)
    for _ in range(8):
        acuan = {i: rng.integers(0, 256, 32, dtype=np.uint8)
                 for i in range(int(rng.integers(40, 200)))}
        uji = {1000 + i: rng.integers(0, 256, 32, dtype=np.uint8)
               for i in range(int(rng.integers(40, 200)))}
        # Kembaran sungguhan, sebagian tepat di ambang.
        for k in list(uji)[:20]:
            h = acuan[int(rng.integers(0, len(acuan)))].copy()
            for _ in range(int(rng.integers(0, split.AMBANG_KEMBAR + 12))):
                h[int(rng.integers(0, 32))] ^= 1 << int(rng.integers(0, 8))
            uji[k] = h
        amb = split.AMBANG_KEMBAR
        assert split.cari_kembar(acuan, uji, amb) == _kasar(acuan, uji, amb)


def _dataset_gambar(tmp, n_sesi, per_sesi, sisi=240, mirip=False):
    """Tulis gambar sungguhan, satu potret per sesi."""
    import cv2

    rng = np.random.default_rng(4)

    def potret():
        """Citra berstruktur lembut, seperti foto — bukan derau acak.

        Derau acak murni hancur total oleh kompresi JPEG: dHash-nya bergeser
        ~105 dari 256 bit, hampir sejauh dua gambar yang tak berhubungan.
        Fixture semacam itu membuat kalibrasi tampak gagal padahal yang salah
        gambar ujinya. Foto sungguhan berisi bidang berfrekuensi rendah, dan
        itulah yang ditiru di sini.
        """
        kecil = rng.integers(40, 215, (6, 6, 3), dtype=np.uint8)
        return cv2.resize(kecil, (sisi, sisi), interpolation=cv2.INTER_CUBIC)

    items, sesi = [], []
    dasar = potret()
    for s in range(n_sesi):
        pola = dasar if mirip else potret()
        for k in range(per_sesi):
            nama = f"IMG_202606{1 + s // 24:02d}_{s % 24:02d}{k:02d}00.jpg"
            p = tmp / nama
            cv2.imwrite(str(p), pola)
            items.append({"img": p, "shapes": [{"label": "botol"}]})
            sesi.append(split.kunci_sesi(nama, "jam"))
    return items, sesi


def test_kalibrasi_memakai_kedua_distribusi_dataset_itu_sendiri(tmp_path):
    """Ambangnya harus muat di antara "wajib tertangkap" dan "wajib lolos".

    Keduanya diukur dari dataset yang sama. Versi pertama memakai pembanding
    lintas-dataset — foto produk berbeda di ruangan berbeda — dan itu terlalu
    mudah: ambangnya tersetel 72, lalu valid botol-kaleng terkuras dari
    11.319 gambar jadi 36.
    """
    items, sesi = _dataset_gambar(tmp_path, n_sesi=20, per_sesi=3)
    sidik = {i: split.dhash(it["img"]) for i, it in enumerate(items)}
    sidik = {i: h for i, h in sidik.items() if h is not None}

    ambang, k = split.kalibrasi_ambang(items, sidik, sesi, contoh=20)
    assert 1 <= ambang <= split.AMBANG_MAKS
    assert k["pasangan"] >= 20 and k["pasangan_beda"] > 0
    # Potret tiap sesi berbeda, jadi kedua distribusinya harus terpisah...
    assert k["terpisah"], k
    # ...dan ambangnya duduk di antaranya.
    assert k["kembaran_p99"] <= ambang <= k["beda_p1"], k


def test_kalibrasi_mengaku_saat_foto_dataset_terlalu_mirip(tmp_path):
    """Kalau semua foto praktis sama, tidak ada ambang yang memisahkan —
    dan itu harus dikatakan, bukan disembunyikan di balik angka."""
    items, sesi = _dataset_gambar(tmp_path, n_sesi=20, per_sesi=3, mirip=True)
    sidik = {i: split.dhash(it["img"]) for i, it in enumerate(items)}
    sidik = {i: h for i, h in sidik.items() if h is not None}
    ambang, k = split.kalibrasi_ambang(items, sidik, sesi, contoh=20)
    assert k["terpisah"] is False, k


# ============================================================
# PEMBAGIAN
# ============================================================

def _dataset(n_sesi: int, per_sesi: int, kelas=("botol", "kaleng")) -> list[dict]:
    out = []
    for s in range(n_sesi):
        for k in range(per_sesi):
            nama = f"IMG_202606{1 + s // 24:02d}_{s % 24:02d}{k:02d}00.jpg"
            out.append(butir(nama, kelas[(s + k) % len(kelas)]))
    return out


def test_sesi_tidak_pernah_terpisah_dua_split():
    """Janji utamanya. Kalau ini gagal, sisanya tidak ada gunanya."""
    items = _dataset(60, 8)
    r = split.rencanakan(items, (0.8, 0.1, 0.1), pakai_dhash=False)
    dimana: dict[str, set[str]] = {}
    for it in items:
        k = split.kunci_sesi(it["img"].name, r["granularitas"])
        dimana.setdefault(k, set()).add(r["peta"][it["img"].name])
    terbelah = {k: v for k, v in dimana.items() if len(v) > 1}
    assert not terbelah, terbelah


def test_dataset_sehat_mendekati_rasio_yang_diminta():
    r = split.rencanakan(_dataset(200, 10), (0.8, 0.1, 0.1), pakai_dhash=False)
    for s, target in (("train", 80), ("valid", 10), ("test", 10)):
        assert abs(r["persen"][s] - target) < 3, r["persen"]
    assert not r["peringatan"], r["peringatan"]


def test_gambar_berlabel_tidak_habis_terserap_ke_valid_dan_test():
    """Regresi atas bug yang terukur di dataset paragon.

    Pengisian valid/test lebih dulu memilih grup yang komposisi kelasnya
    paling mewakili. Pada dataset yang baru sebagian dilabeli, "paling
    mewakili" sama artinya dengan "yang ada labelnya" — dan seluruh 95 objek
    mendarat di valid+test sementara train dapat NOL. Model yang dilatih dari
    situ tidak belajar apa pun.
    """
    items = _dataset(30, 10)                       # 300 gambar berlabel
    items += [butir(f"IMG_20260705_{j:02d}{k:02d}00.jpg")   # 300 negatif
              for j in range(30) for k in range(10)]
    r = split.rencanakan(items, (0.8, 0.1, 0.1), pakai_dhash=False)

    objek = {s: sum(r["kelas"][s].values()) for s in ("train", "valid", "test")}
    total = sum(objek.values())
    assert total, "dataset ujinya sendiri yang salah"
    assert objek["train"] / total > 0.6, objek
    assert objek["valid"] and objek["test"], objek


def test_contoh_negatif_tersebar_bukan_menumpuk_di_satu_split():
    """Label kosong di sini disengaja, jadi porsinya harus seragam.

    Kalau seluruh contoh negatif menumpuk di train, valid tidak pernah menguji
    apakah model bisa menahan diri pada gambar tanpa objek — padahal itu
    justru yang menentukan di ruang detektor.
    """
    items = _dataset(30, 10)
    items += [butir(f"IMG_20260705_{j:02d}{k:02d}00.jpg")
              for j in range(30) for k in range(10)]
    r = split.rencanakan(items, (0.8, 0.1, 0.1), pakai_dhash=False)

    for s in ("train", "valid", "test"):
        n = r["jumlah"][s]
        neg = n - sum(1 for it in items
                      if r["peta"][it["img"].name] == s and it["shapes"])
        assert n and 0.3 < neg / n < 0.7, (s, neg, n)


def test_rasio_yang_tidak_mungkin_dilaporkan_bukan_disembunyikan():
    """Satu sesi berisi 90% dataset: rasio 80/10/10 memang mustahil."""
    items = [butir(f"IMG_20260630_1200{k:02d}.jpg", "botol") for k in range(90)]
    items += [butir(f"IMG_2026070{d}_090000.jpg", "botol") for d in range(1, 6)]
    r = split.rencanakan(items, (0.8, 0.1, 0.1), pakai_dhash=False)
    assert r["peringatan"], "rasio meleset jauh tapi tidak ada peringatan"


def test_split_kosong_tidak_diminta_tidak_diperingatkan():
    """Rasio 90:10:0 berarti test memang sengaja dikosongkan."""
    r = split.rencanakan(_dataset(100, 5), (0.9, 0.1, 0.0), pakai_dhash=False)
    assert r["jumlah"]["test"] == 0
    assert not any("test" in w for w in r["peringatan"]), r["peringatan"]


# ============================================================
# KEMBARAN ISI
# ============================================================

def test_kembaran_dipindahkan_ke_train_bukan_dibuang(tmp_path):
    """Kriteria keluar dari valid sama saja untuk dibuang dan dipindahkan,
    jadi valid-nya identik — bedanya cuma datanya hilang atau tidak."""
    import cv2

    rng = np.random.default_rng(3)
    items, dasar = [], None
    for s in range(40):
        # Tiap sesi punya potretnya sendiri; sesi 20 sengaja dibuat kembar
        # dengan sesi 0, di jam yang berbeda supaya kunci sesi TIDAK
        # menyatukannya. Hanya pemeriksaan isi gambar yang bisa menangkapnya —
        # dan itulah yang sedang diuji. Terukur: 5 gambar pindah ke train.
        pola = rng.integers(0, 255, (48, 64, 3), dtype=np.uint8)
        if s == 0:
            dasar = pola.copy()
        elif s == 20:
            pola = dasar.copy()
        for k in range(5):
            nama = f"IMG_202606{1 + s // 24:02d}_{s % 24:02d}{k:02d}00.jpg"
            p = tmp_path / nama
            cv2.imwrite(str(p), pola)
            items.append({"img": p, "shapes": [{"label": "botol"}]})

    r = split.rencanakan(items, (0.8, 0.1, 0.1))
    assert sum(r["jumlah"].values()) == len(items), "ada gambar yang hilang"
    assert sum(r["dipindah"].values()), \
        "tidak ada yang dipindah — ujinya tidak membuktikan apa pun"

    # Tidak boleh ada gambar valid/test yang punya kembaran di train.
    H = {i: split.dhash(it["img"]) for i, it in enumerate(items)}
    idx = {s: [i for i, it in enumerate(items)
               if r["peta"][it["img"].name] == s] for s in ("train", "valid", "test")}
    acuan = {i: H[i] for i in idx["train"] if H[i] is not None}
    for s in ("valid", "test"):
        uji = {i: H[i] for i in idx[s] if H[i] is not None}
        assert not split.cari_kembar(acuan, uji), f"{s} masih punya kembaran di train"


# ============================================================
# RUTE
# ============================================================

def _ds_bersesi(tmp, n_sesi=12, per_sesi=6):
    """Dataset kecil dengan stempel waktu dan isi gambar yang berbeda-beda."""
    import json

    import cv2

    rng = np.random.default_rng(11)
    d = tmp / "sesi"
    d.mkdir(parents=True, exist_ok=True)
    for s in range(n_sesi):
        # Tiap sesi berisi gambar yang isinya berbeda dari sesi lain, supaya
        # yang diuji adalah pembagiannya — bukan pemindahan kembaran.
        pola = rng.integers(0, 255, (48, 64, 3), dtype=np.uint8)
        for k in range(per_sesi):
            p = d / f"IMG_202606{1 + s // 24:02d}_{s % 24:02d}{k:02d}00.jpg"
            cv2.imwrite(str(p), pola)
            p.with_suffix(".json").write_text(json.dumps({
                "version": "0.4.36", "flags": {}, "imagePath": p.name,
                "imageHeight": 48, "imageWidth": 64, "imageData": None,
                "shapes": [{"label": "botol" if (s + k) % 2 else "kaleng",
                            "shape_type": "polygon",
                            "points": [[5, 5], [30, 5], [30, 30], [5, 30]]}]}))
    return d


def test_rute_splitting_dipakai_ekspor_dan_bisa_dilupakan(klien, lingkungan,
                                                           tmp_path):
    """Rencana yang sudah dijalankan harus menang atas splitting cepat.

    Kalau tidak, dHash yang mahal itu dihitung untuk apa-apa: ZIP-nya tetap
    dibagi lewat hash nama berkas dan kebocoran yang barusan ditutup terbuka
    lagi begitu tombol unduh ditekan.
    """
    import io
    import zipfile
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    d = _ds_bersesi(lingkungan["roots"])
    klien.post(f"/setsrc?path={d}")

    j = klien.post("/api/split/jalankan?split=60,20,20").json()
    assert j["ok"], j
    # Petanya bisa memuat sejuta nama; ia tidak ikut ke browser.
    assert "peta" not in j
    assert j["n_sesi"] == 12

    r = klien.get("/api/ekspor/ringkasan?format=yolo-seg&split=60,20,20").json()
    assert r["split"] == j["jumlah"], (r["split"], j["jumlah"])

    z = zipfile.ZipFile(io.BytesIO(
        klien.get("/ekspor?format=yolo-seg&split=60,20,20").content))
    dari_zip = {n.split("/")[-1]: n.split("/")[0]
                for n in z.namelist() if "/images/" in n and n.endswith(".jpg")}
    assert dari_zip, z.namelist()[:5]
    n_zip = {s: sum(1 for v in dari_zip.values() if v == s)
             for s in ("train", "valid", "test")}
    assert n_zip == j["jumlah"], (n_zip, j["jumlah"])

    # ...dan COCO membelah persis sama, bukan menghitung ulang sendiri.
    zc = zipfile.ZipFile(io.BytesIO(
        klien.get("/ekspor?format=coco&split=60,20,20").content))
    import json as _json
    for s in ("train", "valid", "test"):
        d_coco = _json.loads(zc.read(f"{s}/_annotations.coco.json"))
        assert len(d_coco["images"]) == j["jumlah"][s], s

    assert klien.post("/api/split/lupakan").json()["ok"]
    r2 = klien.get("/api/ekspor/ringkasan?format=yolo-seg&split=60,20,20").json()
    assert r2["rencana"] is None


def test_kemajuan_dilaporkan_dan_bisa_dihentikan(klien, lingkungan, tmp_path):
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    d = _ds_bersesi(lingkungan["roots"], n_sesi=6, per_sesi=4)
    klien.post(f"/setsrc?path={d}")
    klien.post("/api/split/jalankan?split=80,10,10")

    k = klien.get("/api/split/kemajuan").json()
    assert k["ok"] and k["persen"] == 100.0
    assert k["fase_nama"] == "Selesai"

    # Menekan Hentikan saat tidak ada yang berjalan TIDAK boleh meracuni
    # pekerjaan berikutnya: tombolnya bisa saja tertekan setelah splitting
    # selesai, dan itu tidak berarti apa-apa.
    klien.post("/api/split/batal")
    j = klien.post("/api/split/jalankan?split=80,10,10").json()
    assert j["ok"], j


def test_pembatalan_di_tengah_jalan_tidak_meninggalkan_rencana_setengah(tmp_path):
    """Yang dihentikan harus benar-benar berhenti, bukan sekadar diabaikan."""
    items = _dataset(40, 5)
    panggil = {"n": 0}

    def batal():
        panggil["n"] += 1
        return panggil["n"] > 1          # lolos sekali, lalu berhenti

    with pytest.raises(split.Dibatalkan):
        split.rencanakan(items, (0.8, 0.1, 0.1), batal=batal, pakai_dhash=False)


# ============================================================
# KEMANDIRIAN VALID/TEST
# ============================================================

def test_kemandirian_menurun_saat_valid_lebih_mirip_train():
    """Diuji langsung di ruang sidik jari, tanpa gambar.

    Dua valid, train yang sama: satu berisi sidik acak, satu berisi salinan
    sidik train dengan beberapa bit dibalik. Yang kedua HARUS bernilai lebih
    rendah — kalau tidak, angkanya tidak mengukur apa yang dinamainya.

    Versi pertama membandingkan jarak-terdekat dengan median pasangan acak,
    dan hasilnya terbalik: dataset yang seluruh fotonya nyaris sama justru
    bernilai lebih tinggi. Minimum atas ratusan gambar memang selalu jauh di
    bawah median pasangan acak, jadi yang terukur cuma besar train-nya.
    """
    rng = np.random.default_rng(31)
    n_train = 300
    sidik = {i: rng.integers(0, 256, 32, dtype=np.uint8) for i in range(n_train)}
    sesi = [f"s{i // 5}" for i in range(n_train)]

    jauh, dekat = {}, {}
    for k in range(40):
        i = n_train + k
        jauh[i] = rng.integers(0, 256, 32, dtype=np.uint8)
        h = sidik[k].copy()
        for _ in range(30):                       # mirip, tapi tidak identik
            h[int(rng.integers(0, 32))] ^= 1 << int(rng.integers(0, 8))
        dekat[i] = h
    for i in list(jauh) + list(dekat):
        sesi.append(f"v{i}")

    nilai = {}
    for nama, kel in (("jauh", jauh), ("dekat", dekat)):
        semua = dict(sidik); semua.update(kel)
        hasil = {"train": list(sidik), "valid": list(kel), "test": []}
        r = split.nilai_kemandirian(semua, hasil, sesi)
        nilai[nama] = r["valid"]["kemandirian"]

    assert nilai["dekat"] < nilai["jauh"], nilai
    # dan yang jauh harus mendekati 1: sama mandirinya dengan gambar train
    # mana pun terhadap sesi train lain.
    assert 0.8 < nilai["jauh"] < 1.25, nilai


def test_kemandirian_melaporkan_jumlah_sesi_di_valid():
    """Jumlah sesi lebih mudah dibaca daripada rasio mana pun: valid dari
    2 sesi cuma menguji 2 kondisi pemotretan, berapa pun gambarnya."""
    rng = np.random.default_rng(3)
    sidik = {i: rng.integers(0, 256, 32, dtype=np.uint8) for i in range(60)}
    sesi = [f"s{i // 10}" for i in range(60)]
    hasil = {"train": list(range(40)), "valid": list(range(40, 60)), "test": []}
    r = split.nilai_kemandirian(sidik, hasil, sesi)
    assert r["valid"]["n_sesi"] == 2 and r["valid"]["n"] == 20




def test_stempel_waktu_massal_tidak_dikira_sesi_pemotretan(tmp_path):
    """Tidak semua gambar datang dari kamera sendiri.

    Bundel unduhan dan berkas hasil penamaan massal sering memakai satu
    stempel waktu yang sama untuk seluruh isinya — waktu mengunduhnya, bukan
    waktu memotretnya. Dipercaya begitu saja, seluruh dataset jadi satu grup
    tak-terpisahkan dan hasilnya train 100% / valid 0 / test 0.

    Berkas TANPA stempel waktu sudah lama bekerja benar (tiap gambar jadi
    grupnya sendiri, isi gambar yang menjaga). Yang berstempel massal harus
    berakhir sama.
    """
    import cv2

    rng = np.random.default_rng(1)

    def potret():
        kecil = rng.integers(40, 215, (6, 6, 3), dtype=np.uint8)
        return cv2.resize(kecil, (240, 240), interpolation=cv2.INTER_CUBIC)

    def jalankan(nama):
        d = tmp_path / str(abs(hash(nama[0])) % 10000)
        d.mkdir(parents=True, exist_ok=True)
        items = []
        for n in nama:
            p = d / n
            cv2.imwrite(str(p), potret())
            items.append({"img": p, "shapes": [{"label": "kahf"}]})
        return split.rencanakan(items, (0.8, 0.1, 0.1))

    r = jalankan([f"IMG_20260630_101500_{i:03d}.jpg" for i in range(120)])
    assert r["jumlah"]["valid"] and r["jumlah"]["test"], r["jumlah"]
    for s, target in (("train", 80), ("valid", 10), ("test", 10)):
        assert abs(r["persen"][s] - target) < 5, r["persen"]
    assert any("stempel waktu yang sama" in w for w in r["peringatan"])

    # Sesi pemotretan sungguhan JANGAN ikut dipecah: yang membedakan hanyalah
    # ukurannya terhadap seluruh dataset.
    r2 = jalankan([f"IMG_202607{1 + s // 24:02d}_{s % 24:02d}{k:02d}00.jpg"
                   for s in range(24) for k in range(5)])
    assert r2["n_sesi"] == 24, r2["n_sesi"]
    assert not any("stempel waktu yang sama" in w for w in r2["peringatan"])


def test_kelima_format_memakai_pembagian_yang_sama_persis(klien, lingkungan,
                                                          tmp_path):
    """Bukan cuma jumlahnya sama, tapi BERKAS yang sama mendarat di split
    yang sama di semua format.

    Kalau tidak, model YOLO dan model COCO dari dataset yang sama dinilai
    memakai gambar yang berbeda, dan angkanya tidak bisa dibandingkan sama
    sekali. Jumlah yang kebetulan sama tidak membuktikan itu.
    """
    import io
    import json as _json
    import zipfile
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    d = _ds_bersesi(lingkungan["roots"], n_sesi=12, per_sesi=6)
    klien.post(f"/setsrc?path={d}")
    j = klien.post("/api/split/jalankan?split=80,10,10").json()
    assert j["ok"], j

    def peta(fmt):
        z = zipfile.ZipFile(io.BytesIO(
            klien.get(f"/ekspor?format={fmt}&split=80,10,10").content))
        out = {}
        for n in z.namelist():
            split_n = n.split("/")[0]
            if fmt.startswith("yolo") and "/images/" in n and n.endswith(".jpg"):
                out[n.split("/")[-1]] = split_n
            elif fmt == "coco" and n.endswith("_annotations.coco.json"):
                for g in _json.loads(z.read(n))["images"]:
                    out[g["file_name"]] = split_n
            elif fmt == "createml" and n.endswith("_annotations.createml.json"):
                for g in _json.loads(z.read(n)):
                    out[g["image"]] = split_n
            elif fmt == "voc" and n.endswith(".jpg"):
                out[n.split("/")[-1]] = split_n
        return out

    acuan = peta("yolo-seg")
    assert acuan, "ZIP yolo-seg kosong"
    for fmt in ("yolo", "coco", "voc", "createml"):
        assert peta(fmt) == acuan, fmt
    n = {s: sum(1 for v in acuan.values() if v == s) for s in split.SPLIT}
    assert n == j["jumlah"], (n, j["jumlah"])


def test_rasio_diubah_sesudahnya_tidak_diam_diam_mengubah_isi_zip(klien,
                                                                  lingkungan,
                                                                  tmp_path):
    """Rencana yang sudah jadi mengunci pembagiannya.

    Yang penting: angka di layar dan isi ZIP harus tetap sepakat. Ringkasan
    boleh menampilkan pembagian lama, asal ZIP-nya juga pembagian lama.
    """
    import io
    import zipfile
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    d = _ds_bersesi(lingkungan["roots"], n_sesi=12, per_sesi=6)
    klien.post(f"/setsrc?path={d}")
    j = klien.post("/api/split/jalankan?split=80,10,10").json()

    r = klien.get("/api/ekspor/ringkasan?format=yolo-seg&split=50,25,25").json()
    z = zipfile.ZipFile(io.BytesIO(
        klien.get("/ekspor?format=yolo-seg&split=50,25,25").content))
    n = {s: sum(1 for x in z.namelist()
                if x.startswith(f"{s}/images/") and x.endswith(".jpg"))
         for s in split.SPLIT}
    assert n == r["split"] == j["jumlah"], (n, r["split"], j["jumlah"])


def test_zip_membawa_catatan_cara_membelahnya(klien, lingkungan, tmp_path):
    """ZIP harus bisa menjelaskan dirinya sendiri.

    Berbulan-bulan kemudian, melihat dua berkas ZIP dari dataset yang sama,
    tidak ada cara membedakan mana yang dibelah anti-bocor dan mana yang
    sekadar per nama berkas. Keterangan yang cuma tampil di layar saat
    mengunduh tidak menolong siapa pun setelah tabnya ditutup.
    """
    import io
    import zipfile
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    d = _ds_bersesi(lingkungan["roots"], n_sesi=12, per_sesi=6)
    klien.post(f"/setsrc?path={d}")

    def info(fmt):
        z = zipfile.ZipFile(io.BytesIO(klien.get(f"/ekspor?format={fmt}").content))
        return z.read("SPLIT-INFO.txt").decode()

    sebelum = info("yolo-seg")
    assert "SPLITTING: cepat" in sebelum
    assert "TIDAK diperiksa" in sebelum

    j = klien.post("/api/split/jalankan?split=80,10,10").json()
    for fmt in ("yolo-seg", "yolo", "coco", "voc", "createml"):
        t = info(fmt)
        assert "SPLITTING: anti-bocor" in t, fmt
        assert f"Ambang kemiripan  : {j['ambang']}" in t, fmt
        assert f"Sesi terdeteksi   : {j['n_sesi']:,}" in t, fmt
        # angkanya harus sama dengan isi ZIP-nya sendiri
        for s in split.SPLIT:
            assert f"{s:6s} {j['jumlah'][s]:7,} gambar" in t, (fmt, s)


def test_unduhan_menandai_balasannya_supaya_bisa_ditunggu(klien, lingkungan,
                                                          tmp_path):
    """Unduhan lewat <a href> tidak punya kejadian yang bisa ditunggu JS.

    Tanpa penanda ini, 33 detik pembentukan ZIP paragon (1,27 GB) berlalu
    tanpa satu pun tanda di layar, dan tombolnya tampak rusak.
    """
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    d = _ds_bersesi(lingkungan["roots"], n_sesi=4, per_sesi=3)
    klien.post(f"/setsrc?path={d}")
    r = klien.get("/ekspor?format=yolo-seg&tanda=uji123")
    assert "unduh_siap=uji123" in r.headers.get("set-cookie", "")
    # tanpa tanda, tidak ada cookie yang ditinggalkan
    r2 = klien.get("/ekspor?format=yolo-seg")
    assert "unduh_siap" not in r2.headers.get("set-cookie", "")
