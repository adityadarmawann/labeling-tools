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

Decode-nya lewat FFMPEG, bukan OpenCV. Video ponsel masa kini banyak yang HDR
(HLG/PQ, 10-bit); OpenCV mengurai HDR TANPA tone-mapping sehingga gambarnya
tampak terlalu terang/pudar dan tak sesuai aslinya, sementara ffmpeg (dengan
zscale+tonemap) menurunkannya ke SDR bt709 dengan benar. ffmpeg juga menjaga
resolusi penuh dan menulis JPG mutu tinggi (-q:v 4). OpenCV tetap dipakai
sebagai cadangan kalau ffmpeg tak tersedia di server.

Skor buram (varian Laplacian) dan deduplikasi (selisih thumbnail abu-abu)
berjalan SETELAH frame diekstrak, pada berkas hasil yang warnanya sudah benar.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
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

# Sampling berbasis FPS (gaya Roboflow): user memilih berapa frame per detik,
# jumlah target = durasi * fps, lalu disebar MERATA (_indeks_kandidat) sehingga
# jarak antar-frame tetap = benar-benar fps. Menang atas preset/kerapatan lama.
# Dengan FPS user yang mengatur kerapatan, batas atas cuma jaring pengaman
# (bukan lagi pembatas desain); buang buram/kembar tetap merapikan hasilnya.
FPS_BAWAAN = 2.0

# Batas aman jumlah frame per video. MIN menjaga klip sangat pendek tetap
# memberi sesuatu; MAKS jaring pengaman supaya fps tinggi x video sangat panjang
# tak meledak tak terbatas (buram/kembar biasanya menahannya jauh di bawah ini).
MIN_FRAME = 3
MAKS_FRAME = 5000

# Kandidat diambil lebih banyak dari target (oversample) supaya masih ada sisa
# untuk dibuang saat menyaring buram/duplikat tanpa jatuh jauh di bawah target.
_OVERS = 1.6

# Ambang buram: sebuah frame dibuang kalau varian Laplacian-nya di bawah
# BURAM_RASIO * median seluruh kandidat (buram RELATIF terhadap video itu
# sendiri — ambang mutlak tak berlaku lintas resolusi/konten), tapi tak pernah
# lebih longgar dari BURAM_LANTAI (menangkap frame rata/blank yang nyaris nol).
#
# RASIO sengaja RENDAH (0.15, bukan 0.35): untuk rekaman gerak cepat (mis.
# basket) tiap frame bisa sedikit motion-blur, dan membuang yang "agak buram"
# ikut membuang momen aksi yang justru mau dilabeli (pemain melompat, bola di
# udara). Dengan 0.15, hanya frame yang BENAR-BENAR rusak — smear ekstrem /
# blank (< 15% ketajaman median, atau di bawah lantai) — yang dibuang; frame
# gerak-cepat biasa tetap disimpan.
_BURAM_RASIO = 0.15
_BURAM_LANTAI = 8.0

# Deduplikasi: dua frame dianggap kembar kalau rata-rata selisih absolut
# thumbnail abu-abu THUMB x THUMB-nya (skala 0..255) di bawah DUP_AMBANG.
_THUMB = 16
_DUP_AMBANG = 3.0

_JPEG_MUTU = 95            # cadangan OpenCV (cv2.imwrite)
# Mutu mjpeg ffmpeg: 1=terbaik..31=terburuk. 4 (~JPEG q85) dipilih sengaja:
# tajam penuh untuk melabeli, tapi ukurannya terjaga -- 1080p ~0,5 MB, 4K ~2 MB
# (kasus berisik; rekaman nyata lebih kecil), jadi 4K tak menembus 3 MB seperti
# q:v 2. "Pecah" yang dulu dari decode HDR salah, bukan dari angka ini.
_FF_QSCALE = "4"
_FPS_BAWAAN = 25.0

