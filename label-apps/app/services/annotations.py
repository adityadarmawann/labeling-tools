"""
Menulis anotasi ke disk.

Hanya dua operasi tulis yang diizinkan dari web: menandai gambar sebagai latar
(anotasi kosong) dan membatalkannya. Keduanya menolak bekerja kalau gambar
punya objek, supaya tidak ada anotasi yang terhapus tanpa sengaja lewat klik.
"""
from __future__ import annotations

import json
from pathlib import Path

LABELME_VERSION = "0.4.36"


class Menolak(Exception):
    """Operasi ditolak karena akan menghilangkan data."""


def write_label_file(sess) -> list[str]:
    """
    Kumpulkan label yang sudah dipakai di folder + label tambahan dari setelan,
    tulis ke satu berkas. Berkas ini diteruskan ke AnyLabeling lewat --labels
    sehingga daftar kelas sudah terisi dan tidak perlu diketik ulang.
    """
    used = {str(s["label"]).strip() for it in sess.items for s in it["shapes"]
            if s["label"] is not None and str(s["label"]).strip()}
    allv = sorted(used | set(sess.settings.extra_labels))
    p = sess.thumbdir / "labels.txt"
    p.write_text("\n".join(allv) + "\n", encoding="utf-8")
    sess.labelfile = p if allv else None
    return allv


def mark_background(it: dict) -> Path:
    """
    Tulis berkas anotasi kosong (shapes: []) di samping gambar.

    Setara 'Mark Null' di Roboflow: gambar ikut ke dataset sebagai contoh
    negatif, bukan dibuang. AnyLabeling sendiri menolak menyimpan berkas tanpa
    shape, jadi berkasnya ditulis dari sini.
    """
    if it["shapes"]:
        raise Menolak(f"gambar ini punya {len(it['shapes'])} objek — "
                      "hapus dulu anotasinya di AnyLabeling")
    if "berkas anotasi rusak" in it["issues"]:
        raise Menolak("berkas anotasi rusak — periksa atau hapus manual dulu")
    jp = it["img"].with_suffix(".json")
    jp.write_text(json.dumps({
        "version": LABELME_VERSION, "flags": {}, "shapes": [],
        "imagePath": it["img"].name, "imageData": None,
        "imageHeight": it["H"], "imageWidth": it["W"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    it["shapes"] = []
    it["issues"] = ["latar (tanpa objek)"]
    return jp


def unmark_background(it: dict) -> Path:
    if it["shapes"]:
        raise Menolak("gambar ini punya anotasi — tidak dihapus")
    jp = it["img"].with_suffix(".json")
    if jp.exists():
        try:
            if json.loads(jp.read_text(encoding="utf-8")).get("shapes"):
                raise Menolak("berkas anotasi tidak kosong — tidak dihapus")
        except Menolak:
            raise
        except Exception:
            pass          # berkas rusak: tetap boleh dibuang
        jp.unlink()
    it["issues"] = ["belum dilabeli"]
    return jp
