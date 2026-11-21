"""Satu pintu ke paket `osam`, yang API publiknya berganti di versi 0.12.

Dua generasi API hidup berdampingan di modul ini, dan itu disengaja untuk
sementara:

  0.5   `osam.apis.generate(osam.types.GenerateRequest(...))`. Sesi
        onnxruntime dibuat di `osam.types._model`, embedding gambar
        dikembalikan ke pemanggil supaya bisa disimpan sendiri, dan pilihan
        CPU atau GPU hanya bisa diatur dengan menambal pembuat sesi itu.
  0.12  `osam.Client(in_process=True, device=...)` dengan metode `segment`.
        Sesi, pelepasan model saat diam, dan pemakaian ulang embedding diurus
        osam sendiri, dan perangkatnya dipilih lewat argumen.

Kenapa keduanya harus ada, bukan langsung pindah: SAM 3 yang asli, yang
menerima klik titik dan kotak selain prompt teks, HANYA ada di 0.12 ke atas,
sementara prod masih berjalan dengan 0.5 sampai pemasangan di sana selesai dan
servicenya dimuat ulang. Di jendela itu kode yang sama harus hidup di kedua
sisi, karena urutan amannya adalah: kirim kode dulu, pasang osam kemudian,
baru muat ulang service. Tanpa modul ini, satu klik SAM di prod akan gagal
begitu osam dinaikkan, sebab `osam.apis` sudah tidak ada lagi di sana.

Begitu kedua venv memakai 0.12, cabang 0.5 boleh dibuang dan `baru()` hilang
bersamanya.

Pembagian tugas dengan `autolabel`: KEBIJAKAN tetap di sana (model mana yang
boleh naik GPU, berapa VRAM yang harus bebas, penjaga training). Modul ini
hanya menerima keputusan itu sebagai `gpu=True/False` lalu berbicara dengan
osam.
"""

from __future__ import annotations

import threading
from typing import Any

from ..log import catat

log = catat("labelapp.osam")

# Berapa model boleh tinggal termuat sekaligus. Dua, bukan satu: jalur klik
# (sam2/sam3) dan jalur prompt teks (yoloworld/sam3) memakai model berbeda,
# dan dengan satu saja, bergantian memakai keduanya berarti memuat ulang bobot
# setiap kali. Model yang diam tetap dilepas osam sendiri sesudah keep_alive.
MODEL_RESIDEN = 2

_KUNCI = threading.Lock()
_klien: dict[str, Any] = {}
_jenis_prompt: dict[str, frozenset[str]] = {}


def baru() -> bool:
    """True kalau osam terpasang memakai API Client, yaitu 0.12 ke atas."""
    import osam
    return hasattr(osam, "Client")


def versi() -> str:
    import osam
    return getattr(osam, "__version__", "?")


def klien(gpu: bool):
    """Klien osam dalam proses untuk perangkat yang diminta, dibuat sekali.

    Satu klien per perangkat, bukan satu untuk semua: pilihan CPU atau GPU
    diputuskan per panggilan oleh penjaga VRAM di `autolabel`, sedangkan
    sebuah klien memegang perangkatnya sejak dibuat. `in_process=True` berarti
    tidak ada server terpisah yang dinyalakan; modelnya berjalan di proses app
    seperti sebelumnya.
    """
    import osam
    nama = "cuda" if gpu else "cpu"
    with _KUNCI:
        ada = _klien.get(nama)
    if ada is not None:
        return ada
    # Pembuatan klien dikerjakan DI LUAR kunci. Kalau di dalam, jatuhnya GPU ke
    # CPU memanggil fungsi ini lagi sementara kuncinya masih dipegang, dan
    # threading.Lock bukan reentran: prosesnya menggantung tanpa pesan apa pun.
    try:
        c = osam.Client(in_process=True, device=nama,
                        max_loaded_models=MODEL_RESIDEN)
    except Exception as e:              # noqa: BLE001 -- CUDA tak terpakai
        if not gpu:
            raise
        log.warning("klien osam GPU gagal dibuat (%s), memakai CPU", e)
        return klien(False)
    with _KUNCI:
        dipakai = _klien.setdefault(nama, c)
    if dipakai is not c:                # ada yang mendahului, lepas yang kalah
        try:
            c.close()
        except Exception:               # noqa: BLE001
            pass
    return dipakai


def _kehabisan_memori(e: BaseException) -> bool:
    """Apakah galat ini soal memori perangkat, bukan soal permintaan kita.

    Dibedakan karena hanya galat memori yang pantas dicoba ulang di CPU:
    prompt yang salah atau model yang tak dikenal akan gagal lagi di sana,
    dan mengulangnya hanya menyembunyikan sebabnya.
    """
    pesan = str(e).lower()
    return any(k in pesan for k in ("out of memory", "failed to allocate",
                                    "cudamalloc", "cuda_error_out_of_memory"))


def segmen(model: str, rgb, *, gpu: bool = False, **prompt) -> list:
    """Segmentasi satu panggilan, mengembalikan daftar annotation osam.

    `prompt` diteruskan apa adanya ke `Client.segment`: `points` dengan
    `point_labels`, atau `box`, untuk satu objek; `texts` untuk memindai
    seluruh gambar. Kalau GPU kehabisan memori di tengah jalan, panggilannya
    diulang di CPU: lebih lambat, tetapi tetap memberi hasil, dan itulah
    perilaku yang dulu harus ditambal sendiri.
    """
    try:
        return klien(gpu).segment(model, rgb, **prompt)
    except Exception as e:              # noqa: BLE001
        if not gpu or not _kehabisan_memori(e):
            raise
        log.warning("osam %s kehabisan VRAM, diulang di CPU: %s", model, e)
        return klien(False).segment(model, rgb, **prompt)


def jenis_prompt(model: str) -> frozenset[str]:
    """Jenis prompt yang diterima sebuah model, menurut osam sendiri.

    Dibaca dari mesinnya, bukan didaftar di sini, supaya antarmuka tidak
    pernah menawarkan klik titik pada model yang hanya menerima teks, dan
    sebaliknya. Kosong kalau modelnya tak dikenal.
    """
    if not _jenis_prompt:
        try:
            for m in klien(False).list():
                _jenis_prompt[m.name] = frozenset(m.prompt_kinds)
        except Exception as e:          # noqa: BLE001
            log.warning("daftar model osam tak terbaca: %s", e)
            return frozenset()
    return _jenis_prompt.get(model, frozenset())


def terunduh(model: str) -> bool:
    """Bobot model itu sudah lengkap di cache atau belum.

    Tidak di-cache, berbeda dari `jenis_prompt`: jenis prompt sebuah model
    tetap, sedangkan "sudah terunduh" justru berubah tepat ketika orang
    menunggu jawabannya.
    """
    try:
        for m in klien(False).list():
            if m.name == model:
                return m.size is not None
    except Exception as e:              # noqa: BLE001
        log.warning("status unduhan %s tak terbaca: %s", model, e)
    return False


def tutup() -> bool:
    """Lepaskan semua klien beserta model yang mereka pegang.

    Dipakai `autolabel.bebaskan_gpu` sebelum training lokal mulai. Klien baru
    dibuat lagi sendiri pada panggilan berikutnya, dan selama training
    berjalan penjaga VRAM akan mengarahkannya ke CPU.
    """
    with _KUNCI:
        ada = bool(_klien)
        for c in _klien.values():
            try:
                c.close()
            except Exception:           # noqa: BLE001 -- sudah tertutup
                pass
        _klien.clear()
    return ada
