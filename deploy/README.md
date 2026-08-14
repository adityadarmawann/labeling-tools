# Memasang di belakang domain

Contoh di bawah memakai `anylabel.higo.id`. Untuk domain sendiri yang tidak
menyentuh zona perusahaan, `anylabel-higo.my.id` sama saja — ganti namanya di
semua perintah. Perhatikan `cv.id` **bukan** zona terdaftar (tidak punya NS),
jadi `*.cv.id` tidak bisa dipakai; SLD Indonesia yang tersedia antara lain
`my.id`, `web.id`, `co.id`, `biz.id`.

Tujuannya menggantikan `http://103.182.240.28:8042` dengan
`https://anylabel.higo.id` — tanpa nomor port, dan **dengan TLS**, sehingga
password anggota tim tidak lagi lewat HTTP polos.

Semua perintah di bawah butuh `sudo` dan **kamu yang menjalankannya**.

## 1. DNS

Tambahkan satu A record di panel DNS `higo.id`:

```
anylabel.higo.id.    A    103.182.240.28
```

`higo.id` sekarang menunjuk `103.141.230.130` (server lain) — biarkan, yang
ditambah hanya subdomain-nya.

Tunggu sampai menyebar, lalu periksa dari mana saja:

```bash
getent hosts anylabel.higo.id     # harus menjawab 103.182.240.28
```

Jangan lanjut ke langkah 4 sebelum ini benar — certbot memverifikasi lewat
domain, dan akan gagal kalau DNS-nya belum menunjuk ke sini.

## 2. Firewall

Certbot memverifikasi lewat port 80 dari server Let's Encrypt, jadi port 80
harus terbuka untuk **semua**, bukan hanya IP kantor:

```bash
sudo ufw allow 80/tcp   comment 'nginx - verifikasi certbot'
sudo ufw allow 443/tcp  comment 'anylabel.higo.id'
sudo ufw status | grep -E '80|443|8042|22|2202'
```

Pastikan `22` dan `2202` masih ada di daftar sesudahnya.

Port `8042` tidak perlu lagi terbuka ke jaringan setelah nginx jalan — lihat
langkah 5.

## 3. Nginx

```bash
sudo apt install nginx
sudo cp nginx-anylabel.higo.id.conf /etc/nginx/sites-available/anylabel.higo.id
sudo ln -s /etc/nginx/sites-available/anylabel.higo.id /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Uji sebelum TLS: `curl -I http://anylabel.higo.id` harus menjawab `303` ke
`/login`.

## 4. Sertifikat TLS

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d anylabel.higo.id
```

Certbot menambahkan blok `443` dan pengalihan HTTP→HTTPS ke berkas konfigurasi
tadi, lalu memperbarui sertifikatnya sendiri lewat timer systemd.

Sesudah ini `https://anylabel.higo.id` sudah bisa dibuka, dan password tidak
lagi terkirim polos.

## 5. Tutup port 8042 dari jaringan

Setelah nginx yang meneruskan, aplikasi tidak perlu lagi mendengar di semua
alamat. Jalankan hanya di localhost:

```bash
cd ~/computer-vision/smartbin/rvm/labeling-tools/label-apps
./start.sh --lokal
```

Lalu cabut aturan lama supaya 8042 tidak bisa disentuh langsung dari luar:

```bash
sudo ufw status numbered          # cari nomor aturan 8042
sudo ufw delete <nomor>
```

Dengan begini satu-satunya jalan masuk adalah `https://anylabel.higo.id`.

## 6. Supaya hidup terus

```bash
tmux new -s label
cd ~/computer-vision/smartbin/rvm/labeling-tools/label-apps
./start.sh --lokal
```

Lepas dengan `Ctrl+B` lalu `D`; kembali dengan `tmux attach -t label`.

## Yang berubah setelah memakai domain

**Tombol AnyLabeling dan dialog folder akan mati untuk semua orang, termasuk
kamu.** Itu disengaja. Di belakang nginx, semua permintaan datang dari
`127.0.0.1`, jadi aplikasi tidak bisa lagi membedakan "dari mesin server" lewat
alamat soket. Yang dipakai sebagai penanda adalah kehadiran header proxy
(`X-Forwarded-For`, `X-Real-IP`, dan sejenisnya) — kalau ada, permintaannya
dianggap datang dari luar.

