# Pembelahan train / valid / test

Dokumen rujukan untuk `app/services/split.py`. Ditulis supaya kalau nanti ada
crash, angka yang janggal, atau hasil yang tidak masuk akal, ada tempat untuk
memeriksa **apa yang seharusnya terjadi** dan **kenapa dibuat begitu**.

Semua angka di sini hasil pengukuran pada dua dataset sungguhan
(21 Agustus 2026), bukan perkiraan:

| | isi | ukuran foto |
|---|---|---|
| `paragon` | 476 foto ponsel | 4080×2296 |
| `botol-kaleng-tetra-mlp-cup-1` | 11.319 ekspor Roboflow | 640×640 |

---

## 1. Masalah yang dipecahkan

Membelah per-gambar terdengar adil, tapi menghasilkan angka validasi palsu.
Foto yang diambil dua detik berselang — meja sama, cahaya sama, produk fisik
sama, tangan bergeser sedikit — praktis gambar yang sama. Kalau satu masuk
train dan satu masuk valid, model dinilai memakai sesuatu yang sudah dia
hafal.

Pembelahan lama (`export.bagi_split` lewat `kunci_asal()`) hanya membuang
sufiks augmentasi Roboflow sesudah `.rf.<hash>`. Diukur pada paragon:

```
476 gambar  ->  476 grup      grup terbesar: 1 gambar
```

Nol perlindungan. Penyakit yang sama membuat model sirsak-v13 melaporkan
mAP 0,9499 tapi 0/7 di ruang detektor sungguhan.

---

## 2. Alur lengkap

`rencanakan(items, rasio, kunci=, batal=, pakai_dhash=)` tidak menyentuh satu
berkas pun. Ia mengembalikan **rencana**: peta `nama berkas -> split` beserta
diagnosanya.

| Fase | Nama di layar | Persen |
|---|---|---|
| `pindai` | Mengumpulkan gambar | 0 |
| `sesi` | Mengelompokkan per sesi pemotretan | 5 |
| `bagi` | Membagikan ke train/valid/test | 10 |
| `dhash` | Membaca isi gambar | 10 → 74 |
| `kalibrasi` | Mengukur ambang kemiripan dataset ini | 76 |
| `bersih` | Memindahkan kembaran keluar dari valid/test | 80 → 95 |
| `nilai` | Menilai kemandirian valid dan test | 96 |
| `selesai` | Selesai | 97 → 100 |

`dhash` sengaja mengisi porsi terbesar bilah: **56 ms per gambar terukur**,
jadi ia yang menghabiskan waktu (≈19 jam untuk sejuta gambar, satu inti).

---

## 3. Lapis pertama — sesi pemotretan

### Kunci sesi

`kunci_sesi(nama, gran)` hanya mengenali **penanda waktu** di nama berkas:

```
IMG_20260630_085207_jpg.rf.8e4u4mvM.jpg  ->  ts:20260630_0852
20240506_170457.jpg                      ->  ts:20240506_1704
2024-05-06 17.04.57.jpg                  ->  ts:20240506_1704
foto-tanpa-waktu.jpg                     ->  file:foto-tanpa-waktu
```

`stem_asli()` lebih dulu membuang hash Roboflow, sufiks augmentasi
(`_aug`, `_bal`, `_p5*`, `_sw*`), dan penanda salinan (`- Copy`, `(2)`),
supaya `foo`, `foo_aug1`, dan `foo - Copy` jatuh ke sesi yang sama.

**Kenapa hanya penanda waktu.** Versi awal juga mengelompokkan lewat prefiks
nama (mis. semua `img_00044` jadi sesi `img`). Prefiks semacam itu konvensi
penamaan SELURUH dataset, bukan sesi pemotretan — pada dataset sirsak cara
itu menyatukan 85% gambar jadi satu grup dan pembagian rasio jadi mustahil.

Berkas tanpa penanda waktu jadi sesinya sendiri. Itu **bukan** kelalaian:
kemiripan nyatanya tetap tertangkap lapis kedua, yang tidak peduli namanya
apa.

