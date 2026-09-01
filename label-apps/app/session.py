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


class Session:
    """Keadaan milik satu akun yang sedang login."""

    def __init__(self, user: str, settings: Settings):
        self.user = user
        self.settings = settings
        self.src: Path | None = None
        self.items: list[dict] = []
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
