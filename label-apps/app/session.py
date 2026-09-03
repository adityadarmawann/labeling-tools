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

import secrets
import shutil
import threading
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


def cap_sekarang(src) -> int:
    with _kunci_cap:
        return _cap_ubah.get(str(Path(src).resolve()), 0)


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


class Session:
    """Keadaan milik satu akun yang sedang login."""

    def __init__(self, user: str, settings: Settings):
        self.user = user
        self.settings = settings
        self.src: Path | None = None
        self.items: list[dict] = []
        # Nilai penanda perubahan saat isi ini dipindai. Lihat _cap_ubah.
        self.cap = 0
        self.names: dict[int, str] = {}
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
        self.projek_batal = False
        # Peran dibaca sekali saat sesi dibuat. Membacanya ulang di tiap
        # permintaan berarti membuka users.json puluhan kali per halaman.
        self.admin = False

    # -- dataset --

    def load(self, src: Path) -> list[dict]:
        """Pindai folder baru dan buang cache thumbnail folder sebelumnya."""
        self.src = Path(src).resolve()
        # Dicatat SEBELUM memindai: perubahan yang datang di tengah pemindaian
        # lebih baik memicu satu pemindaian berlebih daripada terlewat.
        self.cap = cap_sekarang(self.src)
        self.items, self.names = scanner.scan(self.src)
        # Penempat memegang jumlah gambar per split saat ia dibuat. Setelah
        # pemindaian ulang angka itu sudah usang, jadi dibuang — kalau tidak,
        # penambahan berikutnya membagi berdasarkan keadaan yang sudah lewat.
        self._penempat = None
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
        return True

    def muat_ulang_item(self, it: dict) -> None:
        """Baca ulang anotasi SATU gambar dari disk."""
        try:
            if it.get("yolo"):
                sh = scanner.read_yolo(it["labels"], it["W"], it["H"], self.names)
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

    def find(self, path: str) -> dict | None:
        """
        Cari item berdasarkan path. Hanya berkas yang ada di dataset yang
        sedang dibuka akun ini yang bisa ditemukan — inilah yang mencegah
        satu akun membaca berkas sembarangan di disk server.
        """
        try:
            rp = Path(path).resolve()
        except (OSError, ValueError):
            return None
        for it in self.items:
            if it["img"].resolve() == rp:
                return it
        return None

    # -- berkas milik akun --

    def reset_thumbs(self) -> None:
        shutil.rmtree(self.thumbdir, ignore_errors=True)
        self.thumbdir.mkdir(parents=True, exist_ok=True)

    def drop_thumbs_for(self, item: dict) -> None:
        for f in self.thumbdir.glob(f"{scanner.item_key(item)}_*.jpg"):
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


class SessionStore:
    """Peta sid (dari cookie) -> Session, aman dipakai dari banyak thread."""

    def __init__(self):
        self._data: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, user: str, settings: Settings) -> tuple[str, Session]:
        sid = secrets.token_urlsafe(32)
        sess = Session(user, settings)
        from .security import is_admin, load_users
        sess.admin = is_admin(load_users(settings.users_file), user)
        with self._lock:
            self._data[sid] = sess
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

    def users(self) -> list[str]:
        with self._lock:
            return sorted({s.user for s in self._data.values()})


store = SessionStore()