# color_transfer HDR SEJATI (fungsi transfer PQ/HLG) -> HARUS di-tone-map ke
# SDR bt709, kalau tidak frame-nya pudar/terlalu terang. Hanya dua ini; sengaja
# TIDAK memasukkan bt2020-10/12 (itu penanda gamut lebar, kerap pada video SDR),
# supaya video SDR SAMA SEKALI tak disentuh kecerahannya -- diekstrak apa adanya.
_HDR_TRC = {"smpte2084", "arib-std-b67", "smptest2084", "arib_std_b67"}

# Rantai tone-map HDR->SDR (butuh zscale = libzimg). Linearkan, petakan rentang
# dinamis dengan Hable, lalu kembalikan ke bt709 rentang tv dan yuv420p 8-bit.
_TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,"
            "tonemap=tonemap=hable:desat=0,"
            "zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p")


class EkstrakTolak(Exception):
    """Video tak bisa dibaca atau tak menghasilkan satu frame pun."""


def tmp_dir(keluar, prefix) -> Path:
    """Folder sementara tempat ffmpeg menulis frame SEBELUM dipindahkan ke
    folder akhir. Dibuat publik supaya pemantau status bisa menghitung frame
    yang sedang ditulis (progress) dan membersihkannya kalau ekstraksi terputus."""
    from ..security import safe_slug as _slug
    return Path(keluar) / ("._ekstrak_" + (_slug(prefix) or "frame"))


def sah_preset(preset: str) -> str:
    p = str(preset or "").strip().lower()
    return p if p in PRESET_SAH else PRESET_BAWAAN


def target_frame(durasi: float, preset: str = PRESET_BAWAAN,
                 kerapatan: float | None = None, fps: float | None = None) -> int:
    """Jumlah frame yang disasar untuk sebuah durasi (detik).

    Urutan prioritas (yang pertama terisi menang):
      1. `fps`  : frame per detik (gaya Roboflow). target = durasi * fps.
      2. `kerapatan` : FAKTOR pengali kontinu relatif preset "sedang" (slider lama).
      3. `preset`    : "jarang"/"sedang"/"rapat" (kurva sublinear lama).
    Semuanya dijepit ke [MIN_FRAME, MAKS_FRAME]. Buang buram/kembar berjalan
    SETELAH ini, jadi hasil akhir bisa sedikit lebih kecil dari target."""
    durasi = max(0.0, float(durasi))
    if durasi <= 0:
        n = 0
    elif fps and fps > 0:
        n = round(durasi * float(fps))
    elif kerapatan and kerapatan > 0:
        n = round(_KOEF["sedang"] * float(kerapatan) * (durasi ** _PANGKAT))
    else:
        n = round(_KOEF[sah_preset(preset)] * (durasi ** _PANGKAT))
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


def _ffbin() -> tuple[str, str] | None:
    """(ffmpeg, ffprobe) yang benar-benar punya encoder video, atau None kalau
    tak ada. Memakai deteksi yang sama dengan pemotong klip (/usr/bin/ffmpeg
    didahulukan — itu yang punya libx264 DAN zscale di server ini)."""
    try:
        from .klip import detect_ffmpeg
        ff, probe, _, _ = detect_ffmpeg()
        return ff, probe
    except Exception:                                 # noqa: BLE001
        return None


_zscale_cache: dict[str, bool] = {}


def _punya_zscale(ff: str) -> bool:
    """Apakah biner ffmpeg ini punya filter zscale (libzimg) untuk tone-map HDR."""
    if ff in _zscale_cache:
        return _zscale_cache[ff]
    ada = False
    try:
        r = subprocess.run([ff, "-hide_banner", "-filters"],
                           capture_output=True, text=True, timeout=15)
        ada = bool(re.search(r"^\s*\S*\s+zscale\s", r.stdout, re.M))
    except Exception:                                 # noqa: BLE001
        ada = False
    _zscale_cache[ff] = ada
    return ada


