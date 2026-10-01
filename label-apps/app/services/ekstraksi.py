"""
Ekstraksi frame dari video menjadi gambar, untuk projek berjenis image.

Projek image boleh menerima unggahan video; keluarannya tetap GAMBAR. Satu
video dipotong menjadi sejumlah frame yang disebar merata sepanjang durasinya,
lalu frame yang buram atau nyaris sama dengan frame sebelumnya dibuang supaya
yang tersisa layak dilabeli.

Kerapatannya tidak diatur lewat fps tetap, melainkan tiga PRESET adaptif —
"jarang"/"sedang"/"rapat". Alasannya: fps tetap tidak masuk akal untuk dua
ujung durasi sekaligus. Satu klip 2 detik pada 1 fps cuma menghasilkan 2 frame,
sementara video 1 menit pada 15 fps menghasilkan 900. Preset memetakan DURASI
ke jumlah target lewat kurva pangkat (sublinear), jadi video pendek diambil
rapat dan video panjang diambil jarang, dengan jumlah yang selalu wajar:

    durasi 2 dtk  -> jarang ~6   sedang ~12  rapat ~20
    durasi 1 mnt  -> jarang ~30  sedang ~60  rapat ~100

Semua CPU-only: cv2 membaca video (grab tanpa decode untuk frame yang
dilewati, retrieve hanya untuk kandidat), skor buram pakai varian Laplacian,
dan deduplikasi membandingkan thumbnail abu-abu antar frame yang disimpan.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from ..security import safe_slug

log = logging.getLogger(__name__)

# Preset kerapatan. Jumlah target = KOEF * durasi**PANGKAT. PANGKAT < 1 membuat
# kurvanya sublinear (video makin panjang, fps efektifnya makin turun), dan
# KOEF menggeser seluruh kurva naik-turun. "sedang" = 2x "jarang", "rapat"
# ~3,3x "jarang"; ketiganya dikalibrasi ke contoh di docstring modul.
PRESET_SAH = ("jarang", "sedang", "rapat")
PRESET_BAWAAN = "sedang"
_KOEF = {"jarang": 4.3, "sedang": 8.6, "rapat": 14.4}
_PANGKAT = 0.47

# Batas aman jumlah frame per video. MIN menjaga klip sangat pendek tetap
# memberi sesuatu; MAKS mencegah video berjam-jam meledak jadi ribuan gambar.
MIN_FRAME = 3
MAKS_FRAME = 600

# Kandidat diambil lebih banyak dari target (oversample) supaya masih ada sisa
# untuk dibuang saat menyaring buram/duplikat tanpa jatuh jauh di bawah target.
_OVERS = 1.6

# Ambang buram: sebuah frame dibuang kalau varian Laplacian-nya di bawah
# BURAM_RASIO * median seluruh kandidat (buram RELATIF terhadap video itu
# sendiri — ambang mutlak tak berlaku lintas resolusi/konten), tapi tak pernah
# lebih longgar dari BURAM_LANTAI (menangkap frame rata/blank yang nyaris nol).
_BURAM_RASIO = 0.35
_BURAM_LANTAI = 8.0

# Deduplikasi: dua frame dianggap kembar kalau rata-rata selisih absolut
# thumbnail abu-abu THUMB x THUMB-nya (skala 0..255) di bawah DUP_AMBANG.
_THUMB = 16
_DUP_AMBANG = 3.0

_JPEG_MUTU = 95
_FPS_BAWAAN = 25.0


class EkstrakTolak(Exception):
    """Video tak bisa dibaca atau tak menghasilkan satu frame pun."""


def sah_preset(preset: str) -> str:
    p = str(preset or "").strip().lower()
    return p if p in PRESET_SAH else PRESET_BAWAAN


def target_frame(durasi: float, preset: str) -> int:
    """Jumlah frame yang disasar untuk sebuah durasi (detik) dan preset."""
    durasi = max(0.0, float(durasi))
    n = round(_KOEF[sah_preset(preset)] * (durasi ** _PANGKAT)) if durasi > 0 else 0
    return int(min(MAKS_FRAME, max(MIN_FRAME, n)))


def _durasi(cap: "cv2.VideoCapture") -> tuple[int, float, float]:
    """(jumlah_frame, fps, durasi_detik). Jumlah frame diverifikasi dengan
    menghitung sendiri kalau metadata-nya mencurigakan (0 atau negatif)."""
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps != fps or fps <= 0:      # 0, NaN, negatif
        fps = _FPS_BAWAAN
    F = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if F <= 0:
        F = 0
        while cap.grab():
            F += 1
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    return F, float(fps), (F / fps if fps else 0.0)


def _indeks_kandidat(F: int, n: int) -> list[int]:
    """n indeks frame yang disebar merata di [0, F-1], tanpa duplikat."""
    if F <= 0 or n <= 0:
        return []
    if n >= F:
        return list(range(F))
    # round(i*(F-1)/(n-1)) membagi rata termasuk frame pertama dan terakhir.
    pembagi = n - 1 if n > 1 else 1
    idx = sorted({round(i * (F - 1) / pembagi) for i in range(n)})
    return idx


def _skor_buram(frame: "np.ndarray") -> tuple[float, "np.ndarray"]:
    """(varian Laplacian sebagai skor ketajaman, thumbnail abu-abu untuk dedup)."""
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    tajam = float(cv2.Laplacian(g, cv2.CV_64F).var())
    kecil = cv2.resize(g, (_THUMB, _THUMB), interpolation=cv2.INTER_AREA)
    return tajam, kecil.astype(np.float32)


def _duplikat(a: "np.ndarray", b: "np.ndarray") -> bool:
    return float(np.abs(a - b).mean()) < _DUP_AMBANG


def ekstrak(video: Path, keluar: Path, preset: str = PRESET_BAWAAN,
            prefix: str = "") -> dict:
    """
    Potong `video` menjadi gambar JPG di folder `keluar`.

    Mengembalikan ringkasan: berapa ditulis, berapa dibuang karena buram atau
    duplikat, plus durasi/target supaya pemanggil bisa melaporkannya. Tidak
    menyentuh berkas videonya sendiri — penghapusan diserahkan ke pemanggil
    (setelah frame-nya selamat), persis seperti arsip pada /unzip.
    """
    video = Path(video)
    keluar = Path(keluar)
    preset = sah_preset(preset)
    prefix = safe_slug(prefix or video.stem) or "frame"

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        cap.release()
        raise EkstrakTolak("video tidak bisa dibuka atau formatnya tak didukung")
    try:
        F, fps, durasi = _durasi(cap)
        if F <= 0:
            raise EkstrakTolak("video tidak berisi frame yang bisa dibaca")
        target = target_frame(durasi, preset)
        M = min(F, max(target, round(target * _OVERS)))
        kandidat = _indeks_kandidat(F, M)
        kset = set(kandidat)

        # Lintasan 1: decode HANYA kandidat (grab melewati sisanya tanpa decode),
        # kumpulkan skor ketajaman + thumbnail untuk dedup.
        skor: dict[int, float] = {}
        thumb: dict[int, "np.ndarray"] = {}
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        i = 0
        while cap.grab():
            if i in kset:
                ok, frame = cap.retrieve()
                if ok and frame is not None:
                    skor[i], thumb[i] = _skor_buram(frame)
            i += 1
        if not skor:
            raise EkstrakTolak("tidak ada frame yang bisa diekstrak")

        # Ambang buram relatif terhadap video ini sendiri, dengan lantai mutlak.
        ambang = max(_BURAM_LANTAI, _BURAM_RASIO * float(np.median(list(skor.values()))))

        # Saring berurut: buang buram, lalu buang yang kembar dengan yang
        # terakhir disimpan. Dedup dipakai pada frame yang SUDAH lolos buram
        # supaya thumbnail acuannya memang frame yang akan ditulis.
        simpan: list[int] = []
        buang_buram = 0
        buang_dup = 0
        acuan = None
        for idx in sorted(skor):
            if skor[idx] < ambang:
                buang_buram += 1
                continue
            if acuan is not None and _duplikat(thumb[idx], acuan):
                buang_dup += 1
                continue
            simpan.append(idx)
            acuan = thumb[idx]

        # Kalau setelah disaring masih lebih banyak dari target, rampingkan
        # merata ke target (bukan memotong ekor) supaya sebarannya tetap rata.
        if len(simpan) > target:
            pilih = _indeks_kandidat(len(simpan), target)
            simpan = [simpan[k] for k in pilih]
        tulis_set = set(simpan)
        if not tulis_set:
            raise EkstrakTolak("semua frame terbuang sebagai buram/duplikat")

        # Lintasan 2: decode & tulis hanya frame terpilih.
        keluar.mkdir(parents=True, exist_ok=True)
        urut = {idx: k for k, idx in enumerate(simpan, start=1)}
        ditulis = 0
        nama: list[str] = []
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        i = 0
        while cap.grab():
            if i in tulis_set:
                ok, frame = cap.retrieve()
                if ok and frame is not None:
                    fn = _nama_bebas(keluar, prefix, urut[i], len(simpan))
                    if cv2.imwrite(str(keluar / fn),
                                   frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_MUTU]):
                        ditulis += 1
                        nama.append(fn)
            i += 1
    finally:
        cap.release()

    if not ditulis:
        raise EkstrakTolak("gagal menulis satu pun frame ke disk")
    log.info("ekstrak %r: %d frame (buram %d, dup %d) dari %.1f dtk @ %s",
             video.name, ditulis, buang_buram, buang_dup, durasi, preset)
    return {"ditulis": ditulis, "buram": buang_buram, "duplikat": buang_dup,
            "durasi": round(durasi, 2), "target": target, "preset": preset,
            "nama": nama}


def _nama_bebas(keluar: Path, prefix: str, nomor: int, total: int) -> str:
    """`<prefix>-NNN.jpg` dengan lebar angka mengikuti jumlah frame; kalau sudah
    ada, tambahkan akhiran sampai tak bentrok (video kedua ke projek yang sama)."""
    lebar = max(3, len(str(total)))
    dasar = f"{prefix}-{nomor:0{lebar}d}"
    fn = f"{dasar}.jpg"
    k = 2
    while (keluar / fn).exists():
        fn = f"{dasar}-{k}.jpg"
        k += 1
    return fn
