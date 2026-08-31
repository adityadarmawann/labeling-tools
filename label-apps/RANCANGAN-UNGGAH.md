# Rancangan halaman "Unggah data"

Rancangan tata letak untuk `app/templates/unggah.html` (baru) dan tambahan gaya
untuk `app/static/app.css`. Hanya struktur dan gaya. Perpindahan tahap, baca
berkas, pratinjau, dan unggahan itu pekerjaan JavaScript yang belum ditulis.

Tiga tahap hidup di **satu halaman**, bukan tiga rute. Alasannya sederhana:
nama batch dan tag yang sudah diketik harus selamat saat orang mundur dari
tahap 2 ke tahap 1, dan pindah halaman menghapusnya. Tahap 2 dan 3 memakai
atribut `hidden`; aturan global `[hidden]{display:none !important}` yang sudah
ada di `app.css` baris 111 mengalahkan `display:grid`/`display:flex` milik
kelas-kelas baru, jadi `el.hidden = false` cukup untuk memunculkannya.

---

## 1. Keputusan tata letak dan alasannya

### Kolom kanan Roboflow dibuang, kotak drop melebar penuh

Roboflow memakai ~330px di kanan untuk tiga kartu: unggah dari ponsel, cari di
Universe, dan Cloud Storage. Ketiganya tidak ada di kita.

Ruang itu **tidak** diisi kartu pengganti. Kotak drop yang melebar penuh
sasaran seretnya jauh lebih besar, dan sasaran seret memang harus besar: orang
menyeret folder dari jendela lain sambil menahan tombol tetikus, dengan
penglihatan terbagi antara dua jendela. Kolom kanan selebar 330px memotong
sasaran itu 25% demi kartu yang dibaca sekali seumur hidup.

Sumber kedua kita, **folder di server**, tidak layak jadi kartu sempit di
kanan juga. Ia butuh kotak isian path selebar mungkin (path server panjang,
lihat `.lanjut-path` dan `#pathbox` yang sekarang), jadi ia duduk **di bawah**
kotak drop, sebaris dengan panel "Format yang didukung", dua-duanya memakai
kelas `.panel` yang sudah ada. Keduanya sama-sama bacaan pendukung: satu
menjawab "boleh unggah apa", satu menjawab "kalau datanya sudah di server".

Ringkasnya, urutan tahap 1 dari atas ke bawah:

```
nama batch + tag        (dua kolom, auto-fit)
kotak drop besar        (lebar penuh)
format didukung | folder di server   (1.3fr : 1fr, menumpuk di bawah 900px)
```

### Penunjuk tahap di dalam header

Header sudah lengket (`position:sticky`). Penunjuk `1 Pilih berkas -> 2 Periksa
-> 3 Unggah` ditaruh di sana, menggantikan `<div style="height:12px">` yang
dipakai `pick.html` sebagai pengganjal. Di tahap 2 dengan 5.000 thumbnail,
penunjuk itu satu-satunya hal yang memberitahu bahwa masih ada tahap ketiga.
Kalau kamu tidak menginginkannya, hapus `<nav class="ug-langkah">` beserta
blok CSS-nya dan kembalikan pengganjal 12px; sisanya tidak bergantung padanya.

### Bilah tahap 2 tipis, kisi menggulir sendiri

Di tahap 2 yang dicari orang thumbnailnya, jadi kotak drop besar diganti bilah
setinggi ~52px yang memuat keterangan seret, "Pilih berkas", "Pilih folder",
dan tombol utama "Simpan dan lanjutkan". Seluruh panggung tetap jadi sasaran
seret, jadi tidak ada kemampuan yang hilang.

Kisinya menggulir di wadahnya sendiri, bukan ikut halaman. Pada 5.000 berkas,
tombol "Simpan dan lanjutkan" yang ikut tergulir akan berada puluhan layar
jauhnya dari thumbnail terakhir, dan orang menggulir balik ke atas untuk
mencarinya. Dengan wadah bergulir sendiri, bilahnya selalu di tempatnya tanpa
perlu `position:sticky` sama sekali.

### Bilah progres tahap 3

Memakai `.prog` + `.pr-kotak` + `.pr-teks` yang sudah ada. Tidak ada komponen
progres baru. Dua jalan pakai, pilih salah satu saat menulis JS-nya:

- **Disarankan:** `Progres.mulai('Mengunggah 231 berkas', {di: elemen})` pada
  `#ug-progres-jalur`. Modul `Progres` di `app.js` baris 486 membangun sendiri
  `.pr-kotak > .prog[data-on] > i` beserta `.pr-teks`, mengurus persentase,
  waktu, keadaan gagal, dan penanda kerja global di pojok layar. Ia menimpa
  `innerHTML` wadahnya, jadi markup statis di dalamnya memang untuk ditimpa.
- **Manual:** setel langsung `#ug-fill.style.width` dan isi `#ug-progres-persen`.
  Markup statis di templat sudah berbentuk persis seperti yang dibangun
  `Progres`, jadi tampilannya sama.

`.prog` tersembunyi sampai diberi `data-on` (`app.css` baris 385), dan itu sudah
terpasang di markup.

---

## 2. Templat Jinja2

Simpan sebagai `app/templates/unggah.html`.

Variabel konteks yang diasumsikan ada, semuanya sudah dipakai `pick.html`:
`sess`, `max_upload_mb`, `max_zip_mb`, `riwayat`. Tambahan opsional: `projek`
(projek tujuan unggahan; kalau tidak ada, baris path di header ikut kosong).