### Granularitas dipilih sendiri

`pilih_granularitas(nama, rasio)` mencoba `hari` → `jam` → `menit`, dan
mengambil **yang paling kasar yang grup terbesarnya masih muat di split
terkecil yang diminta**.

Kenapa syaratnya begitu, bukan "sekurangnya sekian sesi": sesi tidak boleh
dipecah, jadi satu grup yang lebih besar daripada kuota valid pasti
melompatinya. Dinyatakan begitu, syaratnya mengikuti rasio yang benar-benar
diminta.

Kedua skrip acuan (`resplit-sesi-v14.py`, `resplit-sesi-paragon.py`) mematok
granularitas, dan dua-duanya jadi salah di dataset yang lain:

- per-jam mustahil untuk paragon — seluruhnya diambil dalam satu jam
- per-menit memecah sesi yang sebenarnya satu, pada dataset berbulan-bulan

Terlalu kasar juga merugikan: 2.000 gambar dalam 200 sesi jadi **76:12:12**
kalau dikunci per-hari, tapi **80:10:10 persis** kalau per-jam.

---

## 4. Pembagian ke split

`_bagikan()` memecah dua kolam lebih dulu, lalu membagi keduanya dengan rasio
yang sama:

- **berobjek** — grup yang memuat sekurangnya satu bentuk
- **negatif** — grup yang seluruh gambarnya tanpa objek

**Kenapa dipisah.** Pengisian valid/test memilih grup yang komposisi kelasnya
paling mewakili. Pada dataset yang baru sebagian dilabeli, "paling mewakili"
sama artinya dengan "yang ada labelnya" — dan grup berlabel habis terserap.
Diukur pada paragon (476 gambar, 87 beranotasi): **seluruh 95 objek mendarat
di valid+test dan train dapat NOL**. Model yang dilatih dari situ tidak
belajar apa pun.

Memisahkan kolam sekaligus menjaga porsi contoh negatif tetap seragam di
ketiga split — dan itu memang harus dijaga, karena label kosong di sini
disengaja, bukan pekerjaan yang belum selesai.

### Fungsi biaya

`_bagikan_kolam()` menempatkan grup **dari yang terbesar**, tiap grup ke split
dengan `penalti()` terkecil. Satu angka menilai dua hal sekaligus:

1. ukuran tiap split terhadap kuotanya
2. komposisi kelasnya terhadap komposisi kolam (split tanpa objek dihitung
   menyimpang penuh, +2,0)

**Kenapa satu angka.** Versi sebelumnya mengisi valid/test lebih dulu demi
keseimbangan kelas, dan pada kolam bergrup sedikit itu membuat train kebagian
paling sedikit: 15,8% objek untuk train, 56,8% untuk test.

Grup besar ditempatkan lebih dulu karena yang kecil masih bisa menambal sisa,
sedangkan grup besar yang datang belakangan hanya bisa merusak.

### Jaminan tidak kosong

Selama jumlah grup ≥ jumlah split yang diminta, tiap split wajib kebagian
sekurangnya satu grup. Valid atau test yang kosong tidak bisa dipakai sama
sekali, sedangkan rasio yang meleset masih bisa dibaca — dan memang
diperingatkan.

---

## 5. Lapis kedua — kemiripan isi

Lapis pertama tidak berguna kalau namanya acak (UUID, hash, sumber campur
aduk). Karena itu isi gambarnya yang diperiksa.

### dHash 256 bit

`dhash()` memakai petak **16×16** → sidik jari 256 bit (32 byte).

Diuji dengan kembaran BUATAN yang pasti benar (kompresi ulang q70/q50, ubah
ukuran lewat 640, kecerahan +12%, potong 2%, geser 4 px) lawan pasangan
bukan-kembaran yang pasti benar:

```
8x8   (64 bit)   kembaran maks 14  ·  bukan-kembaran min 11   -> TUMPANG TINDIH
16x16 (256 bit)  kembaran maks 65  ·  bukan-kembaran min 77   -> celah 12 bit
```

