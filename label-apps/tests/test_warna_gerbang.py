"""
Gerbang warna (mode `color-gated`): contoh "bentuk benar, warna salah".

Yang dijaga di sini:
  * warna_gerbang mengukur rona objek dari poligon, memilih warna di LUAR pita
    yang diterima, dan merecolor HANYA objek (konteks tak tersentuh);
  * mode_warna.par_gerbang membaca tingkat ketat jadi margin/salinan;
  * saat versi dibangun dengan mode gerbang, lahir sampel negatif berwarna-salah
    (label KOSONG) dari tiap positif train — dan mode lain TIDAK membuatnya.

Ini pelengkap detektor warna: model boleh menolak barang sebentuk tapi warnanya
beda tanpa perlu foto produk pesaing. Batasnya jujur: beda warna KASAR, bukan
corak halus (lihat mode_warna.py).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services import mode_warna, olah, warna_gerbang as wg
from tests.test_data import masuk, PW_PAUL


# ------------------------------------------------------------- unit: warna_gerbang
def _kotak_poly(x, y, w, h, iw, ih):
    return [x / iw, y / ih, (x + w) / iw, y / ih,
            (x + w) / iw, (y + h) / ih, x / iw, (y + h) / ih]


def _gambar_biru(iw=320, ih=240, kotak=(60, 40, 120, 120)):
    """Frame abu dengan satu objek BIRU pekat (rona ~ 218 deg)."""
    rng = np.random.default_rng(0)
    im = (rng.random((ih, iw, 3)) * 30 + 90).astype(np.uint8)   # latar abu
    x, y, w, h = kotak
    # BGR biru: B tinggi, R rendah -> rona biru. Sedikit derau tapi tetap biru.
    im[y:y + h, x:x + w, 0] = 200 + rng.integers(0, 30, (h, w))
    im[y:y + h, x:x + w, 1] = 70 + rng.integers(0, 30, (h, w))
    im[y:y + h, x:x + w, 2] = 25 + rng.integers(0, 20, (h, w))
    return im, _kotak_poly(x, y, w, h, iw, ih)


def test_rona_dominan_objek_biru():
    im, poly = _gambar_biru()
    dom = wg.rona_dominan(wg.hist_rona_objek(im, [poly]))
    # Biru di ruang 0..360 ada di sekitar 200..240 deg.
    assert dom is not None and 195 <= dom <= 245, dom


def test_piksel_pucat_tak_mengganggu_pengukuran():
    """Latar abu (saturasi rendah) tak boleh ikut terhitung sebagai rona."""
    im, _poly = _gambar_biru()
    # Poligon seluruh frame: tetap harus terbaca biru, bukan abu.
    full = [0, 0, 1, 0, 1, 1, 0, 1]
    dom = wg.rona_dominan(wg.hist_rona_objek(im, [full]))
    assert dom is not None and 195 <= dom <= 245, dom


def test_warna_salah_di_luar_pita_diterima():
    im, poly = _gambar_biru()
    hist = wg.hist_rona_objek(im, [poly])
    terlarang = wg.rona_terlarang({0: hist}, margin_deg=45)
    target = wg.hue_negatif(terlarang, n=4)
    assert target, "harus ada warna salah untuk produk satu-warna"
    # Tak satu pun target boleh jatuh di zona terlarang (biru + margin).
    for t in target:
        assert not terlarang[(t // 2) % 180], (t, "jatuh di zona terlarang")


def test_recolor_hanya_menyentuh_objek():
    im, poly = _gambar_biru()
    dom = wg.rona_dominan(wg.hist_rona_objek(im, [poly]))
    out = wg.recolor_objek(im, [poly], target_deg=130, dom_deg=dom)   # ke hijau
    m = wg.masker_objek([poly], im.shape[1], im.shape[0])
    # Luar objek: identik byte demi byte.
    assert np.array_equal(out[m == 0], im[m == 0])
    # Dalam objek: rona bergeser menjauh dari biru, mendekat ke hijau.
    dom2 = wg.rona_dominan(wg.hist_rona_objek(out, [poly]))
    assert dom2 is not None and 90 <= dom2 <= 170, dom2


def test_produk_warna_warni_tak_memaksa_negatif():
    """Kalau seluruh roda warna 'diterima', hue_negatif mengembalikan []."""
    semua = np.ones(180, bool)
    assert wg.hue_negatif(semua, n=4) == []


# ------------------------------------------------------------- unit: mode_warna
def test_par_gerbang_ketat_menaikkan_porsi_mengecilkan_margin():
    longgar = mode_warna.par_gerbang({"warna": {"mode": "color-gated",
                                                 "ketat": "longgar"}})
    ketat = mode_warna.par_gerbang({"warna": {"mode": "color-gated",
                                              "ketat": "ketat"}})
    assert longgar["aktif"] and ketat["aktif"]
    assert ketat["margin_deg"] < longgar["margin_deg"]
    assert ketat["porsi"] > longgar["porsi"]
    # Bahkan ketat harus menjaga positif tetap mayoritas (porsi < 0,5).
    assert ketat["porsi"] < 0.5


def test_par_gerbang_mati_untuk_mode_lain():
    assert not mode_warna.par_gerbang({"warna": {"mode": "bentuk"}})["aktif"]
    assert not mode_warna.par_gerbang({"warna": {"mode": "warna"}})["aktif"]


def test_gerbang_mengunci_rona_seperti_warna():
    # Positif dikunci: operasi penggeser rona dimatikan, seperti mode warna.
    r = mode_warna.terap_ke_aug({"aug": {"hue_sat": {"aktif": True}}},
                                "color-gated")
    assert r["aug"]["hue_sat"]["aktif"] is False
    assert mode_warna.par_latih("color-gated") == mode_warna.par_latih("warna")


# ------------------------------------------------------------- integrasi (build)
def _ds_biru(klien, n_pos=8, n_neg=4) -> Path:
    ruang = Path(klien.get("/api/projek/daftar").json()["ruang"])
    d = ruang / "gerbang"
    d.mkdir(parents=True, exist_ok=True)
    (d / "classes.txt").write_text("susu\n")
    for i in range(n_pos + n_neg):
        if i < n_pos:
            im, poly = _gambar_biru()
            pts = np.array(poly, float).reshape(-1, 2)
            pts[:, 0] *= 320
            pts[:, 1] *= 240
            shapes = [{"label": "susu", "shape_type": "polygon",
                       "points": pts.tolist()}]
        else:
            rng = np.random.default_rng(100 + i)
            im = (rng.random((240, 320, 3)) * 40 + 90).astype(np.uint8)
            shapes = []
        cv2.imwrite(str(d / f"g{i:02d}.jpg"), im)
        (d / f"g{i:02d}.json").write_text(json.dumps({
            "version": "0.4.36", "flags": {}, "shapes": shapes,
            "imagePath": f"g{i:02d}.jpg", "imageData": None,
            "imageHeight": 240, "imageWidth": 320}))
    return d


def _tunggu(klien, batas=120):
    for _ in range(batas * 10):
        k = klien.get("/api/versi/kemajuan").json()
        if k.get("selesai") or k.get("batal") or k.get("galat"):
            return k
        time.sleep(0.1)
    pytest.fail(f"versi tak selesai dalam {batas} detik: {k}")


def _resep(mode, **warna):
    # Matikan fase yang memperbanyak gambar supaya yang diuji hanya gerbang.
    return {"volume": {"per_gambar": 0}, "warna": {"mode": mode, **warna},
            "fase": {"crop_zoom": {"aktif": False},
                     "balans_skala": {"aktif": False},
                     "balans_kelas": {"aktif": False},
                     "porsi_negatif": {"aktif": False}}}


def test_gerbang_melahirkan_negatif_warna_salah(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d = _ds_biru(klien)
    klien.post(f"/setsrc?path={d}")
    r = klien.post("/api/versi/mulai?split=100,0,0",
                   json={"resep": _resep("color-gated", ketat="sedang")}).json()
    assert r.get("ok"), r
    assert _tunggu(klien).get("selesai")

    v = d / ".versi" / "v1"
    warnaneg = list((v / "train" / "images").glob("*_warnaneg*.jpg"))
    assert warnaneg, "mode gerbang harus membuat sampel warna-salah"

    for ip in warnaneg:
        lp = v / "train" / "labels" / f"{ip.stem}.txt"
        # Negatif = label KOSONG (bukan tidak ada berkas).
        assert lp.exists() and lp.read_text().strip() == "", ip.name
        # Rona objek pada negatif harus SUDAH bukan biru. Poligonnya diambil dari
        # positif sumber (negatif sendiri tak berlabel).
        stem = ip.stem.split("_warnaneg")[0]
        src = olah.baca_label(v / "train" / "labels" / f"{stem}.txt")
        polys = [p for _, p in src]
        img = cv2.imread(str(ip))
        dom = wg.rona_dominan(wg.hist_rona_objek(img, polys))
        if dom is not None:                      # objek cukup berwarna
            assert not (195 <= dom <= 245), f"{ip.name} masih biru: {dom}"

    # Manifes menandainya sebagai asal 'warna_neg' tanpa objek.
    man = json.loads((v / "MANIFES.json").read_text())
    baris = man if isinstance(man, list) else man.get("berkas", man.get("items", []))
    wn = [b for b in baris if isinstance(b, dict) and b.get("asal") == "warna_neg"]
    assert wn and all(b.get("objek", 0) == 0 for b in wn), "manifes warna_neg salah"


def _biru_dominan(hexwarna: str) -> bool:
    r = int(hexwarna[1:3], 16); g = int(hexwarna[3:5], 16); b = int(hexwarna[5:7], 16)
    return b > r and b > g


def test_pratinjau_band_warna_memisahkan_diterima_dari_ditolak(klien, lingkungan):
    """Endpoint pratinjau: warna diterima = biru (produk), warna ditolak TIDAK
    boleh biru. Ini jaminan akurasi yang bisa diperiksa orang sebelum membangun:
    warna salah yang menabrak produk asli justru menurunkan akurasi."""
    masuk(klien, "paul", PW_PAUL)
    d = _ds_biru(klien)
    klien.post(f"/setsrc?path={d}")
    r = klien.post("/api/versi/warna-band",
                   json={"resep": {"warna": {"mode": "color-gated",
                                             "ketat": "sedang"}}}).json()
    assert r.get("ok") and r.get("cukup"), r
    # Kelas 'susu' terukur, dan swatch diterimanya biru.
    susu = [k for k in r["kelas"] if k["nama"] == "susu"]
    assert susu and susu[0]["rona"], r
    assert any(_biru_dominan(h) for h in susu[0]["rona"]), susu
    # Warna yang akan ditolak ADA dan TAK satu pun biru.
    assert r["salah"], r
    assert not any(_biru_dominan(h) for h in r["salah"]), r["salah"]


def test_mode_lain_tak_membuat_negatif_warna_salah(klien, lingkungan):
    masuk(klien, "paul", PW_PAUL)
    d = _ds_biru(klien)
    klien.post(f"/setsrc?path={d}")
    klien.post("/api/versi/mulai?split=100,0,0",
               json={"resep": _resep("warna")}).json()
    assert _tunggu(klien).get("selesai")
    v = d / ".versi" / "v1"
    assert not list((v / "train" / "images").glob("*_warnaneg*.jpg"))
