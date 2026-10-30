"""
Menambah gambar ke dataset yang sedang dibuka.

Berbeda dengan impor, yang membuat dataset baru. Di sini berkasnya menyatu ke
dataset yang sudah ada, dan itu memunculkan dua pertanyaan yang tidak muncul
saat membuat dataset baru: ke split mana gambar barunya, dan apa yang terjadi
kalau namanya sudah terpakai.

**Tidak ada berkas yang pernah ditimpa.** Nama yang bentrok diperiksa isinya
lebih dulu: isi yang sama persis berarti gambar itu memang sudah ada, jadi
dilewati; isi yang berbeda berarti gambar lain yang kebetulan senama, jadi
tetap masuk dengan akhiran. Menambah tidak boleh berarti mengganti.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..config import ANN_EXT, IMG_EXT
from ..services import scanner

# Tata letak dataset menentukan ke mana berkas baru mendarat. Salah menaruh
# berarti gambarnya tidak pernah terbaca pemindai — hilang tanpa pesan.
TATA_SPLIT = "split"      # <split>/images/ + <split>/labels/  (ekspor Roboflow)
TATA_YOLO = "yolo"        # images/ + labels/ di akar
TATA_DATAR = "datar"      # gambar dan .json bersebelahan (labelme)

POTONG_BACA = 1024 * 1024


def tata_letak(src: Path) -> str:
    if scanner.split_bersarang(src):
        return TATA_SPLIT
    if (src / "images").is_dir() and (src / "labels").is_dir():
        return TATA_YOLO
    return TATA_DATAR


def _sidik(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while blok := f.read(POTONG_BACA):
            h.update(blok)
    return h.hexdigest()


def sama_isi(a: Path, b: Path) -> bool:
    """Ukurannya dibandingkan lebih dulu: berkas yang beda ukuran pasti beda
    isi, dan pemeriksaan itu jauh lebih murah daripada membaca keduanya."""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False
    return _sidik(a) == _sidik(b)


def _nama_bebas(dest: Path) -> Path:
    """Cari nama yang belum terpakai dengan akhiran -2, -3, ..."""
    for i in range(2, 1000):
        calon = dest.with_name(f"{dest.stem}-{i}{dest.suffix}")
        if not calon.exists():
            return calon
    raise OSError("terlalu banyak berkas senama")


def pasang(tmp: Path, dest: Path) -> tuple[Path | None, str]:
    """
    Pindahkan berkas sementara ke tempatnya. -> (path akhir, keterangan)

    Keterangan: "baru", "sudah-ada" (dilewati karena isinya identik), atau
    "senama" (isinya beda, disimpan dengan akhiran).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        tmp.replace(dest)
        return dest, "baru"
    if sama_isi(tmp, dest):
        tmp.unlink(missing_ok=True)
        return None, "sudah-ada"
    lain = _nama_bebas(dest)
    tmp.replace(lain)
    return lain, "senama"


