/*
 * Halaman Versi: daftar versi + wizard enam langkah.
 *
 * Wizardnya bekerja pada SATU objek `resep` yang dikirim utuh ke server saat
 * Buat ditekan. Tidak ada keadaan yang disimpan di server di tengah jalan —
 * menutup wizard membuang seluruhnya, dan itu memang yang diinginkan: separuh
 * resep yang tertinggal di server adalah hal yang tidak ada yang bisa
 * membersihkannya.
 */
(() => {
  const el = (id) => document.getElementById(id);
  const isi = el('vs-isi');
  if (!isi) return;
  const ds = isi.dataset.ds || '';
  // Tombol hapus hanya untuk yang boleh mengelola. Templat sudah
  // menyembunyikan wizardnya untuk yang lain; ini mengikuti penanda
  // yang sama supaya tidak ada dua sumber kebenaran.
  const BOLEH = !!document.getElementById('wz');

  // ------------------------------------------------- daftar versi & detail
  const detail = el('vs-detail');
  const cincin = [...document.querySelectorAll('.vs-ring')];

  const mb = (b) => (b / 1073741824 >= 1
    ? (b / 1073741824).toFixed(1) + ' GB'
    : Math.round(b / 1048576) + ' MB');

  async function bukaVersi(n) {
    cincin.forEach((c) => c.toggleAttribute('data-on', c.dataset.nomor === String(n)));
    if (!detail) return;
    detail.innerHTML = '<div class="vs-detail-muat">memuat…</div>';
    const r = await fetch(`/api/versi/isi?nomor=${n}`).then((x) => x.json())
      .catch(() => null);
    if (!r || !r.ok) {
      detail.innerHTML = `<div class="vs-detail-muat">${(r && r.error) || 'gagal memuat'}</div>`;
      return;
    }
    // Kegagalan menggambar TIDAK boleh menyisakan "memuat…" yang menetap.
    // Itu yang terjadi selama ini: satu ReferenceError di dalam template
    // membuat panelnya menggantung tanpa batas, dan dari layar ia terbaca
    // sebagai server yang lambat — bukan sebagai kekeliruan yang harus
    // diperbaiki. Kalau gagal, katakan gagal.
    try {
      gambarDetail(r.versi, r.isi || {});
    } catch (e) {
      detail.innerHTML = '<div class="vs-detail-muat">gagal menggambar '
        + 'rincian versi ini. Muat ulang halaman; kalau tetap begini, '
        + 'sebutkan pesan ini: ' + (e && e.message ? e.message : e) + '</div>';
      throw e;          // tetap masuk konsol, supaya bisa dilacak
    }
  }

  // Warna kelas: rumus yang SAMA dengan kanvas (label.js) dan thumbnail
  // (render.py cls_color). Disalin, bukan diimpor, karena halaman ini tidak
  // memuat label.js — dan yang penting bukan berbagi kode melainkan berbagi
  // HASIL: satu kelas harus berwarna sama di kanvas, di grid, dan di sini.
  // Kalau berbeda, warna berhenti jadi penanda dan jadi hiasan.
  function hashKode(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return Math.abs(h);
  }
  const warnaKelas = (label) =>
    `hsl(${((hashKode(String(label)) % 997) / 997 * 360).toFixed(0)},62%,55%)`;

  // Satu angka desimal, format Indonesia. Tanpa ini panelnya menulis "1.203"
  // dan "48.7%" berdampingan: titik yang sama berarti ribuan di satu tempat
  // dan desimal di sebelahnya.
  const desimal = (x) => x.toLocaleString('id', {minimumFractionDigits: 1,
                                                 maximumFractionDigits: 1});

  /**
   * Objek per kelas sebagai bagan batang.
   *
   * Yang benar-benar ditanyakan orang saat melihat daftar ini bukan "berapa
   * botol", melainkan "dataset saya timpang tidak?" — karena itulah yang
   * menentukan model belajar dengan adil atau tidak. Deretan angka menjawab
   * pertanyaan pertama dan diam soal yang kedua: 12.031 lawan 1.204 harus
   * dibaca dua kali dan dibagi di kepala sebelum ketimpangannya terasa.
   *
   * Karena itu ada tiga hal di sini, bukan cuma batang yang lebih cantik:
   *   - panjang batang, supaya perbandingannya terbaca tanpa membaca angka;
   *   - persentase di samping jumlah, karena "berapa bagian dari seluruhnya"
   *     itulah yang menentukan;
   *   - rasio timpang (terbanyak : tersedikit) sebagai satu angka ringkas.
   *
   * Sampel negatif dipisah di bawah garis: ia bukan kelas, dan menaruhnya
   * dalam deret yang sama membuat persentase seluruh kelas ikut salah.
   */
  function baganKelas(kelas, negatif) {
    const total = kelas.reduce((a, [, c]) => a + c, 0);
    const maks = kelas[0][1];
    const min = kelas[kelas.length - 1][1];
    const rasio = min > 0 ? maks / min : 0;
    // Ambangnya sengaja longgar. Yang mau ditandai ketimpangan yang benar-
    // benar mengubah hasil latihan, bukan selisih wajar antarkelas.
    const timpang = kelas.length > 1 && rasio >= 3;
    const baris = kelas.map(([n, c]) => {
      const persen = total ? (c / total * 100) : 0;
      return `<div class="vk-baris" title="${n}: ${c.toLocaleString('id')} objek (${desimal(persen)}%)">
        <span class="vk-nama"><i style="background:${warnaKelas(n)}"></i>${n}</span>
        <span class="vk-bar"><i style="width:${(c / maks * 100).toFixed(1)}%;
          background:${warnaKelas(n)}"></i></span>
        <b class="vk-n">${c.toLocaleString('id')}</b>
        <em class="vk-persen">${desimal(persen)}%</em>
      </div>`;
    }).join('');
    const kaki = negatif
      ? `<div class="vk-baris vk-negatif" title="Gambar tanpa objek — sampel negatif yang disengaja">
           <span class="vk-nama"><i class="vk-kosong"></i>sampel negatif</span>
           <span class="vk-bar"></span>
           <b class="vk-n">${negatif.toLocaleString('id')}</b>
           <em class="vk-persen">gambar</em>
         </div>`
      : '';
    return `<div class="vs-bagian">
      <h4>Objek per kelas
        <span class="vk-total">${total.toLocaleString('id')} objek · ${kelas.length} kelas</span>
      </h4>
      <div class="vk-bagan">${baris}${kaki}</div>
      ${kelas.length > 1 ? `<p class="vk-rasio${timpang ? ' vk-timpang' : ''}">
        Terbanyak <b>${kelas[0][0]}</b> berbanding tersedikit
        <b>${kelas[kelas.length - 1][0]}</b> =
        <b>${desimal(rasio)}&times;</b>${timpang
          ? ' — cukup timpang. "Seimbangkan jumlah kelas" di langkah 5 memperkecil selisih ini.'
          : ' — cukup seimbang.'}</p>` : ''}
    </div>`;
  }

  function gambarDetail(v, isi) {
    // Ringkasan hasil pembuatan versi. HARUS lewat `v`: sebagai `hasil` polos
    // ia bukan variabel yang ada di mana pun, dan JavaScript menjawab itu
    // dengan ReferenceError di tengah menyusun template — sehingga baris
    // `detail.innerHTML = ...` di bawah tidak pernah tercapai dan panelnya
    // menetap di "memuat…" selamanya, tanpa satu pun pesan.
    const hasil = v.hasil || {};
    const j = isi.jumlah || v.jumlah || {};
    const tot = (j.train || 0) + (j.valid || 0) + (j.test || 0);
    const resep = v.resep || {};
    const rs = ((resep.pra || {}).resize) || {};
    const punyaHasil = !!(isi.contoh && isi.contoh.length);

    // Deretan contoh: hanya untuk versi yang benar-benar punya berkas hasil.
    // Versi lama cuma membekukan pembagian, jadi tidak ada gambar hasil untuk
    // ditunjukkan — dan deretan kosong yang selalu ada lebih membingungkan
    // daripada tidak ada deretan sama sekali.
    const contoh = punyaHasil
      ? `<div class="vs-bagian"><h4>${(isi.contoh.length)} contoh dari ${tot} gambar</h4>
           <div class="vs-strip">` +
        isi.contoh.map((n) =>
          `<img loading="lazy" src="/versi/gambar?nomor=${v.nomor}` +
          `&nama=${encodeURIComponent(n)}&s=200" alt="">`).join('') +
        '</div></div>'
      : '';

    const kelas = Object.entries(isi.per_kelas || {})
      .sort((a, b) => b[1] - a[1]);
    const barisKelas = kelas.length ? baganKelas(kelas, isi.negatif || 0) : '';

    // Resep yang disimpan hanya memuat yang DITIMPA pemakai; yang tidak
    // disebut memakai bawaan katalog dan tetap berjalan. Menampilkan isi
    // resep apa adanya karena itu membuat versi yang menjalankan 16 transform
    // tertulis "-". Yang ditampilkan harus yang BERJALAN.
    const langkah = (tahap) => {
      const d = resep[tahap] || {};
      const kat = (katalog && katalog[tahap]) || {};
      const nyala = Object.keys(kat).filter((k) => (
        d[k] !== undefined ? d[k].aktif !== false : kat[k].bawaan_aktif));
      if (nyala.length) return nyala.map((k) => kat[k].nama).join(', ');
      // Katalog belum sempat dimuat: tampilkan apa adanya daripada berbohong.
      const eks = Object.keys(d).filter((k) => d[k] && d[k].aktif !== false);
      return eks.length ? eks.join(', ') : '-';
    };

    detail.innerHTML = `
      <div class="vs-detail-atas">
        <b class="vs-nomor">v${v.nomor}</b>
        <span class="vs-detail-judul">${v.dibuat}</span>
        <span class="halus">oleh ${v.oleh}</span>
        <span class="spacer"></span>
        ${BOLEH ? `<button class="chip vs-hapus" type="button" data-nomor="${v.nomor}">Hapus</button>` : ''}
      </div>
      ${v.catatan ? `<p class="an-catatan">${v.catatan}</p>` : ''}

      <div class="vs-bagian"><h4>Unduh</h4>
        <div class="vs-unduh">
          <a class="chip" href="/ekspor?nomor=${v.nomor}&format=yolo-seg">YOLO-seg</a>
          <a class="chip" href="/ekspor?nomor=${v.nomor}&format=yolo">YOLO</a>
          <a class="chip" href="/ekspor?nomor=${v.nomor}&format=coco">COCO</a>
          <a class="chip" href="/ekspor?nomor=${v.nomor}&format=voc">VOC</a>
          <a class="chip" href="/ekspor?nomor=${v.nomor}&format=createml">CreateML</a>
          <a class="chip" href="/ekspor?nomor=${v.nomor}&format=yolo-seg&gambar=0">label saja</a>
        </div>
      </div>

      <div class="vs-bagian"><h4>Pembagian</h4>
        <div class="vs-split">
          <div class="vs-kotak vs-k-train"><span>TRAIN</span><b>${(j.train || 0).toLocaleString('id')}</b>
            <em>${tot ? Math.round((j.train || 0) / tot * 100) : 0}%</em></div>
          <div class="vs-kotak vs-k-valid"><span>VALID</span><b>${(j.valid || 0).toLocaleString('id')}</b>
            <em>${tot ? Math.round((j.valid || 0) / tot * 100) : 0}%</em></div>
          <div class="vs-kotak vs-k-test"><span>TEST</span><b>${(j.test || 0).toLocaleString('id')}</b>
            <em>${tot ? Math.round((j.test || 0) / tot * 100) : 0}%</em></div>
        </div>
        <p class="halus">Rasio yang diminta <b>${(v.rasio || '').replace(/,/g, ' / ')}</b>
          berlaku pada gambar SUMBER. Angka di atas sudah termasuk hasil
          augmentasi, dan augmentasi hanya menyentuh train, jadi porsi train
          memang lebih besar daripada rasio yang diketik.</p>
        <p class="halus">${v.berencana
          ? 'Dibelah dengan pemeriksaan isi gambar (anti-bocor): foto yang sama tidak berada di train sekaligus di valid.'
          : 'Dibelah cepat berdasarkan nama berkas; isi gambarnya tidak diperiksa.'}</p>
      </div>

      ${contoh}
      ${barisKelas}

      <div class="vs-bagian"><h4>Resep</h4>
        <div class="vs-tinjau">
          <div><span>Preprocessing</span><b>${langkah('pra')}</b></div>
          <div><span>Augmentasi</span><b>${langkah('aug')}</b></div>
          <div><span>Salinan per gambar</span><b>${(resep.volume || {}).per_gambar ?? '-'}</b></div>
          <div><span>Ukuran</span><b>${punyaHasil ? `${rs.lebar || 640}×${rs.tinggi || 640}` : '-'}</b></div>
          <div><span>Rasio diminta</span><b>${v.rasio || '-'}</b></div>
          ${hasil.jenis ? `<div><span>Format anotasi</span><b>${
            hasil.jenis === 'kotak' ? 'Kotak (deteksi)' : 'Poligon (segmentasi)'
          }</b></div>` : ''}
          ${isi.byte ? `<div><span>Ukuran di disk</span><b>${mb(isi.byte)}</b></div>` : ''}
        </div>
        ${hasil.dipulangkan ? `<p class="vs-pulang"><b>${hasil.dipulangkan} gambar</b> tidak ikut ke versi ini: bentuknya tidak bisa jadi mask di dataset poligon. Semuanya sudah dikembalikan ke kolom <b>Belum ditugaskan</b> di halaman Anotasi supaya ada yang membetulkannya. Anotasinya tidak dihapus; yang berubah hanya keanggotaan dataset.</p>` : ''}
      </div>`;

    const h = detail.querySelector('.vs-hapus');
    if (h) h.onclick = () => hapus(h.dataset.nomor);
  }

  async function hapus(n) {
    if (!confirm(`Hapus versi v${n}? Berkas hasilnya ikut terhapus permanen.`)) return;
    const r = await fetch(`/api/versi/hapus?nomor=${n}`, { method: 'POST' })
      .then((x) => x.json()).catch(() => ({ ok: false }));
    if (r.ok) location.reload();
    else alert(r.error || 'gagal menghapus');
  }

  cincin.forEach((c) => { c.onclick = () => bukaVersi(c.dataset.nomor); });
  if (cincin.length) {
    // Katalog dibutuhkan panel detail untuk menerjemahkan resep jadi nama
    // langkah, jadi ia dimuat di sini juga — bukan hanya saat wizard dibuka.
    fetch('/api/versi/katalog').then((r) => r.json()).then((k) => {
      if (k && k.ok) katalog = k;
      bukaVersi(cincin[0].dataset.nomor);
    }).catch(() => bukaVersi(cincin[0].dataset.nomor));
  }

  const wz = el('wz');
  if (!wz) return;                       // bukan pemilik projek

  // ------------------------------------------------------------- keadaan
  const resep = { pra: {}, aug: {}, fase: {}, volume: { per_gambar: 1 },
                  warna: { mode: 'bentuk' } };
  let katalog = null;
  let sumber = null;                     // hasil /api/versi/estimasi terakhir

  // ------------------------------------------------------------- langkah
  function buka(n) {
    wz.querySelectorAll('.wz-item').forEach((li) => {
      li.toggleAttribute('data-buka', Number(li.dataset.langkah) === n);
      li.toggleAttribute('data-lewat', Number(li.dataset.langkah) < n);
    });
    if (n === 2) muatKelas();
    if (n === 6) hitungPerkiraan();
  }
  /* Mode warna. Begitu dipilih, operasi penggeser rona di daftar augmentasi
     ditandai mati — bukan sekadar diabaikan diam-diam di server. Orang harus
     melihat apa yang berubah karena pilihannya, bukan menemukannya nanti di
     dataset yang sudah jadi. */
  const OP_RONA = ['hue_sat', 'blackbody', 'iluminan', 'color_jitter',
                   'grayscale', 'saturasi'];
  function terapkanModeWarna(gambarUlang = true) {
    const dipilih = wz.querySelector('input[name="wz-warna"]:checked');
    const m = dipilih ? dipilih.value : 'bentuk';
    const berubah = (resep.warna || {}).mode !== m;
    resep.warna = { mode: m };

    /* Saklarnya BENAR-BENAR disetel, bukan sekadar ditandai.
       Versi pertama cuma menambahkan penanda visual pada barisnya, dan itu
       tidak berpengaruh apa pun: operasi yang mati memang TIDAK dirender
       sama sekali di daftar ini, jadi penandanya tidak pernah mengenai apa-
       apa. Akibatnya pilihan mode terlihat "sudah jalan" padahal resep yang
       dikirim tetap membawa keenam operasi penggeser rona dalam keadaan
       menyala.

       Keenamnya DIMILIKI mode ini: memilih "warna" mematikannya, memilih
       "bentuk" mengembalikannya ke bawaan katalog. Menyerahkannya setengah-
       setengah ke orang berarti membuka kombinasi yang tidak konsisten —
       mode warna dengan hue_sat menyala menghasilkan dataset yang labelnya
       diam-diam salah. */
    if (berubah) {
      OP_RONA.forEach((oid) => {
        if (!katalog || !katalog.aug || !katalog.aug[oid]) return;
        if (m === 'warna') {
          resep.aug[oid] = { ...(resep.aug[oid] || {}), aktif: false };
        } else {
          delete resep.aug[oid];          // kembali ke bawaan katalog
        }
      });
      if (gambarUlang && katalog) gambarOperasi();
    }

    const ket = document.getElementById('wz-mode-ket');
    if (ket) {
      ket.textContent = m === 'warna'
        ? OP_RONA.length + ' langkah pengubah warna dimatikan di daftar bawah. '
          + 'Saat melatih nanti, warna asli juga dipertahankan.'
        : OP_RONA.length + ' langkah pengubah warna dinyalakan di daftar bawah. '
          + 'Warna diacak lebar di sini dan juga saat melatih nanti.';
    }
  }
  wz.querySelectorAll('input[name="wz-warna"]').forEach((r) => {
    // Menyetel mode warna langsung = menyimpang dari preset; lupakan preset
    // terpilih supaya penanda jatuh kembali ke tebakan dari mode.
    r.addEventListener('change', () => {
      presetDipilih = null; terapkanModeWarna();
      if (!el('op-dlg').hidden) gambarPopup();   // segarkan baris preset di popup
    });
  });

  /* Registry "jenis dataset/proyek". Tiap entri menyetel default yang masuk akal
     untuk use-case itu dalam SATU klik, dibahasakan awam. Menambah jenis baru =
     menambah SATU entri di sini; kartunya digambar otomatis dan membungkus
     sendiri — tak ada tombol yang di-hardcode di HTML, tak terbatas dua.
       warna  : mode warna ('bentuk' = warna diacak / 'warna' = warna dijaga)
       off    : operasi augmentasi non-warna yang dimatikan
       resize : ukuran preprocessing {mode, lebar, tinggi}
     Hanya menggerakkan mekanisme resep yang sudah ada -> server tak perlu tahu;
     SmartBin = bawaan lama, jalur RVM tak berubah. */
  const PRESET_DETEKSI = [
    { id: 'smartbin', nama: 'SmartBin', sub: 'botol · kaleng · tetra',
      jelas: 'Warna diacak kuat supaya model belajar BENTUK, bukan warna — '
           + 'cocok untuk benda yang bentuknya sama walau warnanya beda. '
           + 'Ukuran 640×640, memakai latar ruang detektor RVM.',
      warna: 'bentuk', off: [], resize: { mode: 'fit', lebar: 640, tinggi: 640 },
      // RVM: pakai pelat latar bawaan + fase crop/zoom & balans ukuran (perilaku lama).
      latarRvm: true, fase: { crop_zoom: true, balans_skala: true } },
    { id: 'basket', nama: 'Basket / olahraga', sub: 'pemain · bola · lapangan',
      jelas: 'Warna tiap tim DIJAGA supaya bisa dibedakan, tanpa bayangan yang '
           + 'menutupi pemain, tak dibalik atas-bawah, dan TANPA latar ruang RVM. '
           + 'Frame video 16:9 (1280×720); blur/pecah dibiarkan karena crop 1 '
           + 'frame kadang jelek.',
      warna: 'warna', off: ['flip_v', 'bayangan'],
      resize: { mode: 'regang', lebar: 1280, tinggi: 720 },
      // NON-RVM: jangan pakai pelat RVM bawaan; matikan fase yang menempel objek
      // ke pelat (crop/zoom, balans ukuran) karena itu teknik khas ruang RVM.
      latarRvm: false, fase: { crop_zoom: false, balans_skala: false } },
  ];
  // Peta id-fase -> id checkbox di langkah 5 (kumpulkanResep membangun ulang
  // resep.fase dari checkbox ini, jadi preset HARUS menyetel checkboxnya).
  const FASE_CB = { crop_zoom: 'wz-f-crop', balans_skala: 'wz-f-skala',
                    balans_kelas: 'wz-f-kelas', porsi_negatif: 'wz-f-neg' };
  // Semua operasi yang PERNAH dimatikan preset mana pun — direset tiap pindah
  // preset supaya tak ada sisa dari pilihan sebelumnya.
  const OP_OFF_SEMUA = [...new Set(PRESET_DETEKSI.flatMap((p) => p.off))];
  let presetDipilih = null;

  function terapkanPresetDeteksi(id) {
    const p = PRESET_DETEKSI.find((x) => x.id === id);
    if (!p) return;
    presetDipilih = id;
    // 1) mode warna -> nyala/matikan 6 operasi warna lewat mekanisme yang ada.
    const r = wz.querySelector(`input[name="wz-warna"][value="${p.warna}"]`);
    if (r) r.checked = true;
    resep.warna = { mode: '__paksa__' };        // paksa terapkanModeWarna anggap berubah
    terapkanModeWarna(false);
    // 2) operasi non-warna yang dimatikan preset ini (reset semua kandidat dulu).
    OP_OFF_SEMUA.forEach((oid) => {
      if (!(katalog && katalog.aug && katalog.aug[oid])) return;
      if (p.off.includes(oid)) resep.aug[oid] = { ...(resep.aug[oid] || {}), aktif: false };
      else delete resep.aug[oid];
    });
    // 3) ukuran resize (preprocessing, langkah 4).
    if (p.resize) {
      resep.pra = resep.pra || {};
      resep.pra.resize = { ...(resep.pra.resize || {}), aktif: true, ...p.resize };
    }
    // 4) LATAR RVM: pelat ruang RVM bawaan HANYA untuk RVM. Non-RVM -> false,
    //    supaya latar ruang RVM tak menyusup ke dataset lewat fase crop/zoom.
    resep.latar_bawaan = (p.latarRvm !== false);
    // 5) Fase pelat (crop/zoom, balans ukuran) = teknik ruang RVM. Setel LEWAT
    //    CHECKBOX langkah 5 karena kumpulkanResep membangun resep.fase dari situ.
    Object.entries(p.fase || {}).forEach(([oid, on]) => {
      const cb = el(FASE_CB[oid]);
      if (cb) cb.checked = !!on;
      resep.fase = resep.fase || {};
      resep.fase[oid] = { ...(resep.fase[oid] || {}), aktif: !!on };
    });
    gambarOperasi();
    // Popup (kalau terbuka) digambar ulang -> baris preset yang aktif tampil
    // nyala, lainnya mati (pilih-salah-satu).
    if (!el('op-dlg').hidden) gambarPopup();
  }

  wz.querySelectorAll('[data-lanjut]').forEach((b) => {
    b.onclick = () => buka(Number(b.closest('.wz-item').dataset.langkah) + 1);
  });
  wz.querySelectorAll('[data-mundur]').forEach((b) => {
    b.onclick = () => buka(Number(b.closest('.wz-item').dataset.langkah) - 1);
  });
  wz.querySelectorAll('.wz-ubah').forEach((b) => {
    b.onclick = () => buka(Number(b.closest('.wz-item').dataset.langkah));
  });

  el('vs-mulai').onclick = async () => {
    // Satu atribut menyembunyikan daftar versi, kotak kosong, dan tombolnya
    // sekaligus (lihat app.css). DOM-nya TIDAK dibongkar: Batal lalu masuk
    // lagi karena itu tidak menghapus satu pun isian yang sudah dipilih.
    isi.dataset.mode = 'wizard';
    wz.hidden = false;
    buka(1);
    if (!katalog) katalog = await fetch('/api/versi/katalog').then((r) => r.json());
    // Modenya diterapkan SEBELUM daftar digambar: kalau tidak, wizard yang
    // dibuka ulang dengan mode `warna` masih menampilkan keenam langkah
    // penggeser rona menyala, dan orang melihat daftar yang berselisih
    // dengan pilihannya sendiri.
    resep.warna = { mode: 'tidak-ada' };      // paksa dianggap berubah
    terapkanModeWarna(false);
    gambarOperasi();
    // Pemilih "jenis proyek" (SmartBin/Basket) kini digambar DI DALAM popup
    // "Atur formula" Preprocessing (gambarPopup), jadi tak perlu dirender di sini.
    await muatFilter();
    await muatSumber();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };
  el('wz-tutup').onclick = () => {
    wz.hidden = true;
    delete isi.dataset.mode;
    window.scrollTo({ top: 0 });
  };

  // --------------------------------------------------- langkah 1: sumber
  async function muatSumber() {
    const r = await kirimResep('/api/versi/estimasi');
    if (!r || !r.ok) {
      el('wz-sumber').textContent = (r && r.error) || 'gagal membaca dataset';
      return;
    }
    sumber = r;
    el('wz-nama').value = 'v' + (r.nomor_berikut || (jumlahVersi() + 1));
    const buang = r.dibuang_saring || 0;
    const asli = r.n_sumber + buang;                 // total sebelum filter
    const frac = asli ? buang / asli : 0;
    // Ambang peringatan: filter yang membuang lebih dari 40% dataset hampir
    // pasti tak disengaja (mis. salah lepas centang batch besar). Diberitahu,
    // bukan diblokir — subset kecil yang disengaja tetap boleh.
    const banjir = frac > 0.4;
    el('wz-sumber').innerHTML =
      `<span><b>${r.n_sumber}</b> gambar</span>` +
      `<span><b>${r.objek_sumber}</b> objek</span>` +
      `<span><b>${r.kelas}</b> kelas</span>` +
      `<span><b>${r.negatif_sumber}</b> sampel negatif</span>` +
      (buang ? `<span class="wz-buang${banjir ? ' wz-buang-banyak' : ''}">`
        + `−${buang} dibuang filter</span>` : '');
    el('wz-r1').textContent = `${r.n_sumber} gambar · ${r.kelas} kelas`
      + (buang ? ` · −${buang} filter` : '');
    const ket = el('wz-filter-ket');
    ket.classList.toggle('wz-filter-awas', banjir);
    ket.textContent = !buang ? ''
      : banjir
        ? `⚠ Filter membuang ${buang} gambar (${Math.round(frac * 100)}%) — `
          + `dari ${asli} jadi ${r.n_sumber}. Pastikan ini disengaja: versi yang `
          + `terlalu kecil bikin model lemah.`
        : `${buang} gambar dibuang dari versi ini oleh filter `
          + `(dataset sumber tetap utuh).`;
    gambarSplit();
    muatKelas();
  }

  // Daftar batch & tag yang ada di isi dataset, untuk panel filter langkah 1.
  // Dipanggil sekali saat wizard dibuka; centang batch = ikut, centang tag =
  // dibuang. Tiap perubahan menghitung ulang perkiraan lewat muatSumber.
  async function muatFilter() {
    const r = await fetch('/api/versi/sumber').then((x) => x.json()).catch(() => null);
    if (!r || !r.ok) return;
    const batch = r.batch || {}, tag = r.tag || {};
    const nb = Object.keys(batch).length, nt = Object.keys(tag).length;
    // Panel selalu tampil: opsi "buang katalog" berlaku walau tak ada batch/tag.
    el('wz-filter').hidden = false;
    const kat = el('wz-buang-katalog');
    if (kat) kat.onchange = () => muatSumber();
    const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    el('wz-filter-batch').innerHTML = nb ? '<b class="wz-filter-jdl">Batch (yang dicentang ikut)</b>'
      + Object.entries(batch).map(([b, n]) =>
        `<label class="wz-filter-baris"><input type="checkbox" class="wz-fb" checked
          data-batch="${esc(b)}"> ${esc(b)} <i>${n}</i></label>`).join('') : '';
    el('wz-filter-tag').innerHTML = nt ? '<b class="wz-filter-jdl">Tag (yang dicentang dibuang)</b>'
      + Object.entries(tag).map(([t, n]) =>
        `<label class="wz-filter-baris"><input type="checkbox" class="wz-ft"
          data-tag="${esc(t)}"> ${esc(t)} <i>${n}</i></label>`).join('') : '';
    el('wz-filter').querySelectorAll('.wz-fb, .wz-ft').forEach((c) => {
      c.onchange = () => muatSumber();
    });
  }
  const jumlahVersi = () => document.querySelectorAll('.vs-kartu').length;

  // ---------------------------------------------------- langkah 3: split
  const rasioTeks = () =>
    `${el('wz-train').value},${el('wz-valid').value},${el('wz-test').value}`;

  // Cara membelah yang DIPILIH orang. Sebelumnya nilai radio ini tidak pernah
  // dibaca siapa pun: yang menentukan hanyalah ada atau tidaknya rencana di
  // sesi, sehingga memilih "Anti-bocor" tanpa menjalankan splittingnya tetap
  // menghasilkan pembagian berdasarkan nama berkas -- tanpa peringatan.
  const belahMode = () => (document.querySelector(
    'input[name="wz-belah"]:checked') || {}).value || 'cepat';

  // Rencana anti-bocor hanya ada sesudah tombolnya dijalankan di sesi ini.
  let adaRencana = false;

  function segarkanBelah() {
    const bocor = belahMode() === 'bocor';
    const pr = el('wz-belah-pesan');
    if (pr) {
      pr.hidden = !(bocor && !adaRencana);
      pr.textContent = 'Anti-bocor dipilih, tetapi pemeriksaan isi gambarnya '
        + 'belum dijalankan. Tekan "Jalankan splitting anti-bocor" di bawah; '
        + 'tanpa itu pembagiannya tetap memakai nama berkas.';
    }
    const lanjut = document.querySelector(
      '[data-langkah="3"] [data-lanjut]');
    if (lanjut) lanjut.disabled = bocor && !adaRencana;
  }

  document.querySelectorAll('input[name="wz-belah"]').forEach((r) => {
    r.addEventListener('change', () => {
      segarkanBelah();
      // Angka perkiraan ikut berubah, jadi jangan biarkan yang lama terbaca
      // seolah masih berlaku.
      if (sumber) hitungPerkiraan().catch(() => {});
    });
  });

  function gambarSplit() {
    if (!sumber) return;
    const a = ['wz-train', 'wz-valid', 'wz-test'].map((i) => Number(el(i).value) || 0);
    const tot = a.reduce((x, y) => x + y, 0) || 1;
    const n = sumber.n_sumber;
    const jml = a.map((v) => Math.round((v / tot) * n));
    el('wz-bilah').innerHTML =
      `<i class="p-ok" style="flex-grow:${a[0]}"></i>` +
      `<i class="p-warn" style="flex-grow:${a[1]}"></i>` +
      `<i class="p-bg" style="flex-grow:${a[2]}"></i>`;
    el('wz-split').innerHTML = ['Train', 'Valid', 'Test']
      .map((s, i) => `<span>${s} <b>${jml[i]}</b> (${Math.round(a[i] / tot * 100)}%)</span>`)
      .join('');
    el('wz-r2').textContent = `${a[0]}/${a[1]}/${a[2]}`;
  }
  ['wz-train', 'wz-valid', 'wz-test'].forEach((i) => { el(i).oninput = gambarSplit; });

  el('wz-jalan-bocor').onclick = async () => {
    const jalur = el('wz-bocor-jalur');
    const pr = Progres.mulai('Splitting anti-bocor', { di: jalur }).taktentu();
    el('wz-batal-bocor').hidden = false;
    const pantau = setInterval(async () => {
      const k = await fetch('/api/split/kemajuan').then((r) => r.json()).catch(() => null);
      if (k && k.persen != null) pr.set(k.persen / 100, k.fase_nama || '');
    }, 500);
    try {
      const r = await fetch(`/api/split/jalankan?split=${encodeURIComponent(rasioTeks())}`,
                            { method: 'POST' }).then((x) => x.json());
      clearInterval(pantau);
      el('wz-batal-bocor').hidden = true;
      if (r && r.peringatan) {
        pr.selesai(`${r.n_sesi} sesi pemotretan`);
        el('wz-bocor-hasil').textContent =
          `${r.n_sesi} sesi · terbesar ${(r.grup_terbesar_pct * 100 || 0).toFixed(1)}%` +
          (r.peringatan.length ? ` · ${r.peringatan.join(' · ')}` : '');
        document.querySelector('input[name="wz-belah"][value="bocor"]').checked = true;
        adaRencana = true;
        segarkanBelah();
      } else {
        pr.gagal((r && r.error) || 'gagal');
      }
    } catch (e) {
      clearInterval(pantau);
      el('wz-batal-bocor').hidden = true;
      pr.gagal('gagal menghubungi server');
    }
  };
  el('wz-batal-bocor').onclick = () => fetch('/api/split/batal', { method: 'POST' });

  // ------------------------------------------- langkah 4 & 5: daftar operasi
  function aktif(tahap, oid) {
    const p = resep[tahap][oid];
    if (p !== undefined) return p.aktif !== false;
    return !!(katalog && katalog[tahap][oid] && katalog[tahap][oid].bawaan_aktif);
  }

  // Membuat entri resep TANPA ikut menyalakan operasinya. aktif() membaca
  // "ada entri tanpa aktif:false" sebagai menyala, jadi menulis angka ke
  // operasi yang bawaannya mati akan diam-diam menyalakannya. Yang menggeser
  // batang Cutout tidak sedang meminta Cutout ikut dipakai.
  function entri(tahap, oid) {
    if (!resep[tahap][oid]) resep[tahap][oid] = { aktif: aktif(tahap, oid) };
    return resep[tahap][oid];
  }

  // Nilai yang BERLAKU untuk satu angka: geseran orang kalau ada, kalau tidak
  // bawaan katalog (yang isinya angka v14).
  function nilaiPar(tahap, oid, kunci) {
    const par = resep[tahap][oid] || {};
    const s = katalog[tahap][oid].param[kunci];
    return par[kunci] !== undefined ? par[kunci] : s.bawaan;
  }

  function digeser(tahap, oid, kunci) {
    const par = resep[tahap][oid] || {};
    if (par[kunci] === undefined) return false;
    const b = katalog[tahap][oid].param[kunci].bawaan;
    // Bukan cuma angka. Warna isian tepi bernilai teks '#rrggbb', dan
    // pengurangan pada teks menghasilkan NaN — yang membuat setiap warna yang
    // dipilih orang terbaca "belum diubah", lencana operasinya ikut kosong.
    if (typeof par[kunci] === 'number' && typeof b === 'number') {
      return Math.abs(par[kunci] - b) > 1e-9;
    }
    return par[kunci] !== b;
  }

  // Berapa hal yang sudah diubah orang pada satu operasi, untuk lencana di
  // tombolnya. Peta kelas dihitung per KELAS yang tersentuh, bukan per
  // parameter: "1" pada operasi yang membuang tiga kelas tidak menjawab apa
  // pun, dan `peta` serta `nama` adalah dua parameter untuk satu layar.
  function jumlahUbah(tahap, oid) {
    const spec = katalog[tahap][oid].param || {};
    if (spec.peta && spec.peta.jenis === 'peta_kelas') {
      const par = resep[tahap][oid] || {};
      return new Set([...Object.keys(par.peta || {}),
                      ...Object.keys(par.nama || {})]).size;
    }
    return Object.keys(spec).filter((k) => digeser(tahap, oid, k)).length;
  }

  // Angka jadi teks. Peluang ditulis persen karena "0,18" tidak menjawab
  // pertanyaan yang sedang diajukan orang, yaitu "seberapa sering".
  function teksPar(s, v) {
    if (s.jenis === 'peluang') return `${Math.round(v * 100)}%`;
    if (s.jenis === 'int') return `${Math.round(v)}${s.satuan || ''}`;
    if (s.jenis === 'float') {
      const desimal = (s.langkah || 0.01) < 0.05 ? 2 : 1;
      return `${Number(v).toFixed(desimal).replace('.', ',')}${s.satuan || ''}`;
    }
    return String(v);
  }

  function gambarOperasi() {
    if (!katalog) return;
    for (const tahap of ['pra', 'aug']) {
      const wadah = el(tahap === 'pra' ? 'wz-pra' : 'wz-aug');
      // ubah_kelas punya langkahnya sendiri (Langkah 2 · Kelas), jadi tidak
      // ikut di daftar operasi Preprocessing — dua tempat mengatur hal yang
      // sama cuma membingungkan.
      const urut = (tahap === 'pra' ? katalog.urut_pra : Object.keys(katalog.aug))
        .filter((oid) => oid !== 'ubah_kelas');
      const nyala = urut.filter((oid) => aktif(tahap, oid));
      // Bawaan yang DIMATIKAN: tidak muncul di daftar, tetapi harus disebut.
      // Tanpa ini, mematikan langkah adalah pintu satu arah yang tidak
      // menyebutkan jalan pulangnya.
      const mati = urut.filter((oid) => !aktif(tahap, oid)
                                     && katalog[tahap][oid].bawaan_aktif);
      wadah.innerHTML = '';

      // Petak, bukan daftar menurun: 16 langkah dalam tiga kolom cuma enam
      // baris, jadi seluruhnya terbaca sekaligus tanpa perlu dilipat. Versi
      // sebelumnya menyembunyikannya di balik "Lihat ▾" justru karena sebagai
      // daftar menurun ia memanjang 600px dan mendorong sisa wizard keluar
      // layar.
      const kotak = document.createElement('div');
      kotak.className = 'op-daftar-isi';
      for (const oid of nyala) {
        const meta = katalog[tahap][oid];
        const par = resep[tahap][oid] || {};
        const baris = document.createElement('div');
        baris.className = 'op-baris';
        // Penanda HANYA pada yang menyimpang dari bawaan, dan dibaca dari
        // bawaan_aktif — bukan dari kelompok. Auto-Orient berkelompok
        // "tambahan" tetapi menyala sejak awal, dan penanda berbasis kelompok
        // membuatnya tampil polos di antara baris berlencana.
        baris.innerHTML =
          `<div class="op-nama" title="${meta.nama}">${meta.nama}` +
          (meta.bawaan_aktif ? '' : '<span class="op-tanda">ditambahkan</span>') +
          `</div><div class="op-ket">${ringkasPar(meta, par)}</div>`;
        const x = document.createElement('button');
        x.className = 'op-mati';
        x.type = 'button';
        x.textContent = '×';
        x.setAttribute('aria-label', `Matikan ${meta.nama}`);
        x.title = `Matikan ${meta.nama}`;
        x.onclick = () => {
          resep[tahap][oid] = { ...(resep[tahap][oid] || {}), aktif: false };
          gambarOperasi();
        };
        baris.appendChild(x);
        kotak.appendChild(baris);
      }
      if (!nyala.length) kotak.innerHTML = '<div class="op-kosong">Tidak ada langkah.</div>';
      wadah.appendChild(kotak);

      if (mati.length) {
        const q = document.createElement('div');
        q.className = 'op-balik';
        q.innerHTML = `${mati.length} langkah bawaan dimatikan · `;
        const a = document.createElement('button');
        a.type = 'button';
        a.textContent = 'Nyalakan lagi';
        a.onclick = () => { mati.forEach((o) => delete resep[tahap][o]); gambarOperasi(); };
        q.appendChild(a);
        wadah.appendChild(q);
      }

      el(tahap === 'pra' ? 'wz-r3' : 'wz-r4').textContent =
        nyala.length ? `${nyala.length} langkah` : 'tidak ada';
      const tbl = document.querySelector(`[data-tambah="${tahap}"]`);
      if (tbl) {
        tbl.textContent =
          `Atur formula · ${nyala.length} dari ${Object.keys(katalog[tahap]).length} menyala`;
      }
    }
  }

  // Parameter saja. Operasi tanpa parameter meninggalkan slot ini KOSONG —
  // dulu ia jatuh ke kalimat deskripsi, dan 16 baris augmentasi berubah jadi
  // 16 kalimat penuh. Deskripsi adalah bahan untuk MEMILIH; tempatnya di
  // popup, bukan di daftar hal yang sudah dipilih.
  function ringkasPar(meta, par) {
    const spec = Object.entries(meta.param || {});
    if (!spec.length) return '';
    const nilai = (k, s) => (par[k] !== undefined ? par[k] : s.bawaan);
    const beda = (k, s) => par[k] !== undefined
      && Math.abs(par[k] - s.bawaan) > 1e-9;
    // Peta kelas bukan angka: nilainya objek, dan teksPar akan mencetak
    // "[object Object]". Yang berguna dibaca sekilas adalah berapa kelas
    // yang tersentuh, bukan isi petanya.
    if (meta.param.peta && meta.param.peta.jenis === 'peta_kelas') {
      const peta = par.peta || {};
      const nama = par.nama || {};
      const buang = Object.values(peta).filter((v) => v === null).length;
      const gabung = Object.values(peta).filter((v) => v !== null).length;
      const bagian = [];
      if (gabung) bagian.push(`${gabung} digabung`);
      if (buang) bagian.push(`${buang} dibuang`);
      if (Object.keys(nama).length) bagian.push(`${Object.keys(nama).length} ganti nama`);
      return bagian.length ? bagian.join(' · ') : 'belum ada perubahan';
    }
    const pel = meta.param.p;
    if (pel) {
      // Augmentasi: yang paling ingin diketahui sekilas adalah seberapa
      // sering efeknya kena, jadi peluang selalu tampil. Angka lain hanya
      // kalau digeser; menuliskan kelimanya membuat petaknya jadi paragraf.
      const sisa = spec.filter(([k, s]) => k !== 'p' && beda(k, s))
        .map(([k, s]) => `${(s.label || k).toLowerCase()} ${teksPar(s, nilai(k, s))}`);
      return [teksPar(pel, nilai('p', pel)), ...sisa].join(' · ');
    }
    return spec.map(([k, s]) => teksPar(s, nilai(k, s))).join(' · ');
  }

  // -------------------------------------------------------------- popup
  const dlg = el('op-dlg');
  dlg.querySelectorAll('[data-tutup]').forEach((b) => {
    b.onclick = () => { dlg.hidden = true; };
  });
  wz.querySelectorAll('[data-tambah]').forEach((b) => {
    b.onclick = () => bukaPopup(b.dataset.tambah);
  });

  let tahapPopup = 'pra';
  // Operasi yang sedang diatur angkanya. null = popup sedang menampilkan
  // daftar operasi.
  let oidAtur = null;

  function kepalaDaftar() {
    const tahap = tahapPopup;
    el('op-kembali').hidden = true;
    el('op-judul').textContent = tahap === 'pra' ? 'Preprocessing' : 'Augmentasi';
    el('op-ket').textContent = tahap === 'pra'
      ? 'Dikenakan ke setiap gambar di train, valid, dan test.'
      : 'Menghasilkan salinan tambahan dari data train.';
    // Kotak cari hanya kalau daftarnya memang panjang. Pada 8 operasi
    // preprocessing ia cuma satu kotak lagi untuk dilewati.
    el('op-cari').hidden = Object.keys(katalog[tahap]).length <= 12;
    el('op-bawaan').textContent = 'Kembalikan ke bawaan';
  }

  function bukaPopup(tahap) {
    if (!katalog) return;
    tahapPopup = tahap;
    oidAtur = null;
    el('op-cari').value = '';
    gambarPopup();
    dlg.hidden = false;
    const cari = el('op-cari');
    (cari.hidden ? dlg.querySelector('.sk-in') : cari)?.focus();
  }

  el('op-kembali').onclick = () => { oidAtur = null; gambarPopup(); };

  // Satu angka: batang geser, nilai berjalan di kanan label, dan bawaan v14
  // tercetak di bawahnya sebagai tombol supaya jalan pulangnya selalu ada.
  function barisAngka(tahap, oid, kunci, s) {
    const baris = document.createElement('div');
    baris.className = 'pr';
    const v = nilaiPar(tahap, oid, kunci);
    baris.innerHTML =
      `<div class="pr-atas"><span class="pr-label">${s.label || kunci}</span>` +
      `<span class="pr-nilai">${teksPar(s, v)}</span></div>` +
      `<input class="pr-bar" type="range" min="${s.min}" max="${s.maks}" ` +
      `step="${s.langkah || 1}" value="${v}">` +
      `<div class="pr-kaki"><span>${teksPar(s, s.min)}</span>` +
      `<button type="button" class="pr-bawaan">bawaan ${teksPar(s, s.bawaan)}</button>` +
      `<span>${teksPar(s, s.maks)}</span></div>`;
    const bar = baris.querySelector('.pr-bar');
    const cap = baris.querySelector('.pr-nilai');
    const tandai = () => {
      cap.textContent = teksPar(s, nilaiPar(tahap, oid, kunci));
      cap.classList.toggle('pr-geser', digeser(tahap, oid, kunci));
      const rentang = s.maks - s.min;
      bar.style.setProperty('--isi',
        `${rentang ? ((Number(bar.value) - s.min) / rentang) * 100 : 0}%`);
    };
    // Ditulis saat digeser supaya angkanya tidak pernah tertinggal di
    // belakang batangnya; daftar di langkah 4/5 baru digambar ulang saat
    // jarinya lepas, karena itu menyentuh seluruh petak.
    bar.oninput = () => {
      entri(tahap, oid)[kunci] = s.jenis === 'int'
        ? Math.round(Number(bar.value)) : Number(bar.value);
      tandai();
    };
    bar.onchange = () => { gambarOperasi(); };
    baris.querySelector('.pr-bawaan').onclick = () => {
      delete (resep[tahap][oid] || {})[kunci];
      bar.value = s.bawaan;
      tandai();
      gambarOperasi();
    };
    tandai();
    return baris;
  }

  // Pemilih warna. Tidak memakai batang seperti angka: yang ditanyakan bukan
  // "seberapa besar" melainkan "yang mana", dan tiga batang RGB memaksa orang
  // menyusun warna dari komponennya.
  function barisWarna(tahap, oid, kunci, s) {
    const baris = document.createElement('div');
    baris.className = 'pr pr-warna';
    const v = nilaiPar(tahap, oid, kunci) || s.bawaan || '#000000';
    baris.innerHTML =
      `<div class="pr-atas"><span class="pr-label">${s.label || kunci}</span>` +
      `<span class="pr-nilai">${String(v).toUpperCase()}</span></div>` +
      `<input class="pr-warna-in" type="color" value="${v}">` +
      '<div class="pr-kaki"><span></span>' +
      `<button type="button" class="pr-bawaan">bawaan ${String(s.bawaan || '').toUpperCase()}</button>` +
      '<span></span></div>';
    const inp = baris.querySelector('.pr-warna-in');
    const cap = baris.querySelector('.pr-nilai');
    const tandai = () => {
      cap.textContent = String(inp.value).toUpperCase();
      cap.classList.toggle('pr-geser', digeser(tahap, oid, kunci));
    };
    inp.oninput = () => { entri(tahap, oid)[kunci] = inp.value; tandai(); };
    inp.onchange = () => { gambarOperasi(); };
    baris.querySelector('.pr-bawaan').onclick = () => {
      delete (resep[tahap][oid] || {})[kunci];
      inp.value = s.bawaan || '#000000';
      tandai();
      gambarOperasi();
    };
    tandai();
    return baris;
  }

  function barisPilih(tahap, oid, kunci, s) {
    const baris = document.createElement('div');
    baris.className = 'pr pr-pilih';
    const v = nilaiPar(tahap, oid, kunci);
    baris.innerHTML =
      `<div class="pr-atas"><span class="pr-label">${s.label || kunci}</span></div>` +
      '<select class="pr-sel">' + (s.pilihan || []).map(
        ([a, b]) => `<option value="${a}"${a === v ? ' selected' : ''}>${b}</option>`)
        .join('') + '</select>';
    const sel = baris.querySelector('.pr-sel');
    sel.onchange = () => {
      entri(tahap, oid)[kunci] = sel.value;
      gambarOperasi();
    };
    return baris;
  }

  // Satu layar untuk seluruh kelas projek (Langkah 2 · Kelas): centang =
  // dipakai, lepas centang = jadi latar (buang), plus gabung ke kelas lain atau
  // ganti nama. `peta` memakai INDEKS kelas — itu yang dibaca mesinnya — tetapi
  // yang tampil harus nama, jadi keduanya datang bersama dari
  // /api/versi/estimasi. Perubahan apa pun MENYALAKAN operasinya sendiri
  // (aktif=true); kalau semua dikembalikan ke "dipakai + Biarkan", ia mati lagi
  // supaya versi yang tak menyentuh kelas tidak membayar apa-apa.
  function layarKelas(tahap, oid) {
    const bagian = document.createElement('div');
    const daftar = (sumber && sumber.daftar_kelas) || [];
    if (!daftar.length) {
      bagian.innerHTML = '<p class="op-hampa">Dataset ini belum punya kelas '
        + 'bernama, jadi tidak ada yang bisa diatur.</p>';
      return bagian;
    }
    const par = resep[tahap][oid] || {};
    const peta = par.peta || {};
    const nama = par.nama || {};

    const jumlahDipakai = () =>
      [...bagian.querySelectorAll('.kl-pakai')].filter((x) => x.checked).length;

    for (const k of daftar) {
      const baris = document.createElement('div');
      baris.className = 'kl';
      const dibuang = peta[k.i] === null;
      if (dibuang) baris.classList.add('kl-latar');
      const lain = daftar.filter((x) => x.i !== k.i);
      const kini = (typeof peta[k.i] === 'number') ? `g:${peta[k.i]}`
        : (nama[k.i] !== undefined ? 'nama' : '');
      baris.innerHTML =
        `<label class="kl-pakai-bungkus" title="Centang: dipakai. Lepas: jadi latar.">` +
        `<input class="kl-pakai" type="checkbox"${dibuang ? '' : ' checked'}></label>` +
        `<div class="kl-kiri"><b>${k.nama}</b>` +
        `<span class="kl-n">${k.objek.toLocaleString('id')} objek</span></div>` +
        '<select class="kl-sel">' +
        `<option value=""${kini === '' ? ' selected' : ''}>Biarkan</option>` +
        `<option value="nama"${kini === 'nama' ? ' selected' : ''}>Ganti nama</option>` +
        lain.map((x) => `<option value="g:${x.i}"` +
          `${kini === `g:${x.i}` ? ' selected' : ''}>Gabung ke ${x.nama}</option>`).join('') +
        '</select>' +
        `<input class="kl-nama" type="text" maxlength="60" placeholder="nama baru" ` +
        `value="${nama[k.i] !== undefined ? String(nama[k.i]).replace(/"/g, '&quot;') : ''}"` +
        `${kini === 'nama' ? '' : ' hidden'}>` +
        `<span class="kl-latar-tanda"${dibuang ? '' : ' hidden'}>&rarr; jadi latar</span>`;
      const cb = baris.querySelector('.kl-pakai');
      const sel = baris.querySelector('.kl-sel');
      const inp = baris.querySelector('.kl-nama');
      const tulis = () => {
        const e = entri(tahap, oid);
        e.peta = { ...(e.peta || {}) };
        e.nama = { ...(e.nama || {}) };
        delete e.peta[k.i];
        delete e.nama[k.i];
        if (!cb.checked) e.peta[k.i] = null;                 // jadi latar (buang)
        else if (sel.value.startsWith('g:')) e.peta[k.i] = Number(sel.value.slice(2));
        else if (sel.value === 'nama') e.nama[k.i] = inp.value;
        // Nyalakan/matikan operasinya sendiri menurut ada-tidaknya perubahan.
        e.aktif = !!(Object.keys(e.peta).length || Object.keys(e.nama).length);
        ringkasKelas();
        gambarOperasi();
      };
      cb.onchange = () => {
        // Jangan biarkan SEMUA kelas jadi latar: versi tanpa satu pun kelas
        // cuma berisi contoh negatif dan tak bisa melatih apa pun.
        if (!cb.checked && jumlahDipakai() < 1) {
          cb.checked = true;
          toast('Sisakan minimal satu kelas — versi tanpa kelas cuma berisi latar.');
          return;
        }
        baris.classList.toggle('kl-latar', !cb.checked);
        sel.disabled = !cb.checked;
        inp.hidden = !cb.checked || sel.value !== 'nama';
        baris.querySelector('.kl-latar-tanda').hidden = cb.checked;
        tulis();
      };
      sel.onchange = () => {
        inp.hidden = sel.value !== 'nama';
        if (!inp.hidden && !inp.value) inp.value = k.nama;
        tulis();
        if (!inp.hidden) inp.focus();
      };
      inp.oninput = tulis;
      sel.disabled = dibuang;
      bagian.appendChild(baris);
    }
    return bagian;
  }

  // Langkah 2 · Kelas: gambar panelnya + ringkasannya. Dipanggil saat sumber
  // termuat dan tiap kali langkah 2 dibuka, jadi ia selalu mencerminkan keadaan
  // resep.pra.ubah_kelas terkini (mis. sesudah diubah lalu dibuka lagi).
  function muatKelas() {
    const wrap = el('wz-kelas');
    if (!wrap) return;
    if (!sumber) { wrap.textContent = 'memuat…'; return; }
    wrap.innerHTML = '';
    wrap.appendChild(layarKelas('pra', 'ubah_kelas'));
    ringkasKelas();
  }

  function ringkasKelas() {
    const daftar = (sumber && sumber.daftar_kelas) || [];
    const peta = ((resep.pra.ubah_kelas || {}).peta) || {};
    const nama = ((resep.pra.ubah_kelas || {}).nama) || {};
    const latar = daftar.filter((k) => peta[k.i] === null).length;
    const gabung = daftar.filter((k) => typeof peta[k.i] === 'number').length;
    const rename = daftar.filter((k) => nama[k.i] !== undefined).length;
    const pakai = daftar.length - latar;
    const bagian = [];
    if (latar) bagian.push(`${latar} jadi latar`);
    if (gabung) bagian.push(`${gabung} digabung`);
    if (rename) bagian.push(`${rename} diganti nama`);
    const r = el('wz-r-kelas');
    if (r) {
      r.textContent = daftar.length
        ? (bagian.length ? `${pakai} kelas dipakai · ${bagian.join(' · ')}`
                         : `${daftar.length} kelas, semua dipakai`)
        : '';
    }
  }

  function gambarAtur() {
    const tahap = tahapPopup;
    const oid = oidAtur;
    const meta = katalog[tahap][oid];
    const spec = meta.param || {};
    el('op-kembali').hidden = false;
    el('op-judul').textContent = meta.nama;
    el('op-ket').textContent = meta.ket;
    el('op-cari').hidden = true;
    // Layar kelas tidak berisi satu angka pun; menyebutnya "angka bawaan"
    // di sana menamai tombol menurut cara kerjanya, bukan akibatnya.
    el('op-bawaan').textContent = (spec.peta && spec.peta.jenis === 'peta_kelas')
      ? 'Kembalikan semua kelas' : 'Kembalikan angka bawaan';
    const wadah = el('op-isi');
    wadah.innerHTML = '';
    for (const [kunci, s] of Object.entries(spec)) {
      if (s.jenis === 'pilih') wadah.appendChild(barisPilih(tahap, oid, kunci, s));
      else if (s.jenis === 'warna') wadah.appendChild(barisWarna(tahap, oid, kunci, s));
      else if (s.jenis === 'int' || s.jenis === 'float' || s.jenis === 'peluang') {
        wadah.appendChild(barisAngka(tahap, oid, kunci, s));
      } else if (s.jenis === 'peta_kelas') {
        // `peta` dan `nama` diatur di SATU layar, karena keduanya menjawab
        // pertanyaan yang sama ("kelas ini mau diapakan?"). `nama` karena itu
        // tidak menggambar apa-apa sendiri.
        wadah.appendChild(layarKelas(tahap, oid));
      } else if (s.jenis !== 'nama_kelas') {
        const p = document.createElement('p');
        p.className = 'op-hampa';
        p.textContent = `${s.label || kunci}: belum bisa diatur dari sini.`;
        wadah.appendChild(p);
      }
    }
    if (!wadah.children.length) {
      wadah.innerHTML = '<p class="op-hampa">Operasi ini tidak punya angka.</p>';
    }
  }

  function gambarPopup() {
    const tahap = tahapPopup;
    if (oidAtur) { gambarAtur(); return; }
    kepalaDaftar();
    const q = (el('op-cari').value || '').trim().toLowerCase();
    const wadah = el('op-isi');
    wadah.innerHTML = '';
    const semua = Object.keys(katalog[tahap]).filter((i) => i !== 'ubah_kelas');
    // PREPROCESSING: grup PALING ATAS = pemilih JENIS PROYEK (SmartBin / Basket),
    // baris SAKLAR KECIL (pilih salah satu), bukan card. Ia menyetel default
    // resize di sini + warna/latar/fase di Augmentasi. Hanya tahap 'pra', dan
    // disembunyikan saat sedang mencari (biar tak mengganggu hasil pencarian).
    if (tahap === 'pra' && !q) {
      const hp = document.createElement('div');
      hp.className = 'op-grup';
      hp.textContent = 'Jenis proyek (default)';
      wadah.appendChild(hp);
      const aktifId = presetDipilih
        || ((resep.warna || {}).mode === 'warna' ? 'basket' : 'smartbin');
      for (const p of PRESET_DETEKSI) {
        const pil = document.createElement('div');
        pil.className = 'op-pil';
        const lab = document.createElement('label');
        lab.className = 'sk';
        lab.innerHTML =
          `<input class="sk-in" type="checkbox" role="switch"${p.id === aktifId ? ' checked' : ''}>`
          + '<span class="sk-track"><span class="sk-knob"></span></span>'
          + `<span class="sk-teks"><b>${p.nama}</b><em>${p.sub}</em></span>`;
        // Pilih-salah-satu: klik mana pun MENERAPKAN preset itu lalu gambar ulang
        // (yang aktif tampil nyala, lainnya mati) — tak ada keadaan kosong/dobel.
        lab.querySelector('.sk-in').onchange = () => terapkanPresetDeteksi(p.id);
        pil.appendChild(lab);
        wadah.appendChild(pil);
      }
    }

    // AUGMENTASI: grup PALING ATAS = PERAN WARNA di projek ini (bentuk / warna),
    // baris SAKLAR KECIL (pilih salah satu), bukan card di badan langkah. Ia
    // menentukan mana dari operasi penggeser rona di bawahnya yang boleh menyala.
    // Radio #wz-mode (tersembunyi) tetap jadi sumber kebenaran; baris ini hanya
    // menyetelnya. Disembunyikan saat sedang mencari.
    if (tahap === 'aug' && !q) {
      const hp = document.createElement('div');
      hp.className = 'op-grup';
      hp.textContent = 'Peran warna di projek ini';
      wadah.appendChild(hp);
      const modeKini = (resep.warna || {}).mode === 'warna' ? 'warna' : 'bentuk';
      const MODE = [
        { v: 'bentuk', nama: 'Bentuk yang menentukan kelas',
          sub: 'Warna sengaja dirusak supaya model belajar bentuk — botol/kaleng/'
             + 'tetra. Rona digeser lebar.' },
        { v: 'warna', nama: 'Warna bagian dari kelas',
          sub: 'Rona dipertahankan; hanya terang yang divariasikan — pengenalan '
             + 'produk/SKU atau warna jersey tim. Penggeser rona dimatikan.' },
      ];
      for (const mo of MODE) {
        const pil = document.createElement('div');
        pil.className = 'op-pil';
        const lab = document.createElement('label');
        lab.className = 'sk';
        lab.innerHTML =
          `<input class="sk-in" type="checkbox" role="switch"${mo.v === modeKini ? ' checked' : ''}>`
          + '<span class="sk-track"><span class="sk-knob"></span></span>'
          + `<span class="sk-teks"><b>${mo.nama}</b><em>${mo.sub}</em></span>`;
        // Pilih-salah-satu: setel radio kanonik lalu terapkanModeWarna (sinkron
        // resep + saklar rona) + gambar ulang popup (baris & daftar ikut berubah).
        lab.querySelector('.sk-in').onchange = () => {
          const r = wz.querySelector(`input[name="wz-warna"][value="${mo.v}"]`);
          if (r) r.checked = true;
          presetDipilih = null;          // pilih mode manual = menyimpang dari preset
          terapkanModeWarna();
          gambarPopup();
        };
        pil.appendChild(lab);
        wadah.appendChild(pil);
      }
    }

    // Operasi. Preprocessing -> SATU grup "Formula Preprocessing" (semua op);
    // Augmentasi -> tetap dipisah Bawaan/Opsional menurut bawaan_aktif.
    const grup = tahap === 'pra'
      ? [[null, 'Formula Preprocessing']]
      : [[true, 'Bawaan (menyala sejak awal)'], [false, 'Opsional (mati sejak awal)']];
    for (const [kunci, judul] of grup) {
      const ids = semua.filter((i) =>
        (kunci === null || !!katalog[tahap][i].bawaan_aktif === kunci)
        && (!q || (katalog[tahap][i].nama + ' ' + katalog[tahap][i].ket)
          .toLowerCase().includes(q)));
      if (!ids.length) continue;
      const h = document.createElement('div');
      h.className = 'op-grup';
      h.textContent = judul;
      wadah.appendChild(h);
      for (const oid of ids) {
        const meta = katalog[tahap][oid];
        // Hanya yang benar-benar punya kendali. Tombol yang membuka halaman
        // berisi "belum bisa diatur" adalah jalan buntu yang terlihat seperti
        // pintu. `nama` tidak ikut dihitung: ia digambar oleh layar yang sama
        // dengan `peta`, jadi menghitungnya membuat satu layar terhitung dua.
        const angka = Object.entries(meta.param || {})
          .filter(([, s]) => ['int', 'float', 'peluang', 'pilih', 'peta_kelas',
            'warna'].includes(s.jenis))
          .map(([k]) => k);
        const pil = document.createElement('div');
        pil.className = 'op-pil';
        const lab = document.createElement('label');
        lab.className = 'sk';
        lab.innerHTML =
          `<input class="sk-in" type="checkbox" role="switch"${aktif(tahap, oid) ? ' checked' : ''}>` +
          '<span class="sk-track"><span class="sk-knob"></span></span>' +
          `<span class="sk-teks"><b>${meta.nama}</b><em>${meta.ket}</em></span>`;
        const inp = lab.querySelector('.sk-in');
        // Diterapkan seketika, tanpa OK. Daftar di belakang popup ikut berubah
        // dan terlihat di sela dialog yang lebarnya cuma 560px, jadi akibatnya
        // langsung terbaca.
        inp.onchange = () => {
          resep[tahap][oid] = { ...(resep[tahap][oid] || {}), aktif: inp.checked };
          gambarOperasi();
          cacah();
        };
        pil.appendChild(lab);
        // Tombolnya di LUAR <label>: di dalamnya, tiap klik pada tombol ikut
        // membalik saklarnya, karena label meneruskan klik ke input miliknya.
        if (angka.length) {
          const a = document.createElement('button');
          a.type = 'button';
          a.className = 'op-angka';
          const n = jumlahUbah(tahap, oid);
          a.innerHTML = `Angka${n ? `<span class="op-angka-n">${n}</span>` : ''} \u203a`;
          a.title = `Atur angka ${meta.nama}`;
          a.onclick = () => { oidAtur = oid; gambarPopup(); };
          pil.appendChild(a);
        }
        wadah.appendChild(pil);
      }
    }
    if (!wadah.children.length) {
      wadah.innerHTML = '<p class="op-hampa">Tidak ada operasi yang cocok.</p>';
    }
    cacah();

    function cacah() {
      const n = semua.filter((i) => aktif(tahap, i)).length;
      let c = el('op-cacah');
      if (!c) {
        c = document.createElement('span');
        c.className = 'op-cacah';
        c.id = 'op-cacah';
        el('op-judul').appendChild(c);
      }
      c.textContent = `${n} / ${semua.length} nyala`;
    }
  }

  el('op-cari').oninput = gambarPopup;
  el('op-bawaan').onclick = () => {
    if (oidAtur) {
      // Hanya angka operasi ini. Menyala atau matinya adalah keputusan lain,
      // dan membatalkannya sekalian berarti tombol ini melakukan dua hal yang
      // tidak diminta bersamaan.
      resep[tahapPopup][oidAtur] = { aktif: aktif(tahapPopup, oidAtur) };
      gambarPopup();
      gambarOperasi();
      return;
    }
    // Jaring pengaman: seluruh penyimpangan dibuang, katalog yang berlaku lagi.
    resep[tahapPopup] = {};
    gambarPopup();
    gambarOperasi();
  };

  // ------------------------------------------------------- langkah 6: buat
  function kumpulkanResep() {
    // Dibaca ulang di sini, bukan cuma mengandalkan handler change: kalau
    // radionya dipulihkan browser (muat ulang, kembali dari tab lain) tanpa
    // memicu change, resep akan terkirim dengan mode yang sudah tidak sesuai
    // dengan yang terlihat di layar.
    terapkanModeWarna();
    resep.volume.per_gambar = Number(el('wz-salin').value) || 0;
    resep.fase = {
      crop_zoom: { aktif: el('wz-f-crop').checked },
      balans_skala: { aktif: el('wz-f-skala').checked },
      balans_kelas: { aktif: el('wz-f-kelas').checked },
      porsi_negatif: { aktif: el('wz-f-neg').checked },
    };
    // Filter sumber: batch yang centangnya DILEPAS dibuang; tag yang dicentang
    // dibuang. Cuma berlaku untuk versi ini — dataset sumber tak tersentuh.
    resep.sumber = {
      batch_buang: [...document.querySelectorAll('.wz-fb:not(:checked)')]
        .map((c) => c.dataset.batch),
      tag_buang: [...document.querySelectorAll('.wz-ft:checked')]
        .map((c) => c.dataset.tag),
      buang_katalog: !!(el('wz-buang-katalog') && el('wz-buang-katalog').checked),
    };
    return resep;
  }

  const kirimResep = (url) => fetch(
    `${url}?split=${encodeURIComponent(rasioTeks())}`
    + `&belah=${encodeURIComponent(belahMode())}`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ resep: kumpulkanResep() }) })
    .then((r) => r.json()).catch(() => null);

  async function hitungPerkiraan() {
    const t = el('wz-tinjau');
    const daftar = (tahap) => Object.keys(katalog ? katalog[tahap] : {})
      .filter((i) => aktif(tahap, i))
      .map((i) => katalog[tahap][i].nama);
    // Fase lanjutan ikut ditinjau, dan itu bukan hiasan: keempatnya menyala
    // sejak awal dan justru merekalah yang paling banyak menambah gambar —
    // "Seimbangkan jumlah kelas" bisa melipatgandakan kelas minoritas. Tanpa
    // baris ini langkah 6 diam soal keempatnya, sehingga satu-satunya cara
    // tahu balancer sedang menyala adalah kembali ke langkah 5 dan membukanya.
    // Yang tidak aktif TETAP disebut, sebagai "mati": senyap tentang sesuatu
    // yang dimatikan terbaca sama dengan senyap karena tidak punya fiturnya.
    // Yang mati disebut di barisnya sendiri, dengan kata "mati" yang benar-
    // benar tertulis — bukan sekadar dicoret. Coretan cuma pembeda visual:
    // teksnya terbaca sama persis saat dipindai cepat atau dibacakan pembaca
    // layar, jadi "Seimbangkan jumlah kelas" yang dicoret gampang terbaca
    // sebagai menyala, yaitu kebalikan dari yang sebenarnya.
    const FASE = [['wz-f-crop', 'Crop & zoom'],
                  ['wz-f-skala', 'Seimbangkan ukuran objek'],
                  ['wz-f-kelas', 'Seimbangkan jumlah kelas'],
                  ['wz-f-neg', 'Pulihkan porsi sampel negatif']];
    const nyala = FASE.filter(([id]) => el(id).checked).map(([, n]) => n);
    const mati = FASE.filter(([id]) => !el(id).checked).map(([, n]) => n);
    const fase = (nyala.join(', ') || 'semuanya dimatikan')
      + (mati.length
        ? `<span class="wz-fase-mati">mati: ${mati.join(', ')}</span>` : '');
    // Baris Kelas: fakta persis dari pilihan di langkah 2, supaya pratinjau
    // menyebut kelas apa yang benar-benar masuk versi dan mana yang jadi latar.
    // Jumlah yang bertahan dihitung dari pilihannya sendiri (dibuang & digabung
    // sama-sama menghilangkan satu kelas), jadi selalu tepat.
    const daftarK = (sumber && sumber.daftar_kelas) || [];
    const uk = resep.pra.ubah_kelas || {};
    const petaK = uk.peta || {};
    const namaK = uk.nama || {};
    const latarK = daftarK.filter((k) => petaK[k.i] === null);
    const gabungK = daftarK.filter((k) => typeof petaK[k.i] === 'number');
    const renameK = daftarK.filter((k) => namaK[k.i] !== undefined);
    let kelasTinjau = '';
    if (daftarK.length) {
      const sisa = daftarK.length - latarK.length - gabungK.length;
      const det = [];
      if (latarK.length) det.push(`${latarK.map((k) => k.nama).join(', ')} &rarr; latar`);
      if (gabungK.length) det.push(`${gabungK.length} digabung`);
      if (renameK.length) det.push(`${renameK.length} diganti nama`);
      const teks = det.length ? `${sisa} dari ${daftarK.length} kelas · ${det.join(' · ')}`
                              : `${daftarK.length} kelas, semua dipakai`;
      kelasTinjau = `<div><span>Kelas</span><b>${teks}</b></div>`;
    }
    t.innerHTML =
      kelasTinjau +
      `<div><span>Preprocessing</span><b>${daftar('pra').join(', ') || '-'}</b></div>` +
      `<div><span>Augmentasi</span><b>${daftar('aug').join(', ') || 'dimatikan'}</b></div>` +
      `<div><span>Salinan per gambar</span><b>${el('wz-salin').value}</b></div>` +
      `<div><span>Fase lanjutan</span><b class="wz-fase-tinjau">${fase}</b></div>` +
      `<div><span>Pembagian</span><b>${rasioTeks().replace(/,/g, ' / ')}</b></div>`;
    el('wz-perkira').textContent = 'menghitung perkiraan…';
    const r = await kirimResep('/api/versi/estimasi');
    if (!r || !r.ok) { el('wz-perkira').textContent = (r && r.error) || 'gagal'; return; }
    // Sumber kebenarannya server, bukan ingatan klien: sesi bisa saja sudah
    // punya rencana dari kunjungan sebelumnya di tab yang sama.
    if (belahMode() === 'bocor') { adaRencana = !!r.berencana; segarkanBelah(); }
    // Sama seperti panel ekspor: rasio yang tidak terbaca tetap dipakai
    // sebagai bawaan, tetapi dikatakan di sebelah angka yang terpengaruh.
    const keluhan = r.rasio_pesan
      ? `<span class="split-warn">${r.rasio_pesan}</span><br>` : '';
    // Tiga fase penambah menumpang pipeline augmentasi. Kalau seluruh
    // transform dimatikan di langkah 5, ketiganya tidak menghasilkan apa pun
    // — sementara sakelarnya di sana tetap tampak menyala dan tidak
    // menceritakan itu kepada siapa pun.
    const matiAug = r.ada_aug === false
      ? '<span class="split-warn">Semua transform augmentasi dimatikan, jadi '
        + 'Salinan per gambar, Seimbangkan jumlah kelas, dan Pulihkan porsi '
        + 'sampel negatif ikut tidak berjalan.</span><br>' : '';
    el('wz-perkira').innerHTML = keluhan + matiAug +
      `<b>${r.n.toLocaleString('id')}</b> gambar diperkirakan ` +
      `(${r.n_sumber.toLocaleString('id')} sumber + ${r.tambahan.toLocaleString('id')} hasil), ` +
      `sekitar <b>${mb(r.byte)}</b> di disk. ` +
      `Disk sistem tersedia sekarang: <b>${mb(r.disk_kosong)}</b> kosong.` +
      (r.cukup ? '' : `<span class="split-warn"> Ruang tidak cukup untuk ` +
        `versi ini (perlu sisa cadangan di atas ukuran versinya).</span>`) +
      `<span class="halus">Perkiraan, bukan janji: berapa hasil augmentasi ` +
      `yang ditolak penjaga keterbacaan baru diketahui saat dijalankan.</span>`;
    el('wz-buat').disabled = !r.cukup;
  }

  el('wz-buat').onclick = async () => {
    const c = encodeURIComponent(el('wz-catatan').value || '');
    const r = await fetch(
      `/api/versi/mulai?split=${encodeURIComponent(rasioTeks())}&catatan=${c}`
      + `&belah=${encodeURIComponent(belahMode())}`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resep: kumpulkanResep() }) })
      .then((x) => x.json()).catch(() => null);
    if (!r || !r.ok) { alert((r && r.error) || 'gagal memulai'); return; }
    wz.querySelector('.wz-langkah').hidden = true;
    el('wz-kerja').hidden = false;
    el('wz-kerja-judul').textContent = `Membuat v${r.nomor}…`;
    pantauKerja();
  };

  function pantauKerja() {
    const jam = setInterval(async () => {
      const k = await fetch('/api/versi/kemajuan').then((r) => r.json()).catch(() => null);
      if (!k) return;
      const p = Math.max(0, Math.min(100, k.persen || 0));
      el('wz-fill').style.width = p + '%';
      el('wz-persen').textContent = p.toFixed(0) + '%';
      el('wz-fase').textContent =
        (k.fase_nama || '') + (k.fase_ke ? ` (${k.fase_ke}/${k.fase_dari})` : '')
        + (k.dipulangkan
           ? ` \u00b7 ${k.dipulangkan} dikembalikan ke Anotasi` : '');
      if (k.selesai) { clearInterval(jam); location.reload(); }
      if (k.batal) { clearInterval(jam); location.reload(); }
      if (k.galat) {
        clearInterval(jam);
        el('wz-fase').textContent = 'Gagal: ' + k.galat;
      }
    }, 700);
  }
  el('wz-batal').onclick = () => {
    if (confirm('Hentikan pembuatan versi? Hasil setengah jadi dibuang.')) {
      fetch('/api/versi/batal', { method: 'POST' });
    }
  };

  // ------------------------------------------- foto latar ruang detektor
  //
  // Pelat projek DITAMBAHKAN ke pelat bawaan, tidak menggantikannya, dan
  // angkanya disebutkan supaya orang tahu berapa yang sebenarnya dipakai.
  // Pratinjaunya sengaja menampilkan PELAT JADI, bukan foto yang tadi
  // diunggah: yang perlu dinilai mata adalah hasil olahannya — bantalan
  // terbuang, warna dinetralkan, terang divariasikan — dan kalau yang
  // ditunjukkan fotonya sendiri, kekeliruan di ketiga langkah itu tidak
  // pernah terlihat.
  async function gambarLatar() {
    const wadah = el('lt-daftar');
    const info = el('lt-info');
    if (!wadah) return;
    const r = await fetch('/api/latar').then((x) => x.json()).catch(() => null);
    if (!r || !r.ok) {
      wadah.innerHTML = '';
      info.textContent = (r && r.error) || 'gagal membaca foto latar';
      return;
    }
    wadah.innerHTML = r.foto.map((f) => `
      <figure class="lt-kartu">
        <img src="/api/latar/pratinjau?nama=${encodeURIComponent(f.nama)}&t=${Date.now()}"
             alt="Pelat latar dari ${f.nama}">
        <figcaption><span class="lt-mode-cap">${
          f.mode === 'asli' ? 'warna asli' : 'dinetralkan'}</span>
          <button class="lt-buang" type="button" data-nama="${f.nama}"
            title="Hapus foto latar ini">&times;</button></figcaption>
      </figure>`).join('');
    const sisa = r.maks - r.foto.length;
    el('lt-berkas').disabled = sisa <= 0;
    document.querySelector('.lt-pilih').classList.toggle('lt-penuh', sisa <= 0);
    info.textContent = r.foto.length
      ? `${r.n_pelat} pelat dari ${r.foto.length} foto, dipakai bersama `
        + `${r.n_bawaan} pelat bawaan`
        + (sisa > 0 ? ` · bisa tambah ${sisa} lagi` : ' · sudah penuh')
      : `Belum ada. Augmentasi memakai ${r.n_bawaan} pelat bawaan aplikasi.`;
    wadah.querySelectorAll('.lt-buang').forEach((b) => {
      b.onclick = async () => {
        if (!confirm(`Hapus foto latar ${b.dataset.nama}? Pelat yang dibuat `
                     + 'darinya ikut hilang.')) return;
        await fetch(`/api/latar/buang?nama=${encodeURIComponent(b.dataset.nama)}`,
                    { method: 'POST' });
        gambarLatar();
      };
    });
  }

  // Keterangan mode ditulis di sini, bukan di templat, supaya ia berubah
  // mengikuti pilihan yang sedang aktif. Dua kalimat yang menjelaskan
  // AKIBATNYA, bukan cara kerjanya: yang perlu diputuskan orang adalah mana
  // yang cocok untuk ruangannya.
  const KET_MODE = {
    netral: 'Warna lampu pada foto dibuang, lalu tiap pelat diberi sedikit '
      + 'variasi suhu warna. Pilih ini kalau lampu ruanganmu berganti-ganti '
      + 'warna — augmentasi akan menambahkan warna lampunya sendiri, dan '
      + 'warna yang terlanjur terekam di foto akan bertumpuk dengannya.',
    asli: 'Warna foto tidak disentuh sama sekali; yang berubah hanya '
      + 'terang-gelapnya. Pilih ini kalau lampu ruanganmu memang selalu satu '
      + 'warna itu dan kamu ingin pelatnya persis seperti aslinya.',
  };
  function modeLatar() {
    const r = document.querySelector('#lt-mode input:checked');
    return r ? r.value : 'netral';
  }
  if (el('lt-mode')) {
    const perbarui = () => {
      el('lt-ket').textContent = KET_MODE[modeLatar()];
      el('lt-mode').querySelectorAll('.seg-opt').forEach((o) => {
        o.toggleAttribute('data-on', o.querySelector('input').checked);
      });
    };
    el('lt-mode').addEventListener('change', perbarui);
    perbarui();
  }

  if (el('lt-berkas')) {
    el('lt-berkas').onchange = async (ev) => {
      const berkas = [...ev.target.files];
      ev.target.value = '';
      const info = el('lt-info');
      for (const f of berkas) {
        info.textContent = `mengunggah ${f.name}…`;
        const r = await fetch(
          `/api/latar?name=${encodeURIComponent(f.name)}`
          + `&mode=${encodeURIComponent(modeLatar())}`,
          { method: 'PUT', body: f }).then((x) => x.json()).catch(() => null);
        // Berhenti di kegagalan pertama, dengan sebabnya: melanjutkan diam-diam
        // membuat orang mengira semuanya masuk padahal batasnya sudah kena.
        if (!r || !r.ok) {
          info.textContent = (r && r.error) || 'gagal mengunggah';
          break;
        }
      }
      gambarLatar();
    };
    gambarLatar();
  }

  // Kalau halaman dibuka saat masih ada pekerjaan berjalan, susul saja.
  // muatSumber() tidak jalan di jalur ini, jadi Nama versi + judul diisi dari
  // nomor yang sudah dicatat pekerjaannya — kalau tidak, keduanya kosong.
  fetch('/api/versi/kemajuan').then((r) => r.json()).then((k) => {
    if (k && k.jalan) {
      wz.hidden = false;
      el('vs-mulai').hidden = true;
      wz.querySelector('.wz-langkah').hidden = true;
      el('wz-kerja').hidden = false;
      if (k.nomor) {
        el('wz-nama').value = 'v' + k.nomor;
        el('wz-kerja-judul').textContent = `Membuat v${k.nomor}…`;
      }
      pantauKerja();
    }
  }).catch(() => {});
})();
