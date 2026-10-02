"""
Menggambar mask di atas gambar, lalu menyimpannya sebagai thumbnail.

Thumbnail di-cache per akun. Warna kelas diturunkan dari nama kelasnya, jadi
kelas yang sama selalu berwarna sama tanpa perlu tabel warna.
"""
from __future__ import annotations

import colorsys
import json
import os
import re
import threading
from pathlib import Path

import cv2
import numpy as np

from ..config import get_settings
from .scanner import item_key, kunci_isi

JPEG_QUALITY = 86
# Isi mask di atas gambar. Dinaikkan dari 0.34: pada foto terang (kotak susu
# putih) warna setipis itu nyaris tak terlihat.
OVERLAY_ALPHA = 0.45
# Dinaikkan tiap kali gambar overlay-nya berubah, supaya thumbnail lama yang
# tercache (kunci-nya dari isi berkas, BUKAN dari kode ini) ikut dibuat ulang.
# v3: pose/keypoint digambar seperti Roboflow — rangka tipis berwarna per sisi +
# titik kecil, bbox instance tak dibanjiri warna.
# v4: nomor slot titik dicetak di pratinjau besar (/view), bukan di grid.
RENDER_VERSI = 4


def hash_kelas(nama) -> int:
    """
    Hash nama kelas yang SAMA PERSIS dengan hashKode di label.js.

    `hash()` bawaan Python tidak bisa dipakai: untuk string ia diacak ulang
    setiap proses (PYTHONHASHSEED), sehingga warna sebuah kelas berubah tiap
    kali server dinyalakan ulang — dan tidak pernah sama dengan warna di kanvas,
    walau komentar di label.js selama ini menyatakan sebaliknya.
    """
    h = 0
    for ch in str(nama):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    if h >= 0x80000000:            # kembalikan ke rentang bertanda 32-bit
        h -= 0x100000000
    return abs(h)


def warna_kelas(nama) -> str:
    """Warna kelas sebagai string CSS, sama dengan `warna()` di kanvas."""
    return f"hsl({hash_kelas(nama) % 997 / 997 * 360:.0f}, 62%, 55%)"


def cls_color(key) -> tuple[int, int, int]:
    """Warna kelas sebagai RGB, untuk menggambar overlay thumbnail."""
    h = (hash_kelas(key) % 997) / 997.0
    # HSL 62%/55%, sama dengan yang dipakai kanvas — bukan HSV, supaya
    # warnanya benar-benar sama dan bukan sekadar bernuansa mirip.
    r, g, b = colorsys.hls_to_rgb(h, 0.55, 0.62)
    return int(r * 255), int(g * 255), int(b * 255)