class Penempat:
    """
    Menentukan ke mana tiap berkas baru mendarat, konsisten sepanjang batch.

    Dua hal yang harus dijaga bersamaan. Pertama, gambar dan berkas labelnya
    wajib mendarat di split yang SAMA — kalau tidak, labelnya menjadi yatim
    dan gambarnya tampak belum dilabeli. Karena itu keputusannya diingat per
    nama-tanpa-ekstensi, bukan per berkas, dan urutan datangnya tidak penting.

    Kedua, perbandingan train/valid/test yang sudah ada harus tetap terjaga.
    Split berikutnya selalu yang paling tertinggal dari targetnya, dihitung
    ulang setiap kali satu gambar ditambahkan, sehingga sisipan sekecil apa
    pun tidak menggeser perbandingannya.
    """

    def __init__(self, src: Path):
        self.src = Path(src)
        self.tata = tata_letak(self.src)
        self.hitung: dict[str, int] = {}
        self.pilihan: dict[str, str] = {}
        self.ganti_nama: dict[str, str] = {}
        if self.tata == TATA_SPLIT:
            for d in scanner.split_bersarang(self.src):
                n = sum(1 for p in (d / "images").iterdir()
                        if p.is_file() and p.suffix.lower() in IMG_EXT)
                self.hitung[d.name] = n
        self.awal = dict(self.hitung)

    @property
    def total_awal(self) -> int:
        return sum(self.awal.values())

    def split_untuk(self, stem: str) -> str:
        if not self.hitung:
            return ""
        if stem in self.pilihan:
            return self.pilihan[stem]
        total = self.total_awal or 1
        # Yang dipilih adalah split dengan kekurangan terbesar terhadap
        # targetnya. Split bertarget nol dilewati, kalau tidak pembagiannya
        # akan mengisi split yang memang sengaja dikosongkan.
        pilih = min(
            (s for s in self.hitung if self.awal.get(s)),
            key=lambda s: (self.hitung[s] + 1) / (self.awal[s] / total),
            default=next(iter(self.hitung)))
        # Dinaikkan DI SINI, saat keputusannya dibuat, bukan setelah berkasnya
        # mendarat. Menunggu sampai mendarat membuat setiap berkas dinilai
        # seolah ia satu-satunya yang ditambahkan, dan seluruh batch lalu
        # menumpuk di satu split — persis yang pernah terjadi: 20 gambar baru
        # semuanya masuk train dan rasio 80:10:10 rusak menjadi 100:10:10.
        self.hitung[pilih] += 1
        self.pilihan[stem] = pilih
        return pilih

    def tujuan(self, rel: str) -> Path | None:
        """
        Path lengkap tempat `rel` harus mendarat, atau None kalau tidak boleh.

        Struktur folder asal kiriman sengaja DIBUANG untuk gambar dan label:
        yang menentukan letaknya adalah tata letak dataset tujuan, bukan tata
        letak folder di laptop pengirim. Tanpa itu, menyeret folder ekspor
        Roboflow ke dataset bersplit akan membuat `train/images/` bersarang di
        dalam `train/images/`.
        """
        nama = Path(rel).name
        suf = Path(nama).suffix.lower()
        if suf not in IMG_EXT and suf not in ANN_EXT:
            return None            # data.yaml diurus pemanggil, sisanya ditolak
        stem = Path(nama).stem
        gambar = suf in IMG_EXT
        # Gambar yang sudah diganti namanya karena bentrok menyeret berkas
        # labelnya ikut berganti. Tanpa ini gambarnya menjadi n0-2.jpg
        # sementara labelnya tetap n0.txt atau malah n0-3.txt — gambarnya
        # tampak belum dilabeli dan labelnya menjadi yatim.
        nama = self.ganti_nama.get(stem, stem) + Path(nama).suffix
        if self.tata == TATA_SPLIT:
            s = self.split_untuk(stem)
            return self.src / s / ("images" if gambar else "labels") / nama
        if self.tata == TATA_YOLO:
            return self.src / ("images" if gambar else "labels") / nama
        return self.src / nama

    def catat(self, asal: str, akhir: Path) -> None:
        """Laporkan nama yang benar-benar dipakai, supaya berkas lain dengan
        nama-dasar yang sama mengikutinya."""
        stem = Path(asal).stem
        if akhir.stem != stem:
            self.ganti_nama.setdefault(stem, akhir.stem)


def boleh_ditambahi(src: Path, ruang_kerja: Path) -> str:
    """
    Kembalikan pesan penolakan, atau "" kalau boleh.

    Dataset yang dibuka langsung dari sebuah path di server TIDAK boleh
    ditambahi: berkas barunya akan mendarat di folder milik orang lain, dan
    aturan yang dipegang aplikasi ini adalah folder sumber hanya dibaca.
    """
    try:
        Path(src).resolve().relative_to(Path(ruang_kerja).resolve())
    except (ValueError, OSError):
        return ("dataset ini dibuka langsung dari folder di server, jadi tidak "
                "bisa ditambahi — salin dulu ke ruang kerja sendiri lewat "
                "\"Salin ke ruang kerja\" di halaman pilih dataset")
    return ""


