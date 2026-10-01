"""
Penyaring KLIP berdasar kehadiran OBJEK sasaran (Langkah 5, rencana C.3/G#5).

Dipanggil SETELAH klip terkumpul (klip.potong) tetapi SEBELUM pembagian tugas:
sebuah dialog menanyakan "saring klip supaya hanya yang memuat <objek> yang
disimpan?". Klip yang tak memuat objek bukan data pelabelan aksi yang berguna,
jadi dibuang — TAPI dipindah, bukan dihapus (aturan repo tak pernah menghapus
media pengguna): ke klip/_ditolak/<batch>/, yang sudah dilewati klip_scan.pindai
sehingga tak terbaca lagi sebagai kerja, namun tetap bisa dipulihkan.

Diport dari action-labeler/persiapan-dataset/filter_clips_8class.py, dengan satu
perubahan desain yang disengaja: di prototipe "ada objek" berarti HARD-CODE
kelas 0 = bola. Di sini objek sasaran DAPAT DIKONFIGURASI (rencana G#5) — nama
kelas diterjemahkan ke indeks dari `names` milik modelnya sendiri, karena kelas
0 bukan "bola" di semua model (best-object-basketball.pt: ball/player/hoop/
referee/backboard; lihat posec3d/action-v14/konfigurasi.py).

DUA METODE
----------
  "warna"  : BEBAS MODEL, CPU, jalur yang diuji di CI. Sampling tiap `setiap`
             frame; masker HSV untuk rentang warna (bawaan oranye bola) +
             pemeriksaan bentuk/ukuran (area + kebundaran, seperti prototipe).
             Klip disimpan bila >= `min_frame` frame tersampel memuat objek.
             Cadangan terbaik untuk objek berwarna khas & bulat (bola);
             kalau objeknya bukan itu, pakai metode "yolo".
  "yolo"   : ultralytics (lazy import; HANYA ada di .venv-gpu). Model di-cache
             per-path di tingkat modul. Sampling tiap `setiap` frame; klip
             disimpan bila >= `min_frame` frame memuat SALAH SATU indeks kelas
             sasaran pada conf >= ambang. Modelnya dicari lewat direktori bobot
             latih (jadi pengguna cukup menaruh best-object-basketball.pt di
             sana seperti bobot lain); kalau lib/model tak ada, FilterTolak
             dilempar dengan pesan jelas dan siap_filter() melaporkannya jujur.

cv2 memang tersedia di .venv (dipakai juga oleh ekstraksi.py/autolabel.py), jadi
jalur "warna" bisa diuji penuh tanpa GPU. ultralytics sengaja lazy-import di
dalam fungsi supaya seluruh app tetap bisa diimpor bersih di venv CPU.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from ..config import KLIP, KLIP_DITOLAK
from ..security import safe_slug
from . import klip_scan, latih

log = logging.getLogger(__name__)

BERKAS_LAPORAN = ".filter.json"
VERSI = 1


class FilterTolak(Exception):
    """Penyaringan tak bisa dijalankan (lib/model objek tak ada, dihentikan).

    Padanan klip.KlipTolak untuk jalur filter: dilempar pada kegagalan yang
    pemanggilnya perlu laporkan ke pengguna dengan kalimat yang jelas, bukan
    gagal senyap di tengah seperti script lama.
    """


# ============================================================
# KONFIG FILTER  (rencana C.3/G#5 — objek sasaran DAPAT DIKONFIGURASI)
# ============================================================
# metode     "warna" | "yolo"
# model      nama berkas .pt (dicari di direktori bobot) atau path — "yolo" saja
# kelas      daftar nama ATAU indeks kelas yang dihitung sebagai "objek ada".
#            Untuk "yolo" nama diterjemahkan ke indeks dari model.names; kosong
#            = indeks 0 (bawaan prototipe: bola). Untuk "warna" tak dipakai
#            selain sebagai label hitungan per-kelas.
# conf       ambang confidence YOLO
# min_frame  minimal frame tersampel yang harus memuat objek agar klip disimpan
# setiap     sampling tiap N frame (hemat; prototipe memakai 4)
# hsv        rentang & bentuk untuk metode warna (bawah/atas HSV, min_area,
#            circularity). Mengubah ini MEMBALIK klip mana yang lolos — bukti
#            sasaran tak di-hardcode.
HSV_BAWAAN: dict = {
    "bawah": [5, 100, 100],      # rona rendah oranye (H,S,V)
    "atas": [25, 255, 255],      # rona tinggi oranye
    "min_area": 150,             # ~13x13 px; buang derau kecil
    "circularity": 0.5,          # 1.0 = lingkaran sempurna
}

KONFIG_BAWAAN: dict = {
    "metode": "warna",
    "model": "best-object-basketball.pt",
    "kelas": [],
    "conf": 0.25,
    "min_frame": 2,
    "setiap": 4,
    "hsv": HSV_BAWAAN,
}

_METODE_SAH = ("warna", "yolo")


def _num(x, bawaan):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float(bawaan)


def _jepit(x, lo, hi, bawaan):
    return min(max(_num(x, bawaan), lo), hi)


def _hsv_sah(hsv: dict | None) -> dict:
    """Bersihkan rentang HSV + ambang bentuk ke bentuk kanonik yang aman.

    Nilai dari form tak dipercaya: H/S/V dijepit 0..255 (cv2 memakai H 0..179,
    tapi menjepit ke 255 tak berbahaya — inRange tetap benar), area & kebundaran
    dijepit ke rentang wajar supaya satu angka keliru tak meledakkan cv2.
    """
    h = hsv if isinstance(hsv, dict) else {}

    def _triplet(v, bawaan):
        try:
            a, b, c = v
            return [int(_jepit(a, 0, 255, bawaan[0])),
                    int(_jepit(b, 0, 255, bawaan[1])),
                    int(_jepit(c, 0, 255, bawaan[2]))]
        except (TypeError, ValueError):
            return list(bawaan)

    return {
        "bawah": _triplet(h.get("bawah"), HSV_BAWAAN["bawah"]),
        "atas": _triplet(h.get("atas"), HSV_BAWAAN["atas"]),
        "min_area": int(_jepit(h.get("min_area"), 1, 10_000_000,
                               HSV_BAWAAN["min_area"])),
        "circularity": round(_jepit(h.get("circularity"), 0.0, 1.0,
                                    HSV_BAWAAN["circularity"]), 4),
    }


def konfig_sah(konfig: dict | None) -> dict:
    """Lengkapi + jepit konfig filter ke bentuk kanonik (cermin klip.resep_sah)."""
    k = konfig if isinstance(konfig, dict) else {}

    metode = str(k.get("metode") or KONFIG_BAWAAN["metode"]).strip().lower()
    if metode not in _METODE_SAH:
        metode = "warna"

    kelas_mentah = k.get("kelas")
    kelas: list = []
    if isinstance(kelas_mentah, (list, tuple)):
        for x in kelas_mentah:
            if isinstance(x, bool):               # bool lolos int; tolak tegas
                continue
            if isinstance(x, int):
                kelas.append(x)
            else:
                s = str(x).strip()
                if s:
                    # Angka dalam string = indeks; selain itu nama kelas.
                    kelas.append(int(s) if s.lstrip("-").isdigit() else s)

    return {
        "metode": metode,
        "model": str(k.get("model") or KONFIG_BAWAAN["model"]).strip(),
        "kelas": kelas,
        "conf": round(_jepit(k.get("conf"), 0.0, 1.0, KONFIG_BAWAAN["conf"]), 4),
        "min_frame": int(_jepit(k.get("min_frame"), 1, 10_000,
                                KONFIG_BAWAAN["min_frame"])),
        "setiap": int(_jepit(k.get("setiap"), 1, 1000, KONFIG_BAWAAN["setiap"])),
        "hsv": _hsv_sah(k.get("hsv")),
    }


# ============================================================
# LOKASI MODEL OBJEK  (lewat direktori bobot latih — rencana G#5)
# ============================================================

def _cari_model(model: str) -> Path | None:
    """Temukan berkas .pt objek: path langsung dulu, lalu di direktori bobot.

    best-object-basketball.pt (22 MB) SENGAJA tak di-commit ke git; pengguna
    menaruhnya di direktori bobot (latih.dir_bobot) seperti yolo26n-seg.pt.
    Mengembalikan None kalau tak ketemu — pemanggil menurunkannya jadi pesan
    "model tidak ditemukan" yang jelas, bukan menebak path.
    """
    if not model:
        return None
    p = Path(model).expanduser()
    if p.is_file():
        return p
    nama = Path(model).name
    for d in latih.dir_bobot():
        kand = d / nama
        if kand.is_file():
            return kand
    return None


# Cache model di tingkat modul, dikunci per-path: memuat YOLO itu mahal, dan
# satu sesi penyaringan ratusan klip tak boleh memuat ulang tiap klip.
_cache_model: dict[str, object] = {}


def _muat_yolo(path: Path):
    """Muat (sekali) model YOLO. ultralytics lazy-import — TAK ADA di .venv CPU."""
    key = str(path)
    m = _cache_model.get(key)
    if m is None:
        from ultralytics import YOLO       # lazy: hanya .venv-gpu
        log.info("memuat model objek untuk filter: %s", key)
        m = YOLO(key)
        _cache_model[key] = m
    return m


def _lupakan_model() -> None:
    """Kosongkan cache model — untuk pengujian."""
    _cache_model.clear()


def _kelas_indeks(model, kelas: list) -> tuple[list[int], dict]:
    """Terjemahkan daftar nama/indeks ke indeks kelas model (+ peta idx->nama).

    Nama dicocokkan dari model.names milik modelnya SENDIRI (bukan asumsi kelas
    0 = bola). Kosong -> [0] (bawaan prototipe). Indeks/nama tak dikenal
    diabaikan; kalau toh tak ada yang cocok, pemanggil melempar FilterTolak.
    """
    names = getattr(model, "names", {}) or {}
    if isinstance(names, (list, tuple)):
        peta_idx = {i: str(n) for i, n in enumerate(names)}
    else:
        peta_idx = {int(i): str(n) for i, n in names.items()}
    nama_ke_idx = {n.lower(): i for i, n in peta_idx.items()}

    if not kelas:
        return ([0] if 0 in peta_idx else sorted(peta_idx)[:1]), peta_idx

    out: list[int] = []
    for x in kelas:
        if isinstance(x, int):
            if x in peta_idx and x not in out:
                out.append(x)
        else:
            i = nama_ke_idx.get(str(x).strip().lower())
            if i is not None and i not in out:
                out.append(i)
    return out, peta_idx


# ============================================================
# DETEKSI OBJEK PER FRAME
# ============================================================

def _ada_warna(frame, bawah, atas, min_area: int, circularity: float) -> bool:
    """Ada objek berwarna & cukup bundar di frame? (port detect_ball_color).

    Masker HSV -> buka/tutup morfologi (buang derau) -> contour; sebuah contour
    dianggap objek bila luasnya >= min_area DAN kebundarannya > ambang. Ambang
    & rentang warna datang dari konfig, jadi "oranye bola" cuma bawaan.
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, bawah, atas)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue
        perimeter = cv2.arcLength(cnt, True)
        if perimeter == 0:
            continue
        bundar = 4 * np.pi * area / (perimeter ** 2)
        if bundar > circularity:
            return True
    return False


