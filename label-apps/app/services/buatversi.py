"""
Pembangkit versi dataset: menjalankan resep preprocessing + augmentasi pada
seluruh isi dataset, menulis hasilnya, dan melaporkan kemajuannya.

Perbedaan mendasar dari aug-bal-v14.py, dan semuanya disengaja:

  1. v14 MENIMPA gambar dan label sumbernya di tempat (f:1153-1155). Di sini
     sumber tidak pernah disentuh sama sekali; seluruh hasil ditulis ke
     `<projek>/.versi/vN/`. Itu yang membuat sebuah versi bisa dibuang tanpa
     menyisakan kerusakan, dan membuat dua versi bisa hidup berdampingan.

  2. v14 melacak asal-usul berkas lewat substring nama (`_aug`, `_bal`, `_p5`,
     …), sehingga gambar pemakai yang kebetulan bernama `foto_p5.jpg` salah
     diklasifikasi. Di sini asal-usul dicatat di MANIFES.json.

  3. v14 memanggil np.random.seed di tingkat modul (f:579-580), yang mengubah
     RNG SELURUH proses. Di server itu berarti dua pekerjaan paralel saling
     merusak. Di sini setiap pekerjaan memegang Random-nya sendiri.

  4. v14 hanya menyentuh `train/`. Di sini preprocessing berlaku ke train,
     valid, dan test; augmentasi hanya ke train.
"""
from __future__ import annotations

import json
import random
import shutil
import threading
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from ..log import catat
from . import export, olah

log = catat("labelapp.buatversi")

MUTU_JPEG = 92
SPLIT = ("train", "valid", "test")

# Fase dan bobotnya pada bilah kemajuan. Angkanya perkiraan porsi waktu, dan
# hanya dipakai supaya bilahnya tidak melompat — bukan janji.
FASE = [
    ("pra",   "Preprocessing"),
    ("aug",   "Augmentasi"),
    ("crop",  "Crop & zoom"),
    ("skala", "Menyeimbangkan ukuran objek"),
    ("kelas", "Menyeimbangkan jumlah kelas"),
    ("neg",   "Memulihkan porsi sampel negatif"),
    ("tutup", "Menulis manifes"),
]

# ------------------------------------------------------------------ kemajuan
# Pola yang sama dengan split.py dan impor.py: dict modul berkunci akun,
# dilindungi lock. Hilang kalau proses mati, dan itu memang yang diinginkan —
# tidak ada yang tertinggal untuk dibersihkan.
_maju: dict[str, dict] = {}
_kunci = threading.Lock()


def catat_maju(kunci: str, **nilai) -> None:
    with _kunci:
        d = _maju.setdefault(kunci, {})
        d.update(nilai)
        d["saat"] = time.time()


def kemajuan(kunci: str) -> dict:
    with _kunci:
        return dict(_maju.get(kunci) or {})


def bersihkan_maju(kunci: str) -> None:
    with _kunci:
        _maju.pop(kunci, None)


class Dibatalkan(Exception):
    """Dilempar dari dalam pekerjaan saat pemakai menekan Hentikan."""


# ------------------------------------------------------------------- berkas
def dir_versi(ds: Path, nomor: int) -> Path:
    return Path(ds) / ".versi" / f"v{nomor}"


def ukuran_versi(ds: Path, nomor: int) -> int:
    d = dir_versi(ds, nomor)
    if not d.exists():
        return 0
    total = 0
    for p in d.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def buang_hasil(ds: Path, nomor: int) -> None:
    shutil.rmtree(dir_versi(ds, nomor), ignore_errors=True)


def _tulis(dirv: Path, split: str, nama: str, img, label, segmentasi: bool) -> None:
    ip = dirv / split / "images" / f"{nama}.jpg"
    ip.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(ip), img, [int(cv2.IMWRITE_JPEG_QUALITY), MUTU_JPEG])
    olah.tulis_label(dirv / split / "labels" / f"{nama}.txt", label, segmentasi)


def _baca_item(it: dict):
    """
    Satu item hasil pindai -> (img BGR, label ternormalkan).

    Bentuk `shape` aplikasi ini (pts piksel) diubah ke poligon ternormalkan,
    yaitu bentuk yang dipakai seluruh mesin olah.
    """
    img = cv2.imread(str(it["img"]))
    if img is None:
        return None, None
    h, w = img.shape[:2]
    label = []
    for s in it.get("shapes") or []:
        if s.get("label") is None:
            continue
        pts = s.get("pts")
        if pts is None or len(pts) < 2:
            continue
        koor = []
        for x, y in np.asarray(pts, dtype=np.float64):
            koor.extend((float(x) / w, float(y) / h))
        if len(koor) == 4:              # garis/2 titik -> kotak pembungkus
            x1, y1, x2, y2 = koor
            koor = [x1, y1, x2, y1, x2, y2, x1, y2]
        if len(koor) >= 6:
            label.append((s["label"], koor))
    return img, label


