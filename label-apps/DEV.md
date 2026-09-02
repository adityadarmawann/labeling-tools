# Pengembangan: dev dan prod

Dokumen ini menjelaskan cara aplikasi ini dijalankan dua kali di satu mesin:
satu untuk tim (`prod`), satu untuk mengoding (`dev`). Ditulis supaya kalau
ada yang janggal, penyebabnya bisa dicari tanpa membaca kodenya lebih dulu.

Untuk algoritma splitting, lihat [SPLITTING.md](SPLITTING.md).

## Ringkasnya

| | dev | prod |
|---|---|---|
| Folder | `rvm/labeling-tools-dev` | `rvm/labeling-tools` |
| Cabang git | `dev` | `main` |
| Port | 8043 | 8042 |
| Berkas akun | `users.dev.json` | `users.json` |
| Setelan | `env/dev.env` | `env/prod.env` |
| Muat ulang saat kode berubah | ya | tidak |
| Masuk otomatis tanpa password | ya, hanya dari mesin itu sendiri | tidak |
| Batas unggahan & arsip | sama dengan prod | 80 MB / 4096 MB |

Keduanya boleh hidup bersamaan; menyalakan dev tidak mengganggu tim.

## Kenapa foldernya dipisah

Bukan sekadar kerapian. **Templat dan CSS dibaca dari disk tiap permintaan.**
Kalau dev dan prod berbagi satu folder, menyunting `app.css` atau `view.html`
langsung mengubah apa yang dilihat tim di 8042, termasuk saat baru setengah
jadi.

Terukur pada server ini, ketika keduanya masih satu folder:

```
mtime app.css        : 6a904646
prod (8042) melayani : app.css?v=6a904646
$ touch app/static/app.css
mtime baru           : 6a9509e0
prod (8042) melayani : app.css?v=6a9509e0     <- ikut berubah seketika
```

Kode Python tidak begitu: prod memuatnya sekali saat menyala, jadi perubahan
`.py` baru hidup setelah `deploy.sh` mengganti prosesnya. Perbedaan itu penting
saat memutuskan apakah sebuah perubahan benar-benar perlu restart.

## Peta folder

```
rvm/
├── labeling-tools/            cabang main  ->  prod 8042
│   └── label-apps/
│       ├── users.json         akun tim
│       ├── logs/prod.log      keluaran prod (dibuat deploy.sh)
│       └── run/prod.commit    commit yang sedang dijalankan prod
└── labeling-tools-dev/        cabang dev   ->  dev 8043
    └── label-apps/
        ├── users.dev.json     akun dev
        ├── .venv              hard link dari venv prod
        ├── yolo26n-seg.pt     hard link dari prod
        └── dev-data/
            ├── datasets/
            ├── thumb/
            └── unggahan/
                ├── darma      tautan ATAU salinan dari prod
                └── devuser/
```

Satu `.git` dipakai berdua (`git worktree`), jadi riwayat dan cabangnya sama.
`git worktree list` menunjukkan keduanya.

Yang **tidak** ikut git dan harus disediakan sendiri di tiap folder:
`.venv`, `users*.json`, `dev-data/`, `logs/`, `run/`, `yolo26n-seg.pt`.

## Menyalakan

```bash
cd ~/computer-vision/smartbin/rvm/labeling-tools-dev/label-apps
./start.sh                 # dev, port 8043, muat ulang otomatis
```

Prod dinyalakan dari folder yang lain, dan biasanya lewat `deploy.sh` bukan
langsung:

```bash
cd ~/computer-vision/smartbin/rvm/labeling-tools/label-apps
./start.sh prod            # port 8042
```

Jangan menjalankan `uvicorn app.main:app` langsung. `start.sh` yang memuat
`env/*.env`; tanpa itu seluruh `LABELAPP_*` hilang dan aplikasi memakai path
bawaan, sehingga projek tampak lenyap padahal berkasnya utuh.

Menghentikan:

```bash
kill $(pgrep -f 'run\.py .*--port 8043')
```