```jinja
{% extends "base.html" %}
{% block title %}Unggah data{% endblock %}

{% block body %}
<header>
  <div class="top">
    <h1>Unggah data</h1>
    {% if projek %}<span class="path mono">{{ projek.nama }}</span>{% endif %}
    {% include "_header.html" %}
  </div>

  {# Penunjuk tahap duduk di header yang lengket. Di tahap 2 dengan ribuan
     thumbnail, ini satu-satunya yang memberitahu masih ada tahap sesudahnya.
     data-on = tahap sekarang, data-selesai = tahap yang sudah dilewati;
     keduanya dipindahkan JavaScript. #}
  <nav class="ug-langkah" id="ug-langkah" aria-label="Tahap unggahan">
    <span class="ug-langkah-item" id="ug-langkah-1" data-on>
      <span class="ug-langkah-no">1</span>Pilih berkas</span>
    <span class="ug-langkah-panah" aria-hidden="true">&rarr;</span>
    <span class="ug-langkah-item" id="ug-langkah-2">
      <span class="ug-langkah-no">2</span>Periksa</span>
    <span class="ug-langkah-panah" aria-hidden="true">&rarr;</span>
    <span class="ug-langkah-item" id="ug-langkah-3">
      <span class="ug-langkah-no">3</span>Unggah</span>
  </nav>
</header>

<main>
<div class="ug-halaman">

  {# ---------------------------------------------------------- nama & tag
     Dipakai bersama tahap 1 dan tahap 2: di Roboflow pun kolom ini tidak
     hilang saat berkasnya sudah di-staging, dan nama batch justru paling
     sering diperbaiki setelah melihat isinya. #}
  <div class="ug-isian">
    <div>
      <label class="form-lbl" for="ug-nama-batch">Nama batch</label>
      <input type="text" id="ug-nama-batch" autocomplete="off"
             placeholder="mis. sesi-pagi-2026-08-31">
    </div>
    <div>
      <label class="form-lbl" for="ug-tag-input">Tag</label>
      {# Tampak seperti satu kotak isian, isinya chip. Wadahnya yang diberi
         tepi; inputnya sendiri telanjang supaya tidak ada kotak di dalam
         kotak. #}
      <div class="ug-tag-kotak" id="ug-tag-kotak">
        <input type="text" class="ug-tag-input" id="ug-tag-input"
               autocomplete="off" placeholder="Tambah tag, pisahkan dengan Enter">
      </div>
    </div>
  </div>

  {# ============================================================= TAHAP 1 #}
  <section class="ug-tahap1" id="ug-tahap1">

    <div class="drop ug-drop" id="ug-drop">
      <span class="ug-drop-ikon" aria-hidden="true">&uarr;</span>
      <b class="ug-drop-judul">Tarik gambar, folder dataset, atau berkas
        <span class="mono">.zip</span> ke sini</b>
      <span class="ug-drop-atau">atau pilih sendiri</span>
      <span class="ug-drop-tombol">
        <button class="cta" type="button" id="ug-pilih-berkas">Pilih berkas</button>
        <button class="cta garis" type="button" id="ug-pilih-folder">Pilih folder</button>
      </span>
      <span class="ug-drop-batas">
        subfolder <span class="mono">images</span> /
        <span class="mono">labels</span> ikut terjaga &middot;
        maks {{ max_upload_mb }} MB per berkas, {{ max_zip_mb }} MB untuk .zip</span>
    </div>

    {# webkitdirectory: pemilih folder. Didukung Chrome, Edge, dan Firefox.
       Kedua input ini dipakai ulang oleh tombol di bilah tahap 2, jadi ia
       duduk di luar section tahap mana pun tidak perlu; ia di sini karena
       tahap 1 yang pertama memakainya, dan `hidden` pada section induk tidak
       pernah memengaruhinya karena input file memang tak terlihat. #}
    <input id="ug-folder" type="file" webkitdirectory multiple hidden>
    {# Daftar ini harus sama dengan UP_EXT di app.js dan dengan
       IMG_EXT + ANN_EXT + META_EXT + ARSIP_EXT di app/config.py. #}
    <input id="ug-berkas" type="file" multiple hidden
           accept=".jpg,.jpeg,.png,.bmp,.webp,.tif,.tiff,.json,.txt,.yaml,.yml,.zip">

    <div class="ug-bawah">
      <section class="panel ug-format">
        <h3>Format yang didukung</h3>
        <div class="ug-format-kisi">
          <div class="ug-format-grup">
            <b>Gambar</b>
            <span class="mono">.jpg .jpeg .png .bmp .webp .tif .tiff</span>
          </div>
          <div class="ug-format-grup">
            <b>Anotasi</b>
            <span><span class="mono">.json</span> labelme &middot;
              <span class="mono">.txt</span> YOLO</span>
          </div>
          <div class="ug-format-grup">
            <b>Kelas</b>
            {# Sebab paling sering dataset Roboflow tampil dengan kelas berupa
               angka: data.yaml ketinggalan saat mengunggah. #}
            <span><span class="mono">data.yaml</span> &middot; tanpa berkas ini
              kelasnya jadi angka</span>
          </div>
          <div class="ug-format-grup">
            <b>Arsip</b>
            <span><span class="mono">.zip</span> &middot; dibongkar sendiri di
              server</span>
          </div>
        </div>
      </section>

      <section class="panel ug-server">
        <h3>Ambil dari folder di server</h3>
        <p class="ug-server-ket">Isinya <b>disalin</b> ke ruang kerjamu. Folder
          aslinya tidak pernah disentuh, jadi aman disunting dan ditambahi
          gambar.</p>
        <div class="row">
          <input id="ug-server-path" type="text" class="mono"
                 placeholder="/home/paul/computer-vision/datasets/nama-folder">
          <button class="cta" type="button" id="ug-server-impor">Salin</button>
        </div>
        <div class="jalur-status" id="ug-server-jalur"></div>

        {# Path dataset di server panjang, dan salah satu huruf di tengahnya
           bisa membuka folder lain tanpa disadari. Karena itu yang sudah
           pernah dipakai diingat, bukan diketik ulang. #}
        {% if riwayat %}
        <div class="ug-riwayat" id="ug-server-riwayat">
          <div class="ug-riwayat-judul">Path yang pernah kamu pakai</div>
          {%- for r in riwayat %}
          <div class="ug-riwayat-baris">
            <button class="ug-riwayat-path mono" type="button" data-path="{{ r.path }}"
              {% if not r.ada %}disabled title="folder ini sudah tidak ada di server"{% endif %}
              >{{ r.path }}</button>
            <button class="ug-riwayat-lupa" type="button" data-path="{{ r.path }}"
                    title="hapus dari daftar ini saja"
                    aria-label="Lupakan path ini">&times;</button>
          </div>
          {%- endfor %}
        </div>
        {% endif %}
      </section>
    </div>
  </section>

  {# ============================================================= TAHAP 2 #}
  <section class="ug-tahap2" id="ug-tahap2" hidden>

    <div class="seg ug-tab" id="ug-tab" role="group" aria-label="Saring berkas">
      <label class="seg-opt" data-on>
        <input type="radio" name="ug-saring" value="semua" checked>
        Semua gambar <b id="ug-n-semua">0</b></label>
      <label class="seg-opt">
        <input type="radio" name="ug-saring" value="anotasi">
        Sudah dianotasi <b id="ug-n-anotasi">0</b></label>
      <label class="seg-opt">
        <input type="radio" name="ug-saring" value="tanpa">
        Belum dianotasi <b id="ug-n-tanpa">0</b></label>
    </div>

    {# Seluruh panggung jadi sasaran seret, bukan cuma bilahnya: setelah
       thumbnail memenuhi layar, bilah setinggi 52px itu sasaran yang terlalu
       kecil untuk folder yang diseret dari jendela lain. #}
    <div class="ug-panggung" id="ug-panggung">
      <div class="ug-bilah">
        <div class="ug-bilah-ket">
          <b>Tarik berkas lain ke sini untuk menambah</b>
          <span class="mono">.jpg .png .json .txt data.yaml .zip</span>
        </div>
        <span class="spacer"></span>
        <button class="chip" type="button" id="ug-tambah-berkas">Pilih berkas</button>
        <button class="chip" type="button" id="ug-tambah-folder">Pilih folder</button>
        <button class="chip aksi" type="button" id="ug-simpan">
          Simpan dan lanjutkan &rarr;</button>
      </div>

      <div class="ug-kisi-bungkus" id="ug-kisi-bungkus" tabindex="0"
           aria-label="Berkas yang akan diunggah">
        <div class="ug-kisi" id="ug-kisi">
          {# Diisi JavaScript dari #ug-ubin-tpl. Kosong di server. #}
        </div>

        {# Muncul hanya saat saringan tidak menyisakan apa pun. .pkosong sudah
           punya grid-column:1/-1, jadi ia melebar penuh di dalam kisi. #}
        <div class="pkosong" id="ug-kosong" hidden>
          <b>Tidak ada yang cocok</b>
          Tidak ada berkas pada saringan ini. Pilih tab lain.
        </div>
      </div>
    </div>

    {# Cetakan satu ubin. Dipakai JavaScript dengan cloneNode(true). #}
    <template id="ug-ubin-tpl">
      <figure class="ug-ubin" data-anotasi="tidak">
        <span class="ug-gambar">
          <img alt="" loading="lazy" decoding="async">
          <span class="ug-tanda" hidden>anotasi</span>
          <button class="ug-buang" type="button"
                  aria-label="Keluarkan berkas ini">&times;</button>
        </span>
        <figcaption class="ug-nama"></figcaption>
      </figure>
    </template>
  </section>

  {# ============================================================= TAHAP 3 #}
  <section class="ug-tahap3" id="ug-tahap3" hidden>
    <div class="ug-unggah">
      <h3 class="ug-unggah-judul">Mengunggah
        <b id="ug-unggah-nama">batch</b></h3>

      {# Wadah untuk Progres.mulai({di: ...}). Markup di dalamnya berbentuk
         persis seperti yang dibangun modul itu, jadi tampilannya sama entah
         digerakkan Progres atau disetel tangan lewat #ug-fill. #}
      <div class="jalur-status" id="ug-progres-jalur">
        <div class="pr-kotak" id="ug-progres">
          <div class="prog" id="ug-prog" data-on><i id="ug-fill"></i></div>
          <div class="pr-teks">
            <span id="ug-progres-rinci">Menyiapkan&hellip;</span>
            <b id="ug-progres-persen">0%</b>
          </div>
        </div>
      </div>

      <p class="ug-unggah-ket" id="ug-unggah-ket">
        Biarkan tab ini terbuka sampai selesai. Berkas yang sudah masuk tidak
        diulang kalau unggahannya dilanjutkan nanti.</p>

      <div class="ug-unggah-aksi">
        <button class="chip" type="button" id="ug-batal">Batalkan</button>
        <span class="spacer"></span>
        <a class="chip aksi" id="ug-lanjut" href="/" hidden>Buka grid &rarr;</a>
      </div>
    </div>
  </section>

</div>
</main>
{% endblock %}
```

