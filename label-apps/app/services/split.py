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
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

# Butuh sekurang-kurangnya sekian sesi sebelum sebuah granularitas dianggap
# layak. Di bawah ini train dan valid pasti berbagi kondisi pemotretan, dan
# angka validasinya akan optimistis walau pembelahannya sendiri benar.
MIN_SESI = 20

# Satu grup tidak boleh memuat lebih dari sekian bagian dataset; kalau
# melebihi, rasio yang diminta tidak mungkin dipenuhi.
MAKS_GRUP = 0.40

# Hamming <= ini dianggap kembaran. Longgar boleh, karena hasilnya
# pemindahan — bukan penggabungan — jadi tidak bisa meruntuhkan grup.
AMBANG_KEMBAR = 5

# Berapa kali pemindahan diulang sebelum menyerah. Biasanya selesai di
# putaran kedua; batas ini hanya penjaga supaya tidak berputar selamanya.
MAKS_PUTARAN = 5

FASE = {
    "pindai": "Mengumpulkan gambar",
    "sesi": "Mengelompokkan per sesi pemotretan",
    "dhash": "Membaca isi gambar",
    "bagi": "Membagikan ke train/valid/test",
    "bersih": "Memindahkan kembaran keluar dari valid/test",
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


def dhash(path) -> np.ndarray | None:
    """Sidik jari 64-bit dari gradien mendatar gambar kecil.

    Tahan terhadap perubahan ukuran, kompresi ulang, dan pergeseran kecerahan
    — persis tiga hal yang membuat dua salinan foto yang sama punya md5 beda.
    """
    im = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if im is None:
        return None
    im = cv2.resize(im, (9, 8), interpolation=cv2.INTER_AREA)
    return np.packbits(im[:, 1:] > im[:, :-1])


def _pita(h: np.ndarray, n: int) -> list[bytes]:
    """
    Potong hash menjadi n pita yang mencakup SELURUH bit.

    Pembagiannya sengaja tidak rata (8 byte jadi 6 pita -> 1,1,2,1,1,2):
    yang penting setiap bit masuk tepat satu pita. Versi pertama memakai
    `lebar = len(b) // n` lalu memotong `[:n]`, dan itu membuang dua byte
    terakhir — jaminan lubang merpatinya batal diam-diam, sehingga sebagian
    kembaran lolos tanpa pernah terlihat salah.
    """
    b = h.tobytes()
    n = max(1, min(n, len(b)))
    batas = [len(b) * i // n for i in range(n + 1)]
    return [b[batas[i]:batas[i + 1]] for i in range(n)]


def cari_kembar(acuan: dict[int, np.ndarray], uji: dict[int, np.ndarray],
                ambang: int = AMBANG_KEMBAR) -> set[int]:
    """
    Indeks di `uji` yang punya kembaran di `acuan` (Hamming <= ambang).

    Memakai indeks banyak-pita, bukan membandingkan semua lawan semua.
    Perbandingan menyeluruh berskala kuadrat: pada satu juta gambar itu
    8x10^12 perbandingan byte. Dengan menyimpan hash ke dalam ambang+1 pita,
    dua hash yang berjarak <= ambang PASTI sama persis di sekurang-kurangnya
    satu pita (asas lubang merpati: ambang perbedaan bit tidak bisa tersebar
    ke lebih dari ambang pita). Jadi cukup membandingkan yang sepita.
    """
    if not acuan or not uji:
        return set()
    n_pita = ambang + 1
    indeks: list[dict[bytes, list[int]]] = [defaultdict(list) for _ in range(n_pita)]
    for i, h in acuan.items():
        for p, potong in enumerate(_pita(h, n_pita)):
            indeks[p][potong].append(i)

    ketemu = set()
    for j, h in uji.items():
        calon = set()
        for p, potong in enumerate(_pita(h, n_pita)):
            calon.update(indeks[p].get(potong, ()))
        if not calon:
            continue
        A = np.array([acuan[i] for i in calon], dtype=np.uint8)
        d = _BIT[np.bitwise_xor(A, h[None, :])].sum(1)
        if d.min() <= ambang:
            ketemu.add(j)
    return ketemu


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

    # -- 3. dHash, lalu pindahkan kembaran keluar dari valid/test
    dipindah = {"valid": 0, "test": 0}
    terbaca = 0
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
                # dHash mendominasi waktu kerjanya, jadi ia yang mengisi
                # sebagian besar bilah: 10% -> 80%.
                catat_maju(kunci, n=k + 1, total=n,
                           persen=10.0 + 70.0 * (k + 1) / n)
        terbaca = len(H)
        cek()

        catat_maju(kunci, fase="bersih", persen=82.0)
        for putaran in range(MAKS_PUTARAN):
            acuan = {i: H[i] for i in hasil["train"] if i in H}
            pindah_kali_ini = 0
            for split in ("valid", "test"):
                uji = {i: H[i] for i in hasil[split] if i in H}
                kena = cari_kembar(acuan, uji)
                if not kena:
                    continue
                hasil[split] = [i for i in hasil[split] if i not in kena]
                hasil["train"].extend(sorted(kena))
                dipindah[split] += len(kena)
                pindah_kali_ini += len(kena)
                # Yang baru pindah ikut jadi acuan pada putaran berikutnya.
            catat_maju(kunci, persen=82.0 + 13.0 * (putaran + 1) / MAKS_PUTARAN)
            cek()
            if not pindah_kali_ini:
                break
        else:
            peringatan.append(
                f"Pemindahan kembaran belum tenang setelah {MAKS_PUTARAN} "
                f"putaran; masih mungkin ada sisa kembaran di valid/test.")

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
        "jumlah": {s: len(hasil[s]) for s in SPLIT},
        "persen": {s: 100.0 * len(hasil[s]) / tot for s in SPLIT},
        "kelas": {s: dict(sum((_kelas_item(items[i]) for i in hasil[s]),
                              Counter())) for s in SPLIT},
        "peringatan": peringatan,
        "total": n,
    }
    catat_maju(kunci, fase="selesai", persen=100.0, hasil=ringkas)
    return ringkas