## Akun

Akun dev terpisah dari akun prod, dan itu disengaja: akun uji tidak boleh bisa
dipakai masuk ke produksi. Namanya pun sengaja dibedakan — `darma-dev`, bukan
`darma` — supaya sekali lihat jelas kamu sedang berada di simulasi, bukan di
pekerjaan tim.

```bash
.venv/bin/python run.py --users users.dev.json --adduser darma-dev
```

Akun dev sebaiknya admin, supaya halaman kelola akun ikut bisa dicoba.

`LABELAPP_DEV_AUTOLOGIN=devuser` di `env/dev.env` membuat permintaan **dari
mesin itu sendiri** masuk tanpa password. Penjaganya `is_local()` di
`app/deps.py`, dan permintaan lewat reverse proxy pun ditolak. Karena itu ia
tetap aman walau dev mendengar di `0.0.0.0`: dari laptop, halaman login tetap
muncul.

## Membukanya dari laptop

Dev mendengar di `0.0.0.0` seperti prod, tetapi ufw harus mengizinkan portnya:

```bash
sudo ufw allow from 103.182.240.26 to any port 8043 proto tcp
```

Terbukti pada server ini:

```
127.0.0.1:8043        -> 200, masuk otomatis sebagai devuser
103.182.240.28:8043   -> 303, dialihkan ke /login
```

## Projek yang sama seperti prod

Tata letak hanya bisa dinilai dengan foto sungguhan, bukan gambar uji 80x60.
[sinkron-dev.sh](sinkron-dev.sh) menghadirkan projek prod di dev dengan dua
cara:

```bash
./sinkron-dev.sh --lihat                                  # sedang di mode mana
./sinkron-dev.sh darma --ke darma-dev --projek paragon    # satu projek saja
./sinkron-dev.sh darma --ke darma-dev                     # semua projeknya
./sinkron-dev.sh --tautan                                 # berbagi folder prod
./sinkron-dev.sh --lepas                                  # lepas tautan
```

`--ke` dipakai karena nama akun dev berbeda dari akun prod. `--projek`
menyalin satu projek saja dan tidak menghapus projek lain yang sudah ada di
dev; tanpa `--projek`, dev dibuat persis seperti prod.

| | SALIN | TAUTAN |
|---|---|---|
| Ikut berubah saat prod bertambah | tidak, jalankan lagi | ya, langsung |
| Tambahan pemakaian disk | ~2 MB untuk 3 GB projek | nol |
| Buang / gabung / ganti nama di dev | hanya kena dev | **kena berkas prod sungguhan** |

**TAUTAN** untuk pekerjaan tata letak dan fitur. **SALIN** saat menyentuh
`services/projek.py`, ekspor, atau apa pun yang memindahkan dan menghapus
berkas. Yang tidak punya jalan pulang adalah `gabung`; `ke_sampah` masih
memindah ke `_sampah`, bukan menghapus.

Berpindah ke TAUTAN menyingkirkan salinan lama ke `<akun>.salinan-<tanggal>`
alih-alih menghapusnya.

### Kenapa SALIN tidak memakan ruang

Yang dibuat **hard link**, bukan salinan isi. Itu aman karena setiap penulisan
di aplikasi ini lewat berkas sementara lalu diganti namanya
(`annotations.tulis_aman`, `annotate.py`, `scanner.py`). Mengganti nama
memutus tautannya: berkas baru lahir di sisi dev, berkas prod tidak tersentuh.

Terukur:

```
inode prod : 16164601 (2 tautan)
inode dev  : 16164601        <- sama
tulis ulang berkasnya di dev, lalu periksa prod:
md5 sebelum: b6055bc4e6713b69a4a200cfab03c929
md5 sesudah: b6055bc4e6713b69a4a200cfab03c929   <- tidak berubah
inode dev  : 26476731        <- tautan putus, dev punya berkasnya sendiri
```

### Tautan harus di tingkat folder akun