### Daftar id dan kegunaannya

**Header dan tahap**

| id | untuk apa |
|---|---|
| `ug-langkah` | wadah penunjuk tahap |
| `ug-langkah-1` .. `ug-langkah-3` | pindahkan `data-on` / `data-selesai` di sini saat tahap berganti |
| `ug-tahap1`, `ug-tahap2`, `ug-tahap3` | tiga section; `el.hidden` untuk berpindah |

**Isian bersama**

| id | untuk apa |
|---|---|
| `ug-nama-batch` | `<input type=text>` nama batch |
| `ug-tag-kotak` | wadah chip tag; sisipkan `<span class="ug-tag">` sebelum inputnya |
| `ug-tag-input` | tempat mengetik tag baru |

**Tahap 1**

| id | untuk apa |
|---|---|
| `ug-drop` | kotak drop besar; pasang `dragover`/`drop`, setel `data-over` seperti pada `#drop` yang sudah ada (`app.js` baris 413) |
| `ug-pilih-berkas`, `ug-pilih-folder` | tombol pemicu input file |
| `ug-berkas`, `ug-folder` | `<input type=file>` tersembunyi; dipakai ulang oleh tombol tahap 2 |
| `ug-server-path` | isian path folder server |
| `ug-server-impor` | tombol salin |
| `ug-server-jalur` | wadah kosong untuk `Progres.mulai({di: ...})` |
| `ug-server-riwayat` | daftar path yang pernah dipakai; tombolnya membawa `data-path` |

**Tahap 2**

| id | untuk apa |
|---|---|
| `ug-tab` | kontrol tersegmen; radio bernama `ug-saring`, nilai `semua`/`anotasi`/`tanpa`. Pindahkan `data-on` ke `<label>` yang terpilih, sama seperti `#ptab` |
| `ug-n-semua`, `ug-n-anotasi`, `ug-n-tanpa` | angka di tiap tab |
| `ug-panggung` | sasaran seret kedua; setel `data-over` di sini |
| `ug-tambah-berkas`, `ug-tambah-folder` | tombol di bilah, memicu input file yang sama |
| `ug-simpan` | tombol utama, memindahkan ke tahap 3 |
| `ug-kisi-bungkus` | wadah bergulir |
| `ug-kisi` | kisi thumbnail |
| `ug-ubin-tpl` | `<template>` satu ubin |
| `ug-kosong` | pesan saat saringan kosong |

**Tahap 3**