def _nama_target_warna(konfig: dict) -> str:
    """Label hitungan per-kelas untuk metode warna (nama kelas pertama / 'warna')."""
    for x in konfig["kelas"]:
        if isinstance(x, str) and x.strip():
            return x.strip()
    return "warna"


def _saring_klip_warna(klip_path: Path, konfig: dict) -> tuple[bool, dict]:
    """Putuskan satu klip via metode warna. (keep, info)."""
    cap = cv2.VideoCapture(str(klip_path))
    if not cap.isOpened():
        return False, {"metode": "warna", "alasan": "tak_bisa_buka",
                       "frame_cek": 0, "frame_objek": 0, "kelas_terlihat": []}
    bawah = np.array(konfig["hsv"]["bawah"])
    atas = np.array(konfig["hsv"]["atas"])
    min_area = konfig["hsv"]["min_area"]
    circ = konfig["hsv"]["circularity"]
    setiap = konfig["setiap"]
    frame_objek = frame_cek = idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if idx % setiap == 0:
            if _ada_warna(frame, bawah, atas, min_area, circ):
                frame_objek += 1
            frame_cek += 1
        idx += 1
    cap.release()
    keep = frame_objek >= konfig["min_frame"]
    nm = _nama_target_warna(konfig)
    return keep, {"metode": "warna", "frame_cek": frame_cek,
                  "frame_objek": frame_objek,
                  "kelas_terlihat": [nm] if frame_objek > 0 else []}


