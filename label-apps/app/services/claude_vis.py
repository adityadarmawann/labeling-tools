"""Claude sebagai sumber auto-label: sebut nama kelasnya, ia membaca gambar.

Kenapa ini ada di sebelah YOLO-World dan SAM 3, bukan menggantikan keduanya:
ketiganya menjawab pertanyaan yang BERBEDA. YOLO-World dan SAM 3 pandai
menemukan "ada botol di sini"; yang sulit bagi keduanya adalah membedakan
varian yang bentuknya sama persis dan hanya beda tulisan pada kemasan, dan
justru di situlah kesalahan paling sering terjadi pada model terlatih projek
ini (frestea apel lawan teh hijau, 250ml lawan 350ml).

Yang TIDAK boleh diharapkan darinya: ketelitian piksel. Kotaknya kira-kira,
jadi untuk projek poligon ia dirapikan SAM lebih dulu, pola yang sama dengan
jalur GroundingDINO. Karena itu `bentuk`-nya rectangle.

Dimatikan dengan cara dikosongkan kuncinya, dan matinya menyeluruh: pilihan
Claude tidak muncul di antarmuka sama sekali. Menawarkan sesuatu yang pasti
gagal saat ditekan itu lebih buruk daripada tidak menawarkannya.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import cv2
import numpy as np

from ..log import catat

log = catat("labelapp.claude")

# Nama model di daftar "Deteksi dari teks". Dipakai autolabel untuk mengenali
# bahwa permintaan ini harus ke sini, bukan ke osam.
MODEL = "claude"

# Kotak diminta dalam skala 0..1000, bukan piksel. Alasannya satu: gambar
# dikecilkan sebelum dikirim, jadi koordinat piksel yang dijawab akan merujuk
# ukuran yang SUDAH dikecilkan, dan kekeliruan seperti itu tak terlihat
# sebagai galat, hanya sebagai kotak yang meleset.
SKALA = 1000


class ClaudeGagal(Exception):
    """Permintaan ke Claude tidak bisa dijalankan atau jawabannya tak terbaca."""


def aktif(settings) -> bool:
    """Fitur ini hidup hanya kalau kuncinya disetel."""
    return bool((getattr(settings, "anthropic_api_key", "") or "").strip())


def _klien(settings):
    """Klien Anthropic. Dipisah supaya uji bisa menggantinya tanpa jaringan."""
    try:
        import anthropic
    except ImportError as e:                    # noqa: BLE001
        raise ClaudeGagal(
            "paket 'anthropic' belum terpasang di venv ini") from e
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _gambar_untuk_kirim(img: Path, sisi_maks: int) -> tuple[str, int, int]:
    """(JPEG base64, lebar asli, tinggi asli).

    Dikecilkan supaya biaya token dan waktu unggah terkendali. Ukuran ASLI
    tetap dikembalikan karena kotak jawabannya harus diskalakan ke sana, bukan
    ke ukuran kirim.
    """
    bgr = cv2.imread(str(img))
    if bgr is None:
        raise ClaudeGagal("gambar tidak bisa dibaca")
    H, W = bgr.shape[:2]
    sisi = max(H, W)
    if sisi > sisi_maks:
        skala = sisi_maks / sisi
        bgr = cv2.resize(bgr, (max(1, int(W * skala)), max(1, int(H * skala))),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise ClaudeGagal("gambar gagal disandikan")
    return base64.b64encode(buf.tobytes()).decode("ascii"), W, H


def _perintah(kelas: list[str]) -> str:
    """Perintahnya menyodorkan daftar kelas dan MELARANG mengarang nama.

    Tanpa larangan itu, jawabannya cenderung memakai istilah sendiri ("green
    tea bottle") yang lalu masuk ke dataset sebagai kelas baru, dan daftar
    kelas projek membengkak oleh sinonim.
    """
    daftar = "\n".join(f"- {k}" for k in kelas)
    return (
        "Kamu membantu melabeli dataset deteksi objek.\n\n"
        "Temukan SEMUA objek pada gambar yang termasuk salah satu kelas "
        "berikut, dan HANYA kelas berikut:\n"
        f"{daftar}\n\n"
        "Perhatikan tulisan pada kemasan untuk membedakan varian dan ukuran "
        "yang bentuknya mirip.\n\n"
        "Jawab HANYA dengan JSON array, tanpa penjelasan apa pun:\n"
        '[{"label": "<salah satu kelas di atas>", '
        '"box": [x1, y1, x2, y2], "confidence": 0.0}]\n\n'
        f"Koordinat dalam skala 0 sampai {SKALA} relatif lebar dan tinggi "
        "gambar, dengan x1<x2 dan y1<y2. confidence 0 sampai 1.\n"
        "Kalau tidak ada objek yang cocok, jawab []."
    )


def _baca_jawaban(teks: str, kelas: list[str], W: int, H: int,
                  ambang: float, maks: int) -> list[dict]:
    """Jawaban Claude -> daftar {label, bbox piksel, skor}.

    Dipisah dari pemanggilan jaringan supaya bisa diuji apa adanya, dan
    karena di sinilah hampir semua yang bisa salah berada: JSON terbungkus
    teks, kelas yang dikarang, kotak terbalik, dan koordinat di luar gambar.
    """
    if not teks:
        return []
    # JSON kadang datang terbungkus ```json ... ``` atau kalimat pengantar.
    cocok = re.search(r"\[.*\]", teks, re.S)
    if not cocok:
        return []
    try:
        mentah = json.loads(cocok.group(0))
    except ValueError:
        log.warning("jawaban Claude bukan JSON yang sah")
        return []
    if not isinstance(mentah, list):
        return []

    sah = {k.strip().lower(): k for k in kelas if str(k).strip()}
    out: list[dict] = []
    for d in mentah:
        if not isinstance(d, dict):
            continue
        nama = sah.get(str(d.get("label", "")).strip().lower())
        if nama is None:
            # Kelas yang dikarang DIBUANG, bukan dipakai apa adanya: satu
            # sinonim yang lolos akan jadi kelas baru di dataset.
            continue
        kotak = d.get("box") or d.get("bbox")
        if not isinstance(kotak, (list, tuple)) or len(kotak) != 4:
            continue
        try:
            x1, y1, x2, y2 = (float(v) for v in kotak)
            skor = float(d.get("confidence", d.get("skor", 0.0)))
        except (TypeError, ValueError):
            continue
        if skor < ambang:
            continue
        # Terbalik dibetulkan, bukan dibuang: isinya tetap benar.
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        px1 = int(round(max(0.0, min(1.0, x1 / SKALA)) * W))
        py1 = int(round(max(0.0, min(1.0, y1 / SKALA)) * H))
        px2 = int(round(max(0.0, min(1.0, x2 / SKALA)) * W))
        py2 = int(round(max(0.0, min(1.0, y2 / SKALA)) * H))
        if px2 - px1 < 2 or py2 - py1 < 2:
            continue                    # kotak sekecil itu tak bisa dipakai
        out.append({"label": nama, "bbox": (px1, py1, px2, py2),
                    "skor": max(0.0, min(1.0, skor))})
    out.sort(key=lambda d: d["skor"], reverse=True)
    return out[:max(1, int(maks))]


def deteksi(img: Path, kelas: list[str], *, settings, ambang: float = 0.1,
            maks: int = 100) -> list[dict]:
    """Objek pada satu gambar menurut Claude: [{label, bbox piksel, skor}]."""
    kelas = [str(k).strip() for k in kelas if str(k).strip()]
    if not kelas:
        raise ClaudeGagal("belum ada nama kelas yang dicari")
    if not aktif(settings):
        raise ClaudeGagal("kunci API Claude belum disetel di server")

    b64, W, H = _gambar_untuk_kirim(img, int(settings.anthropic_sisi_maks))
    try:
        jawab = _klien(settings).messages.create(
            model=settings.anthropic_model,
            max_tokens=2048,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/jpeg",
                                             "data": b64}},
                {"type": "text", "text": _perintah(kelas)},
            ]}],
        )
    except ClaudeGagal:
        raise
    except Exception as e:                      # noqa: BLE001
        raise ClaudeGagal(f"panggilan Claude gagal: {str(e)[:160]}") from e

    teks = "".join(getattr(b, "text", "") or "" for b in (jawab.content or []))
    return _baca_jawaban(teks, kelas, W, H, ambang, maks)