| id | untuk apa |
|---|---|
| `ug-progres-jalur` | wadah untuk `Progres.mulai({di: ...})` |
| `ug-progres` | `.pr-kotak`; setel `data-keadaan="selesai"` atau `"gagal"` untuk pewarnaan yang sudah ada |
| `ug-prog`, `ug-fill` | `.prog` dan batang isinya; setel `style.width` kalau tidak memakai `Progres` |
| `ug-progres-rinci`, `ug-progres-persen` | teks kiri dan persentase kanan |
| `ug-unggah-nama`, `ug-unggah-ket` | judul dan keterangan |
| `ug-batal`, `ug-lanjut` | batalkan, dan tautan ke grid setelah selesai |

Kelas yang perlu ditambahkan JavaScript: `.ug-lebih` (baris penutup kisi saat
daftarnya dipotong, lihat bagian 5), `.ug-tag` (chip tag).

### Dua jebakan untuk yang menulis JS-nya

1. `.drop` punya `cursor:pointer` dan biasanya diklik untuk membuka pemilih
   berkas. Di dalam `.ug-drop` ada dua tombol; klik pada tombol menggelembung
   ke kotaknya dan pemilih berkas terbuka **dua kali**. Panggil
   `e.stopPropagation()` di kedua tombol, atau jangan pasang `onclick` pada
   kotaknya sama sekali.
2. `Progres.mulai({di: el})` menghapus `innerHTML` wadahnya. Kalau memakai
   jalur itu, jangan simpan rujukan ke `#ug-fill` sebelum memanggilnya.

---

## 3. CSS

Tempel di **akhir** `app.css`, setelah blok dialog. Urutan itu penting: dua
aturan di bawah (`.ug-tab` melawan `margin` milik `.seg`, `.cta.garis` melawan
`.cta:hover`) menang karena datang belakangan, bukan karena selektornya lebih
kuat.

Tidak ada warna heksadesimal baru. Tidak ada blok tema gelap untuk warna,
karena semuanya sudah token; yang perlu diulang hanya bayangan, persis seperti
yang sudah dilakukan `.pcard` dan `.kg-pil`.