def _saring_klip_yolo(klip_path: Path, konfig: dict, model,
                      kelas_idx: list[int], peta_idx: dict) -> tuple[bool, dict]:
    """Putuskan satu klip via YOLO. (keep, info). Hanya .venv-gpu."""
    cap = cv2.VideoCapture(str(klip_path))
    if not cap.isOpened():
        return False, {"metode": "yolo", "alasan": "tak_bisa_buka",
                       "frame_cek": 0, "frame_objek": 0, "kelas_terlihat": []}
    target = set(kelas_idx)
    conf = konfig["conf"]
    setiap = konfig["setiap"]
    frame_objek = frame_cek = idx = 0
    terlihat: set[int] = set()
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if idx % setiap == 0:
            ada = False
            for r in model(frame, conf=conf, verbose=False):
                for c in r.boxes.cls:
                    ci = int(c)
                    if ci in target:
                        ada = True
                        terlihat.add(ci)
            if ada:
                frame_objek += 1
            frame_cek += 1
        idx += 1
    cap.release()
    keep = frame_objek >= konfig["min_frame"]
    return keep, {"metode": "yolo", "frame_cek": frame_cek,
                  "frame_objek": frame_objek,
                  "kelas_terlihat": [peta_idx.get(i, str(i))
                                     for i in sorted(terlihat)]}