Menautkan **satu projek** ke dalam folder dev gagal separuh: projeknya muncul
di daftar, tetapi sampulnya 404 dan seluruh menu kelolanya ditolak dengan
"nama itu menunjuk ke luar ruang kerjamu". Sebabnya `_didalam()` me-resolve
kedua sisi, dan projek itu resolve ke luar ruang kerja dev.

Di tingkat **folder akun**, rootnya ikut resolve ke seberang sehingga
penjaganya utuh: sampul 200, dan operasi kelola berjalan.

## Menaikkan kode ke prod

Dijalankan dari folder **prod**, bukan dari worktree dev: yang menentukan apa
yang tayang adalah folder yang dijalankan prod.

```bash
cd ~/computer-vision/smartbin/rvm/labeling-tools/label-apps
./deploy.sh --status     # apa yang sedang jalan, tanpa mengubah apa pun
./deploy.sh --dari-dev   # gabungkan cabang dev ke main, lalu naikkan
./deploy.sh              # naikkan apa yang sudah ada di main
```

Yang dilakukannya berurutan:

1. menolak berjalan kalau ada perubahan yang belum di-commit;
2. menyebutkan commit apa saja yang akan naik, dan mengatakan kalau isinya
   hanya templat/CSS/uji — yang memang sudah tayang tanpa restart;
3. menjalankan pytest, dan berhenti kalau ada yang gagal;
4. meminta konfirmasi, karena restart memutus semua sesi yang sedang berjalan
   dan mematikan splitting atau ekspor yang sedang jalan;
5. mengganti prosesnya, lalu menunggu `/login` menjawab 200 sebelum menyatakan
   berhasil. Kalau tidak menjawab, ia menampilkan log dan perintah untuk
   kembali ke commit sebelumnya.

Commit yang sedang dijalankan prod dicatat di `run/prod.commit`.

## Menyiapkan worktree dev dari nol

Kalau foldernya hilang atau perlu dibuat di mesin lain:

```bash
cd ~/computer-vision/smartbin/rvm/labeling-tools
git worktree add ../labeling-tools-dev -b dev
cd ../labeling-tools-dev/label-apps

cp -al ../../labeling-tools/label-apps/.venv .venv            # hard link, ~0 byte
cp -al ../../labeling-tools/label-apps/yolo26n-seg.pt .
cp -al ../../labeling-tools/models ../models                 # bobot MobileSAM
.venv/bin/python run.py --users users.dev.json --adduser darma-dev
mkdir -p dev-data/{datasets,unggahan,thumb}
./sinkron-dev.sh darma --ke darma-dev --projek paragon
```

`models/` duduk di akar worktree, sejajar `label-apps/`, bukan di dalamnya.
Melewatkannya adalah kekeliruan yang paling mudah terjadi dan paling lambat
ketahuan: seluruh 245 pytest tetap lolos, dan barulah tombol SAM menjawab
"berkas MobileSAM tidak ada".

`cp -al` untuk `.venv` aman: `pip` menulis berkas baru, jadi memasang paket di
dev tidak mengubah venv prod.

## Memastikan dev benar-benar bisa dipakai

pytest membuat lingkungannya sendiri di `tmp_path`, jadi ia tidak pernah
menyentuh worktree dev yang sungguhan. Worktree yang kekurangan `models/` atau
`dev-data/` lolos seluruhnya, dan baru gagal saat tombolnya ditekan orang.

[tests/sapu_dev.py](tests/sapu_dev.py) yang menutup celah itu: ia menembak
server dev yang sedang berjalan, lewat jaringan, dengan akun dan dataset
sungguhan.

```bash
.venv/bin/python tests/sapu_dev.py <sandi-darma-dev>

# Tombol salin di konteks TIDAK aman — lewat alamat IP, HTTP biasa.
# Wajib lewat IP, bukan 127.0.0.1: localhost adalah secure context, dan di
# sana navigator.clipboard ada sehingga bugnya tidak pernah muncul.
.venv/bin/python tests/e2e_salin.py <sandi-darma-dev>
```

