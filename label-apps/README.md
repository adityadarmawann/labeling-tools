# Labeling Tools

Papan periksa anotasi berbasis web untuk dataset **AnyLabeling / labelme / YOLO-seg**,
dipakai bersama satu tim.

Menjawab satu kebutuhan: melihat banyak anotasi sekaligus, menemukan yang salah,
lalu memperbaikinya — tanpa setiap orang harus membuka folder dataset di
aplikasi desktop masing-masing.

## Fitur

- **Grid thumbnail** dengan mask ter-overlay, warna per kelas konsisten
- **Saringan**: semua / perlu dicek / belum dilabeli / per kelas
- **Strip kesehatan dataset** di atas — satu tick per gambar, langsung terlihat
  seberapa banyak yang bermasalah
- **Login per akun.** Tiap akun punya dataset dan cache thumbnail sendiri, jadi
  satu orang mengganti folder tidak mengubah tampilan orang lain
- **Unggah folder atau `.zip` dari laptop** — tarik-lepas satu folder dataset,
  atau langsung berkas `.zip` ekspor Roboflow yang dibongkar sendiri di server.
  Subfolder `images/` dan `labels/` **ikut terjaga**, dan `data.yaml` ikut
  terkirim sehingga nama kelasnya tidak hilang jadi angka. Kalau sebuah dataset
  YOLO diunggah tanpa nama kelas, aplikasi mengatakannya sebelum kamu mulai
  bekerja
- **Tandai latar** (setara *Mark Null* di Roboflow): gambar tanpa objek ikut ke
  dataset sebagai contoh negatif, bukan dibuang
- **Pindai ulang** tanpa restart
- **Ekspor Roboflow dibuka dari akarnya** — `train/`, `valid/`, dan `test/`
  dipindai sekaligus, tiap gambar diberi tanda asal splitnya, dan nama kelas
  diambil dari `data.yaml`
- **Menyunting dataset YOLO menulis balik ke `labels/*.txt`**, ditambah berkas
  `.json` di sebelah gambar sebagai cadangan untuk `group_id` dan catatan teks
  yang tidak muat di format YOLO. Baris yang tidak disunting ditulis kembali
  **persis seperti aslinya**, termasuk jumlah desimalnya — diuji pada 900 berkas
  label sungguhan dari ekspor Roboflow, byte-nya identik semua
- **Ekspor YOLO-seg siap latih** — struktur dan `data.yaml` sama seperti ekspor
  Roboflow, diverifikasi termuat oleh ultralytics 8.4 sebagai dataset
  segmentasi. Split asli dataset dipertahankan, dan saat membagi sendiri
  augmentasi dari satu foto tidak pernah terpisah antar split
- **Deteksi satu gambar penuh lewat prompt teks** — sebut nama kelasnya
  (`botol, kaleng, tetra`), semua yang cocok langsung jadi objek. Lewat
  YOLO-World XL (keluaran kotak) atau SAM 3 (keluaran poligon); bobotnya
  diunduh sekali, dan ukurannya disebutkan sebelum tombolnya ditekan. Satu
  Ctrl+Z membatalkan seluruh deteksi
- **Lima format ekspor**, masing-masing dengan tata letak yang benar-benar
  bisa dibaca alatnya. YOLO memakai `<split>/images/` + `<split>/labels/`;
  COCO, Pascal VOC, dan CreateML menaruh gambar SEJAJAR berkas anotasinya karena
  `file_name` dan `filename` di kedua format itu diselesaikan relatif terhadap
  letak berkas anotasi. `category_id` COCO dihitung sekali untuk seluruh
  dataset mengikuti urutan `data.yaml`, jadi id yang sama berarti kelas yang
  sama di `train/`, `valid/`, dan `test/`
- **Tombol "Perbaiki di AnyLabeling"** — hanya aktif untuk akses dari mesin
  server, karena jendela Qt-nya muncul di layar server

**Melabeli langsung di browser**, mengikuti AnyLabeling:

- **MobileSAM**: klik objeknya, poligonnya muncul sebagai pratinjau, perbaiki
  dengan +Point / −Point atau kotak, sahkan dengan `F`. Pipeline-nya sudah
  diverifikasi identik dengan desktop — lihat [PARITAS.md](PARITAS.md)
- **Splitting train/valid/test anti-bocor**: di-split per sesi pemotretan lalu
  isi tiap gambar diperiksa, supaya foto yang sama tidak muncul di train dan
  valid sekaligus. Ambang kemiripannya diukur dari dataset yang bersangkutan,
  bukan angka tetap — algoritma dan angka ukurnya di
  [SPLITTING.md](SPLITTING.md)