Tanpa aturan itu, siapa pun yang membuka domain bisa menekan "Perbaiki di
AnyLabeling" dan jendela Qt-nya terbuka di monitor fisik paul-higo.

Untuk memakai tombol desktop, buka langsung dari mesin itu lewat
`http://127.0.0.1:8042` — tanpa lewat nginx.

## Kalau gagal

| Gejala | Kemungkinan |
|---|---|
| `certbot` gagal verifikasi | DNS belum menunjuk ke `103.182.240.28`, atau port 80 belum terbuka untuk semua |
| `502 Bad Gateway` | aplikasi tidak jalan — cek `ss -tln \| grep 8042` |
| `413 Request Entity Too Large` | berkas unggahan melebihi `client_max_body_size` di konfigurasi nginx |
| Unggahan besar terputus | naikkan `proxy_read_timeout` |
| Tombol desktop hilang padahal dibuka dari server | memang begitu lewat domain; pakai `http://127.0.0.1:8042` |


---

# Varian: hanya bisa diakses dari WiFi kantor

Kalau aksesnya memang mau dibatasi ke jaringan kantor saja, jangan pakai
langkah 2 dan 4 di atas — keduanya menuntut port 80 terbuka untuk seluruh
dunia karena Let's Encrypt memverifikasi dengan mengetuk dari luar (HTTP-01).

Pakai **DNS-01**: kepemilikan domain dibuktikan lewat record TXT, bukan lewat
HTTP. Tidak ada port yang perlu dibuka ke publik.

## Firewall

```bash
sudo ufw allow from 103.182.240.26 to any port 443 proto tcp comment 'anylabel - kantor'
# port 80 TIDAK dibuka sama sekali
sudo ufw status | grep -E '443|22|2202'
```

Pastikan `22` dan `2202` masih ada sesudahnya.

## Sertifikat tanpa membuka port

Paling praktis kalau DNS domainnya dikelola penyedia yang punya plugin
certbot — Cloudflare gratis dan paling umum:

```bash
sudo apt install certbot python3-certbot-dns-cloudflare

# token API Cloudflare dengan izin Zone:DNS:Edit, simpan berizin 600
sudo install -m 600 /dev/null /etc/letsencrypt/cloudflare.ini
sudo tee /etc/letsencrypt/cloudflare.ini >/dev/null <<'EOF'
dns_cloudflare_api_token = TARUH_TOKEN_DI_SINI
EOF

sudo certbot certonly --dns-cloudflare   --dns-cloudflare-credentials /etc/letsencrypt/cloudflare.ini   -d anylabel-higo.my.id
```

Perpanjangannya otomatis lewat timer systemd, tanpa port terbuka dan tanpa
kamu sentuh lagi.

Kalau DNS-nya di penyedia tanpa plugin, masih bisa manual:

```bash
sudo certbot certonly --manual --preferred-challenges dns -d anylabel-higo.my.id
```

Tapi record TXT-nya harus kamu tambahkan sendiri **setiap kali diperbarui**
(sekitar 60 hari). Mudah terlupa sampai sertifikatnya kedaluwarsa — pakai ini
hanya kalau tidak ada pilihan lain.

## Nginx untuk mode ini

Pakai konfigurasi yang sama, tapi karena certbot dijalankan dengan `certonly`
(bukan `--nginx`), blok TLS-nya ditulis sendiri:

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name anylabel-higo.my.id;

    ssl_certificate     /etc/letsencrypt/live/anylabel-higo.my.id/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/anylabel-higo.my.id/privkey.pem;

    client_max_body_size 100M;
    proxy_request_buffering off;
    proxy_read_timeout 300s;

    location / {
        proxy_pass http://127.0.0.1:8042;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Tidak ada blok `listen 80` sama sekali — tidak dibutuhkan, dan tidak membuka
apa pun.

## Yang perlu diingat

Karena port 443 dibatasi ke `103.182.240.26`, anggota tim **hanya** bisa
mengakses dari jaringan kantor. Dari rumah atau data seluler akan tertolak —
dan itu memang yang diminta.

Kalau IP publik kantor berubah (ISP dinamis), aturan itu patah dan semua orang
kehilangan akses. Periksa berkala dengan `curl -s ifconfig.me` dari laptop
kantor; kalau berubah, hapus aturan lama lewat `sudo ufw status numbered` lalu
`sudo ufw delete <nomor>` dan pasang yang baru.
