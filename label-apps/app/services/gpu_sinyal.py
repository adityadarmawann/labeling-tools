"""
Sinyal ringan "GPU sedang dipakai training" antar-proses.

Masalah yang diselesaikan: SAM (MobileSAM/osam) berjalan DI DALAM proses app dan
bisa menahan beberapa GB VRAM. Training berjalan di SUBPROSES terpisah. Di GPU 8
GB, kalau sesi SAM masih memegang ~4 GB saat training mulai, training gagal
"CUDA out of memory". Proses yang berbeda tak bisa saling membebaskan memori,
jadi butuh sinyal sederhana: training menulis penanda saat ia MEMEGANG GPU, dan
layanan SAM di proses app membacanya untuk memilih CPU selama itu.

Penanda = berkas kecil di /tmp berisi PID training. PID dipakai supaya penanda
BASI (training mati mendadak, mis. dibunuh OOM-killer, tanpa sempat membersihkan)
terdeteksi lewat proses yang sudah tak ada, lalu diabaikan dan dihapus. Modul ini
sengaja TANPA dependensi berat (tak ada torch/onnxruntime/cv2) supaya murah
diimpor baik oleh proses app maupun oleh subproses training.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_MARKER = Path(tempfile.gettempdir()) / "labelapp-latih-gpu-aktif.json"


def tandai_latih(pid: int | None = None) -> None:
    """Dipanggil subproses training SAAT ia sudah memegang GPU (sesudah flock)."""
    try:
        _MARKER.write_text(json.dumps({"pid": int(pid if pid else os.getpid())}))
    except OSError:
        pass


def lepas_latih() -> None:
    """Dipanggil subproses training saat selesai (di blok finally)."""
    try:
        _MARKER.unlink()
    except OSError:
        pass


def _hidup(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:          # ada, cuma beda pemilik
        return True
    except OSError:
        return False
    return True


def latih_aktif() -> bool:
    """True kalau ada training yang SEDANG memegang GPU (penanda ada & pid-nya
    hidup). Penanda basi dari proses yang sudah mati dibersihkan lalu diabaikan.
    Murah: satu baca berkas kecil + satu cek pid, aman dipanggil tiap klik SAM."""
    try:
        d = json.loads(_MARKER.read_text())
    except (OSError, ValueError):
        return False
    if _hidup(int(d.get("pid") or 0)):
        return True
    lepas_latih()                    # basi -> bersihkan
    return False
