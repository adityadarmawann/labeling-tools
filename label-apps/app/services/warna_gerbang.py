"""
Gerbang warna: mengubah salinan objek yang diterima jadi "warna salah" lalu
melabelinya sebagai contoh NEGATIF — supaya detektor menolak barang yang
bentuknya mirip tapi warnanya beda (mis. produk pesaing), tanpa harus
mengumpulkan banyak foto pesaing.

Dipakai oleh mode warna ketiga (`color-gated`, lihat mode_warna.py). Alurnya:

  1. UKUR warna tiap kelas yang diterima dari piksel OBJEK-nya (di dalam
     poligon/kotak anotasi), bukan seluruh frame — di RVM objek cuma sebagian
     kecil frame, sisanya ruang mesin. Hanya piksel yang cukup berwarna
     (saturasi tinggi) yang dihitung; putih/abu/logo tak punya rona yang stabil.
  2. Pita rona "diterima" = gabungan rona semua kelas + margin (toleransi
     lampu). Rona di LUAR pita = kandidat "warna salah".
  3. Buat salinan: putar rona OBJEK ke rona di luar pita (konteks dibiarkan),
     simpan dengan label KOSONG = contoh negatif.

Fokus di sumbu RONA (biru->coklat/merah/hijau), bukan terang-gelap, karena
terang-gelap persis yang digoyang lampu produksi — negatif di sumbu itu bakal
nabrak produk sah yang kena lampu aneh.
"""
from __future__ import annotations

import numpy as np

try:
    import cv2
except Exception:                                    # noqa: BLE001
    cv2 = None

# Piksel dianggap "berwarna" (rona-nya bermakna) kalau saturasi & value cukup.
# Putih/abu/hitam/logo disaring: rona-nya acak dan cuma mengotori pengukuran.
_S_MIN = 55
_V_MIN = 35
_V_MAKS = 240

# Target rona hangat (merah..kuning, 0-60 deg) sekalian diredupkan + diturunkan
# saturasinya sedikit biar jadi coklat/warna pudar yang realistis, bukan oranye
# neon. Rona dingin tak disentuh value-nya.
_HANGAT_MAKS_DEG = 60


def _poly_ke_titik(poly_norm, w: int, h: int) -> np.ndarray:
    """Poligon ter-normalisasi ([x1,y1,x2,y2,...] atau [[x,y],...], 0..1) ->
    titik piksel int32 untuk cv2.fillPoly."""
    a = np.asarray(poly_norm, dtype=np.float64).reshape(-1, 2)
    a[:, 0] *= w
    a[:, 1] *= h
    return a.round().astype(np.int32)


def masker_objek(polys_norm, w: int, h: int) -> np.ndarray:
    """Masker biner (0/255) gabungan semua poligon objek pada frame w x h."""
    m = np.zeros((h, w), np.uint8)
    for p in polys_norm:
        titik = _poly_ke_titik(p, w, h)
        if len(titik) >= 3:
            cv2.fillPoly(m, [titik], 255)
        elif len(titik) == 2:                        # kotak [[x1,y1],[x2,y2]]
            (x1, y1), (x2, y2) = titik
            cv2.rectangle(m, (int(x1), int(y1)), (int(x2), int(y2)), 255, -1)
    return m


def hist_rona_objek(img_bgr: np.ndarray, polys_norm) -> np.ndarray:
    """Histogram rona (180 bin, 0..179 = 0..358 deg) piksel BERWARNA di dalam
    objek. Piksel pucat/gelap/putih disaring supaya tak mengganggu."""
    h, w = img_bgr.shape[:2]
    m = masker_objek(polys_norm, w, h)
    if not m.any():
        return np.zeros(180, np.int64)
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    sel = (m > 0) & (hsv[:, :, 1] >= _S_MIN) \
        & (hsv[:, :, 2] >= _V_MIN) & (hsv[:, :, 2] <= _V_MAKS)
    if not sel.any():
        return np.zeros(180, np.int64)
    return np.bincount(hsv[:, :, 0][sel], minlength=180).astype(np.int64)


def _pita_diterima(hist: np.ndarray, ambang: float = 0.25) -> np.ndarray:
    """Bin rona (0..179) yang 'diterima' = count >= ambang * puncak. Ambang
    sengaja agak tinggi: pita = warna INTI produk (mode dominan), sedangkan
    toleransi lampu diurus `margin_deg` saat pelebaran. Ambang rendah membuat
    ekor histogram ikut 'diterima', lalu margin melebarkannya sampai menelan
    hampir seluruh roda warna — tak ada 'warna salah' tersisa untuk dibuat."""
    if hist.sum() <= 0:
        return np.zeros(180, bool)
    return hist >= (hist.max() * ambang)


def _lebarkan(mask: np.ndarray, margin_bin: int) -> np.ndarray:
    """Lebarkan masker rona melingkar (0..179 wrap) sebanyak margin_bin ke
    kiri-kanan — ini margin toleransi lampu di sekitar pita diterima."""
    out = mask.copy()
    for d in range(1, margin_bin + 1):
        out |= np.roll(mask, d) | np.roll(mask, -d)
    return out


