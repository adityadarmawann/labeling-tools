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
- **Unggah dari laptop** — tarik-lepas gambar, masuk ke folder milik akun itu
- **Tandai latar** (setara *Mark Null* di Roboflow): gambar tanpa objek ikut ke
  dataset sebagai contoh negatif, bukan dibuang
- **Pindai ulang** tanpa restart
- **Tombol "Perbaiki di AnyLabeling"** — hanya aktif untuk akses dari mesin
  server, karena jendela Qt-nya muncul di layar server

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
├── requirements.txt
├── .env.example            semua setelan yang bisa diatur
└── app/
    ├── main.py             perakitan aplikasi (tipis)
    ├── config.py           setelan, dibaca dari environment
    ├── security.py         password pbkdf2, nama berkas/akun yang aman
    ├── session.py          keadaan per akun + penyimpan sesi
    ├── deps.py             dependency: sesi, penjagaan localhost
    ├── templating.py       instance Jinja2 + filter
    ├── routers/            satu berkas per kelompok URL
    │   ├── auth.py         /login  /logout
    │   ├── datasets.py     /pilih  /setsrc  /rescan  /pickdir
    │   ├── review.py       /  /view  /thumb  /markbg  /unmarkbg  /open
    │   └── uploads.py      /upload  /useupload
    ├── services/           logika inti, tanpa HTTP
    │   ├── scanner.py      pindai dataset, nilai anotasi
    │   ├── render.py       overlay mask + cache thumbnail
    │   ├── annotations.py  tulis/hapus anotasi latar
    │   └── anylabeling.py  jalankan AnyLabeling & dialog folder
    ├── templates/          Jinja2
    └── static/             app.css, app.js
```

Aturan yang dipegang: `services/` tidak mengimpor FastAPI sama sekali, sehingga
bisa dipakai dari skrip atau notebook tanpa menjalankan server. `routers/` hanya
menerjemahkan HTTP ke `services/`.

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
- **Editor anotasi di browser** — membuat poligon baru masih di AnyLabeling desktop
