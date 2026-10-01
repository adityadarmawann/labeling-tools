"""
Proses yang menjalankan EVALUASI classifier aksi, terlepas dari server:

    python -m app.services.eval_aksi_jalan <folder-projek> <nomor>

Subproses dengan alasan yang sama seperti latih_aksi_jalan/evaluasi_jalan: torch
(dan matplotlib untuk gambarnya) berat, dan server CPU tak boleh membayar ongkos
itu maupun menariknya ke dalam proses yang harus tetap bisa diimpor tanpanya.

DUA HAL yang membuatnya aman berdampingan dengan training (rencana G#9) — dan
keduanya DIPINJAM apa adanya dari latih_aksi_jalan supaya ada SATU sumber:

  1. KUNCI GPU AKSI YANG SAMA (latih_aksi_jalan.KUNCI_GPU). Evaluasi memakai
     kartu persis seperti training, jadi ia mengantre di LAJUR AKSI yang sama:
     eval tak pernah jalan bersamaan dengan training aksi di kartu yang sama, dan
     tak pernah menyentuh lajur training GAMBAR.
  2. PENJAGA VRAM (latih_aksi_jalan.tunggu_vram). Di bawah ambang, tunggu lega
     lalu jatuh ke CPU — tak pernah memaksa masuk GPU yang sedang penuh.

Hasil ditulis ke .latih/A<n>.eval.json (eval_aksi.tulis_hasil); metrik.json +
confusion.png ditulis ke run dir oleh eval_aksi.evaluasi.
"""
from __future__ import annotations

import fcntl
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from . import eval_aksi, latih_aksi, latih_aksi_jalan

# Lajur GPU yang SAMA dengan training aksi — eval memakai kartu seperti training,
# jadi ia harus mengantre di kunci itu, bukan lajur sendiri.
KUNCI_GPU = latih_aksi_jalan.KUNCI_GPU


def _sekarang() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def main() -> int:
    if len(sys.argv) < 3:
        print("pemakaian: eval_aksi_jalan <folder-projek> <nomor>",
              file=sys.stderr)
        return 2
    ds, nomor = Path(sys.argv[1]), int(sys.argv[2])

    isi = latih_aksi.baca(ds, nomor)
    if isi is None:
        print(f"training aksi A{nomor} tidak ada di {ds}", file=sys.stderr)
        return 2
    best = latih_aksi.bobot_training(ds, nomor, "best")
    if best is None:
        eval_aksi.tulis_hasil(ds, nomor, {
            "keadaan": "gagal", "selesai": _sekarang(),
            "galat": "best.pt belum ada — trainingnya belum selesai"})
        print("GAGAL: best.pt belum ada", file=sys.stderr)
        return 1

    # ---- antre di lajur GPU AKSI (sama dengan training aksi) ----
    KUNCI_GPU.touch(exist_ok=True)
    f = open(KUNCI_GPU, "r+")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print("  giliran (lajur aksi) langsung didapat", flush=True)
        except OSError:
            print("  menunggu giliran — GPU aksi sedang dipakai "
                  "(training/eval lain)", flush=True)
            eval_aksi.tulis_hasil(ds, nomor, {"keadaan": "antre",
                                              "pid": os.getpid(),
                                              "mulai": _sekarang()})
            fcntl.flock(f, fcntl.LOCK_EX)
            print("  giliran didapat", flush=True)

        # Penjaga VRAM: lindungi apa pun yang mungkin masih memakai kartu.
        eval_aksi.tulis_hasil(ds, nomor, {"keadaan": "jalan",
                                          "pid": os.getpid(),
                                          "mulai": _sekarang()})
        device = latih_aksi_jalan.tunggu_vram()
        print(f"  backend : {isi.get('backend')}  | device {device}", flush=True)

        def progres(sudah, total):
            # Kemajuan kasar (klip ke-i dari N) — eval biasanya singkat, cukup
            # supaya UI tak terlihat menggantung pada versi yang besar.
            if total and (sudah == total or sudah % 20 == 0):
                eval_aksi.tulis_hasil(ds, nomor, {
                    "keadaan": "jalan", "pid": os.getpid(), "mulai": _sekarang(),
                    "maju": {"sudah": sudah, "total": total}})

        metrik = eval_aksi.evaluasi(ds, nomor, device=device, progres=progres)

        eval_aksi.tulis_hasil(ds, nomor, {
            "keadaan": "selesai", "pid": os.getpid(), "selesai": _sekarang(),
            "backend": isi.get("backend"), "versi": isi.get("versi"),
            "metrik": metrik,
            "punya_gambar": bool(metrik.get("punya_gambar")),
        })
        print(f"\n  akurasi              : {metrik.get('akurasi')}", flush=True)
        print(f"  akurasi rata-kelas   : "
              f"{metrik.get('akurasi_rerata_kelas')}", flush=True)
        print(f"  vonis                : {metrik.get('tingkat')}", flush=True)
        return 0
    except Exception as e:                       # noqa: BLE001
        eval_aksi.tulis_hasil(ds, nomor, {"keadaan": "gagal",
                                          "selesai": _sekarang(),
                                          "galat": str(e)[:300]})
        traceback.print_exc()
        return 1
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
