# Memasang di `anylabel.higo.id`

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