```css
/* ===================================================== halaman Unggah data
 *
 * Tiga tahap, satu halaman, satu <main>. Tahap 2 dan 3 hanya disembunyikan
 * dengan atribut hidden: nama batch dan tag yang sudah diketik harus selamat
 * saat orang mundur dari tahap 2 ke tahap 1, dan pindah halaman
 * menghapusnya.
 *
 * Kolom kanan Roboflow (ponsel, Universe, cloud storage) tidak ada di sini,
 * dan ruangnya sengaja TIDAK diisi kartu pengganti. Kotak drop yang melebar
 * penuh sasaran seretnya lebih besar, dan itu memang yang dibutuhkan: orang
 * menyeret folder dari jendela lain sambil menahan tombol tetikus, dengan
 * penglihatan terbagi antara dua jendela.
 *
 * Lebarnya 1360px, sama dengan halaman Pilih projek. Dua halaman yang lebar
 * isinya berbeda membuat header dan kartunya bergeser tiap kali berpindah.
 */
.ug-halaman{max-width:1360px;margin:0 auto}

/* ------------------------------------------------------- penunjuk tahap
   Duduk di dalam header yang lengket, menggantikan pengganjal 12px milik
   pick.html. Pada tahap 2 dengan ribuan thumbnail, ini satu-satunya yang
   memberitahu bahwa masih ada tahap ketiga sesudahnya. */
.ug-langkah{display:flex;align-items:center;gap:9px;flex-wrap:wrap;
  margin-top:12px;padding-bottom:11px;font-size:12px;color:var(--ink-faint)}
.ug-langkah-item{display:flex;align-items:center;gap:7px;white-space:nowrap}
.ug-langkah-no{display:grid;place-items:center;width:18px;height:18px;
  flex:0 0 auto;border-radius:50%;background:var(--sunk);
  border:1px solid var(--line);font-size:10.5px;font-weight:700;
  color:var(--ink-faint);font-variant-numeric:tabular-nums}
.ug-langkah-item[data-on]{color:var(--ink);font-weight:600}
.ug-langkah-item[data-on] .ug-langkah-no{background:var(--aksi);
  border-color:var(--aksi);color:var(--aksi-ink)}
.ug-langkah-item[data-selesai]{color:var(--ink-dim)}
.ug-langkah-item[data-selesai] .ug-langkah-no{background:var(--ok-lembut);
  border-color:var(--ok-pudar);color:var(--aksi)}
.ug-langkah-panah{font-size:11px}

/* ------------------------------------------------------------ nama & tag
   auto-fit, bukan dua kolom mati: di bawah ~600px keduanya menumpuk sendiri
   tanpa media query, dan pada lebar berapa pun tidak ada kolom yang lebih
   sempit dari 280px. Kotak isian yang lebih sempit dari itu membuat nama
   batch tergulir mendatar di dalam kotaknya sendiri. */
.ug-isian{display:grid;gap:10px 18px;margin-top:16px;
  grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
.ug-isian .form-lbl{margin-top:0}

/* Kotak tag tampak seperti satu <input type=text> supaya sebaris dengan nama
   batch di sebelahnya, tapi isinya chip. 40px = tinggi input yang sudah ada
   (padding 9px + baris 20px + tepi 2px); disamakan dengan tangan karena
   isinya bukan teks biasa. */
.ug-tag-kotak{display:flex;align-items:center;gap:6px;flex-wrap:wrap;
  min-height:40px;padding:5px 8px;border:1px solid var(--line2);
  border-radius:6px;background:var(--raised)}
.ug-tag-kotak:focus-within{border-color:var(--ok)}
.ug-tag{display:inline-flex;align-items:center;gap:4px;max-width:220px;
  padding:2px 4px 2px 9px;border-radius:99px;
  background:var(--lab-bg);color:var(--lab-ink);font-size:11.5px}
.ug-tag span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ug-tag button{flex:0 0 auto;border:0;background:transparent;color:inherit;
  cursor:pointer;font:inherit;line-height:1;padding:1px 4px;border-radius:99px}
.ug-tag button:hover{background:var(--hover);color:var(--ink)}
/* flex-basis 90px, bukan auto: dengan auto kolom ketiknya menyusut jadi
   beberapa piksel begitu chipnya banyak, dan kursornya menghilang. */
.ug-tag-input{flex:1 1 90px;min-width:90px;border:0;background:transparent;
  color:var(--ink);font:inherit;font-size:13.5px;padding:4px 2px}
.ug-tag-input:focus{outline:0}

/* ------------------------------------------------------- kotak drop besar
   Mewarisi .drop, jadi hover dan [data-over] sudah terurus dan penangan
   seret di app.js tidak perlu tahu ada bentuk kedua.

   Tingginya clamp, bukan angka mati. Pada laptop 1280x720 ruang di bawah
   header tinggal sekitar 600px; kotak 380px membuat panel "Format yang
   didukung" jatuh di luar layar, padahal justru itu yang dibaca orang
   SEBELUM menyeret. 34vh menjaga panel itu tetap terlihat di layar pendek
   dan tetap memberi kotak yang lapang di layar tinggi. */
.drop.ug-drop{display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:11px;
  min-height:clamp(190px,34vh,300px);margin-top:16px;padding:28px 20px;
  background:var(--panel);border-radius:11px}
.ug-drop-ikon{display:grid;place-items:center;width:46px;height:46px;
  flex:0 0 auto;border-radius:50%;background:var(--sunk);color:var(--ink-dim);
  font-size:21px;transition:background .14s,color .14s}
.drop.ug-drop:hover .ug-drop-ikon,
.drop.ug-drop[data-over] .ug-drop-ikon{background:var(--ok-pudar);color:var(--aksi)}
.ug-drop-judul{font-size:15px;font-weight:600;color:var(--ink);text-align:center}
.ug-drop-atau{font-size:12px;color:var(--ink-faint)}
.ug-drop-tombol{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}
/* .cta bawaan punya margin-top:26px untuk pemakaiannya di .card-form. */
.ug-drop-tombol .cta{margin-top:0;padding:10px 22px;font-size:13.5px}
.ug-drop-batas{max-width:560px;font-size:11.5px;line-height:1.55;
  color:var(--ink-faint);text-align:center}

/* Varian bergaris untuk .cta. Sebelumnya bentuk ini ditulis sebagai style
   sebaris di pick.html tiap kali dibutuhkan, dan tiap salinannya sedikit
   berbeda. */
.cta.garis{background:transparent;color:var(--ink-dim);
  border:1px solid var(--line2)}
.cta.garis:hover{background:var(--hover);color:var(--ink);
  border-color:var(--line2)}

/* -------------------------------------------------- panel pendukung bawah
   minmax(0,...), bukan 1.3fr saja: jalur grid bawaannya min-content, dan
   satu path server sepanjang 90 karakter di dalam .ug-server memaksa
   jalurnya melebihi lebar halaman. Itu jalur paling mudah menghasilkan
   guliran mendatar yang tidak diinginkan siapa pun. */
.ug-bawah{display:grid;gap:16px;margin-top:16px;
  grid-template-columns:minmax(0,1.3fr) minmax(0,1fr)}
.ug-bawah > .panel{margin-top:0}

.ug-format-kisi{display:grid;gap:12px 18px;
  grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.ug-format-grup b{display:block;font-size:12px;color:var(--ink);
  margin-bottom:3px;font-weight:600}
/* anywhere, bukan break-word: deretan ekstensi itu satu "kata" panjang tanpa
   spasi yang bisa dipatahkan, dan tanpa ini ia melebarkan panelnya. */
.ug-format-grup span{display:block;font-size:11.5px;line-height:1.6;
  color:var(--ink-dim);overflow-wrap:anywhere}

.ug-server-ket{font-size:12px;line-height:1.55;color:var(--ink-dim);
  margin-bottom:10px}
.ug-server-ket b{color:var(--ink);font-weight:600}
/* .row tidak membungkus; di kolom kanan yang sempit, tombol Salin terdorong
   keluar panel. */
.ug-server .row{flex-wrap:wrap}
.ug-server .row input{min-width:170px}
.ug-server .row .cta{flex:0 0 auto;width:auto;margin-top:0;
  padding:9px 16px;font-size:13px}

.ug-riwayat{margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}
.ug-riwayat-judul{font-size:11px;color:var(--ink-faint);margin-bottom:6px}
.ug-riwayat-baris{display:flex;align-items:center;gap:4px;margin-bottom:4px}
.ug-riwayat-path{flex:1 1 auto;min-width:0;text-align:left;cursor:pointer;
  border:1px solid var(--line2);border-radius:6px;background:transparent;
  color:var(--ink-dim);font-family:inherit;font-size:11.5px;padding:6px 8px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ug-riwayat-path:hover{border-color:var(--ok);color:var(--ink)}
.ug-riwayat-path:disabled{color:var(--ink-faint);cursor:default;
  border-color:var(--line)}
.ug-riwayat-lupa{flex:0 0 auto;border:0;background:transparent;cursor:pointer;
  color:var(--ink-faint);font:inherit;font-size:14px;line-height:1;
  padding:4px 7px;border-radius:6px}
.ug-riwayat-lupa:hover{background:var(--hover);color:var(--ink)}

/* --------------------------------------------------------- tahap 2: tab
   Bentuknya sama dengan .ptab di halaman projek. Ditulis terpisah, bukan
   memakai .ptab, karena .ptab ikut aturan bilah alat lengket yang tidak ada
   di halaman ini.
   width:max-content supaya pilnya selebar isinya, bukan selebar halaman;
   flex-wrap supaya di layar sempit ketiga tab menumpuk di dalam pil, bukan
   menggulir mendatar. */
.ug-tab{margin:18px 0 0;width:max-content;max-width:100%;flex-wrap:wrap}
.ug-tab .seg-opt{flex:0 0 auto;padding:6px 14px}
.ug-tab .seg-opt b{margin-left:7px;font-size:10.5px;font-weight:600;
  color:var(--ink-faint);font-variant-numeric:tabular-nums}
.ug-tab .seg-opt[data-on] b{color:var(--aksi)}

/* ------------------------------------------------------ tahap 2: panggung */
.ug-panggung{margin-top:12px;background:var(--panel);
  border:1px solid var(--line);border-radius:11px;overflow:hidden;
  box-shadow:0 1px 2px rgba(var(--bayang),.05)}
/* Seluruh panggung jadi sasaran seret. Setelah thumbnail memenuhi layar,
   bilah setinggi 52px itu sasaran yang terlalu kecil untuk folder yang
   diseret dari jendela lain. */
.ug-panggung[data-over]{border-color:var(--ok);
  box-shadow:0 0 0 1px var(--ok),0 1px 2px rgba(var(--bayang),.05)}

/* Bilah tipis, bukan kotak drop kedua setinggi tahap 1: di tahap 2 yang
   dicari orang thumbnailnya, dan tiap 100px bilah ini memakan satu baris
   thumbnail penuh. */
.ug-bilah{display:flex;align-items:center;gap:9px;flex-wrap:wrap;
  padding:10px 14px;background:var(--raised);
  border-bottom:1px solid var(--line)}
.ug-bilah-ket{flex:1 1 240px;min-width:0}
.ug-bilah-ket b{display:block;font-size:12.5px;color:var(--ink)}
.ug-bilah-ket span{display:block;margin-top:2px;font-size:11px;
  color:var(--ink-faint);overflow-wrap:anywhere}
.ug-bilah .chip{flex:0 0 auto}
/* Tombol utama sedikit lebih besar dari chip di sebelahnya: dari sudut mata,
   ukuran yang membedakannya, bukan warnanya saja. */
.ug-bilah .chip.aksi{padding:7px 15px;font-size:13px}

/* Kisi menggulir SENDIRI, bukan ikut halaman. Pada 5.000 berkas, tombol
   "Simpan dan lanjutkan" yang ikut tergulir berada puluhan layar jauhnya
   dari thumbnail terakhir, dan orang menggulir balik ke atas mencarinya.
   Dengan wadah bergulir sendiri, bilahnya selalu di tempat tanpa
   position:sticky sama sekali.
   min(62vh,620px): 62vh supaya di layar pendek kisi tidak menelan seluruh
   jendela, 620px supaya di monitor 1440p tinggi kisinya tetap masuk akal. */
.ug-kisi-bungkus{max-height:min(62vh,620px);overflow-y:auto;overflow-x:hidden;
  overscroll-behavior:contain;padding:14px}
.ug-kisi-bungkus:focus-visible{outline:2px solid var(--ok);outline-offset:-2px}
/* auto-fill, BUKAN auto-fit. Dengan auto-fit jalur yang kosong diciutkan,
   dan saat yang di-staging cuma satu berkas, ubin tunggal itu melar
   selebar 1.300px. auto-fill menyisakan jalur kosongnya, jadi ubin
   pertama tetap seukuran ubin mana pun. */
.ug-kisi{display:grid;gap:14px 12px;
  grid-template-columns:repeat(auto-fill,minmax(112px,1fr))}

/* 5.000 ubin berarti 5.000 gambar yang harus ditata dan dilukis.
   content-visibility melewatkan yang berada di luar layar;
   contain-intrinsic-size memberinya tinggi tebakan supaya bilah gulirnya
   tidak melompat-lompat saat digulir. 152px = ubin 112px + nama 30px +
   jarak 6px + sedikit lebih. */
.ug-ubin{position:relative;margin:0;min-width:0;
  content-visibility:auto;contain-intrinsic-size:auto 152px}
.ug-gambar{position:relative;display:block;aspect-ratio:1;border-radius:7px;
  overflow:hidden;background:var(--sunk);border:1px solid var(--line);
  transition:border-color .12s}
/* contain, bukan cover: yang diperiksa di tahap ini adalah "berkasnya benar
   atau tidak", dan cover memotong sisi gambar potret sampai isinya berubah
   arti. Sama dengan .card img di halaman grid. */
.ug-gambar img{width:100%;height:100%;object-fit:contain;display:block}
.ug-ubin:hover .ug-gambar{border-color:var(--line2)}
.ug-tanda{position:absolute;left:5px;bottom:5px;padding:1px 6px;
  border-radius:99px;font-size:9.5px;font-weight:600;
  background:var(--aksi);color:var(--aksi-ink)}
.ug-buang{position:absolute;top:4px;right:4px;width:20px;height:20px;
  display:grid;place-items:center;border:0;border-radius:6px;cursor:pointer;
  font:inherit;font-size:13px;line-height:1;
  background:var(--panel);color:var(--ink-faint);
  box-shadow:0 0 0 1px var(--line)}
.ug-buang:hover{color:var(--stop);box-shadow:0 0 0 1px var(--stop)}
.ug-buang:focus-visible{outline:2px solid var(--ok);outline-offset:1px}
/* Disembunyikan sampai hover HANYA pada tetikus. Di layar sentuh tidak ada
   hover, dan tombol yang butuh hover di sana sama saja dengan tidak ada. */
@media (hover:hover){
  .ug-buang{opacity:0;transition:opacity .12s}
  .ug-ubin:hover .ug-buang,.ug-buang:focus-visible{opacity:1}
}
/* Dua baris lalu terpotong, dan tingginya DIKUNCI. Kalau tiap nama memakai
   jumlah baris berbeda, tiap baris kisi punya tinggi berbeda dan ubinnya
   jadi bergerigi. Nama lengkapnya ada di atribut title.
   break-word, dan bukan direction:rtl: rtl memindahkan titik ke depan
   sehingga "000036.jpg" terbaca "jpg.000036". */
.ug-nama{margin-top:6px;height:30px;overflow:hidden;
  display:-webkit-box;-webkit-line-clamp:2;line-clamp:2;
  -webkit-box-orient:vertical;
  font-size:10.5px;line-height:1.4;color:var(--ink-dim);
  word-break:break-word;text-align:center}

/* Baris penutup kisi saat daftarnya dipotong. Di DALAM kisi dengan kolom
   penuh, supaya ia ikut tergulir bersama thumbnail terakhir dan bukan
   menggantung di luar wadah gulir. */
.ug-lebih{grid-column:1/-1;padding:14px 4px 2px;text-align:center;
  font-size:12px;color:var(--ink-dim);font-variant-numeric:tabular-nums}

/* ---------------------------------------------------------- tahap 3
   Sempit dan di tengah. Yang terjadi di tahap ini cuma satu hal, dan
   membentangkannya selebar 1.360px membuat bilah progres terlihat seperti
   garis pemisah halaman, bukan seperti pekerjaan yang sedang berjalan. */
.ug-unggah{max-width:560px;margin:28px auto 0;background:var(--panel);
  border:1px solid var(--line);border-radius:11px;padding:20px 22px 18px;
  box-shadow:0 1px 2px rgba(var(--bayang),.05)}
.ug-unggah-judul{font-size:14px;font-weight:600;color:var(--ink)}
/* Nama batch bisa panjang; dipotong elipsis supaya judulnya tidak membungkus
   tiga baris dan mendorong bilah progres ke bawah. */
.ug-unggah-judul b{display:inline-block;max-width:100%;vertical-align:bottom;
  color:var(--aksi);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ug-unggah-ket{margin-top:12px;font-size:12px;line-height:1.55;
  color:var(--ink-dim)}
.ug-unggah-aksi{display:flex;align-items:center;gap:8px;margin-top:16px}

/* ---------------------------------------------------------- layar sempit */
@media (max-width:900px){
  .ug-bawah{grid-template-columns:1fr}
}
@media (max-width:720px){
  /* Kotak drop yang tingginya sepertiga layar menyisakan terlalu sedikit
     ruang untuk panel format di bawahnya pada layar ponsel. */
  .drop.ug-drop{min-height:clamp(160px,26vh,230px);padding:22px 14px}
  .ug-drop-tombol .cta{flex:1 1 140px}
  .ug-tab{width:auto}
  .ug-tab .seg-opt{flex:1 1 auto}
  /* Ubin 112px memberi 3 kolom di layar 360px, dan 3 kolom membuat nama
     berkas terpotong di huruf keenam. 92px memberi 4 kolom yang lebih
     lapang. */
  .ug-kisi{grid-template-columns:repeat(auto-fill,minmax(92px,1fr));gap:12px 10px}
  .ug-ubin{contain-intrinsic-size:auto 130px}
  .ug-kisi-bungkus{padding:11px}
  /* Tombol utama pindah ke barisnya sendiri, selebar bilah. */
  .ug-bilah .chip.aksi{flex:1 1 100%;text-align:center}
}

/* -------------------------------------------------------------- tema gelap
   Seluruh warnanya sudah token; yang perlu diulang hanya bayangan. Bayangan
   halus di atas --panel gelap tidak terlihat, jadi panggung dan kartu
   unggahnya kehilangan pemisah dari latar. */
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]) .ug-panggung,
  :root:not([data-theme="light"]) .ug-unggah{box-shadow:none}
  :root:not([data-theme="light"]) .ug-panggung[data-over]{
    box-shadow:0 0 0 1px var(--ok)}
  /* Tombol buang berlatar --panel di atas gambar gelap butuh tepi yang lebih
     tegas supaya bentuknya masih terbaca. */
  :root:not([data-theme="light"]) .ug-buang{box-shadow:0 0 0 1px var(--line2)}
}
:root[data-theme="dark"] .ug-panggung,
:root[data-theme="dark"] .ug-unggah{box-shadow:none}
:root[data-theme="dark"] .ug-panggung[data-over]{box-shadow:0 0 0 1px var(--ok)}
:root[data-theme="dark"] .ug-buang{box-shadow:0 0 0 1px var(--line2)}

@media (prefers-reduced-motion:reduce){
  .ug-drop-ikon,.ug-gambar,.ug-buang{transition:none}
}
```

