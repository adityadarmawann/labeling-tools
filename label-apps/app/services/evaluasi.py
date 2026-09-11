"""
Evaluasi produksi: apakah model ini benar-benar bisa dipakai di ruang detektor.

Acuannya eval-produksi-v14.py, dan alasan keberadaannya ditulis di sana dengan
gamblang: v13 melaporkan mAP50-95 0,9499 lalu benar 0 dari 7 pada foto RVM
sungguhan. Angka validasi tidak menangkap kegagalan itu sama sekali.

    "mAP validasi tidak berguna kalau valid-nya bocor atau domainnya beda.
     Yang berlaku hanya foto dari RVM."

TIGA PENGUKURAN, DAN YANG KEDUA YANG PALING MENENTUKAN

1. AKURASI PADA FOTO SUNGGUHAN
   Butuh label. Diambil dari split `test` versinya, yang labelnya sudah ada.

2. KETERGANTUNGAN WARNA  <- ini yang tidak ada di mana pun selain di sini
   Objek yang sama diberi beberapa perlakuan warna TANPA mengubah bentuknya
   sedikit pun. Kalau kelas yang diprediksi ikut berubah, model memutuskan
   lewat warna, bukan bentuk — dan ia akan runtuh begitu lampu ruang detektor
   berbeda dari lampu saat pemotretan.

   Terukur di v13, dan inilah bukti yang menjelaskan kegagalannya:
       gelas plastik            -> kaleng 0,97       (salah)
       gelas plastik GRAYSCALE  -> plastic-cup 0,93  (benar)
       botol bening             -> plastic-cup 0,75  (salah)
       botol bening DICAT MERAH -> botol 0,80        (benar)

   Pengukuran ini TIDAK BUTUH LABEL sama sekali. Ia membandingkan jawaban
   model dengan jawabannya sendiri, jadi bisa dijalankan pada foto apa pun.

3. KELAS DEFAULT
   Model diberi latar RVM kosong. Model yang sehat menjawab "tidak ada".
   v13 menjawab `kaleng` 0,37 — tanda ada kelas yang jadi tempat pelarian
   saat fiturnya ambigu.

SATU PERBAIKAN DARI v14
v14 mengambil petak pojok kiri-atas 45% dan BERHARAP bagian itu kosong. Itu
tebakan, dan pada foto yang objeknya memang di pojok kiri-atas ia mengukur hal
yang salah. Di sini, kalau projeknya punya foto ruang RVM yang diunggah
(.latar/asli — memang diminta berupa ruangan KOSONG), foto itu yang dipakai.
Petak pojok tetap ada sebagai cadangan, dan hasilnya menyebutkan mana yang
dipakai supaya angkanya tidak dibaca lebih kuat daripada semestinya.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

# Ambang keputusan, disalin apa adanya dari eval-produksi-v14.py. Bukan angka
# bulat yang enak dibaca: 50% berarti separuh objek berganti kelas hanya karena
# lampunya berbeda, dan model seperti itu sudah pasti gagal di ruang detektor.
AMBANG_BURUK = 50.0
AMBANG_SEDANG = 20.0

# Setelan inferensi, sama dengan v14. Kalau berbeda, angkanya tidak bisa
# dibandingkan dengan angka v14 yang sudah ada.
CONF = 0.25
IOU = 0.45
IMGSZ = 640


# ============================================================
# PERLAKUAN WARNA — bentuk TIDAK berubah sama sekali
# ============================================================
#
# Itu seluruh dasar pengukurannya: kalau bentuknya ikut berubah, perubahan
# jawaban model tidak lagi membuktikan apa pun tentang warna. Jadi tidak boleh
# ada satu pun operasi di sini yang memindahkan piksel — tidak crop, tidak
# rotasi, tidak resize. Ada tes yang menjaganya.

def t_asli(im, rng):
    return im


def t_grayscale(im, rng):
    return cv2.cvtColor(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)


def t_iluminan(im, rng):
    """Tiga kanal dikali gain berbeda — meniru lampu berwarna."""
    g = rng.uniform(0.65, 1.35, 3)
    return np.clip(im.astype(np.float32) * g.reshape(1, 1, 3), 0, 255).astype(np.uint8)


def _putar_hue(im, d):
    h = cv2.cvtColor(im, cv2.COLOR_BGR2HSV).astype(np.int16)
    h[..., 0] = (h[..., 0] + d) % 180
    return cv2.cvtColor(h.astype(np.uint8), cv2.COLOR_HSV2BGR)


def t_hue40(im, rng):
    return _putar_hue(im, 40)


def t_hue90(im, rng):
    """Putaran 90 dari 180 — rona yang berlawanan. Perlakuan paling keras."""
    return _putar_hue(im, 90)


def t_desat(im, rng):
    h = cv2.cvtColor(im, cv2.COLOR_BGR2HSV).astype(np.int16)
    h[..., 1] = (h[..., 1] * 0.3).astype(np.int16)
    return cv2.cvtColor(h.astype(np.uint8), cv2.COLOR_HSV2BGR)


def t_redup(im, rng):
    """Seluruh gambar diredupkan. Rona TIDAK berubah sama sekali."""
    return np.clip(im.astype(np.float32) * 0.55, 0, 255).astype(np.uint8)


def t_terang(im, rng):
    """Kebalikannya. Juga tanpa menyentuh rona."""
    return np.clip(im.astype(np.float32) * 1.45 + 12, 0, 255).astype(np.uint8)


# Perlakuan dikelompokkan, dan pengelompokan inilah yang membuat satu alat
# ukur bisa menilai DUA MODE dengan adil:
#
#   RONA     menggeser warnanya. Di mode `bentuk` jawaban yang berubah di sini
#            BURUK — model memakai warna sebagai pintasan. Di mode `warna`
#            justru WAJAR: rona memang identitas kelasnya, dan memutar rona
#            sama saja dengan mengganti produknya.
#
#   TERANG   mengubah gelap-terang tanpa menyentuh rona. Jawaban yang berubah
#            di sini BURUK di KEDUA MODE, tanpa kecuali. Ruang detektor kadang
#            terang kadang remang, dan model yang kelasnya ikut berganti karena
#            lampunya diredupkan tidak bisa dipakai di mana pun.
#
# Tanpa pemisahan ini, mode `warna` tidak bisa dinilai sama sekali: seluruh
# skornya akan tinggi karena rona memang sengaja jadi petunjuk, dan kerapuhan
# yang sesungguhnya — terhadap terang — tenggelam di dalamnya.
RONA = (
    ("grayscale", t_grayscale),
    ("hue +40", t_hue40),
    ("hue +90", t_hue90),
    ("saturasi x0.3", t_desat),
)
TERANG = (
    ("iluminan acak", t_iluminan),
    ("diredupkan", t_redup),
    ("diterangkan", t_terang),
)
PERLAKUAN = (("asli", t_asli),) + RONA + TERANG

NAMA_RONA = tuple(n for n, _ in RONA)
NAMA_TERANG = tuple(n for n, _ in TERANG)


# ============================================================
# PENILAIAN
# ============================================================

def _goyah(vals, indeks) -> bool:
    """Jawabannya berbeda-beda di antara perlakuan yang dipilih?

    `asli` SELALU ikut dibandingkan: yang ditanyakan bukan "apakah keempat
    perlakuan ini saling setuju" melainkan "apakah perlakuan ini mengubah
    jawaban model dari jawaban aslinya".
    """
    dipakai = [vals[0]] + [vals[i] for i in indeks if i < len(vals)]
    ada = [v for v in dipakai if v is not None]
    if not ada:
        return False
    return len(set(dipakai)) > 1


def nilai_warna(jawaban_per_gambar: list[list[str]], mode: str = None) -> dict:
    """Seberapa jauh jawaban model bergantung pada warna — dinilai per MODE.

    `jawaban_per_gambar` satu daftar per gambar, urutannya sama dengan
    PERLAKUAN (asli, lalu RONA, lalu TERANG).

    Dua angka dihitung terpisah, dan itu inti rancangannya:

      skor_rona     berapa persen gambar yang jawabannya berubah ketika
                    RONANYA digeser. Di mode `bentuk` ini yang dinilai; di
                    mode `warna` ia dilaporkan tetapi TIDAK dijadikan vonis.

      skor_terang   berapa persen yang berubah ketika hanya TERANGNYA yang
                    berubah. Dinilai di KEDUA mode, tanpa kecuali.

    Yang dihitung PERUBAHAN, bukan kebenaran — sengaja. Model yang konsisten
    salah tetap lebih bisa dipercaya daripada model yang jawabannya berganti
    mengikuti lampu: yang pertama bisa diperbaiki dengan data, yang kedua
    akan terus berganti-ganti di produksi tanpa pola.
    """
    from . import mode_warna as mw

    m = mw.sah(mode)
    nama = [n for n, _ in PERLAKUAN]
    i_rona = [nama.index(n) for n in NAMA_RONA if n in nama]
    i_terang = [nama.index(n) for n in NAMA_TERANG if n in nama]

    total = kosong = 0
    b_rona = b_terang = 0
    goyah_rona: list[int] = []
    goyah_terang: list[int] = []
    for i, vals in enumerate(jawaban_per_gambar):
        if not vals:
            continue
        # Gambar yang TIDAK PERNAH terdeteksi di perlakuan mana pun tidak
        # mengatakan apa-apa tentang warna — model itu memang tidak melihat
        # objeknya. Dihitung terpisah, bukan dibuang diam-diam: kalau
        # jumlahnya banyak, itu kegagalan tersendiri yang harus dilaporkan.
        if all(v is None for v in vals):
            kosong += 1
            continue
        total += 1
        # Gambar yang jawaban ASLINYA kosong tetapi terdeteksi di perlakuan
        # lain IKUT DIHITUNG. Itu bentuk ketergantungan warna yang paling
        # telanjang: model hanya melihat objeknya di bawah warna tertentu.
        # eval-produksi-v14.py melewatkannya (`if vals[0] == "-": continue`),
        # dan pada model yang belum terlatih hal itu membuat skornya 0/0 lalu
        # dilaporkan "baik" — jaminan yang palsu.
        if _goyah(vals, i_rona):
            b_rona += 1
            goyah_rona.append(i)
        if _goyah(vals, i_terang):
            b_terang += 1
            goyah_terang.append(i)

    n_semua = total + kosong
    s_rona = 100.0 * b_rona / total if total else 0.0
    s_terang = 100.0 * b_terang / total if total else 0.0

    dasar = {"total": total, "kosong": kosong,
             "skor_rona": round(s_rona, 1), "skor_terang": round(s_terang, 1),
             "berubah_rona": b_rona, "berubah_terang": b_terang,
             "goyah": sorted(set(goyah_rona) | set(goyah_terang)),
             "goyah_rona": goyah_rona, "goyah_terang": goyah_terang,
             "mode": m}

    # Terlalu sedikit deteksi berarti skornya tidak bermakna, berapa pun
    # angkanya. Dikatakan apa adanya, karena lencana hijau "baik" pada model
    # yang tidak mendeteksi apa pun jauh lebih berbahaya daripada tidak ada
    # angka sama sekali.
    if n_semua and total < max(3, n_semua * 0.3):
        return {**dasar, "skor": round(s_rona, 1), "berubah": b_rona,
                "tingkat": "tak-terukur",
                "pesan": (f"Model tidak mendeteksi apa pun pada {kosong} dari "
                          f"{n_semua} foto uji, jadi ketergantungan warnanya "
                          "tidak bisa diukur. Yang perlu diperbaiki lebih dulu "
                          "kemampuan mendeteksinya — latih lebih lama atau "
                          "periksa apakah versinya cocok dengan tugasnya.")}

    # Kerapuhan terhadap TERANG selalu buruk, mode apa pun. Diperiksa lebih
    # dulu karena ia lebih menentukan: model yang berganti kelas saat lampunya
    # diredupkan tidak bisa dipakai di ruang detektor mana pun.
    if s_terang >= AMBANG_BURUK:
        return {**dasar, "skor": round(s_terang, 1), "berubah": b_terang,
                "tingkat": "buruk",
                "pesan": (f"{b_terang} dari {total} gambar berganti kelas "
                          "hanya karena TERANGNYA diubah — rona tidak "
                          "disentuh sama sekali. Ini rapuh di mode mana pun: "
                          "ruang detektor kadang terang kadang remang.")}

    if m == mw.WARNA:
        # Mode `warna`: rona memang identitas kelasnya. Jawaban yang berubah
        # saat rona diputar bukan kegagalan — itu justru yang diminta.
        if s_terang >= AMBANG_SEDANG:
            tingkat, pesan = "sedang", (
                f"Rona boleh menentukan kelas di mode ini, dan memang begitu "
                f"({s_rona:.0f}%). Tetapi {b_terang} dari {total} gambar juga "
                "berganti kelas hanya karena terangnya berubah, dan itu tetap "
                "harus diperbaiki.")
        else:
            tingkat, pesan = "baik", (
                f"Model memakai rona sebagai petunjuk ({s_rona:.0f}% berubah "
                "saat rona diputar) — memang itu yang diminta di mode ini — "
                "dan tetap stabil ketika hanya terangnya yang berubah "
                f"({s_terang:.0f}%).")
        return {**dasar, "skor": round(s_rona, 1), "berubah": b_rona,
                "tingkat": tingkat, "pesan": pesan}

    # Mode `bentuk`: ini kasus v14, dan rona TIDAK boleh menentukan apa pun.
    if s_rona >= AMBANG_BURUK:
        tingkat, pesan = "buruk", (
            "Model memutuskan lewat warna, bukan bentuk. Ia akan gagal begitu "
            "lampu ruang detektor berbeda dari lampu saat pemotretan — persis "
            "yang terjadi pada v13.")
    elif s_rona >= AMBANG_SEDANG:
        tingkat, pesan = "sedang", (
            "Masih ada sisa ketergantungan warna. Periksa apakah augmentasi "
            "warna benar-benar menyala di versinya DAN di setelan trainingnya.")
    else:
        tingkat, pesan = "baik", (
            "Keputusan model stabil terhadap perubahan warna.")
    return {**dasar, "skor": round(s_rona, 1), "berubah": b_rona,
            "tingkat": tingkat, "pesan": pesan}


def nilai_default(tally: dict[str, int], n_latar: int) -> dict:
    """Adakah kelas yang jadi tempat pelarian saat fiturnya ambigu."""
    if not tally:
        return {"tally": {}, "tingkat": "baik", "n": n_latar,
                "pesan": "Tidak ada deteksi pada latar kosong."}
    urut = sorted(tally.items(), key=lambda t: -t[1])
    teratas, jml = urut[0]
    # Satu deteksi dari dua puluh latar itu derau; sepertiga bukan.
    porsi = jml / n_latar if n_latar else 0
    tingkat = "buruk" if porsi >= 0.33 else ("sedang" if porsi >= 0.1 else "baik")
    return {"tally": dict(urut), "tingkat": tingkat, "n": n_latar,
            "teratas": teratas, "porsi": round(porsi * 100, 1),
            "pesan": (f"`{teratas}` muncul pada {jml} dari {n_latar} latar kosong "
                      f"({porsi * 100:.0f}%). Kelas yang muncul di latar kosong "
                      "adalah tempat pelarian model saat fiturnya ambigu.")}


def nilai_akurasi(pasangan: list[tuple]) -> dict:
    """(sebenarnya, prediksi) per gambar -> akurasi dan kebingungan."""
    benar = 0
    bingung: dict[str, dict[str, int]] = {}
    for asli, tebak in pasangan:
        if asli is None:
            continue
        if asli == tebak:
            benar += 1
        bingung.setdefault(asli, {})
        k = tebak or "(tidak ada)"
        bingung[asli][k] = bingung[asli].get(k, 0) + 1
    n = sum(1 for a, _ in pasangan if a is not None)
    return {"benar": benar, "n": n,
            "persen": round(100.0 * benar / n, 1) if n else 0.0,
            "bingung": bingung}


def putusan(warna: dict, akurasi: dict | None, default: dict) -> dict:
    """Satu kalimat kesimpulan, karena tiga angka terpisah tidak menyimpulkan.

    Dasarnya kalimat penutup eval-produksi-v14.py sendiri: "v14 berhasil kalau
    AKURASI naik DAN ketergantungan warna turun. Kalau akurasi naik tapi
    ketergantungan warna tetap tinggi, perbaikannya rapuh dan akan gagal lagi
    saat lampu berubah." — dengan satu tambahan yang tidak ada di v14: apa
    yang dianggap "ketergantungan warna yang buruk" bergantung pada MODE
    projeknya.
    """
    # .get, bukan [] : fungsi kesimpulan tidak boleh jatuh karena satu medan
    # keterangan tidak ada. Yang menentukan `tingkat`, dan itu selalu ada.
    ket = warna.get("pesan") or ""
    if warna["tingkat"] == "tak-terukur":
        return {"tingkat": "buruk", "pesan":
                ("Belum bisa dinilai: model ini nyaris tidak mendeteksi apa "
                 "pun pada foto uji. " + ket).strip()}
    if warna["tingkat"] == "buruk":
        return {"tingkat": "buruk",
                "pesan": ("JANGAN dikirim ke produksi. " + ket).strip()}
    if default["tingkat"] == "buruk":
        return {"tingkat": "buruk", "pesan":
                f"Hati-hati. `{default.get('teratas')}` jadi jawaban default "
                "pada latar kosong, jadi model menebak kelas itu setiap kali "
                "fiturnya ambigu."}
    if warna["tingkat"] == "sedang" or default["tingkat"] == "sedang":
        return {"tingkat": "sedang", "pesan":
                ("Layak diuji lebih lanjut, tetapi belum bersih. " + ket).strip()}
    if akurasi and akurasi["n"] and akurasi["persen"] < 70:
        return {"tingkat": "sedang", "pesan":
                f"Keputusannya stabil, tetapi akurasinya baru "
                f"{akurasi['persen']:.0f}%. Yang kurang datanya, bukan "
                "setelannya."}
    return {"tingkat": "baik", "pesan":
            "Model stabil terhadap perubahan yang seharusnya tidak mengubah "
            "jawabannya, dan tidak punya kelas pelarian."}


# ============================================================
# SUMBER FOTO
# ============================================================

EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def foto_uji(ds, nomor_versi: int, batas: int = 60) -> list[Path]:
    """Foto untuk diuji: split `test` versinya.

    `test` dipilih, bukan `valid`: valid dipakai saat training untuk memilih
    bobot terbaik, jadi mengukur di sana mengukur sesuatu yang sudah ikut
    menentukan modelnya sendiri.
    """
    from . import buatversi

    d = buatversi.dir_versi(Path(ds), nomor_versi) / "test" / "images"
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix.lower() in EXTS)[:batas]


def label_uji(ds, nomor_versi: int, gambar: Path) -> str | None:
    """Kelas sebenarnya sebuah foto uji, dari berkas label YOLO-nya.

    Diambil kelas dengan luas kotak TERBESAR, bukan baris pertama: satu foto
    bisa memuat beberapa objek, dan yang menentukan jawaban model adalah objek
    yang paling menonjol.
    """
    from . import buatversi

    d = buatversi.dir_versi(Path(ds), nomor_versi)
    p = d / "test" / "labels" / f"{gambar.stem}.txt"
    if not p.exists():
        return None
    terbaik, luas_maks = None, -1.0
    try:
        for baris in p.read_text().splitlines():
            sel = baris.split()
            if len(sel) < 5:
                continue
            try:
                kls = int(float(sel[0]))
                koor = [float(x) for x in sel[1:]]
            except ValueError:
                continue
            # Baris YOLO bisa berupa kotak (4 angka) atau poligon (berpasangan).
            if len(koor) == 4:
                luas = koor[2] * koor[3]
            else:
                xs, ys = koor[0::2], koor[1::2]
                luas = (max(xs) - min(xs)) * (max(ys) - min(ys)) if xs else 0.0
            if luas > luas_maks:
                terbaik, luas_maks = kls, luas
    except OSError:
        return None
    if terbaik is None:
        return None
    nama = kelas_versi(ds, nomor_versi)
    return nama[terbaik] if 0 <= terbaik < len(nama) else str(terbaik)


def kelas_versi(ds, nomor_versi: int) -> list[str]:
    """Nama kelas dari data.yaml versinya, urut sesuai indeksnya."""
    from . import buatversi

    p = buatversi.dir_versi(Path(ds), nomor_versi) / "data.yaml"
    if not p.exists():
        return []
    for baris in p.read_text().splitlines():
        if baris.strip().startswith("names:"):
            isi = baris.split(":", 1)[1].strip()
            if isi.startswith("[") and isi.endswith("]"):
                return [x.strip().strip("'\"") for x in isi[1:-1].split(",") if x.strip()]
    return []


def foto_latar(ds, batas: int = 20) -> tuple[list[Path], str]:
    """Foto ruang RVM KOSONG, untuk mengukur kelas default.

    Yang dipakai foto yang diunggah orang ke .latar — formnya memang meminta
    foto ruangan kosong. Kalau tidak ada, pemanggil memakai cara v14: petak
    pojok kiri-atas dari foto uji, yang cuma TEBAKAN bahwa bagian itu kosong.
    Mana yang dipakai ikut dilaporkan, supaya angkanya tidak dibaca lebih kuat
    daripada semestinya.
    """
    from . import latar

    d = Path(ds) / latar.FOLDER / latar.ASLI
    if d.is_dir():
        p = sorted(x for x in d.iterdir() if x.suffix.lower() in EXTS)[:batas]
        if p:
            return p, "foto ruang RVM yang diunggah"
    return [], "petak pojok foto uji (tebakan)"


def petak_kosong(im):
    """Cara v14: pojok kiri-atas 45%, diperbesar ke ukuran inferensi."""
    h, w = im.shape[:2]
    petak = im[0:max(1, int(h * 0.45)), 0:max(1, int(w * 0.45))]
    return cv2.resize(petak, (IMGSZ, IMGSZ), interpolation=cv2.INTER_CUBIC)
