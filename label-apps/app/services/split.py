"""
Pembelahan train/valid/test yang tidak membocorkan foto yang sama ke dua sisi.

MASALAHNYA
----------
Membelah per-gambar terdengar adil, tapi menghasilkan angka validasi palsu.
Foto yang diambil dua detik berselang — meja yang sama, cahaya yang sama,
produk fisik yang sama, tangan yang bergeser sedikit — praktis gambar yang
sama. Kalau satu masuk train dan satu masuk valid, model dinilai memakai
sesuatu yang sudah dia hafal.

Diukur pada dataset pengguna sendiri (paragon, 476 gambar): pengelompokan
lama `kunci_asal()` menghasilkan 476 grup untuk 476 gambar — yaitu nol
perlindungan, karena tiap gambar jadi grupnya sendiri. Seluruh datasetnya
diambil dalam SATU JAM.

Penyakit yang sama membuat model sirsak-v13 melaporkan mAP 0,9499 tapi 0/7
di ruang detektor sungguhan.

DUA LAPIS PERTAHANAN
--------------------
1. **Sesi pemotretan.** Gambar dari sesi yang sama tidak pernah terpisah
   split. Kuncinya dari stempel waktu di nama berkas.

2. **Kemiripan isi (dHash).** Lapis pertama tidak berguna kalau namanya acak
   — UUID, hash, nama dari sumber campur aduk. Karena itu isi gambarnya yang
   diperiksa, dan gambar valid/test yang punya kembaran di train DIPINDAHKAN
   ke train.

Kenapa dipindahkan, bukan dibuang: kriteria keluarnya sama persis, jadi
valid/test-nya identik dua-duanya — bedanya cuma datanya terbuang atau tidak.
Riset yang jadi acuan (Barz & Denzler 2020, "Purging CIFAR of Near-Duplicates")
membuang dari test karena train set CIFAR tidak boleh diubah; tujuan mereka
memperbaiki tolok ukur yang sudah dipakai ribuan makalah. Kita mengendalikan
kedua sisi, jadi membuang hanya merugikan tanpa menambah kebersihan.

Kenapa memindahkan harus DIULANG: gambar yang baru pindah ke train bisa
punya kembaran lain di test yang tadinya aman. Sekali jalan tidak cukup.

YANG SENGAJA TIDAK DILAKUKAN
----------------------------
Kembaran TIDAK digabungkan menjadi satu grup. Penggabungan bersifat transitif
— A~B dan B~C menyatukan A, B, C walau A dan C tidak mirip. Diukur pada
dataset sirsak, cara itu meruntuhkan 66-85% gambar jadi satu grup raksasa
sehingga rasio train/valid/test mustahil dipenuhi. Memindahkan mencapai
tujuan yang sama tanpa efek samping itu.
"""
from __future__ import annotations

import re
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

from ..log import catat

log = catat("labelapp.split")

# Butuh sekurang-kurangnya sekian sesi sebelum sebuah granularitas dianggap
# layak. Di bawah ini train dan valid pasti berbagi kondisi pemotretan, dan
# angka validasinya akan optimistis walau pembelahannya sendiri benar.
MIN_SESI = 20

# Satu grup tidak boleh memuat lebih dari sekian bagian dataset; kalau
# melebihi, rasio yang diminta tidak mungkin dipenuhi.
MAKS_GRUP = 0.40

# Sisi petak dHash. 16 -> sidik jari 256 bit.
#
# Diukur 21 Agustus 2026 pada dua dataset yang sangat berbeda (paragon:
# 476 foto ponsel 4080x2296; botol-kaleng: 11.319 ekspor Roboflow 640x640),
# dengan kembaran BUATAN yang pasti benar (kompresi ulang q70/q50, ubah
# ukuran lewat 640, kecerahan +12%, potong 2%, geser 4 px) dan pasangan
# bukan-kembaran yang pasti benar (lintas kedua dataset):
#
#     8x8  (64 bit)   kembaran maks 14  ·  bukan-kembaran min 11  -> TUMPANG TINDIH
#     16x16 (256 bit) kembaran maks 65  ·  bukan-kembaran min 77  -> celah 12 bit
#
# Pada 64 bit tidak ada satu pun ambang yang menangkap seluruh kembaran
# tanpa ikut menangkap foto yang berbeda. Menyetel ambang di alat ukur
# sekasar itu hanya memilih jenis kesalahan mana yang mau ditanggung.
SISI_HASH = 16
BIT_HASH = SISI_HASH * SISI_HASH

# Ambang cadangan, dipakai hanya kalau kalibrasi tidak bisa dijalankan
# (mis. gambarnya tidak terbaca). Angka yang sebenarnya dipakai selalu
# diukur dari dataset yang bersangkutan — lihat kalibrasi_ambang().
#
# Satu angka tetap TIDAK cukup, dan ini terukur. Dengan pembanding yang
# benar (pasangan beda-sesi di dalam dataset yang sama):
#
#     paragon       kembaran p99  39  ·  beda-sesi p1  97  -> ambang 68
#     botol-kaleng  kembaran p99  47  ·  beda-sesi p1  51  -> ambang 48
#
# Memakai 72 untuk keduanya menguras botol-kaleng: 10% pasangan beda-sesi
# ikut tertangkap, dan valid tinggal 36 dari 11.319 gambar.
AMBANG_KEMBAR = 56

# Batas atas hasil kalibrasi. Lebih tinggi dari ini, foto yang benar-benar
# berbeda mulai ikut terseret dan valid/test terkuras tanpa alasan.
AMBANG_MAKS = 96

