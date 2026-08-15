"""
Menyalin dataset dari sebuah path di server ke ruang kerja pemakai.

Setara dengan mengunggah dari laptop, hanya sumbernya berbeda: yang satu dari
komputer pemakai, yang ini dari folder di server. Hasilnya sama-sama mendarat
di folder unggahan milik akun itu, sehingga menyunting dan menambah gambar
tidak pernah menyentuh dataset aslinya.

Aturan yang dipegang berkas ini: **folder sumber hanya dibaca.** Tidak ada satu
pun operasi tulis, hapus, atau ganti nama yang mengarah ke sana. Salinan dibuat
sungguhan, bukan tautan — tautan memang lebih hemat, tetapi menuntut setiap
penulis di aplikasi ini memakai pola tulis-lalu-ganti-nama selamanya, dan satu
kelalaian di masa depan akan merusak berkas asli tanpa jejak. Harga salinan
adalah disk; harga tautan adalah kepercayaan yang tidak bisa dijamin.
"""
from __future__ import annotations

import shutil
import threading
from pathlib import Path

from ..security import safe_relpath
from . import tambah

# Sisa disk yang harus tetap tersisa setelah menyalin. Mengisi disk sampai
# penuh membuat seluruh mesin bermasalah, bukan cuma aplikasi ini.
SISA_MIN_BYTE = 5 * 1024 ** 3

# Sesering apa kemajuan diperbarui. Menulis tiap berkas membuat penyalinan
# berebut kunci ribuan kali tanpa satu pun mata sempat melihat bedanya.
LAPOR_TIAP = 25


class ImporTolak(Exception):
    """Impor tidak bisa dijalankan — pesannya untuk dibaca pemakai."""


# ------------------------------------------------------------------- kemajuan
#
# Penyalinan berjalan di thread terpisah sementara permintaannya menggantung,
# jadi kemajuannya tidak bisa dikirim lewat balasan permintaan itu sendiri.
# Disimpan di sini supaya permintaan lain bisa menanyakannya. Di memori saja:
# kemajuan yang tidak selamat dari restart tidak merugikan siapa pun, karena
# penyalinannya juga tidak.

_maju: dict[str, dict] = {}
_kunci_maju = threading.Lock()


def catat_maju(kunci: str, **nilai) -> None:
    if kunci:
        with _kunci_maju:
            _maju.setdefault(kunci, {}).update(nilai)


def kemajuan(kunci: str) -> dict:
    with _kunci_maju:
        return dict(_maju.get(kunci) or {})


def _didalam(anak: Path, induk: Path) -> bool:
    try:
        anak.resolve().relative_to(induk.resolve())
        return True
    except ValueError:
        return False


def survei(sumber: Path) -> dict:
    """
    Lihat dulu apa yang akan disalin, tanpa menyalin apa pun.

    Dipakai untuk memberi tahu ukuran dan jumlah berkas sebelum orang menekan
    tombol — menyalin 3 GB tanpa pemberitahuan bukan kejutan yang menyenangkan.
    """
    sumber = Path(sumber)
    n = byte = dilewati = 0
    # Dipakai aturan yang persis sama dengan impor_folder, bukan sekadar cek
    # ekstensi. Kalau berbeda, angka yang ditampilkan sebelum menyalin tidak
    # akan cocok dengan yang benar-benar tersalin, dan taksiran disk meleset.
    for p in sumber.rglob("*"):
        if not p.is_file():
            continue
        if safe_relpath(str(p.relative_to(sumber))):
            n += 1
            try:
                byte += p.stat().st_size
            except OSError:
                pass
        else:
            dilewati += 1
    return {"berkas": n, "bytes": byte, "dilewati": dilewati}


