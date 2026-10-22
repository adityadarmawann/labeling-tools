"""
Token unggah dari HP.

Roboflow punya kartu "Upload data from your phone" yang menampilkan QR; ponsel
memindainya lalu mengunggah gambar langsung ke projek. HIGOLAB menirunya: token
pendek yang menitipkan SATU hak — menambah gambar ke SATU projek, atas nama SATU
akun — ke sebuah tautan yang ponsel bisa buka tanpa login.

Alasannya disimpan di MEMORI, bukan di disk, dan itu disengaja: sama seperti
sesi (lihat session.SessionStore), token ini hilang saat proses restart. Hak
mengunggah yang menggantung di disk melewati reboot adalah hak yang tak bisa
dicabut; umurnya pendek (bawaan 2 jam) justru supaya tautan yang terlanjur
tersebar lewat foto QR tidak hidup selamanya.

Token TIDAK memberi hak lain: bukan login, tak bisa membaca gambar, membuka
projek lain, atau melabeli. Yang dipegangnya cuma (akun, projek) — dan saat
ponsel mengunggah, jalur yang sama persis dengan unggah biasa memeriksa ulang
boleh_unggah atas akun itu, jadi hak yang sudah dicabut di projeknya langsung
menutup unggahan HP juga.
"""
from __future__ import annotations

import secrets
import threading
import time

# Umur token. Pendek dengan sengaja: QR itu difoto, dibagikan, tertinggal di
# galeri — tautan yang hidup berhari-hari berarti siapa pun yang pernah melihat
# layarnya bisa menambah gambar kapan saja. Dua jam cukup untuk satu sesi
# memotret lapangan, dan desktop tinggal membuat QR baru kalau kedaluwarsa.
TTL_DETIK = 2 * 3600


class _Simpanan:
    """Peta token -> (user, ds, kedaluwarsa). Aman dari banyak thread."""

    def __init__(self) -> None:
        self._data: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _bersihkan(self, now: float) -> None:
        # Dipanggil di dalam lock. Token kedaluwarsa dibuang saat disentuh,
        # bukan oleh timer: tak ada banyak token sekaligus (satu desktop, satu
        # QR aktif per projek), jadi sapuan penuh tiap akses tetap murah.
        mati = [t for t, v in self._data.items() if v["exp"] <= now]
        for t in mati:
            del self._data[t]

    def buat(self, user: str, ds: str, ttl: int = TTL_DETIK) -> tuple[str, float]:
        """Terbitkan token baru untuk (user, ds). Mengembalikan (token, exp)."""
        token = secrets.token_urlsafe(24)
        exp = time.time() + max(60, int(ttl))
        with self._lock:
            self._bersihkan(time.time())
            self._data[token] = {"user": user, "ds": ds, "exp": exp}
        return token, exp

    def ambil(self, token: str) -> dict | None:
        """{'user','ds','exp'} kalau token masih hidup, None kalau tidak.

        Dipakai jalur unggah HP: None berarti tautannya kedaluwarsa atau palsu,
        dan rutenya membalas "minta QR baru" alih-alih menulis apa pun.
        """
        if not token:
            return None
        now = time.time()
        with self._lock:
            self._bersihkan(now)
            v = self._data.get(token)
            return dict(v) if v else None

    def cabut(self, token: str) -> None:
        with self._lock:
            self._data.pop(token, None)


_simpan = _Simpanan()

buat = _simpan.buat
ambil = _simpan.ambil
cabut = _simpan.cabut