Pada 64 bit **tidak ada satu pun ambang** yang menangkap seluruh kembaran
tanpa ikut menyeret foto yang berbeda; ambang 5 yang dipakai sebelumnya
melewatkan 14% kembaran di botol-kaleng. Menyetel ambang di alat ukur sekasar
itu cuma memilih jenis kesalahan mana yang mau ditanggung.

### Ambang dikalibrasi per-dataset

`kalibrasi_ambang(items, sidik, sesi)` mengukur dua distribusi dataset itu
sendiri:

- **wajib tertangkap** — ~60 fotonya diproses ulang dengan perubahan yang
  pasti tidak mengubah isinya (`_varian()`); ambil persentil 99
- **wajib lolos** — pasangan dari **sesi berbeda di dalam dataset ini**;
  ambil persentil 1 (bukan minimum, karena sebagian memang kembaran)

Kalau terpisah → ambang = titik tengahnya. Kalau bertumpuk → ambang = p99
kembaran, dan diperingatkan.

```
paragon       kembaran p99 39  ·  beda-sesi p1 97   -> ambang 67
botol-kaleng  kembaran p99 47  ·  beda-sesi p1 51   -> ambang 47
```

> **Jangan ganti ini jadi angka tetap.** Percobaan pertama memakai pembanding
> lintas-dataset (paragon lawan botol-kaleng) — terlalu mudah, karena produk
> berbeda di ruangan berbeda memang berjauhan. Ambangnya tersetel 72, dan
> **valid botol-kaleng terkuras dari 11.319 gambar jadi 36**.

`AMBANG_KEMBAR = 56` hanya cadangan kalau kalibrasi tidak bisa jalan.
`AMBANG_MAKS = 96` batas atas; kalau kalibrasi meminta lebih, ambangnya
dipatok dan diperingatkan.

`_varian()` **wajib memuat potong dan geser**. Tanpa keduanya, kalibrasi pada
dataset yang sudah 640×640 nyaris tidak mengukur apa-apa — langkah "ubah
ukuran lewat 640" di sana tidak mengubah apa pun.

### Pencarian kembaran

`cari_kembar(acuan, uji, ambang)` membandingkan **menyeluruh**, dalam petak
yang muat memori (`SEL_PER_PETAK = 2.000.000` sel ≈ 64 MB).

Versi sebelumnya memakai indeks banyak-pita: hash dipotong jadi `ambang+1`
pita, dan dua hash berjarak ≤ ambang pasti sama persis di sekurangnya satu
pita. Jauh lebih cepat, **tapi mustahil di sini** — ambang 47 berarti 48 pita
sedangkan hash 256 bit cuma punya 32 byte.

Melepasnya boleh karena bukan di situ waktunya habis: membaca gambar 56 ms
per berkas, perbandingan menyeluruh untuk jumlah yang sama selesai dalam
hitungan menit.

### Dipindahkan, bukan dibuang

Gambar valid/test yang punya kembaran di train **dipindahkan ke train**.

Kriteria keluar dari valid sama persis untuk dibuang maupun dipindahkan, jadi
valid-nya identik — bedanya cuma datanya terbuang atau tidak. Riset acuan
(Barz & Denzler 2020, *Purging CIFAR of Near-Duplicates*) membuang dari test
karena train set CIFAR tidak boleh diubah; tujuan mereka memperbaiki tolok
ukur yang sudah dipakai ribuan makalah. Kita mengendalikan kedua sisi.

**Diulang sampai tenang**, bukan sampai jatah putaran habis. Gambar yang baru
pindah ikut jadi acuan dan bisa menarik gambar lain yang tadinya aman. Versi
pertama berhenti di 5 putaran, dan pada paragon itu memotong sebelum selesai:
**4 kembaran tetap tertinggal di valid**. `MAKS_PUTARAN = 60` semata penjaga.

### Yang sengaja TIDAK dilakukan

