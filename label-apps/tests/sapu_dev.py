"""
Sapu seluruh fitur di server dev yang SEDANG BERJALAN.

Bedanya dengan pytest: yang ini menembak server sungguhan lewat jaringan,
dengan akun sungguhan dan dataset sungguhan. Yang dijaga bukan logikanya —
itu sudah ada di pytest — melainkan bahwa lingkungan dev-nya sendiri lengkap:
berkas model ada di tempatnya, folder datanya terisi, akunnya berhak, dan
batas ukurannya sama dengan prod.

Itu perkara yang tidak bisa ditangkap pytest sama sekali, karena pytest
membuat lingkungannya sendiri di tmp_path. Sebuah worktree dev yang kekurangan
`models/` lolos seluruh 245 tes dan baru gagal saat tombol SAM ditekan.

    .venv/bin/python tests/sapu_dev.py <sandi-darma-dev>
    .venv/bin/python tests/sapu_dev.py <sandi> --base http://127.0.0.1:8043

Uji ini MENULIS ke dataset dev (menandai latar, menyimpan bentuk, menggandakan
projek) lalu mengembalikannya. Jangan diarahkan ke prod.
"""
from __future__ import annotations

import io
import re
import sys
import time
import zipfile
from urllib.parse import unquote

import cv2
import httpx
import numpy as np

BASE = "http://103.182.240.28:8043"
AKUN = "darma-dev"
PROJEK = "paragon"

gagal: list[str] = []
n_ok = 0


def cek(nama: str, ok: bool, detail: object = "") -> None:
    global n_ok
    if ok:
        n_ok += 1
    else:
        gagal.append(nama)
    tanda = "\033[32mOK   \033[0m" if ok else "\033[31mGAGAL\033[0m"
    print(f"  {tanda} {nama}" + (f"  {str(detail)[:100]}" if detail else ""))


def jpeg(warna: int) -> bytes:
    return cv2.imencode(".jpg", np.full((80, 120, 3), warna, np.uint8))[1].tobytes()


