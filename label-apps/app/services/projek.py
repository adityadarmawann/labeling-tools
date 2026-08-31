"""
Mengurus dataset sebagai "projek": daftar berkartu, ganti nama, duplikat,
gabungkan, dan buang ke tempat sampah.

BATAS YANG TIDAK BOLEH DILANGGAR
--------------------------------
Seluruh operasi di sini menyentuh berkas sungguhan, sebagian ribuan sekaligus.
Tiga aturan menjaganya:

1. **Hanya di dalam ruang kerja akun itu sendiri.** Folder dataset bersama
   (`datasets_root`) boleh dibaca tapi tidak boleh diubah — isinya dipakai
   orang lain, dan sebagian aslinya milik proyek lain di mesin ini.

2. **Nama dibersihkan, bukan dipercaya.** Nama projek datang dari kotak isian.
   Tanpa dibersihkan, `../` di dalamnya cukup untuk memindahkan folder ke luar
   ruang kerja.

3. **Menghapus berarti memindahkan.** Tidak ada `rmtree` di berkas pengguna.
   Yang dibuang mendarat di `_sampah/` dengan cap waktu, dan bisa
   dikembalikan dengan memindahkannya balik lewat berkas manajer biasa.
"""
from __future__ import annotations

import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from ..config import ANN_EXT, IMG_EXT
from ..log import catat

log = catat("labelapp.projek")

SAMPAH = "_sampah"

# Folder yang tidak pernah muncul sebagai projek.
_SEMBUNYI = {SAMPAH, "_unggahan"}

# Batas penelusuran per folder. Tanpa ini, satu dataset 300 ribu berkas
# membuat halaman daftar menggantung setiap kali dibuka.
MAKS_TELUSUR = 20_000


# ------------------------------------------------------------------ kemajuan
#
# Pola yang sama dengan impor dan splitting: dict di memori plus rute polling.
# Menyalin 1,2 GB memakan menit, dan tanpa laporan tombolnya tampak mati.

_maju: dict[str, dict] = {}
_kunci_maju = __import__("threading").Lock()


def catat_maju(kunci: str, **nilai) -> None:
    if kunci:
        with _kunci_maju:
            _maju.setdefault(kunci, {}).update(nilai)


def kemajuan(kunci: str) -> dict:
    with _kunci_maju:
        return dict(_maju.get(kunci) or {})


def bersihkan_maju(kunci: str) -> None:
    if kunci:
        with _kunci_maju:
            _maju.pop(kunci, None)


class Tolak(Exception):
    """Permintaan yang tidak boleh dijalankan, dengan alasan untuk pengguna."""