Kembaran **tidak** digabungkan jadi satu grup. Penggabungan bersifat transitif
— A~B dan B~C menyatukan A, B, C walau A dan C tidak mirip. Diukur pada
dataset sirsak, cara itu meruntuhkan 66–85% gambar jadi satu grup raksasa
sehingga rasio mustahil dipenuhi. Memindahkan mencapai tujuan yang sama tanpa
efek samping itu.

---

## 6. Menilai mutu hasilnya

Kebocoran nol **tidak** berarti angka validasinya bisa dipercaya. Nol hanya
berarti tidak ada yang melewati ambang; gambar valid masih bisa duduk tepat di
atasnya. `nilai_kemandirian()` menghitung dua hal:

**1. Kemandirian** — jarak khas gambar valid ke train TERDEKAT, dibagi jarak
khas satu gambar train ke **sesi train lain**.

```
~1,0   valid semandiri gambar train mana pun terhadap sesi train lainnya
<0,8   valid masih lebih mirip train  ->  peringatan
```

> **Patokannya harus setara.** Versi pertama membagi dengan median pasangan
> acak. Minimum atas ratusan gambar memang selalu jauh di bawah median
> pasangan acak, jadi skornya pasti di bawah 1 dan makin kecil setiap train
> membesar — artefak, bukan ukuran. Ketahuan saat diuji: dataset yang seluruh
> fotonya nyaris sama justru bernilai LEBIH TINGGI (0,78) daripada yang
> beragam (0,58). Sesi yang sama juga dibuang dari patokan, karena gambar
> sesesi memang nyaris kembar.

**2. Jumlah sesi di valid/test.** Lebih mudah dibaca daripada rasio mana pun:
valid dari 2 sesi cuma menguji 2 kondisi pemotretan — meja, cahaya, dan sudut
yang itu-itu saja — berapa pun jumlah gambarnya.

Hasil terukur:

```
paragon       kemandirian 0,95  ·   2 dari   10 sesi
botol-kaleng  kemandirian 1,47  ·  89 dari 4572 sesi
```

Pembelahan paragon ternyata **baik**; yang kurang keragaman datanya. Tanpa
angka ini, peringatannya menunjuk ke tempat yang salah.

---

## 7. Bagaimana rencananya dipakai

Rencana disimpan di `Session.rencana_split` (memori, hilang saat server
dinyalakan ulang). `export.bagi_split(items, rasio, rencana)` memakainya
dengan urutan:

1. **rencana** kalau ada → dipakai apa adanya, untuk kelima format sekaligus
2. **split bawaan dataset** kalau ada → dihormati
3. **pembelahan cepat** berbasis hash nama berkas → cadangan

Gambar yang belum ada saat rencana dibuat tetap mendarat lewat aturan
cadangan.

### Rute

| Rute | Guna |
|---|---|
| `POST /api/split/jalankan?split=80,10,10` | jalankan; menggantung sampai selesai |
| `GET /api/split/kemajuan` | fase + persen, ditanya berkala oleh browser |
| `POST /api/split/batal` | hentikan yang sedang jalan |
| `POST /api/split/lupakan` | buang rencana, kembali ke pembelahan cepat |

Petanya (`peta`) **tidak** ikut dikirim ke browser — ia bisa memuat sejuta
nama berkas. `ringkas_rencana()` di `app/routers/datasets.py` membuangnya.

Kalau belum dijalankan, ringkasan ekspor **mengaku sendiri** bahwa isi gambar
belum diperiksa. Itu disengaja: tanpa itu, satu-satunya penanda bahwa
ekspornya bocor adalah tombol yang kebetulan tidak ditekan.

---

## 8. Kalau ada yang janggal

### Rasio jauh meleset dari yang diminta

Wajar kalau sesinya sedikit — sesi tidak boleh dipecah. Periksa `n_sesi` dan
`grup_terbesar_pct`. Peringatannya menyebut sebabnya, dan membedakan
"sesi tidak bisa dipecah" dari "kembaran dipindahkan".

### valid atau test terkuras habis

Berarti hampir semua gambarnya punya kembaran di train. Periksa `dipindah`
dan `ambang`. Kalau `ambang` jauh lebih tinggi daripada `kalibrasi.kembaran_p99`,
kalibrasinya yang perlu diperiksa — bukan datanya.