- **Enam tipe bentuk**: polygon, rectangle (bbox), circle, line, linestrip,
  point — masing-masing dengan cara mengakhiri seperti aslinya
- **Menyunting terasa ringan**: klik di sisi poligon menyisipkan titik,
  Shift+klik di titik menghapusnya, klik ulang membatalkan pilihan, seret klik
  kanan menyalin atau memindahkan
- `Ctrl+C`/`Ctrl+V` antar gambar, `G`/`U` grup, `Alt` mematikan tarik-magnet,
  urutan objek bisa digeser, kecerahan/kontras per gambar, dan riwayat urungkan
  40 langkah
- **Simpan otomatis** begitu tiap objek punya kelas — dan semuanya tetap bisa
  disunting sesudahnya

## Pasang

```bash
cd label-apps
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Jalankan

Buat akun dulu — server menolak jalan tanpa akun sama sekali:

```bash
.venv/bin/python run.py --adduser paul
.venv/bin/python run.py --adduser anggi
.venv/bin/python run.py --list-users
```

Password diminta lewat prompt, tidak lewat argumen, supaya tidak tertinggal di
riwayat shell.

Pakai sendiri di laptop:

```bash
.venv/bin/python run.py --src ~/dataset/sponge
```

Dipakai tim lewat jaringan:

```bash
.venv/bin/python run.py --host 0.0.0.0 \
  --datasets-root ~/computer-vision/datasets
```

Lalu buka `http://127.0.0.1:8042`, atau `http://<ip-server>:8042` dari laptop
anggota tim. Kalau ada firewall, port-nya perlu dibuka lebih dulu
(`sudo ufw allow 8042/tcp`).

Dokumentasi API otomatis ada di `/docs`.

## Struktur

```
label-apps/
├── run.py                  entrypoint: argumen CLI -> environment -> uvicorn
├── start.sh                nyalakan mode dev atau prod (memuat env/*.env)
├── deploy.sh               naikkan kode ke prod: uji, tukar proses, buktikan hidup
├── sinkron-dev.sh          hadirkan projek prod di dev (tautan atau salinan)
├── .env.example            semua setelan yang bisa diatur
├── env/                    dev.env, prod.env
├── DEV.md                  dev & prod: menjalankan, menyinkronkan, deploy
├── SPLITTING.md            algoritma pembagian train/valid/test anti-bocor
├── PARITAS.md              kesetaraan dengan AnyLabeling desktop
└── app/
    ├── main.py             perakitan aplikasi (tipis)
    ├── config.py           setelan, dibaca dari environment
    ├── security.py         password pbkdf2, nama berkas/akun yang aman
    ├── session.py          keadaan per akun + penyimpan sesi
    ├── deps.py             dependency: sesi, penjagaan localhost
    ├── log.py              logger yang tetap terlihat di mode prod
    ├── templating.py       instance Jinja2 + filter
    ├── routers/            satu berkas per kelompok URL
    │   ├── auth.py         /login  /logout  /daftar
    │   ├── admin.py        /akun + API kelola akun
    │   ├── datasets.py     /pilih  /setsrc  /rescan  /pickdir
    │   ├── projek.py       /api/projek/* : kartu dataset dan operasinya
    │   ├── review.py       /  /view  /thumb  /markbg  /unmarkbg  /open
    │   ├── annotate.py     kanvas: simpan bentuk, navigasi gambar
    │   └── uploads.py      /upload  /unzip  /useupload
    ├── services/           logika inti, tanpa HTTP
    │   ├── scanner.py      pindai dataset, nilai anotasi
    │   ├── projek.py       ganti nama, gandakan, gabung, sampah
    │   ├── split.py        pembagian train/valid/test anti-bocor
    │   ├── export.py       susun ZIP dalam lima format
    │   ├── impor.py        salin dataset dari folder lain
    │   ├── arsip.py        bongkar .zip unggahan dengan aman
    │   ├── autolabel.py    SAM: MobileSAM, EfficientSAM, SAM 2
    │   ├── render.py       overlay mask + cache thumbnail
    │   ├── riwayat.py      catat folder yang pernah dibuka
    │   ├── annotations.py  tulis/hapus anotasi latar
    │   └── anylabeling.py  jalankan AnyLabeling & dialog folder
    ├── templates/          Jinja2
    └── static/             app.css, app.js
```

Aturan yang dipegang: `services/` tidak mengimpor FastAPI sama sekali, sehingga
bisa dipakai dari skrip atau notebook tanpa menjalankan server. `routers/` hanya
menerjemahkan HTTP ke `services/`.

## Dua mode: dev dan prod

Dua folder, satu `.git`. Prod berjalan dari checkout `main`; pengembangan
berjalan dari worktree di cabang `dev`.