# ============================================================ pekerjaan utama
class Pekerjaan:
    """
    Satu pembuatan versi. Seluruh keadaan dipegang instance ini — tidak ada
    global yang dimutasi, jadi dua pekerjaan boleh berjalan bersamaan tanpa
    saling merusak keacakannya.
    """

    def __init__(self, ds: Path, nomor: int, items: list[dict], names: dict,
                 resep: dict, peta_split: dict, *, kunci: str, seed: int = 42,
                 batal=None):
        self.ds = Path(ds)
        self.nomor = nomor
        self.items = items
        self.names = names or {}
        self.resep = resep or {}
        self.peta = peta_split or {}
        self.kunci = kunci
        self.rng = random.Random(seed)
        self.np_rng = np.random.default_rng(seed)
        self._batal = batal or (lambda: False)
        self.dirv = dir_versi(ds, nomor)
        self.segmentasi = any(
            (s.get("type") or "") not in ("rectangle",)
            for it in items for s in (it.get("shapes") or []))
        self.manifes: list[dict] = []
        # Indeks kelas lewat jalur yang sama dengan ekspor. Dulu dipetakan
        # langsung dari self.names, dan itu punya dua lubang: dataset labelme
        # TANPA data.yaml/classes.txt memberi names kosong, sehingga kelas_idx
        # menjawab None untuk setiap bentuk dan SELURUH objek terbuang tanpa
        # satu pun pesan — versinya jadi dataset tanpa anotasi. Lubang kedua:
        # label yang ada di anotasi tetapi tidak tercantum di data.yaml ikut
        # hilang. export.peta_kelas menutup keduanya, dan memakainya berarti
        # indeks versi selalu sama dengan indeks ekspor biasa.
        self.idx = export.peta_kelas(items, self.names)
        self.names = {i: n for n, i in self.idx.items()}
        self.n_kelas = len(self.idx)
        self._t0 = time.time()
        self._langkah_selesai = 0
        self._langkah_total = 1

    # ---------------------------------------------------------- pembantu
    def cek(self):
        if self._batal():
            raise Dibatalkan()

    def maju(self, fase: str, n: int = 0, total: int = 0, **sisa):
        i = [k for k, _ in FASE].index(fase)
        dalam = (n / total) if total else 0.0
        persen = round((i + min(dalam, 1.0)) / len(FASE) * 100, 1)
        catat_maju(self.kunci, fase=fase, fase_nama=dict(FASE)[fase],
                   fase_ke=i + 1, fase_dari=len(FASE),
                   n=n, total=total, persen=persen,
                   detik=round(time.time() - self._t0, 1), **sisa)

    def kelas_idx(self, nama) -> int | None:
        if nama in self.idx:
            return self.idx[nama]
        if isinstance(nama, int):
            return nama
        return None

    def _simpan(self, split: str, nama: str, img, label, asal: str, sumber: str):
        _tulis(self.dirv, split, nama, img, label, self.segmentasi)
        self.manifes.append({"berkas": f"{split}/images/{nama}.jpg", "split": split,
                             "asal": asal, "sumber": sumber, "objek": len(label)})

    # ------------------------------------------------------------- Fase 0
    def fase_pra(self):
        """
        Preprocessing: SETIAP gambar, SEMUA split, deterministik.

        Inilah satu-satunya fase yang menyentuh valid dan test. Kalau ia tidak
        deterministik, dua versi tidak bisa dibandingkan angkanya — dan itu
        sebabnya letterbox di sini memakai warna tetap, bukan pelat acak
        seperti v14.
        """
        total = len(self.items)
        dibuang = 0
        self.asli: dict[str, list[str]] = {s: [] for s in SPLIT}
        for i, it in enumerate(self.items):
            self.cek()
            if i % 25 == 0:
                self.maju("pra", i, total, dibuang=dibuang)
            nama_berkas = it["img"].name
            split = self.peta.get(nama_berkas) or self.peta.get(it["img"].stem) or "train"
            img, label = _baca_item(it)
            if img is None:
                dibuang += 1
                continue
            # label pakai nama kelas -> indeks
            num = []
            for c, poli in label:
                ci = self.kelas_idx(c)
                if ci is not None:
                    num.append((ci, poli))
            hasil = olah.terapkan_pra(img, num, self.resep, self.n_kelas)
            if hasil is None:
                dibuang += 1
                continue
            img, num = hasil
            stem = Path(nama_berkas).stem
            self._simpan(split, stem, img, num, "asli", nama_berkas)
            self.asli[split].append(stem)
        self.maju("pra", total, total, dibuang=dibuang)
        return dibuang

    # ------------------------------------------------------------- Fase 1
    def fase_aug(self):
        """
        Augmentasi dasar: hanya train, N salinan per gambar.

        Gambar tanpa objek IKUT diaugmentasi — itu sampel negatif, dan variasi
        lampu pada latar kosong justru yang mengajari model menolak ruang
        kosong. v14 melakukan hal yang sama (f:1181-1194).
        """
        n_salin = int((self.resep.get("volume") or {}).get("per_gambar", 1))
        if n_salin <= 0:
            self.maju("aug", 0, 0)
            return 0
        pipeline = olah.bangun_pipeline(self.resep)
        if pipeline is None:
            self.maju("aug", 0, 0)
            return 0
        sumber = list(self.asli["train"])
        total = len(sumber) * n_salin
        jadi = gagal = 0
        for i, stem in enumerate(sumber):
            self.cek()
            img, label = self._muat_hasil("train", stem)
            if img is None:
                continue
            for k in range(n_salin):
                if (jadi + gagal) % 25 == 0:
                    self.maju("aug", jadi + gagal, total, jadi=jadi, gagal=gagal)
                hasil = self._coba_aug(img, label, pipeline)
                if hasil is None:
                    gagal += 1
                    continue
                g, l = hasil
                self._simpan("train", f"{stem}_aug{k}", g, l, "aug", stem)
                jadi += 1
        self.maju("aug", total, total, jadi=jadi, gagal=gagal)
        return jadi

    def _coba_aug(self, img, label, pipeline):
        """Augmentasi dengan percobaan ulang, persis MAKS_ULANG_AUG v14."""
        for _ in range(olah.MAKS_ULANG_AUG):
            self.cek()
            hasil = olah.augmentasi_sekali(img, label, pipeline)
            if hasil is None:
                continue
            g, l = hasil
            if label and not olah.objek_terbaca(g, l):
                continue
            return g, l
        return None

    def _muat_hasil(self, split: str, stem: str):
        """Baca kembali berkas yang sudah ditulis fase sebelumnya."""
        ip = self.dirv / split / "images" / f"{stem}.jpg"
        img = cv2.imread(str(ip))
        if img is None:
            return None, None
        return img, olah.baca_label(self.dirv / split / "labels" / f"{stem}.txt")

    # ------------------------------------------------------------- Fase 5
    def _tempel(self, kanvas, kecil, label_kecil, ox, oy):
        """
        Tempel `kecil` ke `kanvas` di (ox, oy) lewat masker poligon objeknya,
        bukan sebagai petak persegi.

        Petak persegi meninggalkan bingkai tajam berisi latar asal foto, dan
        model belajar mendeteksi bingkai itu, bukan objeknya. Masker dilebarkan
        lalu dikaburkan (TEMPEL_LEMBUT) supaya tepinya tidak jadi garis.
        """
        kh, kw = kecil.shape[:2]
        H, W = kanvas.shape[:2]
        if kh <= 0 or kw <= 0 or oy + kh > H or ox + kw > W:
            return kanvas
        if not label_kecil:
            kanvas[oy:oy + kh, ox:ox + kw] = kecil
            return kanvas
        m = np.zeros((kh, kw), np.uint8)
        for _c, poli in label_kecil:
            titik = np.array([[poli[i] * kw, poli[i + 1] * kh]
                              for i in range(0, len(poli), 2)], np.int32)
            if len(titik) >= 3:
                cv2.fillPoly(m, [titik], 255)
        k = olah.TEMPEL_LEMBUT * 2 + 1
        m = cv2.dilate(m, np.ones((k, k), np.uint8))
        m = cv2.GaussianBlur(m, (k, k), 0)
        a = (m.astype(np.float32) / 255.0)[:, :, None]
        petak = kanvas[oy:oy + kh, ox:ox + kw].astype(np.float32)
        kanvas[oy:oy + kh, ox:ox + kw] = (
            kecil.astype(np.float32) * a + petak * (1 - a)).astype(np.uint8)
        return kanvas

    def _potong_keras(self, img, label):
        """Potong satu sisi acak 20–45%, lalu letterbox hasilnya ke pelat RVM."""
        h, w = img.shape[:2]
        f = self.rng.uniform(0.20, 0.45)
        sisi = self.rng.choice(("kiri", "kanan", "atas", "bawah"))
        x1, y1, x2, y2 = 0, 0, w, h
        if sisi == "kiri":
            x1 = int(w * f)
        elif sisi == "kanan":
            x2 = int(w * (1 - f))
        elif sisi == "atas":
            y1 = int(h * f)
        else:
            y2 = int(h * (1 - f))
        if x2 - x1 < 32 or y2 - y1 < 32:
            return None
        potong = img[y1:y2, x1:x2]
        ph, pw = potong.shape[:2]
        S = max(h, w)
        s = min(S / pw, S / ph)
        nw, nh = int(pw * s), int(ph * s)
        kanvas = olah.kanvas_latar(S, S, self.rng)
        pl, pt = (S - nw) // 2, (S - nh) // 2
        kanvas[pt:pt + nh, pl:pl + nw] = cv2.resize(potong, (nw, nh),
                                                    interpolation=cv2.INTER_AREA)
        baru = []
        for c, poli in label:
            koor = []
            for i in range(0, len(poli), 2):
                koor.append(((poli[i] * w - x1) * s + pl) / S)
                koor.append(((poli[i + 1] * h - y1) * s + pt) / S)
            baru.append((c, koor))
        # Dipotong ke KOTAK ISI, bukan ke bingkai: di luar kotak isi itu pelat
        # latar, dan objek tidak mungkin ada di sana.
        kotak = (pl / S, pt / S, (pl + nw) / S, (pt + nh) / S)
        return kanvas, olah.saring_label(baru, kotak)

    def _zoom_keluar(self, img, label):
        """Kecilkan seluruh frame lalu tempel di posisi acak pada pelat RVM."""
        h, w = img.shape[:2]
        S = max(h, w)
        sf = self.rng.uniform(0.20, 0.55)
        nw, nh = max(8, int(w * sf)), max(8, int(h * sf))
        kecil = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
        kanvas = olah.kanvas_latar(S, S, self.rng)
        m = int(S * 0.08)
        ox = self.rng.randint(m, max(m, S - nw - m)) if S - nw - 2 * m > 0 else m
        oy = self.rng.randint(m, max(m, S - nh - m)) if S - nh - 2 * m > 0 else m
        kecil_label = [(c, list(poli)) for c, poli in label]
        kanvas = self._tempel(kanvas, kecil, kecil_label, ox, oy)
        baru = []
        for c, poli in label:
            koor = []
            for i in range(0, len(poli), 2):
                koor.append((poli[i] * nw + ox) / S)
                koor.append((poli[i + 1] * nh + oy) / S)
            baru.append((c, koor))
        return kanvas, olah.saring_label(baru)

    def fase_crop(self):
        """
        Crop keras dan zoom-keluar pada sebagian pool train.

        Gambar tanpa objek DILEWATI di sini, sama seperti v14 (f:1461):
        memotong dan mengecilkan latar kosong tidak menambah informasi apa pun.
        """
        par = (self.resep.get("fase") or {}).get("crop_zoom") or {}
        if not par.get("aktif", True):
            self.maju("crop", 0, 0)
            return 0
        porsi = float(par.get("porsi", 0.20))
        kolam = [s for s in self.asli["train"]
                 if olah.baca_label(self.dirv / "train" / "labels" / f"{s}.txt")]
        n = int(len(kolam) * porsi)
        if n <= 0:
            self.maju("crop", 0, 0)
            return 0
        pilih = self.rng.sample(kolam, n)
        jadi = 0
        for i, stem in enumerate(pilih):
            self.cek()
            if i % 10 == 0:
                self.maju("crop", i, n, jadi=jadi)
            img, label = self._muat_hasil("train", stem)
            if img is None or not label:
                continue
            mode = self.rng.choice(("crop", "zoom"))
            hasil = self._potong_keras(img, label) if mode == "crop" \
                else self._zoom_keluar(img, label)
            if not hasil or not hasil[1]:
                continue
            g, l = hasil
            if self.rng.random() < olah.FISHEYE_PROB:
                g, l, _ = olah.terapkan_fisheye(g, l, rng=self.rng)
                if not l:
                    continue
            if not olah.objek_terbaca(g, l):
                continue
            self._simpan("train", f"{stem}_p5{mode}", g, l, "crop_zoom", stem)
            jadi += 1
        self.maju("crop", n, n, jadi=jadi)
        return jadi

    # -------------------------------------------------- Fase A / 5B / 5C
    def _katalog_skala(self):
        """
        Katalog (stem, kelas, luas) untuk seluruh train yang punya objek.
        Dipakai kedua fase skala. Gambar negatif tidak masuk katalog.
        """
        kat = []
        for stem in list(self.asli["train"]):
            lab = olah.baca_label(self.dirv / "train" / "labels" / f"{stem}.txt")
            for c, poli in lab:
                luas = olah.luas_poligon(poli)
                if luas >= 0.004:            # MIN_OBJ_AREA_ABS v14 f:1504
                    kat.append((stem, c, luas))
        return kat

    def _ubah_skala(self, img, label, faktor):
        """
        Perbesar/perkecil objek dengan menskalakan seluruh frame.

        Cabang mengecil menempel ke pelat RVM; cabang membesar mengambil
        jendela berpusat pada objek. v14 (f:1728-1740) melewatkan pelat DAN
        iluminan komposit di cabang membesar, sehingga varian membesar tidak
        mendapat perbaikan domain sama sekali — di sini keduanya diperlakukan
        sama.
        """
        h, w = img.shape[:2]
        S = max(h, w)
        nw, nh = max(8, int(w * faktor)), max(8, int(h * faktor))
        skala = cv2.resize(img, (nw, nh),
                           interpolation=cv2.INTER_AREA if faktor < 1 else cv2.INTER_CUBIC)
        if faktor <= 1.0:
            kanvas = olah.kanvas_latar(S, S, self.rng)
            ox = self.rng.randint(0, max(0, S - nw))
            oy = self.rng.randint(0, max(0, S - nh))
            kanvas = self._tempel(kanvas, skala, [(c, list(p)) for c, p in label], ox, oy)
            baru = [(c, [v for i in range(0, len(p), 2)
                         for v in ((p[i] * nw + ox) / S, (p[i + 1] * nh + oy) / S)])
                    for c, p in label]
            return kanvas, olah.saring_label(baru)
        # membesar: jendela S×S berpusat di objek pertama
        cx = int(np.mean(label[0][1][0::2]) * nw)
        cy = int(np.mean(label[0][1][1::2]) * nh)
        sx = min(max(0, cx - S // 2), max(0, nw - S))
        sy = min(max(0, cy - S // 2), max(0, nh - S))
        jendela = skala[sy:sy + S, sx:sx + S]
        if jendela.shape[0] < S or jendela.shape[1] < S:
            kanvas = olah.kanvas_latar(S, S, self.rng)
            kanvas[:jendela.shape[0], :jendela.shape[1]] = jendela
            jendela = kanvas
        baru = [(c, [v for i in range(0, len(p), 2)
                     for v in ((p[i] * nw - sx) / S, (p[i + 1] * nh - sy) / S)])
                for c, p in label]
        return jendela, olah.saring_label(baru)

    def _varian_skala(self, stem, luas_asal, luas_sasaran, tanda):
        f = float(np.sqrt(luas_sasaran / max(luas_asal, 1e-9)))
        if not (1 / 3.0 <= f <= 3.0):          # MAX_SCALE_FACTOR v14 f:1503
            return False
        img, label = self._muat_hasil("train", stem)
        if img is None or not label:
            return False
        g, l = self._ubah_skala(img, label, f)
        if not l or not olah.objek_terbaca(g, l):
            return False
        self._simpan("train", f"{stem}_{tanda}", g, l, "skala", stem)
        return True

    def fase_skala(self):
        """
        Dua langkah v14 digabung: sapu menyeluruh (5C) lalu penambalan defisit
        per bin (5B). Yang diseimbangkan SEBARAN UKURAN objek, bukan jumlahnya.
        """
        par = (self.resep.get("fase") or {}).get("balans_skala") or {}
        if not par.get("aktif", True):
            self.maju("skala", 0, 0)
            return 0
        n_kecil = int(par.get("sapu_kecil", 2))
        n_besar = int(par.get("sapu_besar", 1))
        kat = self._katalog_skala()
        if not kat:
            self.maju("skala", 0, 0)
            return 0

        # 5C — satu entri per GAMBAR, diwakili objek terbesarnya (v14 f:1780).
        per_gambar: dict[str, tuple] = {}
        for stem, c, luas in kat:
            if stem not in per_gambar or luas > per_gambar[stem][1]:
                per_gambar[stem] = (c, luas)
        total = len(per_gambar) * (n_kecil + n_besar)
        jadi = 0
        i = 0
        for stem, (_c, luas) in per_gambar.items():
            self.cek()
            for k in range(n_kecil):
                i += 1
                if i % 10 == 0:
                    self.maju("skala", i, total, jadi=jadi)
                jadi += self._varian_skala(
                    stem, luas, self.rng.uniform(0.012, 0.055), f"swout{k}")
            for k in range(n_besar):
                i += 1
                jadi += self._varian_skala(
                    stem, luas, self.rng.uniform(0.10, 0.30), f"swin{k}")

        # 5B — tambal defisit per (kelas, bin), acuan "union" seperti v14.
        bins = np.logspace(np.log10(0.005), np.log10(0.60), 13)
        hist: dict[int, np.ndarray] = defaultdict(lambda: np.zeros(12))
        sumber: dict[int, list] = defaultdict(list)
        for stem, c, luas in self._katalog_skala():
            b = int(np.clip(np.searchsorted(bins, luas) - 1, 0, 11))
            hist[c][b] += 1
            sumber[c].append((stem, luas))
        if hist:
            prop = {c: (h / max(h.sum(), 1)) for c, h in hist.items()}
            acuan = np.max(np.stack(list(prop.values())), axis=0)
            acuan = acuan / max(acuan.sum(), 1e-9)
            for c, h in hist.items():
                self.cek()
                tot = h.sum()
                for b in range(12):
                    butuh = int(max(acuan[b] * tot - h[b], 0))
                    if butuh <= 0 or not sumber[c]:
                        continue
                    butuh = min(butuh, 40)     # rem: v14 tidak punya, dan pada
                                               # dataset kecil defisitnya meledak
                    gagal = 0
                    for n in range(butuh):
                        if gagal >= butuh * 4:
                            break
                        self.cek()
                        stem, luas = sumber[c][(n + gagal) % len(sumber[c])]
                        sasaran = float(self.rng.uniform(bins[b], bins[b + 1]))
                        if self._varian_skala(stem, luas, sasaran, f"sc{c}b{b}_{n}"):
                            jadi += 1
                        else:
                            gagal += 1
        self.maju("skala", total, total, jadi=jadi)
        return jadi

    # ------------------------------------------------------------- Fase 3
    def fase_kelas(self):
        """
        Seimbangkan JUMLAH OBJEK per kelas — inilah yang membuat namanya
        "aug-bal".

        Sasaran bawaan p75, sama seperti v14 (f:2357). Sumber diputar melingkar
        supaya tiap gambar dipakai adil, dan kuota dihitung dalam OBJEK bukan
        gambar — satu gambar bisa menyumbang beberapa objek kelas yang sama.

        v14 memakai rem tetap MAX_BALANCE_PER_CLASS=1500, tetapi angka itu
        dikalibrasi untuk 8.889 gambar (komentar f:564-575). Rem di sini
        mengikuti ukuran dataset, bukan angka mati yang benar untuk satu
        dataset saja.
        """
        par = (self.resep.get("fase") or {}).get("balans_kelas") or {}
        if not par.get("aktif", True):
            self.maju("kelas", 0, 0)
            return 0
        sasaran_mode = par.get("sasaran", "p75")

        # Dihitung dari SELURUH isi train saat ini, bukan dari gambar asli:
        # fase-fase sebelumnya sudah memperbanyak tiap kelas secara
        # proporsional, jadi menghitung dari yang asli berarti menambal
        # ketimpangan dengan angka yang sudah basi — dan ketimpangannya lolos
        # utuh ke hasil akhir. v14 memindai ulang folder di Fase 2 (f:2336)
        # persis karena ini.
        #
        # SUMBER tetap hanya gambar asli (v14 f:2381-2383): mengaugmentasi
        # hasil augmentasi menumpuk distorsi di atas distorsi.
        hitung: dict[int, int] = defaultdict(int)
        berkas_kelas: dict[int, list[str]] = defaultdict(list)
        asli = set(self.asli["train"])
        for p_lab in sorted((self.dirv / "train" / "labels").glob("*.txt")):
            lab = olah.baca_label(p_lab)
            kelas_di_sini = set()
            for c, _ in lab:
                hitung[c] += 1
                kelas_di_sini.add(c)
            if p_lab.stem in asli:
                for c in kelas_di_sini:
                    berkas_kelas[c].append(p_lab.stem)
        if len(hitung) < 2:
            self.maju("kelas", 0, 0)
            return 0

        nilai = sorted(hitung.values())
        if sasaran_mode == "max":
            sasaran = nilai[-1]
        elif sasaran_mode == "median":
            sasaran = int(np.median(nilai))
        else:
            sasaran = int(np.percentile(nilai, 75))
        rem = int(par.get("maks_per_kelas") or max(50, len(self.asli["train"]) // 2))

        pipeline = olah.bangun_pipeline(self.resep)
        rencana = {c: min(max(sasaran - n, 0), rem) for c, n in hitung.items()}
        total = sum(rencana.values())
        jadi = 0
        sudah = 0
        for c, butuh in rencana.items():
            if butuh <= 0 or not berkas_kelas[c] or pipeline is None:
                continue
            srcs = berkas_kelas[c]
            dibuat_obj = 0
            jaga = 0
            i = 0
            while dibuat_obj < butuh and jaga < butuh * 4:
                self.cek()
                sudah += 1
                if sudah % 20 == 0:
                    self.maju("kelas", sudah, total, jadi=jadi, sasaran=sasaran)
                stem = srcs[i % len(srcs)]
                i += 1
                img, label = self._muat_hasil("train", stem)
                if img is None or not label:
                    jaga += 1
                    continue
                hasil = self._coba_aug(img, label, pipeline)
                if hasil is None:
                    jaga += 1
                    continue
                g, l = hasil
                self._simpan("train", f"{stem}_bal{c}_{jadi}", g, l, "balans_kelas", stem)
                jadi += 1
                dibuat_obj += max(1, sum(1 for cc, _ in l if cc == c))
        self.maju("kelas", total, total, jadi=jadi, sasaran=sasaran)
        return jadi

    # ------------------------------------------------------------- Fase 7
    def fase_negatif(self):
        """
        Pulihkan porsi sampel negatif.

        Fase-fase sebelumnya hanya memperbanyak gambar BERONJEK: crop/zoom,
        skala, dan balans kelas semuanya melewati gambar kosong. Akibatnya
        porsi negatif yang dirancang menyusut — terukur di v14 dari 17,8%
        menjadi 7,0% (komentar f:2433-2439). Fase ini mengembalikannya ke
        porsi dataset ASLI.
        """
        par = (self.resep.get("fase") or {}).get("porsi_negatif") or {}
        if not par.get("aktif", True):
            self.maju("neg", 0, 0)
            return 0

        neg_asli = [s for s in self.asli["train"]
                    if not olah.baca_label(self.dirv / "train" / "labels" / f"{s}.txt")]
        if not neg_asli:
            self.maju("neg", 0, 0)
            return 0
        pos0 = len(self.asli["train"]) - len(neg_asli)
        porsi0 = len(neg_asli) / max(len(self.asli["train"]), 1)
        sasaran_porsi = par.get("porsi")
        sasaran_porsi = porsi0 if sasaran_porsi is None else float(sasaran_porsi)
        if sasaran_porsi >= 0.999:
            self.maju("neg", 0, 0)
            return 0

        kini = list((self.dirv / "train" / "labels").glob("*.txt"))
        neg_kini = sum(1 for p in kini if p.stat().st_size == 0)
        pos_kini = len(kini) - neg_kini
        neg_sasaran = int(round(sasaran_porsi * pos_kini / max(1 - sasaran_porsi, 1e-9)))
        butuh = max(0, neg_sasaran - neg_kini)
        if not butuh:
            self.maju("neg", 0, 0, porsi=round(porsi0, 4))
            return 0

        pipeline = olah.bangun_pipeline(self.resep)
        jadi = jaga = 0
        i = 0
        while jadi < butuh and jaga < butuh * 4 and pipeline is not None:
            self.cek()
            if (jadi + jaga) % 20 == 0:
                self.maju("neg", jadi, butuh, porsi_asal=round(porsi0, 4))
            stem = neg_asli[i % len(neg_asli)]
            i += 1
            img, _ = self._muat_hasil("train", stem)
            if img is None:
                jaga += 1
                continue
            hasil = olah.augmentasi_sekali(img, [], pipeline)
            if hasil is None:
                jaga += 1
                continue
            self._simpan("train", f"{stem}_neg{jadi}", hasil[0], [], "negatif", stem)
            jadi += 1
        self.maju("neg", butuh, butuh, jadi=jadi, porsi_asal=round(porsi0, 4))
        return jadi

    # ------------------------------------------------------------- penutup
    def tutup(self, catatan: str = "") -> dict:
        """data.yaml, MANIFES.json, dan ringkasan angka."""
        self.maju("tutup", 0, 1)
        # Ganti nama dikerjakan DI SINI, bukan di _pra_ubah_kelas: yang diganti
        # adalah daftar nama di data.yaml, sedangkan label per gambar menyimpan
        # indeks. Menggantinya lebih awal akan merusak self.idx, yang memetakan
        # nama ASLI bentuk ke indeks.
        uk = ((self.resep.get("pra") or {}).get("ubah_kelas") or {})
        ganti = {}
        if uk.get("aktif"):
            for k, v in (uk.get("nama") or {}).items():
                teks = str(v).strip()
                if teks:
                    try:
                        ganti[int(k)] = teks
                    except (TypeError, ValueError):
                        pass
        nama_kelas = [ganti.get(i, self.names.get(i, str(i)))
                      for i in range(self.n_kelas)] if self.names else []
        yaml = ["train: ../train/images", "val: ../valid/images",
                "test: ../test/images", "",
                f"nc: {len(nama_kelas)}",
                "names: [" + ", ".join(f"'{n}'" for n in nama_kelas) + "]", ""]
        (self.dirv / "data.yaml").write_text("\n".join(yaml), encoding="utf-8")

        jumlah = {}
        objek = 0
        per_kelas: dict[str, int] = defaultdict(int)
        negatif = 0
        for s in SPLIT:
            d = self.dirv / s / "labels"
            berkas = sorted(d.glob("*.txt")) if d.exists() else []
            jumlah[s] = len(berkas)
            for p in berkas:
                lab = olah.baca_label(p)
                if not lab:
                    negatif += 1
                objek += len(lab)
                for c, _ in lab:
                    per_kelas[nama_kelas[c] if c < len(nama_kelas) else str(c)] += 1

        (self.dirv / "MANIFES.json").write_text(json.dumps({
            "versi": self.nomor, "resep": self.resep, "berkas": self.manifes,
        }, ensure_ascii=False), encoding="utf-8")

        ringkas = {"n": sum(jumlah.values()), "jumlah": jumlah, "objek": objek,
                   "kelas": len(per_kelas), "per_kelas": dict(per_kelas),
                   "negatif": negatif, "byte": ukuran_versi(self.ds, self.nomor),
                   "detik": round(time.time() - self._t0, 1)}
        # `ringkas` memuat kunci "n", yang bentrok dengan parameter maju();
        # dikirim sebagai satu bundel supaya penambahan medan baru di ringkasan
        # tidak bisa lagi menabrak tanda tangan pelaporan kemajuan.
        self.maju("tutup", 1, 1, ringkas=ringkas)
        return ringkas

    # ------------------------------------------------------------- jalankan
    def jalankan(self, catatan: str = "") -> dict:
        buang_hasil(self.ds, self.nomor)
        self.dirv.mkdir(parents=True, exist_ok=True)
        try:
            self.fase_pra()
            self.fase_aug()
            self.fase_crop()
            self.fase_skala()
            self.fase_kelas()
            self.fase_negatif()
            return self.tutup(catatan)
        except Dibatalkan:
            buang_hasil(self.ds, self.nomor)
            catat_maju(self.kunci, batal=True, fase_nama="Dibatalkan")
            raise


# ================================================================= perkiraan
def perkirakan(items: list[dict], names: dict, resep: dict, peta: dict) -> dict:
    """
    Perkirakan jumlah gambar, objek, dan ukuran SEBELUM apa pun ditulis.

    Dihitung dengan rumus yang sama dengan fasenya, bukan faktor tetap hasil
    kalibrasi satu dataset — v14 memakai konstanta yang diturunkan dari dataset
    sirsak 8.889 gambar (f:2049) dan angkanya menyesatkan di dataset lain.

    Tetap sebuah PERKIRAAN: berapa augmentasi yang ditolak penjaga keterbacaan
    tidak bisa diketahui tanpa menjalankannya.
    """
    pra = (resep.get("pra") or {})
    fase = (resep.get("fase") or {})
    volume = (resep.get("volume") or {})
    buang_kosong = (pra.get("buang_kosong") or {}).get("aktif", False)

    n_split = {"train": 0, "valid": 0, "test": 0}
    train_obj = train_neg = neg_semua = 0
    per_kelas: dict[str, int] = defaultdict(int)
    for it in items:
        punya = bool(it.get("shapes"))
        if buang_kosong and not punya:
            continue
        s = peta.get(it["img"].name) or "train"
        n_split[s] = n_split.get(s, 0) + 1
        if not punya:
            neg_semua += 1
        if s == "train":
            if punya:
                train_obj += 1
            else:
                train_neg += 1
        for sh in it.get("shapes") or []:
            if sh.get("label") is not None:
                per_kelas[str(sh["label"])] += 1

    n = sum(n_split.values())
    tambah = 0
    if volume.get("per_gambar", 1) and (resep.get("aug") is not False):
        tambah += (train_obj + train_neg) * int(volume.get("per_gambar", 1))
    if (fase.get("crop_zoom") or {}).get("aktif", True):
        tambah += int(train_obj * float((fase.get("crop_zoom") or {}).get("porsi", 0.20)))
    if (fase.get("balans_skala") or {}).get("aktif", True):
        p = fase.get("balans_skala") or {}
        tambah += train_obj * (int(p.get("sapu_kecil", 2)) + int(p.get("sapu_besar", 1)))
    if (fase.get("balans_kelas") or {}).get("aktif", True) and len(per_kelas) > 1:
        nilai = sorted(per_kelas.values())
        sasaran = int(np.percentile(nilai, 75))
        # Dikalikan lipatan fase sebelumnya: kekurangan dihitung dari keadaan
        # SESUDAH fase-fase itu, bukan dari gambar asli.
        lipat = (n + tambah) / max(n, 1)
        tambah += int(sum(max(sasaran - v, 0) for v in per_kelas.values()) * lipat)
    if (fase.get("porsi_negatif") or {}).get("aktif", True) and train_neg:
        porsi0 = train_neg / max(train_obj + train_neg, 1)
        pos_akhir = (train_obj / max(train_obj + train_neg, 1)) * (n + tambah)
        tambah += max(0, int(porsi0 * pos_akhir / max(1 - porsi0, 1e-9)) - train_neg)

    total = n + tambah
    sisi = int(((pra.get("resize") or {}).get("lebar")) or olah.TARGET_SIZE)
    # ~70 KB untuk JPEG 640x640 mutu 92; diskalakan kuadratik terhadap sisi.
    byte = int(total * 70_000 * (sisi / 640.0) ** 2)
    return {"n_sumber": n, "n": total, "tambahan": tambah,
            "jumlah": n_split, "byte": byte,
            "objek_sumber": sum(per_kelas.values()),
            "kelas": len(per_kelas),
            # Nama DAN indeksnya. Popup Modify Classes memakai indeks (itu yang
            # dibaca _pra_ubah_kelas) tetapi harus menampilkan nama, dan tanpa
            # keduanya di satu tempat ia hanya bisa menampilkan angka telanjang.
            "daftar_kelas": [{"i": i, "nama": n, "objek": per_kelas.get(n, 0)}
                             for n, i in sorted(export.peta_kelas(items, names).items(),
                                                key=lambda kv: kv[1])],
            # Dua angka, karena keduanya menjawab pertanyaan berbeda: yang
            # pertama "berapa sampel negatif yang kupunya", yang kedua "berapa
            # yang dipakai fase pemulihan porsi" (fase itu hanya menyentuh
            # train). Satu angka untuk keduanya menyesatkan di kedua arah.
            "negatif_sumber": neg_semua,
            "negatif_train": train_neg,
            "detik": round(total * 0.045, 1)}


# ============================================ membaca kembali hasil versi
def ada_hasil(ds: Path, nomor: int) -> bool:
    return (dir_versi(ds, nomor) / "data.yaml").exists()


def items_hasil(ds: Path, nomor: int):
    """
    Baca berkas hasil sebuah versi jadi bentuk `item` yang dikenal export.py.

    Dengan ini kelima format ekspor — YOLO, YOLO-seg, COCO, VOC, CreateML —
    bekerja pada hasil versi TANPA satu baris pun kode ekspor berubah, dan
    seluruh uji ekspor yang sudah ada tetap menjaganya.

    Mengembalikan (items, names, peta_split).
    """
    d = dir_versi(ds, nomor)
    names: dict[int, str] = {}
    yml = d / "data.yaml"
    if yml.exists():
        teks = yml.read_text(encoding="utf-8")
        for baris in teks.splitlines():
            if baris.startswith("names:"):
                isi = baris.split(":", 1)[1].strip().strip("[]")
                for i, n in enumerate(x.strip().strip("'\"") for x in isi.split(",")):
                    if n:
                        names[i] = n
    items, peta = [], {}
    for split in SPLIT:
        gdir = d / split / "images"
        if not gdir.is_dir():
            continue
        for ip in sorted(gdir.glob("*.jpg")):
            lp = d / split / "labels" / f"{ip.stem}.txt"
            dim = cv2.imread(str(ip), cv2.IMREAD_REDUCED_COLOR_8)
            if dim is None:
                continue
            H, W = dim.shape[0] * 8, dim.shape[1] * 8
            bentuk = []
            for c, poli in olah.baca_label(lp):
                pts = np.array([[poli[i] * W, poli[i + 1] * H]
                                for i in range(0, len(poli), 2)], np.float32)
                bentuk.append({"label": names.get(c, str(c)), "type": "polygon",
                               "pts": pts, "pts_asli": pts.tolist()})
            items.append({"img": ip, "shapes": bentuk, "W": W, "H": H,
                          "ann": lp, "issues": [], "split": split})
            peta[ip.name] = split
    return items, names, peta


def isi_versi(ds: Path, nomor: int, contoh: int = 12) -> dict:
    """
    Rincian satu versi untuk panel detail: jumlah per split, per kelas, dan
    beberapa nama berkas contoh untuk deretan thumbnail.

    Dibaca dari MANIFES.json kalau ada — jauh lebih murah daripada memindai
    puluhan ribu berkas hanya untuk menghitung. Kalau manifesnya hilang,
    jatuh ke memindai, karena kartu yang tidak bisa menyebutkan isinya lebih
    buruk daripada kartu yang lambat sekali.
    """
    d = dir_versi(ds, nomor)
    if not (d / "data.yaml").exists():
        return {}
    jumlah = {}
    per_kelas: dict[str, int] = defaultdict(int)
    negatif = 0
    nama_kelas: list[str] = []
    yml = d / "data.yaml"
    for baris in yml.read_text(encoding="utf-8").splitlines():
        if baris.startswith("names:"):
            isi = baris.split(":", 1)[1].strip().strip("[]")
            nama_kelas = [x.strip().strip("'\"") for x in isi.split(",") if x.strip()]
    for s in SPLIT:
        ldir = d / s / "labels"
        berkas = sorted(ldir.glob("*.txt")) if ldir.is_dir() else []
        jumlah[s] = len(berkas)
        for p in berkas:
            lab = olah.baca_label(p)
            if not lab:
                negatif += 1
            for c, _ in lab:
                per_kelas[nama_kelas[c] if c < len(nama_kelas) else str(c)] += 1
    # Contoh diambil merata dari train, bukan 12 pertama: dua belas berkas
    # pertama menurut abjad hampir selalu berasal dari satu gambar sumber yang
    # sama, dan deretannya jadi dua belas salinan yang mirip.
    gdir = d / "train" / "images"
    semua = sorted(p.name for p in gdir.glob("*.jpg")) if gdir.is_dir() else []
    langkah = max(1, len(semua) // max(contoh, 1))
    return {"jumlah": jumlah, "per_kelas": dict(per_kelas), "negatif": negatif,
            "objek": sum(per_kelas.values()), "byte": ukuran_versi(ds, nomor),
            "contoh": [f"train/images/{n}" for n in semua[::langkah][:contoh]]}


def berkas_versi(ds: Path, nomor: int, nama: str) -> Path | None:
    """
    Selesaikan `nama` di dalam folder versi, atau None kalau ia menunjuk keluar.

    Nama datang dari URL, jadi ia tidak boleh dipercaya. Yang menjaga di sini
    bukan penyaringan karakter melainkan resolve() lalu memeriksa hasilnya
    benar-benar berada di dalam folder versinya — satu-satunya cara yang tidak
    bisa diakali oleh `..%2f`, tautan simbolik, atau path absolut.
    """
    akar = dir_versi(ds, nomor).resolve()
    try:
        p = (akar / nama).resolve()
        p.relative_to(akar)
    except (ValueError, OSError):
        return None
    return p if p.is_file() else None