### Train dapat nol objek

Regresi kolam negatif. Pastikan `_bagikan()` masih memisahkan **berobjek** dan
**negatif**. Uji penjaganya:
`test_gambar_berlabel_tidak_habis_terserap_ke_valid_dan_test`.

### "Pemindahan kembaran belum tenang"

Seharusnya tidak muncul. Kalau muncul, ada siklus pemindahan — laporkan
beserta `ambang` dan `n_sesi`.

### Pembelahannya lambat

Yang lambat pasti fase `dhash` (56 ms/gambar). Perbandingan dan kalibrasi
hitungan menit. Kalau fase `bersih` yang lama, ambangnya kemungkinan
kelewat longgar sehingga tiap putaran memindahkan banyak.

### Hasil ekspor tidak sesuai rencana

Rencana hilang saat server restart (`Session` di memori). Cek
`GET /api/ekspor/ringkasan` — kalau `rencana` bernilai `null`, jalankan ulang.

### Memori membengkak

`SEL_PER_PETAK` menjaga petak di kisaran 64 MB. Yang tidak dijaga adalah
gambar mentah: foto 4080×2296 memakan **28 MB per citra**. Jangan menahan
banyak citra sekaligus. `dhash()` aman karena citranya lepas sendiri saat
fungsi kembali; `kalibrasi_ambang()` berputar, jadi ia melepasnya eksplisit
(`del im`).

> Pernah terjadi saat mengukur: 220 foto × 7 salinan penuh ditahan sekaligus
> = 21 GB, dan prosesnya mati tanpa pesan sama sekali. Kalau ada proses yang
> hilang begitu saja, curigai ini lebih dulu.

---

## 9. Yang TIDAK dijamin

1. **Objek fisik yang sama dari sudut berbeda.** dHash menangkap foto yang
   mirip, bukan "botol yang sama dipotret dari arah lain". Kalau nama
   berkasmu memuat penanda unit produk, kunci itu bisa dipakai dan jaminannya
   naik satu tingkat.

2. **Kalibrasi memakai contoh, bukan sensus.** 60 foto dan transformasi
   tiruan. Itu operating point terukur, bukan bukti.

3. **Augmentasi sebelum split.** Di luar jangkauan aplikasi ini; ada di
   pipeline latih. `resplit-sesi-v14.py` sudah benar — ia melewati berkas
   `_aug/_bal/_p5/_sw` dan mengaugmentasi *setelah* split, hanya pada train.

4. **Domain gap.** Bukan kebocoran, tapi membuat angkanya sama tidak
   bergunanya. Recht dkk. (2019, *Do ImageNet Classifiers Generalize to
   ImageNet?*) menunjukkan bahkan test set yang dipisahkan dengan benar pun
   tetap optimistis dibanding data yang dikumpulkan terpisah. Test set harus
   mirip **ruang detektor RVM**, bukan mirip **meja labeling**.

---

## 10. Uji yang menjaga semua ini

`tests/test_split.py` — 22 uji. Yang paling penting:

| Uji | Menjaga |
|---|---|
| `test_sesi_tidak_pernah_terpisah_dua_split` | janji utamanya |
| `test_pencarian_berpetak_sama_dengan_sekali_hitung` | petak tidak mengubah hasil |
| `test_kalibrasi_memakai_kedua_distribusi_dataset_itu_sendiri` | ambang duduk di antara keduanya |
| `test_gambar_berlabel_tidak_habis_terserap_ke_valid_dan_test` | regresi kolam negatif |
| `test_kemandirian_menurun_saat_valid_lebih_mirip_train` | skornya tidak terbalik |
| `test_kembaran_dipindahkan_ke_train_bukan_dibuang` | tidak ada gambar hilang |
| `test_rute_pembelahan_dipakai_ekspor_dan_bisa_dilupakan` | rencana benar-benar dipakai ZIP |

Tampilannya dijaga blok `potret` di `tests/e2e_kanvas.py`:
`tests/e2e_kanvas.py potret`.