---

## 4. Ukuran yang dipilih, dan alasannya

| Yang diatur | Nilai | Kenapa segitu |
|---|---|---|
| Lebar halaman | `1360px` | Sama dengan halaman Pilih projek. Dua halaman dengan lebar isi berbeda membuat header bergeser tiap kali berpindah. |
| Tinggi kotak drop | `clamp(190px, 34vh, 300px)` | Pada 1280x720 ruang di bawah header hanya ~600px. Kotak 380px (mendekati Roboflow) membuang panel "Format yang didukung" ke luar layar, padahal itu yang dibaca orang sebelum menyeret. 34vh menjaga panel itu terlihat di layar pendek, batas 300px mencegah kotak kosong sepertiga layar di monitor 1440p. |
| Lingkaran ikon drop | `46px` | Cukup jadi jangkar visual di tengah kotak 250px, tidak sampai jadi tombol palsu yang orang coba klik. |
| Jarak isi kotak drop | `gap 11px` | Judul, "atau", tombol, dan batas ukuran adalah satu blok bacaan; jarak lebih besar memecahnya jadi empat hal terpisah. |
| Ukuran thumbnail | `minmax(112px, 1fr)` | Di 1280 memberi 9 kolom. Pada 112px, foto 4080x2296 masih cukup untuk menjawab satu-satunya pertanyaan tahap ini: "berkas yang benar atau bukan". Di bawah ~90px nama berkasnya yang jadi tidak terbaca, bukan gambarnya. |
| Jarak antar ubin | `14px 12px` | Baris lebih renggang daripada kolom karena di antara baris ada dua baris teks nama; jarak yang sama di dua arah membuat nama ubin atas terbaca menempel ke gambar ubin bawah. |
| Tinggi nama berkas | `30px` mati (2 baris x 10.5px x 1.4) | Dikunci, bukan otomatis. Nama satu baris di sebelah nama tiga baris membuat tiap baris kisi punya tinggi berbeda dan ubinnya bergerigi. |
| Tinggi wadah gulir | `min(62vh, 620px)` | 62vh menyisakan ruang untuk tab di atas dan tepi bawah di layar pendek; 620px mencegah kisi setinggi 900px di monitor tinggi, yang berarti menggulir mata dari atas ke bawah untuk satu baris thumbnail. |
| Isian wadah gulir | `14px` | Sama dengan `gap` baris, jadi jarak thumbnail ke tepi panggung sama dengan jarak antar thumbnail. |
| Tinggi bilah tahap 2 | `~52px` (padding 10px + dua baris teks) | Tiap 100px bilah memakan satu baris thumbnail penuh. Roboflow memakai ~95px karena memuat daftar format lengkap; kita cukup satu baris ringkas. |
| Jarak antar blok | `16px` | Sama dengan `.panel{margin-top:16px}` yang sudah ada, jadi irama vertikalnya sama dengan halaman lain. |
| Lebar kartu tahap 3 | `560px` | Sedikit lebih lebar dari `.dlg-kotak` (520px). Membentangkan bilah progres selebar 1.360px membuatnya terbaca sebagai garis pemisah halaman, bukan sebagai pekerjaan yang berjalan. |