def bersihkan_nama(s: str) -> str:
    """Nama folder projek yang aman: tanpa pemisah path, titik depan, spasi tepi."""
    s = re.sub(r"[^\w .()-]+", "-", str(s or "").strip(), flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip(" .-")
    return s[:80]


def _didalam(anak: Path, induk: Path) -> bool:
    try:
        anak.resolve().relative_to(induk.resolve())
        return True
    except (ValueError, OSError):
        return False


def _folder(root: Path, nama: str) -> Path:
    """Folder projek `nama` di dalam `root`, dengan seluruh pemeriksaannya."""
    bersih = bersihkan_nama(nama)
    if not bersih:
        raise Tolak("nama projek kosong atau seluruhnya karakter terlarang")
    d = (Path(root) / bersih)
    if not _didalam(d, Path(root)):
        raise Tolak("nama itu menunjuk ke luar ruang kerjamu")
    return d


# ============================================================
# DAFTAR
# ============================================================

def anotasi_untuk(gambar: Path) -> Path | None:
    """
    Berkas anotasi milik sebuah gambar, kalau ada.

    Dua tata letak beredar dan keduanya harus dikenali. Labelme menaruh
    `.json` bersebelahan dengan gambarnya; YOLO menaruh `.txt` di folder
    `labels/` yang sejajar dengan `images/`. Memeriksa `.json` saja membuat
    seluruh dataset YOLO tampak belum berlabel — terukur pada
    botol-kaleng-tetra-mlp-cup-1: 11.321 berkas .txt, nol .json.
    """
    j = gambar.with_suffix(".json")
    if j.is_file():
        return j
    t = gambar.with_suffix(".txt")
    if t.is_file():
        return t
    if gambar.parent.name == "images":
        t = gambar.parent.parent / "labels" / (gambar.stem + ".txt")
        if t.is_file():
            return t
    return None


def punya_gambar(d: Path) -> bool:
    """Apakah projek ini sudah berisi gambar.

    Berhenti pada temuan pertama, bukan menghitung semuanya: yang ditanyakan
    cuma kosong atau tidak, dan projek terbesar di sini berisi dua puluh dua
    ribu berkas. Menghitungnya berarti menelusuri seluruhnya untuk menjawab
    pertanyaan ya-tidak.
    """
    try:
        for f in Path(d).rglob("*"):
            if f.is_file() and f.suffix.lower() in IMG_EXT:
                return True
    except OSError:
        pass
    return False


def _survei(d: Path) -> dict:
    """Sekali telusur untuk semua angka yang dibutuhkan satu kartu.

    Digabung menjadi satu jalan karena menelusuri folder besar itu mahal:
    menghitung gambar, menghitung anotasi, mencari sampul, dan mencari berkas
    terbaru masing-masing sendiri berarti empat kali kerja yang sama.
    """
    n_img = n_ann = 0
    sampul: Path | None = None
    sampul_berlabel = False
    terbaru = 0.0
    lebih = False
    n = 0
    for p in d.rglob("*"):
        n += 1
        if n > MAKS_TELUSUR:
            lebih = True
            break
        if not p.is_file():
            continue
        sfx = p.suffix.lower()
        if sfx in IMG_EXT:
            n_img += 1
            # Sampul diambil dari gambar yang PUNYA anotasi, bukan yang
            # pertama menurut abjad. Foto produk di atas meja yang belum
            # dilabeli tidak memberi tahu apa pun tentang isi projeknya —
            # yang tampak cuma mejanya.
            if sampul is None or not sampul_berlabel:
                punya = anotasi_untuk(p) is not None
                if sampul is None or punya:
                    sampul, sampul_berlabel = p, punya
        elif sfx in ANN_EXT:
            n_ann += 1
            try:
                terbaru = max(terbaru, p.stat().st_mtime)
            except OSError:
                pass
    if not terbaru:
        try:
            terbaru = d.stat().st_mtime
        except OSError:
            terbaru = 0.0
    return {"gambar": n_img, "anotasi": n_ann, "sampul": sampul,
            "diubah": terbaru, "lebih": lebih}


def _usia(t: float) -> str:
    if not t:
        return "-"
    d = max(0, time.time() - t)
    for batas, satuan, bagi in ((60, "detik", 1), (3600, "menit", 60),
                                (86400, "jam", 3600), (2592000, "hari", 86400)):
        if d < batas:
            n = int(d // bagi) or 1
            return f"{n} {satuan} lalu"
    return datetime.fromtimestamp(t).strftime("%d %b %Y")


def temukan(uploads_root: Path, akun: str, ds: str) -> Path | None:
    """
    Folder projek yang boleh dibuka `akun`, dari nama di URL.

    Dua bentuk yang diterima:
        "paragon"          projek milik akun ini sendiri
        "darma/paragon"    projek milik orang lain yang mengundang akun ini

    Bentuk kedua yang membuat penugasan lintas akun mungkin. Ia tetap dijaga:
    yang tidak diundang mendapat None, sama seperti kalau projeknya tidak ada.
    Membedakan "tidak ada" dari "tidak boleh" berarti memberi tahu orang luar
    projek apa saja yang dimiliki orang lain.
    """
    from . import tugas

    ds = (ds or "").strip().strip("/")
    if not ds:
        return None
    root = Path(uploads_root)
    if "/" in ds:
        pemilik, _, nama = ds.partition("/")
        pemilik, nama = bersihkan_nama(pemilik), bersihkan_nama(nama)
        if not pemilik or not nama:
            return None
        d = root / pemilik / nama
        if not _didalam(d, root) or not d.is_dir():
            return None
        if pemilik == akun:
            return d
        data = tugas.baca(d, pemilik)
        return d if tugas.boleh_lihat(data, akun) else None

    d = root / akun / bersihkan_nama(ds)
    return d if _didalam(d, root / akun) and d.is_dir() else None


def punya_tamu(uploads_root: Path, akun: str) -> list[dict]:
    """
    Projek milik akun LAIN yang mengundang `akun`.

    Ditelusuri dengan membaca berkas tugas tiap projek, bukan lewat daftar
    terpusat. Satu daftar terpusat berarti dua sumber kebenaran yang harus
    dijaga tetap sama, dan yang menang saat berbeda tidak pernah jelas.
    """
    from . import tugas

    root = Path(uploads_root)
    out = []
    if not root.is_dir():
        return out
    for folder_akun in sorted(root.iterdir()):
        if not folder_akun.is_dir() or folder_akun.name == akun:
            continue
        for d in sorted(folder_akun.iterdir()):
            if not d.is_dir() or d.name.startswith(".") or d.name in _SEMBUNYI:
                continue
            if not (d / tugas.BERKAS).is_file():
                continue
            data = tugas.baca(d, folder_akun.name)
            if akun in data["anggota"]:
                out.append({"pemilik": folder_akun.name, "nama": d.name,
                            "path": str(d.resolve()),
                            "ds": f"{folder_akun.name}/{d.name}"})
    return out


def ringkas(d: Path) -> dict:
    """Angka satu projek untuk kepala halaman dan sidebar.

    Memakai penelusuran yang sama dengan kartu di halaman projek, jadi angka
    di sidebar dan angka di kartunya tidak pernah berbeda.
    """
    s = _survei(Path(d))
    return {"jumlah": s["gambar"], "anotasi": s["anotasi"],
            "sampul": str(s["sampul"]) if s["sampul"] else "",
            "lebih": s["lebih"]}


def daftar(root: Path | None) -> list[dict]:
    """Kartu untuk tiap projek di dalam `root`."""
    if not root or not Path(root).is_dir():
        return []
    out = []
    for d in sorted(Path(root).iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.name in _SEMBUNYI:
            continue
        s = _survei(d)
        # Projek KOSONG tetap ditampilkan. Dulu dibuang, dan itu benar selama
        # projek hanya lahir bersama unggahannya. Sejak projek dibuat lebih
        # dulu lalu diisi belakangan, membuangnya berarti orang menekan "Projek
        # baru", berhasil, lalu kembali ke halaman yang tidak menampilkan
        # apa-apa.
        out.append({
            "nama": d.name, "path": str(d.resolve()),
            "jumlah": s["gambar"], "anotasi": s["anotasi"], "lebih": s["lebih"],
            "kosong": s["gambar"] == 0,
            "diubah": s["diubah"], "usia": _usia(s["diubah"]),
            "sampul": str(s["sampul"]) if s["sampul"] else "",
        })
    return out


# ============================================================
# OPERASI
# ============================================================

def buat(root: Path, nama: str) -> dict:
    """
    Projek kosong, tanpa satu berkas pun.

    Membuat projek dan mengisinya dipisah karena itulah bentuk pekerjaannya:
    satu projek diisi berkali-kali, dari sumber yang berbeda, pada hari yang
    berbeda. Menyatukan keduanya memaksa orang menyiapkan seluruh gambarnya
    sebelum boleh memberi nama.
    """
    d = _folder(root, nama)
    if d.exists():
        raise Tolak(f"sudah ada projek bernama '{d.name}'")
    d.mkdir(parents=True)
    log.info("projek dibuat: %r", d.name)
    return {"nama": d.name, "path": str(d.resolve())}


def ganti_nama(root: Path, lama: str, baru: str) -> dict:
    src = _folder(root, lama)
    dst = _folder(root, baru)
    if not src.is_dir():
        raise Tolak(f"projek '{lama}' tidak ada")
    if dst == src:
        return {"nama": dst.name, "path": str(dst)}
    if dst.exists():
        raise Tolak(f"sudah ada projek bernama '{dst.name}'")
    src.rename(dst)
    log.info("ganti nama: %r -> %r", src.name, dst.name)
    return {"nama": dst.name, "path": str(dst)}


def duplikat(root: Path, nama: str, baru: str = "", *,
             kunci: str = "", batal=None) -> dict:
    """Salinan penuh. Berat, tapi itu memang yang diminta."""
    src = _folder(root, nama)
    if not src.is_dir():
        raise Tolak(f"projek '{nama}' tidak ada")
    dst = _folder(root, baru) if baru else None
    if dst is None:
        # Nama bebas berikutnya, supaya menekan tombolnya dua kali tidak gagal.
        for i in range(2, 100):
            calon = _folder(root, f"{src.name} {i}")
            if not calon.exists():
                dst = calon
                break
        else:
            raise Tolak("terlalu banyak salinan dengan nama serupa")
    if dst.exists():
        raise Tolak(f"sudah ada projek bernama '{dst.name}'")

    # Disalin sendiri per berkas, bukan lewat shutil.copytree, semata supaya
    # kemajuannya bisa dilaporkan. Menyalin 1,2 GB memakan menit, dan selama
    # itu layar diam sepenuhnya kalau penyalinannya satu panggilan tertutup.
    berkas = [f for f in src.rglob("*") if f.is_file()]
    total = len(berkas)
    t = time.monotonic()
    catat_maju(kunci, tahap="salin", n=0, total=total, persen=0.0,
               judul=f"Menduplikat {src.name}")
    dst.mkdir(parents=True)
    langkah = max(1, total // 200)
    for i, f in enumerate(berkas, 1):
        if batal is not None and batal():
            # Salinan setengah jadi lebih berbahaya daripada tidak ada:
            # ia muncul sebagai projek utuh di daftar.
            shutil.rmtree(dst, ignore_errors=True)
            bersihkan_maju(kunci)
            raise Tolak("dihentikan; salinan setengah jadi dibuang")
        tuj = dst / f.relative_to(src)
        tuj.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, tuj)
        if i % langkah == 0 or i == total:
            catat_maju(kunci, n=i, total=total, persen=i / max(total, 1))
    catat_maju(kunci, tahap="selesai", persen=1.0)
    log.info("duplikat: %r -> %r, %s berkas dalam %.0f dtk", src.name, dst.name,
             f"{total:,}", time.monotonic() - t)
    return {"nama": dst.name, "path": str(dst), "berkas": total}


def ke_sampah(root: Path, nama: str) -> dict:
    """
    Dipindahkan ke `_sampah/`, TIDAK dihapus.

    Satu klik keliru di sini berarti ribuan gambar dan jam kerja pelabelan
    hilang tanpa bisa dikembalikan. Memindahkan memberi jalan pulang, dan
    ruang disknya bisa dibersihkan belakangan lewat berkas manajer biasa.
    """
    src = _folder(root, nama)
    if not src.is_dir():
        raise Tolak(f"projek '{nama}' tidak ada")
    kotak = Path(root) / SAMPAH
    kotak.mkdir(parents=True, exist_ok=True)
    cap = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = kotak / f"{src.name}--{cap}"
    src.rename(dst)
    log.info("ke sampah: %r -> %s", src.name, dst)
    return {"nama": src.name, "sampah": str(dst)}


def isi_sampah(root: Path | None) -> list[dict]:
    kotak = Path(root) / SAMPAH if root else None
    if not kotak or not kotak.is_dir():
        return []
    out = []
    for d in sorted(kotak.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        nama, _, cap = d.name.rpartition("--")
        try:
            t = datetime.strptime(cap, "%Y%m%d-%H%M%S").timestamp()
        except ValueError:
            nama, t = d.name, d.stat().st_mtime
        out.append({"nama": nama or d.name, "folder": d.name,
                    "path": str(d.resolve()), "usia": _usia(t)})
    return out


def pulihkan(root: Path, folder: str) -> dict:
    """Kembalikan satu projek dari tempat sampah."""
    kotak = Path(root) / SAMPAH
    src = kotak / bersihkan_nama(folder)
    if not _didalam(src, kotak) or not src.is_dir():
        raise Tolak("tidak ada di tempat sampah")
    nama = src.name.rpartition("--")[0] or src.name
    dst = _folder(root, nama)
    if dst.exists():
        dst = _folder(root, f"{nama} pulih")
        if dst.exists():
            raise Tolak(f"sudah ada projek bernama '{nama}'; ganti namanya dulu")
    src.rename(dst)
    log.info("pulih dari sampah: %s -> %r", folder, dst.name)
    return {"nama": dst.name, "path": str(dst)}


def gabung(root: Path, sumber: str, tujuan: str, *, kunci: str = "",
           batal=None) -> dict:
    """
    Salin isi projek `sumber` ke dalam `tujuan`, keduanya di ruang kerja ini.

    Memakai mesin yang sama dengan "Tambah gambar" di halaman grid, termasuk
    Penempat-nya: kalau tujuan sudah terbagi train/valid/test, berkas baru
    mendarat mengikuti perbandingan yang ada, dan gambar beserta labelnya
    dijaga tetap di split yang sama.

    Sumbernya TIDAK dihapus. Menggabungkan lalu membuang yang lama adalah dua
    keputusan berbeda, dan yang kedua harus disengaja.
    """
    from . import impor, tambah

    a = _folder(root, sumber)
    b = _folder(root, tujuan)
    if not a.is_dir():
        raise Tolak(f"projek '{sumber}' tidak ada")
    if not b.is_dir():
        raise Tolak(f"projek '{tujuan}' tidak ada")
    if a == b:
        raise Tolak("sumber dan tujuannya projek yang sama")
    if _didalam(a, b) or _didalam(b, a):
        raise Tolak("salah satu folder berada di dalam yang lain")

    penempat = tambah.Penempat(b)
    t = time.monotonic()
    hasil = impor.impor_folder(a, b, kunci=kunci, batal=batal,
                               tentukan=penempat.tujuan,
                               lapor_nama=penempat.catat)
    log.info("gabung: %r -> %r, %s berkas dalam %.0f dtk",
             a.name, b.name, f"{hasil['berkas']:,}", time.monotonic() - t)
    return {"sumber": a.name, "tujuan": b.name,
            "ditambah": hasil["berkas"], "sudah_ada": hasil["sudah_ada"],
            "dilewati": hasil["dilewati"], "bentrok": hasil.get("bentrok", [])}
