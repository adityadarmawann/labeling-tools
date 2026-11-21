"""
Auto-label KOLABORATIF (ensemble): jalankan beberapa sumber (model terlatih
projek, model .pt diunggah, zero-shot SAM3/YOLO-World, nanti Claude) lalu
GABUNGKAN hasilnya supaya di mana satu sumber lemah yang lain menutup.

Terbukti dari uji nyata: pada projek foto produk brand-spesifik, zero-shot dan
model terlatih sama-sama presisi tinggi tapi SALING MELENGKAPI (satu jago
karton, satu jago kaleng); gabungannya menutupi jauh lebih banyak.

File ini berisi LANGKAH 1: engine merge murni (tanpa model) -- bentuk data satu
deteksi, IoU, penggabungan/dedup, dan koersi ke jenis anotasi projek. Orkestrator
job + adapter sumber ada di langkah berikutnya.
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..log import catat
from . import ai_antre, autolabel, gpu_sinyal, prelabel, scanner, tugas
from .autolabel_zero import (Dibatalkan, TrainingSedangJalan, bersihkan_maju,
                             catat_maju, kemajuan)

log = catat("labelapp.autolabel")

# Urutan prioritas sumber saat dua sumber menemukan objek yang SAMA: label
# diambil dari sumber paling tepercaya untuk KELAS. Model terlatih projek tahu
# taksonomi projek, jadi labelnya menang atas zero-shot (yang cuma kata prompt).
PRIORITAS_BAWAAN = ("terlatih", "eksternal", "claude", "zeroshot")

# Ambang IoU: dua deteksi dengan tumpang tindih di atas ini dianggap objek sama.
IOU_BAWAAN = 0.6


@dataclass
class Deteksi:
    """Satu objek yang ditemukan sebuah sumber, dinormalkan lintas sumber."""
    bbox: tuple[float, float, float, float]      # (x1, y1, x2, y2) piksel
    label: str
    skor: float = 0.0
    sumber: str = ""
    pts: list | None = None                      # poligon [[x,y],...] atau None
    extra: dict = field(default_factory=dict)    # mis. shape pose mentah


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def gabung(deteksi: list[Deteksi], iou_ambang: float = IOU_BAWAAN,
           prioritas: tuple = PRIORITAS_BAWAAN) -> list[Deteksi]:
    """Gabungkan deteksi dari banyak sumber:

    - Deteksi yang tumpang tindih (IoU > ambang) dianggap OBJEK SAMA -> satu.
    - Label kelompok = dari sumber prioritas tertinggi (model terlatih menang).
    - Geometri = anggota poligon prioritas tertinggi (mask SAM presisi) kalau
      ada, selain itu kotak prioritas tertinggi. Jadi kelas dari satu sumber +
      bentuk dari sumber lain BISA berpadu -- itu justru intinya.
    - Deteksi yang tak berpasangan tetap disertakan (union) -> recall naik.
    """
    pidx = {s: i for i, s in enumerate(prioritas)}
    pr = lambda d: pidx.get(d.sumber, len(prioritas))

    # Seed kelompok dari skor tertinggi supaya deteksi terbaik jadi inti.
    kelompok: list[list[Deteksi]] = []
    for d in sorted(deteksi, key=lambda x: -x.skor):
        taruh = False
        for g in kelompok:
            if any(_iou(d.bbox, m.bbox) > iou_ambang for m in g):
                g.append(d)
                taruh = True
                break
        if not taruh:
            kelompok.append([d])

    out: list[Deteksi] = []
    for g in kelompok:
        oleh_pr = sorted(g, key=pr)
        label = oleh_pr[0].label
        geom = next((m for m in oleh_pr if m.pts), oleh_pr[0])
        out.append(Deteksi(
            bbox=geom.bbox, pts=geom.pts, label=label,
            skor=max(m.skor for m in g),
            sumber="+".join(sorted({m.sumber for m in g}))))
    return out


def _kurung(x, batas):
    return min(max(float(x), 0.0), float(batas))


def ke_bentuk(deteksi: list[Deteksi], jenis: str, W: int, H: int) -> list[dict]:
    """Deteksi gabungan -> bentuk labelme sesuai JENIS projek:

    - 'poligon' -> polygon (pakai pts kalau ada; kalau cuma kotak, kotak diubah
      jadi poligon 4 titik sebagai perkiraan kasar untuk disunting manusia).
    - selain itu (kotak/kosong) -> rectangle dua titik.
    Pose ('kerangka') ditangani di jalur terpisah (bukan merge lintas sumber).
    """
    out: list[dict] = []
    poligon = str(jenis or "").strip().lower() == "poligon"
    for d in deteksi:
        x1, y1, x2, y2 = d.bbox
        if poligon:
            if d.pts and len(d.pts) >= 3:
                pts = [[_kurung(x, W), _kurung(y, H)] for x, y in d.pts]
                pts = scanner.tutup_cincin(pts)
            else:
                pts = [[_kurung(x1, W), _kurung(y1, H)],
                       [_kurung(x2, W), _kurung(y1, H)],
                       [_kurung(x2, W), _kurung(y2, H)],
                       [_kurung(x1, W), _kurung(y2, H)]]
            out.append({"label": str(d.label), "text": "", "points": pts,
                        "group_id": None, "shape_type": "polygon", "flags": {}})
        else:
            out.append({"label": str(d.label), "text": "",
                        "points": [[_kurung(x1, W), _kurung(y1, H)],
                                   [_kurung(x2, W), _kurung(y2, H)]],
                        "group_id": None, "shape_type": "rectangle", "flags": {}})
    return out


# ============================== LANGKAH 2 ==================================
# Adapter sumber (gambar -> list[Deteksi]) + orkestrator job.

# Antrean yang SAMA dengan pre-label dan zero-shot (ai_antre). Jalur ini
# justru yang paling berbahaya kalau berdiri sendiri: satu job ensemble
# bisa memuat model terlatih + .pt eksternal + SAM/YOLO-World +
# GroundingDINO sekaligus, jadi membiarkannya berjalan berbarengan dengan
# jalur lain melipatgandakan pemakaian VRAM tanpa ada yang membatasi.
SERENTAK = ai_antre.SERENTAK
_giliran = ai_antre.giliran

# Model .pt yang diunggah dari luar disimpan di .latih/_eksternal/ (awalan "_"
# menjaganya di luar glob L* run terlatih). Lister + endpoint unggah menyusul.
DIR_EKSTERNAL = "_eksternal"


def _slug_pt(nama: str) -> str:
    s = str(nama or "").strip()
    if s.lower().endswith(".pt"):
        s = s[:-3]
    s = re.sub(r"[^A-Za-z0-9_.-]+", "-", s).strip("-.")
    return s or "model"


def pt_eksternal(ds: Path, nama: str) -> Path:
    """Path .pt eksternal milik satu projek dari namanya (slug ketat)."""
    return Path(ds) / prelabel.latih.FOLDER / DIR_EKSTERNAL / (_slug_pt(nama) + ".pt")


def _shape_ke_deteksi(s: dict, sumber: str, skor: float) -> Deteksi | None:
    pts = s.get("points") or []
    st = s.get("shape_type")
    if st == "rectangle" and len(pts) >= 2:
        (x1, y1), (x2, y2) = pts[0], pts[1]
        x1, x2 = sorted((float(x1), float(x2)))
        y1, y2 = sorted((float(y1), float(y2)))
        return Deteksi(bbox=(x1, y1, x2, y2), label=str(s.get("label")),
                       skor=skor, sumber=sumber)
    if st == "polygon" and len(pts) >= 3:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return Deteksi(bbox=(min(xs), min(ys), max(xs), max(ys)),
                       pts=[[float(x), float(y)] for x, y in pts],
                       label=str(s.get("label")), skor=skor, sumber=sumber)
    return None


def _catat_sumber_gagal(nama: str, it: dict) -> None:
    log.warning("auto-label: sumber %s gagal pada %s", nama,
                it["img"].name, exc_info=True)


def sumber_model(model, it: dict, conf: float, kelas: list, sumber: str) -> list[Deteksi]:
    """Model ultralytics (terlatih projek / .pt eksternal) -> Deteksi. Reuse
    jalur prediksi prelabel supaya kotak/mask ditangani sama."""
    out: list[Deteksi] = []
    # Keyakinan model IKUT, tidak lagi dipaku 0.0. gabung() menyemai kelompok
    # dari skor tertinggi, jadi dengan 0.0 deteksi model terlatih selalu
    # terurut paling akhir dan zero-shot yang selalu jadi inti kelompok --
    # kebalikan dari maksudnya.
    skor: list[float] = []
    bentuk = prelabel._prediksi_satu(model, it, conf, kelas, skor)
    for i, sh in enumerate(bentuk):
        d = _shape_ke_deteksi(sh, sumber, skor[i] if i < len(skor) else 0.0)
        if d is not None:
            out.append(d)
    return out


def sumber_zeroshot(img: Path, model: str, prompt: list, conf: float) -> list[Deteksi]:
    """Zero-shot teks (SAM3 -> poligon, YOLO-World -> kotak) -> Deteksi."""
    try:
        temuan = autolabel.dari_teks(img, prompt, model=model, ambang=float(conf))
    except autolabel.TidakAdaObjek:
        return []
    out: list[Deteksi] = []
    for t in temuan:
        pts = t.points if t.shape_type == "polygon" else None
        out.append(Deteksi(bbox=tuple(float(v) for v in t.bbox), pts=pts,
                           label=str(t.label), skor=float(t.skor), sumber="zeroshot"))
    return out


def sumber_groundingdino(img: Path, prompt: list, conf: float,
                         refine_sam: bool, model_sam: str = "sam2:small") -> list[Deteksi]:
    """GroundingDINO (open-vocab, recall tinggi) -> KOTAK; untuk projek segmentasi
    tiap kotak dirapikan jadi mask lewat SAM (dari_kotak) = "Grounded-SAM"."""
    try:
        temuan = autolabel.dari_groundingdino(img, prompt, ambang=float(conf))
    except autolabel.TidakAdaObjek:
        return []
    out: list[Deteksi] = []
    for t in temuan:
        x1, y1, x2, y2 = t.bbox
        pts = None
        if refine_sam:
            try:
                pts = autolabel.dari_kotak(img, x1, y1, x2, y2, model=model_sam).points
            except autolabel.TidakAdaObjek:
                pts = None
        out.append(Deteksi(bbox=(float(x1), float(y1), float(x2), float(y2)),
                           pts=pts, label=str(t.label), skor=float(t.skor),
                           sumber="groundingdino"))
    return out


def namai(dets: list, nama: str) -> list:
    """Paksa SEMUA deteksi satu sumber memakai satu nama kelas.

    Satu batch = satu produk yang sudah diketahui, jadi taksonomi model
    ("botol") sering bukan nama yang dipakai dataset ("botol-pet-ades"), dan
    prompt yang bagus untuk deteksi ("ADES logo on the bottle") hampir selalu
    buruk sebagai nama kelas. Tanpa penamaan di sini, satu-satunya jalan
    adalah mengganti nama ratusan objek satu per satu sesudah auto-label.

    Kosong = biarkan label asli sumbernya.
    """
    nama = str(nama or "").strip()
    if not nama:
        return dets
    return [replace(d, label=nama) for d in dets]


def _siapkan_model_sumber(ds: Path, sumber: list) -> list[tuple]:
    """Muat model untuk sumber terlatih/eksternal SEKALI (sebelum loop gambar).
    Kembalikan list (nama_sumber, model, kelas, conf, kelas_nama). Sumber yang
    bobotnya tak ada dilewati diam-diam (tetap jalan dengan sumber lain)."""
    out = []
    for sc in sumber:
        j = sc.get("jenis")
        if j == "terlatih":
            pt = prelabel.latih.bobot_training(ds, int(sc["nomor"]), "best")
            if not pt or not Path(pt).exists():
                continue
            st = prelabel.latih.status(ds, int(sc["nomor"])) or {}
            out.append(("terlatih", prelabel._muat_model(pt),
                        list(st.get("kelas") or []), float(sc.get("conf", 0.25)),
                        str(sc.get("kelas_nama") or "")))
        elif j == "eksternal":
            pt = pt_eksternal(ds, sc.get("nama", ""))
            if not pt.exists():
                continue
            out.append(("eksternal", prelabel._muat_model(pt), [],
                        float(sc.get("conf", 0.25)),
                        str(sc.get("kelas_nama") or "")))
    return out


def jalankan(ds: Path, uploads_root: Path, akun: str, batas: int, sumber: list,
             *, tid: str | None = None, batch: str | None = None,
             iou_ambang: float = IOU_BAWAAN, batal=None,
             sasaran: str = "belum", bentuk_paksa: str = "") -> dict:
    """Auto-label GABUNGAN: jalankan semua sumber terpilih per gambar BELUM
    teranotasi (lingkup job `tid` atau batch `batch`), gabung hasilnya, tulis
    (ditandai perlu_dicek). Melempar TrainingSedangJalan saat training aktif,
    Dibatalkan saat diminta, ValueError kalau tak ada sumber / projek pose.

    `sasaran` menentukan gambar mana yang boleh disentuh: "belum" (hanya yang
    kosong, perilaku lama), "dicek" (itu ditambah hasil AI yang belum
    diverifikasi), atau "semua". Selain "belum", bentuk baru DITAMBAHKAN ke
    yang sudah ada, bukan menggantinya -- itu yang membuat jalan kedua
    mungkin: mask produk dulu, kotak logo menyusul.
    """
    kunci = akun
    sumber = list(sumber or [])
    if not sumber:
        raise ValueError("belum ada sumber auto-label dipilih")

    # Saluran kemajuan DIKOSONGKAN lebih dulu, bukan sekadar ditimpa nol.
    #
    # catat_maju menggabung (d.update), jadi bendera akhir jalan SEBELUMNYA
    # (selesai, galat, dibatalkan, training) tetap tinggal walau pencacahnya
    # sudah disetel nol di sini. Antarmuka memeriksa `selesai` lebih dulu,
    # sehingga jalan kedua dan seterusnya di proses server yang sama LANGSUNG
    # memperlihatkan "Selesai. 0 gambar diisi, 0 objek" lalu berhenti memantau,
    # padahal pekerjaannya baru saja mulai dan terus menulis di latar.
    # Dilaporkan dari prod: 580 berkas ditulis sementara layar berkata nol.
    bersihkan_maju(kunci)
    catat_maju(kunci, jalan=True, persen=0.0, fase_nama="Menunggu giliran",
               diproses=0, objek=0, dilewati=0, total=0)
    if gpu_sinyal.latih_aktif():
        raise TrainingSedangJalan(
            "training sedang berjalan di server ini; auto-label dinonaktifkan "
            "sampai selesai. Anotasi manual tetap bisa.")

    with _giliran:
        if gpu_sinyal.latih_aktif():
            raise TrainingSedangJalan("training keburu mulai; auto-label dibatalkan.")
        catat_maju(kunci, fase_nama="Menyiapkan model")
        items, names = scanner.scan(ds)
        data = tugas.baca_projek(ds, uploads_root)
        jenis = tugas.jenis_berlaku(data, items)
        if jenis == "kerangka":
            raise ValueError("projek kerangka (pose) belum didukung auto-label "
                             "gabungan; pakai 'Pre-label dengan AI'.")

        sasaran = sasaran if sasaran in tugas.SASARAN else "belum"
        tambah = sasaran != "belum"
        # Pemilihnya milik tugas.py, bukan disalin di sini: angka yang
        # dijanjikan dialog dihitung dengan fungsi yang sama persis, jadi ia
        # tak bisa menjanjikan gambar yang tak akan dikerjakan.
        target = tugas.target_autolabel(items, data, ds, tid=tid, batch=batch,
                                        sasaran=sasaran)
        if target is None:
            raise FileNotFoundError("tugas tak dikenal")
        # Nol sasaran dilaporkan sebagai SEBAB, bukan dijalankan sampai selesai
        # dengan "0 dari 0". Bilah kemajuan yang berhenti di nol tanpa sepatah
        # kata membuat orang mengira fiturnya rusak, lalu mencoba lagi dan lagi;
        # terjadi di prod. Lingkup yang kosong hampir selalu berarti pilihan
        # "Kerjakan" tak cocok dengan keadaan gambarnya.
        if not target:
            raise ValueError(
                "tidak ada gambar yang cocok di lingkup ini. Kalau semuanya "
                "sudah dianotasi, ganti pilihan \"Kerjakan\" jadi "
                "\"+ hasil AI yang belum diverifikasi\" atau \"semua\".")
        if batas and batas > 0:
            target = target[:int(batas)]

        model_src = _siapkan_model_sumber(ds, sumber)
        zs = [sc for sc in sumber if sc.get("jenis") == "zeroshot"]
        gd = [sc for sc in sumber if sc.get("jenis") == "groundingdino"]
        if not model_src and not zs and not gd:
            raise ValueError("tak ada sumber yang siap (model/bobot tak ditemukan)")
        # Bentuk keluaran boleh DIPAKSA per jalan, tidak selalu ikut format
        # projek. Itu separuh lagi dari kebutuhan "dua jalan": tanpa ini,
        # jalan kedua di projek segmentasi tetap menghasilkan poligon, jadi
        # mask produk + kotak logo mustahil betapapun seringnya dijalankan.
        if bentuk_paksa in ("poligon", "kotak"):
            jenis = bentuk_paksa
        refine = jenis == "poligon"      # projek seg: kotak GDINO -> mask via SAM

        total = len(target)
        catat_maju(kunci, total=total, fase_nama="Memprediksi")
        diproses = objek = ditolak = kosong = 0
        for i, it in enumerate(target):
            if batal and batal():
                raise Dibatalkan()
            if gpu_sinyal.latih_aktif():
                raise TrainingSedangJalan(
                    "training mulai di tengah proses; auto-label dihentikan. "
                    "Gambar yang sudah terisi tetap ada.")
            if tugas.tolak_tulis(ds, akun, it["img"]):
                ditolak += 1
            else:
                dets: list[Deteksi] = []
                # Satu sumber gagal pada satu gambar tidak boleh menjatuhkan
                # seluruh job, TAPI juga tidak boleh senyap: ditelan diam-diam,
                # galat pemrograman (mis. tanda tangan adapter berubah) menyamar
                # jadi "tak ada deteksi" dan job tetap melapor sukses dengan nol
                # objek. Dicatat sekali per (sumber, gambar).
                for nm, m, kelas, cf, knm in model_src:
                    try:
                        dets += namai(sumber_model(m, it, cf, kelas, nm), knm)
                    except Exception:            # noqa: BLE001
                        _catat_sumber_gagal(nm, it)
                for sc in zs:
                    try:
                        dets += namai(
                            sumber_zeroshot(it["img"], sc.get("model"),
                                            sc.get("prompt") or [],
                                            float(sc.get("conf", 0.1))),
                            sc.get("kelas_nama"))
                    except Exception:            # noqa: BLE001
                        _catat_sumber_gagal("zeroshot", it)
                for sc in gd:
                    try:
                        dets += namai(
                            sumber_groundingdino(it["img"], sc.get("prompt") or [],
                                                 float(sc.get("conf", 0.25)), refine),
                            sc.get("kelas_nama"))
                    except Exception:            # noqa: BLE001
                        _catat_sumber_gagal("groundingdino", it)
                bentuk = ke_bentuk(gabung(dets, iou_ambang), jenis, it["W"], it["H"])
                    # Sesi lain harus tahu berkas ini berubah. Tanpa ini,
                    # pelabel yang sedang bekerja di kanvas TIDAK AKAN PERNAH
                    # melihat hasil auto-label selama ia memakai panah kiri/
                    # kanan: segarkan() keluar lebih awal kalau pencacahnya tak
                    # naik. Ia lalu melabeli ulang dari nol lalu menyimpan, dan
                    # simpanannya menimpa hasil AI.
                if bentuk:
                    prelabel._beritahu(ds, it["img"])
                    masuk: list[int] = []
                    names = prelabel.tulis_prediksi(it, ds, names, bentuk,
                                                    tambah=tambah,
                                                    ditambah=masuk)
                    n = masuk[0] if masuk else len(bentuk)
                    objek += n
                    # Nol bentuk BARU berarti seluruhnya menduplikasi yang
                    # sudah ada. Menghitungnya "diproses" membuat laporan
                    # mengaku menambah objek yang tak pernah ditulis.
                    if n:
                        diproses += 1
                    else:
                        kosong += 1
                else:
                    kosong += 1
            catat_maju(kunci,
                       persen=round((i + 1) / max(total, 1) * 100, 1),
                       diproses=diproses, objek=objek,
                       dilewati=ditolak + kosong, kosong=kosong,
                       ditolak=ditolak)

    # Dua sebab yang SANGAT berbeda, dulu dihitung di ember yang
    # sama: "dilewati 8" tak memberi petunjuk apakah modelnya memang
    # tak menemukan apa-apa atau orangnya tidak berhak menulis ke
    # gambar-gambar itu. Yang pertama berarti ganti prompt/model, yang
    # kedua berarti minta akses. `dilewati` dipertahankan sebagai
    # jumlah keduanya supaya pembaca lama tetap benar.
    return {"total": total, "diproses": diproses, "objek": objek,
            "dilewati": ditolak + kosong, "kosong": kosong, "ditolak": ditolak}
