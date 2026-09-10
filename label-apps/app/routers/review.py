"""Papan periksa: grid, tampilan besar, thumbnail, tandai latar."""
from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import Settings, get_settings
from ..deps import current_session, current_session_api, is_local, require_local
from ..security import safe_slug
from ..services import (annotations, anylabeling, projek as svc_projek,
                        render, scanner, tambah, tugas)
from ..services.annotations import Menolak
from ..session import Session
from ..templating import templates
from .datasets import picker_context

router = APIRouter(tags=["review"])

THUMB_MIN, THUMB_MAX = 32, 2000


# Pilihan urutan. Kunci dipakai di URL, nilainya yang tampil di menu.
URUT = {
    "nama": "Nama berkas (A→Z)",
    "nama-turun": "Nama berkas (Z→A)",
    "label-baru": "Terbaru dilabeli",
    "label-lama": "Terlama dilabeli",
    "gambar-baru": "Gambar terbaru ditambahkan",
    "gambar-lama": "Gambar terlama ditambahkan",
    "objek-banyak": "Objek terbanyak",
    "objek-sedikit": "Objek tersedikit",
}
URUT_BAWAAN = "nama"

# Jumlah kartu per halaman. Empat pilihan saja, dan angkanya sengaja bulat:
# yang dibutuhkan orang bukan angka bebas, melainkan "sedikit dulu" atau
# "sekalian semuanya". 50 jadi bawaan mengikuti Roboflow — pada projek 11.319
# gambar, memuat semuanya sekali jalan berarti HTML puluhan megabita.
PER_PILIHAN = (50, 100, 500, 1000)
PER_BAWAAN = 50

# Keadaan "tanpa kelas" yang bisa ikut dicentang di dropdown kelas. Keduanya
# sama-sama gambar tanpa objek, tetapi artinya berlawanan: `latar` sudah selesai
# diperiksa dan sengaja dikosongkan (padanan Mark Null di Roboflow), `unlab`
# justru pekerjaan yang belum dikerjakan.
TANPA_KELAS = {
    "latar": ("Latar (tanpa objek)", "bg"),
    "unlab": ("Belum dilabeli", "stop"),
}


def _urutkan(items: list[dict], urut: str) -> list[dict]:
    """
    Urutkan hasil saringan.

    Waktu label dibaca dari disk di sini, bukan diambil dari hasil pindai:
    orang melabeli beberapa gambar lalu kembali ke grid untuk melihat hasilnya,
    dan nilai yang dibekukan saat memindai tidak akan berubah sampai dipindai
    ulang — persis membuat "Terbaru dilabeli" tidak berguna.

    Nama berkas selalu jadi kunci kedua supaya urutannya tetap sama di antara
    gambar yang nilainya seri; tanpa itu, grid bisa berubah urutan sendiri tiap
    kali dimuat ulang.
    """
    nama = lambda it: it["img"].name.lower()          # noqa: E731
    if urut == "nama-turun":
        return sorted(items, key=nama, reverse=True)
    if urut in ("label-baru", "label-lama"):
        w = {id(it): scanner.waktu_label(it) for it in items}
        return sorted(items, key=lambda it: (-w[id(it)], nama(it))) if urut == "label-baru" \
            else sorted(items, key=lambda it: (w[id(it)], nama(it)))
    if urut in ("gambar-baru", "gambar-lama"):
        def mtime(it):
            try:
                return it["img"].stat().st_mtime
            except OSError:
                return 0.0
        w = {id(it): mtime(it) for it in items}
        return sorted(items, key=lambda it: (-w[id(it)], nama(it))) if urut == "gambar-baru" \
            else sorted(items, key=lambda it: (w[id(it)], nama(it)))
    if urut == "objek-banyak":
        return sorted(items, key=lambda it: (-len(it["shapes"]), nama(it)))
    if urut == "objek-sedikit":
        return sorted(items, key=lambda it: (len(it["shapes"]), nama(it)))
    return sorted(items, key=nama)


