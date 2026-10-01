"""
Pemotongan video menjadi KLIP pendek, untuk projek berjenis video.

Projek video menyimpan videonya apa adanya di `_sumber/` (lihat
routers/uploads.py), lalu video itu dipotong menjadi klip-klip berdurasi tetap
yang menjadi satuan kerja pelabelan aksi — padanan "frame" pada projek image.
Keluarannya mendarat di `klip/<nama_batch>/<slug>_<srcid>_<tNNNN>.mp4`, dan
video sumbernya TIDAK dibuang (asetnya memang videonya; lihat rencana G#6).

Semua CPU-only lewat ffmpeg/ffprobe — TIDAK ada torch/ultralytics, dan modul
ini tidak mengimpor cv2 sama sekali: hanya subprocess. Satu video dipotong
dalam dua tahap supaya grafik ffmpeg yang mahal (minterpolate) cukup dijalankan
SEKALI, bukan sekali per klip:

  Tahap A (normalisasi): satu panggilan ffmpeg menghasilkan satu berkas
    sementara yang sudah slow-mo (opsional), ber-fps target, dan berukuran
    target dengan LETTERBOX (tak pernah crop — sama seperti buatversi/olah).
  Tahap B (potong): dari berkas sementara itu, tiap N detik dipotong jadi satu
    klip dengan jumlah frame dipaksa persis lewat -frames:v, lalu diverifikasi;
    klip yang durasinya/jumlah framenya meleset dipindahkan ke `_sumber/_bad/`
    (bisa dipulihkan, tak pernah dihapus — aturan repo).

JEBAKAN yang benar-benar ada di mesin ini: `ffmpeg` dari conda muncul lebih
dulu di PATH dan TIDAK punya libx264, sementara /usr/bin/ffmpeg punya. Karena
itu biner + encoder dipilih lewat detect_ffmpeg() (diport dari aug-balance-v4),
bukan dipercaya dari $PATH; kalau tak ada yang bisa dipakai, KlipTolak dilempar
dengan pesan jelas — bukan gagal senyap seperti script lama.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from ..config import KLIP, KLIP_BURUK, SUMBER_VIDEO, VIDEO_EXT
from ..security import safe_slug

log = logging.getLogger(__name__)


class KlipTolak(Exception):
    """Video/ffmpeg tak bisa dipakai, atau potongan tak menghasilkan klip.

    Padanan ekstraksi.EkstrakTolak untuk jalur klip: dilempar pada kegagalan
    yang pemanggilnya perlu laporkan ke pengguna dengan kalimat yang jelas.
    """


# ============================================================
# DETEKSI FFMPEG + ENCODER  (diport dari aug-balance-v4.detect_ffmpeg)
# ============================================================
# Dicari biner yang BENAR-BENAR punya encoder H.264. /usr/bin/ffmpeg didahulukan
# justru karena ffmpeg conda di PATH kerap tanpa libx264 — pangkal kegagalan
# senyap yang dulu melaporkan "+0 klip" tanpa menyebut sebabnya.
_ENCODER_PREFS = [
    ("libx264", ["-crf", "23"]),         # utama — sama dgn codec dataset (h264)
    ("libopenh264", ["-b:v", "2M"]),     # cadangan H.264
    ("mpeg4", ["-q:v", "3"]),            # cadangan terakhir
]

# Hasil deteksi disimpan sekali per proses: memanggil ffmpeg -encoders tiap
# potong itu mahal, dan binernya tak berubah selama server hidup.
_ff_cache: tuple[str, str, str, list[str]] | None = None


def detect_ffmpeg() -> tuple[str, str, str, list[str]]:
    """(ffmpeg, ffprobe, encoder, opsi_encoder) yang benar-benar tersedia.

    Melempar KlipTolak kalau tak ada biner ffmpeg dengan encoder H.264/MPEG-4
    yang bisa dipakai — jauh lebih baik daripada membiarkan tiap potong gagal
    senyap satu per satu.
    """
    global _ff_cache
    if _ff_cache is not None:
        return _ff_cache
    for binary in ("/usr/bin/ffmpeg", "ffmpeg", "/usr/local/bin/ffmpeg"):
        if shutil.which(binary) is None and not Path(binary).exists():
            continue
        try:
            r = subprocess.run([binary, "-hide_banner", "-encoders"],
                               capture_output=True, text=True, timeout=20)
        except Exception:                        # noqa: BLE001
            continue
        if r.returncode != 0:
            continue
        for enc, opts in _ENCODER_PREFS:
            # Baris encoder ffmpeg: "V..... libx264  H.264 ..." — huruf V di
            # kolom pertama = video encoder. Dicocokkan persis supaya "libx264"
            # tak salah tertangkap oleh "libx264rgb".
            if re.search(rf"^\s*V\S*\s+{re.escape(enc)}\s", r.stdout, re.M):
                ffprobe = (binary[:-6] + "ffprobe"
                           if binary.endswith("ffmpeg") else "ffprobe")
                # Kalau ffprobe sebelah biner itu tak ada, jatuh ke yang di PATH.
                if shutil.which(ffprobe) is None and not Path(ffprobe).exists():
                    ffprobe = "ffprobe"
                _ff_cache = (binary, ffprobe, enc, opts)
                log.info("ffmpeg dipilih: %s (encoder %s)", binary, enc)
                return _ff_cache
    raise KlipTolak(
        "tidak ada ffmpeg dengan encoder H.264/MPEG-4 yang bisa dipakai di "
        "server ini — pasang ffmpeg (butuh libx264/libopenh264/mpeg4)")


def _lupakan_deteksi() -> None:
    """Kosongkan cache deteksi — untuk pengujian yang memalsu PATH ffmpeg."""
    global _ff_cache
    _ff_cache = None


def siap_video() -> tuple[bool, str]:
    """Server ini bisa memotong klip video atau tidak, beserta alasannya.

    Cermin latih.siap_latih(): diperiksa SEBELUM orang memulai, supaya form
    ingest/potong bisa mengatakan apa adanya bahwa ffmpeg kurang — bukan
    membiarkan pekerjaan diluncurkan lalu jatuh senyap pada baris subprocess.

    Prasyaratnya: ffmpeg + ffprobe di server ini dengan encoder H.264/MPEG-4
    yang benar-benar ada (bukan sekadar `ffmpeg` di PATH yang mungkin tanpa
    libx264). ffprobe diuji terpisah karena durasi/jumlah frame dibaca darinya.
    """
    try:
        ffmpeg, ffprobe, enc, _ = detect_ffmpeg()
    except KlipTolak as e:
        return False, str(e)
    try:
        r = subprocess.run([ffprobe, "-version"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            raise OSError(r.stderr[:80])
    except Exception as e:                        # noqa: BLE001
        return False, f"ffprobe tidak bisa dijalankan ({str(e)[:80]})"
    return True, ""


# ============================================================
# RESEP INGEST  (bentuk dari rencana C.2)
# ============================================================
# Pilihan potong disimpan per batch, sama seperti `resep` pada versi dataset.
#   fps           : normalisasi/decode ke fps ini (0 = ikut fps sumber)
#   slowmo        : slow-motion minterpolate (Req 2)
#   potong        : potong tiap N detik dari `mulai` (Req 3). Tiap klip
#                   berdurasi `setiap_detik`; potongan ekor yang lebih pendek
#                   dari itu DIBUANG (hanya klip utuh), persis extract_clips.
#   ukuran        : [W, H] keluaran
#   letterbox     : True = kecilkan+bingkai hitam (tak pernah crop);
#                   False = regang paksa ke WxH
RESEP_BAWAAN: dict = {
    "fps": 25,
    "slowmo": {"aktif": False, "faktor": 2, "mode": "blend"},
    "potong": {"setiap_detik": 1.0, "mulai": 0.0},
    "ukuran": [224, 224],
    "letterbox": True,
}

_MODE_SLOWMO_SAH = ("blend", "dup", "mci")


def resep_sah(resep: dict | None) -> dict:
    """Bersihkan + lengkapi resep ingest ke bentuk kanonik yang aman dipakai.

    Nilai dari form tak dipercaya: dijepit ke rentang wajar supaya satu angka
    keliru tak membuat ffmpeg meledak (fps negatif, ukuran ganjil yang ditolak
    libx264, slowmo 1000×). Kekurangan medan diisi dari RESEP_BAWAAN.
    """
    r = resep if isinstance(resep, dict) else {}

    def _num(x, bawaan):
        try:
            return float(x)
        except (TypeError, ValueError):
            return float(bawaan)

    fps = int(_num(r.get("fps"), RESEP_BAWAAN["fps"]))
    fps = 0 if fps <= 0 else min(fps, 120)        # 0 = ikut sumber

    sm = r.get("slowmo") if isinstance(r.get("slowmo"), dict) else {}
    faktor = int(_num(sm.get("faktor"), 2))
    faktor = min(max(faktor, 1), 8)               # 1 = tak ada perlambatan
    mode = str(sm.get("mode") or "blend").strip().lower()
    slowmo = {"aktif": bool(sm.get("aktif")),
              "faktor": faktor,
              "mode": mode if mode in _MODE_SLOWMO_SAH else "blend"}

    pt = r.get("potong") if isinstance(r.get("potong"), dict) else {}
    setiap = _num(pt.get("setiap_detik"), RESEP_BAWAAN["potong"]["setiap_detik"])
    setiap = min(max(setiap, 0.1), 600.0)
    mulai = max(0.0, _num(pt.get("mulai"), 0.0))

    uk = r.get("ukuran") if isinstance(r.get("ukuran"), (list, tuple)) else []
    try:
        W, H = int(uk[0]), int(uk[1])
    except (IndexError, TypeError, ValueError):
        W, H = RESEP_BAWAAN["ukuran"]
    # libx264 (yuv420p) menuntut dimensi genap; dibulatkan naik ke genap di sini
    # supaya tak ditolak di tengah proses.
    W = max(16, W + (W % 2))
    H = max(16, H + (H % 2))

    return {"fps": fps, "slowmo": slowmo,
            "potong": {"setiap_detik": round(setiap, 4), "mulai": round(mulai, 4)},
            "ukuran": [W, H], "letterbox": bool(r.get("letterbox", True))}


# ============================================================
# PROBE  (diport dari cek-durasi-klip.probe)
# ============================================================

def probe(video: Path, ffprobe: str | None = None) -> dict:
    """Metadata video lewat ffprobe: {duration, frames, fps, w, h}. {} kalau gagal.

    Durasi stream didahulukan, jatuh ke durasi container; jumlah frame diambil
    dari nb_frames dan — kalau tak ada — diperkirakan dari durasi × fps.
    """
    if ffprobe is None:
        ffprobe = detect_ffmpeg()[1]
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0",
           "-show_entries",
           "stream=duration,nb_frames,avg_frame_rate,width,height,codec_name",
           "-show_entries", "format=duration", "-of", "json", str(video)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        data = json.loads(r.stdout or "{}")
    except Exception:                             # noqa: BLE001
        return {}
    streams = data.get("streams") or []
    if not streams:
        return {}
    s, fmt = streams[0], data.get("format") or {}

    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    def _i(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

    dur = _f(s.get("duration")) or _f(fmt.get("duration"))
    frames = _i(s.get("nb_frames"))
    fps = None
    afr = s.get("avg_frame_rate") or "0/0"
    if "/" in afr:
        num, den = afr.split("/")
        if _f(den):
            fps = _f(num) / _f(den)
    if frames is None and dur and fps:
        frames = round(dur * fps)
    return {"duration": dur, "frames": frames, "fps": fps,
            "w": _i(s.get("width")), "h": _i(s.get("height")),
            "codec": s.get("codec_name")}


# ============================================================
# POTONG
# ============================================================

# Toleransi verifikasi klip. Durasi: setengah jendela sebuah frame plus sedikit
# longgar untuk pembulatan container. Frame: ±2 untuk ketidaktepatan encoder.
_TOL_DURASI = 0.12
_TOL_FRAME = 2


def _vf_normalisasi(resep: dict, fps_pakai: int) -> str:
    """Rangkai filter tahap A: slow-mo (opsional) -> fps -> ukuran (letterbox)."""
    W, H = resep["ukuran"]
    parts: list[str] = []
    sm = resep["slowmo"]
    if sm["aktif"] and sm["faktor"] > 1:
        # minterpolate menaikkan fps ke faktor× lalu setpts meregang waktunya;
        # fps= di bawah mengembalikannya ke fps target (hasil akhir faktor×
        # lebih panjang pada fps target). Diport dari slowmo_split.py.
        parts.append(f"minterpolate=fps={fps_pakai * sm['faktor']}:"
                     f"mi_mode={sm['mode']}")
        parts.append(f"setpts={sm['faktor']}.0*PTS")
    parts.append(f"fps={fps_pakai}")
    if resep["letterbox"]:
        # Kecilkan agar muat lalu beri bingkai hitam — TIDAK ADA piksel dibuang
        # (bandingkan increase+crop yang sengaja tak dipakai).
        parts.append(f"scale={W}:{H}:force_original_aspect_ratio=decrease")
        parts.append(f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black")
    else:
        parts.append(f"scale={W}:{H}")
    return ",".join(parts)


def _nama_klip(keluar: Path, slug: str, srcid: str, mulai: float) -> Path:
    """`<slug>_<srcid>_t<desidetik>.mp4`, dijamin tak bentrok di folder batch.

    tNNNN memakai desidetik (sepersepuluh detik) dari waktu mulai klip supaya
    tetap unik untuk potongan di bawah 1 detik, dan tetap terbaca sebagai cap
    waktu. Kalau toh bentrok (dua video sumber ke batch yang sama), akhiran
    bertambah sampai bebas — sama caranya dengan ekstraksi._nama_bebas.
    """
    deci = int(round(mulai * 10))
    dasar = f"{slug}_{srcid}_t{deci:04d}"
    p = keluar / f"{dasar}.mp4"
    k = 2
    while p.exists():
        p = keluar / f"{dasar}-{k}.mp4"
        k += 1
    return p


def potong(video: Path, projek: Path, resep: dict | None = None, *,
           nama_batch: str = "batch", slug: str = "", srcid: str = "",
           maju: Callable[[dict], None] | None = None,
           batal: Callable[[], bool] | None = None) -> dict:
    """
    Potong satu `video` di `_sumber/` menjadi klip di `klip/<nama_batch>/`.

    `projek` adalah AKAR projek video; tata letaknya dihitung dari situ supaya
    satu tempat memegang di mana klip, karantina, dan sumber berada:
        klip         -> <projek>/klip/<nama_batch>/
        klip cacat   -> <projek>/_sumber/_bad/   (dipindah, tak pernah dihapus)
    Video sumbernya TIDAK disentuh (asetnya memang videonya; rencana G#6).

    `maju(dict)` dipanggil berkala untuk pelaporan kemajuan (cermin pola di
    ekstraksi.py / projek.catat_maju); `batal()` yang True menghentikan di
    tengah dan membuang keluaran setengah jadi. Mengembalikan ringkasan:
    berapa klip ditulis, berapa dikarantina, durasi/fps/jumlah frame sasaran.
    """
    video = Path(video)
    projek = Path(projek)
    resep = resep_sah(resep)
    ffmpeg, ffprobe, enc, opts = detect_ffmpeg()

    if not video.is_file():
        raise KlipTolak("video sumber tidak ada di _sumber/")
    if video.suffix.lower() not in VIDEO_EXT:
        raise KlipTolak("yang diminta bukan berkas video")

    info = probe(video, ffprobe)
    dur_sumber = info.get("duration")
    if not dur_sumber or dur_sumber <= 0:
        raise KlipTolak("video tidak terbaca atau durasinya nol")

    fps = resep["fps"] or int(round(info.get("fps") or 25)) or 25
    setiap = resep["potong"]["setiap_detik"]
    mulai = resep["potong"]["mulai"]
    faktor = resep["slowmo"]["faktor"] if resep["slowmo"]["aktif"] else 1
    W, H = resep["ukuran"]
    n_frame = int(round(fps * setiap))
    if n_frame < 1:
        raise KlipTolak("kombinasi fps dan durasi potong menghasilkan 0 frame")

    slug = safe_slug(slug or nama_batch) or "klip"
    srcid = safe_slug(srcid or video.stem) or "src"

    keluar = projek / KLIP / safe_slug(nama_batch or "batch")
    bad_dir = projek / SUMBER_VIDEO / KLIP_BURUK

    ditulis: list[str] = []
    dibuang = 0

    # Satu berkas sementara untuk hasil normalisasi (slow-mo + fps + ukuran),
    # di direktori sementara sistem supaya TIDAK meninggalkan apa pun di dalam
    # folder projek kalau prosesnya gagal/terhenti.
    with tempfile.TemporaryDirectory(prefix="labelapp-klip-") as tmp:
        antara = Path(tmp) / "antara.mp4"
        vf = _vf_normalisasi(resep, fps)
        cmd_a = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(video),
                 "-vf", vf, "-r", str(fps), "-c:v", enc, *opts,
                 "-pix_fmt", "yuv420p", "-an", "-y", str(antara)]
        if not _jalankan(cmd_a) or not antara.is_file():
            raise KlipTolak("normalisasi video gagal (ffmpeg/encoder)")

        info_antara = probe(antara, ffprobe)
        dur = info_antara.get("duration") or (dur_sumber * faktor)

        # Jumlah klip UTUH: potongan ekor yang lebih pendek dari `setiap` dibuang
        # (persis `while t + CLIP_DURATION <= duration` di extract_clips). eps
        # satu frame menolong kasus durasi yang meleset sepersekian frame.
        eps = 1.0 / fps
        n_klip = int((dur - mulai + eps) // setiap)
        n_klip = max(0, n_klip)
        if n_klip == 0:
            raise KlipTolak(
                f"video {dur:.2f}s terlalu pendek untuk satu klip {setiap:.2f}s")

        keluar.mkdir(parents=True, exist_ok=True)
        if maju:
            maju({"tahap": "potong", "n": 0, "total": n_klip, "persen": 0.0})

        for i in range(n_klip):
            if batal is not None and batal():
                shutil.rmtree(keluar, ignore_errors=True)
                raise KlipTolak("dihentikan; potongan setengah jadi dibuang")
            start = mulai + i * setiap
            dst = _nama_klip(keluar, slug, srcid, start)
            # -ss SESUDAH -i = penyarian keluaran (akurat per-frame), lalu
            # -frames:v memaksa jumlah frame persis. Berkas antara sudah ber-fps
            # & berukuran target, jadi tak perlu -vf lagi di sini.
            cmd_b = [ffmpeg, "-hide_banner", "-loglevel", "error",
                     "-i", str(antara), "-ss", f"{start:.4f}",
                     "-frames:v", str(n_frame), "-r", str(fps),
                     "-c:v", enc, *opts, "-pix_fmt", "yuv420p", "-an",
                     "-y", str(dst)]
            ok = _jalankan(cmd_b) and dst.is_file()
            if ok and _klip_sah(dst, setiap, n_frame, ffprobe):
                ditulis.append(dst.name)
            else:
                # Klip cacat dikarantina, tak dihapus: bisa jadi masih sebagian
                # berguna, dan aturan repo tak pernah menghapus media pengguna.
                if dst.is_file():
                    bad_dir.mkdir(parents=True, exist_ok=True)
                    dst.replace(bad_dir / dst.name)
                dibuang += 1
            if maju:
                maju({"tahap": "potong", "n": i + 1, "total": n_klip,
                      "persen": (i + 1) / n_klip})

    if not ditulis:
        # Semua klip cacat: biarkan yang terkarantina, tapi beri tahu pemanggil.
        raise KlipTolak("tak satu klip pun lolos verifikasi durasi/jumlah frame")

    log.info("potong %r: %d klip (buang %d) @ %dfps %dx%d, potong %.2fs x%d",
             video.name, len(ditulis), dibuang, fps, W, H, setiap, faktor)
    if maju:
        maju({"tahap": "selesai", "persen": 1.0})
    return {"ditulis": len(ditulis), "dikarantina": dibuang,
            "nama": ditulis, "batch": safe_slug(nama_batch or "batch"),
            "fps": fps, "ukuran": [W, H], "setiap_detik": setiap,
            "frame_per_klip": n_frame, "slowmo": faktor if faktor > 1 else 0,
            "folder": str(keluar)}


def _jalankan(cmd: list[str], timeout: int = 300) -> bool:
    """Jalankan ffmpeg; True kalau keluar 0. Senyap — pemanggil yang melapor."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode == 0
    except Exception:                             # noqa: BLE001
        return False


def _klip_sah(klip: Path, setiap: float, n_frame: int, ffprobe: str) -> bool:
    """Durasi & jumlah frame klip dalam toleransi (cermin cek-durasi-klip)."""
    info = probe(klip, ffprobe)
    dur = info.get("duration")
    if dur is None or abs(dur - setiap) > _TOL_DURASI:
        return False
    frames = info.get("frames")
    # Jumlah frame boleh tak terbaca (sebagian container tak menulis nb_frames);
    # durasi yang sudah lolos sudah cukup menjadi penjaga utama.
    if frames is not None and abs(frames - n_frame) > _TOL_FRAME:
        return False
    return True