def ratakan_split(d: Path) -> dict:
    """
    Ratakan ekspor bersplit (train/valid/test) jadi satu kolam YOLO datar.

    HIGOLAB membelah datasetnya SENDIRI saat membuat versi — pembagian
    anti-bocor berdasarkan sesi + dHash. Kalau split bawaan Roboflow dibiarkan,
    `export.bagi_split` mempertahankannya (aturan 1) dan justru MEMBATALKAN
    pembelahan kita: gambar yang tadinya di valid tetap di valid, kebocoran
    tetap kebocoran. Maka begitu dataset jadi diunggah atau diimpor ke ruang
    kerja, persebaran train/valid/test-nya dicopot di sini — semua gambar
    berkumpul di `images/`+`labels/` di akar, dan pembagian sepenuhnya
    diserahkan ke mesin versi.

    HANYA untuk dataset di ruang unggahan sendiri. Folder yang dibuka di tempat
    lewat /setsrc tidak pernah melewati sini, dan memang tidak boleh diubah.

    Split-nya dicari di `d` sendiri MAUPUN satu tingkat lebih dalam: unggahan
    folder dari laptop selalu membawa nama folder pembungkus (webkitRelativePath
    "rf-dataset/train/images/x.jpg"), jadi split-nya mendarat di
    `d/rf-dataset/`, bukan di `d`. Sekalian, `data.yaml` yang ikut terbungkus
    itu diangkat ke akar — tanpa itu `baca_nama_kelas` yang cuma menengok dua
    tingkat tak menemukannya dan kelasnya jatuh jadi "0", "1", "2".

    Idempoten: dataset yang sudah datar tidak punya split bersarang lagi
    (`images/`+`labels/` di akar membuat `split_bersarang` mengembalikan []),
    jadi panggilan berikutnya tidak melakukan apa-apa.

    -> {"diratakan": n, "bentrok": [..maks 5..]}
    """
    d = Path(d)
    splits = scanner.split_bersarang(d)
    if not splits:
        # Unggahan folder membungkus datasetnya satu tingkat. Turun satu tingkat
        # untuk menemukannya — tetapi hanya satu, dan tidak menembus projek lain.
        for sub in sorted(p for p in d.iterdir() if p.is_dir()):
            dalam = scanner.split_bersarang(sub)
            if dalam:
                splits = dalam
                break
    if not splits:
        return {"diratakan": 0, "bentrok": []}

    akar = splits[0].parent            # d sendiri, atau pembungkusnya
    (d / "images").mkdir(exist_ok=True)
    (d / "labels").mkdir(exist_ok=True)

    # Daftar kelas ekspor Roboflow ada di akar split. Kalau akar itu bukan `d`
    # (kasus pembungkus), berkasnya diangkat supaya kelasnya tetap terbaca.
    if akar != d:
        for nama in ("data.yaml", "data.yml", "dataset.yaml", "classes.txt"):
            asal = akar / nama
            if asal.is_file() and not (d / nama).exists():
                asal.replace(d / nama)

    # Diproses PER SPLIT, gambar sebuah nama sebelum labelnya. Penggantian nama
    # karena bentrok dilacak per split, bukan global: nama-dasar yang sama bisa
    # muncul di train DAN valid sebagai foto yang berbeda, dan gambar dengan
    # labelnya yang sepasang selalu berada di split yang sama. Melacaknya global
    # membuat bentrok di valid menyeret nama label train — labelnya jadi yatim.
    bentrok: list[str] = []
    diratakan = 0
    for s in splits:
        ganti: dict[str, str] = {}
        img_dir, lbl_dir = s / "images", s / "labels"
        imgs = sorted(p for p in img_dir.iterdir() if p.is_file()) if img_dir.is_dir() else []
        lbls = sorted(p for p in lbl_dir.iterdir() if p.is_file()) if lbl_dir.is_dir() else []
        for p in imgs:
            akhir, hasil = pasang(p, d / "images" / p.name)
            if hasil == "senama":
                ganti[p.stem] = akhir.stem
                if len(bentrok) < 5:
                    bentrok.append(p.name)
            if hasil != "sudah-ada":
                diratakan += 1
        for p in lbls:
            nama = ganti.get(p.stem, p.stem) + p.suffix
            pasang(p, d / "labels" / nama)

    # Folder split yang kini kosong dibuang; yang menyisakan berkas lain (mis.
    # gambar rusak yang tak tersalin) sengaja dibiarkan daripada menghapus data.
    for s in splits:
        for sub in ("images", "labels"):
            f = s / sub
            if f.is_dir() and not any(f.iterdir()):
                f.rmdir()
        if s.is_dir() and not any(s.iterdir()):
            s.rmdir()
    # Pembungkus yang kini kosong ikut dibuang, supaya scan(d) mengenali
    # `images/`+`labels/` di akar dan bukan lagi menuruni pembungkusnya.
    if akar != d and akar.is_dir() and not any(akar.iterdir()):
        akar.rmdir()

    return {"diratakan": diratakan, "bentrok": bentrok}


