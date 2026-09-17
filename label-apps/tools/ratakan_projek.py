#!/usr/bin/env python3
"""
Ratakan SATU projek yang terlanjur bersplit (train/valid/test) jadi kolam datar.

Perkakas pemeliharaan sekali pakai untuk projek yang DIIMPOR SEBELUM kode
"ratakan saat impor" ada (lihat services/tambah.ratakan_split). HIGOLAB membelah
datasetnya sendiri per versi (anti-bocor); persebaran train/valid/test bawaan
Roboflow harus dicopot supaya tidak dipertahankan dan membatalkan pembelahan itu.

Memindahkan berkas saja TIDAK cukup: .tugas.json (dataset + tugas[*].gambar) dan
.tag.json (gambar{}) menyimpan path seperti 'test/images/x.jpg'. Tanpa ditulis
ulang, belasan ribu entri jadi yatim — gambar tampak hilang dari dataset dan
tugasnya. Perkakas ini memindah berkas DAN menulis ulang ketiganya sekaligus.

Sifatnya:
  - Uji-kering secara bawaan; menulis hanya dengan --apply.
  - Aman-ulang: projek yang sudah datar tidak diapa-apakan.
  - Berhenti kalau ada nama berkas bentrok antar-split (perlu tangan manusia).
  - Mencadangkan .tugas.json + .tag.json + manifes split-asal ke .pra-ratakan/.
  - TIDAK menyentuh _sampah-gambar/, data.yaml, maupun README*.

PENTING: hentikan server dulu sebelum --apply. Memindahkan puluhan ribu berkas
sementara server menulis .tugas.json yang sama bisa merusak keduanya.

Pakai:
    python tools/ratakan_projek.py <path-projek>            # uji-kering
    python tools/ratakan_projek.py <path-projek> --apply     # betulan
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

SPLITS = ("train", "valid", "val", "test")
_RE = re.compile(r"^(?:train|valid|val|test)/(images|labels)/")


def ratakan_ref(p: str) -> str:
    """'test/images/x.jpg' -> 'images/x.jpg'; path lain dibiarkan apa adanya."""
    return _RE.sub(r"\1/", p)


def _yolo_disini(d: Path) -> bool:
    return (d / "images").is_dir() and (d / "labels").is_dir()


def migrasi(P: Path, apply: bool) -> dict:
    P = Path(P)
    if not (P / ".tugas.json").is_file():
        return {"status": "BUKAN-PROJEK",
                "pesan": f"{P} tidak punya .tugas.json — bukan projek HIGOLAB"}

    splits = [P / s for s in SPLITS if (P / s).is_dir() and _yolo_disini(P / s)]
    if not splits:
        return {"status": "sudah-datar", "berkas_dipindah": 0}

    # 0. Bentrok nama antar-split diperiksa lebih dulu: kalau ada, dua foto
    #    berbeda akan bertabrakan di images/ dan salah satu tertimpa. Berhenti.
    asal: dict[str, str] = {}
    bentrok: list[str] = []
    for s in splits:
        idir = s / "images"
        if idir.is_dir():
            for n in os.listdir(idir):
                if n in asal:
                    bentrok.append(n)
                asal[n] = s.name
    if bentrok:
        return {"status": "BENTROK", "n_bentrok": len(bentrok),
                "contoh": bentrok[:10],
                "pesan": "nama berkas sama muncul di lebih dari satu split; "
                         "selesaikan manual dulu"}

    if not apply:
        akan = sum(1 for s in splits for sub in ("images", "labels")
                   if (s / sub).is_dir()
                   for f in (s / sub).iterdir() if f.is_file())
        return {"status": "uji-kering", "splits": [s.name for s in splits],
                "berkas_akan_dipindah": akan,
                "pesan": "jalankan ulang dengan --apply untuk menerapkan"}

    # 1. Cadangkan sidecar + manifes split asal.
    cad = P / ".pra-ratakan"
    cad.mkdir(exist_ok=True)
    for nm in (".tugas.json", ".tag.json"):
        if (P / nm).is_file():
            shutil.copy2(P / nm, cad / nm)
    (cad / "split-asal.json").write_text(json.dumps(asal, ensure_ascii=False))

    # 2. Pindahkan berkas ke images/+labels/ di akar (bijektif, tanpa bentrok).
    (P / "images").mkdir(exist_ok=True)
    (P / "labels").mkdir(exist_ok=True)
    dipindah = 0
    for s in splits:
        for sub in ("images", "labels"):
            src = s / sub
            if not src.is_dir():
                continue
            for f in src.iterdir():
                if not f.is_file():
                    continue
                dst = P / sub / f.name
                if dst.exists():
                    return {"status": "BENTROK-RUNTIME", "berkas": f.name}
                f.replace(dst)
                dipindah += 1

    # 3. Tulis ulang .tugas.json (dataset + tugas[*].gambar).
    tj = P / ".tugas.json"
    ref_tugas = 0
    d = json.loads(tj.read_text())
    d["dataset"] = [ratakan_ref(x) for x in d.get("dataset", [])]
    ref_tugas += len(d.get("dataset", []))
    for t in d.get("tugas", {}).values():
        if "gambar" in t:
            t["gambar"] = [ratakan_ref(x) for x in t["gambar"]]
            ref_tugas += len(t["gambar"])
    _tulis_json(tj, d)

    # 4. Tulis ulang .tag.json (gambar{} keys).
    tag = P / ".tag.json"
    ref_tag = 0
    if tag.is_file():
        d = json.loads(tag.read_text())
        if isinstance(d.get("gambar"), dict):
            d["gambar"] = {ratakan_ref(k): v for k, v in d["gambar"].items()}
            ref_tag = len(d["gambar"])
        _tulis_json(tag, d)

    # 5. Buang folder split yang kini kosong.
    for s in splits:
        for sub in ("images", "labels"):
            f = s / sub
            if f.is_dir() and not any(f.iterdir()):
                f.rmdir()
        if s.is_dir() and not any(s.iterdir()):
            s.rmdir()

    return {"status": "ok", "splits": [s.name for s in splits],
            "berkas_dipindah": dipindah,
            "ref_tugas_ditulis_ulang": ref_tugas,
            "ref_tag_ditulis_ulang": ref_tag,
            "cadangan": str(cad)}


def _tulis_json(path: Path, data: dict) -> None:
    """Tulis ke .tmp lalu ganti nama: proses terputus tak meninggalkan JSON separuh."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(path)


