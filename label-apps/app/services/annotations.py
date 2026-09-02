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


def tulis_aman(p: Path, teks: str) -> None:
    """
    Tulis lewat berkas sementara lalu ganti nama, supaya proses yang terputus
    di tengah penulisan tidak meninggalkan berkas anotasi setengah jadi. Berkas
    setengah jadi lebih berbahaya daripada tidak ada berkas sama sekali: ia
    tetap ikut terpindai, dan JSON yang terpotong terbaca sebagai anotasi rusak.
    """
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(teks, encoding="utf-8")
        tmp.replace(p)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


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

    Yang diperiksa BERKAS DI DISK, bukan `it["shapes"]` dari ingatan sesi.
    Ingatan sesi hanya diperbarui untuk orang yang menyimpannya sendiri, jadi
    sesi orang lain — dan sesi mana pun sesudah anotasi ditulis dari
    AnyLabeling desktop — tetap mengira gambarnya kosong. Memeriksa ingatan
    membuat penolakan ini gagal justru pada satu-satunya kasus yang penting:
    dua orang di projek yang sama, dan yang satu menghapus pekerjaan yang lain
    tanpa satu pun peringatan. unmark_background sudah membaca disk sejak
    awal; yang merusak justru yang tidak.
    """
    jp = it["img"].with_suffix(".json")
    di_disk = []
    if jp.is_file():
        try:
            di_disk = json.loads(jp.read_text(encoding="utf-8")).get("shapes") or []
        except (OSError, ValueError):
            raise Menolak("berkas anotasi rusak — periksa atau hapus manual dulu")
    n = len(di_disk) or len(it["shapes"])
    if n:
        raise Menolak(f"gambar ini punya {n} objek — "
                      "hapus dulu anotasinya di AnyLabeling")
    if "berkas anotasi rusak" in it["issues"]:
        raise Menolak("berkas anotasi rusak — periksa atau hapus manual dulu")
    tulis_aman(jp, json.dumps({
        "version": LABELME_VERSION, "flags": {}, "shapes": [],
        "imagePath": it["img"].name, "imageData": None,
        "imageHeight": it["H"], "imageWidth": it["W"],
    }, ensure_ascii=False, indent=2))
    it["shapes"] = []
    it["issues"] = ["latar (tanpa objek)"]
    return jp


def unmark_background(it: dict) -> Path:
    # Sama alasannya dengan mark_background: ingatan sesi bisa basi. Di sini
    # pemeriksaan disknya memang sudah ada sejak awal, di bawah.
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
