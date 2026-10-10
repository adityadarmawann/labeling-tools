"""Impor dataset klip aksi JADI menjadi VERSI siap-latih di projek video.

Sejajar dengan impor dataset gambar (Roboflow) di jalur gambar: kadang klip
sudah dipotong, dilabeli, di-split, dan di-augmentasi DI LUAR HIGOLAB (mis.
`dataset/01-action/.../train` + `val` folder-per-kelas). Alur normal
ingest -> label -> build versi akan MEMBANGUN ULANG (split + aug lagi) — salah
untuk data yang sudah jadi. Importer ini mendaftarkan dataset apa adanya sebagai
satu versi `.versi/vN/`, sehingga langsung muncul di pemilih versi UI dan bisa
dilatih ketiga backend (VideoMAE/SlowFast/PoseC3D) tanpa diproses ulang.

KONTRAK MASUKAN
  <train_dir>/<kelas>/*.mp4   (boleh mengandung _aug_/_bal_)
  <val_dir>/<kelas>/*.mp4     (WAJIB 100% klip asli — tanpa _aug_/_bal_)

Invarian valid-bersih sama dengan klip_olah: klip augmentasi di valid membuat
angka validasi menipu, jadi ditolak keras di sini — bukan dibuang diam-diam.

Tak ada impor berat (torch dll): importer hanya menyalin berkas + menulis
aksi.yaml/MANIFES + mendaftarkan versi, jadi aman diimpor di server CPU.
"""
from __future__ import annotations

import json
import shutil
import time
from collections import defaultdict
from pathlib import Path

from ..security import safe_slug
from . import projek, tugas, versi

SPLIT = ("train", "valid")
EKST = ".mp4"


class ImporTolak(Exception):
    """Masukan tak memenuhi kontrak (val kotor, kelas kosong, dll)."""


# ──────────────────────────────── pembantu ────────────────────────────────
def _klip(d: Path) -> list[Path]:
    return sorted(p for p in d.glob(f"*{EKST}") if p.is_file()) if d.is_dir() else []


def _kelas_folder(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir()
                  if p.is_dir() and _klip(p)) if d.is_dir() else []


def _ada_aug(klips: list[Path]) -> list[str]:
    return [p.name for p in klips if "_aug_" in p.stem or "_bal_" in p.stem]


def _asal(nama: str) -> str:
    """Id video sumber dari nama klip (token 11-karakter gaya YouTube), untuk
    kolom asal-usul MANIFES. 'impor' kalau tak terbaca."""
    stem = Path(nama).stem
    for s in ("_aug_", "_bal_"):
        if s in stem:
            stem = stem[:stem.index(s)]
    for bagian in stem.split("_"):
        if len(bagian) == 11:
            return bagian
    return "impor"


# ──────────────────────────────── rencana ─────────────────────────────────
def rencana(train_dir: Path, val_dir: Path, kelas: list[str] | None = None) -> dict:
    """Periksa masukan TANPA menulis. Kembalikan kelas terpilih, jumlah per
    split/kelas, dan daftar masalah (val kotor, kelas tanpa val, dll)."""
    train_dir, val_dir = Path(train_dir), Path(val_dir)
    masalah: list[str] = []
    if not train_dir.is_dir():
        masalah.append(f"folder train tak ada: {train_dir}")
    if not val_dir.is_dir():
        masalah.append(f"folder val tak ada: {val_dir}")

    k_train, k_val = _kelas_folder(train_dir), _kelas_folder(val_dir)
    if kelas:
        pilih = [k for k in kelas if k]
    else:
        # bawaan: kelas yang punya klip di KEDUA split (kelas train-saja seperti
        # 'stand' otomatis dilewati — tak ada val = tak bisa diukur).
        pilih = [k for k in k_train if k in set(k_val)]

    per_kelas: dict[str, dict[str, int]] = {}
    for k in pilih:
        tr, va = _klip(train_dir / k), _klip(val_dir / k)
        per_kelas[k] = {"train": len(tr), "valid": len(va)}
        if not tr:
            masalah.append(f"kelas '{k}': tak ada klip train")
        if not va:
            masalah.append(f"kelas '{k}': tak ada klip valid (tak bisa diukur)")
        kotor = _ada_aug(va)
        if kotor:
            masalah.append(f"kelas '{k}': {len(kotor)} klip augmentasi di valid "
                           f"(mis. {kotor[0]}) — valid harus 100% klip asli")
    if not pilih:
        masalah.append("tak ada kelas yang punya klip di train & val sekaligus")
    jumlah = {s: sum(per_kelas[k][s] for k in pilih) for s in SPLIT}
    return {"kelas": pilih, "per_kelas": per_kelas, "jumlah": jumlah,
            "train_saja": sorted(set(k_train) - set(k_val)),
            "val_saja": sorted(set(k_val) - set(k_train)), "masalah": masalah}