---

## 5. Perilaku menurut lebar layar

**1280px (laptop).** `main` berisi 22px di kiri-kanan, jadi lebar isi 1236px,
di bawah batas 1360px: halaman memakai seluruh lebar yang ada.

- Nama batch dan tag: dua kolom, masing-masing ~609px.
- Kotak drop: lebar penuh 1236px, tinggi 245px pada jendela 720px.
- Panel bawah: 1.3fr : 1fr, jadi ~688px dan ~532px. Panel format sendiri
  memuat 4 grup dalam satu baris (`minmax(150px,1fr)` di lebar 652px).
- Kisi: `(1236 - 2 - 28) / 124 = 9,7` jadi **9 kolom** ubin ~123px.
- Bilah tahap 2: keterangan, dua chip, dan tombol utama muat dalam satu baris.

**1920px (monitor).** Lebar isi terkunci di 1360px, sisa 560px jadi margin.

- Kisi: `(1360 - 2 - 28) / 124 = 10,7` jadi **10 kolom** ubin ~122px.
- Selisihnya dengan 1280 cuma satu kolom, dan itu memang disengaja. Membiarkan
  halaman melebar penuh di 1920 memberi 15 kolom, dan pada 15 kolom nama
  berkas jadi hal terkecil di layar sementara gambar yang isinya hampir sama
  semua jadi hal terbesar. Kalau nanti kamu ingin lebih banyak kolom di
  monitor besar, satu-satunya yang perlu diubah adalah `max-width` pada
  `.ug-halaman`; sisanya ikut sendiri.