def _filter(items: list[dict], flt: str, kelas, tanpa=(), mode="atau", *,
            tugasku=frozenset()) -> list[dict]:
    if flt == "tugasku":
        # Gambar yang ditugaskan kepadaku. Bukan sekadar kenyamanan: di projek
        # 10.000 gambar, jatah seseorang bisa 200, dan tanpa saringan ini ia
        # menggulir sembilan ribu delapan ratus gambar milik orang lain untuk
        # menemukan pekerjaannya sendiri.
        items = [i for i in items if id(i) in tugasku]
    elif flt == "issue":
        items = [i for i in items if i["issues"] and i["shapes"]]
    elif flt == "bg":
        # Gambar yang SENGAJA ditandai tanpa objek — sampel negatif. Ia berbeda
        # dari "belum dilabeli": yang ini sudah selesai diperiksa, dan porsinya
        # di dataset menentukan seberapa sering model salah menebak latar
        # sebagai objek. Tanpa saringan sendiri, ia tidak bisa dihitung maupun
        # ditinjau ulang.
        items = [i for i in items if scanner.severity(i) == "bg"]
    elif flt == "sudah":
        # Latar IKUT. Menandai gambar tanpa objek adalah keputusan yang sudah
        # diambil, bukan pekerjaan yang belum dikerjakan — dan ia satu-satunya
        # isi sampel negatif, yang di sini justru paling perlu dihitung.
        #
        # Empat tempat lain sudah menghitungnya begitu sejak awal: kartu
        # projek, sidebar, papan anotasi, dan halaman tugas. Grid yang
        # sendirian mengecualikannya membuat satu projek menyebut dua angka
        # "sudah dikerjakan" yang berbeda di dua halaman.
        items = [i for i in items if scanner.severity(i) != "stop"]
    elif flt == "unlab":
        # severity 'stop', bukan sekadar "tanpa objek": gambar yang sudah
        # ditandai latar memang tanpa objek tapi sudah selesai diperiksa, dan
        # angka di chip "Belum dilabeli" juga menghitung 'stop'. Kalau di sini
        # dipakai "tanpa objek", jumlah di chip tidak sama dengan isi grid.
        items = [i for i in items if scanner.severity(i) == "stop"]
    if kelas or tanpa:
        # Beberapa kelas sekaligus, dengan arti "punya SALAH SATU dari ini" —
        # sama seperti filter Classes di Roboflow. Arti "punya SEMUANYA" jarang
        # dibutuhkan dan mudah disalahpahami, jadi tidak dipakai.
        #
        # Keadaan tanpa-kelas ikut di dalam pilihan yang SAMA, bukan saringan
        # terpisah: "botol atau latar" adalah satu pertanyaan, dan memisahkannya
        # ke dua kotak membuat orang harus menebak apakah keduanya digabung
        # dengan DAN atau ATAU.
        pilih = set(kelas)
        sev_pilih = {TANPA_KELAS[t][1] for t in tanpa if t in TANPA_KELAS}

        def cocok(it):
            ada = {str(s["label"]) for s in it["shapes"]}
            sev = scanner.severity(it)
            if mode == "dan":
                # SEMUA kelas yang dicentang harus ada pada gambar yang sama.
                #
                # Latar dan Belum dilabeli tidak ikut di mode ini, dan tidak
                # ditawarkan di antarmukanya. Gambar berobjek menurut
                # definisinya bukan latar, jadi "punya botol DAN latar" selalu
                # nol — dan menawarkan pilihan yang pasti nol itu sendiri sudah
                # cacat, betapa pun benarnya angka nol itu.
                return pilih <= ada
            return bool(pilih & ada) or sev in sev_pilih

        items = [i for i in items if cocok(i)]
    return items


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, f: str = "all",
                # Alias dipakai supaya URL-nya tetap pendek (?s=&q=) tanpa nama
                # `s` dan `q` masuk ke ruang nama fungsi ini — di bawah ada
                # `for s in it["shapes"]` yang akan MENIMPA parameternya, dan
                # akibatnya bukan urutan yang salah melainkan TypeError saat ada
                # gambar berlabel.
                urut_q: str = Query(URUT_BAWAAN, alias="s"),
                cari_q: str = Query("", alias="q"),
                # Boleh muncul berkali-kali: ?c=botol&c=kaleng. Satu nilai tetap
                # bekerja seperti sebelumnya, jadi tautan lama tidak rusak.
                kelas_q: list[str] = Query([], alias="c"),
                # Keadaan tanpa-kelas, dipilih dari dropdown yang sama. Dipisah
                # dari `c` supaya tidak mungkin bentrok dengan kelas yang
                # kebetulan bernama "latar" atau "unlab".
                tanpa_q: list[str] = Query([], alias="x"),
                # Aturan penggabungan centang: "atau" (bawaan, seperti Roboflow)
                # atau "dan" (semuanya harus ada dalam satu gambar).
                mode_q: str = Query("atau", alias="m"),
                # Projek yang mau dibuka, dipakai tautan di sidebar.
                # Tanpa ini, menekan "Dataset" di sidebar projek A
                # menampilkan projek B yang kebetulan masih terbuka.
                # Tag dan nama unggahan yang dipilih. Keduanya keterangan yang
                # dipasang orang, dan satu-satunya cara menemukan kembali
                # sekelompok gambar yang tidak punya ciri lain yang bisa dicari.
                tag_q: list[str] = Query([], alias="tg"),
                batch_q: str = Query("", alias="bt"),
                # Split asal (train/valid/test) pada ekspor bersplit, dan
                # asal-usul gambar (foto asli, hasil augmentasi, hasil
                # penyeimbangan) pada dataset yang diunggah sudah ber-aug-bal.
                # Keduanya di satu menu karena keduanya menjawab pertanyaan yang
                # sama: bagian mana dari dataset ini yang sedang saya lihat.
                split_q: list[str] = Query([], alias="sp"),
                turunan_q: list[str] = Query([], alias="tu"),
                # Paginasi. Sebelum ini seluruh hasil saringan dirender sekali
                # jalan: projek produksi terbesar 11.319 gambar berarti 11.319
                # kartu dan 11.319 <img> dalam satu HTML. loading="lazy"
                # menahan unduhan gambarnya, tetapi tidak menahan HTML-nya, dan
                # bukan itu yang membuat halamannya berat.
                per_q: int = Query(PER_BAWAAN, alias="per"),
                hal_q: int = Query(1, alias="hal"),
                # Nomor gambar pertama yang sedang terlihat, dikirim HANYA oleh
                # pengatur "per halaman". Dengan ini mengubah 100 jadi 50 tidak
                # melempar orang ke halaman 1: ia mendarat di halaman yang
                # memuat gambar yang sama, dan perpindahannya bisa diramalkan.
                dari_q: int = Query(0, alias="dari"),
                ds: str = "",
                sess: Session = Depends(current_session),
                settings: Settings = Depends(get_settings)):
    if ds:
        # `ds` datang dari URL, jadi ia diselesaikan lewat projek.temukan yang
        # memegang aturannya: projek sendiri, atau projek orang lain yang
        # mengundang akun ini. Selain itu None, dan tidak ada yang terbuka.
        d = svc_projek.temukan(settings.uploads_root, sess.user, ds)
        if d is None:
            # Diam-diam menampilkan projek lain yang kebetulan masih terbuka
            # adalah jawaban yang paling menyesatkan: halamannya terbuka,
            # isinya bukan yang diminta, dan tidak ada yang menyebutkan itu.
            return RedirectResponse("/pilih", status_code=303)
        if str(sess.src or "") != str(d):
            await asyncio.to_thread(sess.load, d)
        else:
            await asyncio.to_thread(sess.segarkan)

    # Belum memilih dataset -> tampilkan pemilih, bukan grid kosong.
    if sess.src is None:
        return templates.TemplateResponse(request, "pick.html",
                                          picker_context(request, sess, settings))

    with sess.lock:
        seluruh = list(sess.items)

    # Halaman ini adalah "Dataset", dan datasetnya HANYA gambar yang sudah
    # dinyatakan masuk lewat Tambahkan ke dataset. Tanpa saringan ini, gambar
    # yang baru diunggah — belum dilabeli, belum ditugaskan, belum diperiksa
    # siapa pun — langsung tampil di sini seolah sudah jadi, padahal alurnya
    # justru: unggah, kerjakan di Anotasi, baru masuk dataset.
    #
    # Ekspor, splitting, dan versi sudah memakai saringan yang sama sejak
    # awal. Yang tertinggal cuma halamannya, dan itu membuat halaman ini
    # menyebut angka yang berbeda dari yang benar-benar diekspor.
    items, hitung_ds = await asyncio.to_thread(
        tugas.saring_dataset, seluruh, sess.src,
        settings.uploads_root)
    n_luar = hitung_ds["n_semua"] - hitung_ds["n_dataset"]

    kelas_hitung: dict[str, int] = {}
    for it in items:
        for s in it["shapes"]:
            k = str(s["label"])
            kelas_hitung[k] = kelas_hitung.get(k, 0) + 1

    sev = [scanner.severity(i) for i in items]

    mode = "dan" if mode_q == "dan" else "atau"
    urut = urut_q if urut_q in URUT else URUT_BAWAAN
    cari = (cari_q or "").strip()
    # Kelas yang tidak dikenal SENGAJA dipertahankan, bukan dibuang. Membuangnya
    # membuat saringan diam-diam tidak berlaku dan grid menampilkan semuanya —
    # terlihat seperti saringan yang bekerja padahal tidak. Dibiarkan apa adanya,
    # hasilnya nol, dan penunjuk "0 dari N gambar tampil" yang menjelaskannya.
    kelas = list(dict.fromkeys(k for k in kelas_q if k))
    tanpa = [t for t in dict.fromkeys(tanpa_q) if t in TANPA_KELAS]
    # Di mode "semuanya", keadaan tanpa-kelas dibuang seluruhnya — termasuk dari
    # daftar chip — supaya URL lama atau hasil suntingan tangan tidak
    # menghasilkan saringan yang tidak bisa dibuat lewat antarmukanya sendiri.
    if mode_q == "dan":
        tanpa = []
    # Penugasan dibaca sekali per permintaan. Kunci per gambar dihitung dari
    # akar projek, sama seperti di berkas tag, supaya satu gambar tidak punya
    # dua nama di dua berkas pendamping.
    from ..services import tag as svc_tag
    from ..services import tugas as svc_tugas
    tdata = svc_tugas.baca_projek(sess.src, settings.uploads_root)
    pelabel_dari = {}
    tugasku_id = set()
    # Jatah di SELURUH projek, bukan cuma yang sudah masuk dataset. Chip
    # "Tugasku" menghitung dalam lingkup halaman ini — dan itu benar — tetapi
    # seorang pelabel yang membukanya untuk mencari pekerjaannya selalu
    # melihat angka yang lebih kecil daripada jatahnya, dan pada projek yang
    # baru dibagi melihat nol. Selisihnya disebutkan, dengan jalan ke tempat
    # pekerjaan itu benar-benar ada.
    n_jatah_projek = sum(
        1 for x in tdata["tugas"].values() if x.get("pelabel") == sess.user
        for _ in (x.get("gambar") or []))
    if not tdata["warisan"]:
        for it in items:
            k = svc_tag.kunci_gambar(sess.src, it["img"])
            siapa = svc_tugas.pelabel_gambar(tdata, k)
            if siapa:
                pelabel_dari[id(it)] = siapa
                if siapa == sess.user:
                    tugasku_id.add(id(it))

    tdata_tag = svc_tag.baca(sess.src)
    tag_dari = {}
    if tdata_tag["gambar"]:
        for it in items:
            r = svc_tag.untuk(tdata_tag, svc_tag.kunci_gambar(sess.src, it["img"]))
            if r["tag"] or r["batch"]:
                tag_dari[id(it)] = r

    tampil = _filter(items, f, kelas, tanpa, mode, tugasku=tugasku_id)

    # Saringan tag dan unggahan dipasang SESUDAH saringan lain, dan sengaja
    # bersifat "punya salah satu": "sesi pagi atau lampu redup" adalah satu
    # pertanyaan, dan menuntut keduanya sekaligus hampir tidak pernah yang
    # dimaksud saat menandai gambar.
    tag_pilih = [x for x in dict.fromkeys(tag_q) if x]
    if tag_pilih:
        pilih = set(tag_pilih)
        tampil = [it for it in tampil
                  if pilih & set((tag_dari.get(id(it)) or {}).get("tag") or [])]
    if batch_q:
        tampil = [it for it in tampil
                  if (tag_dari.get(id(it)) or {}).get("batch") == batch_q]

    # Split dan asal-usul gambar. Keduanya bersifat "punya salah satu" seperti
    # tag: "train atau valid" satu pertanyaan, dan menuntut keduanya sekaligus
    # mustahil karena satu gambar cuma punya satu split.
    split_pilih = [x for x in dict.fromkeys(split_q) if x]
    if split_pilih:
        pilih = set(split_pilih)
        tampil = [it for it in tampil if (it.get("split") or "") in pilih]
    turunan_pilih = [x for x in dict.fromkeys(turunan_q)
                     if x in ("asli", "aug", "bal")]
    if turunan_pilih:
        pilih = set(turunan_pilih)
        tampil = [it for it in tampil
                  if scanner.jenis_turunan(it["img"].name) in pilih]
    if cari:
        pola = cari.lower()
        tampil = [it for it in tampil if pola in it["img"].name.lower()]
    tampil = _urutkan(tampil, urut)

    # Potong jadi satu halaman. Dihitung SESUDAH seluruh saringan dan urutan,
    # supaya "halaman 2" berarti halaman kedua dari yang sedang dilihat, bukan
    # dari dataset penuh.
    per = per_q if per_q in PER_PILIHAN else PER_BAWAAN
    n_tampil = len(tampil)
    n_hal = max(1, -(-n_tampil // per))          # pembulatan ke atas
    hal = (dari_q - 1) // per + 1 if dari_q > 0 else hal_q
    # Nomor halaman di luar rentang DIJEPIT, bukan dijawab dengan halaman
    # kosong: datanya ada, nomornya yang salah, dan "tidak ada yang cocok" di
    # situ adalah jawaban yang keliru.
    hal = min(max(hal, 1), n_hal)
    mulai = (hal - 1) * per
    halaman = tampil[mulai:mulai + per]

    # Sidebar projek. Hanya untuk dataset yang ada di ruang kerja seseorang:
    # dataset yang dibuka langsung dari path server tidak punya halaman Unggah,
    # Anotasi, maupun Versi, dan menampilkan menunya berarti menawarkan empat
    # tautan yang semuanya berujung ke daftar projek.
    pr = None
    if svc_projek.pemilik_dari(settings.uploads_root, sess.src):
        pr = await asyncio.to_thread(svc_projek.konteks, sess.src,
                                     settings.uploads_root, sess.user)

    return templates.TemplateResponse(request, "index.html", {
        "sess": sess,
        "pr": pr,
        "aktif": "dataset",
        # Berapa gambar projek ini yang BELUM masuk dataset. Halamannya
        # menyebutkannya dan menautkan ke Anotasi: grid yang menampilkan 38
        # dari 476 tanpa mengatakan apa-apa terbaca seperti gambarnya hilang.
        "n_luar": n_luar,
        "n_projek": hitung_ds["n_semua"],
        "local": is_local(request),
        "items": halaman,
        "urut": urut,
        "urut_pilihan": URUT,
        "cari": cari,
        "n_tampil": n_tampil,
        # Paginasi. `mulai`/`akhir` sudah 1-berbasis supaya templatnya tidak
        # perlu menambah satu di dua tempat dan lupa di salah satunya.
        "per": per,
        "per_pilihan": PER_PILIHAN,
        "per_bawaan": PER_BAWAAN,
        "hal": hal,
        "n_hal": n_hal,
        "mulai": mulai + 1 if halaman else 0,
        "akhir": mulai + len(halaman),
        "severity": scanner.severity,
        "flt": f,
        "kelas": kelas,
        "tanpa": tanpa,
        # Nama tampil dari seluruh yang tercentang, dihitung di sini supaya
        # templatnya tidak perlu menggabungkan dua daftar yang bentuknya beda.
        "pilihan_nama": [TANPA_KELAS[t][0] for t in tanpa] + list(kelas),
        "mode": mode,
        "tanpa_nama": {k: v[0] for k, v in TANPA_KELAS.items()},
        "total": len(items),
        # Empat keadaan yang saling lepas dan jumlahnya pas `total`; itu yang
        # dipakai bilah kemajuan. n_sudah SENGAJA tumpang tindih dengan n_warn
        # dan n_bg — chip "Sudah dilabeli" memang memuat yang perlu dicek dan
        # yang ditandai latar — jadi ia tidak bisa dipakai sebagai potongan
        # bilah.
        "n_ok": sum(1 for s in sev if s == "ok"),
        "n_warn": sum(1 for s in sev if s == "warn"),
        "n_stop": sum(1 for s in sev if s == "stop"),
        "n_bg": sum(1 for s in sev if s == "bg"),
        "n_sudah": sum(1 for s in sev if s in ("ok", "warn", "bg")),
        "n_obj": sum(len(i["shapes"]) for i in items),
        # Penugasan: siapa pemilik tiap gambar, dan berapa jatahku. Kosong di
        # projek yang belum pernah dibagi, dan chip-nya pun tidak muncul.
        "tag_hitung": svc_tag.hitung(tdata_tag),
        "tag_pilih": tag_pilih,
        "batch_pilih": batch_q,
        # Menu Split hanya digambar kalau ada yang bisa dipilih. Dataset labelme
        # biasa tidak punya split sama sekali, dan dataset yang belum pernah
        # diaugmentasi tidak punya turunan; menu kosong di sana cuma satu
        # tombol lagi untuk dilewati.
        "split_hitung": dict(sorted(
            Counter(it["split"] for it in items if it.get("split")).items())),
        "turunan_hitung": dict(sorted(
            Counter(scanner.jenis_turunan(it["img"].name)
                    for it in items).items())),
        "split_pilih": split_pilih,
        "turunan_pilih": turunan_pilih,
        # `pelabel_dari` sendiri tidak lagi diserahkan: kartu tidak mencetak
        # cap pelabel lagi. Perhitungannya TETAP dipakai di bawah untuk
        # `ada_tugas`, yang menentukan muncul tidaknya saringan "Tugasku".
        "n_tugasku": len(tugasku_id),
        "n_jatah_projek": n_jatah_projek,
        "ada_tugas": bool(pelabel_dari) or bool(n_jatah_projek),
        "pemilik_projek": tdata["pemilik"],
        "kelas_hitung": dict(sorted(kelas_hitung.items())),
        # Kenapa dataset ini tidak bisa ditambahi gambar. Dulu kalimat ini
        # menempel pada chip "Tambah gambar" yang dimatikan; chipnya sudah
        # tidak ada (memasukkan gambar dikerjakan lewat "Unggah data" di
        # sidebar), tetapi kalimatnya tetap perlu — justru untuk dataset yang
        # TIDAK punya sidebar, karena di sana tidak ada pintu unggah sama
        # sekali dan tanpa keterangan itu terbaca seperti fitur yang hilang.
        "tolak_tambah": tambah.boleh_ditambahi(
            sess.src, settings.uploads_root / safe_slug(sess.user)),
    })


@router.get("/view", response_class=HTMLResponse)
async def view(request: Request, path: str = "",
               sess: Session = Depends(current_session),
               settings: Settings = Depends(get_settings)):
    # Sama alasannya dengan halaman kanvas: yang ditampilkan harus keadaan
    # sekarang, bukan keadaan saat projek ini pertama dibuka sesi ini.
    await asyncio.to_thread(sess.segarkan)
    it = sess.find(path)
    if not it:
        return templates.TemplateResponse(
            request, "notfound.html", {"sess": sess},
            status_code=404)
    with sess.lock:
        items = sess.items
        i = items.index(it)
        prev_it = items[i - 1] if i > 0 else None
        next_it = items[i + 1] if i < len(items) - 1 else None
        posisi = (i + 1, len(items))

    hitung: dict[str, int] = {}
    for s in it["shapes"]:
        k = str(s["label"])
        hitung[k] = hitung.get(k, 0) + 1

    from ..services import tag as svc_tag
    from ..services import tugas as svc_tugas

    tdata = svc_tag.baca(sess.src)
    kunci = svc_tag.kunci_gambar(sess.src, it["img"])
    # Siapa yang ditugaskan melabeli gambar ini. Dulu ini dicetak sebagai cap
    # di sudut gambar tiap kartu grid, dan di sana ia mengganggu: kartu jadi
    # ramai justru di bagian yang dipakai orang memeriksa fotonya. Tempatnya
    # di sini, sebaris dengan keterangan lain tentang satu gambar.
    tugas_data = svc_tugas.baca_projek(sess.src, settings.uploads_root)
    return templates.TemplateResponse(request, "view.html", {
        "sess": sess, "local": is_local(request), "it": it,
        "sev": scanner.severity(it), "prev_it": prev_it, "next_it": next_it,
        "posisi": posisi, "hitung": dict(sorted(hitung.items())),
        "tag": svc_tag.untuk(tdata, kunci),
        "pelabel": ("" if tugas_data["warisan"]
                    else svc_tugas.pelabel_gambar(tugas_data, kunci)),
        # Menandai gambar itu MENULIS keterangan tentangnya, jadi ia tunduk
        # pada aturan yang sama dengan menyunting labelnya.
        "boleh_tag": not svc_tugas.tolak_tulis(sess.src, sess.user, it["img"]),
    })


@router.get("/thumb")
async def thumb(path: str = "", s: int = 320,
                sess: Session = Depends(current_session)):
    it = sess.find(path)
    if not it:
        return Response(status_code=404)
    side = min(max(s, THUMB_MIN), THUMB_MAX)
    tp = await asyncio.to_thread(render.thumb_path, sess, it, side)
    if not tp:
        return Response(status_code=404)
    return Response(tp.read_bytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=60"})


@router.post("/markbg")
async def mark_bg(path: str = "", sess: Session = Depends(current_session_api)):
    return _set_bg(sess, path, True)


@router.post("/unmarkbg")
async def unmark_bg(path: str = "", sess: Session = Depends(current_session_api)):
    return _set_bg(sess, path, False)


def _set_bg(sess: Session, path: str, on: bool):
    it = sess.find(path)
    if not it:
        return {"ok": False, "error": "berkas tidak dikenal di dataset ini"}
    # Menandai latar itu MENULIS anotasi (berkas dengan shapes kosong), jadi ia
    # tunduk pada aturan yang sama dengan menyimpan bentuk.
    tolak = tugas.tolak_tulis(sess.src, sess.user, it["img"])
    if tolak:
        return {"ok": False, "error": tolak}
    try:
        with sess.lock:
            if on:
                annotations.mark_background(it)
                msg = "ditandai sebagai latar"
            else:
                annotations.unmark_background(it)
                msg = "tanda latar dilepas"
            sess.drop_thumbs_for(it)
    except Menolak as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": str(e)[:90]}
    # Sesi lain memegang salinan isi projek ini; tanpa penanda, papan kemajuan
    # mereka membeku di angka sebelum perubahan ini.
    from ..session import tandai_berubah
    tandai_berubah(sess.src, it["img"])
    return {"ok": True, "msg": msg}


@router.post("/open", dependencies=[Depends(require_local)])
async def open_in_anylabeling(path: str = "",
                              sess: Session = Depends(current_session_api),
                              settings: Settings = Depends(get_settings)):
    """Jalankan AnyLabeling di mesin server — hanya untuk akses lokal."""
    it = sess.find(path)
    if not it:
        return {"ok": False, "error": "berkas tidak dikenal di dataset ini"}
    try:
        anylabeling.launch(sess, it["img"])
    except FileNotFoundError:
        return {"ok": False,
                "error": f"perintah '{settings.anylabeling}' tidak ditemukan"}
    except OSError as e:
        return {"ok": False, "error": str(e)[:90]}
    msg = ("folder dibuka — pakai A / D untuk pindah gambar"
           if settings.open_mode == "dir" else it["img"].name)
    return {"ok": True, "msg": msg}