def rona_terlarang(hist_per_kelas: dict, margin_deg: int = 40) -> np.ndarray:
    """Gabungan pita diterima SEMUA kelas + margin. Masker boolean 180 bin;
    True = rona yang TIDAK boleh dipakai jadi negatif (terlalu dekat produk)."""
    mask = np.zeros(180, bool)
    for hist in hist_per_kelas.values():
        mask |= _pita_diterima(np.asarray(hist, dtype=np.int64))
    if not mask.any():
        return mask
    return _lebarkan(mask, max(0, margin_deg // 2))


def rona_dominan(hist) -> int | None:
    """Rona dominan (derajat 0..358) dari sebuah histogram, atau None."""
    h = np.asarray(hist, dtype=np.int64)
    if h.sum() <= 0:
        return None
    return int(np.argmax(h)) * 2


def hue_negatif(terlarang: np.ndarray, n: int = 4) -> list[int]:
    """n rona target (derajat) di LUAR wilayah terlarang, tersebar merata di
    roda warna. Kalau seluruh roda terlarang (produk warna-warni), kembalikan []."""
    bebas = np.where(~terlarang)[0]                  # bin 0..179 yang boleh
    if len(bebas) == 0:
        return []
    # Ambil kandidat tiap ~30 deg, pilih yang jatuh di wilayah bebas, sebar.
    kandidat = [d for d in range(0, 360, 20) if not terlarang[(d // 2) % 180]]
    if not kandidat:
        kandidat = [int(bebas[i] * 2) for i in
                    np.linspace(0, len(bebas) - 1, num=min(n * 2, len(bebas))).astype(int)]
    if len(kandidat) <= n:
        return kandidat
    idx = np.linspace(0, len(kandidat) - 1, num=n).round().astype(int)
    return [kandidat[i] for i in sorted(set(idx))]


def hue_ke_hex(deg: int, target: bool = False) -> str:
    """Rona (derajat) -> warna tampil "#rrggbb" untuk swatch pratinjau. Kalau
    `target` dan ronanya hangat, diredupkan sama seperti recolor_objek supaya
    swatch mencerminkan warna negatif yang benar-benar dibuat (coklat, bukan
    oranye neon)."""
    s, v = 200, 220
    if target and (deg % 360 <= _HANGAT_MAKS_DEG
                   or deg % 360 >= 360 - _HANGAT_MAKS_DEG):
        v = int(v * 0.6)
        s = int(s * 0.8)
    px = np.uint8([[[(deg % 360) // 2, s, v]]])
    b, g, r = cv2.cvtColor(px, cv2.COLOR_HSV2BGR)[0, 0]
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}"


def swatch_diterima(hist, n: int = 3) -> list[int]:
    """n rona (derajat) paling sering di sebuah histogram — warna yang diterima
    untuk dipamerkan sebagai swatch. Mode-mode dominannya, bukan sekadar puncak."""
    h = np.asarray(hist, dtype=np.int64)
    if h.sum() <= 0:
        return []
    # Puncak lokal yang berjauhan (>=20 deg) supaya tak menampilkan 3 biru kembar.
    urut = np.argsort(h)[::-1]
    dipakai: list[int] = []
    for b in urut:
        if h[b] < h.max() * 0.10:
            break
        if all(min(abs(int(b) - u), 180 - abs(int(b) - u)) >= 10 for u in dipakai):
            dipakai.append(int(b))
        if len(dipakai) >= n:
            break
    return [b * 2 for b in dipakai]


def recolor_objek(img_bgr: np.ndarray, polys_norm, target_deg: int,
                  dom_deg: int | None) -> np.ndarray:
    """Salin gambar dengan RONA objek diputar ke `target_deg`. Konteks (luar
    objek) dibiarkan. Rona diputar sebesar (target - dominan) supaya warna
    dominan objek pindah ke target sambil struktur warnanya (logo, putih) ikut
    bergeser wajar. Target hangat (0-60 deg) sekalian diredupkan jadi coklat."""
    h, w = img_bgr.shape[:2]
    m = masker_objek(polys_norm, w, h)
    if not m.any():
        return img_bgr
    base = dom_deg if dom_deg is not None else (rona_dominan(
        hist_rona_objek(img_bgr, polys_norm)) or 0)
    dh = int(round((target_deg - base) / 2))         # OpenCV H 0..179 = 0..358
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV).astype(np.int16)
    sel = m > 0
    hsv[:, :, 0][sel] = (hsv[:, :, 0][sel] + dh) % 180
    # Coklat realistis: untuk target hangat, redupkan value & saturasi objek.
    if target_deg % 360 <= _HANGAT_MAKS_DEG or target_deg % 360 >= 360 - _HANGAT_MAKS_DEG:
        hsv[:, :, 2][sel] = (hsv[:, :, 2][sel] * 0.6).astype(np.int16)
        hsv[:, :, 1][sel] = np.clip(hsv[:, :, 1][sel] * 0.8, 0, 255).astype(np.int16)
    hsv = np.clip(hsv, 0, 255).astype(np.uint8)
    out = img_bgr.copy()
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    out[sel] = bgr[sel]
    return out