# Pemindahan diulang sampai tidak ada lagi yang pindah, bukan sampai
# sekian putaran. Batas ini semata penjaga supaya tidak berputar selamanya.
#
# Versi pertama berhenti di 5 putaran, dan pada paragon itu memotong sebelum
# selesai: 4 kembaran tetap tertinggal di valid, padahal justru itu yang
# hendak dicegah. Tiap putaran memindahkan gambar ke train, dan gambar yang
# baru pindah bisa punya kembaran lain yang tadinya aman — jadi yang benar
# adalah berhenti saat keadaannya tenang, bukan saat jatah putarannya habis.
MAKS_PUTARAN = 60

FASE = {
    "pindai": "Mengumpulkan gambar",
    "sesi": "Mengelompokkan per sesi pemotretan",
    "dhash": "Membaca isi gambar",
    "kalibrasi": "Mengukur ambang kemiripan dataset ini",
    "bagi": "Membagikan ke train/valid/test",
    "bersih": "Memindahkan kembaran keluar dari valid/test",
    "nilai": "Menilai kemandirian valid dan test",
    "selesai": "Selesai",
}


# ============================================================
# KEMAJUAN
# ============================================================
#
# Pola yang sama dengan impor: dict di memori plus rute polling. Pekerjaannya
# berjalan di thread lain, jadi permintaan HTTP-nya tidak menggantung.

_maju: dict[str, dict] = {}
_kunci = threading.Lock()


def catat_maju(kunci: str, **nilai) -> None:
    if kunci:
        with _kunci:
            _maju.setdefault(kunci, {}).update(nilai)


def kemajuan(kunci: str) -> dict:
    with _kunci:
        return dict(_maju.get(kunci) or {})


def bersihkan_maju(kunci: str) -> None:
    if kunci:
        with _kunci:
            _maju.pop(kunci, None)


class Dibatalkan(Exception):
    """Pengguna menghentikan pembelahan di tengah jalan."""


# ============================================================
# KUNCI SESI
# ============================================================

_RF = re.compile(r"\.rf\.")
_AUG = re.compile(r"_(aug|bal|p5[a-z]*|sw[a-z]*)\d*.*$", re.I)


def stem_asli(nama: str) -> str:
    """Nama foto aslinya: hash Roboflow, sufiks augmentasi, dan penanda
    salinan dibuang."""
    s = _RF.split(str(nama))[0]
    s = re.sub(r"\.(jpg|jpeg|png|webp|bmp|tif|tiff)$", "", s, flags=re.I)
    s = re.sub(r"_(jpg|jpeg|png)$", "", s, flags=re.I)
    s = _AUG.sub("", s)
    s = re.sub(r"[-_ ]?(Copy|copy|salinan)$", "", s)
    s = re.sub(r"\s*\(\d+\)$", "", s)
    return s


def _stempel(s: str) -> tuple[str, str, str] | None:
    """(tanggal, jam, menit) dari nama berkas, atau None."""
    # 20240506_170457 / IMG_20240506_170457
    m = re.search(r"(?<!\d)(\d{8})[_-](\d{2})(\d{2})\d{2}(?!\d)", s)
    if m:
        return m.group(1), m.group(2), m.group(3)
    # 2024-05-06 17.04.57 / 2024-05-06T17:04
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})[ _T](\d{2})[.:]?(\d{2})", s)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}", m.group(4), m.group(5)
    # Tanggal saja
    m = re.search(r"(?<!\d)(20\d{6})(?!\d)", s)
    if m:
        return m.group(1), "", ""
    return None


def kunci_sesi(nama: str, gran: str = "menit") -> str:
    """
    Kunci sesi pemotretan. Gambar sesesi tidak pernah terpisah split.

    Aturannya sengaja KETAT dan hanya mengenali penanda waktu. Mengelompokkan
    berdasarkan prefiks nama (mis. semua `img_00044` jadi sesi "img") terdengar
    masuk akal tapi salah arah: prefiks semacam itu konvensi penamaan SELURUH
    dataset, bukan sesi pemotretan. Diukur pada dataset sirsak, cara itu
    menyatukan 85% gambar jadi satu grup.

    Berkas tanpa penanda waktu menjadi sesinya sendiri. Itu bukan kelalaian:
    kemiripan nyata antar-berkas tetap tertangkap oleh dHash, yang tidak
    peduli namanya apa.
    """
    s = stem_asli(nama)
    t = _stempel(s)
    if not t:
        return "file:" + s
    tgl, jam, menit = t
    if gran == "hari" or not jam:
        return "ts:" + tgl
    if gran == "jam":
        return f"ts:{tgl}_{jam}"
    return f"ts:{tgl}_{jam}{menit}"


def pilih_granularitas(nama: list[str], rasio=(0.8, 0.1, 0.1)) -> tuple[str, dict]:
    """
    Granularitas paling KASAR yang grup terbesarnya masih muat di split
    terkecil yang diminta.

    Kenapa otomatis: kedua skrip acuan mematoknya dan dua-duanya jadi salah di
    dataset yang lain. Sirsak memakai per-jam; dipakai di paragon — yang
    seluruhnya diambil dalam satu jam — hasilnya 1 grup dan pembelahan
    mustahil. Paragon memakai per-menit; dipakai di dataset yang terbentang
    berbulan-bulan, per-menit memecah sesi yang sebenarnya satu.

    Kenapa syaratnya "muat di split terkecil", bukan "sekurangnya sekian
    sesi": sesi tidak boleh dipecah, jadi satu grup yang lebih besar daripada
    kuota valid akan selalu melompatinya. Dinyatakan begitu, syaratnya
    mengikuti rasio yang benar-benar diminta alih-alih angka tetap yang
    kebetulan cocok di satu dataset.

    Kasar lebih aman daripada halus — makin kasar kuncinya, makin banyak foto
    yang dijaga tetap bersama — tapi hanya sampai batas itu. Terlalu kasar
    membuat rasio meleset tanpa menambah perlindungan yang berarti: dataset
    2.000 gambar dalam 200 sesi jadi 76:12:12 kalau dikunci per-hari, dan
    80:10:10 persis kalau per-jam.
    """
    n = max(len(nama), 1)
    aktif = [r for r in rasio if r > 0]
    muat = min(aktif) * n if aktif else n
    laporan = {}
    for g in ("hari", "jam", "menit"):
        c = Counter(kunci_sesi(n2, g) for n2 in nama)
        besar = max(c.values()) if c else 0
        laporan[g] = {"sesi": len(c), "terbesar": besar, "pct": besar / n}
        if besar <= muat:
            return g, laporan
    # Tidak ada yang layak. Yang paling halus dipakai, karena menolak sama
    # sekali berarti pengguna tidak bisa mengekspor apa pun — dan angkanya
    # tetap kita laporkan apa adanya.
    return "menit", laporan