# ============================================================
# KESIAPAN
# ============================================================

def siap_filter(metode: str, model: str | None = None) -> tuple[bool, str]:
    """Bisakah metode ini dijalankan di server ini? (bisa, alasan).

    Cermin latih.siap_latih()/klip.siap_video(): diperiksa SEBELUM orang mulai,
    supaya form bisa mengatakan apa adanya. "warna" hanya butuh cv2 (ada di
    .venv). "yolo" butuh ultralytics (HANYA .venv-gpu) DAN berkas model objek
    yang benar-benar ada di direktori bobot — keduanya dilaporkan terpisah.
    """
    metode = (metode or "").strip().lower()
    if metode == "warna":
        try:
            import cv2 as _cv2  # noqa: F401
        except Exception as e:                    # noqa: BLE001
            return False, f"OpenCV (cv2) tak bisa diimpor ({str(e)[:80]})"
        return True, ""

    if metode == "yolo":
        import importlib.util
        if importlib.util.find_spec("ultralytics") is None:
            return False, (
                "Metode YOLO butuh ultralytics yang hanya ada di .venv-gpu — "
                "nyalakan server dengan LABELAPP_OLAH=gpu, atau pakai metode "
                "warna (bebas model, jalan di CPU).")
        if _cari_model(model or KONFIG_BAWAAN["model"]) is None:
            return False, (
                f"Model objek '{model or KONFIG_BAWAAN['model']}' tak ada di "
                "direktori bobot — taruh berkas .pt-nya di sana (mis. "
                "best-object-basketball.pt) atau pakai metode warna.")
        return True, ""

    return False, f"metode filter tak dikenal: {metode!r}"


# ============================================================
# PROSES  (dipakai bersama oleh pratinjau & terapkan)
# ============================================================

