"""
Mode warna: apakah warna PETUNJUK yang sah, atau pintasan yang harus dirusak.

Ini pertanyaan yang menentukan tiga hal sekaligus — augmentasi, setelan
training, dan cara menilai model — dan jawabannya berbeda untuk projek yang
berbeda. Tanpa konsep ini, salah satunya pasti mengkhianati yang lain.

DUA MODE

  bentuk  — kelasnya dibedakan BENTUK, warna cuma kebetulan.
            Contohnya klasifikasi material RVM: botol / kaleng / tetra /
            gelas plastik. Sebuah botol tetap botol apa pun warnanya, jadi
            warna adalah petunjuk termurah yang paling menyesatkan.

            Inilah kasus v14, dan kegagalan yang diperbaikinya: v13 memakai
            hsv_h=0.0 sehingga model belajar memutuskan lewat warna, lalu
            melaporkan mAP50-95 0,9499 dan benar 0 dari 7 pada foto RVM
            sungguhan. Gelas plastik dijawab `kaleng` 0,97; gelas yang sama
            dalam grayscale dijawab `plastic-cup` 0,93.

            Yang harus dilakukan: RUSAK warna sebagai petunjuk. Augmentasi
            menggeser rona, training membuka hsv_h, dan model yang jawabannya
            berubah karena warna dinilai BURUK.

  warna   — warnanya BAGIAN dari identitas kelasnya.
            Contohnya pengenalan produk: kahf_extradry_deodorant_45ml lawan
            kahf_skinergizing_facewash_50ml. Yang membedakan keduanya sebagian
            besar justru warna dan desain kemasannya. Merusak warna di sini
            bukan memperbaiki model, melainkan membuang petunjuk terbaiknya.

            Yang harus dilakukan: PERTAHANKAN rona. Augmentasi hanya boleh
            meredupkan dan menerangkan, training menutup hsv_h dan bgr, dan
            model yang jawabannya berubah karena rona digeser TIDAK dinilai
            buruk — itu memang yang diminta.

KENAPA SATU BERKAS SENDIRI
Mode ini harus dibaca tiga bagian yang berbeda dan tidak boleh berselisih:
augmentasi (services/olah.py), training (services/latih.py), dan evaluasi
(services/evaluasi.py). Kalau tiap bagian menyimpulkannya sendiri-sendiri,
cepat atau lambat ada satu yang menyimpulkan berbeda — dan akibatnya baru
terlihat berjam-jam kemudian dalam bentuk model yang tidak bisa dipakai.

YANG SUDAH TERJADI TANPA KONSEP INI
eval-produksi-paragon.py di folder paragon adalah salinan skrip v14 yang masih
mencetak "BURUK. Model memutuskan lewat warna" untuk dataset Kahf — vonis
yang justru terbalik bagi model pengenal produk.
"""
from __future__ import annotations

BENTUK = "bentuk"
WARNA = "warna"
MODE = (BENTUK, WARNA)
BAWAAN = BENTUK

# Operasi augmentasi yang MENGGESER RONA. Di mode `warna` semuanya dimatikan:
# rona adalah identitas kelasnya, dan menggesernya sama dengan mengganti
# labelnya diam-diam.
OP_GESER_RONA = ("hue_sat", "blackbody", "iluminan", "color_jitter",
                 "grayscale", "saturasi")

# Operasi yang mengubah TERANG tanpa menyentuh rona. Ini tetap menyala di
# KEDUA mode — ruang detektor kadang terang kadang remang, dan model harus
# tahan terhadap itu berapa pun modenya. Justru inilah yang membuat mode
# `warna` tetap punya augmentasi yang berguna.
OP_TERANG = ("terang_kontras", "gamma", "eksposur", "vignette")

KETERANGAN = {
    BENTUK: {
        "nama": "Bentuk menentukan kelas",
        "ringkas": "Warna dirusak supaya model belajar bentuk",
        "contoh": "botol / kaleng / tetra — sebuah botol tetap botol apa pun "
                  "warnanya",
        "aug": "Rona digeser lebar: hue, suhu warna, lampu berwarna, "
               "grayscale acak.",
        "latih": "hsv_h 0,030 · hsv_s 0,70 · bgr 0,10 (angka v14)",
        "nilai": "Jawaban yang berubah karena warna dinilai BURUK.",
    },
    WARNA: {
        "nama": "Warna bagian dari kelas",
        "ringkas": "Rona dipertahankan, hanya terang yang divariasikan",
        "contoh": "kahf_extradry_deodorant vs kahf_skinergizing_facewash — "
                  "warna kemasan yang membedakannya",
        "aug": "Rona TIDAK digeser. Yang divariasikan cuma terang, gamma, "
               "eksposur, dan vignette.",
        "latih": "hsv_h 0,0 · hsv_s 0,20 · bgr 0,0 — rona dikunci",
        "nilai": "Jawaban yang berubah karena RONA digeser itu wajar. Yang "
                 "tetap dinilai buruk: jawaban yang berubah karena "
                 "TERANG berubah.",
    },
}


def sah(mode: str) -> str:
    """Normalkan nilainya; apa pun yang tidak dikenal jadi bawaan."""
    m = (mode or "").strip().lower()
    return m if m in MODE else BAWAAN


def dari_resep(resep: dict) -> str:
    """Mode yang tercatat di resep sebuah versi."""
    return sah(((resep or {}).get("warna") or {}).get("mode"))


def tulis_ke_resep(resep: dict, mode: str) -> dict:
    """Catat mode ke resep, tanpa mengubah yang lain."""
    keluar = dict(resep or {})
    keluar["warna"] = {**(keluar.get("warna") or {}), "mode": sah(mode)}
    return keluar


def terap_ke_aug(resep: dict, mode: str | None = None) -> dict:
    """Matikan operasi penggeser rona kalau modenya `warna`.

    Dipaksa di sini, di satu tempat, bukan diserahkan ke orang yang mengisi
    form: mode `warna` dengan hue_sat yang tertinggal menyala adalah dataset
    yang labelnya diam-diam salah — objek berwarna biru diberi label produk
    yang kemasannya hijau — dan tidak ada yang akan menyadarinya sampai
    modelnya gagal.
    """
    m = sah(mode if mode is not None else dari_resep(resep))
    keluar = dict(resep or {})
    if m != WARNA:
        return keluar
    aug = dict(keluar.get("aug") or {})
    for oid in OP_GESER_RONA:
        aug[oid] = {**(aug.get(oid) or {}), "aktif": False}
    keluar["aug"] = aug
    return keluar


def par_latih(mode: str) -> dict:
    """Setelan warna waktu-latih yang sejalan dengan modenya.

    Angka mode `bentuk` dari train-rvm-v14.py. Angka mode `warna` adalah
    kebalikannya dan sengaja mirip setelan v13 — yang untuk material menjadi
    bencana, tetapi untuk pengenalan produk justru yang benar: rona dikunci
    supaya model boleh memakainya.

    hsv_v tetap dibuka lebar di KEDUA mode. Terang berubah di ruang detektor
    mana pun, dan itu harus selalu jadi petunjuk yang tidak bisa diandalkan.
    """
    if sah(mode) == WARNA:
        return {"hsv_h": 0.0, "hsv_s": 0.20, "hsv_v": 0.50, "bgr": 0.0}
    return {"hsv_h": 0.030, "hsv_s": 0.70, "hsv_v": 0.50, "bgr": 0.10}
