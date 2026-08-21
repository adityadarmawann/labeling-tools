"""
Membaca folder dataset dan menilai anotasinya.

Mendukung tiga tata letak:
  - labelme / AnyLabeling : <nama>.jpg + <nama>.json bersebelahan
  - YOLO                  : images/ + labels/ (+ classes.txt)
  - ekspor Roboflow       : train/ valid/ test/, masing-masing YOLO, plus
                            data.yaml berisi nama kelas di akarnya

Penilaian di inspect() sengaja konservatif: yang dilaporkan hanya hal yang
hampir pasti salah (mask terlalu kecil, titik di luar gambar, poligon dengan
titik terlalu sedikit), bukan selera. Temuan palsu membuat orang berhenti
mempercayai papan periksa.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import cv2
import numpy as np
import yaml
from PIL import Image

from ..config import IMG_EXT

# Nama folder split yang dikenali pada ekspor Roboflow/ultralytics. `val` dan
# `valid` dua-duanya dipakai di lapangan: data.yaml Roboflow menulis `val:`
# tetapi foldernya bernama `valid`.
SPLIT_DIKENAL = ("train", "valid", "val", "test")


def item_key(item: dict) -> str:
    """Kunci stabil per gambar untuk penamaan berkas thumbnail."""
    return str(abs(hash(str(item["img"].resolve()))))


def dimensi(ip: Path) -> tuple[int, int] | None:
    """
    (H, W) sebuah gambar, dibaca dari HEADER-nya saja.

    Dulu di sini dipakai `cv2.imread`, yang mendekode seluruh piksel padahal
    yang dibutuhkan cuma dua angka. Pada dataset 55 ribu gambar bedanya bukan
    sedikit: header ~33x lebih cepat, memindai turun dari satu setengah menit
    jadi beberapa detik.

    Mengembalikan None kalau berkasnya tidak terbaca — itu sekaligus jadi
    penyaring berkas rusak, sama seperti `imread` yang mengembalikan None.
    """
    try:
        with Image.open(ip) as im:
            w, h = im.size
        if w > 0 and h > 0:
            return h, w
    except Exception:
        pass
    # Sebagian format yang dikenali OpenCV tidak dikenali Pillow (dan
    # sebaliknya); jatuh ke jalur lama daripada membuang gambarnya.
    im = cv2.imread(str(ip))
    if im is None:
        return None
    return im.shape[:2]


def _nama_dari_yaml(p: Path) -> dict:
    """`names` di data.yaml -> {indeks: nama}. Terima bentuk daftar maupun peta."""
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(d, dict):
        return {}
    n = d.get("names")
    if isinstance(n, list):
        return {i: str(v) for i, v in enumerate(n)}
    if isinstance(n, dict):
        out = {}
        for k, v in n.items():
            try:
                out[int(k)] = str(v)
            except (TypeError, ValueError):
                continue
        return out
    return {}


def baca_nama_kelas(src: Path) -> dict:
    """
    Cari nama kelas untuk dataset YOLO di `src`.

    Ditelusuri dari folder itu sendiri lalu naik dua tingkat, karena ekspor
    Roboflow menaruh `data.yaml` di AKAR sementara gambarnya ada di
    `train/images`. Tanpa penelusuran ke atas, membuka satu split membuat
    kelasnya tampil sebagai "0", "1", "2" — angka yang tidak berarti apa-apa
    bagi orang yang sedang memeriksa anotasi.
    """
    src = Path(src)
    for folder in (src, src.parent, src.parent.parent):
        for nama in ("data.yaml", "data.yml", "dataset.yaml"):
            p = folder / nama
            if p.is_file():
                n = _nama_dari_yaml(p)
                if n:
                    return n
        cf = folder / "classes.txt"
        if cf.is_file():
            try:
                baris = cf.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            n = {i: b.strip() for i, b in enumerate(baris) if b.strip()}
            if n:
                return n
    return {}


def tutup_cincin(pts: list) -> list:
    """
    Tambahkan lagi titik pertama di akhir poligon, kalau belum ada.

    AnyLabeling melakukannya untuk setiap poligon hasil SAM
    (`segment_anything.py:235`), dan ekspor Roboflow juga menutup cincinnya —
    diukur 40 dari 40 poligon. Supaya keluaran kita bisa dibandingkan
    berdampingan dengan keduanya, cincinnya ditutup juga.

    Tidak mengubah bentuk sama sekali: perasterisasi menutup poligon sendiri,
    jadi mask-nya identik dengan atau tanpa titik ini.
    """
    if len(pts) < 3:
        return list(pts)
    a, b = pts[0], pts[-1]
    if abs(float(a[0]) - float(b[0])) < 1e-9 and abs(float(a[1]) - float(b[1])) < 1e-9:
        return list(pts)
    return list(pts) + [list(a)]


def buka_cincin(pts: list) -> list:
    """
    Kebalikan `tutup_cincin` — buang titik terakhir kalau sama dengan yang pertama.

    Dipakai di batas PEMBACAAN. Kanvas bekerja dengan cincin terbuka: dua titik
    yang bertumpuk persis membuat penyuntingan menyesatkan — menyeret salah
    satunya meninggalkan duri yang tidak terlihat asalnya. Jadi berkas menyimpan
    cincin tertutup, kanvas memakainya terbuka, dan penutupannya dikembalikan
    saat menulis.
    """
    if len(pts) > 3:
        a, b = pts[0], pts[-1]
        if abs(float(a[0]) - float(b[0])) < 1e-9 and abs(float(a[1]) - float(b[1])) < 1e-9:
            return list(pts[:-1])
    return list(pts)


def rapikan_titik(pts: list, W: int, H: int) -> list:
    """
    Rapikan titik sebelum ditulis: kurung ke dalam gambar, buang kembaran beruntun.

    Dua hal yang diukur berbeda dari Roboflow, dan dua-duanya berpihak pada
    Roboflow. Dari 3.905 poligon nyata mereka: **nol** koordinat di luar [0,1]
    dan **nol** titik kembar beruntun.

    Koordinat di luar gambar tidak pernah datang dari kanvas kita — di sana
    setiap titik sudah dikurung. Yang mungkin membawanya adalah berkas impor.
    Meneruskannya apa adanya membuat perasterisasi saat latihan memotong
    seenaknya; mengurungnya membuat bentuknya jelas dan sah.
    """
    out = []
    for x, y in pts:
        x = min(max(float(x), 0.0), float(W))
        y = min(max(float(y), 0.0), float(H))
        if out and abs(out[-1][0] - x) < 1e-9 and abs(out[-1][1] - y) < 1e-9:
            continue
        out.append([x, y])
    # Pengurungan bisa membuat titik pertama dan terakhir jadi kembar juga.
    while len(out) > 3 and abs(out[0][0] - out[-1][0]) < 1e-9 \
            and abs(out[0][1] - out[-1][1]) < 1e-9:
        out.pop()
    return out


def periksa_kelengkapan(src: Path) -> list[str]:
    """
    Hal yang membuat sebuah folder dataset kurang lengkap, dalam bahasa manusia.

    Dipakai setelah unggahan supaya orang tahu SEBELUM mulai bekerja. Yang
    paling sering terjadi: folder ekspor Roboflow diunggah tanpa `data.yaml`,
    dan kelasnya lalu tampil sebagai "0", "1", "2" — anotasinya benar, cuma
    namanya hilang, dan itu baru ketahuan setelah gambar dibuka satu per satu.
    """
    src = Path(src)
    pesan: list[str] = []
    if not src.is_dir():
        return ["folder tidak ada"]

    punya_txt = any(True for _ in src.rglob("labels/*.txt"))
    if punya_txt and not baca_nama_kelas(src):
        pesan.append(
            "dataset YOLO ini tidak punya data.yaml atau classes.txt, jadi "
            "nama kelasnya akan tampil sebagai angka (0, 1, 2). Unggah juga "
            "data.yaml-nya, atau buat classes.txt berisi satu nama kelas "
            "per baris.")

    if any(True for _ in src.rglob("*.json")) and punya_txt:
        pesan.append(
            "folder ini memuat anotasi labelme (.json) DAN YOLO (.txt) "
            "sekaligus — periksa mana yang sebenarnya kamu maksud.")
    return pesan


def poly_area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) / 2.0


# Enam tipe bentuk AnyLabeling (shape.py:89-103), beserta jumlah titik minimal
# yang membuatnya sah. Rectangle dan circle disimpan 2 titik di berkas labelme;
# pemekaran jadi poligon hanya untuk menggambar, tidak pernah untuk menyimpan.
JENIS_BENTUK = {
    "polygon": 3, "rectangle": 2, "circle": 2,
    "line": 2, "linestrip": 2, "point": 1,
}
# Jumlah sisi saat lingkaran dijadikan poligon untuk luas dan thumbnail.
SISI_LINGKARAN = 32


def lingkaran_ke_poligon(pusat, tepi, n: int = SISI_LINGKARAN):
    """Circle labelme (titik pusat + satu titik di tepi) -> poligon n sisi."""
    cx, cy = float(pusat[0]), float(pusat[1])
    r = float(np.hypot(float(tepi[0]) - cx, float(tepi[1]) - cy))
    sudut = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return [[cx + r * np.cos(a), cy + r * np.sin(a)] for a in sudut]


def untuk_menggambar(jenis: str, pts: list) -> list:
    """
    Titik apa adanya -> titik untuk digambar/dihitung luasnya.

    Hanya rectangle dan circle yang berubah. Sengaja dipisah dari titik yang
    disimpan: berkas labelme menuntut rectangle tepat 2 titik, dan mencampurkan
    keduanya pernah membuat rectangle buatan desktop tersimpan jadi 4 titik.
    """
    if jenis == "rectangle" and len(pts) == 2:
        (x1, y1), (x2, y2) = pts
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    if jenis == "circle" and len(pts) == 2:
        return lingkaran_ke_poligon(pts[0], pts[1])
    return pts


def read_json(jp: Path):
    """
    Baca anotasi labelme/AnyLabeling.

    `pts` adalah bentuk SIAP GAMBAR (rectangle dan circle sudah dimekarkan),
    sedangkan `pts_asli` mempertahankan titik seperti di berkas. Pembeda itu
    yang menjaga rectangle tetap 2 titik saat disimpan ulang.
    """
    d = json.loads(Path(jp).read_text(encoding="utf-8"))
    W, H = d.get("imageWidth"), d.get("imageHeight")
    shapes = []
    for i, s in enumerate(d.get("shapes", [])):
        pts = s.get("points") or []
        jenis = s.get("shape_type") or "polygon"
        if jenis not in JENIS_BENTUK or len(pts) < JENIS_BENTUK[jenis]:
            continue
        # Cincin dibuka untuk pemakaian di kanvas; berkasnya sendiri tidak
        # diubah, dan penutupannya dipasang lagi saat menulis.
        if jenis == "polygon":
            pts = buka_cincin(pts)
        # `idx` adalah nomor urut bentuk ini DI BERKAS, bukan di daftar hasil.
        # Keduanya berbeda begitu ada satu bentuk yang dilewati di atas, dan
        # pemanggil yang memasangkan bentuk dengan barisan aslinya harus memakai
        # nomor ini. Dulu dipakai nomor urut hasil, sehingga satu bentuk yang
        # dilewati membuat group_id, catatan, dan flag SELURUH bentuk sesudahnya
        # menempel ke objek yang salah.
        shapes.append({"label": s.get("label"), "type": jenis, "idx": i,
                       "pts": np.array(untuk_menggambar(jenis, pts), np.float32),
                       "pts_asli": [[float(x), float(y)] for x, y in pts]})
    return shapes, W, H


def bentuk_terlewat(mentah: dict) -> list[dict]:
    """
    Bentuk di berkas yang TIDAK sampai ke kanvas, apa adanya.

    Bentuk seperti ini tidak bisa digambar (tipenya asing, atau titiknya kurang
    dari minimum tipenya), tetapi ia tetap milik orang yang membuatnya. Tanpa
    dikembalikan saat menyimpan, ia hilang permanen pada penyimpanan pertama
    dari web — padahal pemakainya tidak pernah menyentuhnya.
    """
    out = []
    for s in mentah.get("shapes") or []:
        if not isinstance(s, dict):
            continue
        jenis = s.get("shape_type") or "polygon"
        if jenis not in JENIS_BENTUK or len(s.get("points") or []) < JENIS_BENTUK[jenis]:
            out.append(s)
    return out


def read_yolo(tp: Path, W: int, H: int, names: dict):
    """Baca satu berkas label YOLO, baik format bbox (5 kolom) maupun poligon."""
    shapes = []
    if not os.path.exists(tp):
        return shapes
    for line in Path(tp).read_text().strip().splitlines():
        p = line.split()
        if len(p) < 5:
            continue
        cid = int(float(p[0]))
        v = [float(x) for x in p[1:]]
        if len(v) == 4:
            # Baris 5 kolom = kotak. Dibaca sebagai `rectangle` 2 titik, BUKAN
            # poligon 4 titik: kalau ditandai poligon, menyimpannya kembali akan
            # menulis ulang seluruh berkas dalam format segmentasi dan diam-diam
            # mengubah jenis dataset orang.
            cx, cy, bw, bh = v
            pts = [[(cx - bw / 2) * W, (cy - bh / 2) * H],
                   [(cx + bw / 2) * W, (cy + bh / 2) * H]]
            jenis = "rectangle"
        else:
            pts = [[v[i] * W, v[i + 1] * H] for i in range(0, len(v) - 1, 2)]
            jenis = "polygon"
            if len(pts) < 3:
                continue
            pts = buka_cincin(pts)      # Roboflow menutup cincinnya; kanvas tidak
        shapes.append({"label": names.get(cid, str(cid)), "type": jenis,
                       "pts": np.array(untuk_menggambar(jenis, pts), np.float32),
                       "pts_asli": [[float(x), float(y)] for x, y in pts]})
    return shapes


def format_yolo(tp: Path) -> str:
    """Format berkas label YOLO yang sudah ada: "bbox" | "seg" | "" kalau kosong."""
    try:
        for baris in Path(tp).read_text().splitlines():
            k = baris.split()
            if len(k) >= 5:
                return "bbox" if len(k) == 5 else "seg"
    except OSError:
        pass
    return ""


def _baris_lama_kalau_sama(lama: str | None, baru: str) -> str:
    """
    Kembalikan baris LAMA kalau isinya sama dengan yang baru, jika tidak yang baru.

    "Sama" diukur pada angkanya, bukan teksnya: berkas asli bisa menyimpan
    `0.144853125` sementara kita menulis `0.144853`, dan pada gambar 4000 piksel
    beda itu 0,004 piksel — bukan perubahan anotasi. Yang tidak disunting siapa
    pun sebaiknya tetap tertulis apa adanya.

    Ambangnya 1e-6, yaitu satu satuan pada desimal keenam yang kita tulis.
    Suntingan yang lebih halus dari itu tidak mungkin dilakukan dengan mouse.

    Perbedaan penutupan cincin juga diabaikan: poligon yang sama boleh ditulis
    dengan atau tanpa titik pertama diulang di akhir, dan itu bukan suntingan.
    Tanpa pengecualian ini, menambahkan penutupan cincin akan menulis ulang
    seluruh berkas milik orang lain hanya untuk satu titik yang tidak mengubah
    bentuk apa pun.
    """
    if not lama:
        return baru
    a, b = lama.split(), baru.split()
    if not a or not b or a[0] != b[0]:
        return baru
    try:
        va = [float(x) for x in a[1:]]
        vb = [float(x) for x in b[1:]]
    except ValueError:
        return baru

    def buka(v):
        if len(v) >= 6 and abs(v[0] - v[-2]) <= 1e-6 and abs(v[1] - v[-1]) <= 1e-6:
            return v[:-2]
        return v

    va, vb = buka(va), buka(vb)
    if len(va) == len(vb) and all(abs(x - y) <= 1e-6 for x, y in zip(va, vb)):
        return lama
    return baru


def tulis_yolo(tp: Path, bentuk: list[dict], W: int, H: int,
               indeks: dict) -> tuple[int, list[str]]:
    """
    Tulis anotasi ke berkas label YOLO. -> (jumlah baris, daftar peringatan)

    `bentuk` memakai bentuk labelme (label + shape_type + points piksel).
    `indeks` memetakan nama kelas -> id angka.

    Format dipilih sendiri: begitu ada satu bentuk yang bukan rectangle,
    seluruh berkas ditulis sebagai poligon — menulisnya sebagai bbox akan
    membuang bentuk maskny. Kalau semuanya rectangle, format berkas yang lama
    dipertahankan supaya dataset tidak berganti bentuk tanpa diminta.
    """
    tp = Path(tp)
    peringatan: list[str] = []
    dipakai = [s for s in bentuk if s.get("shape_type") in
               ("polygon", "rectangle", "circle")]
    dilewat = [s for s in bentuk if s not in dipakai]
    if dilewat:
        jenis = sorted({s.get("shape_type", "?") for s in dilewat})
        peringatan.append(
            f"{len(dilewat)} bentuk ({', '.join(jenis)}) tidak muat di format "
            "YOLO — tersimpan di berkas .json saja")

    perlu_seg = any(s.get("shape_type") != "rectangle" for s in dipakai)
    mode = "seg" if perlu_seg else (format_yolo(tp) or "bbox")

    # Baris lama disimpan supaya yang TIDAK berubah bisa ditulis kembali persis
    # seperti aslinya. Tanpa ini, memperbaiki satu objek akan menulis ulang
    # seluruh berkas dengan 6 desimal dan diam-diam memangkas ketelitian objek
    # lain yang tidak disentuh siapa pun.
    try:
        lama = [b for b in tp.read_text().splitlines() if b.strip()]
    except OSError:
        lama = []

    baris = []
    for s in dipakai:
        nama = str(s.get("label", "")).strip()
        if nama not in indeks:
            peringatan.append(f'kelas "{nama}" belum ada di daftar kelas dataset')
            continue
        cid = indeks[nama]
        pts = untuk_menggambar(s.get("shape_type"), s.get("points") or [])
        if len(pts) < 2:
            continue
        pts = rapikan_titik(pts, W, H)
        if len(pts) < (3 if mode == "seg" else 2):
            continue
        if s.get("shape_type") == "polygon" or mode == "seg":
            pts = tutup_cincin(pts)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if mode == "seg":
            if len(pts) < 3:
                continue
            v = " ".join(f"{x / W:.6f} {y / H:.6f}" for x, y in pts)
            b = f"{cid} {v}"
        else:
            x1, x2 = min(xs), max(xs)
            y1, y2 = min(ys), max(ys)
            b = (f"{cid} {(x1 + x2) / 2 / W:.6f} {(y1 + y2) / 2 / H:.6f} "
                 f"{(x2 - x1) / W:.6f} {(y2 - y1) / H:.6f}")
        i = len(baris)
        baris.append(_baris_lama_kalau_sama(lama[i] if i < len(lama) else None, b))

    tp.parent.mkdir(parents=True, exist_ok=True)
    tmp = tp.with_suffix(".txt.tmp")
    tmp.write_text("\n".join(baris) + ("\n" if baris else ""), encoding="utf-8")
    tmp.replace(tp)
    return len(baris), peringatan


def inspect(shapes, W, H, has_ann=False) -> list[str]:
    if not shapes:
        return ["latar (tanpa objek)"] if has_ann else ["belum dilabeli"]
    out, frame = [], (W or 1) * (H or 1)
    for s in shapes:
        if s["label"] is None:
            out.append("label kosong")
        # Bentuk yang memang tidak punya luas (titik, garis, polyline) tidak
        # dinilai dengan ukuran mask — di sana "hanya 2 titik" bukan cacat,
        # itu memang bentuknya.
        berluas = s["type"] in ("polygon", "rectangle", "circle")
        if berluas and len(s["pts"]) < 8 and s["type"] not in ("rectangle", "circle"):
            out.append(f"hanya {len(s['pts'])} titik")
        a = poly_area(s["pts"]) if berluas else 0.0
        if berluas and a / frame < 0.002:
            out.append("mask sangat kecil")
        if berluas and a / frame > 0.92:
            out.append("mask memenuhi frame")
        x, y = s["pts"][:, 0], s["pts"][:, 1]
        if (x < -1).any() or (y < -1).any() or (x > W + 1).any() or (y > H + 1).any():
            out.append("titik di luar gambar")
    return sorted(set(out))


def severity(it: dict) -> str:
    """ok | warn | stop | bg — menentukan warna rel di kartu dan strip atas."""
    if not it["shapes"]:
        return "bg" if "latar (tanpa objek)" in it["issues"] else "stop"
    return "warn" if it["issues"] else "ok"


def _scan_yolo(src: Path, names: dict | None = None):
    items = []
    if names is None:
        names = baca_nama_kelas(src)
    for ip in sorted(p for p in (src / "images").iterdir() if p.suffix.lower() in IMG_EXT):
        d = dimensi(ip)
        if d is None:
            continue
        H, W = d
        tp = src / "labels" / (ip.stem + ".txt")
        sh = read_yolo(tp, W, H, names)
        _gabung_cadangan(ip, sh)
        items.append({"img": ip, "shapes": sh, "W": W, "H": H, "yolo": True,
                      "labels": tp, "ann": tp,
                      "issues": inspect(sh, W, H, tp.exists())})
    return items, names


def _gabung_cadangan(ip: Path, shapes: list[dict]) -> None:
    """
    Ambil kembali hal-hal yang tidak muat di format YOLO dari cadangan .json.

    Format YOLO hanya punya kelas dan koordinat. `group_id`, catatan teks, dan
    flag per-bentuk disimpan aplikasi ini di berkas .json di sebelah gambarnya.
    Geometri TIDAK diambil dari sana: yang menentukan tetap berkas .txt, karena
    itu yang dibaca saat melatih. Cadangan yang jumlah bentuknya sudah tidak
    cocok diabaikan seluruhnya — menebak pasangannya lebih berbahaya daripada
    kehilangan catatan.
    """
    jp = ip.with_suffix(".json")
    if not jp.is_file():
        return
    try:
        d = json.loads(jp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    cad = d.get("shapes") if isinstance(d, dict) else None
    if not isinstance(cad, list) or len(cad) != len(shapes):
        return
    for s, a in zip(shapes, cad):
        if not isinstance(a, dict):
            continue
        s["group_id"] = a.get("group_id")
        s["text"] = a.get("text") or ""
        s["flags"] = a.get("flags") or {}
        s["titipan"] = {k: v for k, v in a.items()
                        if k not in ("label", "text", "points", "group_id",
                                     "shape_type", "flags")}


def _scan_labelme(src: Path):
    items, seen, broken = [], set(), set()
    for jp in sorted(src.rglob("*.json")):
        try:
            sh, W, H = read_json(jp)
        except Exception:
            broken.add(jp.stem)
            continue
        ip = None
        for e in IMG_EXT:
            for c in (jp.with_suffix(e), jp.with_suffix(e.upper())):
                if c.exists():
                    ip = c
                    break
            if ip:
                break
        if not ip:
            continue
        seen.add(ip.resolve())
        items.append({"img": ip, "shapes": sh, "W": W, "H": H, "ann": jp,
                      "issues": inspect(sh, W, H, True)})

    # gambar yang belum punya anotasi sama sekali
    for ip in sorted(p for p in src.rglob("*") if p.suffix.lower() in IMG_EXT):
        if ip.resolve() in seen:
            continue
        d = dimensi(ip)
        if d is None:
            continue
        H, W = d
        iss = ["berkas anotasi rusak"] if ip.stem in broken else ["belum dilabeli"]
        items.append({"img": ip, "shapes": [], "W": W, "H": H,
                      "ann": ip.with_suffix(".json"), "issues": iss})
    # Dataset labelme boleh punya daftar kelas resmi juga (classes.txt atau
    # data.yaml di folder yang sama). Kalau ada, daftar itu yang membuat kanvas
    # bisa menahan salah ketik nama kelas, dan membuat indeks kelas saat
    # mengekspor tetap stabil walau sebagian kelas kebetulan tidak terpakai.
    return items, baca_nama_kelas(src)


def _yolo_disini(d: Path) -> bool:
    return (d / "images").is_dir() and (d / "labels").is_dir()


def split_bersarang(src: Path) -> list[Path]:
    """
    Subfolder split ekspor bersplit, urut train -> valid -> val -> test.

    Sengaja KETAT, karena salah menebak di sini berarti gambar hilang dari
    pandangan tanpa pesan apa pun. Tiga syarat:

      1. `src` sendiri bukan dataset YOLO.
      2. Tidak ada gambar yang tergeletak langsung di `src`.
      3. SETIAP subfolder split yang ditemukan berbentuk YOLO (images/+labels/).

    Syarat ketiga yang membedakan ekspor Roboflow dari folder biasa yang
    kebetulan punya subfolder bernama "test". Dataset labelme bersarang tidak
    perlu jalur ini: pemindai labelme sudah menelusuri subfolder sendiri.
    """
    src = Path(src)
    if _yolo_disini(src):
        return []
    try:
        if any(p.suffix.lower() in IMG_EXT for p in src.iterdir() if p.is_file()):
            return []
    except OSError:
        return []
    out = [src / n for n in SPLIT_DIKENAL if (src / n).is_dir()]
    if not out or not all(_yolo_disini(d) for d in out):
        return []
    return out


def scan(src: Path):
    """
    Pindai folder dataset -> (items, names).

    Ekspor Roboflow ditunjuk di AKARNYA akan dipindai seluruh splitnya
    sekaligus, dan tiap gambar diberi tahu berasal dari split mana. Sebelum
    ini, menunjuk akar membuat seluruh gambar tampak "belum dilabeli" — bukan
    karena anotasinya hilang, melainkan karena `labels/` ada satu tingkat lebih
    dalam dan tidak pernah dicari. Itu jauh lebih menyesatkan daripada sekadar
    tidak menemukan apa-apa.
    """
    src = Path(src)
    splits = split_bersarang(src)
    if splits:
        # Nama kelas dibaca SEKALI di akar, lalu dipakai untuk semua split,
        # supaya id kelas yang sama berarti kelas yang sama di seluruh dataset.
        names = baca_nama_kelas(src)
        items = []
        for d in splits:
            bagian, _ = _scan_yolo(d, names)
            for it in bagian:
                it["split"] = d.name
            items.extend(bagian)
    elif _yolo_disini(src):
        items, names = _scan_yolo(src)
    else:
        items, names = _scan_labelme(src)
    items.sort(key=lambda it: (it.get("split", ""), it["img"].name))
    return items, names


def waktu_label(it: dict) -> float:
    """
    Kapan anotasi gambar ini terakhir ditulis, sebagai detik epoch. 0 kalau
    belum pernah dilabeli.

    Dibaca dari disk SETIAP dipanggil, bukan disimpan saat memindai. Itu
    disengaja: orang melabeli beberapa gambar lalu kembali ke grid untuk melihat
    hasilnya, dan nilai yang dibekukan saat pindai tidak akan berubah sampai
    dipindai ulang — persis membuat urutan "terbaru dilabeli" tidak berguna.
    """
    a = it.get("ann")
    if not a:
        return 0.0
    try:
        return a.stat().st_mtime
    except OSError:
        return 0.0


def count_images(d: Path, cap: int = 2000) -> tuple[int, bool]:
    """
    Hitung gambar di sebuah folder, berhenti di cap. Dipakai halaman pemilih
    dataset; tanpa batas, satu folder raksasa membuat halaman itu lambat.
    """
    n = 0
    for p in d.rglob("*"):
        if p.suffix.lower() in IMG_EXT:
            n += 1
            if n >= cap:
                return n, True
    return n, False


def list_dirs(root: Path | None) -> list[dict]:
    """Subfolder berisi gambar di dalam root, untuk daftar pilihan dataset."""
    if not root or not Path(root).is_dir():
        return []
    out = []
    for d in sorted(Path(root).iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        n, more = count_images(d)
        if n:
            out.append({"nama": d.name, "path": str(d.resolve()),
                        "jumlah": n, "lebih": more})
    return out