def _klip_batch(projek_dir: Path, batch_slug: str) -> list[dict]:
    """Item klip milik satu batch, dari klip_scan.pindai (yang sudah melewati
    klip/_ditolak & klip/_bad). Difilter ke prefix klip/<batch>/ supaya klip
    yang sudah pernah ditolak TIDAK ikut tersaring lagi."""
    prefix = f"{KLIP}/{batch_slug}/"
    pindai = klip_scan.pindai(projek_dir)
    return [it for it in pindai["items"] if it["rel"].startswith(prefix)]


def _proses(projek_dir: Path, batch: str, konfig: dict, *,
            maju: Callable[[dict], None] | None,
            batal: Callable[[], bool] | None) -> dict:
    """Jalankan keputusan keep/tolak untuk tiap klip batch (TAK memindah apa pun).

    Dipakai oleh pratinjau (dry-run) dan terapkan (yang kemudian memindah).
    Satu tempat menjalankan model/warna supaya dry-run dan apply menghasilkan
    keputusan yang persis sama.
    """
    projek_dir = Path(projek_dir)
    konfig = konfig_sah(konfig)
    batch_slug = safe_slug(batch) or "batch"
    items = _klip_batch(projek_dir, batch_slug)

    model = kelas_idx = peta_idx = None
    if konfig["metode"] == "yolo":
        model, kelas_idx, peta_idx = _siapkan_yolo(konfig)

    per_klip: dict[str, dict] = {}
    per_kelas: dict[str, int] = {}
    lolos = 0
    total = len(items)
    if maju:
        maju({"tahap": "saring", "n": 0, "total": total, "persen": 0.0})

    for i, it in enumerate(items):
        if batal is not None and batal():
            raise FilterTolak("penyaringan dihentikan")
        if konfig["metode"] == "yolo":
            keep, info = _saring_klip_yolo(it["klip"], konfig, model,
                                           kelas_idx, peta_idx)
        else:
            keep, info = _saring_klip_warna(it["klip"], konfig)
        per_klip[it["rel"]] = {"keep": keep, **info}
        for nm in info.get("kelas_terlihat", []):
            per_kelas[nm] = per_kelas.get(nm, 0) + 1
        if keep:
            lolos += 1
        if maju:
            maju({"tahap": "saring", "n": i + 1, "total": total,
                  "persen": (i + 1) / total if total else 1.0})

    return {"items": items, "per_klip": per_klip, "per_kelas": per_kelas,
            "lolos": lolos, "total": total, "batch_slug": batch_slug,
            "konfig": konfig}


def _siapkan_yolo(konfig: dict) -> tuple[object, list[int], dict]:
    """Muat model + terjemahkan kelas sasaran, atau FilterTolak yang jelas."""
    path = _cari_model(konfig["model"])
    if path is None:
        raise FilterTolak(
            f"model objek '{konfig['model']}' tidak ditemukan di direktori "
            "bobot — taruh berkas .pt-nya di sana atau pakai metode warna")
    try:
        model = _muat_yolo(path)
    except Exception as e:                        # noqa: BLE001
        raise FilterTolak(f"gagal memuat model objek: {str(e)[:90]}") from e
    kelas_idx, peta_idx = _kelas_indeks(model, konfig["kelas"])
    if not kelas_idx:
        raise FilterTolak(
            "tak satu pun kelas sasaran cocok dengan kelas model "
            f"({sorted(peta_idx.values())})")
    return model, kelas_idx, peta_idx


def _laporan(hasil: dict, *, pratinjau: bool) -> dict:
    """Bentuk laporan publik dari hasil _proses (dipakai respons & berkas)."""
    total = hasil["total"]
    lolos = hasil["lolos"]
    return {
        "ok": True,
        "pratinjau": pratinjau,
        "metode": hasil["konfig"]["metode"],
        "batch": hasil["batch_slug"],
        "total": total,
        "lolos": lolos,
        "ditolak": total - lolos,
        "pass_rate": round(lolos / total, 4) if total else 0.0,
        "per_kelas": hasil["per_kelas"],
        "klip": {rel: {"keep": v["keep"],
                       "frame_cek": v.get("frame_cek", 0),
                       "frame_objek": v.get("frame_objek", 0),
                       "kelas_terlihat": v.get("kelas_terlihat", [])}
                 for rel, v in hasil["per_klip"].items()},
    }