def gabung_dataset_ke_projek(zp: Path, d: Path, *, maks_byte: int,
                             maks_entri: int = 200_000) -> dict:
    """
    Bongkar & GABUNGKAN sebuah .zip dataset (mis. ekspor Roboflow bersplit) ke
    projek DATAR (labelme) yang SUDAH berisi.

    Kenapa butuh jalur sendiri (bukan /unzip + ratakan_split biasa):
      1. `split_bersarang` menolak kalau ada gambar lepas di akar (projek datar
         yang sudah berisi selalu begitu), jadi split-nya tak akan terdeteksi
         kalau dibongkar langsung ke akar.
      2. Projek datar MENGABAIKAN label YOLO `.txt` (scanner cuma baca `.json`
         di sini). Maka label YOLO harus DIKONVERSI jadi labelme `.json`.

    Alurnya: bongkar ke folder STAGING terpisah (akar projek tak mengganggu
    deteksi split), ratakan di situ, lalu tiap gambar + label-nya dikonversi ke
    labelme `.json` dan ditaruh di AKAR projek, menyatu dengan yang sudah ada.
    Kelas dipetakan lewat NAMA (dari data.yaml), jadi menyatu dengan kelas yang
    sudah ada selama namanya sama. Tidak pernah menimpa berkas (lewat `pasang`).
    Arsip + folder staging dibuang di akhir.

    -> {"digabung": n, "boxes": m, "kelas": [nama...], "bentrok": [..maks 5..]}
    """
    import json as _json
    import secrets
    import shutil

    from PIL import Image

    from . import arsip
    from .annotations import LABELME_VERSION

    d = Path(d)
    staging = d / f"._impor-zip-{secrets.token_hex(4)}"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        arsip.bongkar(zp, staging, maks_byte=maks_byte, maks_entri=maks_entri)
        ratakan_split(staging)                       # -> staging/images+labels
        names = scanner.baca_nama_kelas(staging)     # {idx: nama} dari data.yaml
        img_dir, lbl_dir = staging / "images", staging / "labels"
        imgs = sorted(p for p in img_dir.iterdir()
                      if p.is_file() and p.suffix.lower() in IMG_EXT) \
            if img_dir.is_dir() else []
        digabung = boxes = 0
        bentrok: list[str] = []
        kelas_pakai: set[str] = set()
        for p in imgs:
            try:
                W, H = Image.open(p).size
            except Exception:                        # noqa: BLE001 -- gambar rusak
                continue
            shapes = []
            txt = lbl_dir / (p.stem + ".txt")
            if txt.is_file():
                for ln in txt.read_text(encoding="utf-8",
                                        errors="ignore").splitlines():
                    f = ln.split()
                    if len(f) != 5:
                        continue
                    try:
                        c = int(float(f[0]))
                        cx, cy, w, h = map(float, f[1:])
                    except ValueError:
                        continue
                    nama = names.get(c, str(c))
                    kelas_pakai.add(nama)
                    x1 = max(0.0, min(float(W), (cx - w / 2) * W))
                    y1 = max(0.0, min(float(H), (cy - h / 2) * H))
                    x2 = max(0.0, min(float(W), (cx + w / 2) * W))
                    y2 = max(0.0, min(float(H), (cy + h / 2) * H))
                    shapes.append({"label": nama, "text": "",
                                   "points": [[x1, y1], [x2, y2]],
                                   "group_id": None, "shape_type": "rectangle",
                                   "flags": {}})
            # Gambar -> akar projek (pasang menangani bentrok nama tanpa menimpa).
            akhir, hasil = pasang(p, d / p.name)
            if hasil == "sudah-ada" or akhir is None:
                continue
            if hasil == "senama" and len(bentrok) < 5:
                bentrok.append(p.name)
            doc = {"image_text": "", "version": LABELME_VERSION, "flags": {},
                   "shapes": shapes, "imagePath": akhir.name, "imageData": None,
                   "imageHeight": H, "imageWidth": W}
            jp = akhir.with_suffix(".json")
            tmp = jp.with_name(jp.name + ".tmp")
            tmp.write_text(_json.dumps(doc, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(jp)
            digabung += 1
            boxes += len(shapes)
        return {"digabung": digabung, "boxes": boxes,
                "kelas": sorted(kelas_pakai), "bentrok": bentrok}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        zp.unlink(missing_ok=True)
