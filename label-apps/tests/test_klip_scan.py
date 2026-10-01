"""
Uji folder internal projek video (_sumber, klip, klip/_ditolak, _sumber/_bad)
dilewati di SETIAP penghitung gambar — scanner.tersembunyi dan sidebar
(projek.ringkas/_survei). Aturan repo "folder internal jangan dihitung": kalau
tidak, satu video atau ratusan klip terbaca sebagai "gambar" di kartu & lencana.
"""
from __future__ import annotations

from pathlib import Path

from app.config import FOLDER_INTERNAL
from app.services import projek, scanner


def _sentuh(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")                 # penghitung hanya melihat ekstensi


def _projek_campur(tmp_path) -> Path:
    """Projek dengan 3 gambar asli di akar + berkas di tiap folder internal."""
    d = tmp_path / "proj"
    for i in range(3):
        _sentuh(d / f"asli-{i}.jpg")
    _sentuh(d / "_sumber" / "video.mp4")
    _sentuh(d / "_sumber" / "nyasar.jpg")          # harus tetap terlewati
    _sentuh(d / "_sumber" / "_bad" / "cacat.mp4")
    _sentuh(d / "klip" / "b1" / "a.jpg")           # klip "gambar" pun dilewati
    _sentuh(d / "klip" / "_ditolak" / "b.jpg")
    return d


def test_konstanta_memuat_folder_video():
    assert "_sumber" in FOLDER_INTERNAL and "klip" in FOLDER_INTERNAL


def test_tersembunyi_melewati_folder_internal(tmp_path):
    d = _projek_campur(tmp_path)
    assert scanner.tersembunyi(d / "_sumber" / "nyasar.jpg", d)
    assert scanner.tersembunyi(d / "_sumber" / "_bad" / "cacat.mp4", d)
    assert scanner.tersembunyi(d / "klip" / "b1" / "a.jpg", d)
    assert scanner.tersembunyi(d / "klip" / "_ditolak" / "b.jpg", d)
    assert not scanner.tersembunyi(d / "asli-0.jpg", d)


def test_ringkas_hanya_hitung_gambar_akar(tmp_path):
    d = _projek_campur(tmp_path)
    # Sidebar (projek.ringkas -> _survei) hanya melihat 3 gambar asli; video di
    # _sumber dan klip di klip/ tak ikut.
    assert projek.ringkas(d)["jumlah"] == 3