```
rvm/
├── labeling-tools/       cabang main  ->  prod 8042, dipakai tim
└── labeling-tools-dev/   cabang dev   ->  dev  8043, tempat ngoding
```

```bash
cd ../labeling-tools-dev/label-apps && ./start.sh    # dev, port 8043
./start.sh prod                                      # prod, port 8042
```

Keduanya boleh hidup bersamaan. Setelannya di [env/dev.env](env/dev.env) dan
[env/prod.env](env/prod.env); akun, dataset, unggahan, dan thumbnail terpisah
sepenuhnya, dan [tests/test_mode.py](tests/test_mode.py) gagal kalau salah
satunya sampai sama.

Foldernya dipisah karena templat dan CSS dibaca dari disk tiap permintaan:
selama keduanya berbagi satu folder, menyunting `app.css` langsung mengubah
apa yang dilihat tim di 8042.

```bash
./sinkron-dev.sh --tautan   # hadirkan projek prod di dev
./deploy.sh --dari-dev      # gabungkan dev ke main, uji, lalu naikkan (dari folder prod)
```

Selengkapnya — menyiapkan worktree dari nol, akses dari laptop, dua cara
menghadirkan projek prod, langkah deploy, dan daftar kejanggalan beserta
sebabnya — ada di [DEV.md](DEV.md).

## Paritas dengan AnyLabeling

Aplikasi ini pengembangan dari AnyLabeling, dan source AnyLabeling dipakai
sebagai **spesifikasi** — bukan tangkapan layar, bukan dugaan. Daftar apa yang
sudah sama, apa yang belum, dan apa yang sengaja dibedakan beserta alasannya
ada di [PARITAS.md](PARITAS.md).

Audit menyeluruh atas seluruh basis kode AnyLabeling 0.4.36 — antarmuka, kanvas,
penyimpanan, konfigurasi, auto-labeling, dan ekspor, semuanya dengan rujukan
`berkas:baris` — ada di [AUDIT-ANYLABELING.md](AUDIT-ANYLABELING.md). Dokumen itu
adalah **rujukan utama** saat menambah fitur: bacalah dulu daripada menyimpulkan
perilaku AnyLabeling dari ingatan. Bagian H-nya memuat daftar penyimpangan
aplikasi ini beserta prioritasnya.

## Pengujian

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

244 tes: gerbang login, isolasi antar akun, sterilisasi nama berkas unggahan,
penjagaan endpoint desktop, tandai latar, kecocokan angka chip dengan isi grid,
paritas MobileSAM, bentuk keluaran ekspor, bulat-balik keenam tipe bentuk,
pemindaian ekspor bersplit, penulisan balik label YOLO, pembagian split yang
tidak memecah augmentasi satu foto, serta keamanan bongkar arsip (zip-slip,
zip bomb, dan zip di dalam zip), serta kesetaraan keluaran dengan Roboflow
(cincin poligon, pengurungan koordinat, dan urutan indeks kelas),
serta pemisahan setelan dev dan prod.

Kanvas diuji terpisah dengan Chrome sungguhan, karena perilaku mouse tidak bisa
dipercaya kalau hanya fungsinya yang dipanggil langsung — urutan penanganan klik
justru bagian yang paling mudah salah:

```bash
.venv/bin/python tests/e2e_kanvas.py      # butuh google-chrome terpasang
```

Menyalakan server sendiri di 127.0.0.1, menjalankan Chrome headless lewat CDP,
lalu mengirim peristiwa mouse asli — 35 pemeriksaan: klik di sisi poligon,
Shift+klik di titik, klik ulang untuk membatalkan pilihan, menyeret objek,
autosave yang benar-benar menulis ke disk, pembuatan keenam tipe bentuk,
salin-tempel, grup, seret klik kanan beserta menu dua-pilihannya, kecerahan,
pengurutan ulang panel Objects, dan penjaga salah ketik nama kelas.

Satu aturan yang dijaga otomatis: **pengujian tidak boleh menulis apa pun ke
dalam folder aplikasi.** Fixture `folder_aplikasi_tak_berubah` di
[tests/conftest.py](tests/conftest.py) memotret folder ini sebelum dan sesudah
setiap tes, lalu menggagalkannya kalau ada berkas yang muncul, hilang, atau
berubah. Seluruh berkas akun, dataset, dan unggahan uji dibuat di `tmp_path`
milik pytest.

Aturan itu ada karena pernah kejadian: berkas akun uji tertinggal di folder
aplikasi, lalu `start.sh` melihatnya dan menjalankan server sungguhan yang
terbuka ke jaringan dengan password yang tercatat di log pengujian. Penjaga
ini yang membuatnya tidak bisa terulang — dan sengaja tidak menuntut
`users.json` tidak ada, karena di pemakaian nyata berkas akun memang tinggal
di folder ini.

