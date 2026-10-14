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

  gerbang — warnanya PENENTU: bentuk mirip tapi warna beda harus DITOLAK.
            (`color-gated`.) Perluasan mode `warna` untuk kasus yang sering
            terjadi di RVM: model cuma menerima satu produk (mis. Frisian Flag
            biru), tapi tak punya foto pesaing untuk diajari menolak. Caranya:
            ukur warna tiap kelas dari piksel objeknya, lalu buat salinan objek
            yang RONA-nya digeser ke warna di LUAR warna yang diterima (biru ->
            coklat/merah/hijau), dan simpan salinan itu sebagai contoh NEGATIF
            (label kosong). Model jadi belajar "bentuk benar + warna salah =
            tolak" tanpa perlu mengumpulkan produk pesaing.

            Rona objek asli TETAP dikunci seperti mode `warna` (gerbangnya
            datang dari negatif yang dibuat, bukan dari menggeser positif).
            Batasnya jujur: ini untuk beda warna KASAR (biru vs coklat), bukan
            beda corak halus (biru muda vs biru tua) — lihat mode-warna memory.

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
GERBANG = "color-gated"
MODE = (BENTUK, WARNA, GERBANG)
BAWAAN = BENTUK

# Mode yang mengunci rona objek positif (rona = identitas / penentu). `gerbang`
# ikut di sini: positifnya dikunci seperti `warna`, gerbangnya datang dari
# negatif berwarna-salah yang dibuat terpisah.
MODE_KUNCI_RONA = (WARNA, GERBANG)

# Tingkat ketat gerbang warna -> seberapa dekat warna "salah" boleh dibuat ke
# warna yang diterima, dan sebanyak apa.
#   margin_deg : zona haram (derajat rona) di sekitar warna diterima. Makin
#                KECIL margin, negatif makin dekat ke produk = gerbang makin
#                ketat (menolak selisih warna kecil). Margin ini JUGA yang
#                menjaga akurasi: warna produk asli yang bergeser karena lampu
#                (mis. chute ungu) tetap di dalam zona haram, jadi tak pernah
#                dibuat jadi negatif -- model tak pernah dihukum untuk warna sah.
#   n_warna    : berapa ragam warna salah yang dipakai.
#   porsi      : jumlah negatif warna-salah = porsi x jumlah POSITIF train saat
#                itu. DIBATASI proporsi, bukan per-gambar, supaya rasio
#                positif:negatif tetap terkendali berapa pun besar datasetnya --
#                negatif yang membanjiri bikin model takut mendeteksi (recall
#                turun). Bahkan "ketat" menjaga positif tetap mayoritas.
KETAT_GERBANG = {
    "longgar": {"margin_deg": 65, "n_warna": 3, "porsi": 0.15},
    "sedang":  {"margin_deg": 45, "n_warna": 4, "porsi": 0.25},
    "ketat":   {"margin_deg": 28, "n_warna": 5, "porsi": 0.40},
}
KETAT_BAWAAN = "sedang"

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
    GERBANG: {
        "nama": "Warna menentukan, tolak warna lain",
        "ringkas": "Rona dipertahankan, PLUS dibuatkan contoh warna-salah "
                   "sebagai negatif",
        "contoh": "cuma punya Frisian Flag biru. Produk sebentuk tapi coklat "
                  "(mis. Sosro) otomatis diajari ditolak, tanpa foto pesaing",
        "aug": "Rona objek asli TIDAK digeser (seperti mode warna). Tambahan: "
               "salinan objek yang warnanya digeser ke luar warna diterima, "
               "dipakai jadi contoh negatif.",
        "latih": "hsv_h 0,0 · hsv_s 0,20 · bgr 0,0 — rona dikunci, sama "
                 "seperti mode warna",
        "nilai": "Sama seperti mode warna, plus model diharapkan MENOLAK "
                 "bentuk yang sama berwarna lain.",
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
    if m not in MODE_KUNCI_RONA:
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

    Mode `gerbang` memakai angka yang sama dengan `warna`: rona positif dikunci,
    gerbangnya datang dari contoh negatif yang dibuat, bukan dari training.
    """
    if sah(mode) in MODE_KUNCI_RONA:
        return {"hsv_h": 0.0, "hsv_s": 0.20, "hsv_v": 0.50, "bgr": 0.0}
    return {"hsv_h": 0.030, "hsv_s": 0.70, "hsv_v": 0.50, "bgr": 0.10}


def par_gerbang(resep: dict) -> dict:
    """Setelan gerbang warna dari resep: {aktif, ketat, margin_deg, n_warna,
    salinan}. `aktif` hanya True kalau modenya `gerbang`."""
    w = (resep or {}).get("warna") or {}
    aktif = sah(w.get("mode")) == GERBANG
    ketat = str(w.get("ketat") or KETAT_BAWAAN).strip().lower()
    if ketat not in KETAT_GERBANG:
        ketat = KETAT_BAWAAN
    return {"aktif": aktif, "ketat": ketat, **KETAT_GERBANG[ketat]}
