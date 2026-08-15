"""
Membongkar arsip dataset yang diunggah.

TIDAK memakai `ZipFile.extractall`. Fungsi itu menulis apa pun yang tertulis di
dalam arsip, termasuk nama seperti `../../etc/authorized_keys`, dan tidak punya
batas ukuran hasil bongkaran. Di sini setiap entri disaring sendiri lewat
`safe_relpath` yang sama dengan jalur unggahan biasa, lalu ditulis mengalir
dengan tiga pagar: batas total byte, batas jumlah entri, dan pemeriksaan ulang
bahwa berkas tujuan benar-benar berada di dalam folder unggahan.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from ..security import safe_relpath

CHUNK = 256 * 1024


class ArsipTolak(Exception):
    """Arsipnya sendiri yang bermasalah — bukan sekadar satu entri dilewati."""


def _didalam(anak: Path, induk: Path) -> bool:
    try:
        anak.resolve().relative_to(induk.resolve())
        return True
    except ValueError:
        return False


def bongkar(zip_path: Path, tujuan: Path, *, maks_byte: int,
            maks_entri: int = 200_000) -> dict:
    """
    Bongkar `zip_path` ke dalam `tujuan`. -> ringkasan dict.

    Yang DILEWATI tanpa menggagalkan seluruh proses: folder, berkas berekstensi
    di luar daftar (termasuk .zip di dalam .zip), dan nama yang memuat `..`.
    Yang MENGGAGALKAN: arsip rusak, jumlah entri berlebihan, atau total isi
    melebihi `maks_byte` — dua terakhir adalah tanda zip bomb.

    Nama folder teratas arsip TIDAK dibuang. Ekspor Roboflow membongkar
    langsung menjadi train/valid/test, sedangkan ekspor aplikasi ini juga
    begitu; membuang satu tingkat "kalau semuanya seragam" justru membuat
    hasilnya berbeda-beda tergantung isi arsip.
    """
    tujuan.mkdir(parents=True, exist_ok=True)
    ditulis = dilewati = 0
    byte_total = 0
    contoh_dilewati: list[str] = []

    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ArsipTolak(f"arsip tidak terbaca: {str(e)[:60]}") from e

    with zf:
        entri = zf.infolist()
        if len(entri) > maks_entri:
            raise ArsipTolak(f"isi arsip terlalu banyak ({len(entri)} entri)")

        # Ukuran hasil bongkaran diperiksa dari header DULU, sebelum satu byte
        # pun ditulis. Header bisa berbohong, jadi batasnya diperiksa lagi saat
        # menulis — tetapi memeriksa di awal menghindarkan menulis 40 GB dulu
        # baru sadar.
        perkiraan = sum(i.file_size for i in entri)
        if perkiraan > maks_byte:
            raise ArsipTolak(
                f"isi arsip {perkiraan / 1048576:.0f} MB, melebihi batas "
                f"{maks_byte / 1048576:.0f} MB")

        for info in entri:
            if info.is_dir():
                continue
            rel = safe_relpath(info.filename)
            if not rel:
                dilewati += 1
                if len(contoh_dilewati) < 5:
                    contoh_dilewati.append(info.filename[:60])
                continue
            dest = tujuan / rel
            if not _didalam(dest.parent, tujuan) and dest.parent != tujuan:
                dilewati += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            try:
                with zf.open(info) as src, open(tmp, "wb") as out:
                    while True:
                        buf = src.read(CHUNK)
                        if not buf:
                            break
                        byte_total += len(buf)
                        if byte_total > maks_byte:
                            raise ArsipTolak(
                                "isi arsip melebihi batas saat dibongkar — "
                                "kemungkinan arsip yang sengaja dibuat besar")
                        out.write(buf)
                tmp.replace(dest)
                ditulis += 1
            except ArsipTolak:
                tmp.unlink(missing_ok=True)
                raise
            except Exception:
                tmp.unlink(missing_ok=True)
                dilewati += 1

    return {"ditulis": ditulis, "dilewati": dilewati, "bytes": byte_total,
            "contoh_dilewati": contoh_dilewati}