**Di bawah 900px.** Panel format dan panel folder server menumpuk jadi satu
kolom.

**Di bawah 720px.** Kotak drop memendek, dua tombolnya melebar berbagi baris,
tab jadi selebar pil dan boleh menumpuk, ubin turun ke 92px (4 kolom di layar
360px), dan tombol "Simpan dan lanjutkan" pindah ke barisnya sendiri selebar
bilah.

**Tidak ada guliran mendatar,** dan ini yang menjaganya:

- `.ug-bawah` memakai `minmax(0,...)`, bukan `1.3fr` telanjang. Jalur grid
  bawaannya `min-content`, dan satu path server sepanjang 90 karakter cukup
  untuk memaksa jalurnya melebihi lebar halaman.
- `.ug-bilah-ket` dan `.ug-format-grup span` memakai `overflow-wrap:anywhere`
  untuk deretan ekstensi yang tidak punya spasi.
- `.ug-riwayat-path` dan `.ug-unggah-judul b` memotong dengan elipsis.
- `.ug-kisi-bungkus` memakai `overflow-x:hidden`, jadi kalaupun ada ubin yang
  meleset ia tidak menular ke halaman.
- `.ug-ubin{min-width:0}` supaya jalur grid boleh menyusut di bawah lebar
  isinya.
- `.ug-tab` memakai `flex-wrap:wrap`, bukan gulir mendatar di dalam pil.

---

## 6. Kasus tepi

### Nama berkas sangat panjang

`detection_plastic-cup_2026-08-10-17-15-13_original_augmented_v3.jpg` (68
karakter) di ubin selebar 121px:

- `.ug-nama` memotong pada baris kedua dengan `-webkit-line-clamp:2`, tinggi
  terkunci 30px, jadi baris kisinya tidak berubah tinggi. Yang terbaca kira-kira
  `detection_plastic-cup_2026-08-10-` lalu terpotong.
- `word-break:break-word` yang memotongnya, bukan `direction:rtl`. `rtl` memang
  menampilkan ekor nama yang lebih membedakan, tapi ia juga memindahkan titik
  ke depan sehingga `000036.jpg` terbaca `jpg.000036`. Alasan yang sama sudah
  ditulis di `.fn` pada halaman grid.
- Nama lengkapnya wajib dipasang JavaScript sebagai `title` pada `.ug-ubin`,
  supaya bisa dibaca utuh lewat tooltip. Tanpa itu, dua berkas yang 40 huruf
  pertamanya sama jadi tidak bisa dibedakan sama sekali.
- Di tahap 3, nama batch panjang dipotong elipsis satu baris oleh
  `.ug-unggah-judul b`.

### Hanya 1 berkas di-staging

- `repeat(auto-fill, ...)`, bukan `auto-fit`. Ini bagian yang paling mudah
  salah: dengan `auto-fit` jalur kosong diciutkan dan ubin tunggal itu melar
  selebar ~1.300px, jadi satu foto raksasa memenuhi layar. `auto-fill`
  menyisakan jalur kosongnya, jadi ubin pertama tetap ~123px seperti ubin mana
  pun.
- `max-height` pada wadah gulir adalah batas atas, bukan tinggi tetap, jadi
  panggungnya menyusut mengikuti isinya: satu ubin berarti panggung setinggi
  ~240px, tanpa ruang kosong 600px di bawahnya.
- Tab tetap ditampilkan dengan angka `1 / 0 / 1`. Angka nol di dua tab itu
  informasi, bukan derau: ia mengatakan berkasnya belum punya anotasi.

### Lebih dari 5.000 berkas

Yang sudah ditangani tata letak:

- `content-visibility:auto` + `contain-intrinsic-size:auto 152px` pada
  `.ug-ubin`: peramban melewatkan penataan dan pelukisan ubin yang berada di
  luar layar, dan tetap memberi tinggi tebakan supaya bilah gulirnya tidak
  melompat-lompat.
- Tinggi nama yang terkunci berarti tinggi tiap baris kisi bisa dihitung tanpa
  mengukur, jadi tebakan `contain-intrinsic-size` itu tepat, bukan kira-kira.
- `overscroll-behavior:contain` menahan guliran supaya tidak menular ke halaman
  begitu kisinya habis.
- `.ug-lebih` sudah tersedia untuk baris penutup di dalam kisi, misalnya
  `Menampilkan 1.000 dari 5.231 berkas. Sisanya tetap ikut diunggah.`
- Angka di tab memakai `font-variant-numeric:tabular-nums`, jadi
  `5.231 -> 5.230 -> 5.229` tidak menggeser lebar tabnya saat orang membuang
  berkas satu per satu.

Yang masih harus diputuskan saat menulis JavaScript, dan tata letak ini tidak
menghalanginya:

1. **Batasi jumlah ubin yang benar-benar dibuat.** `content-visibility`
   menyelamatkan pelukisan, bukan memori. 5.000 elemen `<img>` dengan 5.000
   `URL.createObjectURL` menahan 5.000 berkas di memori sekaligus. Rendernya
   dipotong di angka tertentu (1.000 terasa aman), lalu `.ug-lebih` dipakai
   untuk mengatakan berapa yang tidak digambar. Yang tidak digambar **tetap**
   ikut diunggah; kalimat di `.ug-lebih` harus mengatakan itu, kalau tidak
   orang mengira sisanya hilang.
2. **Buat pratinjau bertahap.** Membaca 5.000 berkas sekaligus membekukan tab
   beberapa detik sebelum ubin pertama muncul. Gambarkan per potongan
   (misalnya 50 ubin per `requestAnimationFrame`), dan pakai `Progres` untuk
   tahap membaca itu juga, bukan hanya untuk unggahannya.
3. **Panggil `URL.revokeObjectURL`** saat ubin dibuang dan saat pindah ke tahap
   3. Tanpa itu, satu sesi memilih folder tiga kali menahan 15.000 berkas.
