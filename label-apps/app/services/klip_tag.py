"""
Label per-klip untuk projek VIDEO sub-jenis aksi: berkas pendamping `.klip.json`.

Padanan tag.py (`.tag.json`) untuk klip, dan di sini karena alasan yang sama:
klip yang BELUM dilabeli tidak punya berkas apa pun di sebelahnya, padahal
justru klip itulah yang paling perlu dicatat (ia yang menunggu dilabeli).
Satu berkas di akar projek, bukan satu berkas per klip — melabeli ratusan klip
berarti satu tulis, bukan ratusan.

Kuncinya nama berkas klip RELATIF terhadap akar projek dengan garis miring maju
(mis. `klip/batch1/x.mp4`), persis kunci_gambar di tag.py — satu klip tak punya
dua nama di dua berkas pendamping, dan kunci itu langsung dipakai apa adanya
oleh RBAC (tugas.boleh_labeli) dan kurasi dataset (tugas.dataset[]), tanpa kode
RBAC baru.

BENTUK NILAI per klip:
    {label, batch, srcid?, mutu?}
  label  "" = BELUM dilabeli (bukan negatif — negatif adalah kelas eksplisit
         di aksi{}.negatif; lihat tugas._sah_aksi & MEMORY "sampel negatif").
  batch  nama batch ingest (= folder di bawah klip/), dipakai scope RBAC
         labeler spesifik persis seperti batch gambar di .tag.json.
  srcid  id video sumber klip ini — dipakai pembelahan anti-bocor per sumber
         (satu video sumber tak boleh tersebar ke train & val).
  mutu   (opsional) skor mutu klip 0..1, mis. fraksi frame yang ada pose-nya.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from ..log import catat

log = catat("labelapp.klip_tag")

BERKAS = ".klip.json"
VERSI = 1

MAKS_LABEL = 80

_kunci = threading.Lock()


def _p(ds: Path) -> Path:
    return Path(ds) / BERKAS


def _bersih(s: str) -> str:
    return " ".join(str(s or "").split())[:MAKS_LABEL].strip()


def baca(ds_dir: Path) -> dict:
    """Isi berkas pendamping, selalu berbentuk lengkap walau berkasnya rusak."""
    kosong = {"versi": VERSI, "klip": {}}
    p = _p(ds_dir)
    if not p.is_file():
        return kosong
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Berkas rusak tidak boleh menggagalkan pembukaan projek — labelnya
        # keterangan, kehilangannya lebih ringan daripada projek tak bisa dibuka.
        log.warning("berkas klip rusak, diabaikan: %s", p)
        return kosong
    if not isinstance(d, dict) or not isinstance(d.get("klip"), dict):
        return kosong
    return {"versi": d.get("versi", VERSI), "klip": d["klip"]}


def tulis(ds_dir: Path, data: dict) -> None:
    """Tulis atomik (.tmp lalu replace), sama caranya dengan tag.py."""
    p = _p(ds_dir)
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(p)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def kunci_klip(ds: Path, klip_path: Path) -> str:
    """Nama klip relatif terhadap akar projek, garis miring maju.

    Mencerminkan tag.kunci_gambar persis, termasuk jalur cepat relative_to
    sebelum resolve: pada projek dengan ribuan klip, satu syscall resolve per
    klip ikut di tiap muat papan.
    """
    g, d = Path(klip_path), Path(ds)
    try:
        return g.relative_to(d).as_posix()
    except ValueError:
        try:
            return g.resolve().relative_to(d.resolve()).as_posix()
        except ValueError:
            return g.name


def _untuk(data: dict, klip_rel: str) -> dict:
    r = data["klip"].get(klip_rel) or {}
    out = {"label": str(r.get("label") or ""),
           "batch": str(r.get("batch") or ""),
           "srcid": str(r.get("srcid") or "")}
    if r.get("mutu") is not None:
        try:
            out["mutu"] = float(r.get("mutu"))
        except (TypeError, ValueError):
            pass
    return out


def untuk(ds_dir: Path, klip_rel: str) -> dict:
    """Label satu klip (dibaca dari berkas). Untuk banyak klip sekaligus,
    panggil baca() sekali lalu akses dict-nya langsung (lihat klip_scan)."""
    return _untuk(baca(ds_dir), klip_rel)


def berlabel(data: dict, klip_rel: str) -> bool:
    """Klip ini sudah dilabeli: labelnya tak kosong."""
    return bool((data["klip"].get(klip_rel) or {}).get("label"))


def set_label(ds_dir: Path, klip_rel: str, label: str, *,
              batch: str | None = None, srcid: str | None = None,
              mutu: float | None = None) -> dict:
    """
    Setel label satu klip (dan opsional batch/srcid/mutu).

    `label` "" MENGHAPUS labelnya (klip kembali belum-dilabeli) tetapi batch/
    srcid tetap: itu metadata asal klip, bukan keputusan pelabelan. Entri yang
    tak memuat apa-apa lagi dibuang supaya berkasnya tak menyimpan baris kosong
    selamanya (sama seperti tag.pasang).
    """
    lab = _bersih(label)
    with _kunci:
        data = baca(ds_dir)
        rec = dict(data["klip"].get(klip_rel) or {})
        rec["label"] = lab
        if batch is not None:
            rec["batch"] = _bersih(batch)
        if srcid is not None:
            rec["srcid"] = _bersih(srcid)
        if mutu is not None:
            try:
                rec["mutu"] = float(mutu)
            except (TypeError, ValueError):
                pass
        rec = {k: v for k, v in rec.items() if v not in ("", None)}
        if rec:
            data["klip"][klip_rel] = rec
        else:
            data["klip"].pop(klip_rel, None)
        tulis(ds_dir, data)
    return {"ok": True, "klip": klip_rel, "label": lab,
            "berlabel": bool(lab)}


def rapikan(ds_dir: Path, kunci_yang_ada: set[str]) -> int:
    """Buang catatan milik klip yang berkasnya sudah tidak ada (saat pindai
    ulang), persis tag.rapikan."""
    with _kunci:
        data = baca(ds_dir)
        hilang = [k for k in data["klip"] if k not in kunci_yang_ada]
        if not hilang:
            return 0
        for k in hilang:
            data["klip"].pop(k, None)
        tulis(ds_dir, data)
    log.info("klip-tag dirapikan: %s catatan tanpa klip dibuang", len(hilang))
    return len(hilang)
