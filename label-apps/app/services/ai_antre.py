"""
Satu antrean untuk SEMUA pekerjaan AI yang memakai GPU.

Dulu pre-label dan auto-label punya semaphore sendiri-sendiri, dan tiap router
punya peta "satu job per akun" sendiri. Akibatnya satu orang bisa menjalankan
keduanya bersamaan: dua model ultralytics (plus SAM/YOLO-World) termuat di GPU
yang sama pada saat yang sama. Pada kartu 8 GB itu jalan paling pendek menuju
kehabisan VRAM di tengah pekerjaan orang, dan yang gagal belum tentu yang
belakangan dimulai.

Dua hal yang dijaga di sini, dan hanya di sini:

  giliran  -- berapa pekerjaan AI boleh memakai GPU bersamaan (bawaan 1).
              Yang kedua MENUNGGU, bukan ditolak, supaya orang tak perlu
              menekan tombolnya lagi.
  klaim()  -- satu pekerjaan AI per akun, LINTAS FITUR. Menolak lebih awal,
              di router, dengan menyebut fitur mana yang sedang jalan; tanpa
              itu pesannya jadi "masih ada auto-label yang berjalan" padahal
              yang jalan sebenarnya pre-label.

Env LABELAPP_AI_SERENTAK menggantikan LABELAPP_PRELABEL_SERENTAK dan
LABELAPP_AUTOLABEL_SERENTAK; keduanya masih dibaca supaya pemasangan lama tak
berubah diam-diam, dan yang TERKECIL yang menang -- menaikkan batas harus
disengaja, bukan efek samping satu env yang tertinggal.
"""
from __future__ import annotations

import os
import threading


def _env(*nama: str) -> int:
    nilai = [int(v) for n in nama
             if (v := os.environ.get(n)) and str(v).strip().isdigit()]
    return max(1, min(nilai)) if nilai else 1


SERENTAK = _env("LABELAPP_AI_SERENTAK", "LABELAPP_PRELABEL_SERENTAK",
                "LABELAPP_AUTOLABEL_SERENTAK")
giliran = threading.Semaphore(SERENTAK)

_kunci = threading.Lock()
_jalan: dict[str, str] = {}          # akun -> nama fitur yang sedang jalan


def klaim(akun: str, fitur: str) -> str | None:
    """Daftarkan satu pekerjaan AI untuk `akun`. None kalau berhasil; kalau
    sudah ada yang jalan, kembalikan NAMA fitur yang memegangnya."""
    with _kunci:
        ada = _jalan.get(akun)
        if ada:
            return ada
        _jalan[akun] = fitur
        return None


def lepas(akun: str, fitur: str = "") -> None:
    """Lepaskan klaim. `fitur` opsional: kalau diisi, hanya melepas kalau
    memang fitur itu yang memegang -- supaya penyelesaian job lama tidak
    mencabut klaim job baru yang sudah menggantikannya."""
    with _kunci:
        if akun in _jalan and (not fitur or _jalan[akun] == fitur):
            _jalan.pop(akun, None)


def sedang_jalan(akun: str) -> str:
    with _kunci:
        return _jalan.get(akun, "")
