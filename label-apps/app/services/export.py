"""
Ekspor anotasi ke format latih.

Rumusnya diambil baris demi baris dari `utils/export_formats.py` AnyLabeling
(`FormatExporter.export_to_yolo`), termasuk hal-hal kecil yang menentukan
kecocokan berkas:

- Hanya `rectangle` dan `polygon` yang diekspor; tipe lain dilewati.
- Peta kelas dibuat dari label unik yang **diurutkan**, indeks mulai 0.
- Angka ditulis dengan `%.6f`.
- Mode segmentasi: rectangle dijadikan 4 sudut dengan urutan
  kiri-atas, kanan-atas, kanan-bawah, kiri-bawah.
- Mode deteksi: poligon diringkas menjadi bounding box.

Pascal VOC dan COCO juga mengikuti `export_formats.py` baris demi baris,
termasuk hal yang tampak ganjil — lihat catatan pada `_luas_poligon_coco`.

Yang belum: CreateML.
"""
from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
import zipfile
from pathlib import Path
from xml.dom import minidom

from ..log import catat
from . import scanner

log = catat("labelapp.export")

FORMAT = {
    "yolo-seg": "YOLO segmentation (poligon)",
    "yolo": "YOLO detection (bounding box)",
    "yolo-pose": "YOLO pose (keypoint/skeleton)",
    "coco": "COCO (satu instances.json)",
    "voc": "Pascal VOC (satu .xml per gambar)",
    "createml": "CreateML (Apple, satu _annotations.createml.json)",
}

# Versi yang ditulis di berkas keluaran, sama dengan AnyLabeling 0.4.36.
VERSI = "0.4.36"

# Format ekspor KLIP AKSI — SENGAJA dipisah dari FORMAT di atas, bukan ditambahkan
# ke dalamnya.
#
# FORMAT berisi format anotasi GAMBAR (YOLO/COCO/VOC/CreateML). Rute /ekspor dan
# /api/ekspor/ringkasan memakainya sebagai daftar format yang sah, lalu
# zip_dataset membelahnya. Klip aksi bukan salah satunya: ia bukan anotasi
# per-gambar melainkan arsip folder-per-kelas dari sebuah versi yang SUDAH
# dibangun (split + aug dibekukan di waktu build — Langkah 7), jadi tak ada
# peta_kelas / bagi_split / rasio yang berlaku di sini. Menaruhnya di FORMAT
# membuatnya muncul di dropdown ekspor gambar lalu jatuh ke "format tidak
# dikenal" begitu zip_dataset mencoba membelahnya. Karena itu ia konstanta
# tersendiri dengan pintunya sendiri (zip_aksi + rute /api/aksi/versi/unduh).
FORMAT_AKSI = {"aksi-klip": "Klip aksi (folder per kelas, train/valid)"}


def peta_kelas(items: list[dict], names: dict | None = None) -> dict[str, int]:
    """
    Label -> indeks kelas.

    Kalau dataset sumbernya punya daftar kelas sendiri (`names` dari data.yaml
    atau classes.txt), URUTAN ITU YANG DIPAKAI, dan kelas yang kebetulan tidak
    punya objek tetap disertakan.

    Alasannya penting. Dulu indeks selalu diturunkan ulang dari label yang
    ada, diurutkan abjad. Selama seluruh kelas terwakili dan namanya memang
    urut abjad, hasilnya kebetulan sama. Tetapi begitu satu kelas tidak punya
    objek di seleksi yang diekspor — misalnya setelah menyaring grid, atau
    saat mengekspor sebagian — kelas itu hilang dari peta dan SELURUH indeks
    sesudahnya bergeser. Berkasnya tetap konsisten dengan data.yaml barunya,
    sehingga tidak ada yang tampak salah, padahal labelnya tidak lagi cocok
    dengan dataset asal maupun model yang sudah dilatih.
    """
    ada = {str(s["label"]).strip() for it in items for s in it["shapes"]
           if s["label"] is not None and str(s["label"]).strip()}
    if names:
        urut = [str(n).strip() for _, n in sorted(names.items())]
        peta = {n: i for i, n in enumerate(urut)}
        # Label yang muncul di anotasi tetapi tidak ada di daftar resmi
        # ditambahkan di belakang, supaya tidak diam-diam terbuang.
        for l in sorted(ada - set(peta)):
            peta[l] = len(peta)
        return peta
    return {l: i for i, l in enumerate(sorted(ada))}