# ============================================================
# API PUBLIK
# ============================================================

def pratinjau(projek_dir: Path, batch: str, konfig: dict) -> dict:
    """DRY-RUN: keputusan keep/tolak per klip + pass-rate + hitungan per-kelas.

    TAK MEMINDAH apa pun (cermin --dry-run di prototipe). Dipakai dialog untuk
    menampilkan "berapa yang akan lolos" sebelum pengguna menekan Terapkan.
    """
    hasil = _proses(Path(projek_dir), batch, konfig, maju=None, batal=None)
    return _laporan(hasil, pratinjau=True)


def terapkan(projek_dir: Path, batch: str, konfig: dict, *,
             maju: Callable[[dict], None] | None = None,
             batal: Callable[[], bool] | None = None) -> dict:
    """TERAPKAN: pindahkan klip yang ditolak ke klip/_ditolak/<batch>/.

    Klip ditolak DIPINDAH (tak pernah dihapus — aturan repo): bisa dipulihkan
    dengan menariknya kembali, dan klip_scan.pindai sudah melewati _ditolak
    sehingga tak terbaca lagi sebagai kerja. Laporan apa yang dijalankan &
    disimpan/ditolak ditulis ke klip/<batch>/.filter.json (nama berawalan titik,
    jadi klip_scan tak salah membacanya sebagai klip).
    """
    projek_dir = Path(projek_dir)
    hasil = _proses(projek_dir, batch, konfig, maju=maju, batal=batal)
    batch_slug = hasil["batch_slug"]
    tolak_dir = projek_dir / KLIP / KLIP_DITOLAK / batch_slug

    dipindah: list[str] = []
    for it in hasil["items"]:
        if hasil["per_klip"][it["rel"]]["keep"]:
            continue
        src = it["klip"]
        if not src.is_file():
            continue
        tolak_dir.mkdir(parents=True, exist_ok=True)
        dst = tolak_dir / src.name
        k = 2
        while dst.exists():                       # jangan menimpa klip ditolak lain
            dst = tolak_dir / f"{src.stem}-{k}{src.suffix}"
            k += 1
        src.replace(dst)
        dipindah.append(it["rel"])

    lap = _laporan(hasil, pratinjau=False)
    lap["dipindah"] = dipindah
    _tulis_laporan(projek_dir, batch_slug, hasil, dipindah)
    if maju:
        maju({"tahap": "selesai", "persen": 1.0})
    log.info("filter %r: %d/%d lolos, %d dipindah ke _ditolak (metode %s)",
             batch_slug, hasil["lolos"], hasil["total"], len(dipindah),
             hasil["konfig"]["metode"])
    return lap


def _tulis_laporan(projek_dir: Path, batch_slug: str, hasil: dict,
                   dipindah: list[str]) -> None:
    """Tulis klip/<batch>/.filter.json: konfig, daftar simpan/tolak, cap waktu."""
    batch_dir = projek_dir / KLIP / batch_slug
    if not batch_dir.is_dir():
        return
    simpan = [rel for rel, v in hasil["per_klip"].items() if v["keep"]]
    tolak = [rel for rel, v in hasil["per_klip"].items() if not v["keep"]]
    isi = {
        "versi": VERSI,
        "waktu": _dt.datetime.now().isoformat(timespec="seconds"),
        "metode": hasil["konfig"]["metode"],
        "konfig": hasil["konfig"],
        "total": hasil["total"],
        "lolos": hasil["lolos"],
        "ditolak": hasil["total"] - hasil["lolos"],
        "pass_rate": round(hasil["lolos"] / hasil["total"], 4)
        if hasil["total"] else 0.0,
        "per_kelas": hasil["per_kelas"],
        "disimpan": simpan,
        "ditolak_klip": tolak,
        "dipindah": dipindah,
    }
    p = batch_dir / BERKAS_LAPORAN
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(isi, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(p)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