## Setelan

Semua lewat environment berawalan `LABELAPP_`, atau lewat argumen `run.py` yang
menimpanya. Lihat [.env.example](.env.example) untuk daftar lengkap.
Yang paling sering dipakai:

| Argumen | Environment | Arti |
|---|---|---|
| `--host` | — | `127.0.0.1` (default) atau `0.0.0.0` untuk dibuka ke jaringan |
| `--port` | — | default `8042` |
| `--users` | `LABELAPP_USERS_FILE` | berkas akun |
| `--datasets-root` | `LABELAPP_DATASETS_ROOT` | folder induk; subfoldernya jadi daftar pilihan |
| `--uploads-root` | `LABELAPP_UPLOADS_ROOT` | tempat unggahan, dipisah per akun |
| `--src` | `LABELAPP_DEFAULT_SRC` | folder yang langsung dibuka saat akun masuk |
| `--max-upload-mb` | `LABELAPP_MAX_UPLOAD_MB` | batas per berkas, default 80 |
| `--open-mode` | `LABELAPP_OPEN_MODE` | `file` atau `dir` untuk AnyLabeling |
| `--lock-labels` | `LABELAPP_LOCK_LABELS` | tolak label di luar daftar |

## Tata letak dataset yang dikenali

**labelme / AnyLabeling** — gambar dan anotasi bersebelahan:

```
dataset/
  foto-01.jpg
  foto-01.json
  foto-02.jpg          <- tanpa .json, terbaca "belum dilabeli"
```

**YOLO** — dideteksi otomatis kalau ada subfolder `images/` dan `labels/`:

```
dataset/
  images/foto-01.jpg
  labels/foto-01.txt
  classes.txt          <- opsional, untuk nama kelas
```

Format label YOLO bbox (5 kolom) maupun poligon keduanya terbaca.

## Yang diperiksa

`services/scanner.py` melaporkan hal yang hampir pasti salah, bukan selera:

- `belum dilabeli` — tidak ada berkas anotasi
- `latar (tanpa objek)` — anotasi ada tapi kosong, disengaja
- `berkas anotasi rusak` — JSON tidak bisa dibaca
- `label kosong` — shape tanpa nama kelas
- `hanya N titik` — poligon dengan titik terlalu sedikit
- `mask sangat kecil` / `mask memenuhi frame` — luas di bawah 0,2% atau di atas 92% frame
- `titik di luar gambar` — koordinat melewati batas gambar

Sengaja konservatif: temuan palsu membuat orang berhenti mempercayai papan periksa.

## Catatan keamanan

- **Tidak ada TLS.** Password dikirim lewat HTTP polos. Untuk dipakai lewat
  internet, taruh di belakang reverse proxy ber-HTTPS, atau batasi aksesnya
  lewat firewall / VPN.
- **Kotak "path folder di server" memang bebas.** Akun mana pun bisa
  mengarahkannya ke folder apa pun yang bisa dibaca oleh user yang menjalankan
  server. Kalau ada anggota tim yang tidak sepenuhnya dipercaya, hapus panel itu
  dari `templates/pick.html` dan andalkan `--datasets-root`.
- **`users.json` tidak ikut ke repo** (ada di `.gitignore`) dan disimpan dengan
  izin `600`.
- Sesi disimpan di memori: proses restart berarti semua orang login ulang.
  Disengaja — tidak ada sesi menggantung yang tidak bisa dicabut.
- Endpoint yang memunculkan jendela di layar server (`/open`, `/pickdir`)
  menolak permintaan dari luar localhost. Penilaiannya memakai alamat soket,
  bukan header `X-Forwarded-For` yang bisa dipalsukan — jadi kalau dipasang di
  belakang reverse proxy pada mesin yang sama, semua permintaan akan terlihat
  lokal.

## Belum ada

Hal-hal yang jelas dibutuhkan kalau dipakai satu tim penuh, dan belum dikerjakan:

- **Pembagian tugas dan penguncian** — sekarang dua orang bisa membuka dataset
  yang sama dan saling menimpa anotasi latar
- **Jejak audit** — tidak ada catatan siapa mengubah apa dan kapan
- **Peran** — semua akun punya hak yang sama
- **Ganti bahasa** — AnyLabeling punya menu Bahasa karena pemakainya
  internasional. Di sini tidak dikerjakan atas keputusan sadar: seluruh tim
  berbahasa Indonesia, sementara biayanya sekitar 400 teks yang harus
  diterjemahkan dan setiap perubahan teks ke depan jadi dua kali kerja.
  Tema tetap ada (terang/gelap/ikut sistem)
- **Pembagian tugas dan penguncian antar-anggota** — lihat butir pertama