def temukan_dataset(root: Path) -> tuple[Path, Path] | None:
    """Di dalam folder hasil ekstrak .zip, temukan pasangan (train, val|valid)
    yang masing-masing berisi subfolder kelas ber-*.mp4.

    Zip sering membungkus satu folder atas (mis. 'v2.1-dan-spacejam-aug/train'),
    jadi dicari di root dan sampai kedalaman wajar (3 tingkat). Pasangan pertama
    yang sah dikembalikan."""
    root = Path(root)
    kandidat = [root]
    for p in sorted(root.rglob("*")):
        if p.is_dir() and len(p.relative_to(root).parts) <= 3:
            kandidat.append(p)
    for base in kandidat:
        tr = base / "train"
        va = next((base / n for n in ("valid", "val") if (base / n).is_dir()),
                  None)
        if (tr.is_dir() and va is not None
                and _kelas_folder(tr) and _kelas_folder(va)):
            return tr, va
    return None


# ──────────────────────────────── impor ───────────────────────────────────
def impor(ds: Path, train_dir: Path, val_dir: Path, *, oleh: str,
          kelas: list[str] | None = None, negatif: str = "",
          nomor: int | None = None, salin: bool = True,
          set_kelas: bool = True, isi_klip: bool = True) -> dict:
    """Bangun `.versi/vN/` dari dataset jadi + daftarkan lewat versi.buat.

    `salin=True` menyalin berkas (versi mandiri, tahan kalau sumber dihapus);
    `salin=False` memakai hardlink (0 ruang; sumber & versi berbagi inode).
    Kembalikan ringkasan (termasuk `nomor` versi).
    """
    ds, train_dir, val_dir = Path(ds), Path(train_dir), Path(val_dir)
    if projek.jenis_projek(ds) != "video":
        raise ImporTolak(f"projek {ds.name} bukan projek video")

    rin = rencana(train_dir, val_dir, kelas)
    fatal = [m for m in rin["masalah"] if "augmentasi di valid" in m
             or "folder" in m or "tak ada kelas" in m]
    if fatal:
        raise ImporTolak("; ".join(fatal))
    pilih = rin["kelas"]
    if negatif and negatif not in pilih:
        raise ImporTolak(f"kelas negatif '{negatif}' tak ada di daftar kelas")

    n = int(nomor) if nomor is not None else versi.nomor_berikut(ds)
    dirv = ds / ".versi" / f"v{n}"
    if dirv.exists():
        raise ImporTolak(f"versi v{n} sudah ada di {ds.name}")

    def taruh(src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if salin:
            shutil.copy2(src, dst)
        else:
            try:
                dst.hardlink_to(src)
            except OSError:
                shutil.copy2(src, dst)          # beda device -> salin

    manifes: list[dict] = []
    per_kelas: dict[str, dict[str, int]] = {k: {"train": 0, "valid": 0}
                                            for k in pilih}
    src_split = {"train": train_dir, "valid": val_dir}
    try:
        for s in SPLIT:
            for k in pilih:
                slug = safe_slug(k)
                for src in _klip(src_split[s] / k):
                    taruh(src, dirv / s / slug / src.name)
                    manifes.append({"berkas": f"{s}/{slug}/{src.name}",
                                    "split": s, "asal": _asal(src.name),
                                    "kelas": k})
                    per_kelas[k][s] += 1
    except Exception:                            # noqa: BLE001
        shutil.rmtree(dirv, ignore_errors=True)  # jangan tinggalkan versi separuh
        raise

    jumlah = {s: sum(per_kelas[k][s] for k in pilih) for s in SPLIT}

    # aksi.yaml (format identik klip_olah.Pekerjaan.tutup) + MANIFES.json
    yaml = ["# dataset klip aksi (HIGOLAB) — diimpor dari dataset jadi",
            "train: train", "val: valid", f"nc: {len(pilih)}",
            "names: [" + ", ".join(f"'{k}'" for k in pilih) + "]",
            f"negatif: {negatif or '~'}", ""]
    (dirv / "aksi.yaml").write_text("\n".join(yaml), encoding="utf-8")

    resep = {"impor": True, "sumber_train": str(train_dir),
             "sumber_val": str(val_dir)}
    (dirv / "MANIFES.json").write_text(json.dumps({
        "versi": n, "jenis": "aksi", "resep": resep,
        "aksi": {"kelas": pilih, "merge": {}, "negatif": negatif},
        "berkas": manifes,
    }, ensure_ascii=False), encoding="utf-8")

    asal_hitung: dict[str, int] = defaultdict(int)
    for m in manifes:
        asal_hitung[m["asal"]] += 1
    ringkas = {"n": sum(jumlah.values()), "jumlah": jumlah, "kelas": len(pilih),
               "per_kelas": per_kelas, "negatif": negatif,
               "asal": dict(asal_hitung), "jenis": "aksi", "impor": True,
               "detik": 0.0}

    # Set kelas aksi projek (biar halaman /aksi tahu kelasnya) + daftarkan versi.
    if set_kelas:
        tugas.set_aksi(ds, {"kelas": pilih, "merge": {}, "warna": [],
                            "negatif": negatif}, oleh)
    gambar = [Path(m["berkas"]).name for m in manifes]
    peta = {Path(m["berkas"]).name: m["split"] for m in manifes}
    rasio = (f"{round(jumlah['train'] / max(ringkas['n'], 1) * 100)}:"
             f"{round(jumlah['valid'] / max(ringkas['n'], 1) * 100)}")
    versi.buat(ds, oleh, rasio,
               gambar, peta,
               {"split": jumlah, "kelas": len(pilih), "objek": ringkas["n"],
                "beralas": False},
               f"impor dataset jadi ({len(pilih)} kelas)",
               resep=resep, nomor=n, hasil=ringkas)
    ringkas["nomor"] = n

    # Isi juga klip MENTAH yang bisa dibuka per-video di halaman Label Aksi.
    # Tanpa ini projek cuma punya versi beku tanpa satu pun klip yang bisa
    # ditelusuri (keluhan nyata: "raw-nya tak ada, tiap video tak bisa dibuka").
    if isi_klip:
        try:
            batch = safe_slug(Path(train_dir).parent.name) or "impor"
            km = isi_klip_mentah(ds, train_dir, val_dir, pilih, batch=batch,
                                 oleh=oleh)
            ringkas["klip_mentah"] = km["klip"]
            ringkas["batch"] = km["batch"]
        except Exception:                            # noqa: BLE001
            # Mengisi klip mentah adalah bonus telusur; kegagalannya tak boleh
            # membatalkan versi yang sudah terdaftar.
            ringkas["klip_mentah"] = 0
    return ringkas


def isi_klip_mentah(ds: Path, train_dir: Path, val_dir: Path, kelas: list[str],
                    *, batch: str = "impor", oleh: str = "") -> dict:
    """Salin klip ASLI (bukan _aug_/_bal_) dari train+val ke `klip/<batch>/`,
    beri label kelasnya, dan masukkan ke dataset — supaya tiap video bisa DIBUKA
    & ditelusuri di halaman Label Aksi, bukan cuma beku di dalam versi.

    Klip augmentasi/balance (_aug_/_bal_) DILEWATI: itu turunan sintetis yang
    lahir saat build versi, bukan rekaman mentah yang perlu ditelusuri. Label +
    keanggotaan dataset ditulis sekali-tulis (bukan per-klip) supaya ribuan klip
    tak berarti ribuan tulis-ulang .klip.json."""
    from . import klip_tag, tugas

    slug_b = safe_slug(batch) or "impor"
    bdir = Path(ds) / "klip" / slug_b
    bdir.mkdir(parents=True, exist_ok=True)
    data = klip_tag.baca(ds)
    data.setdefault("klip", {})
    rels: list[str] = []
    seen: set[str] = set()
    for split_dir in (Path(train_dir), Path(val_dir)):
        for k in kelas:
            kd = split_dir / k
            if not kd.is_dir():
                continue
            for src in sorted(kd.glob("*.mp4")):
                if "_aug_" in src.stem or "_bal_" in src.stem:
                    continue
                rel = f"klip/{slug_b}/{src.name}"
                if rel in seen:              # nama sama di train & val (jarang)
                    continue
                seen.add(rel)
                dst = bdir / src.name
                if not dst.exists():
                    shutil.copy2(src, dst)
                rec = {"label": k, "batch": slug_b}
                sid = _asal(src.name)
                if sid and sid != "impor":
                    rec["srcid"] = sid
                data["klip"][rel] = rec
                rels.append(rel)
    klip_tag.tulis(ds, data)
    if rels:
        tugas.masukkan(Path(ds), rels, oleh)
    return {"klip": len(rels), "batch": slug_b}


# ──────────────────────────────── CLI ─────────────────────────────────────
def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Impor dataset klip aksi jadi (train/val folder-per-kelas) "
                    "sebagai versi siap-latih di projek video HIGOLAB.")
    ap.add_argument("--root", required=True,
                    help="uploads root; projek dibuat di <root>/<oleh>/<projek> "
                         "(persis seperti app — kalau salah taruh app tak melihatnya)")
    ap.add_argument("--projek", required=True, help="nama projek video (dibuat bila belum ada)")
    ap.add_argument("--train", required=True, help="folder train (berisi subfolder per kelas)")
    ap.add_argument("--val", required=True, help="folder val/valid (klip asli, tanpa aug)")
    ap.add_argument("--kelas", default="", help="daftar kelas dipisah koma; kosong=auto (kelas di train & val)")
    ap.add_argument("--negatif", default="", help="nama kelas negatif (opsional)")
    ap.add_argument("--oleh", default="impor", help="pemilik/pelaku")
    ap.add_argument("--hardlink", action="store_true", help="hardlink alih-alih salin (0 ruang)")
    ap.add_argument("--dry-run", action="store_true", help="hanya tampilkan rencana, tak menulis")
    a = ap.parse_args()

    # Projek HIGOLAB tinggal di <uploads_root>/<pemilik>/<projek> (lihat
    # routers/projek.py), BUKAN langsung di root. Salah taruh = app tak pernah
    # melihatnya. Maka workspace-nya root/<oleh>.
    ws = Path(a.root) / a.oleh
    ds = projek._folder(ws, a.projek)
    kelas = [k.strip() for k in a.kelas.split(",") if k.strip()] or None

    if a.dry_run:
        print(json.dumps(rencana(Path(a.train), Path(a.val), kelas),
                         indent=1, ensure_ascii=False))
        return 0

    if not ds.exists():
        ws.mkdir(parents=True, exist_ok=True)
        info = projek.buat(ws, a.projek, "video")
        ds = Path(info["path"])
        print(f"projek video dibuat: {ds} (pemilik {a.oleh})")
    t0 = time.time()
    r = impor(ds, Path(a.train), Path(a.val), oleh=a.oleh, kelas=kelas,
              negatif=a.negatif, salin=not a.hardlink)
    r["detik"] = round(time.time() - t0, 1)
    print(json.dumps(r, indent=1, ensure_ascii=False))
    print(f"\nSELESAI: versi v{r['nomor']} di projek '{ds.name}' — "
          f"{r['n']} klip, {r['kelas']} kelas, {r['detik']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
