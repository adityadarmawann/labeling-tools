"""
Sesi per akun.

Inti dari aplikasi ini dipakai bersama: setiap akun punya folder dataset,
hasil pindai, dan cache thumbnail sendiri. Tanpa pemisahan ini, satu orang
mengganti folder akan mengubah tampilan semua orang.

Sesi disimpan di memori, jadi hilang saat proses restart dan semua orang
login ulang. Itu disengaja — tidak ada sesi menggantung yang tidak bisa
dicabut.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import pickle
import secrets
import shutil
import threading
import time
from pathlib import Path

from .config import Settings
from .security import safe_slug
from .services import annotations, scanner


# Penanda perubahan isi projek, dipakai BERSAMA oleh semua sesi.
#
# Tiap sesi memindai projek sekali lalu bekerja dari ingatan. Itu benar selama
# satu orang mengerjakan satu projek, dan salah sejak pekerjaannya dibagi:
# anotasi yang dibuat pelabel tidak pernah terlihat oleh pemilik projek —
# papan kemajuannya membeku di angka saat ia membukanya, dan memuat ulang
# halaman tidak menolong karena yang dimuat ulang cuma tampilannya.
#
# Yang disimpan cuma pencacah per folder. Pemindaian ulang mahal (11 ribu
# gambar butuh detik), jadi ia hanya dijalankan kalau pencacahnya benar-benar
# berubah — dan yang menaikkannya cuma penulisan sungguhan.
# Yang dicatat bukan cuma "ada yang berubah", melainkan GAMBAR MANA. Memindai
# ulang seluruh folder tiap kali orang lain menyimpan terdengar sederhana dan
# tidak bisa dipakai: projek produksi terbesar berisi 11.319 gambar dan sekali
# pindai memakan 5,8 detik. Dengan satu tim yang sedang melabeli, itu berarti
# setiap muat halaman menunggu enam detik.
#
# Riwayatnya dibatasi. Kalau sebuah sesi tertinggal lebih jauh daripada yang
# masih diingat, ia memindai ulang seluruhnya — jarang, dan lebih baik
# daripada menebak apa yang terlewat.
_MAKS_RIWAYAT = 4000
_cap_ubah: dict[str, int] = {}
_riwayat: dict[str, list] = {}
_kunci_cap = threading.Lock()


def tandai_berubah(src, gambar=None) -> None:
    """Catat bahwa anotasi sebuah gambar berubah.

    `gambar` boleh None untuk perubahan yang tidak bisa disebut per berkas —
    itu memaksa pemindaian ulang penuh bagi sesi yang tertinggal.
    """
    k = str(Path(src).resolve())
    with _kunci_cap:
        n = _cap_ubah.get(k, 0) + 1
        _cap_ubah[k] = n
        r = _riwayat.setdefault(k, [])
        r.append((n, str(Path(gambar).resolve()) if gambar else None))
        if len(r) > _MAKS_RIWAYAT:
            del r[:len(r) - _MAKS_RIWAYAT]
    # Menulis anotasi / menandai latar mengubah angka "sudah dilabeli" di kartu
    # & sidebar; buang cache survei projek itu supaya tak tertinggal (lazy impor,
    # projek meng-impor session hanya lazy juga -> tak ada siklus).
    try:
        from .services import projek
        projek.lupakan_survei(src)
    except Exception:                                   # noqa: BLE001
        pass


def cap_sekarang(src) -> int:
    with _kunci_cap:
        return _cap_ubah.get(str(Path(src).resolve()), 0)


def sidik_disk(src) -> tuple[int, int, int]:
    """
    Tanda tangan murah isi folder: (jumlah berkas, mtime terbaru, total byte).

    `_cap_ubah` di atas hanya tahu perubahan yang ditulis APLIKASI INI. Berkas
    yang disunting dari luar — AnyLabeling desktop lewat tombol Desktop di
    kartu, salin-tempel, rsync — tidak menaikkannya sama sekali, jadi
    `segarkan()` melewatinya tanpa sadar. Tanda tangan ini yang menangkapnya.

    Sengaja mencatat mtime DAN ukuran: menyunting anotasi bisa menghasilkan
    berkas yang ukurannya sama persis, dan menyalin berkas bisa
    mempertahankan mtime aslinya. Salah satunya berubah pada hampir semua
    perubahan nyata.

    Terukur 1,5 ms untuk 563 berkas dan 8,7 ms untuk 1.437 -- sekitar 6 mikro-
    detik per berkas, jadi projek produksi terbesar (11.319 gambar, ~22.600
    berkas) berada di kisaran 140 ms. Itu harga satu kali muat halaman, jauh
    di bawah 5,8 detik yang dibutuhkan pemindaian penuh.
    """
    n = t = s = 0
    for dirpath, dirnames, filenames in os.walk(src):
        # Folder bertitik memang tidak pernah ikut dipindai; menyertakannya di
        # sini membuat penulisan sidecar (.tugas.json, .versi/) terbaca sebagai
        # perubahan dari luar dan memicu pemindaian penuh tiap kali.
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for f in filenames:
            if f.startswith("."):
                continue
            try:
                st = os.stat(os.path.join(dirpath, f))
            except OSError:
                continue
            n += 1
            s += st.st_size
            if st.st_mtime_ns > t:
                t = st.st_mtime_ns
    return n, t, s


def berubah_sejak(src, cap: int):
    """
    Gambar yang berubah sesudah `cap`, atau None kalau riwayatnya tidak cukup.

    None berarti "pindai ulang seluruhnya": entah ada perubahan yang tidak
    bisa disebut per berkas, entah sesinya tertinggal lebih jauh daripada yang
    masih diingat.
    """
    k = str(Path(src).resolve())
    with _kunci_cap:
        r = _riwayat.get(k) or []
        if not r or r[0][0] > cap + 1:
            return None
        keluar = set()
        for n, jalur in r:
            if n <= cap:
                continue
            if jalur is None:
                return None
            keluar.add(jalur)
        return keluar


# ----------------------------------------------------- cache hasil pindai
#
# Membuka projek besar memindai SEMUA gambar (buka header + baca label +
# inspect) ke memori; pada projek 30rb gambar itu ~10 detik, dan terulang tiap
# kali projek dibuka di sesi baru (login, ganti projek lalu balik, sesudah
# deploy). Hasil pindai itu disimpan ter-gzip ke disk (projek 30rb ~19 MB) dan
# dimuat ~1,2 detik -> ~8x lebih cepat SELAMA isi folder tak berubah sejak
# pindai terakhir.
#
# Kunci validitas: tanda tangan folder (sidik_disk = jumlah berkas, mtime
# terbaru, total byte). Begitu SATU berkas berubah, tanda tangan berbeda, cache
# DITOLAK, dan folder dipindai ulang -> tak pernah menampilkan data basi. Versi
# dinaikkan kalau bentuk item/logika scanner berubah, supaya cache lama tak
# terpakai lintas-deploy.
#
# Isinya pickle struktur milik aplikasi ini sendiri, ditulis & dibaca hanya oleh
# proses ini di folder server (uploads_root/_scan-cache, di luar git, dilewati
# projek.daftar). Bukan masukan pengguna.
# Versi dinaikkan kalau BENTUK item atau logika pindai/sig berubah -> cache lama
# tak terpakai lintas-deploy (mencegah item basi berformat lama).
_CACHE_PINDAI_VER = 2
_CACHE_PINDAI_MIN = 2000            # projek kecil: pindai sudah cepat, tak di-cache
_CACHE_PINDAI_MAKS = 48            # paling banyak sekian berkas cache ditahan


def _dir_cache_pindai(settings: Settings) -> Path:
    return Path(settings.uploads_root) / "_scan-cache"


def _berkas_cache_pindai(settings: Settings, src: Path) -> Path:
    h = hashlib.sha1(str(src).encode()).hexdigest()[:16]
    return _dir_cache_pindai(settings) / f"{h}.pkl.gz"


def baca_cache_pindai(settings: Settings, src: Path):
    """(items, names, sigs) dari cache kalau ada & versinya cocok; None kalau
    tidak. TAK dijaga tanda-tangan-folder: pemanggil (load) merekonsiliasi lewat
    peta -> gambar berubah dibaca ulang, sisanya dipakai ulang. `sigs` =
    {path: sig_item} untuk membangun peta itu."""
    p = _berkas_cache_pindai(settings, src)
    try:
        with gzip.open(p, "rb") as f:
            d = pickle.load(f)
    except Exception:                                   # noqa: BLE001  (cache opsional)
        return None
    if not isinstance(d, dict) or d.get("v") != _CACHE_PINDAI_VER:
        return None
    items, names, sigs = d.get("items"), d.get("names"), d.get("sigs")
    if (not isinstance(items, list) or not isinstance(names, dict)
            or not isinstance(sigs, dict)):
        return None
    return items, names, sigs, d.get("sig_folder")


def tulis_cache_pindai(settings: Settings, src: Path, items, names,
                       sig_folder=None) -> None:
    """Tulis hasil pindai + tanda tangan (per-item DAN folder) ke cache (atomik).
    Projek kecil dilewati (pindai sudah cepat). sig_folder dipakai jalur cepat:
    kalau folder tak berubah sama sekali, buka berikutnya memakai item ini tanpa
    menelusuri apa pun."""
    if len(items) < _CACHE_PINDAI_MIN:
        return
    d = _dir_cache_pindai(settings)
    try:
        sigs = scanner.sigs_pindai(items)
        d.mkdir(parents=True, exist_ok=True)
        p = _berkas_cache_pindai(settings, src)
        tmp = p.with_suffix(".gz.tmp")
        with gzip.open(tmp, "wb", compresslevel=1) as f:
            pickle.dump({"v": _CACHE_PINDAI_VER, "items": items, "names": names,
                         "sigs": sigs,
                         "sig_folder": tuple(sig_folder) if sig_folder else None},
                        f, protocol=5)
        tmp.replace(p)
        _pangkas_cache_pindai(d)
    except OSError:
        pass                                            # gagal cache != gagal buka


def _pangkas_cache_pindai(d: Path) -> None:
    """Sisakan N berkas cache terbaru; buang sisanya supaya disk tak menumpuk."""
    try:
        berkas = sorted(d.glob("*.pkl.gz"),
                        key=lambda x: x.stat().st_mtime, reverse=True)
    except OSError:
        return
    for x in berkas[_CACHE_PINDAI_MAKS:]:
        try:
            x.unlink()
        except OSError:
            pass


class Session:
    """Keadaan milik satu akun yang sedang login."""

    def __init__(self, user: str, settings: Settings):
        self.user = user
        self.settings = settings
        self.src: Path | None = None
        self.items: list[dict] = []
        # Indeks path->item, dibangun sekali per pemindaian. find() dipanggil
        # sekali PER thumbnail, dan halaman job memuat 11.409 thumbnail dalam
        # satu kali buka; find() yang menyapu seluruh daftar (masing-masing
        # dengan .resolve() = syscall) berarti O(n) per gambar, O(n^2) per
        # halaman, di event loop. Terukur: 90 find() 4,7 detik, 11.409 find()
        # ~10 menit -- itulah "loading setengah jam" yang dilaporkan.
        self._by_path: dict[str, dict] = {}
        # Snapshot navigasi labeler yang DIBEKUKAN saat masuk dari papan/job.
        # Panah kiri/kanan menyusuri daftar ini, tetap walau gambar dilabeli dan
        # keluar dari filter (mis. "belum dianotasi") -- jadi jumlah tak menyusut
        # dan posisi tak hilang di tengah kerja. Disegarkan saat masuk ulang
        # (keluar ke papan lalu buka lagi). {kunci, nama[], paths[]}. Lihat
        # routers/annotate._nav.
        self.nav_beku: dict | None = None
        # Nilai penanda perubahan saat isi ini dipindai. Lihat _cap_ubah.
        self.cap = 0
        # Tanda tangan folder saat isi ini terakhir dibaca. Lihat sidik_disk:
        # ini yang menangkap perubahan dari LUAR aplikasi, yang tidak pernah
        # menaikkan `cap`.
        self._sidik: tuple[int, int, int] | None = None
        self.names: dict[int, str] = {}
        # Template skeleton projek, dibaca sekali per load(). muat_ulang_item
        # memakainya (lewat kpt_sesi) untuk membaca ulang SATU berkas pose —
        # read_yolo butuh K + nama slot untuk mengelompokkan titiknya.
        self.skeleton: dict = {}
        self.labelfile: Path | None = None
        self.thumbdir = settings.thumb_root / safe_slug(user)
        self.thumbdir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._penempat = None
        # Rencana pembelahan train/valid/test terakhir, beserta diagnosanya.
        # Disimpan supaya lima format ekspor memakai pembelahan yang sama
        # persis, dan supaya dHash yang mahal itu tidak dihitung ulang tiap
        # kali tombol ekspor ditekan.
        self.rencana_split: dict | None = None
        self.split_batal = False
        # Ditekan lewat /api/versi/batal, dibaca thread pembuat versi.
        # Terpisah dari split_batal: keduanya bisa berjalan bersamaan,
        # dan satu bendera untuk dua pekerjaan berarti menghentikan yang
        # satu diam-diam menghentikan yang lain.
        self.versi_batal = False
        self.projek_batal = False
        # Peran dibaca sekali saat sesi dibuat. Membacanya ulang di tiap
        # permintaan berarti membuka users.json puluhan kali per halaman.
        self.admin = False
        # Nama tampil dan foto profil dibaca SEKALI saat sesi dibuat, dengan
        # alasan yang sama seperti `admin`: kepala halaman menampilkannya di
        # SETIAP halaman, dan membuka users.json tiap permintaan hanya untuk
        # satu nama berarti puluhan kali baca per muat halaman. Keduanya
        # disegarkan di tempat oleh rute profil saat pemiliknya menyimpan —
        # bukan dibaca ulang dari berkas. Bawaannya nama = slug akun (kepala
        # tetap menampilkan sesuatu walau akunnya belum punya nama tampil).
        self.nama = user
        self.foto = ""

    # -- dataset --

    def load(self, src: Path) -> list[dict]:
        """Pindai folder baru dan buang cache thumbnail folder sebelumnya."""
        self.src = Path(src).resolve()
        # Dicatat SEBELUM memindai: perubahan yang datang di tengah pemindaian
        # lebih baik memicu satu pemindaian berlebih daripada terlewat.
        self.cap = cap_sekarang(self.src)
        # Tanda tangan folder dihitung LEBIH DULU: dipakai dua kali, sebagai
        # kunci cache pindai DAN sebagai self._sidik di bawah -- satu telusur
        # murah (~0,4 dtk utk 30rb) menggantikan dua. Cache memulihkan hasil
        # pindai ~8x lebih cepat selama folder tak berubah; begitu ada yang
        # berubah, tanda tangan beda -> cache ditolak -> pindai ulang (tak
        # pernah basi). Lihat baca_cache_pindai.
        sig = sidik_disk(self.src)
        cache = baca_cache_pindai(self.settings, self.src)
        if cache is not None and tuple(cache[3] or ()) == tuple(sig):
            # JALUR CEPAT: folder tak berubah sejak cache -> pakai item cache apa
            # adanya, tanpa menelusuri/stat ulang (sudah tersortir + terapkan_
            # jenis saat di-cache). ~8x lebih cepat. Dipakai saat buka projek
            # stabil: sesudah restart/deploy tanpa edit, meninjau, atau pindah
            # projek lalu balik.
            self.items, self.names = cache[0], cache[1]
        else:
            # Berubah (sedang dilabeli) atau belum ada cache: pindai dengan PETA
            # dari cache -> item tak berubah dipakai ulang, hanya yang berubah/
            # baru dibaca ulang; yang terhapus hilang sendiri (enumerasi dari
            # disk tetap otoritatif). Hasilnya identik dengan pindai penuh.
            peta = None
            if cache is not None:
                sigs_c = cache[2]
                peta = {}
                for it in cache[0]:
                    try:
                        k = str(it["img"].resolve())
                    except (OSError, ValueError):
                        continue
                    peta[k] = (sigs_c.get(k), it)
            self.items, self.names = scanner.scan(self.src, peta=peta)
            tulis_cache_pindai(self.settings, self.src, self.items,
                               self.names, sig_folder=sig)
        # Template skeleton projek untuk pembacaan-ulang per-item (pose). scan()
        # di atas sudah mengelompokkan baris pose sendiri (membaca .tugas.json),
        # jadi ini HANYA untuk jalur muat_ulang_item/annotate yang memanggil
        # read_yolo langsung. Gagal baca = projek biasa, bukan keypoint.
        try:
            from .services import tugas
            self.skeleton = (tugas.baca_projek(self.src, self.settings.uploads_root)
                             .get("skeleton") or {})
        except Exception:
            self.skeleton = {}
        self._bangun_indeks()
        # Tanda tangan yang SAMA dengan kunci cache di atas: "isi folder saat
        # items terakhir dibaca". Tak dihitung ulang -- sig di atas sudah itu.
        self._sidik = sig
        # Penempat memegang jumlah gambar per split saat ia dibuat. Setelah
        # pemindaian ulang angka itu sudah usang, jadi dibuang — kalau tidak,
        # penambahan berikutnya membagi berdasarkan keadaan yang sudah lewat.
        self._penempat = None
        # Projek baru -> konteks navigasi labeler lama tak berlaku.
        self.nav_beku = None
        # Rencana lama menyebut nama berkas yang mungkin sudah tidak ada.
        # Membiarkannya berarti ekspor berikutnya membelah memakai keadaan
        # yang sudah lewat.
        self.rencana_split = None
        self.reset_thumbs()
        annotations.write_label_file(self)
        return self.items

    def segarkan(self) -> bool:
        """
        Susulkan perubahan yang ditulis sesi lain sejak pemindaian terakhir.

        Dipanggil setiap halaman yang menampilkan isi anotasi: papan, rincian
        job, halaman bagi, grid, kanvas, dan halaman Lihat.

        Yang dibaca ulang HANYA gambar yang benar-benar berubah. Memindai
        seluruh folder tiap kali terdengar sederhana dan tidak bisa dipakai:
        projek produksi terbesar berisi 11.319 gambar, sekali pindai 5,8
        detik, dan dengan satu tim yang sedang melabeli itu berarti setiap
        muat halaman menunggu enam detik.

        Mengembalikan True kalau ada yang disusulkan.
        """
        if self.src is None:
            return False
        cap = cap_sekarang(self.src)
        if self.cap == cap:
            return False
        berubah = berubah_sejak(self.src, self.cap)
        with self.lock:
            if berubah is None:
                self.load(self.src)
                return True
            peta = {str(it["img"].resolve()): it for it in self.items}
            for jalur in berubah:
                it = peta.get(jalur)
                if it is None:
                    # Berkas baru atau terhapus: itu perubahan daftar, bukan
                    # isi, dan cuma pemindaian ulang yang tahu bentuknya.
                    self.load(self.src)
                    return True
                self.muat_ulang_item(it)
            self.cap = cap
            # Perubahan ini ditulis aplikasi sendiri dan sudah disusulkan, jadi
            # tanda tangannya ikut disegarkan. Tanpa baris ini periksa_disk()
            # akan melihat folder yang "berbeda" sesudah tiap kali melabeli,
            # lalu memicu pemindaian penuh 5,8 detik untuk perubahan yang baru
            # saja diproses dengan cara murah.
            self._sidik = sidik_disk(self.src)
        return True

    def kpt_sesi(self) -> dict | None:
        """kpt {K,titik} untuk read_yolo dari template skeleton sesi; None kalau
        projek ini bukan keypoint (lalu read_yolo berperilaku seperti biasa)."""
        t = (self.skeleton or {}).get("titik") or []
        return {"K": len(t), "titik": list(t)} if t else None

    def muat_ulang_item(self, it: dict) -> None:
        """Baca ulang anotasi SATU gambar dari disk."""
        try:
            if it.get("yolo"):
                sh = scanner.read_yolo(it["labels"], it["W"], it["H"],
                                       self.names, kpt=self.kpt_sesi())
                scanner._gabung_cadangan(it["img"], sh)
                it["shapes"] = sh
                it["issues"] = scanner.inspect(sh, it["W"], it["H"], True)
            else:
                sh, W, H = scanner.read_json(it["img"].with_suffix(".json"))
                it["shapes"] = sh
                it["issues"] = scanner.inspect(sh, W or it["W"], H or it["H"], True)
        except Exception:
            it["issues"] = ["berkas anotasi rusak"]
        self.drop_thumbs_for(it)

    def penempat_tambah(self):
        """
        Penentu letak berkas baru, dipakai bersama sepanjang satu batch.

        Satu seretan folder mengirim ratusan permintaan terpisah. Membuat
        penempat baru di tiap permintaan berarti tiap berkas dibagi seolah ia
        satu-satunya yang ditambahkan, dan seluruhnya akan mendarat di split
        yang sama.
        """
        from .services import tambah
        if self._penempat is None or self._penempat.src != self.src:
            self._penempat = tambah.Penempat(self.src)
        return self._penempat

    def reload(self) -> list[dict]:
        if self.src is None:
            return []
        return self.load(self.src)

    def periksa_disk(self) -> bool:
        """
        Susulkan perubahan, TERMASUK yang ditulis di luar aplikasi.

        Ini yang membuat memuat ulang halaman di peramban setara dengan tombol
        "Pindai ulang" yang dulu ada di atas grid. Tombol itu dibuang karena
        menekan sebuah tombol untuk melihat keadaan yang sebenarnya adalah
        pekerjaan yang tidak perlu diminta ke orang — tetapi membuangnya hanya
        benar kalau memuat ulang halaman memang menggantikannya, dan sebelum
        ini tidak.

        Dua jalur, murah dulu. `segarkan()` menangani yang ditulis aplikasi ini
        sendiri dan cuma membaca ulang gambar yang berubah. Yang tersisa —
        berkas yang disunting AnyLabeling desktop, disalin, atau di-rsync —
        tidak menaikkan `cap` sama sekali, dan itu ditangkap tanda tangan
        folder lalu dijawab dengan pemindaian penuh.
        """
        if self.src is None:
            return False
        berubah = self.segarkan()
        sidik = sidik_disk(self.src)
        if sidik == self._sidik:
            return berubah
        with self.lock:
            self.load(self.src)
        return True

    def _bangun_indeks(self) -> None:
        """Petakan path-terselesaikan -> item, sekali per pemindaian.

        muat_ulang_item mengubah isi item DI TEMPAT (objeknya tetap), jadi
        indeks ini tidak basi saat anotasi berubah; hanya penambahan atau
        penghapusan gambar yang mengubah daftar, dan keduanya lewat load()
        penuh yang membangunnya ulang.
        """
        peta: dict[str, dict] = {}
        for it in self.items:
            try:
                peta[str(it["img"].resolve())] = it
            except (OSError, ValueError):
                continue
        self._by_path = peta

    def find(self, path: str) -> dict | None:
        """
        Cari item berdasarkan path. Hanya berkas yang ada di dataset yang
        sedang dibuka akun ini yang bisa ditemukan — inilah yang mencegah
        satu akun membaca berkas sembarangan di disk server.

        Lewat indeks path->item, bukan sapuan linear: rute /thumb memanggil
        ini sekali per gambar, dan satu halaman job bisa memuat sebelas ribu
        thumbnail sekaligus.
        """
        try:
            rp = str(Path(path).resolve())
        except (OSError, ValueError):
            return None
        it = self._by_path.get(rp)
        if it is not None:
            return it
        # Jaring pengaman: indeks dibangun di load(), dan kalau suatu jalur
        # menyisipkan item tanpa lewat sana, sapuan lama tetap menemukannya
        # daripada mengembalikan "tidak ada" yang keliru.
        for cand in self.items:
            try:
                if str(cand["img"].resolve()) == rp:
                    self._by_path[rp] = cand
                    return cand
            except (OSError, ValueError):
                continue
        return None

    # -- berkas milik akun --

    def reset_thumbs(self) -> None:
        """Kosongkan berkas milik akun ini.

        Thumbnail SENDIRI tidak lagi di sini — ia dipakai bersama semua akun
        (lihat render.dir_bersama) dan tidak boleh dihapus hanya karena satu
        orang berpindah dataset. Yang tinggal di folder akun cuma labels.txt.
        """
        shutil.rmtree(self.thumbdir, ignore_errors=True)
        self.thumbdir.mkdir(parents=True, exist_ok=True)

    def drop_thumbs_for(self, item: dict) -> None:
        """Sapu thumbnail lama sebuah gambar dari cache bersama.

        Ini BUKAN lagi yang membuat cache-nya benar: kunci thumbnail
        diturunkan dari waktu-ubah dan ukuran berkasnya, jadi anotasi yang
        berubah sudah otomatis menghasilkan nama baru dan yang lama tidak
        akan pernah terbaca lagi. Yang dikerjakan di sini cuma membereskan
        berkas yatimnya, supaya ia tidak menumpuk selama server hidup.
        """
        from .services import render

        for f in render.dir_bersama().glob(f"{scanner.item_key(item)}_*.jpg"):
            f.unlink(missing_ok=True)

    def upload_dir(self, ds: str) -> Path:
        """Folder projek tujuan unggahan.

        Nama projeknya dibersihkan dengan aturan yang SAMA seperti halaman
        projek (services.projek.bersihkan_nama), bukan safe_slug. Keduanya
        berbeda pada spasi: safe_slug menggantinya dengan tanda hubung. Projek
        bernama "Coba Alur Baru" karena itu menerima unggahannya ke folder lain
        bernama "Coba-Alur-Baru", dan di halaman projek keduanya muncul sebagai
        dua kartu terpisah tanpa ada yang salah di layar mana pun.

        Nama akun tetap memakai safe_slug: akun memang selalu berupa slug.
        """
        from .services.projek import bersihkan_nama, temukan

        # Nama boleh berbentuk "pemilik/projek" untuk projek yang mengundang
        # akun ini. Tanpa itu, mengunggah dari halaman projek tamu dijawab
        # berhasil tetapi berkasnya mendarat di projek SENDIRI yang kebetulan
        # bernama sama, dan tidak ada satu layar pun yang menyebutkannya.
        # Bentuk "pemilik/projek" hanya sah kalau pemiliknya memang akun ini.
        # Mengunggah berarti mengubah isi projek, dan itu pengelolaan — tamu
        # yang diundang boleh melabeli, tidak boleh menambah gambar. Dulu
        # bentuk ini diterima apa adanya, sehingga tamu menulis berkas ke
        # projek pemiliknya padahal /api/simpan menolaknya untuk gambar yang
        # sama.
        if "/" in (ds or ""):
            pemilik, _, nama = ds.partition("/")
            if bersihkan_nama(pemilik) != safe_slug(self.user):
                # Diarahkan ke nama yang jelas bukan projek siapa pun, supaya
                # kegagalannya terlihat alih-alih mendarat diam-diam di projek
                # lain milik pengunggahnya sendiri.
                return (self.settings.uploads_root / safe_slug(self.user)
                        / bersihkan_nama(ds.replace("/", "-")))
            ds = nama
        return (self.settings.uploads_root / safe_slug(self.user)
                / bersihkan_nama(ds))


# Sesi BERTAHAN melewati restart PROSES (deploy), tetapi dibuang saat mesin
# REBOOT (hard restart). Keduanya dibedakan dari waktu-boot mesin: kalau sesi
# ditulis pada boot yang BERBEDA dari boot sekarang, mesin sudah reboot sejak
# itu -> buang semua (orang login ulang + mendarat di landing). Deploy (ganti
# proses tanpa reboot) -> boot sama -> sesi dipulihkan -> tak perlu login lagi.
#
# Dulu sesi murni di memori (hilang tiap restart). Itu disengaja demi keamanan:
# tak ada sesi menggantung yang tak bisa dicabut. Properti itu DIPERTAHANKAN,
# cuma pemicunya dipindah dari "restart apa pun" ke "reboot mesin" (+ TTL +
# LABELAPP_SESI_RESET untuk mencabut manual tanpa reboot).
_SESI_TTL_HARI = 30


def _btime() -> int:
    """Waktu-boot mesin (epoch) dari /proc/stat. 0 kalau tak terbaca -> sesi
    tak dipulihkan (aman: jatuh ke perilaku lama, login ulang)."""
    try:
        for baris in Path("/proc/stat").read_text().splitlines():
            if baris.startswith("btime "):
                return int(baris.split()[1])
    except Exception:                                   # noqa: BLE001
        pass
    return 0


class SessionStore:
    """Peta sid (dari cookie) -> Session, aman dipakai dari banyak thread.

    Dipersistenkan ke berkas (lihat siapkan()) supaya bertahan melewati restart
    proses, kecuali mesin reboot.
    """

    def __init__(self):
        self._data: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._berkas: Path | None = None

    def _bangun(self, user: str, settings: Settings, ts: float | None = None) -> Session:
        # users.json dibaca SEKALI untuk ketiga medan yang dititipkan ke sesi
        # (peran, nama tampil, foto). Akun yang sudah tak ada -> tolak: jangan
        # pulihkan sesi untuk akun yang dihapus.
        from .security import is_admin, load_users
        users = load_users(settings.users_file)
        if user not in users:
            raise ValueError(f"akun {user!r} tidak ada")
        rec = users[user]
        sess = Session(user, settings)
        sess.admin = is_admin(users, user)
        sess.nama = (rec.get("nama") or "").strip() or user
        sess.foto = rec.get("foto") or ""
        sess._ts = ts or time.time()
        return sess

    def siapkan(self, settings: Settings, reset: bool = False) -> None:
        """Dipanggil SEKALI saat startup. Memulihkan sesi yang bertahan dari
        restart proses; mengosongkannya kalau mesin reboot (boot berubah) atau
        `reset` (LABELAPP_SESI_RESET) diminta -> login ulang + landing."""
        self._berkas = Path(settings.uploads_root) / "_sesi.json"
        if reset:
            self._hapus_berkas()
            return
        try:
            data = json.loads(self._berkas.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("boot") != _btime():
            self._hapus_berkas()                        # reboot / berkas aneh
            return
        batas = time.time() - _SESI_TTL_HARI * 86400
        pulih: dict[str, Session] = {}
        for sid, rec in (data.get("sesi") or {}).items():
            if not isinstance(rec, dict) or (rec.get("ts") or 0) < batas:
                continue
            try:
                pulih[sid] = self._bangun(rec.get("user") or "", settings,
                                          rec.get("ts"))
            except Exception:                           # noqa: BLE001
                continue                                # akun hilang/rusak
        with self._lock:
            self._data.update(pulih)
        if len(pulih) != len(data.get("sesi") or {}):
            self._tulis()                               # buang yang kedaluwarsa

    def _hapus_berkas(self) -> None:
        if self._berkas:
            try:
                self._berkas.unlink(missing_ok=True)
            except OSError:
                pass

    def _tulis(self) -> None:
        """Tulis peta sid->akun ke disk, atomik, chmod 600. Hanya sid + akun +
        waktu -- bukan src/items (mahal; dibangun ulang saat dipakai)."""
        if not self._berkas:
            return
        with self._lock:
            sesi = {sid: {"user": s.user, "ts": getattr(s, "_ts", 0) or time.time()}
                    for sid, s in self._data.items()}
        try:
            self._berkas.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._berkas.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"boot": _btime(), "sesi": sesi}),
                           encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self._berkas)
        except OSError:
            pass                                        # gagal persist != gagal login

    def create(self, user: str, settings: Settings) -> tuple[str, Session]:
        sid = secrets.token_urlsafe(32)
        sess = self._bangun(user, settings)
        with self._lock:
            self._data[sid] = sess
        self._tulis()                                   # di luar lock
        return sid, sess

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        with self._lock:
            return self._data.get(sid)

    def drop(self, sid: str | None) -> None:
        if not sid:
            return
        with self._lock:
            sess = self._data.pop(sid, None)
        if sess:
            shutil.rmtree(sess.thumbdir, ignore_errors=True)
            self._tulis()

    def users(self) -> list[str]:
        with self._lock:
            return sorted({s.user for s in self._data.values()})


store = SessionStore()
