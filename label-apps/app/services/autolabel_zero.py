"""
Auto-label batch ZERO-SHOT: sebut nama kelasnya, seluruh gambar BELUM teranotasi
di satu task dipindai sekaligus oleh model teks (YOLO-World untuk kotak, SAM 3
untuk poligon), prediksinya diisi sebagai anotasi yang tinggal diverifikasi.

Beda dari prelabel.py: prelabel memakai model yang SUDAH DILATIH projek; ini
tidak perlu latih apa pun, cukup nama kelas. Mesinnya memakai ulang
`autolabel.dari_teks` yang sudah teruji (lewat osam), bukan menambah tumpukan
baru. Peran "GroundingDINO" diisi YOLO-World (open-vocabulary, teks->kotak);
SAM 3 memberi teks->poligon langsung tanpa langkah SAM terpisah.

Tiga sifat dijaga, sama persis dengan prelabel (lihat prelabel.py):

  1. TIDAK PERNAH menimpa kerja manusia. Hanya gambar yang masih "belum"
     (gambar_konteks_job saring="belum"); hak tulis per-batch dihormati
     (tugas.tolak_tulis).
  2. Gambar tanpa deteksi dibiarkan "belum", BUKAN ditulis kosong (latar).
  3. Prediksi ditulis format sama dengan simpan manual lewat
     prelabel.tulis_prediksi (labelme .json + YOLO .txt), jadi bisa disunting
     seperti anotasi biasa.

Guard VRAM: auto-label DITOLAK saat training lokal aktif (gpu_sinyal.latih_aktif).
Walau model teks ini jalan di CPU, aturan tim: selama training di server ini,
hanya anotasi manual yang boleh. Pemeriksaan dilakukan di router (umpan balik
cepat) dan sekali lagi di sini (jaga-jaga job yang sudah telanjur antre).
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from . import ai_antre, autolabel, gpu_sinyal, prelabel, scanner, tugas
from .prelabel import Dibatalkan, tulis_prediksi

# ------------------------------------------------------------------ kemajuan
# Dict terpisah dari prelabel: fitur yang beda, supaya kemajuan tak saling timpa
# kalau satu akun kebetulan memakai keduanya.
_maju: dict[str, dict] = {}
_kunci = threading.Lock()


def catat_maju(kunci: str, **nilai) -> None:
    with _kunci:
        d = _maju.setdefault(kunci, {})
        d.update(nilai)
        d["saat"] = time.time()


def kemajuan(kunci: str) -> dict:
    with _kunci:
        return dict(_maju.get(kunci) or {})


def bersihkan_maju(kunci: str) -> None:
    with _kunci:
        _maju.pop(kunci, None)


# --------------------------------------------------------------- throttle
# Satu job serentak secara bawaan: yang kedua MENUNGGU giliran, bukan ditolak.
# Antrean yang SAMA dengan pre-label (ai_antre) -- keduanya memuat model ke GPU
# yang sama, jadi membatasinya sendiri-sendiri tidak membatasi apa pun.
SERENTAK = ai_antre.SERENTAK
_giliran = ai_antre.giliran


class TrainingSedangJalan(RuntimeError):
    """Auto-label ditolak karena training lokal sedang memakai GPU."""


# ------------------------------------------------------------------ model
def pilih_model(jenis: str) -> str:
    """Pilih model teks bawaan dari jenis anotasi projek.

    poligon -> SAM 3 (teks->poligon); selain itu (kotak atau belum diatur) ->
    YOLO-World (teks->kotak). Pengguna bisa menimpa dari antarmuka.

    'kerangka' TIDAK disebut di sini: projek pose ditolak jalur zero-shot
    (lihat jalankan), jadi menawarkan model teks untuknya hanya menyesatkan.
    Dulu docstring menjanjikan 'poligon/kerangka -> SAM 3' padahal kodenya
    hanya memeriksa 'poligon'."""
    return ("sam3:latest" if str(jenis or "").strip().lower() == "poligon"
            else "yoloworld:latest")


def prompt_default(ds: Path) -> list[str]:
    """Nama kelas projek -> daftar prompt teks. Kosong kalau projek belum punya
    kelas (pengguna wajib mengetik prompt sendiri di antarmuka)."""
    names = scanner.baca_nama_kelas(ds) or {}
    return [str(v).strip() for v in names.values() if str(v).strip()]


def _bentuk_dari_temuan(temuan: list) -> list[dict]:
    """Daftar autolabel.Temuan -> daftar bentuk labelme (kotak/poligon)."""
    out: list[dict] = []
    for t in temuan:
        pts = [[float(x), float(y)] for x, y in (t.points or [])]
        if t.shape_type == "polygon" and len(pts) < 3:
            continue
        if t.shape_type == "rectangle" and len(pts) < 2:
            continue
        out.append({"label": str(t.label), "text": "", "points": pts,
                    "group_id": None, "shape_type": t.shape_type, "flags": {}})
    return out


# ------------------------------------------------------------------ job
def jalankan(ds: Path, uploads_root: Path, tid: str, akun: str,
             batas: int, conf: float, model: str, prompt: list[str],
             batal=None, batch: str | None = None) -> dict:
    """Auto-label zero-shot gambar BELUM teranotasi (maks `batas`).

    Lingkup: satu JOB (`tid`) ATAU satu BATCH/unggahan (`batch`, mis. dari kartu
    'Belum ditugaskan'). Tepat satu dari keduanya dipakai -- `batch` is not None
    menang. Batch memakai tugas.gambar_belum_batch (tak perlu bagi tugas
    dulu); job memakai tugas.gambar_konteks_job.

    `batal` callable -> True untuk berhenti. Melempar Dibatalkan kalau diminta,
    TrainingSedangJalan kalau training lokal aktif, ValueError kalau prompt/
    model tidak sah, FileNotFoundError kalau tasknya tak dikenal."""
    kunci = akun
    model = (model or "").strip()
    prompt = [str(t).strip() for t in (prompt or []) if str(t).strip()]
    if model not in autolabel.MODEL_TEKS:
        raise ValueError("model auto-label tidak dikenal")
    if not prompt:
        raise ValueError("belum ada nama kelas untuk dicari")

    catat_maju(kunci, jalan=True, persen=0.0, fase_nama="Menunggu giliran",
               diproses=0, objek=0, dilewati=0, total=0, model=model)

    # Guard training: sebelum antre, beri umpan balik cepat.
    if gpu_sinyal.latih_aktif():
        raise TrainingSedangJalan(
            "training sedang berjalan di server ini; auto-label dinonaktifkan "
            "sampai selesai. Anotasi manual tetap bisa.")

    with _giliran:
        # Diperiksa lagi: giliran bisa datang setelah training keburu mulai.
        if gpu_sinyal.latih_aktif():
            raise TrainingSedangJalan(
                "training keburu mulai; auto-label dibatalkan. Coba lagi "
                "setelah training selesai.")

        catat_maju(kunci, fase_nama="Menyiapkan model (unduh sekali kalau perlu)")

        # Dipindai ulang di sini (bukan sess.items): job jalan di thread lain
        # setelah permintaan balik, keadaan gambar bisa sudah berubah.
        items, names = scanner.scan(ds)
        data = tugas.baca_projek(ds, uploads_root)
        # Projek kerangka (pose) ditolak, sama seperti jalur gabungan. Tanpa
        # penjaga ini zero-shot menulis poligon/kotak lewat tulis_prediksi ke
        # dataset keypoint -- bentuk yang tak pernah bisa jadi anotasi pose dan
        # harus dihapus satu per satu belakangan.
        if tugas.jenis_berlaku(data, items) == "kerangka":
            raise ValueError(
                "projek kerangka (pose) tidak bisa auto-label zero-shot; "
                "pakai 'Pre-label dengan AI' dengan model pose terlatih.")
        if batch is not None:
            target = tugas.gambar_belum_batch(items, ds, batch)
        else:
            target = tugas.gambar_konteks_job(items, data, ds, tid, saring="belum")
            if target is None:
                raise FileNotFoundError("tugas tak dikenal")
        if batas and batas > 0:
            target = target[:int(batas)]
        total = len(target)
        catat_maju(kunci, total=total, fase_nama="Memprediksi")

        diproses = objek = ditolak = kosong = 0
        for i, it in enumerate(target):
            if batal and batal():
                raise Dibatalkan()
            # Training bisa mulai di tengah job panjang: hentikan dengan rapi.
            if gpu_sinyal.latih_aktif():
                raise TrainingSedangJalan(
                    "training mulai di tengah proses; auto-label dihentikan. "
                    "Gambar yang sudah terisi tetap ada.")
            if tugas.tolak_tulis(ds, akun, it["img"]):
                ditolak += 1
            else:
                try:
                    temuan = autolabel.dari_teks(
                        it["img"], prompt, model=model, ambang=float(conf))
                    bentuk = _bentuk_dari_temuan(temuan)
                except autolabel.TidakAdaObjek:
                    bentuk = []            # tak ada yang cocok -> biarkan "belum"
                except Exception:          # noqa: BLE001 -- satu gambar gagal
                    bentuk = []            # jangan jatuhkan seluruh job
                    # Sesi lain harus tahu berkas ini berubah. Tanpa ini,
                    # pelabel yang sedang bekerja di kanvas TIDAK AKAN PERNAH
                    # melihat hasil auto-label selama ia memakai panah kiri/
                    # kanan: segarkan() keluar lebih awal kalau pencacahnya tak
                    # naik. Ia lalu melabeli ulang dari nol lalu menyimpan, dan
                    # simpanannya menimpa hasil AI.
                if bentuk:
                    prelabel._beritahu(ds, it["img"])
                    names = tulis_prediksi(it, ds, names, bentuk)
                    objek += len(bentuk)
                    diproses += 1
                else:
                    kosong += 1
            catat_maju(kunci,
                       persen=round((i + 1) / max(total, 1) * 100, 1),
                       diproses=diproses, objek=objek,
                       dilewati=ditolak + kosong, kosong=kosong,
                       ditolak=ditolak)

    # Dua sebab yang SANGAT berbeda, dulu dihitung di ember yang
    # sama: "dilewati 8" tak memberi petunjuk apakah modelnya memang
    # tak menemukan apa-apa atau orangnya tidak berhak menulis ke
    # gambar-gambar itu. Yang pertama berarti ganti prompt/model, yang
    # kedua berarti minta akses. `dilewati` dipertahankan sebagai
    # jumlah keduanya supaya pembaca lama tetap benar.
    return {"total": total, "diproses": diproses, "objek": objek,
            "dilewati": ditolak + kosong, "kosong": kosong, "ditolak": ditolak}
