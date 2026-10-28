/*
 * Halaman unggah: tiga tahap, dan berkasnya baru terbang di tahap ketiga.
 * =====================================================================
 * Tahap 1 memilih, tahap 2 memeriksa, tahap 3 mengirim. Yang membedakannya
 * dari alur lama bukan tampilannya melainkan kapan berkasnya terkirim: dulu
 * memilih folder berarti sudah mengirim, sehingga satu salah pilih folder
 * berarti menunggu seluruh unggahan selesai lalu membereskan sisanya di
 * server. Di sini berkasnya ditahan di peramban sampai ada yang menekan
 * "Simpan dan lanjutkan".
 *
 * Thumbnail dibuat MALAS. Satu objectURL per gambar untuk sepuluh ribu gambar
 * berarti sepuluh ribu bitmap terdekode hidup sekaligus, dan tabnya mati
 * sebelum sempat menampilkan apa pun. Yang dibuat hanya yang terlihat, dan
 * dilepas begitu keluar layar.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const halaman = $('ug-halaman');
  if (!halaman) return;

  const PROJEK = halaman.dataset.projek || '';
  const PATH = halaman.dataset.path || '';
  /* Projek yang SUDAH berisi gambar dikirimi lewat /tambah, yang menaruh tiap
     berkas mengikuti tata letak di sana: dataset bersplit tetap terbagi
     train/valid/test, dataset YOLO tetap punya images/ dan labels/. Projek
     kosong lewat /upload, yang menerima apa saja termasuk .zip lalu dipindai
     dari nol. Yang menentukan keadaan projeknya, bukan pilihan pengguna. */
  const BERISI = halaman.dataset.berisi === '1';
  // Video boleh diunggah ke projek KOSONG maupun projek gambar FLAT (gambar di
  // akar, tata "datar") -- frame hasil ekstraksi mendarat di akar, konsisten.
  // Hanya dataset bersplit/yolo (train/valid/test) yang dikecualikan: frame
  // mentah tak punya tempat di struktur itu. Untuk video, berkasnya SELALU lewat
  // /upload (+ /ekstrak), bukan /tambah yang tak bisa mengekstrak.
  const BOLEH_VIDEO = !BERISI || halaman.dataset.tata === 'datar';
  // Projek VIDEO: "Ambil dari folder di server" mendaftarkan dataset KLIP
  // (train/val folder-per-kelas atau .zip) sebagai VERSI aksi lewat
  // /api/aksi/impor-path, bukan jalur salin-gambar. Hanya admin.
  const JENIS = halaman.dataset.jenis || 'image';
  const ADMIN = halaman.dataset.admin === '1';

  // Nama-nama ini harus sama dengan UP_EXT di app.js dan dengan
  // IMG_EXT + ANN_EXT + META_EXT + ARSIP_EXT di app/config.py.
  const GAMBAR_EXT = ['.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff'];
  const adalahGambar = (n) => GAMBAR_EXT.some(e => n.toLowerCase().endsWith(e));
  // Video hanya diterima di projek IMAGE yang masih kosong: ia diunggah utuh
  // lalu diekstrak jadi frame di server (/ekstrak), videonya dibuang. Harus
  // sama dengan VIDEO_EXT di app/config.py. Projek berisi (BERISI) memakai
  // /tambah yang menempatkan berkas mengikuti tata letaknya — ekstraksi yang
  // menaruh frame di akar tidak cocok di sana, jadi video ditolak di situ.
  const VIDEO_EXT = ['.mp4', '.mov', '.webm', '.mkv', '.avi', '.m4v'];
  const adalahVideo = (n) => VIDEO_EXT.some(e => n.toLowerCase().endsWith(e));
  const stem = (n) => {
    const dasar = n.split('/').pop();
    const t = dasar.lastIndexOf('.');
    return (t > 0 ? dasar.slice(0, t) : dasar).toLowerCase();
  };

  /* Satu daftar untuk semua yang dipilih, dikunci nama relatifnya supaya
     memilih folder yang sama dua kali tidak menggandakan isinya. */
  const berkas = new Map();        // nama -> {file, nama, gambar, arsip, video}
  const tag = [];
  let batal = false;

  // ------------------------------------------------------------- tahap
  function keTahap(n) {
    for (const i of [1, 2, 3]) {
      const s = $('ug-tahap' + i);
      if (s) s.hidden = i !== n;
      const l = $('ug-langkah-' + i);
      if (!l) continue;
      l.toggleAttribute('data-on', i === n);
      l.toggleAttribute('data-selesai', i < n);
    }
    // Nama batch dan tag ikut tahap 1 dan 2, tapi tidak lagi bisa diubah
    // setelah pengirimannya mulai.
    const isian = document.querySelector('.ug-isian');
    if (isian) isian.hidden = n === 3;
  }

  // ------------------------------------------------------------- staging
  function anotasiStem() {
    const s = new Set();
    for (const b of berkas.values()) {
      const n = b.nama.toLowerCase();
      if (n.endsWith('.json') || (n.endsWith('.txt') && !n.endsWith('classes.txt'))) {
        s.add(stem(b.nama));
      }
    }
    return s;
  }

  function gambarTerdaftar() {
    const ann = anotasiStem();
    return [...berkas.values()]
      .filter(b => b.gambar)
      .map(b => ({ ...b, punyaAnotasi: ann.has(stem(b.nama)) }));
  }

  function tambah(daftarFile) {
    let baru = 0, ditolak = 0;
    let arsipDitolak = 0;
    for (const f of daftarFile) {
      const nama = namaKirim(f);
      // Video hanya sah pada projek image yang kosong; di luar itu ia bukan
      // format yang didukung di halaman ini.
      const bolehVideo = BOLEH_VIDEO && adalahVideo(nama);
      if (!UP_EXT.some(e => nama.toLowerCase().endsWith(e)) && !bolehVideo) {
        ditolak++; continue;
      }
      // Membongkar arsip ke dalam dataset yang sudah terbagi akan menumpahkan
      // isinya di akar, di luar train/valid/test. Ditolak di sini, bukan
      // dibiarkan gagal belakangan setelah berkasnya terlanjur naik.
      if (BERISI && adalahArsip(nama)) { arsipDitolak++; continue; }
      if (berkas.has(nama)) continue;
      berkas.set(nama, { file: f, nama, gambar: adalahGambar(nama),
                         arsip: adalahArsip(nama), video: adalahVideo(nama) });
      if (adalahVideo(nama)) muatDurasi(berkas.get(nama));
      baru++;
    }
    if (!berkas.size) {
      toast(BERISI
        ? 'Tidak ada gambar, anotasi, atau data.yaml di pilihan itu'
        : 'Tidak ada gambar, anotasi, data.yaml, .zip, atau video di pilihan itu');
      return;
    }
    if (ditolak) toast(ditolak + ' berkas dilewati karena formatnya tidak didukung');
    if (arsipDitolak) {
      toast(arsipDitolak + ' berkas .zip dilewati: dataset ini sudah berisi, '
            + 'bongkar dulu di laptop lalu tarik isinya');
    }
    if (baru) gambarUlang();
    keTahap(2);
  }

  // ------------------------------------------------------------- thumbnail
  /* Digambar saat ubinnya masuk layar, dilepas saat keluar. rootMargin memberi
     satu layar tambahan di atas dan bawah supaya gambarnya sudah siap saat
     benar-benar terlihat, bukan muncul belakangan sambil menggulir.

     Dipakai createImageBitmap dengan resizeWidth, BUKAN <img src=blob>. Foto
     di dataset ini 2296x4080: sebuah <img> mendekodenya utuh, 37 MB piksel per
     gambar, dan satu layar berisi 14 ubin sudah menghabiskan setengah giga.
     Terukur: dengan <img>, mengambil tangkapan layar halaman ini membuat
     Chrome gagal dengan "Internal error". Dikecilkan saat mendekode, yang
     tersimpan cuma sebesar ubinnya. */
  const SISI_UBIN = 240;

  /* Batas waktu per gambar.
     Foto 2296x4080 di dataset ini pernah membuat createImageBitmap tidak
     pernah menjawab sama sekali di satu lingkungan tanpa GPU. Tanpa batas
     waktu, antrean berhenti di gambar itu dan SELURUH sisa ubin tidak pernah
     digambar. Yang hilang kalau batasnya kena cuma thumbnailnya; berkasnya
     tetap ikut terunggah. */
  const BATAS_LUKIS_MS = 8000;

  const berbatas = (janji) => Promise.race([
    janji,
    new Promise((_, tolak) => setTimeout(() => tolak(new Error('lewat waktu')),
                                         BATAS_LUKIS_MS)),
  ]);

  async function lukis(kanvas, file) {
    let bmp;
    try {
      // resizeWidth membuat pengecilan terjadi DI DALAM pendekodean.
      bmp = await berbatas(createImageBitmap(file, {
        resizeWidth: SISI_UBIN, resizeQuality: 'medium' }));
    } catch (e) {
      try {
        // Peramban tanpa opsi resize: masih jauh lebih baik daripada <img>,
        // karena bitmapnya dilepas begitu selesai digambar.
        bmp = await berbatas(createImageBitmap(file));
      } catch (e2) {
        kanvas.closest('.ug-ubin').dataset.tanpaGambar = '1';
        return;
      }
    }
    const k = Math.min(SISI_UBIN / bmp.width, SISI_UBIN / bmp.height, 1);
    kanvas.width = Math.max(1, Math.round(bmp.width * k));
    kanvas.height = Math.max(1, Math.round(bmp.height * k));
    kanvas.getContext('2d').drawImage(bmp, 0, 0, kanvas.width, kanvas.height);
    bmp.close();
    kanvas.dataset.terlukis = '1';
  }

  /* Antrean, bukan semuanya sekaligus.
     Mendekode satu foto 2296x4080 makan ratusan milidetik, dan satu layar
     berisi 14 ubin. Dilepas bersamaan, keempat belasnya berebut thread yang
     sama dan tabnya membeku beberapa detik: tombol tidak menjawab, guliran
     tersendat. Terukur di uji e2e, halaman berhenti membalas selama lebih
     dari 20 detik. Tiga sekaligus sudah cukup mengisi layar tanpa terasa. */
  const SEKALIGUS = 3;
  const antre = [];
  let jalan = 0;

  function pompa() {
    while (jalan < SEKALIGUS && antre.length) {
      const { kanvas, file } = antre.shift();
      // Ubin yang keluar layar selagi mengantre dilewati: menggulir cepat
      // melewati seribu ubin tidak boleh meninggalkan seribu pekerjaan yang
      // hasilnya tidak akan dilihat siapa pun.
      if (kanvas.dataset.antre !== '1') continue;
      delete kanvas.dataset.antre;
      jalan++;
      lukis(kanvas, file).finally(() => { jalan--; pompa(); });
    }
  }

  const pengamat = new IntersectionObserver((entri) => {
    for (const e of entri) {
      const kanvas = e.target.querySelector('canvas');
      const b = berkas.get(e.target.dataset.nama);
      if (!kanvas || !b) continue;
      if (e.isIntersecting) {
        if (!kanvas.dataset.terlukis && !kanvas.dataset.antre) {
          kanvas.dataset.antre = '1';
          antre.push({ kanvas, file: b.file });
          pompa();
        }
      } else if (kanvas.dataset.antre) {
        delete kanvas.dataset.antre;
      } else if (kanvas.dataset.terlukis) {
        // Mengubah lebar kanvas melepas buffernya. Tanpa ini, menggulir
        // sepuluh ribu ubin berarti sepuluh ribu kanvas terisi yang tidak
        // pernah dilihat lagi.
        kanvas.width = kanvas.width;
        delete kanvas.dataset.terlukis;
      }
    }
  }, { root: null, rootMargin: '400px 0px' });

  // ------------------------------------------------------------- kisi
  // Batas ubin yang digambar sekaligus. Di atas ini daftarnya dipotong dan
  // sisanya cukup dihitung: dua puluh ribu <figure> membuat halaman berat
  // padahal tidak ada yang memeriksa gambar ke-6.000 satu per satu.
  const MAKS_UBIN = 600;
  let saring = 'semua';

  function gambarUlang() {
    const semua = gambarTerdaftar();
    const berlabel = semua.filter(g => g.punyaAnotasi);
    $('ug-n-semua').textContent = semua.length;
    $('ug-n-anotasi').textContent = berlabel.length;
    $('ug-n-tanpa').textContent = semua.length - berlabel.length;

    const tampil = saring === 'anotasi' ? berlabel
                 : saring === 'tanpa' ? semua.filter(g => !g.punyaAnotasi)
                 : semua;

    const kisi = $('ug-kisi');
    pengamat.disconnect();
    kisi.replaceChildren();
    // Kotak kosong pratinjau. Pilihan bisa TAK KOSONG walau kisinya kosong:
    // .zip dan video memang TAK di-thumbnail (dibongkar/diekstrak di server),
    // jadi memilih SATU arsip menyisakan kisi gambar kosong. Dulu kasus itu
    // menampilkan "Tidak ada yang cocok" yang terbaca seperti GAGAL — padahal
    // arsipnya sudah siap, dan orang jadi mengira unggahannya tak berjalan.
    // Sekarang bedakan: tak ada gambar TAPI ada arsip/video -> pesan menenangkan
    // + langkah berikutnya; gambar ada tapi tersaring -> "pilih tab lain".
    const kosong = $('ug-kosong');
    kosong.hidden = tampil.length > 0;
    if (!kosong.hidden) {
      const nArsip = [...berkas.values()].filter(b => b.arsip).length;
      const nVideo = [...berkas.values()].filter(b => b.video).length;
      if (semua.length === 0 && (nArsip || nVideo)) {
        const apa = [];
        if (nArsip) apa.push(`${nArsip} arsip .zip`);
        if (nVideo) apa.push(`${nVideo} video`);
        kosong.innerHTML =
          `<b>${apa.join(' dan ')} siap diunggah</b>`
          + 'Arsip dan video tak perlu pratinjau. Isinya dibongkar/diekstrak '
          + 'di server. Klik <b>“Simpan dan lanjutkan &rarr;”</b> di atas, lalu '
          + '<b>Unggah</b>, untuk memprosesnya.';
      } else {
        kosong.innerHTML =
          '<b>Tidak ada yang cocok</b>'
          + 'Tidak ada berkas pada saringan ini. Pilih tab lain.';
      }
    }

    const tpl = $('ug-ubin-tpl');
    for (const g of tampil.slice(0, MAKS_UBIN)) {
      const el = tpl.content.firstElementChild.cloneNode(true);
      el.dataset.nama = g.nama;
      el.dataset.anotasi = g.punyaAnotasi ? 'ya' : 'tidak';
      el.querySelector('.ug-tanda').hidden = !g.punyaAnotasi;
      const cap = el.querySelector('.ug-nama');
      cap.textContent = g.nama.split('/').pop();
      cap.title = g.nama;
      el.querySelector('.ug-buang').onclick = () => buang(g.nama);
      kisi.appendChild(el);
      pengamat.observe(el);
    }
    if (tampil.length > MAKS_UBIN) {
      const sisa = document.createElement('div');
      sisa.className = 'ug-lebih';
      sisa.textContent = (tampil.length - MAKS_UBIN).toLocaleString('id-ID')
        + ' gambar lagi tidak digambar di sini. Semuanya tetap ikut diunggah.';
      kisi.appendChild(sisa);
    }

    const arsip = [...berkas.values()].filter(b => b.arsip).length;
    const video = [...berkas.values()].filter(b => b.video).length;
    const ket = document.querySelector('.ug-bilah-ket b');
    if (ket) {
      const bagian = [`${semua.length} gambar`];
      if (arsip) bagian.push(`${arsip} arsip .zip`);
      if (video) bagian.push(`${video} video`);
      ket.textContent = bagian.join(' dan ') + ' siap diunggah';
    }
    // Panel pilihan kerapatan hanya relevan kalau ada video yang akan diekstrak.
    const opsi = $('ug-video-opsi');
    if (opsi) {
      opsi.hidden = video === 0;
      const n = $('ug-video-n');
      if (n) n.textContent = String(video);
      if (video) segarkanHintKerapatan();   // tampilkan perkiraan jumlah segera
    }
  }

  function buang(nama) {
    const b = berkas.get(nama);
    berkas.delete(nama);
    // Anotasinya ikut dibuang. Ditinggal, ia terkirim sebagai label yatim
    // yang menunjuk gambar yang tidak pernah ada di sana.
    if (b && b.gambar) {
      const s = stem(nama);
      for (const [k, v] of [...berkas]) {
        if (!v.gambar && !v.arsip && stem(k) === s) berkas.delete(k);
      }
    }
    if (!berkas.size) { keTahap(1); return; }
    gambarUlang();
  }

  // ------------------------------------------------------------- tag
  function gambarTag() {
    const kotak = $('ug-tag-kotak');
    kotak.querySelectorAll('.ug-tag').forEach(e => e.remove());
    const input = $('ug-tag-input');
    for (const t of tag) {
      const el = document.createElement('span');
      el.className = 'ug-tag';
      el.textContent = t;
      const x = document.createElement('button');
      x.type = 'button';
      x.setAttribute('aria-label', 'Hapus tag ' + t);
      x.textContent = '×';
      x.onclick = () => { tag.splice(tag.indexOf(t), 1); gambarTag(); };
      el.appendChild(x);
      kotak.insertBefore(el, input);
    }
  }

  function tambahTag(teks) {
    for (const bagian of String(teks).split(',')) {
      const t = bagian.trim().slice(0, 40);
      if (t && !tag.includes(t) && tag.length < 20) tag.push(t);
    }
    gambarTag();
  }

  // ------------------------------------------------------------- kirim
  // Ukuran manusiawi, dipakai di penghitung unggahan.
  const mb = (b) => b >= 1e9 ? (b / 1e9).toFixed(1) + ' GB'
                  : b >= 1e6 ? (b / 1e6).toFixed(1) + ' MB'
                  : Math.max(1, Math.round(b / 1e3)) + ' KB';
  const jam = (d) => d >= 60 ? `${Math.floor(d / 60)} mnt ${Math.round(d % 60)} dtk`
                             : `${Math.max(1, Math.round(d))} dtk`;

  /* Satu berkas, lewat XMLHttpRequest — BUKAN fetch. Dua alasan, keduanya
     langsung menyentuh keluhan "tidak ada progress":

     1. xhr.upload.onprogress melaporkan BYTE yang sudah terkirim, jadi bilahnya
        bergerak DI DALAM satu berkas. Dengan fetch, satu foto 3 MB di jaringan
        kantor membuat bilah beku beberapa detik per berkas — terlihat seperti
        macet, bukan mengunggah.
     2. fetch tidak punya batas waktu; koneksi yang menggantung (bukan putus
        bersih) menahannya selamanya. xhr.timeout memberi lantai. */
  function kirimBerkas(url, file, onByte) {
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', url);
      // Timeout diskalakan ke UKURAN berkas, bukan 2 menit mati: video bisa
      // beberapa GB dan butuh belasan menit di jaringan kantor. Anggap lantai
      // laju sangat pelan 250 KB/dtk (2,8 GB ~ 3 jam batas atas), minimum 2
      // menit untuk berkas kecil. onprogress tetap mengabari lajunya.
      xhr.timeout = Math.max(120000, Math.ceil((file.size || 0) / 250000) * 1000);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onByte) onByte(e.loaded);
      };
      xhr.onload = () => {
        if (xhr.status === 401) {
          location.href = '/login';
          resolve({ ok: false, putus: false, error: 'sesi habis' });
          return;
        }
        let j = {};
        try { j = JSON.parse(xhr.responseText); } catch (e) { /* bukan JSON */ }
        if (typeof j.ok === 'boolean') { resolve(j); return; }
        const ok = xhr.status >= 200 && xhr.status < 300;
        resolve({ ok, error: ok ? null : (j.detail || 'galat ' + xhr.status) });
      };
      // status 0 = jaringan putus / CORS / dibatalkan. `putus` membedakannya
      // dari penolakan server (HTTP 4xx): yang pertama harus MENGHENTIKAN
      // unggahan dan menawarkan sambung ulang, yang kedua cuma satu berkas
      // yang dilewati.
      xhr.onerror = () => resolve({ ok: false, putus: true, error: 'jaringan terputus' });
      xhr.ontimeout = () => resolve({ ok: false, putus: true, error: 'waktu habis' });
      xhr.send(file);
    });
  }

  // FPS (frame per detik) yang dipilih slider, gaya Roboflow. Langkah diskret
  // yang "bulat" supaya enak dibaca; default 2 fps (index 3) -- seimbang untuk
  // olahraga/gerak cepat. Geser kiri = fps rendah (sedikit), kanan = fps tinggi.
  const FPS_STEPS = [0.25, 0.5, 1, 2, 3, 5, 8];
  function fpsDipilih() {
    const s = $('ug-fps');
    const i = s ? Math.max(0, Math.min(FPS_STEPS.length - 1, Number(s.value))) : 3;
    return FPS_STEPS[i];
  }

  // Perkiraan jumlah frame untuk satu video `dur` detik pada fps terpilih --
  // rumus & penjepit SAMA dengan server (durasi * fps, dijepit [3, 5000]).
  function targetFrameJS(dur, fps) {
    if (!(dur > 0)) return 0;
    return Math.min(5000, Math.max(3, Math.round(dur * fps)));
  }

  // Baca durasi video di browser (loadedmetadata) supaya hint bisa menyebut
  // PERKIRAAN jumlah gambar NYATA, bukan sekadar "per 1 menit". Gagal/takbisa
  // baca -> durasi 0, hint jatuh ke perkiraan per menit.
  function muatDurasi(b) {
    if (!b || !b.file || b.durasi !== undefined) return;
    b.durasi = 0;
    try {
      const v = document.createElement('video');
      v.preload = 'metadata';
      const url = URL.createObjectURL(b.file);
      v.onloadedmetadata = () => {
        b.durasi = (isFinite(v.duration) && v.duration > 0) ? v.duration : 0;
        URL.revokeObjectURL(url);
        segarkanHintKerapatan();
      };
      v.onerror = () => URL.revokeObjectURL(url);
      v.src = url;
    } catch (e) { /* abai: hint pakai perkiraan per menit */ }
  }

  // Beri tahu user berapa gambar yang akan diekstrak pada posisi slider
  // sekarang: total dari durasi video yang distaging kalau sudah terbaca, atau
  // perkiraan "per 1 menit" sebagai cadangan. Terpanggil tiap slider digeser
  // dan tiap durasi sebuah video selesai dibaca.
  function segarkanHintKerapatan() {
    const h = $('ug-preset-hint');
    if (!h) return;
    const fps = fpsDipilih();
    const det = 1 / fps;
    const tiap = det >= 1
      ? `${Math.round(det)} dtk`
      : `${det.toFixed(2).replace(/\.?0+$/, '').replace('.', ',')} dtk`;
    const fpsTxt = `${String(fps).replace('.', ',')} fps (1 frame tiap ${tiap})`;
    const ekor = ' Hanya frame yang benar-benar rusak (smear ekstrem/blank) dibuang; frame gerak-cepat & mirip tetap disimpan. Resolusi asli, maks 5000/video.';
    const vids = [...berkas.values()].filter((b) => b.video);
    const berDurasi = vids.filter((b) => b.durasi > 0);
    if (berDurasi.length) {
      const total = berDurasi.reduce((s, b) => s + targetFrameJS(b.durasi, fps), 0);
      h.textContent = `${fpsTxt}: sampai ≈ ${total} gambar`
        + (vids.length > 1 ? ` dari ${vids.length} video` : '')
        + (berDurasi.length < vids.length ? ' (sebagian durasi belum terbaca)' : '')
        + '.' + ekor;
    } else {
      h.textContent = `${fpsTxt}: video 1 mnt ≈ ${targetFrameJS(60, fps)} gambar.` + ekor;
    }
  }
  if ($('ug-fps')) {
    $('ug-fps').addEventListener('input', segarkanHintKerapatan);
    segarkanHintKerapatan();
  }

  // Spanduk status ekstraksi video DI LATAR. Dibaca dari /api/ekstrak/status,
  // jadi muncul lagi otomatis saat halaman DIBUKA walau tab sebelumnya ditutup
  // di tengah ekstraksi -- user selalu tahu masih jalan / selesai / gagal.
  let _ekstrakTimer = null;
  function pantauEkstrak() {
    const box = $('ug-ekstrak');
    if (!box) return;
    send('/api/ekstrak/status?ds=' + encodeURIComponent(PROJEK)).then((j) => {
      if (!j || !j.ok) return;
      const st = j.status || {};
      const baris = Object.keys(st).map((v) => {
        const r = st[v] || {};
        const nm = `<span class="ug-ekstrak-nama">${esc(v)}</span>`;
        if (r.keadaan === 'jalan') {
          const n = r.sudah != null ? ` · ${Number(r.sudah).toLocaleString('id-ID')} frame` : '';
          return `<div class="ug-ekstrak-baris ug-ekstrak-jalan"><span class="ug-ekstrak-spin">`
            + `</span><span>${nm} sedang diekstrak jadi gambar${n}…</span></div>`;
        }
        // Baris yang SUDAH kelar (selesai/gagal) bisa ditutup supaya tak
        // menetap selamanya tiap halaman dibuka. Yang 'jalan' tanpa tombol.
        const tutup = `<button type="button" class="ug-ekstrak-tutup"`
          + ` data-video="${esc(v)}" title="Sembunyikan" aria-label="Sembunyikan">×</button>`;
        if (r.keadaan === 'selesai') {
          const n = r.n != null ? ` · ${Number(r.n).toLocaleString('id-ID')} gambar` : '';
          return `<div class="ug-ekstrak-baris ug-ekstrak-ok">`
            + `<span class="ug-ekstrak-ikon">✓</span><span>${nm} selesai${n}</span>`
            + tutup + `</div>`;
        }
        if (r.keadaan === 'gagal') {
          return `<div class="ug-ekstrak-baris ug-ekstrak-gagal">`
            + `<span class="ug-ekstrak-ikon">✕</span><span>${nm} gagal`
            + (r.galat ? ` — ${esc(r.galat)}` : '') + `</span>` + tutup + `</div>`;
        }
        return '';
      }).filter(Boolean);
      if (!baris.length) { box.hidden = true; return; }
      box.innerHTML = baris.join('');
      box.hidden = false;
      const adaJalan = Object.values(st).some((r) => r && r.keadaan === 'jalan');
      clearTimeout(_ekstrakTimer);
      if (adaJalan) _ekstrakTimer = setTimeout(pantauEkstrak, 3000);
    }).catch(() => {});
  }
  // Klik ✕ pada baris yang sudah kelar: buang rekamannya di server supaya tak
  // muncul lagi, lalu hilangkan barisnya. Delegasi sekali, bukan per render.
  (function pasangTutupEkstrak() {
    const box = $('ug-ekstrak');
    if (!box) return;
    box.addEventListener('click', (e) => {
      const b = e.target.closest('.ug-ekstrak-tutup');
      if (!b) return;
      const vid = b.dataset.video || '';
      const row = b.closest('.ug-ekstrak-baris');
      if (row) row.remove();
      if (!box.querySelector('.ug-ekstrak-baris')) box.hidden = true;
      send('/api/ekstrak/tutup?ds=' + encodeURIComponent(PROJEK)
        + '&video=' + encodeURIComponent(vid), { method: 'POST' }).catch(() => {});
    });
  })();
  // Saat halaman dibuka: tampilkan status ekstraksi yang mungkin masih jalan
  // dari kunjungan sebelumnya (tab ditutup di tengah proses).
  if ($('ug-ekstrak')) pantauEkstrak();

  // Nama berkas yang SUDAH berhasil, bertahan lintas percobaan: "Coba lagi
  // sisanya" tidak mengirim ulang yang sudah masuk.
  const berhasil = new Set();

  async function kirim(hanyaSisa) {
    const semua = [...berkas.values()]
      .filter((b) => !(hanyaSisa && berhasil.has(b.nama)));
    const namaBatch = ($('ug-nama-batch').value || '').trim();
    batal = false;
    keTahap(3);
    $('ug-unggah-nama').textContent = namaBatch || PROJEK;
    $('ug-lanjut').hidden = true;
    $('ug-coba-lagi').hidden = true;
    $('ug-batal').hidden = false;

    // Progres.mulai menghapus isi wadahnya, jadi tidak boleh ada rujukan ke
    // bilah di dalamnya yang disimpan lebih dulu.
    const pr = Progres.mulai('Mengunggah ke ' + PROJEK, { di: $('ug-progres-jalur') });

    // /tambah bekerja pada dataset yang SEDANG dibuka sesi ini, jadi projeknya
    // harus dibuka lebih dulu. Dilakukan sekali di sini, bukan per berkas.
    if (BERISI) {
      pr.taktentu('Membuka dataset…');
      const buka = await post('/setsrc?path=' + encodeURIComponent(PATH));
      if (!buka.ok) { pr.gagal(buka.error || 'Gagal membuka dataset'); return; }
    }

    // Bilah bergerak per-BYTE, bukan per-berkas: total byte seluruh pilihan
    // jadi penyebutnya, dan `byteKirim` menghitung yang sudah tuntas.
    const byteTotal = semua.reduce((s, b) => s + (b.file.size || 0), 0) || 1;
    let byteKirim = 0, selesai = 0, gagal = 0;
    const arsip = [];
    const video = [];
    const fps = fpsDipilih();
    const t0 = Date.now();

    const lukisBilah = (byteSekarang, ke) => {
      const total = byteKirim + byteSekarang;
      const dtk = (Date.now() - t0) / 1000;
      const laju = dtk > 0.3 ? total / dtk : 0;
      const sisa = laju > 0 ? (byteTotal - total) / laju : 0;
      pr.set(total / byteTotal,
        `${ke.toLocaleString('id-ID')} dari ${semua.length.toLocaleString('id-ID')} berkas`
        + ` · ${mb(total)} / ${mb(byteTotal)}`
        + (laju > 0 ? ` · ${mb(laju)}/dtk · sisa ${jam(sisa)}` : '')
        + (gagal ? ` · ${gagal} gagal` : ''));
    };

    for (const b of semua) {
      if (batal) { pr.gagal(`Dibatalkan setelah ${selesai} berkas`); return; }
      // Video SELALU lewat /upload (lalu /ekstrak): /tambah tak bisa
      // mengekstrak. Berkas lain di projek berisi tetap lewat /tambah supaya
      // tata letaknya terjaga.
      const url = (BERISI && !b.video)
        ? '/tambah?name=' + encodeURIComponent(b.nama)
        : '/upload?ds=' + encodeURIComponent(PROJEK)
          + '&name=' + encodeURIComponent(b.nama);
      const j = await kirimBerkas(url, b.file,
        (byte) => lukisBilah(byte, selesai + 1));

      // Jaringan putus: BERHENTI di sini. Versi lama membiarkan sisa berkas
      // gagal secepat kilat lalu tetap lanjut memindai dataset — unggahan
      // "selesai" dengan ribuan berkas hilang tanpa satu pun tanda. Sekarang
      // yang sudah masuk diamankan, dan sisanya bisa disambung.
      if (j.putus) {
        pr.gagal(`Terputus di ${selesai.toLocaleString('id-ID')} dari `
          + `${semua.length.toLocaleString('id-ID')} berkas. Periksa koneksi, `
          + 'lalu Coba lagi sisanya.');
        $('ug-batal').hidden = true;
        $('ug-coba-lagi').hidden = false;
        return;
      }

      selesai++;
      byteKirim += (b.file.size || 0);
      if (j.ok) {
        berhasil.add(b.nama);
        if (j.arsip) arsip.push(j.name);
        if (j.video) video.push(j.name);
      } else { gagal++; if (gagal <= 2) toast(b.nama + ': ' + j.error); }
      lukisBilah(0, selesai);
    }

    if (selesai <= gagal) { pr.gagal('Semua berkas gagal terkirim'); return; }

    for (const nama of arsip) {
      // Persentasenya memang tidak diketahui, tetapi bahwa ia masih berjalan
      // itu diketahui, dan justru itu yang perlu terlihat.
      pr.taktentu('Membongkar ' + nama + '… (berkas besar perlu waktu)');
      try {
        const j = await post('/unzip?ds=' + encodeURIComponent(PROJEK) +
                             '&name=' + encodeURIComponent(nama));
        if (!j.ok) { pr.gagal('Gagal membongkar: ' + j.error); return; }
      } catch (e) { pr.gagal('Gagal menghubungi server saat membongkar'); return; }
    }

    // Video diekstrak jadi frame DI LATAR di server: prosesnya lanjut walau tab
    // ditutup, dan videonya dibuang server setelah frame selamat. Di sini cukup
    // MEMULAI tiap ekstraksi; progres/hasilnya dipantau spanduk #ug-ekstrak
    // (pantauEkstrak), yang juga muncul lagi otomatis saat halaman dibuka ulang.
    let adaEkstrak = false;
    for (let i = 0; i < video.length; i++) {
      const nama = video[i];
      pr.taktentu(`Memulai ekstraksi ${nama}… (${i + 1}/${video.length})`);
      try {
        const j = await post('/ekstrak?ds=' + encodeURIComponent(PROJEK)
          + '&name=' + encodeURIComponent(nama)
          + '&fps=' + encodeURIComponent(fps));
        if (j.ok) adaEkstrak = true;
        else toast('Gagal memulai ekstraksi ' + nama + ': ' + j.error);
      } catch (e) { toast('Gagal menghubungi server saat memulai ekstraksi'); }
    }

    pr.taktentu('Memindai isi dataset…');
    // /useupload membuka folder unggahan sebagai dataset; untuk yang sudah
    // terbuka cukup dipindai ulang supaya gambar barunya ikut terhitung.
    let buka = BERISI
      ? await post('/rescan')
      : await post('/useupload?ds=' + encodeURIComponent(PROJEK));
    if (!buka.ok) {
      // Unggahan video-SAJA ke projek kosong: saat ini belum ada satu frame pun
      // (ekstraksi baru DIMULAI di latar), jadi "tidak ada gambar terbaca" itu
      // WAJAR -- bukan kegagalan. Lanjut ke status ekstraksi; frame menyusul.
      // Tanpa ini, unggahan video pertama ke projek kosong selalu dilaporkan
      // "gagal" padahal videonya terunggah dan ekstraksinya jalan.
      if (adaEkstrak) {
        buka = { ok: true, n: 0, masuk_dataset: 0 };
      } else {
        pr.gagal(buka.error || 'Gagal memindai dataset');
        return;
      }
    }

    if (namaBatch || tag.length) {
      pr.taktentu('Menandai gambar…');
      try {
        // tanpa_batch: yang baru masuk adalah yang belum punya nama batch.
        // Isi .zip tidak pernah dilihat peramban, jadi menandainya lewat
        // daftar nama yang dikirim dari sini akan melewatkan semuanya.
        await send('/api/tag/pasang', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tanpa_batch: true, batch: namaBatch, tambah: tag }),
        });
      } catch (e) { toast('Gambar terunggah, tapi tagnya gagal disimpan'); }
    }

    // Berapa gambar berlabel yang otomatis masuk dataset. Dihitung server di
    // /useupload (sesudah split diratakan, di atas kunci final): nol kalau yang
    // diunggah bukan dataset jadi (tak ada data.yaml) atau masih mentah.
    const totalMasuk = buka.masuk_dataset || 0;
    const nTotal = buka.n || 0;
    const sisaMentah = Math.max(0, nTotal - totalMasuk);

    // Jelas menyebut "selesai" dan jumlahnya: keluhan aslinya justru tidak
    // tahu unggahannya tuntas atau belum. Video diekstrak DI LATAR, jadi di sini
    // cukup diberi tahu; jumlah frame menyusul di spanduk status.
    const ekstra = adaEkstrak
      ? ' · video sedang diekstrak jadi frame di latar (lihat status di atas)'
      : '';
    const pesan = gagal
      ? `Selesai, ${(berhasil.size).toLocaleString('id-ID')} terkirim, `
        + `${gagal} gagal · ${nTotal.toLocaleString('id-ID')} gambar di dataset`
        + ekstra
      : `Selesai, ${nTotal.toLocaleString('id-ID')} gambar di dataset` + ekstra;
    pr.selesai(pesan);
    if (adaEkstrak) pantauEkstrak();        // mulai pantau ekstraksi latar
    $('ug-batal').hidden = true;
    $('ug-coba-lagi').hidden = true;
    $('ug-lanjut').hidden = false;

    const ket = $('ug-unggah-ket');
    const lanjut = $('ug-lanjut');
    // Mengarahkan langkah berikut sesuai isi unggahan:
    //  - semua gambar sudah berlabel (ekspor Roboflow lengkap) -> sudah jadi
    //    dataset, tak perlu dibagi; berikutnya buat Versi lalu latih.
    //  - masih ada yang mentah (gambar polos / frame video) -> dibagi dulu ke
    //    pelabel; yang berlabel sudah aman di dataset.
    if (totalMasuk > 0 && sisaMentah === 0) {
      lanjut.href = '/versi?ds=' + encodeURIComponent(PROJEK);
      lanjut.textContent = 'Buat versi →';
      ket.textContent = `${totalMasuk.toLocaleString('id-ID')} gambar sudah berlabel, `
        + 'otomatis masuk dataset. Berikutnya: buat Versi lalu latih model.';
    } else if (totalMasuk > 0) {
      lanjut.href = '/bagi?ds=' + encodeURIComponent(PROJEK);
      lanjut.textContent = 'Bagi tugas →';
      ket.textContent = `${totalMasuk.toLocaleString('id-ID')} gambar berlabel otomatis masuk dataset; `
        + `${sisaMentah.toLocaleString('id-ID')} gambar belum berlabel, bagi ke pelabel dulu.`;
    } else {
      lanjut.href = '/bagi?ds=' + encodeURIComponent(PROJEK);
      lanjut.textContent = 'Bagi tugas →';
      ket.textContent = 'Selesai. Berikutnya: bagi gambarnya ke anggota tim.';
    }

    if ((buka.peringatan || []).length) {
      // Ditahan di layar, bukan ditoast: isinya justru yang menentukan apakah
      // datasetnya benar, dan toast hilang sebelum sempat dibaca. Diutamakan
      // di atas arahan langkah karena menyangkut kebenaran dataset.
      ket.innerHTML = '<b>Perlu dicek:</b><br>'
        + buka.peringatan.map(p => '· ' + p).join('<br>');
    }
  }

  // ------------------------------------------------------------- server
  /* Menyalin dari folder di server ini, bukan membukanya di tempat.
     Bedanya menentukan: membuka di tempat berarti menyunting mengubah dataset
     aslinya, yang mungkin dipakai orang lain atau proyek lain di mesin yang
     sama. Di sini isinya disalin dulu ke projek ini.

     Ukurannya disurvei lebih dulu dan tombolnya berubah jadi konfirmasi.
     Menyalin beberapa GB tanpa pemberitahuan bukan kejutan yang menyenangkan,
     dan sebuah dialog confirm() menutupi persis angka yang perlu dibaca. */
  let survei = null;

  // Projek VIDEO: dataset KLIP jadi -> versi aksi lewat /api/aksi/impor-path.
  // Satu klik (tak perlu survei dua tahap): server membongkar (kalau .zip) +
  // mendaftarkan apa adanya, progres dipoll dari /api/aksi/impor/kemajuan
  // (kunci sama dengan impor .zip di halaman Label Aksi).
  async function imporServerAksi(path) {
    const tombol = $('ug-server-impor');
    tombol.disabled = true;
    keTahap(3);
    $('ug-unggah-nama').textContent = PROJEK;
    $('ug-lanjut').hidden = true;
    $('ug-batal').hidden = true;
    const pr = Progres.mulai('Memuat dataset klip ke ' + PROJEK,
                             { di: $('ug-progres-jalur') });
    pr.taktentu('Menyiapkan…');
    const pantau = setInterval(async () => {
      let k;
      try {
        k = await (await fetch('/api/aksi/impor/kemajuan?ds='
                               + encodeURIComponent(PROJEK))).json();
      } catch (e) { return; }
      if (!k || !k.ok) return;
      if (k.galat) {
        clearInterval(pantau); pr.gagal(k.galat); tombol.disabled = false; return;
      }
      if (k.selesai) {
        clearInterval(pantau);
        pr.selesai(`Versi v${k.nomor || ''} dibuat di ${PROJEK}`);
        $('ug-lanjut').hidden = false;
        $('ug-unggah-ket').textContent =
          'Dataset klip terdaftar sebagai versi. Buka Label Aksi untuk melatih.';
        return;
      }
      pr.set((k.persen != null) ? (k.persen / 100) : 0, k.fase_nama || 'Memproses');
    }, 700);
    try {
      const j = await send('/api/aksi/impor-path?ds=' + encodeURIComponent(PROJEK),
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path }) });
      if (!j.ok) {
        clearInterval(pantau); pr.gagal(j.error || 'gagal'); tombol.disabled = false;
      }
    } catch (e) {
      clearInterval(pantau); pr.gagal('Gagal menghubungi server'); tombol.disabled = false;
    }
  }

  async function imporServer() {
    const tombol = $('ug-server-impor');
    const jalur = $('ug-server-jalur');
    const path = ($('ug-server-path').value || '').trim();
    if (!path) { toast('Path masih kosong'); return; }

    // Projek video: daftarkan dataset klip jadi sebagai versi (impor aksi),
    // bukan jalur salin-gambar dua-tahap.
    if (JENIS === 'video') { return imporServerAksi(path); }

    if (!survei || survei.path !== path) {
      const pr = Progres.mulai('Memeriksa isi folder', { di: jalur });
      pr.taktentu('Menghitung berkas di ' + path);
      let s;
      try {
        s = await (await fetch('/api/impor/survei?path='
                               + encodeURIComponent(path))).json();
      } catch (e) { pr.gagal('Gagal menghubungi server'); return; }
      if (!s.ok) { pr.gagal(s.error); return; }
      const mb = s.bytes / 1048576;
      const ukuran = mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB'
                                : mb.toFixed(0) + ' MB';
      pr.selesai(`${s.berkas.toLocaleString('id-ID')} berkas · ${ukuran}`);
      survei = { path, ...s };
      tombol.textContent = `Salin ${s.berkas.toLocaleString('id-ID')} berkas (${ukuran})`;
      tombol.dataset.siap = '1';
      return;
    }

    tombol.disabled = true;
    // Pindah ke tahap 3 (Unggah) DULU dan render progres di wadah tahap 3
    // (ug-progres-jalur), persis seperti unggah berkas (kirim). Kalau progres
    // dirender di panel server (tahap 1) lalu keTahap(3) baru dipanggil di
    // AKHIR, bilah yang dilihat orang di tahap 3 bukan bilah yang diperbarui —
    // jadi tampak macet di "Menyiapkan 0%" padahal salinannya sudah beres.
    keTahap(3);
    $('ug-unggah-nama').textContent = PROJEK;
    $('ug-lanjut').hidden = true;
    $('ug-batal').hidden = true;
    const pr = Progres.mulai('Menyalin ke ' + PROJEK, { di: $('ug-progres-jalur') });
    // Sama seperti berkas dari laptop: projek yang sudah berisi harus lewat
    // jalur yang menghormati tata letaknya. /impor menyalin folder apa adanya
    // ke akar projek, dan pada dataset bersplit itu menumpahkan isinya di luar
    // train/valid/test.
    if (BERISI) {
      pr.taktentu('Membuka dataset…');
      const buka = await post('/setsrc?path=' + encodeURIComponent(PATH));
      if (!buka.ok) { pr.gagal(buka.error || 'Gagal membuka dataset');
                      tombol.disabled = false; return; }
    }
    // Penyalinan berjalan di thread terpisah di server sementara permintaan
    // /impor menggantung sampai selesai, jadi kemajuannya ditanyakan lewat
    // permintaan terpisah. Tanpa ini, menyalin 22 ribu berkas tampak seperti
    // halaman yang macet.
    const pantau = setInterval(async () => {
      let k;
      try { k = await (await fetch('/api/impor/kemajuan')).json(); } catch (e) { return; }
      if (!k || !k.tahap) return;
      if (k.tahap === 'pindai') { pr.taktentu('Tersalin, memindai isinya…'); return; }
      if (k.tahap !== 'salin' || !k.total) return;
      pr.set(k.berkas / k.total,
             `${k.berkas.toLocaleString('id-ID')} dari ${k.total.toLocaleString('id-ID')} berkas`);
    }, 500);

    try {
      const j = await post(BERISI
        ? '/tambah/impor?path=' + encodeURIComponent(path)
        : '/impor?path=' + encodeURIComponent(path)
          + '&ds=' + encodeURIComponent(PROJEK));
      clearInterval(pantau);
      if (!j.ok) { pr.gagal(j.error); tombol.disabled = false; return; }
      pr.selesai(`${(j.n || 0).toLocaleString('id-ID')} gambar masuk ke ${PROJEK}`);
      $('ug-lanjut').hidden = false;
      $('ug-unggah-ket').textContent =
        'Salinan selesai. Berkasnya sekarang ada di ' + PROJEK + '.';
    } catch (e) {
      clearInterval(pantau);
      pr.gagal('Gagal menghubungi server saat menyalin');
      tombol.disabled = false;
    }
  }

  // ------------------------------------------------------------- pasang
  function pasangDrop(el) {
    el.addEventListener('dragover', (e) => {
      e.preventDefault();
      el.setAttribute('data-over', '');
    });
    el.addEventListener('dragleave', () => el.removeAttribute('data-over'));
    el.addEventListener('drop', async (e) => {
      e.preventDefault();
      el.removeAttribute('data-over');
      tambah(await dariDrop(e.dataTransfer));
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    keTahap(1);
    pasangDrop($('ug-drop'));
    pasangDrop($('ug-panggung'));

    // stopPropagation: tanpa itu kliknya menggelembung ke .ug-drop dan
    // pemilih berkas terbuka dua kali.
    //
    // Inputnya dikosongkan SEBELUM dibuka, bukan sesudah berkasnya diambil.
    // Mengosongkannya sesudah membuat File yang sudah kita pegang jadi kosong:
    // isinya masih tercatat, ukurannya nol, dan thumbnail-nya gagal dimuat
    // tanpa satu pesan galat pun. Dikosongkan di sini, memilih folder yang
    // sama dua kali tetap memicu `change`.
    const buka = (input) => (e) => {
      e.stopPropagation();
      input.value = '';
      input.click();
    };
    $('ug-pilih-berkas').onclick = buka($('ug-berkas'));
    $('ug-pilih-folder').onclick = buka($('ug-folder'));
    $('ug-tambah-berkas').onclick = buka($('ug-berkas'));
    $('ug-tambah-folder').onclick = buka($('ug-folder'));
    for (const id of ['ug-berkas', 'ug-folder']) {
      $(id).addEventListener('change', (e) => tambah([...e.target.files]));
    }

    $('ug-tab').addEventListener('change', (e) => {
      saring = e.target.value;
      for (const l of $('ug-tab').querySelectorAll('.seg-opt')) {
        l.toggleAttribute('data-on', l.contains(e.target));
      }
      gambarUlang();
    });

    // Kerapatan ekstraksi video kini slider FPS (#ug-fps); penanganannya ada
    // di dekat fpsDipilih()/segarkanHintKerapatan() di atas.

    const ti = $('ug-tag-input');
    ti.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ',') {
        e.preventDefault();
        tambahTag(ti.value);
        ti.value = '';
      } else if (e.key === 'Backspace' && !ti.value && tag.length) {
        tag.pop();
        gambarTag();
      }
    });
    ti.addEventListener('blur', () => { tambahTag(ti.value); ti.value = ''; });

    $('ug-simpan').onclick = () => kirim(false);
    // Sambung ulang: kirim HANYA yang belum berhasil (berhasil bertahan
    // lintas percobaan), jadi 5.000 dari 6.000 yang sudah masuk tidak diulang.
    $('ug-coba-lagi').onclick = () => kirim(true);
    $('ug-server-impor').onclick = imporServer;
    const jelajah = $('ug-server-jelajah');
    if (jelajah) {
      jelajah.onclick = async () => {
        toast('Dialog terbuka di layar server…');
        const j = await post('/pickdir');
        if (!j.ok) { toast(j.error); return; }
        // /pickdir membuka folder itu di tempat, bukan menyalin. Halaman ini
        // tentang mengisi projek, jadi yang benar setelahnya adalah gridnya.
        location.href = '/';
      };
    }
    $('ug-server-path').addEventListener('input', () => {
      if (JENIS === 'video') return;   // video: satu klik, tak ada survei dua tahap
      // Path berubah berarti hasil surveinya sudah bukan milik path itu lagi.
      survei = null;
      const b = $('ug-server-impor');
      delete b.dataset.siap;
      b.textContent = 'Salin';
    });
    for (const b of document.querySelectorAll('.ug-riwayat-path')) {
      b.onclick = () => {
        $('ug-server-path').value = b.dataset.path;
        $('ug-server-path').dispatchEvent(new Event('input'));
      };
    }
    for (const b of document.querySelectorAll('.ug-riwayat-lupa')) {
      b.onclick = async () => {
        await post('/lupakan-path?path=' + encodeURIComponent(b.dataset.path));
        b.closest('.ug-riwayat-baris').remove();
      };
    }
    $('ug-batal').onclick = () => { batal = true; };
    // Unggahan yang selesai lalu mendarat di grid tidak menjawab pertanyaan
    // berikutnya, yaitu siapa yang mengerjakan ini. Halaman bagi tugas yang
    // menjawabnya, jadi ke situ tujuannya.
    $('ug-lanjut').href = '/bagi?ds=' + encodeURIComponent(PROJEK);

    // Nama batch bawaan: tanggal dan jam hari ini. Kolom kosong membuat orang
    // mengarangnya sendiri tiap kali, dan yang dikarang jarang konsisten.
    const kini = new Date();
    $('ug-nama-batch').value = 'Unggahan ' + kini.toLocaleDateString('id-ID',
      { day: 'numeric', month: 'short', year: 'numeric' }) + ' '
      + String(kini.getHours()).padStart(2, '0') + '.'
      + String(kini.getMinutes()).padStart(2, '0');

    pasangUnggahHP();
  });

  /* Unggah dari HP: tombol meminta token (POST /api/unggah/token), lalu QR-nya
     digambar di peramban dari tautan yang dikembalikan — tanpa pustaka server,
     tanpa memanggil CDN (qrcode.min.js dibundel). Tautannya juga ditampilkan
     teks supaya bisa diketik kalau kamera QR ponsel tak tersedia. */
  function pasangUnggahHP() {
    const panel = $('ug-hp');
    if (!panel || typeof qrcode !== 'function') return;
    const tampil = $('ug-hp-tampil'), qrBox = $('ug-hp-qr'), kode = $('ug-hp-kode');
    const urlEl = $('ug-hp-url'), catatan = $('ug-hp-catatan'), st = $('ug-hp-status');

    const gambarQR = (url) => {
      // tipe 0 = ukuran otomatis dari panjang data; koreksi 'M' (15%) cukup
      // tahan terhadap layar pantulan tanpa membesarkan kode berlebihan.
      const qr = qrcode(0, 'M');
      qr.addData(url);
      qr.make();
      kode.innerHTML = qr.createSvgTag({ cellSize: 5, margin: 2, scalable: true });
    };

    const minta = async () => {
      st.textContent = 'Menyiapkan QR…';
      tampil.disabled = true;
      try {
        const r = await fetch('/api/unggah/token?ds='
          + encodeURIComponent(panel.dataset.ds || PROJEK), { method: 'POST' });
        const j = await r.json();
        if (!j || !j.ok) {
          st.textContent = (j && j.error) || 'gagal membuat QR';
          tampil.disabled = false;
          return;
        }
        gambarQR(j.url);
        urlEl.textContent = j.url;
        urlEl.href = j.url;
        // Server ini bersertifikat sendiri, jadi ponsel menampilkan peringatan
        // keamanan sekali — disebutkan di muka supaya tidak terlihat seperti
        // tautan rusak.
        catatan.textContent = 'Tautan berlaku 2 jam. Kalau ponsel menampilkan '
          + 'peringatan keamanan, pilih Lanjutkan (sertifikatnya milik server '
          + 'sendiri).';
        qrBox.hidden = false;
        tampil.hidden = true;
        st.textContent = '';
      } catch (e) {
        st.textContent = 'gagal menghubungi server';
        tampil.disabled = false;
      }
    };

    tampil.onclick = minta;
    $('ug-hp-baru').onclick = minta;
  }
})();