def jalankan(base: str, sandi: str) -> int:
    c = httpx.Client(base_url=base, timeout=600, follow_redirects=False)
    cek("login lewat jaringan",
        c.post("/login", data={"user": AKUN, "pw": sandi}).status_code == 303)

    # ---------------------------------------------------------- projek
    j = c.get("/api/projek/daftar").json()
    nama = [p["nama"] for p in j.get("projek", [])]
    cek("daftar projek", j.get("ok") and PROJEK in nama, nama)
    if PROJEK not in nama:
        print(f"\n  Dataset '{PROJEK}' belum ada di dev. Salin dulu:\n"
              f"    ./sinkron-dev.sh darma --ke {AKUN} --projek {PROJEK}\n")
        return 1
    kartu = next(p for p in j["projek"] if p["nama"] == PROJEK)
    r = c.get("/api/projek/sampul", params={"path": kartu["sampul"]})
    cek("sampul kartu", r.status_code == 200 and len(r.content) > 500,
        f"{len(r.content)} byte")
    cek("buka dataset", c.post("/setsrc", params={"path": kartu["path"]}).json().get("ok"))

    # ---------------------------------------------------------- grid
    h = c.get("/").text
    cek("grid + bilah kemajuan",
        'class="lajur"' in h and "% selesai" in h and 'class="card"' in h,
        f'{h.count(chr(34) + "card" + chr(34))} kartu')
    for f in ("all", "unlab", "sudah", "issue", "bg"):
        t = c.get("/", params={"f": f})
        cek(f"saringan f={f}", t.status_code == 200,
            f'{t.text.count(chr(34) + "card" + chr(34))} kartu')
    cek("urutkan", c.get("/", params={"s": "nama-turun"}).status_code == 200)
    cek("cari nama berkas", c.get("/", params={"q": "IMG"}).status_code == 200)

    # ---------------------------------------------------------- satu gambar
    p1 = unquote(re.findall(r'/view\?path=([^"&]+)', c.get("/").text)[0])
    cek("halaman Lihat", c.get("/view", params={"path": p1}).status_code == 200)
    cek("halaman kanvas", c.get("/label", params={"path": p1}).status_code == 200)
    r = c.get("/thumb", params={"path": p1, "s": 300})
    cek("thumbnail", r.status_code == 200 and len(r.content) > 500, len(r.content))
    cek("gambar penuh", c.get("/gambar", params={"path": p1}).status_code == 200)

    # ---------------------------------------------------------- tulis, lalu pulihkan
    belum = unquote(re.findall(
        r'/view\?path=([^"&]+)', c.get("/", params={"f": "unlab"}).text)[0])
    cek("tandai latar", c.post("/markbg", params={"path": belum}).json().get("ok"))
    cek("batalkan latar", c.post("/unmarkbg", params={"path": belum}).json().get("ok"))
    r = c.post("/api/simpan", json={"path": belum, "shapes": [
        {"label": "uji-sapu", "shape_type": "rectangle",
         "points": [[5, 5], [80, 80]]}]}).json()
    cek("simpan bentuk dari kanvas", r.get("ok"), r.get("error", ""))
    # Dikembalikan: menyimpan tanpa bentuk menulis penanda latar, jadi
    # berkasnya harus benar-benar dibuang, bukan dikosongkan.
    c.post("/api/simpan", json={"path": belum, "shapes": []})
    c.post("/unmarkbg", params={"path": belum})
    cek("anotasi uji dikembalikan",
        c.get("/", params={"f": "unlab"}).text.count('class="card"') > 0)

    # ---------------------------------------------------------- model
    r = c.post("/api/sam", json={"path": p1, "box": [200, 400, 900, 1400]}).json()
    cek("SAM dari kotak", r.get("ok") and len(r.get("points", [])) > 2,
        r.get("error") or f'{len(r.get("points", []))} titik, {r.get("model")}')
    r = c.post("/api/deteksi", json={"path": p1, "teks": "bottle"}).json()
    cek("deteksi prompt teks", "ok" in r, r.get("error", ""))

    # ---------------------------------------------------------- splitting
    r = c.post("/api/split/jalankan", params={"split": "8:1:1"}).json()
    cek("splitting dijalankan", r.get("ok"), r.get("error", ""))
    k = {}
    for _ in range(300):
        k = c.get("/api/split/kemajuan").json()
        if k.get("selesai") or k.get("gagal") or k.get("persen") == 100:
            break
        time.sleep(1)
    cek("splitting selesai", k.get("persen") == 100 or k.get("selesai"),
        k.get("gagal") or k.get("persen"))

    # ---------------------------------------------------------- ekspor
    cek("ringkasan ekspor", c.get("/api/ekspor/ringkasan").json().get("ok"))
    for fmt in ("yolo-seg", "yolo", "coco", "voc", "createml"):
        r = c.get("/ekspor", params={"format": fmt, "gambar": 0})
        isi = (len(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
               if r.status_code == 200 else 0)
        cek(f"ekspor {fmt}", r.status_code == 200 and isi > 1,
            f"{len(r.content) // 1024} KB, {isi} berkas")
    cek("pindai ulang", c.post("/rescan").json().get("ok", True))

    # ---------------------------------------------------------- unggah
    r = c.put("/upload", params={"ds": "sapu-unggah", "name": "uji-1.jpg"},
              content=jpeg(60), headers={"Content-Type": "image/jpeg"})
    cek("unggah gambar", r.json().get("ok"), r.text[:80])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in (1, 2):
            z.writestr(f"images/uji-{i}.jpg", jpeg(30 + i * 40))
            z.writestr(f"labels/uji-{i}.txt", "0 0.5 0.5 0.2 0.2\n")
        z.writestr("data.yaml", "names:\n  0: botol\n")
    r = c.put("/upload", params={"ds": "sapu-zip", "name": "isi.zip"},
              content=buf.getvalue(), headers={"Content-Type": "application/zip"})
    cek("unggah arsip .zip", r.json().get("ok"), r.text[:80])
    r = c.post("/unzip", params={"ds": "sapu-zip", "name": "isi.zip"}).json()
    cek("bongkar arsip", r.get("ok"), r.get("error", ""))
    cek("pakai unggahan sebagai dataset",
        c.post("/useupload", params={"ds": "sapu-zip"}).json().get("ok"))
    r = c.put("/tambah", params={"name": "tambahan.jpg"}, content=jpeg(200),
              headers={"Content-Type": "image/jpeg"})
    cek("tambah gambar ke dataset terbuka", r.json().get("ok"), r.text[:80])

    # ---------------------------------------------------------- projek CRUD
    r = c.post("/api/projek/gabung",
               params={"sumber": "sapu-unggah", "tujuan": "sapu-zip"}).json()
    cek("gabungkan projek", r.get("ok"), r.get("error", ""))
    r = c.post("/api/projek/duplikat",
               params={"nama": "sapu-zip", "baru": "sapu-dua"}).json()
    cek("gandakan projek", r.get("ok"), r.get("error", ""))
    r = c.post("/api/projek/ganti-nama",
               params={"nama": "sapu-dua", "baru": "sapu-tiga"}).json()
    cek("ganti nama projek", r.get("ok"), r.get("error", ""))
    cek("buang ke sampah",
        c.post("/api/projek/sampah", params={"nama": "sapu-tiga"}).json().get("ok"))
    sampah = c.get("/api/projek/daftar").json().get("sampah") or []
    r = c.post("/api/projek/pulihkan",
               params={"folder": sampah[0]["folder"]}).json() if sampah else {}
    cek("pulihkan dari sampah", r.get("ok"), r.get("error", ""))

    # ---------------------------------------------------------- admin
    cek("halaman kelola akun", c.get("/akun").status_code == 200)
    r = c.get("/api/akun/daftar").json()
    cek("API kelola akun", r.get("ok"), f'{len(r.get("akun", []))} akun')

    # ---------------------------------------------------------- bersihkan
    for n in (r.get("nama") if isinstance(r, dict) else None,
              "sapu-tiga", "sapu-zip", "sapu-unggah"):
        if n:
            c.post("/api/projek/sampah", params={"nama": n})
    c.post("/setsrc", params={"path": kartu["path"]})

    print(f"\n  {n_ok} lolos, {len(gagal)} gagal")
    if gagal:
        print("  gagal: " + ", ".join(gagal))
    return 1 if gagal else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    base = BASE
    if "--base" in sys.argv:
        base = sys.argv[sys.argv.index("--base") + 1]
    raise SystemExit(jalankan(base, sys.argv[1]))