# ============================================================
# dHASH
# ============================================================

_BIT = np.unpackbits(np.arange(256, dtype=np.uint8)[:, None], axis=1).sum(1).astype(np.uint8)


def _hash_dari(im: np.ndarray) -> np.ndarray:
    """dHash dari citra yang sudah di memori."""
    if im.ndim == 3:
        im = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    im = cv2.resize(im, (SISI_HASH + 1, SISI_HASH), interpolation=cv2.INTER_AREA)
    return np.packbits(im[:, 1:] > im[:, :-1])


def dhash(path) -> np.ndarray | None:
    """Sidik jari 256-bit dari gradien mendatar gambar kecil.

    Tahan terhadap perubahan ukuran, kompresi ulang, dan pergeseran kecerahan
    — persis tiga hal yang membuat dua salinan foto yang sama punya md5 beda.
    """
    im = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if im is None:
        return None
    return _hash_dari(im)


# Berapa banyak jarak dihitung sekaligus. Satu petak memakai
# tinggi x lebar x 32 byte, jadi batas ini menjaga pemakaian memori tetap
# di kisaran 64 MB berapa pun besar datasetnya.
SEL_PER_PETAK = 2_000_000


def cari_kembar(acuan: dict[int, np.ndarray], uji: dict[int, np.ndarray],
                ambang: int = AMBANG_KEMBAR) -> set[int]:
    """
    Indeks di `uji` yang punya kembaran di `acuan` (Hamming <= ambang).

    Dihitung menyeluruh, dalam petak-petak yang muat di memori.

    Versi sebelumnya memakai indeks banyak-pita: hash dipotong jadi ambang+1
    pita, dan dua hash berjarak <= ambang pasti sama persis di sekurangnya
    satu pita. Cara itu jauh lebih cepat, tapi menuntut jumlah pita lebih
    banyak daripada ambangnya — mustahil di sini, karena ambang 72 berarti
    73 pita sedangkan hash 256 bit hanya punya 32 byte.

    Melepasnya boleh karena bukan di sinilah waktunya habis: membaca dan
    mengurai gambar makan 56 ms per berkas (19 jam untuk sejuta gambar,
    satu inti), sementara perbandingan menyeluruh untuk jumlah yang sama
    selesai dalam hitungan menit. Menukar menit demi ketepatan itu murah;
    menukar ketepatan demi menit tidak.
    """
    if not acuan or not uji:
        return set()
    k_acuan = list(acuan)
    k_uji = list(uji)
    A = np.array([acuan[i] for i in k_acuan], dtype=np.uint8)
    U = np.array([uji[i] for i in k_uji], dtype=np.uint8)

    lebar = min(len(A), 32_768)
    tinggi = max(1, SEL_PER_PETAK // max(lebar, 1))
    kena = np.zeros(len(U), dtype=bool)

    for i in range(0, len(U), tinggi):
        blok = U[i:i + tinggi]
        # Baris yang sudah ketemu tidak perlu dibandingkan lagi dengan sisa
        # acuan; pada dataset yang penuh kembaran itu memangkas banyak kerja.
        sisa = np.ones(len(blok), dtype=bool)
        for j in range(0, len(A), lebar):
            if not sisa.any():
                break
            sub = blok[sisa]
            d = _BIT[np.bitwise_xor(sub[:, None, :], A[j:j + lebar][None, :, :])].sum(2)
            baru_kena = d.min(1) <= ambang
            idx = np.flatnonzero(sisa)[baru_kena]
            kena[i + idx] = True
            sisa[idx] = False
    return {k_uji[i] for i in np.flatnonzero(kena)}


# ============================================================
# KALIBRASI AMBANG
# ============================================================

# Perubahan yang PASTI menghasilkan gambar yang sama: kompresi ulang, ubah
# ukuran, dan geser kecerahan. Ketiganya benar-benar terjadi di jalur kerja
# ini — ekspor Roboflow mengompresi ulang dan mengubah ukuran, augmentasi
# menggeser kecerahan.
def _varian(im: np.ndarray) -> list[np.ndarray]:
    out = []
    ok, buf = cv2.imencode(".jpg", im, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
    if ok:
        out.append(cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE))
    h, w = im.shape[:2]
    if w > 1:
        kecil = cv2.resize(im, (640, max(1, int(640 * h / w))))
        out.append(cv2.resize(kecil, (w, h)))
    out.append(np.clip(im.astype(np.int16) * 1.12, 0, 255).astype(np.uint8))
    # Perubahan bentuk ikut, dan ini yang paling menentukan: augmentasi
    # memotong dan menggeser, dan hasilnya masih foto yang sama. Tanpa
    # keduanya, kalibrasi pada dataset yang sudah 640x640 nyaris tidak
    # mengukur apa-apa — "ubah ukuran lewat 640" di sana tidak mengubah apa
    # pun, dan ambangnya tersetel jauh terlalu rendah.
    if h > 4 and w > 4:
        dy, dx = max(1, int(h * .02)), max(1, int(w * .02))
        out.append(cv2.resize(im[dy:h - dy, dx:w - dx], (w, h)))
        out.append(cv2.warpAffine(im, np.float32([[1, 0, 4], [0, 1, 4]]), (w, h)))
    return [o for o in out if o is not None]


def kalibrasi_ambang(items: list[dict], sidik: dict[int, np.ndarray],
                     sesi: list[str], contoh: int = 60) -> tuple[int, dict]:
    """
    Ambang untuk dataset INI, diukur dari dua distribusinya sendiri.

    Tidak ada satu angka yang benar untuk semua dataset, dan itu terukur:
    foto yang lebih kecil dan sudah lebih terkompresi kehilangan lebih banyak
    detail saat diproses ulang, sekaligus lebih mirip satu sama lain. Kedua
    hal itu menggeser ambang ke arah berlawanan, jadi keduanya harus diukur.

    **Yang wajib tertangkap** — beberapa foto dataset ini diproses ulang
    dengan perubahan yang PASTI tidak mengubah isinya. Semuanya masih foto
    yang sama, jadi jaraknya menandai batas bawah.

    **Yang wajib lolos** — pasangan dari SESI BERBEDA di dalam dataset ini.
    Sebagian memang kembaran, justru itu yang sedang diburu; karena itu yang
    dipakai persentil 1, bukan nilai terkecilnya.

    Pembandingnya harus dari dataset yang sama. Versi pertama memakai
    pasangan lintas-dataset (paragon lawan botol-kaleng) dan itu terlalu
    mudah — produk berbeda di ruangan berbeda memang berjauhan. Ambangnya
    tersetel 72, dan valid botol-kaleng terkuras jadi 36 dari 11.319 gambar.
    """
    kosong = (AMBANG_KEMBAR, {})
    if not items or not sidik:
        return kosong

    langkah = max(1, len(items) // max(contoh, 1))
    pos: list[int] = []
    dipakai = 0
    for it in items[::langkah][:contoh]:
        im = cv2.imread(str(it["img"]), cv2.IMREAD_GRAYSCALE)
        if im is None:
            continue
        h0 = _hash_dari(im)
        dipakai += 1
        for v in _varian(im):
            pos.append(int(_BIT[np.bitwise_xor(h0, _hash_dari(v))].sum()))
        del im
    if not pos:
        return kosong

    # Negatifnya nyaris gratis: sidik jarinya sudah dihitung semua.
    idx = list(sidik)[::max(1, len(sidik) // 200)][:200]
    neg = [int(_BIT[np.bitwise_xor(sidik[a], sidik[b])].sum())
           for i, a in enumerate(idx) for b in idx[i + 1:]
           if sesi[a] != sesi[b]]

    t_pos = float(np.percentile(pos, 99))
    t_neg = float(np.percentile(neg, 1)) if neg else None
    if t_neg is not None and t_neg > t_pos:
        usul, terpisah = int((t_pos + t_neg) / 2), True
    else:
        # Bertumpuk: foto berbeda di dataset ini sudah sedekat kembarannya.
        # Yang didahulukan adalah menangkap kembaran — kembaran yang lolos
        # menggelembungkan angka validasi diam-diam, sedangkan foto berbeda
        # yang salah tertangkap cuma pindah ke train, tidak ada yang hilang.
        usul, terpisah = int(t_pos), False
    ambang = max(1, min(AMBANG_MAKS, usul))
    return ambang, {
        "dipotong": usul > AMBANG_MAKS,
        "contoh": dipakai, "pasangan": len(pos), "pasangan_beda": len(neg),
        "kembaran_p99": t_pos, "kembaran_maks": int(max(pos)),
        "beda_p1": t_neg, "terpisah": terpisah,
        # Median dipakai sebagai patokan "jarak wajar" oleh nilai_kemandirian.
        "beda_median": float(np.median(neg)) if neg else None,
        "dipakai": ambang, "cadangan": AMBANG_KEMBAR,
    }


# ============================================================
# SEBERAPA MANDIRI VALID/TEST
# ============================================================

def jarak_terdekat(acuan: dict[int, np.ndarray],
                   uji: dict[int, np.ndarray]) -> np.ndarray:
    """Jarak tiap gambar `uji` ke gambar `acuan` yang PALING MIRIP."""
    if not acuan or not uji:
        return np.array([], dtype=np.int32)
    A = np.array(list(acuan.values()), dtype=np.uint8)
    B = np.array(list(uji.values()), dtype=np.uint8)
    lebar = min(len(A), 32_768)
    tinggi = max(1, SEL_PER_PETAK // max(lebar, 1))
    out = np.full(len(B), 1 << 30, dtype=np.int32)
    for i in range(0, len(B), tinggi):
        sub = B[i:i + tinggi]
        for j in range(0, len(A), lebar):
            d = _BIT[np.bitwise_xor(sub[:, None, :], A[j:j + lebar][None, :, :])].sum(2)
            out[i:i + len(sub)] = np.minimum(out[i:i + len(sub)], d.min(1))
    return out


def nilai_kemandirian(sidik: dict[int, np.ndarray], hasil: dict[str, list[int]],
                      sesi: list[str], contoh: int = 200) -> dict:
    """
    Seberapa mandiri valid/test dari train, dibanding patokan yang setara.

    Kebocoran nol tidak berarti angka validasinya bisa dipercaya. Nol hanya
    berarti tidak ada yang melewati ambang; gambar valid masih bisa duduk
    tepat di atasnya — mirip, tapi tidak cukup mirip untuk dipindahkan.

    **Patokannya harus setara.** Versi pertama membandingkan jarak-terdekat
    gambar valid ke train dengan MEDIAN pasangan acak. Itu keliru: minimum
    atas ratusan gambar memang selalu jauh di bawah median pasangan acak,
    jadi skornya pasti di bawah 1 dan makin kecil setiap train membesar —
    artefak, bukan ukuran. Terbukti terbalik saat diuji: dataset yang seluruh
    fotonya nyaris sama justru mendapat skor LEBIH TINGGI.

    Yang dipakai sekarang: jarak-terdekat gambar TRAIN ke train lain dari
    sesi berbeda. Dua-duanya minimum atas kumpulan yang sama, jadi bisa
    dibandingkan.

        ~1,0  valid semandiri satu gambar train terhadap sesi train lain
        <1,0  valid lebih mirip train daripada train mirip dirinya sendiri
    """
    out: dict = {}
    train = [i for i in hasil["train"] if i in sidik]
    if not train:
        return out

    # Patokan: ambil sebagian train, cari tetangga terdekatnya di train yang
    # SESINYA BERBEDA. Sesi yang sama sengaja dibuang — gambar sesesi memang
    # nyaris kembar, dan memasukkannya membuat patokannya terlalu kecil.
    langkah = max(1, len(train) // max(contoh, 1))
    cuplik = train[::langkah][:contoh]
    dasar: list[int] = []
    A = np.array([sidik[i] for i in train], dtype=np.uint8)
    sesi_train = np.array([sesi[i] for i in train])
    for i in cuplik:
        beda = sesi_train != sesi[i]
        if not beda.any():
            continue
        d = _BIT[np.bitwise_xor(A[beda], sidik[i][None, :])].sum(1)
        dasar.append(int(d.min()))
    patokan = float(np.median(dasar)) if dasar else None
    out["patokan"] = patokan
    out["patokan_n"] = len(dasar)

    acuan = {i: sidik[i] for i in train}
    for split in ("valid", "test"):
        uji = {i: sidik[i] for i in hasil[split] if i in sidik}
        bagian = {"n": len(uji), "n_sesi": len({sesi[i] for i in hasil[split]})}
        d = jarak_terdekat(acuan, uji)
        if len(d):
            bagian["terdekat_median"] = float(np.median(d))
            bagian["terdekat_min"] = int(d.min())
            if patokan:
                bagian["kemandirian"] = float(np.median(d)) / patokan
        out[split] = bagian
    return out


# ============================================================
# PEMBELAHAN
# ============================================================

SPLIT = ("train", "valid", "test")


def _kelas_item(it: dict) -> Counter:
    return Counter(str(s.get("label")) for s in it.get("shapes") or [])


def _bagikan_kolam(grup: list[list[int]], kc: list[Counter], rasio,
                   ) -> dict[str, list[int]]:
    """
    Bagikan satu kolam grup ke train/valid/test.

    Valid dan test diisi LEBIH DULU, dari grup yang komposisi kelasnya paling
    mirip komposisi kolam. Kenapa: pada dataset dengan sedikit grup, sebagian
    grup homogen — satu menit pemotretan bisa berisi satu kelas saja.
    Pembagian serakah biasa menaruh grup homogen itu di test, dan hasilnya
    test yang isinya nyaris satu kelas: metrik yang tidak terbaca.
    """
    hasil: dict[str, list[int]] = {k: [] for k in SPLIT}
    if not grup:
        return hasil

    total = sum(len(g) for g in grup)
    semua = Counter()
    for c in kc:
        semua.update(c)
    urut_kelas = sorted(semua)
    prop = np.array([semua[k] for k in urut_kelas], float)
    prop = prop / max(prop.sum(), 1.0)

    kuota = {s: rasio[i] * total for i, s in enumerate(SPLIT)}
    aktif = [s for s in SPLIT if kuota[s] > 0] or ["train"]
    jml = {s: 0 for s in SPLIT}
    punya = {s: Counter() for s in SPLIT}
    milik: dict[int, str] = {}

    def penalti(tambah_ke: str | None = None, gi: int | None = None) -> float:
        """Seberapa jauh keadaan sekarang dari yang diminta.

        Dua hal dinilai bersamaan: ukuran tiap split terhadap kuotanya, dan
        komposisi kelasnya terhadap komposisi kolam. Menilai keduanya dalam
        SATU angka menghindari dua aturan yang saling menabrak — versi
        sebelumnya mengisi valid/test lebih dulu demi keseimbangan kelas, dan
        pada kolam yang cuma punya tiga grup itu membuat train kebagian
        paling sedikit: 15,8% objek untuk train, 56,8% untuk test.
        """
        nilai = 0.0
        for s in aktif:
            n_s = jml[s] + (len(grup[gi]) if s == tambah_ke else 0)
            nilai += abs(n_s / kuota[s] - 1.0)
            if not urut_kelas:
                continue
            c = punya[s] + kc[gi] if s == tambah_ke else punya[s]
            v = np.array([c.get(k, 0) for k in urut_kelas], float)
            # Split yang belum punya objek sama sekali dihitung menyimpang
            # penuh: itulah yang mendorong objek tersebar, bukan menumpuk.
            nilai += 2.0 if v.sum() <= 0 else float(np.abs(v / v.sum() - prop).sum())
        return nilai

    # Grup besar ditempatkan lebih dulu: yang kecil masih bisa menambal sisa,
    # sedangkan grup besar yang datang belakangan hanya bisa merusak.
    for gi in sorted(range(len(grup)), key=lambda g: -len(grup[g])):
        pilih = min(aktif, key=lambda s: penalti(s, gi))
        milik[gi] = pilih
        jml[pilih] += len(grup[gi])
        punya[pilih].update(kc[gi])

    # Split yang diminta tapi kosong: selama grupnya cukup, tiap split wajib
    # kebagian sekurang-kurangnya satu. Valid atau test yang kosong tidak bisa
    # dipakai sama sekali, sedangkan rasio yang meleset masih bisa dibaca —
    # dan memang diperingatkan.
    if len(grup) >= len(aktif):
        for s in aktif:
            if jml[s]:
                continue
            sumber = [gi for gi, ke in milik.items()
                      if sum(1 for g2, k2 in milik.items() if k2 == ke) > 1]
            if not sumber:
                break
            gi = min(sumber, key=lambda g: abs(len(grup[g]) - kuota[s]))
            lama = milik[gi]
            jml[lama] -= len(grup[gi])
            punya[lama].subtract(kc[gi])
            milik[gi] = s
            jml[s] += len(grup[gi])
            punya[s].update(kc[gi])

    for gi, s in milik.items():
        hasil[s].extend(grup[gi])
    return hasil


def _bagikan(grup: list[list[int]], item: list[dict], rasio) -> dict[str, list[int]]:
    """
    Bagikan grup sesi, dengan contoh negatif dibagi sebagai kolam TERPISAH.

    Kenapa dipisah: pengisian valid/test memilih grup yang komposisi kelasnya
    paling mewakili. Pada dataset yang baru sebagian dilabeli, "paling
    mewakili" sama artinya dengan "yang ada labelnya" — dan grup berlabel
    habis terserap ke valid/test lebih dulu. Diukur pada paragon (476 gambar,
    87 beranotasi): SELURUH 95 objek mendarat di valid+test dan train dapat
    nol objek. Model yang dilatih dari situ tidak belajar apa pun.

    Membelah dua kolam dengan rasio yang sama menyelesaikannya sekaligus
    menjaga porsi contoh negatif tetap seragam di ketiga split — yang memang
    harus dijaga, karena label kosong di sini disengaja, bukan pekerjaan
    yang belum selesai.
    """
    kc = [Counter() for _ in grup]
    for gi, g in enumerate(grup):
        for i in g:
            kc[gi].update(_kelas_item(item[i]))

    berobjek = [gi for gi in range(len(grup)) if kc[gi]]
    negatif = [gi for gi in range(len(grup)) if not kc[gi]]

    hasil: dict[str, list[int]] = {k: [] for k in SPLIT}
    for kolam in (berobjek, negatif):
        bagian = _bagikan_kolam([grup[gi] for gi in kolam],
                                [kc[gi] for gi in kolam], rasio)
        for s in SPLIT:
            hasil[s].extend(bagian[s])
    return hasil


def rencanakan(items: list[dict], rasio=(0.8, 0.1, 0.1), *,
               kunci: str = "", batal=None, pakai_dhash: bool = True) -> dict:
    """
    Susun rencana pembelahan lengkap dengan diagnosanya.

    Tidak menyentuh satu berkas pun — yang dikembalikan hanya peta
    `nama berkas -> split` beserta angka-angka untuk dinilai pengguna
    SEBELUM ekspor dijalankan.
    """
    def cek():
        if batal is not None and batal():
            raise Dibatalkan()

    n = len(items)
    t0 = time.monotonic()
    log.info("splitting mulai: %s gambar, rasio %s, akun %r",
             f"{n:,}", ":".join(f"{x:.0%}"[:-1] for x in rasio), kunci or "-")
    catat_maju(kunci, fase="pindai", n=0, total=n, persen=0.0)
    cek()

    nama = [it["img"].name for it in items]
    peringatan: list[str] = []

    # -- 1. sesi
    catat_maju(kunci, fase="sesi", persen=5.0)
    gran, laporan = pilih_granularitas(nama, rasio)
    sesi = [kunci_sesi(x, gran) for x in nama]
    tanpa_stempel = sum(1 for s in sesi if s.startswith("file:"))

    peta_sesi: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(sesi):
        peta_sesi[s].append(i)
    grup = list(peta_sesi.values())
    besar = max((len(g) for g in grup), default=0)

    log.info("sesi: granularitas %s, %s sesi, terbesar %s (%.1f%%), "
             "%s tanpa stempel waktu", gran, f"{len(peta_sesi):,}", f"{besar:,}",
             100.0 * besar / max(n, 1), f"{tanpa_stempel:,}")
    if len(peta_sesi) < MIN_SESI:
        peringatan.append(
            f"Hanya {len(peta_sesi)} sesi foto untuk {n} gambar. Train dan valid "
            f"pasti berbagi kondisi pemotretan yang sama, jadi angka validasi "
            f"akan tetap optimistis. Perbaikan sungguhannya: foto ulang di "
            f"hari, tempat, atau cahaya yang berbeda, lalu jadikan itu test.")
    if n and besar / n > MAKS_GRUP:
        peringatan.append(
            f"Satu sesi memuat {100 * besar / n:.0f}% dataset, jadi rasio yang "
            f"kamu minta tidak mungkin dipenuhi persis.")
    if tanpa_stempel == n and n:
        peringatan.append(
            "Tidak ada nama berkas yang memuat stempel waktu, jadi "
            "pengelompokan sesi tidak memberi perlindungan apa pun. "
            "Seluruhnya bergantung pada pemeriksaan isi gambar.")
    cek()

    # -- 2. bagikan
    catat_maju(kunci, fase="bagi", persen=10.0)
    hasil = _bagikan(grup, items, rasio)
    # Ukuran SEBELUM kembaran dipindahkan. Tanpa ini, rasio yang meleset
    # akibat pemindahan akan dilaporkan seolah salah pembagian sesi — dan
    # pengguna mencari-cari sebab di tempat yang salah.
    sebelum = {s: len(hasil[s]) for s in SPLIT}
    cek()

    # -- 3. dHash, kalibrasi, lalu pindahkan kembaran keluar dari valid/test
    dipindah = {"valid": 0, "test": 0}
    terbaca = 0
    kalib: dict = {}
    mandiri: dict = {}
    ambang = AMBANG_KEMBAR
    if pakai_dhash and n:
        catat_maju(kunci, fase="dhash", n=0, total=n, persen=10.0)
        H: dict[int, np.ndarray] = {}
        # Langkah laporan mengikuti besar datasetnya, bukan angka tetap.
        # Dengan patokan tetap 50, dataset 40 gambar hanya melapor sekali dan
        # bilahnya tampak diam; dataset sejuta melapor 20.000 kali tanpa ada
        # yang bisa membedakannya. Sekitar 200 laporan pas untuk keduanya.
        langkah = max(1, n // 200)
        for k, it in enumerate(items):
            h = dhash(it["img"])
            if h is not None:
                H[k] = h
            if k % langkah == 0:
                cek()
                # Pembacaan gambar mendominasi waktu kerjanya, jadi ia yang
                # mengisi sebagian besar bilah: 10% -> 74%.
                catat_maju(kunci, n=k + 1, total=n,
                           persen=10.0 + 64.0 * (k + 1) / n)
        terbaca = len(H)
        log.info("sidik jari: %s dari %s gambar terbaca dalam %.0f dtk",
                 f"{terbaca:,}", f"{n:,}", time.monotonic() - t0)
        if terbaca < n:
            log.warning("%s gambar TIDAK terbaca dan tidak ikut diperiksa "
                        "kemiripannya", f"{n - terbaca:,}")
        cek()

        # Kalibrasi SESUDAH sidik jarinya jadi: separuh bahannya — pasangan
        # foto beda-sesi — sudah tersedia gratis di situ.
        catat_maju(kunci, fase="kalibrasi", persen=76.0)
        ambang, kalib = kalibrasi_ambang(items, H, sesi)
        log.info("kalibrasi: ambang %s bit dari %s (kembaran p99 %s, "
                 "beda-sesi p1 %s, %s foto contoh, terpisah=%s)",
                 ambang, BIT_HASH,
                 f"{kalib.get('kembaran_p99', 0):.0f}",
                 "-" if kalib.get("beda_p1") is None else f"{kalib['beda_p1']:.0f}",
                 kalib.get("contoh", 0), kalib.get("terpisah"))
        if kalib.get("dipotong"):
            peringatan.append(
                f"Foto dataset ini berubah sangat jauh saat diproses ulang "
                f"({kalib['kembaran_p99']:.0f} bit dari {BIT_HASH}), jadi ambang "
                f"yang diperlukan melampaui batas aman {AMBANG_MAKS}. Ambangnya "
                f"dipatok di {AMBANG_MAKS} dan sebagian kembaran bisa lolos.")
        elif kalib and not kalib.get("terpisah"):
            peringatan.append(
                f"Foto dataset ini terlalu mirip satu sama lain untuk dipisahkan "
                f"dengan pasti: jarak antar-foto dari sesi berbeda sudah sedekat "
                f"jarak antara satu foto dan hasil olah ulangnya sendiri. Ambang "
                f"{ambang} bit dipilih untuk mendahulukan menangkap kembaran, "
                f"jadi sebagian foto yang sebenarnya berbeda ikut pindah ke train.")
        cek()

        catat_maju(kunci, fase="bersih", persen=80.0)
        # Diulang sampai TENANG, bukan sampai jatah putaran habis. Gambar yang
        # baru pindah ke train ikut jadi acuan, dan bisa menarik gambar lain
        # yang tadinya aman.
        putaran = 0
        while putaran < MAKS_PUTARAN:
            putaran += 1
            acuan = {i: H[i] for i in hasil["train"] if i in H}
            pindah_kali_ini = 0
            for split in ("valid", "test"):
                uji = {i: H[i] for i in hasil[split] if i in H}
                kena = cari_kembar(acuan, uji, ambang)
                if not kena:
                    continue
                hasil[split] = [i for i in hasil[split] if i not in kena]
                hasil["train"].extend(sorted(kena))
                dipindah[split] += len(kena)
                pindah_kali_ini += len(kena)
            catat_maju(kunci, persen=80.0 + 15.0 * min(putaran / 8.0, 1.0))
            if pindah_kali_ini:
                log.info("putaran %d: %s gambar pindah ke train "
                         "(valid %s, test %s)", putaran, f"{pindah_kali_ini:,}",
                         f"{len(hasil['valid']):,}", f"{len(hasil['test']):,}")
            cek()
            if not pindah_kali_ini:
                break
        else:
            peringatan.append(
                f"Pemindahan kembaran belum tenang setelah {MAKS_PUTARAN} "
                f"putaran, jadi mungkin masih ada sisa kembaran di valid/test. "
                f"Laporkan ini — seharusnya tidak terjadi.")

        # -- 3b. seberapa mandiri valid/test setelah semuanya bersih
        catat_maju(kunci, fase="nilai", persen=96.0)
        mandiri = nilai_kemandirian(H, hasil, sesi)
        for split in ("valid", "test"):
            b = mandiri.get(split) or {}
            nilai = b.get("kemandirian")
            if nilai is not None and b.get("n") and nilai < 0.8:
                peringatan.append(
                    f"{split} bersih dari kembaran, tapi belum tentu berarti: "
                    f"gambarnya cuma {nilai:.2f}x sejauh dari train dibanding "
                    f"jarak satu gambar train ke sesi train lain. Angka "
                    f"validasinya akan optimistis walau tidak ada yang bocor.")
            if b.get("n") and b.get("n_sesi", 0) <= 2 and len(peta_sesi) > 3:
                peringatan.append(
                    f"{split} hanya berasal dari {b['n_sesi']} sesi pemotretan, "
                    f"jadi ia cuma menguji {b['n_sesi']} kondisi — meja, cahaya, "
                    f"dan sudut yang itu-itu saja, berapa pun jumlah gambarnya.")

    # -- 4. verifikasi: tidak boleh ada sesi yang muncul di dua split
    catat_maju(kunci, fase="selesai", persen=97.0)
    dimana: dict[str, set[str]] = defaultdict(set)
    for split in SPLIT:
        for i in hasil[split]:
            dimana[sesi[i]].add(split)
    bocor = sorted(k for k, v in dimana.items() if len(v) > 1)
    # Sesi yang terbelah HANYA boleh terjadi akibat pemindahan kembaran, dan
    # arahnya selalu menuju train — itu mengurangi kebocoran, bukan menambah.
    bocor_bukan_train = [k for k in bocor if dimana[k] != {"train", "valid"}
                         and dimana[k] != {"train", "test"}]
    if bocor_bukan_train:
        peringatan.append(
            f"{len(bocor_bukan_train)} sesi muncul di valid DAN test sekaligus "
            f"— laporkan ini, seharusnya tidak terjadi.")

    n_objek = {s: sum(_kelas_item(items[i]).total() for i in hasil[s])
               for s in SPLIT}

    # Satu peringatan per split, sebab yang paling menjelaskan saja. Versi
    # sebelumnya mengeluarkan sampai sembilan baris untuk dataset empat gambar
    # — "valid habis", "valid jadi 0% padahal diminta 25%", dan "valid tidak
    # memuat objek" adalah tiga cara mengatakan hal yang sama, dan tumpukan
    # begitu justru membuat yang penting ikut terlewat.
    for si, split in enumerate(SPLIT):
        if rasio[si] <= 0 or not n:
            continue
        nyata = len(hasil[split]) / n
        meleset = abs(nyata - rasio[si]) > max(0.05, 0.5 * rasio[si])

        if split != "train" and not hasil[split]:
            if sebelum[split] and dipindah.get(split) >= sebelum[split]:
                peringatan.append(
                    f"{split} habis: seluruh {sebelum[split]} gambarnya ternyata "
                    f"kembaran gambar di train, jadi dipindahkan ke sana. "
                    f"Dataset ini terlalu banyak memuat foto yang sama untuk "
                    f"bisa dinilai sendiri — tidak ada yang tersisa yang belum "
                    f"pernah dilihat model.")
            else:
                peringatan.append(
                    f"{split} tidak kebagian satu gambar pun. Sesi tidak boleh "
                    f"dipecah dan dataset ini hanya punya {len(peta_sesi)} sesi, "
                    f"jadi tidak ada sesi tersisa untuk diberikan.")
            continue

        if split != "train" and not n_objek[split] and sum(n_objek.values()):
            peringatan.append(
                f"{split} berisi {len(hasil[split])} gambar tapi tidak satu pun "
                f"memuat objek, jadi tidak bisa dipakai menilai model — mAP-nya "
                f"tidak terdefinisi. Labeli lebih banyak sesi lebih dulu.")
            continue

        if split != "train" and len(hasil[split]) < 50:
            peringatan.append(
                f"{split} tinggal {len(hasil[split])} gambar — terlalu sedikit "
                f"untuk metrik yang stabil. Dataset ini banyak berisi foto "
                f"berulang; menambah foto baru lebih menolong daripada "
                f"melonggarkan ambang.")
            continue

        if not meleset:
            continue
        # Sebabnya disebut menurut apa yang benar-benar terjadi. Rasio yang
        # meleset karena kembaran dipindahkan bukan soal pembagian sesi, dan
        # menyalahkan sesi hanya menyuruh orang mencari di tempat yang salah.
        if dipindah.get(split):
            peringatan.append(
                f"{split} jadi {100 * nyata:.0f}% padahal diminta "
                f"{100 * rasio[si]:.0f}%, karena {dipindah[split]} dari "
                f"{sebelum[split]} gambarnya kembaran gambar di train dan "
                f"dipindahkan ke sana. Itu memang tujuannya.")
        else:
            peringatan.append(
                f"{split} jadi {100 * nyata:.0f}% padahal diminta "
                f"{100 * rasio[si]:.0f}%. Sesi tidak boleh dipecah, dan dataset "
                f"ini hanya punya {len(peta_sesi)} sesi — rasio persis memang "
                f"tidak tercapai.")

    peta = {}
    for split in SPLIT:
        for i in hasil[split]:
            peta[items[i]["img"].name] = split

    tot = sum(len(hasil[s]) for s in SPLIT) or 1
    ringkas = {
        "peta": peta,
        "granularitas": gran,
        "laporan_granularitas": laporan,
        "n_sesi": len(peta_sesi),
        "grup_terbesar": besar,
        "grup_terbesar_pct": 100.0 * besar / max(n, 1),
        "tanpa_stempel": tanpa_stempel,
        "dipindah": dipindah,
        "dhash_terbaca": terbaca,
        "ambang": ambang,
        "kalibrasi": kalib,
        "kemandirian": mandiri,
        "jumlah": {s: len(hasil[s]) for s in SPLIT},
        "persen": {s: 100.0 * len(hasil[s]) / tot for s in SPLIT},
        "kelas": {s: dict(sum((_kelas_item(items[i]) for i in hasil[s]),
                              Counter())) for s in SPLIT},
        "peringatan": peringatan,
        "total": n,
    }
    log.info("selesai dalam %.0f dtk: train %s / valid %s / test %s "
             "(%.1f : %.1f : %.1f), %s peringatan",
             time.monotonic() - t0,
             f"{ringkas['jumlah']['train']:,}", f"{ringkas['jumlah']['valid']:,}",
             f"{ringkas['jumlah']['test']:,}",
             ringkas["persen"]["train"], ringkas["persen"]["valid"],
             ringkas["persen"]["test"], len(peringatan))
    for b in ("valid", "test"):
        m = (mandiri.get(b) or {})
        if m.get("kemandirian") is not None:
            log.info("  %s: kemandirian %.2f, %s sesi, %s gambar",
                     b, m["kemandirian"], f"{m['n_sesi']:,}", f"{m['n']:,}")
    # Peringatan ikut ke log, bukan cuma ke layar: kalau nanti ada yang
    # janggal, layarnya sudah lama tertutup.
    for w in peringatan:
        log.warning("  ! %s", w)
    catat_maju(kunci, fase="selesai", persen=100.0, hasil=ringkas)
    return ringkas