43 pemeriksaan: login, daftar projek, sampul, buka dataset, grid dan kelima
saringannya, urut, cari, halaman Lihat, kanvas, thumbnail, tandai latar,
simpan bentuk, SAM dari kotak, deteksi prompt teks, splitting sampai 100%,
kelima format ekspor, pindai ulang, unggah gambar, unggah dan bongkar arsip,
tambah gambar, gabung, gandakan, ganti nama, buang, pulihkan, dan halaman
kelola akun.

Ia MENULIS ke dataset dev lalu mengembalikannya. Jangan diarahkan ke prod.

## Yang dijaga pengujian

[tests/test_mode.py](tests/test_mode.py) gagal kalau:

- dev dan prod berbagi port, berkas akun, atau folder data;
- folder dev bersarang di dalam folder prod;
- `prod.env` memuat `DEV_AUTOLOGIN`;
- ada setelan perilaku (`GOOGLE_DOMAIN`, `DAFTAR_SENDIRI`, `DAFTAR_LANGSUNG`)
  yang disebut di `prod.env` tetapi tidak di `dev.env`, karena bawaannya
  berbeda dan alur yang diuji di dev jadi bukan alur yang berjalan di produksi;
- nilai bawaan argparse menimpa setelan dari env (lihat di bawah).

## Kalau ada yang janggal

**Dev tidak bisa dibuka dari laptop.** Periksa ufw mengizinkan 8043 dari IP
kantor. `LABELAPP_HOST` di `dev.env` harus `0.0.0.0`, bukan `127.0.0.1`.

**Beranda projek kosong padahal prod penuh.** Jalankan `./sinkron-dev.sh
--lihat`; kemungkinan folder akunnya belum ditautkan atau disalin. Bisa juga
akun yang dipakai login di dev berbeda namanya dengan akun prod.

**Setelan di `dev.env` sepertinya tidak berpengaruh.** Dulu benar demikian:
`run.py` punya nilai bawaan argparse untuk `--max-upload-mb`, `--open-mode`,
dan `--anylabeling`, dan nilai bawaan itu ikut ditulis ke environment sehingga
menimpa setelan yang barusan dimuat `start.sh`. `dev.env` menulis 20, aplikasi
memakai 80, dan banner menyebut 80 dengan yakin. Sekarang yang tidak diberikan
di baris perintah tidak ditulis sama sekali. Periksa dengan membaca banner saat
menyala, atau `tr '\0' '\n' < /proc/<pid>/environ | grep LABELAPP_`.

**Suite pytest tiba-tiba lambat sekali.** Penjaga `folder_aplikasi_tak_berubah`
di `tests/conftest.py` memotret seluruh folder aplikasi dua kali untuk setiap
tes. Kalau ada folder besar yang tidak masuk `ABAIKAN`, ia ikut dipindai:
dengan `dev-data` berisi dataset hasil sinkron, satu suite naik dari 43 detik
menjadi lebih dari empat menit. `dev-data`, `logs`, dan `run` sudah diabaikan.

**Tes yang tidak bersalah gagal dengan "tes ini menyentuh folder aplikasi".**
Penjaga yang sama. Penyebab tersering: ada proses lain yang menulis ke folder
itu selagi tes berjalan, misalnya server dev menulis thumbnail, atau kamu
menyentuh berkas di folder aplikasi dari terminal lain.

**`sinkron-dev.sh` menolak menyalin.** Folder akun di dev masih berupa tautan.
`rsync --delete` dengan sumber dan tujuan yang sebenarnya satu folder adalah
cara tercepat kehilangan data, jadi ia berhenti. Lepas dulu dengan `--lepas`.

**Prod tidak menjawab setelah deploy.** `deploy.sh` sudah menampilkan sepuluh
baris terakhir `logs/prod.log` beserta perintah untuk kembali ke commit
sebelumnya. Log lengkapnya ada di berkas itu.
