"""
Path di server yang terakhir dipakai, per akun.

Mengetik ulang `/home/paul/computer-vision/smartbin/sirsak/sirsak-anylabel/
botol-kaleng-tetra-mlp-cup-1` setiap kali bukan cuma merepotkan — salah satu
huruf menghasilkan "folder tidak ada di server", dan salah satu huruf di tengah
path yang lain bisa membuka dataset yang keliru tanpa disadari.

Disimpan di berkas, bukan di sesi, karena sesi hilang setiap kali proses
restart. Berkasnya diletakkan di dalam folder unggahan milik akun itu sendiri:
namanya diawali titik sehingga tidak pernah ikut terpindai sebagai dataset,
dan ia terhapus bersama folder akunnya tanpa perlu pembersihan terpisah.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ..config import Settings
from ..security import safe_slug

MAKS = 10               # cukup untuk beberapa hari kerja, tidak sampai jadi daftar panjang
NAMA = ".riwayat-path.json"

# Dua permintaan dari satu orang bisa datang bersamaan (misalnya membuka dua
# tab). Kuncinya se-proses, bukan per-akun: penulisannya singkat dan jarang.
_kunci = threading.Lock()


def _berkas(settings: Settings, user: str) -> Path:
    return settings.uploads_root / safe_slug(user) / NAMA


def baca(settings: Settings, user: str) -> list[dict]:
    """Daftar terbaru lebih dulu. Berkas rusak diperlakukan sebagai kosong —
    riwayat adalah kemudahan, bukan data; menggagalkan halaman karenanya
    jauh lebih merugikan daripada kehilangan daftarnya."""
    p = _berkas(settings, user)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(d, list):
        return []
    out = []
    for r in d[:MAKS]:
        if isinstance(r, dict) and isinstance(r.get("path"), str):
            out.append({"path": r["path"],
                        "cara": "salin" if r.get("cara") == "salin" else "buka",
                        "waktu": r.get("waktu") or 0,
                        "ada": Path(r["path"]).is_dir()})
    return out


def _tulis(settings: Settings, user: str, daftar: list[dict]) -> None:
    p = _berkas(settings, user)
    p.parent.mkdir(parents=True, exist_ok=True)
    ringkas = [{"path": r["path"], "cara": r["cara"], "waktu": r["waktu"]}
               for r in daftar[:MAKS]]
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(ringkas, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(p)
    except OSError:
        tmp.unlink(missing_ok=True)


def catat(settings: Settings, user: str, path: Path | str, cara: str) -> None:
    """Naikkan `path` ke posisi teratas. Gagal menulis tidak dianggap galat:
    pemanggilnya sedang di jalur sukses membuka dataset, dan riwayat yang tidak
    tercatat bukan alasan untuk menggagalkan itu."""
    s = str(Path(path))
    with _kunci:
        daftar = [r for r in baca(settings, user) if r["path"] != s]
        daftar.insert(0, {"path": s, "cara": cara, "waktu": int(time.time())})
        _tulis(settings, user, daftar)


def lupakan(settings: Settings, user: str, path: str) -> None:
    with _kunci:
        _tulis(settings, user,
               [r for r in baca(settings, user) if r["path"] != str(path)])