def _hex_bgr(hx) -> tuple[int, int, int] | None:
    """'#rrggbb' -> (B,G,R) untuk cv2. None kalau bukan hex 6 digit."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(hx or "").strip())
    if not m:
        return None
    n = int(m.group(1), 16)
    return (n & 255, (n >> 8) & 255, (n >> 16) & 255)


def _warna_slot(warna: list, idx: int, nama) -> tuple[int, int, int]:
    """Warna BGR satu slot keypoint: UTAMAKAN warna[slot] template (skema sisi
    kiri/kanan/tengah dari impor Roboflow), jatuh ke warna-hash nama kalau tak
    ada. Padanan warnaKp() di label.js supaya thumbnail = kanvas."""
    if 0 <= idx < len(warna):
        c = _hex_bgr(warna[idx])
        if c:
            return c
    return cls_color(nama)[::-1]


def _cari_tugas(img: Path) -> Path | None:
    """.tugas.json projek, ditelusuri ke atas dari gambar. Gambar bisa di akar
    projek, di images/, atau di <split>/images/ — jadi naik beberapa tingkat."""
    d = Path(img).parent
    for _ in range(5):
        p = d / ".tugas.json"
        if p.is_file():
            return p
        if d.parent == d:
            break
        d = d.parent
    return None


def skeleton_item(item: dict) -> dict | None:
    """Template skeleton projek pose dari .tugas.json: {titik, edge, warna}.

    None kalau bukan projek pose. Nama slot dinormalkan sama seperti
    scanner._skeleton_titik (yang dipakai read_yolo memberi label titik), jadi
    nama di sini pasti cocok dengan label bentuk titiknya. edge/warna diambil
    apa adanya — _sah_skeleton sudah menyelaraskan indeksnya saat menyimpan.
    """
    p = _cari_tugas(item["img"])
    if not p:
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sk = d.get("skeleton") if isinstance(d, dict) else None
    if not isinstance(sk, dict):
        return None
    titik = [" ".join(str(t or "").split())[:40] for t in (sk.get("titik") or [])]
    if not any(titik):
        return None
    edge = [(int(e[0]), int(e[1])) for e in (sk.get("edge") or [])
            if isinstance(e, (list, tuple)) and len(e) == 2]
    return {"titik": titik, "edge": edge, "warna": list(sk.get("warna") or [])}


def _gambar_pose(im, pose_shapes: list, tpl: dict, sc: float, side: int) -> None:
    """Gambar rangka pose langsung di `im` (opasitas penuh, tajam) ala Roboflow:
    garis sisi tipis berwarna per slot, lalu titik KECIL. Bbox instance SENGAJA
    tidak digambar — Roboflow pun hanya menampilkannya saat kursor di atasnya,
    bukan di grid/pratinjau; membanjirinya warna justru menutupi fotonya."""
    titik, edge, warna = tpl["titik"], tpl["edge"], tpl["warna"]
    # Ukuran relatif thumbnail: kecil, seperti Roboflow. ~2px titik + 1px garis
    # di grid 320; ~4px + 2px di pratinjau 1100.
    r = max(2, round(side / 260))
    lw = max(1, round(side / 550))

    grup: dict = {}
    for s in pose_shapes:
        if s["type"] != "point" or s.get("group_id") is None:
            continue
        x, y = s["pts"][0] * sc
        v = int((s.get("flags") or {}).get("v", 2))
        grup.setdefault(s["group_id"], {})[s["label"]] = (float(x), float(y), v)

    # Sisi dulu, titik menimpanya (sama urutannya dengan label.js:244).
    for byname in grup.values():
        for i, j in edge:
            if not (0 <= i < len(titik) and 0 <= j < len(titik)):
                continue
            a, b = byname.get(titik[i]), byname.get(titik[j])
            if not a or not b or a[2] < 1 or b[2] < 1:   # lewati titik absen
                continue
            col = _warna_slot(warna, i, titik[i])
            cv2.line(im, (round(a[0]), round(a[1])), (round(b[0]), round(b[1])),
                     col, lw, cv2.LINE_AA)

    for byname in grup.values():
        for idx, nm in enumerate(titik):
            p = byname.get(nm)
            if not p or p[2] < 1:                        # absen/dihapus: lewati
                continue
            c = (round(p[0]), round(p[1]))
            col = _warna_slot(warna, idx, nm)
            if p[2] >= 2:                                # visible: titik terisi
                cv2.circle(im, c, r, col, -1, cv2.LINE_AA)
                if r >= 3:                               # tepi tipis agar menonjol
                    cv2.circle(im, c, r, (40, 40, 40), 1, cv2.LINE_AA)
            else:                                        # occluded: cincin kosong
                cv2.circle(im, c, r, col, max(1, lw), cv2.LINE_AA)

    # Nomor slot titik, HANYA di pratinjau besar (side>=700 -> halaman /view
    # "Lihat"), bukan di sel grid yang kecil — di sana lusinan angka cuma jadi
    # bercak. Diletakkan di samping titik, dengan garis luar gelap supaya terbaca
    # di atas latar seterang/segelap apa pun. Diminta supaya titik di layar bisa
    # dicocokkan dengan nomornya.
    if side >= 700:
        fs = max(0.34, side / 2600)
        for byname in grup.values():
            for nm, p in byname.items():
                if p[2] < 1:
                    continue
                org = (round(p[0]) + r + 2, round(p[1]) - r)
                cv2.putText(im, nm, org, cv2.FONT_HERSHEY_SIMPLEX, fs,
                            (0, 0, 0), 3, cv2.LINE_AA)        # garis luar
                cv2.putText(im, nm, org, cv2.FONT_HERSHEY_SIMPLEX, fs,
                            (255, 255, 255), 1, cv2.LINE_AA)  # isi putih


def render(item: dict, side: int):
    """Gambar + mask ter-overlay, diskalakan supaya sisi terpanjang = side."""
    im = cv2.imread(str(item["img"]))
    if im is None:
        return None
    # Diperkecil DULU, baru mask digambar di atasnya. Sebelumnya kebalikannya:
    # garis 3px digambar di gambar 4080px lalu diperkecil ke 320px, jadi lebarnya
    # ikut menyusut ke <0,3px dan lenyap kabur oleh INTER_AREA — itulah kenapa
    # poligonnya "hampir tak kelihatan". Digambar sesudah diperkecil, tebalnya
    # relatif ke ukuran thumbnail dan tetap tajam apa pun resolusi sumbernya.
    h, w = im.shape[:2]
    sc = side / max(h, w)
    ow, oh = max(1, round(w * sc)), max(1, round(h * sc))
    im = cv2.resize(im, (ow, oh), interpolation=cv2.INTER_AREA)
    ov = im.copy()
    tebal = max(2, round(min(ow, oh) / 75))        # ~4px di thumbnail 320px

    # Projek pose: titik+bbox instance ditangani terpisah (rangka tajam, titik
    # kecil, tanpa banjir warna). Sisanya (poligon/kotak biasa) seperti dulu.
    tpl = skeleton_item(item)
    pose_shapes, pakai_overlay = [], False
    for s in item["shapes"]:
        if tpl is not None and s.get("group_id") is not None \
                and s["type"] in ("point", "rectangle"):
            pose_shapes.append(s)
            continue
        col = cls_color(s["label"])[::-1]          # cv2 memakai BGR
        pts = np.round(s["pts"] * sc).astype(np.int32)
        # Titik, garis, dan polyline tidak punya bagian dalam: mengisinya
        # menghasilkan bercak yang tidak ada di anotasinya.
        if s["type"] == "point":
            cv2.circle(im, tuple(pts[0]), max(3, tebal * 2), col, -1, cv2.LINE_AA)
        elif s["type"] in ("line", "linestrip"):
            cv2.polylines(im, [pts], False, col, tebal, cv2.LINE_AA)
        else:
            cv2.fillPoly(ov, [pts], col)
            cv2.polylines(im, [pts], True, col, tebal, cv2.LINE_AA)
            pakai_overlay = True

    out = cv2.addWeighted(ov, OVERLAY_ALPHA, im, 1 - OVERLAY_ALPHA, 0) \
        if pakai_overlay else im
    # Rangka pose digambar SETELAH blend, langsung di hasil akhir: garis & titik
    # keypoint tetap tajam (tidak diredam alpha overlay seperti dulu).
    if tpl is not None and pose_shapes:
        _gambar_pose(out, pose_shapes, tpl, sc, side)
    return out


# Thumbnail dipakai BERSAMA semua akun, tidak lagi satu salinan per akun.
#
# Pikselnya memang sama untuk siapa pun — ia diturunkan dari gambar, anotasi,
# dan ukurannya saja, tidak ada satu pun bagian yang bergantung pada siapa
# yang melihat. Menghitungnya ulang per akun berarti mengerjakan pekerjaan
# yang sama berkali-kali: terukur 10,89 ms CPU per thumbnail, jadi sepuluh
# orang membuka grid 120 gambar yang sama menghabiskan 13,06 detik-CPU untuk
# hasil yang identik, padahal cukup 1,31.
#
# Ia sekaligus memperbaiki kesalahan yang selama ini tidak kelihatan: dengan
# cache per akun, drop_thumbs_for hanya pernah dipanggil pada sesi orang yang
# MENGEDIT. Kalau A memperbaiki anotasi, salinan milik B tidak pernah
# dibatalkan dan B terus melihat mask yang lama. Kunci yang diturunkan dari
# isi berkasnya membuat itu tidak bisa terjadi lagi — berkasnya berubah,
# kuncinya berubah, dan yang lama tidak pernah terbaca siapa pun.
#
# Berbagi folder tidak membuka akses apa pun: rute /thumb memanggil
# sess.find(path) lebih dulu, jadi orang hanya bisa meminta thumbnail gambar
# yang memang sudah terlihat olehnya. Yang dibagi hasil perhitungannya, bukan
# haknya.
FOLDER_BERSAMA = "_bersama"


def dir_bersama() -> Path:
    d = get_settings().thumb_root / FOLDER_BERSAMA
    d.mkdir(parents=True, exist_ok=True)
    return d


def nama_thumb(item: dict, side: int) -> str:
    """`<kunci path>_<kunci isi>_<sisi>.jpg`.

    Kunci path ditaruh di depan supaya berkas lama sebuah gambar masih bisa
    disapu dengan satu glob saat anotasinya berubah — tanpa itu, tiap suntingan
    meninggalkan thumbnail yatim yang menumpuk selama server hidup.
    """
    return f"{item_key(item)}_{kunci_isi(item)}_{side}v{RENDER_VERSI}.jpg"


def thumb_path(sess, item: dict, side: int) -> Path | None:
    """Path thumbnail, dibuat kalau belum ada. Dipakai bersama semua akun."""
    p = dir_bersama() / nama_thumb(item, side)
    if not p.exists():
        im = render(item, side)
        if im is None:
            return None
        # Ditulis ke nama sementara lalu dipindahkan: dua akun bisa meminta
        # thumbnail yang sama pada saat yang sama, dan yang kedua tidak boleh
        # membaca berkas yang baru separuh tertulis. os.replace atomik di
        # dalam satu filesystem.
        # Ekstensi .jpg DIPERTAHANKAN di nama sementara: cv2.imwrite memilih
        # formatnya dari ekstensi, dan ".tmp" membuatnya melempar cv2.error
        # alih-alih menulis apa pun.
        tmp = p.with_name(
            f"{p.stem}.{os.getpid()}.{threading.get_ident()}.tmp.jpg")
        cv2.imwrite(str(tmp), im, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        try:
            os.replace(tmp, p)
        except OSError:
            tmp.unlink(missing_ok=True)
            return None
    return p