def _probe(probe: str, video: Path) -> dict:
    """Metadata video lewat ffprobe: {w, h, durasi, trc, pix_fmt}. {} kalau gagal."""
    cmd = [probe, "-v", "error", "-select_streams", "v:0", "-of", "json",
           "-show_entries",
           "stream=width,height,color_transfer,pix_fmt,duration:format=duration",
           str(video)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return {}
        import json
        d = json.loads(r.stdout or "{}")
    except Exception:                                 # noqa: BLE001
        return {}
    st = (d.get("streams") or [{}])[0]
    durasi = 0.0
    for sumber in (st.get("duration"), (d.get("format") or {}).get("duration")):
        try:
            durasi = float(sumber)
            if durasi > 0:
                break
        except (TypeError, ValueError):
            continue
    return {"w": int(st.get("width") or 0), "h": int(st.get("height") or 0),
            "durasi": max(0.0, durasi),
            "trc": str(st.get("color_transfer") or "").strip().lower(),
            "pix_fmt": str(st.get("pix_fmt") or "").strip().lower()}


def _skor_berkas(p: Path) -> tuple[float, "np.ndarray"] | None:
    """(ketajaman, thumbnail) satu berkas gambar hasil ekstrak, atau None kalau
    tak terbaca."""
    im = cv2.imread(str(p))
    if im is None:
        return None
    return _skor_buram(im)


def _ekstrak_ffmpeg(video: Path, keluar: Path, ff: str,
                    target: int, durasi: float, prefix: str,
                    dedup: bool, hdr: bool) -> dict:
    """Ekstrak lewat ffmpeg: decode benar (tone-map HDR), resolusi penuh, JPG
    mutu tinggi. Lalu buang buram (+ opsional kembar) pada berkas hasil."""
    fps_out = max(target / durasi, 1e-3)
    # Rantai filter: sampling fps dulu (lebih murah), lalu tone-map/format.
    bagian = [f"fps={fps_out:.6f}"]
    if hdr and _punya_zscale(ff):
        bagian.append(_TONEMAP)
    else:
        # SDR, atau HDR tanpa zscale: tetap paksa 8-bit yuv420p supaya mjpeg
        # tak menolak masukan 10-bit. (HDR tanpa zscale: ffmpeg polos masih
        # jauh lebih tepat daripada OpenCV, hanya tanpa tone-map penuh.)
        bagian.append("format=yuv420p")
    vf = ",".join(bagian)

    tmp = tmp_dir(keluar, prefix)
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-i", str(video), "-map", "0:v:0", "-vf", vf,
               "-q:v", _FF_QSCALE, "-an", "-sn",
               str(tmp / "f-%06d.jpg"), "-y"]
        tmo = max(600, int(durasi * 4) + 120)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=tmo)
        frames = sorted(tmp.glob("f-*.jpg"))
        if r.returncode != 0 or not frames:
            raise EkstrakTolak(
                "ffmpeg tak menghasilkan frame: "
                + (r.stderr or "").strip()[-160:])

        # Skor ketajaman + thumbnail, pada berkas hasil (warna sudah benar).
        skor: list[tuple[Path, float, "np.ndarray"]] = []
        for p in frames:
            s = _skor_berkas(p)
            if s is not None:
                skor.append((p, s[0], s[1]))
        if not skor:
            raise EkstrakTolak("tidak ada frame yang bisa dibaca kembali")

        ambang = max(_BURAM_LANTAI,
                     _BURAM_RASIO * float(np.median([s for _, s, _ in skor])))
        simpan: list[Path] = []
        buang_buram = buang_dup = 0
        acuan = None
        for p, s, th in skor:
            if s < ambang:
                buang_buram += 1
                continue
            if dedup and acuan is not None and _duplikat(th, acuan):
                buang_dup += 1
                continue
            simpan.append(p)
            acuan = th

        # Kalau masih di atas target, rampingkan merata (bukan potong ekor).
        if len(simpan) > target:
            pilih = _indeks_kandidat(len(simpan), target)
            simpan = [simpan[k] for k in pilih]
        if not simpan:
            raise EkstrakTolak("semua frame terbuang sebagai buram/duplikat")

        keluar.mkdir(parents=True, exist_ok=True)
        nama: list[str] = []
        for k, p in enumerate(simpan, start=1):
            fn = _nama_bebas(keluar, prefix, k, len(simpan))
            shutil.move(str(p), str(keluar / fn))
            nama.append(fn)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    log.info("ekstrak(ffmpeg) %r: %d frame (buram %d, dup %d) dari %.1f dtk, "
             "fps=%.3f, hdr=%s", video.name, len(nama), buang_buram, buang_dup,
             durasi, fps_out, hdr)
    return {"ditulis": len(nama), "buram": buang_buram, "duplikat": buang_dup,
            "durasi": round(durasi, 2), "target": target, "preset": "fps",
            "nama": nama}