def _titik_rectangle(pts):
    """2 titik labelme -> 4 sudut: kiri-atas, kanan-atas, kanan-bawah, kiri-bawah."""
    (x1, y1), (x2, y2) = pts[0], pts[1]
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def baris_yolo(it: dict, peta: dict[str, int], segmentasi: bool) -> list[str]:
    """Satu gambar -> baris-baris berkas label YOLO."""
    W, H = it["W"], it["H"]
    if not W or not H:
        return []
    keluar = []
    for s in it["shapes"]:
        if s["type"] not in ("rectangle", "polygon"):
            continue
        label = None if s["label"] is None else str(s["label"]).strip()
        if label not in peta:
            continue
        k = peta[label]
        pts = [(float(x), float(y)) for x, y in s["pts"].tolist()]

        if segmentasi:
            if s["type"] == "rectangle" and len(pts) == 2:
                pts = _titik_rectangle(pts)
            # Kurung ke dalam gambar dan buang kembaran beruntun, lalu tutup
            # cincinnya — ketiganya menyamakan keluaran dengan ekspor Roboflow.
            pts = scanner.rapikan_titik(pts, W, H)
            if len(pts) < 3:
                continue
            pts = scanner.tutup_cincin(pts)
            angka = []
            for x, y in pts:
                angka += [x / W, y / H]
            keluar.append(f"{k} " + " ".join(f"{v:.6f}" for v in angka))
        else:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
            cx = (xmin + xmax) / (2 * W)
            cy = (ymin + ymax) / (2 * H)
            bw = (xmax - xmin) / W
            bh = (ymax - ymin) / H
            keluar.append(f"{k} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
    return keluar


# Pembagian bawaan 80:10:10. Roboflow membiarkan pemakainya menentukan sendiri
# lewat Train/Test Split, dan angka ini yang biasa dipakai di proyek ini.
RASIO_BAWAAN = (0.8, 0.1, 0.1)
SPLIT = ("train", "valid", "test")


def data_yaml(peta: dict[str, int], nama: str) -> str:
    """
    data.yaml dengan bentuk yang sama seperti ekspor Roboflow.

    Disalin dari contoh nyata (sirsak-v13/botol-kaleng-tetra-mlp-cup-1):
    path relatif berawalan `../`, `nc` lalu `names` sebagai daftar rata, dan
    tidak ada kunci `path:`. Ultralytics menerima bentuk ini apa adanya.
    """
    urut = [l for l, _ in sorted(peta.items(), key=lambda kv: kv[1])]
    # Apostrof di dalam kutip tunggal YAML ditulis GANDA — 'bo''tol'. Dulu
    # namanya ditempel apa adanya di antara dua kutip, sehingga satu nama kelas
    # berapostrof merusak seluruh berkas ini: ZIP terunduh mulus, gambar dan
    # labelnya benar, lalu latihan gagal di baris pertama tanpa satu pun tanda
    # dari aplikasi ini.
    #
    # Tetap kutip tunggal, bukan gaya JSON, supaya bentuknya sama persis dengan
    # data.yaml Roboflow yang dipakai sebagai acuan.
    nama_kelas = ", ".join("'" + str(l).replace("'", "''") + "'" for l in urut)
    return ("train: ../train/images\n"
            "val: ../valid/images\n"
            "test: ../test/images\n"
            "\n"
            f"nc: {len(peta)}\n"
            f"names: [{nama_kelas}]\n"
            "\n"
            "labeling-tools:\n"
            f"  dataset: {nama}\n"
            "  format: YOLO segmentation\n")


# ============================================================ YOLO-POSE
# Satu instance keypoint = beberapa shape ber-group_id sama: K titik `point`
# (label = nama keypoint di template) + 1 rectangle (bbox). Ekspor memetakan
# label titik -> slot tetap template, jadi urutan klik/sunting tak pernah
# mengacak slot. Tiap instance SELALU K slot: yang absen/tak terisi di-pad
# `0 0 0` (visibilitas 0), persis Roboflow/CVAT. Lihat invariant di memori.

def _v_shape(s) -> int:
    v = (s.get("flags") or {}).get("v")
    return v if v in (0, 1, 2) else 2


def _instansi_pose(it: dict, slot: dict[str, int]) -> dict:
    """Kelompokkan shape satu gambar per instance: {gid: {rect, kp:{i: shape}}}."""
    grup: dict = {}
    for s in it["shapes"]:
        gid = s.get("group_id")
        if gid is None:
            continue
        inst = grup.setdefault(gid, {"rect": None, "kp": {}})
        if s["type"] == "point":
            nm = None if s["label"] is None else str(s["label"]).strip()
            if nm in slot:
                inst["kp"][slot[nm]] = s
        elif s["type"] == "rectangle":
            inst["rect"] = s
    return grup


def _bbox_pose(inst: dict, kp: dict, W: int, H: int):
    """(cx, cy, w, h) ternormalisasi dari rectangle instance (termasuk
    penyesuaian manual), atau HULL titik v>=1 + padding kalau tak ada rectangle.
    Titik absen tak ikut. Ukuran dijaga minimal supaya tak ada kotak nol-luas."""
    rect = inst.get("rect")
    if rect is not None:
        pts = rect["pts"].tolist()
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    else:
        pts = [kp[i]["pts"].tolist()[0] for i in kp if _v_shape(kp[i]) >= 1]
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
        pad = max(0.01 * max(W, H), (xmax - xmin + ymax - ymin) * 0.04)
        xmin, ymin, xmax, ymax = xmin - pad, ymin - pad, xmax + pad, ymax + pad
    xmin = min(W, max(0, xmin)); xmax = min(W, max(0, xmax))
    ymin = min(H, max(0, ymin)); ymax = min(H, max(0, ymax))
    cx, cy = (xmin + xmax) / (2 * W), (ymin + ymax) / (2 * H)
    bw, bh = max((xmax - xmin) / W, 0.01), max((ymax - ymin) / H, 0.01)
    return (cx, cy, bw, bh)


def baris_yolo_pose(it: dict, template: dict, cls_idx: int = 0) -> list[str]:
    """Satu gambar -> baris YOLO-pose `cls cx cy w h (px py v)*K`, ternormalisasi."""
    W, H = it["W"], it["H"]
    titik = template.get("titik") or []
    K = len(titik)
    if not W or not H or K < 1:
        return []
    slot = {n: i for i, n in enumerate(titik)}
    keluar = []
    for inst in _instansi_pose(it, slot).values():
        kp = inst["kp"]
        if not kp:
            continue
        box = _bbox_pose(inst, kp, W, H)
        if box is None:
            continue
        cx, cy, bw, bh = box
        bagian = [str(cls_idx), f"{cx:.6f}", f"{cy:.6f}", f"{bw:.6f}", f"{bh:.6f}"]
        for i in range(K):
            s = kp.get(i)
            v = _v_shape(s) if s is not None else 0
            if s is None or v == 0:
                bagian += ["0.000000", "0.000000", "0"]
            else:
                px, py = s["pts"].tolist()[0]
                bagian += [f"{px / W:.6f}", f"{py / H:.6f}", str(v)]
        keluar.append(" ".join(bagian))
    return keluar


def data_yaml_pose(template: dict, nama: str) -> str:
    """data.yaml YOLO-pose: kpt_shape [K,3] + flip_idx + satu kelas objek."""
    K = len(template.get("titik") or [])
    flip = list(template.get("flip_idx") or list(range(K)))
    kelas = (template.get("kelas") or "objek").replace("'", "''")
    return ("train: ../train/images\n"
            "val: ../valid/images\n"
            "test: ../test/images\n"
            "\n"
            f"kpt_shape: [{K}, 3]\n"
            f"flip_idx: {flip}\n"
            "\n"
            "nc: 1\n"
            f"names: ['{kelas}']\n"
            "\n"
            "labeling-tools:\n"
            f"  dataset: {nama}\n"
            "  format: YOLO pose\n")


def zip_yolo_pose(items: list[dict], nama_dataset: str, template: dict,
                  sertakan_gambar: bool = True, rasio=RASIO_BAWAAN,
                  rencana: dict | None = None) -> bytes:
    """Dataset keypoint -> ZIP tata-letak YOLO-pose (sama seperti ekspor
    Roboflow court): data.yaml(kpt_shape/flip_idx) + train/valid/test."""
    bagian = bagi_split(items, rasio, rencana)
    buf = io.BytesIO()
    n_inst = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("SPLIT-INFO.txt", catatan_split(
            items, bagian, rencana, nama_dataset, "yolo-pose", rasio))
        for split, daftar in bagian.items():
            dipakai: dict[int, str] = {}
            for it in daftar:
                nm = _nama_unik(dipakai, it["img"].name)
                dipakai[id(it)] = nm
                batang = nm.rpartition(".")[0] or nm
                baris = baris_yolo_pose(it, template)
                n_inst += len(baris)
                z.writestr(f"{split}/labels/{batang}.txt",
                           "\n".join(baris) + ("\n" if baris else ""))
                if sertakan_gambar:
                    z.write(it["img"], f"{split}/images/{nm}")
        for split in SPLIT:
            for sub in ("images", "labels"):
                z.writestr(f"{split}/{sub}/", "")
        z.writestr("data.yaml", data_yaml_pose(template, nama_dataset))
        titik = template.get("titik") or []
        z.writestr("README.txt",
                   f"{nama_dataset}\n{'=' * len(nama_dataset)}\n\n"
                   "Diekspor dari labeling-tools (keypoint/pose).\n"
                   f"Format   : YOLO pose, kpt_shape [{len(titik)}, 3]\n"
                   f"Keypoint : {', '.join(titik)}\n"
                   f"Gambar   : {len(items)}  (train {len(bagian['train'])}, "
                   f"valid {len(bagian['valid'])}, test {len(bagian['test'])})\n"
                   f"Instance : {n_inst}\n")
    return buf.getvalue()


# ============================================================ KLIP AKSI
# Arsip sebuah versi klip aksi yang SUDAH DIBANGUN. Tak ada splitting/augmentasi
# di sini — semuanya sudah dibekukan ke .versi/vN/ oleh klip_olah (Langkah 7).

def zip_aksi(versi_dir) -> bytes:
    """Satu versi klip aksi yang SUDAH DIBANGUN (.versi/vN/) -> ZIP folder-per-kelas.

    Tata letaknya dipertahankan apa adanya dari versi beku itu — inilah arsip
    siap-latih yang dibaca langsung ketiga backend aksi (VideoMAE/SlowFast/
    PoseC3D):

        train/<kelas>/*.mp4     ORI + _aug_ + _bal_
        valid/<kelas>/*.mp4     ORI saja
        aksi.yaml
        MANIFES.json

    Pembelahan anti-bocor PER VIDEO SUMBER dan augmentasinya sudah DIBEKUKAN saat
    versi dibangun (klip_olah); tak ada yang dibelah ulang di sini, jadi tak ada
    cara klip turunan bocor ke valid lewat ekspor. valid/ yang berisi hanya klip
    asli dibawa apa adanya — bukan disaring di sini, melainkan sudah bersih sejak
    dibangun (invarian #2 klip_olah), dan diverifikasi kembali di test_export_aksi.

    Melempar ValueError kalau `versi_dir` bukan versi klip aksi (tak ada, atau
    MANIFES.json-nya ber-jenis != "aksi") — jauh lebih baik daripada diam-diam
    mengirim ZIP yang ternyata bukan dataset aksi.
    """
    vd = Path(versi_dir)
    manifes = vd / "MANIFES.json"
    if not vd.is_dir() or not manifes.is_file():
        raise ValueError(
            "versi klip aksi tak ditemukan — bangun dulu versinya di halaman "
            "Label Aksi sebelum mengunduhnya")
    try:
        man = json.loads(manifes.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("MANIFES.json versi ini rusak atau tak terbaca")
    if man.get("jenis") != "aksi":
        raise ValueError("versi ini bukan dataset klip aksi (MANIFES jenis != 'aksi')")

    buf = io.BytesIO()
    hitung = {"train": 0, "valid": 0}
    # ZIP_STORED, bukan DEFLATE: isinya .mp4 yang sudah terkompres (H.264);
    # mendeflate-nya lagi membakar CPU untuk penyusutan ~0. Berkas teks kecil
    # (aksi.yaml/MANIFES/README) ikut disimpan apa adanya — ukurannya tak berarti.
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for split in ("train", "valid"):
            sdir = vd / split
            if not sdir.is_dir():
                continue
            for p in sorted(sdir.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(vd).as_posix())
                    if p.suffix.lower() == ".mp4":
                        hitung[split] += 1
        for nama in ("aksi.yaml", "MANIFES.json"):
            fp = vd / nama
            if fp.is_file():
                z.write(fp, nama)
        # vd = <projek>/.versi/vN -> induk-induknya adalah folder projek.
        nama_ds = vd.parent.parent.name or "dataset"
        z.writestr("README.txt",
                   f"{nama_ds} — dataset klip aksi\n"
                   f"{'=' * 40}\n\n"
                   "Diekspor dari HIGOLAB (classifier aksi video).\n"
                   "Tata letak folder-per-kelas, dibaca langsung oleh\n"
                   "VideoMAE / SlowFast / PoseC3D:\n\n"
                   "    train/<kelas>/*.mp4   (asli + augmentasi + penyeimbang)\n"
                   "    valid/<kelas>/*.mp4   (asli saja)\n"
                   "    aksi.yaml             (daftar kelas + kelas negatif)\n"
                   "    MANIFES.json          (asal-usul tiap klip keluaran)\n\n"
                   f"Klip : train {hitung['train']}, valid {hitung['valid']}\n\n"
                   "Pembelahan train/valid dibekukan PER VIDEO SUMBER saat versi\n"
                   "dibangun (anti-bocor), dan valid hanya berisi klip asli.\n"
                   "Keduanya tak bisa diubah lewat ekspor ini — resepnya ada di\n"
                   "MANIFES.json.\n")
    return buf.getvalue()


def periksa_rasio(teks: str | None) -> str:
    """
    Keluhan tentang `teks` sebagai rasio, atau "" kalau ia dipakai apa adanya.

    Ada TERPISAH dari baca_rasio dengan sengaja. baca_rasio harus selalu
    mengembalikan rasio yang bisa dipakai — halaman ekspor tidak boleh gagal
    hanya karena satu parameter URL salah ketik. Tetapi "selalu berhasil" dan
    "diam saat gagal" adalah dua hal berbeda, dan menyatukannya itulah yang
    membuat satu bug hidup berbulan-bulan: seluruh rasio bertitik dua jatuh ke
    80/10/10 tanpa satu pun layar menyebutkannya.

    Fungsi ini yang memberi pemanggil bahan untuk MENGATAKANNYA. Rutenya
    meneruskan ke UI, dan UI menampilkannya di sebelah angka yang terpengaruh.
    """
    if not teks:
        return ""                       # kosong = memang minta bawaan
    potong = str(teks).replace("/", ",").replace(":", ",").split(",")
    try:
        angka = [float(x) for x in potong]
    except ValueError:
        return (f'Rasio "{teks}" tidak bisa dibaca, jadi yang dipakai '
                f'{_rasio_teks(RASIO_BAWAAN)}. Tulis tiga angka dipisah koma, '
                f'garis miring, atau titik dua — mis. 70,20,10.')
    if len(angka) != 3:
        return (f'Rasio "{teks}" berisi {len(angka)} angka, bukan tiga, jadi '
                f'yang dipakai {_rasio_teks(RASIO_BAWAAN)}.')
    if any(a < 0 for a in angka):
        return (f'Rasio "{teks}" memuat angka negatif, jadi yang dipakai '
                f'{_rasio_teks(RASIO_BAWAAN)}.')
    if sum(angka) <= 0:
        return (f'Rasio "{teks}" berjumlah nol, jadi yang dipakai '
                f'{_rasio_teks(RASIO_BAWAAN)}.')
    return ""


def _rasio_teks(r) -> str:
    return "/".join(str(int(round(x * 100))) for x in r)


def baca_rasio(teks: str | None) -> tuple[float, float, float]:
    """
    "80,10,10" -> (0.8, 0.1, 0.1). Menerima persen maupun pecahan, dan
    dinormalkan supaya jumlahnya selalu 1 — pemakai mengetik 80/10/10 atau
    70/20/10 tanpa harus pas.

    Tiga pemisah diterima: koma, garis miring, dan TITIK DUA. Titik dua dulu
    tidak dikenali sama sekali, padahal itu notasi yang paling wajar untuk
    rasio dan justru itu yang diajarkan kotak isian halaman Versi yang lama
    (bawaannya "8:1:1", tooltipnya "train:valid:test"). Akibatnya "7:2:1"
    gagal diurai lalu jatuh ke bawaan 80/10/10 tanpa satu pun keterangan —
    dan karena "8:1:1" kebetulan menghasilkan angka yang sama dengan
    bawaannya, kekeliruan itu tidak pernah terlihat.

    Nilai yang tidak bisa diurai tetap jatuh ke bawaan — halaman ekspor tidak
    boleh gagal hanya karena satu parameter URL salah ketik — tetapi sekarang
    dicatat ke log, supaya "kenapa hasilnya 80/10/10 padahal saya minta lain"
    punya jejak.
    """
    if not teks:
        return RASIO_BAWAAN
    try:
        angka = [float(x) for x in
                 str(teks).replace("/", ",").replace(":", ",").split(",")]
    except ValueError:
        log.warning("rasio split tidak bisa diurai (%r), memakai bawaan %s",
                    teks, RASIO_BAWAAN)
        return RASIO_BAWAAN
    if len(angka) != 3 or any(a < 0 for a in angka):
        log.warning("rasio split harus tiga angka tak negatif (%r), "
                    "memakai bawaan %s", teks, RASIO_BAWAAN)
        return RASIO_BAWAAN
    total = sum(angka)
    if total <= 0:
        log.warning("rasio split berjumlah nol (%r), memakai bawaan %s",
                    teks, RASIO_BAWAAN)
        return RASIO_BAWAAN
    return (angka[0] / total, angka[1] / total, angka[2] / total)


# Nama split di dataset sumber -> nama yang kita pakai. `val` dan `valid`
# dua-duanya beredar; data.yaml Roboflow menulis `val:` tapi foldernya `valid`.
PETA_SPLIT = {"train": "train", "valid": "valid", "val": "valid", "test": "test"}

# Augmentasi Roboflow menempel SESUDAH `.rf.<hash>`: satu foto bisa jadi
# `foto_jpg.rf.<hash>_aug1.jpg`, `..._bal4_216.jpg`, `..._p5zoom_normal.jpg`.
# Bagian sampai hash itulah identitas foto aslinya.
_POLA_ASAL = re.compile(r"^(.*\.rf\.[0-9a-f]+)")


def kunci_asal(nama: str) -> str:
    """
    Identitas FOTO ASAL sebuah berkas, supaya augmentasinya tidak terpisah.

    Kalau variasi dari satu foto tersebar ke train dan valid, model dinilai
    memakai versi lain dari gambar yang sudah dia pelajari — angkanya naik
    tanpa kemampuannya bertambah. Pada dataset milik pengguna, membagi per
    nama berkas memecah 54% foto asal seperti itu.
    """
    m = _POLA_ASAL.match(nama)
    return m.group(1) if m else Path(nama).stem


def bagi_split(items: list[dict], rasio=RASIO_BAWAAN,
               rencana: dict | None = None) -> dict[str, list[dict]]:
    """
    Bagi dataset menjadi train / valid / test.

    **Rencana yang sudah dijalankan selalu menang.** Kalau pengguna menekan
    "Jalankan pembelahan", hasilnya dipakai apa adanya — termasuk untuk kelima
    format sekaligus, supaya COCO dan YOLO dari dataset yang sama tidak
    membelah berbeda. Pembelahan cepat di bawah ini hanya cadangan untuk
    ekspor yang dijalankan tanpa menekan tombol itu.

    Dua aturan pada cadangannya, keduanya soal mencegah kebocoran:

    **1. Split yang sudah ada dipertahankan.** Kalau datasetnya memang sudah
    terbagi (mis. ekspor Roboflow dengan train/valid/test), pembagian itu yang
    dipakai. Mengacaknya ulang memindahkan gambar yang tadinya di valid ke
    train, sehingga perbandingan dengan hasil latihan sebelumnya jadi tidak
    berarti.

    **2. Satu foto asal tidak pernah terpecah.** Saat membagi sendiri,
    yang diundi adalah FOTO ASALNYA, bukan tiap berkas — supaya augmentasi
    dari foto yang sama mendarat di split yang sama.

    Undiannya deterministik, diturunkan dari nama, bukan dari pengacak: ekspor
    berapa kali pun memberi pembagian yang sama.
    """
    import hashlib

    # 0. Rencana dari mesin pembelahan penuh (sesi + dHash).
    if rencana and rencana.get("peta"):
        peta = rencana["peta"]
        hasil = {k: [] for k in SPLIT}
        sisa = []
        for it in sorted(items, key=lambda x: x["img"].name):
            s = peta.get(it["img"].name)
            (hasil[s] if s in hasil else sisa).append(it)
        # Gambar yang belum ada saat rencana dibuat tetap harus mendarat di
        # suatu tempat; ia mengikuti aturan cadangan di bawah.
        if sisa:
            for s, daftar in bagi_split(
                    [{**it, "split": None} for it in sisa], rasio).items():
                hasil[s].extend(daftar)
        return hasil

    # 1. Hormati split bawaan dataset kalau ada.
    if any(it.get("split") for it in items):
        hasil: dict[str, list[dict]] = {k: [] for k in SPLIT}
        sisa = []
        for it in sorted(items, key=lambda x: x["img"].name):
            s = PETA_SPLIT.get(str(it.get("split") or "").lower())
            (hasil[s] if s else sisa).append(it)
        # Gambar tanpa asal split yang jelas tetap dibagi, tetapi ikut aturan
        # pengelompokan di bawah.
        if sisa:
            for s, daftar in bagi_split(
                    [{**it, "split": None} for it in sisa], rasio).items():
                hasil[s].extend(daftar)
        return hasil

    # 2. Bagi sendiri, per foto asal.
    batas_train = rasio[0]
    batas_valid = rasio[0] + rasio[1]
    hasil = {k: [] for k in SPLIT}
    for it in sorted(items, key=lambda x: x["img"].name):
        h = hashlib.sha1(kunci_asal(it["img"].name).encode()).digest()
        v = int.from_bytes(h[:4], "big") / 0xFFFFFFFF
        if v < batas_train:
            hasil["train"].append(it)
        elif v < batas_valid:
            hasil["valid"].append(it)
        else:
            hasil["test"].append(it)
    return hasil


def pisah_turunan(items: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """
    (foto asli saja, {"aug": n, "bal": n}) — memisahkan salinan mesin.

    Dipakai unduhan dataset asli. Angka yang dikembalikan BUKAN hiasan: yang
    ditinggalkan di sini bisa puluhan ribu berkas, dan unduhan yang diam-diam
    memuat seperlima isi dataset adalah cara terburuk memberi tahu orang bahwa
    dataset mereka pernah diaugmentasi.
    """
    from .scanner import jenis_turunan

    asli, sisa = [], {"aug": 0, "bal": 0}
    for it in items:
        j = jenis_turunan(it["img"].name)
        if j == "asli":
            asli.append(it)
        else:
            sisa[j] += 1
    return asli, sisa


def zip_asli(items: list[dict], nama_dataset: str, segmentasi: bool = True,
             names: dict | None = None, sisa: dict | None = None) -> bytes:
    """
    Dataset apa adanya -> ZIP DATAR, tanpa split:

        data.yaml
        README.txt
        images/   labels/

    Ini bentuk yang bisa diunggah kembali ke HIGOLAB apa adanya: scanner
    mengenali sebuah folder sebagai dataset YOLO begitu ia punya `images/` dan
    `labels/` bersebelahan, dan `data.yaml` di sampingnya yang membuat kelasnya
    terbaca sebagai nama, bukan sebagai angka 0/1/2.

    Bedanya dengan ekspor Versi disengaja dan menyeluruh. Di sana yang diunduh
    adalah HASIL sebuah resep — sudah dibelah train/valid/test, sudah
    dipreprocessing, sudah diaugmentasi — dan bentuk itu tidak bisa diunggah
    balik tanpa menjadi dataset yang berbeda dari yang dimulai. Di sini tidak
    ada satu pun keputusan yang dibekukan ke dalam ZIP-nya.
    """
    peta = peta_kelas(items, names)
    buf = io.BytesIO()
    n_objek = 0
    dipakai: dict[int, str] = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for it in items:
            p: Path = it["img"]
            # Dataset bersplit yang diratakan bisa punya dua `img-01.jpg` dari
            # folder berbeda. Tanpa penomoran ini yang kedua menimpa yang
            # pertama di dalam ZIP, dan yang hilang tidak disebut di mana pun.
            nama = _nama_unik(dipakai, p.name)
            dipakai[id(it)] = nama
            batang = nama.rpartition(".")[0] or nama
            baris = baris_yolo(it, peta, segmentasi)
            n_objek += len(baris)
            # Gambar tanpa objek tetap dapat berkas label kosong: itu penanda
            # contoh negatif yang sah, bukan label yang lupa ditulis.
            z.writestr(f"labels/{batang}.txt",
                       "\n".join(baris) + ("\n" if baris else ""))
            z.write(p, f"images/{nama}")
        for sub in ("images", "labels"):
            z.writestr(f"{sub}/", "")
        # `train:`/`val:` di data.yaml menunjuk ../train/images yang memang
        # tidak ada di sini — itu sebabnya yang ditulis bentuk datarnya.
        urut = [l for l, _ in sorted(peta.items(), key=lambda kv: kv[1])]
        nk = ", ".join("'" + str(k).replace("'", "''") + "'" for k in urut)
        z.writestr("data.yaml",
                   "train: ../images\n"
                   "val: ../images\n"
                   "\n"
                   f"nc: {len(peta)}\n"
                   f"names: [{nk}]\n"
                   "\n"
                   "labeling-tools:\n"
                   f"  dataset: {nama_dataset}\n"
                   "  format: YOLO segmentation (datar, tanpa split)\n")
        sisa = sisa or {}
        catatan = ""
        if sisa.get("aug") or sisa.get("bal"):
            catatan = (
                "\nTIDAK ikut diunduh\n"
                "-------------------\n"
                f"Hasil augmentasi   : {sisa.get('aug', 0)}\n"
                f"Hasil penyeimbangan: {sisa.get('bal', 0)}\n"
                "Keduanya salinan yang dibuat mesin dari foto di atas, dan\n"
                "bisa dibuat ulang kapan saja lewat halaman Versi. Yang ada di\n"
                "ZIP ini hanya foto yang benar-benar dipotret.\n")
        z.writestr("README.txt",
                   f"{nama_dataset}\n{'=' * len(nama_dataset)}\n\n"
                   "Dataset asli, diekspor dari HIGOLAB.\n"
                   "Tata letaknya DATAR: images/ dan labels/ bersebelahan,\n"
                   "tanpa train/valid/test. Folder ini bisa diunggah kembali\n"
                   "ke HIGOLAB apa adanya.\n\n"
                   f"Gambar : {len(items)}\n"
                   f"Objek  : {n_objek}\n"
                   f"Kelas  : {len(peta)}  {', '.join(urut)}\n"
                   + catatan +
                   "\nPembagian train/valid/test, preprocessing dan augmentasi\n"
                   "TIDAK dibekukan ke dalam ZIP ini. Ketiganya ada di halaman\n"
                   "Versi, dan tetap bisa dipilih ulang sesudah diunggah.\n")
    return buf.getvalue()


def zip_yolo(items: list[dict], nama_dataset: str, segmentasi: bool,
             sertakan_gambar: bool = True, rasio=RASIO_BAWAAN,
             names: dict | None = None, rencana: dict | None = None) -> bytes:
    """
    Seluruh dataset -> ZIP dengan tata letak yang **sama seperti ekspor
    Roboflow**, supaya bisa langsung dilatih tanpa dirapikan lagi:

        data.yaml
        README.txt
        train/images/  train/labels/
        valid/images/  valid/labels/
        test/images/   test/labels/

    Gambar tanpa objek tetap mendapat berkas label kosong — itu penanda contoh
    negatif yang sah di YOLO, bukan kelalaian.
    """
    peta = peta_kelas(items, names)
    bagian = bagi_split(items, rasio, rencana)
    buf = io.BytesIO()
    n_objek = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("SPLIT-INFO.txt", catatan_split(
            items, bagian, rencana, nama_dataset,
            "yolo-seg" if segmentasi else "yolo", rasio))
        for split, daftar in bagian.items():
            # Dua gambar bernama sama dari subfolder berbeda bertemu di satu
            # folder setelah diratakan. COCO, VOC, dan CreateML sudah memakai
            # _nama_unik; jalur YOLO tidak, dan akibatnya entri ZIP-nya ganda:
            # ringkasan menyebut 3 gambar, yang terekstrak 2, dan satu kelas
            # ikut hilang tanpa satu pun pesan.
            dipakai: dict[int, str] = {}
            for it in daftar:
                p: Path = it["img"]
                nama = _nama_unik(dipakai, p.name)
                dipakai[id(it)] = nama
                batang = nama.rpartition(".")[0] or nama
                baris = baris_yolo(it, peta, segmentasi)
                n_objek += len(baris)
                z.writestr(f"{split}/labels/{batang}.txt",
                           "\n".join(baris) + ("\n" if baris else ""))
                if sertakan_gambar:
                    z.write(p, f"{split}/images/{nama}")
        # Ketiga folder selalu dibuat, walau salah satu split kosong: data.yaml
        # menunjuk ../test/images, dan folder yang tidak ada membuat perkakas
        # latih mengeluh tentang path yang hilang. Pada dataset kecil, split
        # test memang bisa kebagian nol gambar.
        for split in SPLIT:
            for sub in ("images", "labels"):
                z.writestr(f"{split}/{sub}/", "")
        z.writestr("data.yaml", data_yaml(peta, nama_dataset))
        urut = [l for l, _ in sorted(peta.items(), key=lambda kv: kv[1])]
        z.writestr("README.txt",
                   f"{nama_dataset}\n{'=' * len(nama_dataset)}\n\n"
                   "Diekspor dari labeling-tools, pengembangan dari AnyLabeling.\n"
                   f"Format   : YOLO {'segmentation' if segmentasi else 'detection'}\n"
                   f"Gambar   : {len(items)}"
                   f"  (train {len(bagian['train'])}, valid {len(bagian['valid'])},"
                   f" test {len(bagian['test'])})\n"
                   f"Objek    : {n_objek}\n"
                   f"Kelas    : {len(peta)}  {', '.join(urut)}\n\n"
                   "Pembagian train/valid/test deterministik, diturunkan dari nama\n"
                   "berkas. Ekspor berapa kali pun memberi pembagian yang sama, jadi\n"
                   "tidak ada gambar latih yang berpindah ke validasi.\n")
    return buf.getvalue()


def ringkasan(items: list[dict], segmentasi: bool, rasio=RASIO_BAWAAN,
              names: dict | None = None, rencana: dict | None = None) -> dict:
    """Angka untuk ditampilkan sebelum orang menekan unduh, termasuk jumlah
    gambar per split — seperti yang Roboflow tampilkan di Train/Test Split."""
    peta = peta_kelas(items, names)
    bagian = bagi_split(items, rasio, rencana)
    n_objek = sum(len(baris_yolo(it, peta, segmentasi)) for it in items)
    n_kosong = sum(1 for it in items if not baris_yolo(it, peta, segmentasi))
    # Dua sebab yang berbeda dilebur jadi satu angka: gambar yang SENGAJA
    # ditandai latar, dan gambar yang belum disentuh siapa pun. Keduanya
    # terekspor sebagai berkas label kosong dan tidak bisa dibedakan lagi
    # sesudahnya — jadi kelalaian memasukkan gambar yang belum dikerjakan
    # diam-diam berubah jadi sampel negatif. Dipisah di sini, sebelum
    # ZIP-nya diunduh.
    n_latar = sum(1 for it in items
                  if not baris_yolo(it, peta, segmentasi)
                  and "latar (tanpa objek)" in it.get("issues", []))
    dilewati = sum(1 for it in items for s in it["shapes"]
                   if s["type"] not in ("rectangle", "polygon"))
    # Dari mana pembagiannya datang perlu terlihat: rasio yang diketik orang
    # tidak berlaku kalau datasetnya sudah punya split sendiri, dan diam-diam
    # mengabaikan angka yang mereka ketik itu membingungkan.
    bawaan = any(it.get("split") for it in items)
    # Pada dataset kecil, pembelahan CEPAT (deterministik dari nama, tanpa
    # rencana anti-bocor dan tanpa split bawaan) bisa meninggalkan valid KOSONG:
    # semua gambar jatuh ke train. Model lalu dilatih tanpa satu pun gambar
    # validasi, dan tidak ada apa pun di panel yang mengatakannya kecuali angka
    # "valid 0" yang mudah terlewat. Split bawaan dan rencana adalah pilihan
    # sadar orang, bukan kecelakaan ukuran, jadi tidak diperingatkan. Test
    # kosong memang lumrah di dataset kecil (lihat zip_yolo) — yang mematikan
    # evaluasi cuma valid.
    cepat = not bawaan and not (rencana and rencana.get("peta"))
    valid_kosong = cepat and len(items) > 0 and len(bagian["valid"]) == 0
    return {"gambar": len(items), "objek": n_objek, "kelas": len(peta),
            "nama_kelas": [l for l, _ in sorted(peta.items(), key=lambda kv: kv[1])],
            "tanpa_objek": n_kosong, "latar": n_latar,
            "belum_dilabeli": n_kosong - n_latar,
            "bentuk_dilewati": dilewati,
            "split": {k: len(v) for k, v in bagian.items()},
            "split_bawaan": bawaan,
            "valid_kosong": valid_kosong,
            "rasio": [round(r * 100) for r in rasio],
            # Persentase yang benar-benar tercapai. Berbeda sedikit dari rasio
            # yang diminta karena pembagiannya deterministik dari nama berkas,
            # bukan memotong daftar pada posisi tertentu.
            "persen": {k: (round(100 * len(v) / len(items), 1) if items else 0.0)
                       for k, v in bagian.items()}}


# ---------------------------------------------------------------- Pascal VOC

def voc_xml(it: dict, split: str = "", berkas: str = "") -> str:
    """
    Satu gambar -> XML Pascal VOC.

    Struktur, urutan elemen, dan `toprettyxml(indent="  ")` mengikuti
    `export_to_pascal_voc`. Koordinat ditulis `str(int(...))` — dipotong, bukan
    dibulatkan, sama seperti di sana.

    `folder` dan `path` menunjuk LETAK DI DALAM ZIP, bukan letak berkasnya di
    server. Menulis path absolut server ke dalam berkas yang diunduh orang lain
    bukan cuma tidak berguna — ia membocorkan susunan folder mesin ini, dan
    membuat XML-nya tidak bisa dipakai di komputer mana pun selain di sini.
    """
    p: Path = it["img"]
    nama_berkas = berkas or p.name
    # Tanpa split (dipakai langsung, di luar jalur ZIP), dipakai NAMA folder
    # induknya — tetap relatif dan tetap memberi tahu asalnya, tanpa membocorkan
    # susunan folder server.
    folder = split or p.parent.name
    ann = ET.Element("annotation")
    ET.SubElement(ann, "folder").text = folder
    ET.SubElement(ann, "filename").text = nama_berkas
    ET.SubElement(ann, "path").text = f"{folder}/{nama_berkas}" if folder else nama_berkas
    ET.SubElement(ET.SubElement(ann, "source"), "database").text = "Unknown"
    size = ET.SubElement(ann, "size")
    ET.SubElement(size, "width").text = str(it["W"])
    ET.SubElement(size, "height").text = str(it["H"])
    ET.SubElement(size, "depth").text = "3"
    ET.SubElement(ann, "segmented").text = "0"

    for s in it["shapes"]:
        if s["type"] not in ("rectangle", "polygon"):
            continue
        obj = ET.SubElement(ann, "object")
        ET.SubElement(obj, "name").text = "" if s["label"] is None else str(s["label"])
        ET.SubElement(obj, "pose").text = "Unspecified"
        ET.SubElement(obj, "truncated").text = "0"
        ET.SubElement(obj, "difficult").text = "0"
        pts = s["pts"].tolist()
        xs = [q[0] for q in pts]
        ys = [q[1] for q in pts]
        bnd = ET.SubElement(obj, "bndbox")
        ET.SubElement(bnd, "xmin").text = str(int(min(xs)))
        ET.SubElement(bnd, "ymin").text = str(int(min(ys)))
        ET.SubElement(bnd, "xmax").text = str(int(max(xs)))
        ET.SubElement(bnd, "ymax").text = str(int(max(ys)))

    return minidom.parseString(ET.tostring(ann, encoding="utf-8")).toprettyxml(indent="  ")


# ---------------------------------------------------------------- COCO

def _luas_poligon_coco(pts) -> float:
    """
    Luas poligon dengan shoelace — abs() DI LUAR penjumlahan.

    Ini sengaja BERBEDA dari AnyLabeling, yang menaruh abs() di dalam:

        area += 0.5 * abs(x1*y2 - x2*y1)        # AnyLabeling, keliru

    Shoelace bekerja justru karena suku-sukunya saling meniadakan; mengambil
    nilai mutlak per suku merusak itu. Yang tersisa bukan luas poligon,
    melainkan jumlah luas segitiga tiap sisi dengan titik-asal (0,0) — jadi
    nilainya ikut membesar hanya karena objeknya jauh dari pojok kiri-atas.
    Kotak 10x10 yang sama menghasilkan 100 di titik-asal, 2100 di (100,100),
    dan 8100 di (500,300). Luas tidak boleh bergantung pada posisi.

    Kenapa tidak ditiru demi kompatibilitas: tidak ada yang membaca nilai itu
    dengan mengharapkan bug-nya. pycocotools memakai `area` untuk memilah objek
    small/medium/large pada ambang 32^2 dan 96^2, sehingga nilai yang membengkak
    puluhan kali mendorong hampir semua objek ke bucket "large" dan membuat
    AP_small serta AP_medium tidak bermakna. Pelatihan YOLO tidak membaca field
    ini sama sekali.
    """
    jumlah = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        jumlah += x1 * y2 - x2 * y1
    return abs(jumlah) / 2.0


def coco_dict(items: list[dict], nama_dataset: str,
              names: dict | None = None, kelas: dict | None = None) -> dict:
    """
    Satu bagian dataset -> satu dict COCO.

    `kelas` adalah peta label -> indeks yang dihitung SEKALI untuk seluruh
    dataset, lalu dipakai sama persis di tiap split. Dua cacat sekaligus
    tertutup karenanya, dan keduanya juga ada di AnyLabeling
    (export_formats.py:249-255) sehingga sengaja tidak ditiru:

      1. Kategori dulu diturunkan dari label yang kebetulan ada DI SPLIT ITU,
         padahal `export_to_coco` dipanggil per split. Akibatnya `category_id`
         yang sama berarti kelas yang berbeda di train/ dan valid/, dan dataset
         hasil ekspor tidak bisa digabungkan lagi.
      2. Kategori dulu dikumpulkan dari SELURUH bentuk tanpa menyaring tipenya,
         padahal anotasinya hanya ditulis untuk rectangle dan polygon. Satu
         kelas yang cuma dipakai pada `point` atau `line` karena itu menempati
         satu id dan menggeser seluruh id sesudahnya.

    Parameter `names` dulu diterima tetapi tidak pernah dipakai di sini — jalur
    YOLO sudah memakainya lewat peta_kelas, COCO tidak ikut.
    """
    peta_idx = kelas if kelas is not None else peta_kelas(items, names)
    kategori = [{"id": i + 1, "name": l, "supercategory": "none"}
                for l, i in sorted(peta_idx.items(), key=lambda kv: kv[1])]
    peta = {c["name"]: c["id"] for c in kategori}

    d = {
        "info": {
            "description": f"Dataset {nama_dataset} diekspor dari labeling-tools"
                           " (pengembangan AnyLabeling)",
            "url": "", "version": VERSI, "year": 2023,
            "contributor": "labeling-tools", "date_created": "",
        },
        "licenses": [{"id": 1, "name": "Unknown", "url": ""}],
        "images": [], "annotations": [], "categories": kategori,
    }

    id_anotasi = 1
    for i, it in enumerate(items):
        id_gambar = i + 1
        d["images"].append({
            "id": id_gambar, "file_name": it["img"].name,
            "width": it["W"], "height": it["H"],
            "license": 1, "date_captured": "",
        })
        for s in it["shapes"]:
            if s["type"] not in ("rectangle", "polygon"):
                continue
            pts = [(float(x), float(y)) for x, y in s["pts"].tolist()]
            xs = [q[0] for q in pts]
            ys = [q[1] for q in pts]
            xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)

            if s["type"] == "rectangle":
                w, h = xmax - xmin, ymax - ymin
                segmentasi = [[xmin, ymin, xmax, ymin, xmax, ymax, xmin, ymax]]
                luas = w * h
                bbox = [xmin, ymin, w, h]
            else:
                segmentasi = [[k for q in pts for k in q]]
                luas = _luas_poligon_coco(pts)
                bbox = [xmin, ymin, xmax - xmin, ymax - ymin]

            nama_kelas = str(s["label"]).strip() if s["label"] is not None else ""
            if nama_kelas not in peta:
                continue          # bentuk tanpa kelas tidak punya kategori
            d["annotations"].append({
                "id": id_anotasi, "image_id": id_gambar,
                "category_id": peta[nama_kelas],
                "segmentation": segmentasi, "area": luas,
                "bbox": bbox, "iscrowd": 0,
            })
            id_anotasi += 1
    return d


def createml_list(items: list[dict], nama_berkas: dict | None = None) -> list[dict]:
    """
    Satu bagian dataset -> daftar CreateML (Apple Create ML object detection).

    Bentuknya: satu entri per gambar, `annotations` berisi kotak dengan
    `coordinates` = {x, y, width, height}.

    **x dan y adalah TITIK TENGAH kotak, bukan sudut kiri-atas.** Ini satu-satunya
    tempat kita sengaja menyimpang dari AnyLabeling, dan alasannya bukan selera:
    `export_to_createml` di sana (export_formats.py:400-434) menulis `x_min` dan
    `y_min`, sehingga setiap kotak yang dibaca Create ML bergeser setengah lebar
    ke kiri dan setengah tinggi ke atas. Berkasnya tetap termuat tanpa galat —
    yang salah cuma letak seluruh kotaknya, dan itu baru ketahuan setelah model
    dilatih dan hasilnya meleset.

    Seperti COCO dan VOC, hanya rectangle dan polygon yang punya arti di sini;
    poligon diringkas jadi kotak pembungkusnya.
    """
    out = []
    for it in items:
        nama = (nama_berkas or {}).get(id(it), it["img"].name)
        anotasi = []
        for s in it["shapes"]:
            if s["type"] not in ("rectangle", "polygon"):
                continue
            label = "" if s["label"] is None else str(s["label"]).strip()
            if not label:
                continue
            pts = s["pts"].tolist()
            xs = [q[0] for q in pts]
            ys = [q[1] for q in pts]
            xmin, xmax = min(xs), max(xs)
            ymin, ymax = min(ys), max(ys)
            lebar, tinggi = xmax - xmin, ymax - ymin
            anotasi.append({
                "label": label,
                "coordinates": {
                    "x": xmin + lebar / 2, "y": ymin + tinggi / 2,
                    "width": lebar, "height": tinggi,
                },
            })
        out.append({"image": nama, "annotations": anotasi})
    return out


# ---------------------------------------------------------------- arsip

def catatan_split(items: list[dict], bagian: dict, rencana: dict | None,
                  nama: str, format: str, rasio) -> str:
    """
    Isi berkas SPLIT-INFO.txt yang ikut ke dalam ZIP.

    ZIP yang sudah tersimpan berbulan-bulan tidak menyisakan jejak APA PUN
    tentang cara membelahnya. Setahun lagi, melihat dua berkas ZIP dari
    dataset yang sama, tidak ada cara membedakan mana yang dibelah anti-bocor
    dan mana yang sekadar per nama berkas kecuali menebak dari tanggalnya.

    Karena itu keterangannya ikut masuk ke dalam ZIP, bukan cuma tampil di
    layar saat mengunduh.
    """
    b = [f"Dataset : {nama}",
         f"Format  : {FORMAT.get(format, format)}",
         f"Diminta : {' : '.join(f'{x:.0%}'[:-1] for x in rasio)}", ""]
    tot = sum(len(v) for v in bagian.values()) or 1
    for s in SPLIT:
        n = len(bagian[s])
        kc = Counter()
        for it in bagian[s]:
            kc.update(str(x.get("label")) for x in it.get("shapes") or [])
        b.append(f"{s:6s} {n:7,} gambar ({100.0 * n / tot:5.1f}%)  "
                 f"{sum(kc.values()):7,} objek")
    b.append("")

    if not rencana:
        b += ["SPLITTING: cepat, berbasis nama berkas.", "",
              "Isi gambar TIDAK diperiksa. Dua foto yang sama bisa berada di",
              "train dan valid sekaligus, dan angka validasi dari dataset ini",
              "bisa lebih tinggi daripada kemampuan model yang sebenarnya."]
        return "\n".join(b) + "\n"

    r = rencana
    # Rencana yang hanya membawa peta: pembagiannya dibekukan sebuah versi,
    # bukan dihitung ulang di sini. Angka sesi, ambang, dan kemandirian memang
    # tidak ada, dan mencoba mencetaknya membuat seluruh ekspor gagal.
    if not r.get("n_sesi"):
        b += ["SPLITTING: dibekukan dari versi"
              + (f" v{r['versi']}." if r.get("versi") else "."), "",
              "Gambar mana masuk split mana diambil apa adanya dari versi itu,",
              "jadi ekspor ini berisi pembagian yang sama persis dengan saat",
              "versinya dibuat, walau dataset sudah bertambah sesudahnya."]
        return "\n".join(b) + "\n"

    k = r.get("kalibrasi") or {}
    m = r.get("kemandirian") or {}
    b += ["SPLITTING: anti-bocor (per sesi pemotretan + pemeriksaan isi gambar).",
          "",
          f"Sesi terdeteksi   : {r.get('n_sesi'):,} (kunci per-{r.get('granularitas')})",
          f"Sesi terbesar     : {r.get('grup_terbesar'):,} gambar "
          f"({r.get('grup_terbesar_pct', 0):.1f}%)",
          f"Tanpa stempel wkt : {r.get('tanpa_stempel', 0):,}",
          f"Ambang kemiripan  : {r.get('ambang')} dari 256 bit"
          + (f" (diukur dari {k.get('contoh')} foto dataset ini)" if k else ""),
          f"Dipindah ke train : valid {r.get('dipindah', {}).get('valid', 0):,}, "
          f"test {r.get('dipindah', {}).get('test', 0):,}", ""]
    for s in ("valid", "test"):
        d = m.get(s) or {}
        if d.get("kemandirian") is not None:
            b.append(f"{s:6s} kemandirian {d['kemandirian']:.2f}x, "
                     f"dari {d.get('n_sesi', 0):,} sesi pemotretan")
    if r.get("peringatan"):
        b += ["", f"CATATAN ({len(r['peringatan'])}):"]
        b += [f"  {i}. {w}" for i, w in enumerate(r["peringatan"], 1)]
    return "\n".join(b) + "\n"


def tambah_tags_csv(data: bytes, tag_peta: dict) -> bytes:
    """
    Sisipkan tags.csv ke dalam ZIP yang sudah jadi.

    Ditempelkan sesudahnya, bukan di dalam tiap penulis format, karena tidak
    satu pun format dataset punya tempat untuk tag: YOLO cuma angka kelas dan
    koordinat, VOC per-berkas tanpa medan bebas. Menaruhnya sebagai berkas
    terpisah membuat ia terbaca alat apa pun tanpa mengubah format datanya.

    Pemisah antar-tag titik koma, dan itu sebabnya koma maupun titik koma
    dibuang dari nama tag sejak ia diketik (tag.bersihkan_tag).
    """
    import csv
    import io as _io

    if not tag_peta:
        return data
    baris = _io.StringIO()
    w = csv.writer(baris, lineterminator="\n")
    w.writerow(["berkas", "tag", "unggahan"])
    for nama in sorted(tag_peta):
        r = tag_peta[nama]
        w.writerow([nama, ";".join(r.get("tag") or []), r.get("batch") or ""])

    buf = _io.BytesIO(data)
    with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr("tags.csv", baris.getvalue())
    return buf.getvalue()


def zip_dataset(items: list[dict], nama: str, format: str,
                sertakan_gambar: bool = True, rasio=RASIO_BAWAAN,
                names: dict | None = None, rencana: dict | None = None,
                tag_peta: dict | None = None, skeleton: dict | None = None) -> bytes:
    """Satu pintu untuk semua format."""
    if format in ("yolo", "yolo-seg"):
        # Lewat kata kunci, bukan posisi: urutan parameter kedua fungsi ini
        # tidak sama, dan pemanggilan posisional akan menyerahkan `names`
        # sebagai `rencana` tanpa satu pun kesalahan yang terlihat.
        return tambah_tags_csv(zip_yolo(items, nama, format == "yolo-seg", sertakan_gambar,
                        rasio, names=names, rencana=rencana), tag_peta)

    if format == "yolo-pose":
        if not (skeleton and skeleton.get("titik")):
            raise ValueError("projek ini belum punya template keypoint untuk ekspor pose")
        return tambah_tags_csv(zip_yolo_pose(items, nama, skeleton, sertakan_gambar,
                        rasio, rencana=rencana), tag_peta)

    if format not in ("coco", "voc", "createml"):
        raise ValueError(f"format '{format}' tidak dikenal")

    bagian = bagi_split(items, rasio, rencana)
    # Dihitung SEKALI untuk seluruh dataset, lalu dipakai sama di tiap split.
    kelas = peta_kelas(items, names)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("SPLIT-INFO.txt", catatan_split(
            items, bagian, rencana, nama, format, rasio))
        for split in SPLIT:
            z.writestr(f"{split}/", "")
        for split, daftar in bagian.items():
            # Gambar mendarat SEJAJAR dengan berkas anotasinya, tidak di dalam
            # subfolder images/. Ini bukan selera tata letak: `file_name` di COCO
            # dan `filename` di VOC berisi nama berkas saja, dan keduanya
            # diselesaikan relatif terhadap letak berkas anotasi. Dengan gambar
            # di `train/images/` sementara JSON-nya di `train/`, loader COCO mana
            # pun mencari `train/xxx.jpg` yang tidak ada — ekspornya tidak bisa
            # dipakai sama sekali. AnyLabeling menaruh keduanya di satu folder
            # (export_worker.py:417-426), begitu juga Roboflow.
            nama_dipakai = {}
            for it in daftar:
                nama_dipakai[id(it)] = _nama_unik(nama_dipakai, it["img"].name)
            if format == "coco":
                d = coco_dict(daftar, nama, names, kelas)
                # `file_name` harus memakai nama yang benar-benar ditulis ke ZIP.
                for g, it in zip(d["images"], daftar):
                    g["file_name"] = nama_dipakai[id(it)]
                z.writestr(f"{split}/_annotations.coco.json",
                           json.dumps(d, indent=2))
            elif format == "createml":
                z.writestr(f"{split}/_annotations.createml.json",
                           json.dumps(createml_list(daftar, nama_dipakai), indent=2))
            else:
                for it in daftar:
                    berkas = nama_dipakai[id(it)]
                    z.writestr(f"{split}/{Path(berkas).stem}.xml",
                               voc_xml(it, split, berkas))
            if sertakan_gambar:
                for it in daftar:
                    z.write(it["img"], f"{split}/{nama_dipakai[id(it)]}")
    return tambah_tags_csv(buf.getvalue(), tag_peta)


def _nama_unik(sudah: dict, nama: str) -> str:
    """
    Nama berkas yang belum terpakai di split ini.

    Dua gambar bernama sama dari subfolder berbeda akan bertemu di satu folder
    setelah diratakan. Menimpanya berarti satu gambar hilang dan satu baris
    anotasi menunjuk gambar yang salah — keduanya tanpa pesan apa pun.
    """
    terpakai = set(sudah.values())
    if nama not in terpakai:
        return nama
    batang, titik, ekor = nama.rpartition(".")
    if not titik:
        batang, ekor = nama, ""
    for i in range(2, 100000):
        calon = f"{batang}-{i}" + (f".{ekor}" if ekor else "")
        if calon not in terpakai:
            return calon
    raise ValueError("terlalu banyak berkas senama dalam satu split")