def impor_folder(sumber: Path, tujuan: Path, *, batal=None, kunci: str = "",
                 tentukan=None, lapor_nama=None) -> dict:
    """
    Salin isi `sumber` ke `tujuan`, struktur foldernya dipertahankan.

    Yang disalin hanya gambar, anotasi, dan data.yaml — sisanya dilewati.
    Struktur `images/` dan `labels/` ikut terjaga karena pemindai mengenali
    dataset YOLO justru dari keduanya.

    `batal` adalah fungsi tanpa argumen yang mengembalikan True kalau proses
    harus dihentikan; berkas yang sedang ditulis dibersihkan lebih dulu.

    `kunci` menyalakan pelaporan kemajuan — lihat catat_maju di atas.

    `tentukan(rel) -> Path | None` boleh diberikan untuk memindahkan berkas ke
    tempat lain daripada `tujuan / rel`, atau melewatinya dengan None. Itu yang
    dipakai saat menambah ke dataset yang sudah ada, karena di sana letaknya
    ditentukan tata letak dataset tujuan, bukan tata letak folder sumbernya.

    `lapor_nama(rel, akhir)` dipanggil kalau nama yang benar-benar dipakai
    berbeda dari yang diminta, supaya berkas lain dengan nama-dasar yang sama
    bisa mengikutinya.
    """
    sumber, tujuan = Path(sumber), Path(tujuan)
    catat_maju(kunci, tahap="survei", berkas=0, bytes=0, total=0, total_bytes=0)
    if not sumber.is_dir():
        raise ImporTolak("folder sumber tidak ada di server")
    if _didalam(tujuan, sumber):
        raise ImporTolak("tujuan berada di dalam folder sumber — akan berulang "
                         "menyalin dirinya sendiri")

    s = survei(sumber)
    if not s["berkas"]:
        raise ImporTolak("tidak ada gambar atau anotasi yang bisa disalin di sana")

    tujuan.mkdir(parents=True, exist_ok=True)
    sisa = shutil.disk_usage(tujuan).free
    if sisa - s["bytes"] < SISA_MIN_BYTE:
        raise ImporTolak(
            f"perlu {s['bytes'] / 1073741824:.1f} GB sementara sisa disk "
            f"{sisa / 1073741824:.1f} GB — terlalu mepet")

    catat_maju(kunci, tahap="salin", total=s["berkas"], total_bytes=s["bytes"])
    ditulis = dilewati = sudah_ada = 0
    byte = 0
    bentrok: list[str] = []
    contoh: list[str] = []
    for p in sorted(sumber.rglob("*")):
        if batal and batal():
            raise ImporTolak("dibatalkan")
        if not p.is_file():
            continue
        # Nama disterilkan lewat jalur yang sama dengan unggahan biasa, jadi
        # ekstensi asing dan komponen path aneh tertolak dengan aturan yang sama.
        rel = safe_relpath(str(p.relative_to(sumber)))
        if not rel:
            dilewati += 1
            if len(contoh) < 5:
                contoh.append(p.name)
            continue
        dest = tentukan(rel) if tentukan else (tujuan / rel)
        if dest is None:
            dilewati += 1
            if len(contoh) < 5:
                contoh.append(p.name)
            continue
        if not _didalam(dest.parent, tujuan) and dest.parent != tujuan:
            dilewati += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Salin ke nama sementara lalu serahkan ke tambah.pasang: proses yang
        # terputus tidak meninggalkan gambar setengah jadi yang tampak sah saat
        # dipindai, dan tidak ada berkas yang pernah ditimpa.
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            shutil.copy2(p, tmp)
            akhir, hasil = tambah.pasang(tmp, dest)
        except OSError:
            tmp.unlink(missing_ok=True)
            dilewati += 1
            continue
        if hasil == "sudah-ada":
            sudah_ada += 1
            continue
        if hasil == "senama":
            if len(bentrok) < 5:
                bentrok.append(p.name)
            # Berkas lain dengan nama-dasar yang sama harus ikut berganti,
            # kalau tidak gambar dan labelnya terpisah.
            if lapor_nama:
                lapor_nama(rel, akhir)
        ditulis += 1
        byte += akhir.stat().st_size
        if ditulis % LAPOR_TIAP == 0:
            catat_maju(kunci, berkas=ditulis, bytes=byte)

    # Tahap berikutnya (memindai isi salinan) dikerjakan pemanggil dan bisa
    # selama penyalinannya sendiri, jadi perpindahannya perlu terlihat —
    # kalau tidak, bilah progres berhenti penuh dan tampak menggantung.
    catat_maju(kunci, tahap="pindai", berkas=ditulis, bytes=byte)
    return {"berkas": ditulis, "dilewati": dilewati, "bytes": byte,
            "sudah_ada": sudah_ada, "sumber": str(sumber),
            "contoh_dilewati": contoh, "bentrok": bentrok}