def ekstrak(video: Path, keluar: Path, preset: str = PRESET_BAWAAN,
            prefix: str = "", kerapatan: float | None = None,
            fps: float | None = None, dedup: bool = False) -> dict:
    """
    Potong `video` menjadi gambar JPG di folder `keluar`.

    Memakai ffmpeg (decode benar untuk HDR, resolusi penuh, JPG mutu tinggi);
    kalau ffmpeg tak ada atau gagal, jatuh ke jalur OpenCV. Mengembalikan
    ringkasan: berapa ditulis, berapa dibuang buram/duplikat, durasi & target.
    Tidak menyentuh berkas videonya — penghapusan diserahkan ke pemanggil.
    """
    video = Path(video)
    keluar = Path(keluar)
    preset = sah_preset(preset)
    prefix = safe_slug(prefix or video.stem) or "frame"

    bin_ff = _ffbin()
    if bin_ff is not None:
        ff, probe = bin_ff
        meta = _probe(probe, video)
        durasi = meta.get("durasi", 0.0)
        if durasi > 0:
            target = target_frame(durasi, preset, kerapatan, fps)
            hdr = meta.get("trc", "") in _HDR_TRC
            try:
                return _ekstrak_ffmpeg(video, keluar, ff, target,
                                       durasi, prefix, dedup, hdr)
            except EkstrakTolak:
                raise
            except Exception as e:                    # noqa: BLE001
                # ffmpeg bermasalah tak terduga: jangan gagalkan total, coba
                # jalur OpenCV supaya tetap ada hasil.
                log.warning("ekstrak ffmpeg gagal (%s), coba OpenCV: %s",
                            video.name, e)
        else:
            log.info("ffprobe tak memberi durasi untuk %s, pakai OpenCV",
                     video.name)
    return _ekstrak_cv(video, keluar, preset, prefix, kerapatan, fps, dedup)


def _ekstrak_cv(video: Path, keluar: Path, preset: str = PRESET_BAWAAN,
                prefix: str = "", kerapatan: float | None = None,
                fps: float | None = None, dedup: bool = False) -> dict:
    """
    CADANGAN OpenCV: potong `video` menjadi gambar JPG di folder `keluar`.

    Dipakai hanya kalau ffmpeg tak tersedia. TIDAK melakukan tone-mapping HDR,
    jadi video HDR hasilnya bisa tampak terlalu terang; itu sebabnya jalur
    ffmpeg (ekstrak()) didahulukan.

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
        F, _fps_vid, durasi = _durasi(cap)   # fps VIDEO (bukan fps sampling param)
        if F <= 0:
            raise EkstrakTolak("video tidak berisi frame yang bisa dibaca")
        target = target_frame(durasi, preset, kerapatan, fps)
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

        # Saring berurut: buang buram selalu; buang kembar HANYA kalau dedup=True.
        # Dedup MATI secara bawaan: untuk rekaman beraksi (olahraga) objek selalu
        # bergerak, dan dedup rata-rata-global justru membuang momen berbeda yang
        # bola/pemainnya kecil di frame. Dengan fps user yang menentukan kerapatan,
        # mempertahankan tiap frame terpilih lebih sesuai keinginan.
        simpan: list[int] = []
        buang_buram = 0
        buang_dup = 0
        acuan = None
        for idx in sorted(skor):
            if skor[idx] < ambang:
                buang_buram += 1
                continue
            if dedup and acuan is not None and _duplikat(thumb[idx], acuan):
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
