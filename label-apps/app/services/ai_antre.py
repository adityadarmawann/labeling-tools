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
# akun -> (nama fitur, pemeriksa apakah pekerjanya masih hidup)
_jalan: dict[str, tuple[str, object]] = {}


def _hidup(akun: str) -> bool:
    """Apakah klaim milik `akun` masih dipegang pekerja yang benar-benar jalan.

    Klaim dulu cuma nama fitur, tanpa cara apa pun memeriksa keadaannya. Satu
    pekerja yang berhenti tanpa sempat melepas -- thread mati di luar blok
    try, tugas asyncio dibatalkan, galat di jalur yang tak terduga -- membuat
    akun itu terkunci SELAMANYA: tiap percobaan berikutnya dijawab "masih ada
    auto-label yang berjalan" padahal tak ada apa pun yang berjalan, dan
    satu-satunya jalan keluar adalah menyalakan ulang server. Terjadi di prod.

    Tombol Batalkan pun tak menolong, karena ia hanya menyetel bendera yang
    dibaca pekerja; tanpa pekerja, tak ada yang membacanya.
    """
    ada = _jalan.get(akun)
    if not ada:
        return False
    _, cek = ada
    if cek is None:
        return True              # tak ada cara memeriksa: anggap masih jalan
    try:
        return bool(cek())
    except Exception:            # noqa: BLE001
        return False             # pemeriksanya sendiri rusak: anggap mati


def klaim(akun: str, fitur: str, hidup=None) -> str | None:
    """Daftarkan satu pekerjaan AI untuk `akun`. None kalau berhasil; kalau
    sudah ada yang jalan, kembalikan NAMA fitur yang memegangnya.

    `hidup` sebaiknya diisi pemanggil dengan fungsi yang menjawab apakah
    pekerjanya masih jalan. Klaim yang pekerjanya sudah mati dibuang di sini,
    jadi akun tak bisa lagi terkunci oleh sisa pekerjaan yang tak pernah
    melepas.
    """
    with _kunci:
        if akun in _jalan:
            if _hidup(akun):
                return _jalan[akun][0]
            _jalan.pop(akun, None)
        _jalan[akun] = (fitur, hidup)
        return None


def lepas(akun: str, fitur: str = "") -> None:
    """Lepaskan klaim. `fitur` opsional: kalau diisi, hanya melepas kalau
    memang fitur itu yang memegang -- supaya penyelesaian job lama tidak
    mencabut klaim job baru yang sudah menggantikannya."""
    with _kunci:
        if akun in _jalan and (not fitur or _jalan[akun][0] == fitur):
            _jalan.pop(akun, None)


def sedang_jalan(akun: str) -> str:
    """Nama fitur yang sedang jalan untuk akun ini, atau "".

    Ikut membuang klaim basi: kalau tidak, antarmuka terus melaporkan
    pekerjaan yang sebenarnya sudah tak ada.
    """
    with _kunci:
        if akun in _jalan and not _hidup(akun):
            _jalan.pop(akun, None)
        ada = _jalan.get(akun)
        return ada[0] if ada else ""