def betulkan_latar(P: Path, apply: bool) -> dict:
    """Betulkan penanda latar gaya-labelme yang nyasar di dataset YOLO.

    Versi lama "Tandai latar" menulis .json KOSONG (shapes:[]) di sebelah gambar
    (images/), padahal pemindai YOLO membaca labels/*.txt — jadi gambar itu tak
    pernah terbaca sebagai latar. Di sini tiap images/<n>.json yang shapes-nya
    kosong diubah jadi labels/<n>.txt kosong (penanda latar yang benar), lalu
    .json usangnya dibuang. HANYA untuk projek YOLO (images/+labels/ di akar);
    .json yang berisi objek (cadangan labelme) TIDAK disentuh.
    """
    P = Path(P)
    idir, ldir = P / "images", P / "labels"
    if not (idir.is_dir() and ldir.is_dir()):
        return {"latar_status": "bukan-yolo", "latar_dibetulkan": 0}
    dibetulkan, cruft = 0, 0
    for jp in sorted(idir.glob("*.json")):
        try:
            d = json.loads(jp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not (isinstance(d, dict) and d.get("shapes") == []):
            continue                      # bukan penanda latar (ada objek) -> lewati
        lp = ldir / (jp.stem + ".txt")
        konversi = not lp.exists()
        if apply:
            if konversi:
                lp.write_text("")         # label .txt kosong = latar
            jp.unlink()                   # buang .json usang
        if konversi:
            dibetulkan += 1
        else:
            cruft += 1
    return {"latar_status": "ok" if apply else "uji-kering",
            "latar_dibetulkan": dibetulkan, "json_usang_dibuang": cruft}


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("-")]
    apply = "--apply" in argv
    if len(args) != 1 or "-h" in argv or "--help" in argv:
        print(__doc__)
        return 2
    P = Path(args[0]).expanduser()
    hasil = migrasi(P, apply)
    # Betulkan penanda latar SETELAH diratakan (butuh layout YOLO datar). Jalan
    # juga saat projek sudah datar, karena di situlah orphan latar botol berada.
    if hasil["status"] in ("ok", "uji-kering", "sudah-datar"):
        hasil.update(betulkan_latar(P, apply))
    print(json.dumps(hasil, ensure_ascii=False, indent=2))
    return 0 if hasil["status"] in ("ok", "uji-kering", "sudah-datar") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
