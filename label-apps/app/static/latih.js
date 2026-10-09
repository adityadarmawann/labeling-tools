/* Halaman Training.
 *
 * Tiga hal yang menentukan bentuk berkas ini:
 *
 * 1. SETELAN WARNA BUKAN PILIHAN BEBAS. Ia mengikuti versi yang dipilih.
 *    v13 melaporkan mAP50-95 0,9499 lalu benar 0 dari 7 pada foto RVM
 *    sungguhan karena warna dikunci di dua sisi sekaligus. Jadi begitu versi
 *    diganti, panel warna ikut berubah dan angkanya disetel ulang.
 *
 * 2. KEMAJUAN DIBACA BERKALA, BUKAN DITEBAK. Servernya membaca results.csv
 *    yang ditulis Ultralytics tiap epoch, jadi di sini cukup menanyakannya
 *    dan menggambar. Tidak ada penghitung waktu lokal yang berjalan sendiri —
 *    penghitung semacam itu selalu meleset begitu tabnya ditinggal.
 *
 * 3. HALAMAN INI DITINGGAL BERJAM-JAM. Pembaruan berkala berhenti sendiri
 *    ketika tabnya tidak terlihat, dan menyala lagi saat dibuka. Training
 *    berjalan di subproses terlepas, jadi tidak ada yang hilang.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const isi = $('tr-isi');
  if (!isi) return;
  const bolehKelola = isi.dataset.kelola === '1';

  let BAHAN = null;       // versi, bobot, preset, batas
  let ANTREAN = [];       // percobaan yang disusun tapi belum dikirim
  let timer = null;

  // ---------------------------------------------------------------- bantu
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

  const angka = (x, d = 0) => Number(x ?? 0).toLocaleString('id',
    {minimumFractionDigits: d, maximumFractionDigits: d});

  /* Durasi ditulis sebagai "2j 14m", bukan "8040 detik". Yang dibaca orang
     dari angka ini adalah "masih lama atau sebentar lagi", dan detik mentah
     menuntut mereka membaginya sendiri. */
  function durasi(det) {
    det = Math.max(0, Math.round(Number(det) || 0));
    const j = Math.floor(det / 3600), m = Math.floor((det % 3600) / 60);
    if (j) return `${j}j ${String(m).padStart(2, '0')}m`;
    if (m) return `${m}m ${String(det % 60).padStart(2, '0')}d`;
    return `${det}d`;
  }

  async function ambil(url, opsi) {
    const r = await fetch(url, opsi);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  // ============================================================
  // FORM
  // ============================================================

  /* Setelan dibagi dua: yang hampir selalu disentuh orang, dan sisanya di
     balik "Setelan lanjutan". Menampilkan tiga puluh kotak angka sekaligus
     bukan memberi kendali — ia membuat yang penting tenggelam. */
  /* Keterangan dipindah ke tooltip (title), tidak lagi dicetak di bawah tiap
     kotak. Empat baris penjelasan di bawah empat kotak yang berjajar membuat
     formnya terbaca seperti dokumen, bukan seperti form — dan yang paling
     sering dibaca orang justru cuma angkanya. Yang benar-benar perlu
     peringatan tetap punya satu baris pendek. */
  const PAR_UTAMA = [
    ['epochs', 'Epoch', '', 'Berapa kali seluruh data dilewati saat melatih'],
    ['batch', 'Batch', 'terlalu besar = VRAM habis',
     'Berapa gambar diproses sekaligus tiap langkah'],
    ['imgsz', 'Ukuran gambar', 'piksel',
     'Gambar diperkecil ke ukuran ini sebelum dilatih'],
    ['patience', 'Berhenti otomatis', 'epoch · 0 = tidak pernah',
     'Berhenti kalau hasilnya tidak membaik selama sekian epoch'],
  ];
  const PAR_LANJUT = [
    ['lr0', 'Laju belajar awal', '', 'Seberapa besar langkah perbaikan tiap kali'],
    ['lrf', 'Laju belajar akhir', '', 'Pecahan dari laju belajar awal'],
    ['warmup_epochs', 'Pemanasan', 'epoch', 'Epoch awal dengan laju belajar dinaikkan pelan'],
    ['workers', 'Pembaca data', 'utas', 'Berapa utas dipakai membaca gambar dari disk'],
    ['box', 'Bobot kotak', '', 'Seberapa penting ketepatan letak kotak'],
    ['cls', 'Bobot kelas', '', 'Seberapa penting ketepatan nama kelas'],
    ['dfl', 'Bobot tepi', '', 'Seberapa penting ketepatan tepi kotak'],
    ['scale', 'Variasi ukuran', '', 'Objek diperbesar-perkecil acak sebanyak ini'],
    ['degrees', 'Variasi putaran', 'derajat', 'Gambar diputar acak sampai sekian derajat'],
    ['translate', 'Variasi geser', '', 'Gambar digeser acak sebanyak ini'],
    ['fliplr', 'Balik kiri-kanan', 'peluang', 'Peluang gambar dicerminkan mendatar'],
    ['flipud', 'Balik atas-bawah', 'peluang', 'Peluang gambar dicerminkan tegak'],
    ['mosaic', 'Gabung 4 gambar', 'peluang',
     'Empat gambar ditempel jadi satu. Terlalu tinggi membuat objek jadi kecil sekali'],
    ['close_mosaic', 'Matikan gabung di akhir', 'epoch',
     'Sekian epoch terakhir dilatih tanpa penggabungan'],
    ['copy_paste', 'Tempel objek antar-gambar', 'peluang',
     'Objek dari gambar lain ditempelkan. Merusak konteks ruangan'],
    ['mask_ratio', 'Kehalusan mask', '', 'Khusus segmentasi'],
  ];
  /* hsv_h / hsv_s / hsv_v / bgr SENGAJA TIDAK ADA DI SINI.
     Keempatnya ditentukan sepenuhnya oleh mode warna versinya, di server
     (mode_warna.par_latih). Menampilkannya sebagai kotak isian berarti
     membuka jalan untuk memasang kombinasi yang tidak sejalan dengan cara
     versinya diaugmentasi — dan kombinasi seperti itu menghasilkan model yang
     angkanya bagus lalu gagal di ruang detektor. Yang ditampilkan cukup
     AKIBATNYA, dalam kalimat biasa, di panel mode di sebelah kiri. */

  /* Parameter RF-DETR — daftar yang BERBEDA dari YOLO karena mesinnya berbeda.
     RF-DETR tak punya mosaic/hsv/box-cls-dfl; yang penting justru resolusi
     (kelipatan 56, naik = objek kecil seperti bola lebih terbaca) dan batch
     efektif = batch_size × akumulasi gradien (target 16). Dua laju belajar:
     satu untuk kepala deteksi, satu lebih kecil untuk backbone DINOv2 pralatih.
     Nilai bawaan & batasnya datang dari server (BAHAN.preset_rfdetr /
     BAHAN.batas_rfdetr), persis pola preset YOLO. */
  const PAR_RFDETR = [
    ['epochs', 'Epoch', '', 'Berapa kali seluruh data dilewati saat melatih'],
    ['batch_size', 'Batch', 'terlalu besar = VRAM habis',
     'Berapa gambar diproses sekaligus tiap langkah'],
    ['grad_accum_steps', 'Akumulasi gradien', '× batch = batch efektif',
     'batch × ini = batch efektif. Cara menaikkan batch efektif tanpa menambah VRAM (target 16)'],
    ['resolution', 'Resolusi', 'piksel · kelipatan 56',
     'Gambar diproses pada ukuran ini. Lebih tinggi = objek kecil (bola) lebih terbaca, tapi lebih berat. Dibulatkan ke kelipatan 56'],
    ['lr', 'Laju belajar', '', 'Seberapa besar langkah perbaikan pada kepala deteksi'],
    ['lr_encoder', 'Laju belajar encoder', '',
     'Laju belajar untuk backbone DINOv2, lebih kecil karena sudah pralatih'],
    ['warmup_epochs', 'Pemanasan', 'epoch',
     'Epoch awal dengan laju belajar dinaikkan pelan'],
  ];

  function kotakPar(kunci, label, satuan, jelas, dasar, batas) {
    const p = (dasar || BAHAN.preset)[kunci];
    const b = (batas || BAHAN.batas)[kunci];
    const langkah = Number.isInteger(p) ? 1 : (p < 0.01 ? 0.0001 : 0.01);
    const adaKamus = window.KAMUS && window.KAMUS[kunci];
    return `<label class="tr-p" data-kamus="${esc(kunci)}" title="${esc(jelas || label)}">
      <span class="tr-p-nama">${esc(label)}${adaKamus
        ? `<button type="button" class="km-tanya" data-kamus-key="${esc(kunci)}"
             aria-label="Penjelasan ${esc(kunci)}">?</button>` : ''}</span>
      <input type="number" data-par="${esc(kunci)}" value="${p}" data-bawaan="${p}"
             ${b ? `min="${b[0]}" max="${b[1]}"` : ''} step="${langkah}">
      ${satuan ? `<span class="tr-p-satuan">${esc(satuan)}</span>` : ''}
    </label>`;
  }

  /* ---- Arsitektur (YOLO vs RF-DETR) ------------------------------------ */
  const arsitekturDipilih = () => {
    const s = $('tr-arsitektur');
    return (s && s.value === 'rfdetr') ? 'rfdetr' : 'yolo';
  };
  const rfdetrModelDipilih = () => {
    const s = $('tr-rfdetr-model');
    return (s && s.value) || 'nano';
  };

  function gambarForm() {
    // Isian parameter mengikuti arsitektur: YOLO memakai preset (SmartBin/
    // Basket) + setelan lanjutan; RF-DETR punya daftar sendiri (dari server)
    // dan tidak memisah utama/lanjutan — tujuh angka saja, semuanya penting.
    if (arsitekturDipilih() === 'rfdetr') {
      $('tr-par').innerHTML = PAR_RFDETR.map((x) =>
        kotakPar(x[0], x[1], x[2], x[3],
                 BAHAN.preset_rfdetr || {}, BAHAN.batas_rfdetr || {})).join('');
      $('tr-par-lanjut').innerHTML = '';
    } else {
      $('tr-par').innerHTML = PAR_UTAMA.map((x) => kotakPar(...x)).join('');
      $('tr-par-lanjut').innerHTML = PAR_LANJUT.map((x) => kotakPar(...x)).join('');
    }

    const sel = $('tr-versi');
    const siap = BAHAN.versi.filter((v) => v.siap);
    if (!siap.length) {
      sel.innerHTML = '<option value="">(belum ada versi yang bisa dilatih)</option>';
      $('tr-versi-ket').textContent =
        'Buat versi lebih dulu di halaman Versi. Training memakai pembagian '
        + 'train/valid/test yang sudah dibekukan di sana, bukan isi dataset mentah.';
      return;
    }
    sel.innerHTML = siap.map((v) => {
      const j = v.jumlah || {};
      const n = (j.train || 0) + (j.valid || 0) + (j.test || 0);
      return `<option value="${v.nomor}">v${v.nomor}, ${angka(n)} gambar`
           + `${v.catatan ? ' · ' + esc(v.catatan.slice(0, 40)) : ''}</option>`;
    }).join('');
    sel.onchange = pilihVersi;
    pilihVersi();
  }

  /* Versi berganti -> panel warna berganti -> angka warna ikut disetel.
     Inilah sinkronisasi yang diminta, dan ia berjalan otomatis supaya tidak
     bergantung pada orang mengingat untuk menyetelnya. */
  function pilihVersi() {
    const n = Number($('tr-versi').value || 0);
    const v = BAHAN.versi.find((x) => x.nomor === n);
    if (!v) return;

    const j = v.jumlah || {};
    const kelas = Object.keys(v.kelas || {}).length;
    $('tr-versi-ket').innerHTML =
      `train <b>${angka(j.train || 0)}</b> · valid <b>${angka(j.valid || 0)}</b>`
      + ` · test <b>${angka(j.test || 0)}</b>`
      + (kelas ? ` · ${kelas} kelas` : '')
      + (v.negatif ? ` · ${angka(v.negatif)} sampel negatif` : '')
      + (v.jenis ? ` · ${esc(v.jenis)}` : '');

    // Bentuk anotasi versinya menentukan tugas yang masuk akal.
    if (v.jenis && v.jenis.toLowerCase().includes('kotak')) {
      $('tr-tugas').value = 'detect';
      $('tr-tugas-ket').textContent =
        'Versi ini beranotasi kotak, jadi segmentasi tidak bisa dilatih darinya.';
    } else {
      $('tr-tugas-ket').textContent = '';
    }

    // Bawaannya mengikuti cara versinya dibuat — itu yang hampir selalu
    // benar. Orang tetap boleh menggantinya, dan kalau berbeda, peringatannya
    // menyebutkan akibatnya.
    const w = v.warna || {};
    const asal = w.mode || 'bentuk';
    const box = $('tr-warna');
    // Panel warna cuma untuk preset yang memakainya (SmartBin). Basket: sembunyi.
    box.hidden = !presetPakaiWarna();
    const r = box.querySelector(`input[name="tr-mode"][value="${asal}"]`);
    if (r && !box.dataset.disentuh) r.checked = true;
    gambarMode();
  }

  const modeDipilih = () => {
    const r = document.querySelector('input[name="tr-mode"]:checked');
    return r ? r.value : 'bentuk';
  };

  /* ---- Preset (SmartBin vs Basket) ------------------------------------- */
  function presetTerpilih() {
    const sel = $('tr-preset');
    const id = sel ? sel.value : (BAHAN && BAHAN.preset_bawaan) || 'rvm';
    const daftar = (BAHAN && BAHAN.preset_daftar) || [];
    return daftar.find((p) => p.id === id) || daftar[0] || null;
  }

  // Panel Bentuk/Warna hanya untuk preset yang memakainya (SmartBin). Preset
  // Basket mengurus warnanya sendiri (variasi rona di preset), jadi panelnya
  // disembunyikan supaya tidak membingungkan.
  function presetPakaiWarna() {
    // RF-DETR tak memakai panel Bentuk/Warna sama sekali — ia mengurus
    // augmentasinya sendiri — jadi panelnya selalu tersembunyi di sana.
    if (arsitekturDipilih() === 'rfdetr') return false;
    const p = presetTerpilih();
    return p ? !!p.warna : true;
  }

  /* Terapkan preset terpilih: muat ulang nilai bawaan tiap kotak setelan dari
     preset itu, perbarui penjelasan awam, dan tampilkan/sembunyikan panel
     warna. Dipanggil saat init dan tiap selektor preset berubah. */
  function terapkanPreset() {
    const p = presetTerpilih();
    if (!p) return;
    document.querySelectorAll('#tr-form [data-par]').forEach((el) => {
      const v = p.par[el.dataset.par];
      if (v !== undefined) { el.value = v; el.dataset.bawaan = v; }
    });
    const ket = $('tr-preset-ket');
    if (ket) ket.textContent = p.jelas || '';
    const box = $('tr-warna');
    if (box) box.hidden = !presetPakaiWarna();
  }

  /* Satu tempat yang menggambar seluruh panel warna, dipanggil ulang tiap
     kali versinya atau modenya berganti. Isinya ditulis dari sudut pandang
     HASILNYA — apa yang akan dipelajari model — bukan dari sudut pandang
     setelannya. "Warna dibuka di versinya" itu benar tetapi hanya berarti
     bagi yang sudah tahu apa yang dibuka dan kenapa. */
  function gambarMode() {
    const n = Number($('tr-versi').value || 0);
    const v = BAHAN.versi.find((x) => x.nomor === n) || {};
    const w = v.warna || {};
    const asal = w.mode || 'bentuk';
    const m = modeDipilih();
    const box = $('tr-warna');
    const beda = m !== asal;

    // Satu arah yang MUSTAHIL, dan itu bukan soal selera: versi yang dibangun
    // dengan ronanya diacak ~50 derajat sudah kehilangan informasi warnanya
    // di dalam datanya sendiri. Tidak ada setelan waktu-latih yang bisa
    // mengembalikannya.
    const mustahil = (m === 'warna' && asal === 'bentuk');
    // Tidak ada lagi tingkat ok/beda/awas yang mewarnai seluruh panel: kedua
    // mode sah, dan menandai salah satunya "lulus" membuat yang lain terbaca
    // sebagai kesalahan. Yang tersisa cuma catatan untuk yang memang mustahil.
    $('tr-warna-judul').textContent = m === 'warna'
      ? 'Model akan mengenali dari WARNA dan bentuk'
      : 'Model akan mengenali dari BENTUK, bukan warna';

    const catat = $('tr-warna-catat');
    catat.hidden = !mustahil;
    if (mustahil) {
      // Fakta tentang DATANYA, bukan penilaian atas pilihannya — lengkap
      // dengan jalan keluarnya, supaya tidak berhenti di kabar buruk.
      catat.innerHTML = `<b>Versi v${n} tidak menyimpan warna aslinya.</b> `
        + 'Saat versi ini dibangun, ronanya sengaja diacak lebar, jadi tidak '
        + 'ada lagi warna asli di dalam datanya untuk dipelajari model. Untuk '
        + 'model yang membedakan lewat warna, buat versi baru dan pilih '
        + '"Warna ikut menentukan" di langkah Augmentasi.';
    }

    let pesan;
    if (mustahil) {
      pesan = 'Model diminta memakai warna kemasan sebagai penanda kelas, '
        + 'seperti untuk produk yang bentuknya mirip tapi kemasannya beda '
        + 'warna.';
    } else if (beda) {
      pesan = `Versi v${n} dibuat dengan warna dipertahankan, tetapi model ini `
        + 'akan dilatih untuk mengabaikan warna. Itu boleh, hanya kurang kuat '
        + 'daripada mengacak warnanya sejak versinya dibuat.';
    } else if (m === 'warna') {
      pesan = 'Versi ini dibuat dengan warna dipertahankan, jadi warna kemasan '
        + 'boleh jadi penanda kelas. Cocok untuk membedakan produk yang '
        + 'bentuknya mirip tapi kemasannya beda warna.';
    } else {
      pesan = 'Versi ini dibuat dengan warnanya sengaja diacak, jadi model '
        + 'tidak bisa menebak hanya dari warna dan terpaksa belajar bentuknya. '
        + 'Cocok untuk botol / kaleng / tetra.';
    }
    $('tr-warna-pesan').textContent = pesan;

    /* Angkanya TERUKUR pada pipeline yang sebenarnya, bukan taksiran dari
       nilai setelannya. Rona digeser jauh lebih lebar oleh augmentasi versi
       (~50 derajat) daripada oleh setelan waktu-latih (~5 derajat), jadi
       kalimat yang cuma menyebut "saat melatih" akan menyesatkan. Dijaga
       test_klaim_layar_sesuai_kenyataan di tests/test_evaluasi.py. */
    // Disembunyikan saat mustahil: catatannya sudah mengatakan hal yang sama
    // dengan lebih langsung, dan dua paragraf yang berputar di keterangan
    // yang sama membuat panelnya terbaca seperti dokumen, bukan pilihan.
    $('tr-warna-nilai').hidden = mustahil;
    $('tr-warna-nilai').textContent = m === 'warna'
      ? 'Warna asli dipertahankan, ronanya praktis tidak digeser. Yang '
        + 'divariasikan gelap-terangnya (0,5x sampai 1,2x) dan sedikit '
        + 'kepekatan warnanya, supaya model tetap tahan saat lampu berubah.'
      : 'Objek yang sama akan dilihat model dalam banyak warna berbeda. '
        + 'Ronanya digeser rata-rata sekitar 50 derajat dari 360, dan pada 1 '
        + 'dari 10 gambar merah dan biru ditukar. Warna jadi tidak bisa '
        + 'diandalkan, sehingga model terpaksa belajar bentuknya.';
  }

  function bacaPar() {
    const out = {};
    document.querySelectorAll('#tr-form [data-par]').forEach((el) => {
      const v = Number(el.value);
      if (Number.isFinite(v)) out[el.dataset.par] = v;
    });
    return out;
  }

  function bacaSatu() {
    const rf = arsitekturDipilih() === 'rfdetr';
    return {
      nama: $('tr-nama').value.trim(),
      catatan: $('tr-catatan').value.trim(),
      // RF-DETR hanya deteksi kotak, tak pakai bobot YOLO atau mode warna; yang
      // menggantikan "bobot awal" adalah ukuran model (nano/small/medium/large).
      tugas: rf ? 'detect' : $('tr-tugas').value,
      bobot: rf ? '' : $('tr-bobot').value,
      mode_warna: rf ? '' : modeDipilih(),
      arsitektur: rf ? 'rfdetr' : 'yolo',
      rfdetr_model: rf ? rfdetrModelDipilih() : '',
      par: bacaPar(),
    };
  }

  // ============================================================
  // ANTREAN (batch)
  // ============================================================

  function gambarAntrean() {
    const d = $('tr-batch-daftar');
    if (!ANTREAN.length) {
      d.innerHTML = '<span class="tr-diam">Belum ada yang diantrekan. '
        + 'Tombol Jalankan akan memakai setelan di atas apa adanya.</span>';
      return;
    }
    d.innerHTML = ANTREAN.map((x, i) => {
      // Ringkasan parameter mengikuti arsitektur: RF-DETR memakai batch_size /
      // resolution (bukan batch / imgsz milik YOLO) dan ditandai ukuran modelnya.
      const rf = x.arsitektur === 'rfdetr';
      const ringkas = rf
        ? `${x.par.epochs} epoch · batch ${x.par.batch_size} · ${x.par.resolution}px`
          + ` · RF-DETR ${esc(x.rfdetr_model || '')}`
        : `${x.par.epochs} epoch · batch ${x.par.batch} · ${x.par.imgsz}px · ${esc(x.tugas)}`;
      return `
      <div class="tr-antre">
        <span class="tr-antre-no">${i + 1}</span>
        <span class="tr-antre-nama">${esc(x.nama || '(tanpa nama)')}</span>
        <span class="tr-antre-par">${ringkas}</span>
        <span class="spacer"></span>
        <button class="chip" type="button" data-buang="${i}">Buang</button>
      </div>`;
    }).join('');
    d.querySelectorAll('[data-buang]').forEach((b) => {
      b.onclick = () => { ANTREAN.splice(Number(b.dataset.buang), 1); gambarAntrean(); };
    });
  }

  function galat(pesan) {
    const g = $('tr-galat');
    g.hidden = !pesan;
    g.textContent = pesan || '';
  }

  /* Gerbang tombol Jalankan mengikuti backend yang DIPILIH. Lokal menuntut
     siap_latih() (ultralytics/GPU di mesin ini); Kaggle menuntut akun
     terkonfigurasi — dan sengaja bisa dipakai meski lokal tidak siap, karena
     itulah gunanya cadangan: server CPU pun bisa melatih di Kaggle. */
  function terapkanBackend() {
    if (!BAHAN) return;
    const b = ($('tr-backend') && $('tr-backend').value) || 'lokal';
    const rf = arsitekturDipilih() === 'rfdetr';
    // Gerbang = backend × arsitektur. Kaggle melatih YOLO MAUPUN RF-DETR (kernel
    // memasang rfdetr sendiri), jadi kesiapannya cukup kaggle_siap. Lokal:
    // RF-DETR menuntut rfdetr terpasang di mesin ini (rfdetr_siap), YOLO menuntut
    // ultralytics/GPU (siap).
    let siap, alasan;
    if (b === 'kaggle') { siap = BAHAN.kaggle_siap; alasan = BAHAN.kaggle_alasan; }
    else if (rf) { siap = BAHAN.rfdetr_siap; alasan = BAHAN.rfdetr_alasan; }
    else { siap = BAHAN.siap; alasan = BAHAN.alasan; }
    // Saklar 2-GPU hanya relevan untuk RF-DETR + Kaggle (T4x2). Dev lokal 1 GPU.
    const mg = $('tr-rfdetr-multigpu-bungkus');
    if (mg) mg.hidden = !(rf && b === 'kaggle');
    if ($('tr-jalankan')) $('tr-jalankan').disabled = (siap === false);
    if ($('tr-tambah')) $('tr-tambah').disabled = (siap === false);
    galat(siap === false ? (alasan || 'backend ini belum siap') : '');
    const ket = $('tr-backend-ket');
    if (ket) {
      // kaggle_akun = [{user, pakai_jam, sisa_jam, kuota_jam, habis}] — tunjukkan
      // sisa jatah minggu ini tiap akun, supaya terlihat mana yang masih bisa.
      const akun = (BAHAN.kaggle_akun || []).map((a) =>
        a.habis ? `${a.user} (kuota habis)` : `${a.user} (sisa ~${a.sisa_jam}j)`
      ).join(' · ') || '-';
      ket.textContent = b === 'kaggle'
        ? `akun: ${akun} · di luar PC ini, boleh berbarengan dengan training `
          + 'lokal; run panjang disambung otomatis'
        : 'GPU mesin ini, satu training pada satu waktu';
    }
    gambarAkun();                 // panel kelola akun Kaggle ikut backend
  }

  /* ---- Akun Kaggle PER-USER (panel di form Setup Training) ----
     Tiap user mendaftarkan akun Kaggle-nya sendiri: tempel username + API token,
     tekan Test (kernel uji GPU di akunnya -> Berhasil/Gagal), lalu Simpan. Token
     dikirim lewat BODY (tak masuk URL/log) dan tak pernah kembali kecuali dimask. */
  function gambarAkun() {
    const wrap = $('tr-akun');
    if (!wrap || !BAHAN) return;
    const b = ($('tr-backend') && $('tr-backend').value) || 'lokal';
    wrap.hidden = !(b === 'kaggle' && BAHAN.kaggle_mungkin);
    if (wrap.hidden) return;
    const daftar = $('tr-akun-daftar');
    const akun = BAHAN.kaggle_akun || [];
    if (!akun.length) {
      daftar.innerHTML = '<p class="tr-bantu">Belum ada akun. Tekan "+ Tambah akun", '
        + 'tempel username + API token Kaggle, lalu Test.</p>';
    } else {
      daftar.innerHTML = akun.map((a) => `
        <div class="tr-akun-baris">
          <span class="tr-akun-nama">${esc(a.user)}${a.utama ? ' <b>(utama)</b>' : ''}</span>
          <span class="tr-akun-stat">${a.habis ? 'kuota habis' : 'sisa ~' + a.sisa_jam + 'j'}`
        + ` · ${esc(a.token_mask || '')}</span>
          <span class="spacer"></span>
          ${a.utama ? '' : `<button class="chip" type="button" data-utama="${esc(a.user)}">Jadikan utama</button>`}
          <button class="chip tr-akun-hapus" type="button" data-hapus="${esc(a.user)}">Hapus</button>
        </div>`).join('');
      daftar.querySelectorAll('[data-utama]').forEach((btn) => {
        btn.onclick = () => aksiAkun('/api/latih/akun/utama', { kaggle_user: btn.dataset.utama });
      });
      daftar.querySelectorAll('[data-hapus]').forEach((btn) => {
        btn.onclick = async () => {
          if (!confirm(`Hapus akun Kaggle "${btn.dataset.hapus}"?`)) return;
          await aksiAkun('/api/latih/akun/hapus', { kaggle_user: btn.dataset.hapus });
        };
      });
    }
    const mon = $('tr-akun-monitor');
    if (mon) mon.hidden = !BAHAN.admin;
  }

  async function aksiAkun(url, payload) {
    try {
      const r = await ambil(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (r.ok === false) { galat(r.error || 'gagal'); return; }
      if (r.akun) BAHAN.kaggle_akun = r.akun;
      BAHAN.kaggle_siap = (BAHAN.kaggle_akun || []).length > 0;
      gambarAkun(); terapkanBackend();
    } catch (e) { galat('gagal: ' + e); }
  }

  let _verifPoll = null;
  function setelTest(keadaan, pesan) {
    const h = $('tr-akun-hasil');
    if (h) { h.textContent = pesan || ''; h.className = 'tr-bantu tr-test-' + (keadaan || ''); }
  }

  // SATU alur ringkas: Test (verifikasi GPU penuh) -> kalau Berhasil, langsung
  // SIMPAN otomatis; kalau Gagal, tampilkan alasan. Tak ada langkah "Simpan"
  // terpisah supaya tak membingungkan.
  async function testAkun() {
    const user = ($('tr-akun-user').value || '').trim();
    const tok = ($('tr-akun-token').value || '').trim();
    if (!user || !tok) { setelTest('gagal', 'isi username Kaggle dan API token dulu'); return; }
    const tbl = $('tr-akun-test');
    setelTest('', 'menguji akun di Kaggle (menjalankan kernel GPU kecil, beberapa menit)…');
    if (tbl) tbl.disabled = true;
    let id;
    try {
      const r = await ambil('/api/latih/akun/test', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kaggle_user: user, token: tok }),
      });
      if (r.ok === false) { setelTest('gagal', r.error || 'gagal memulai Test'); if (tbl) tbl.disabled = false; return; }
      id = r.id;
    } catch (e) { setelTest('gagal', 'gagal: ' + e); if (tbl) tbl.disabled = false; return; }
    if (_verifPoll) clearInterval(_verifPoll);
    _verifPoll = setInterval(async () => {
      try {
        const s = await ambil('/api/latih/akun/test-status?id=' + encodeURIComponent(id));
        const st = s.status || {};
        if (st.keadaan === 'pending') return;
        clearInterval(_verifPoll); _verifPoll = null;
        if (st.keadaan === 'berhasil') { await _simpanAkun(user, tok); }
        else { if (tbl) tbl.disabled = false; setelTest('gagal', '✗ Gagal: ' + (st.alasan || st.keadaan)); }
      } catch (e) { /* biarkan poll lanjut sampai jawaban berikut */ }
    }, 5000);
  }

  async function _simpanAkun(user, tok) {
    const tbl = $('tr-akun-test');
    try {
      const r = await ambil('/api/latih/akun/simpan', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kaggle_user: user, token: tok }),
      });
      if (tbl) tbl.disabled = false;
      if (r.ok === false) { setelTest('gagal', r.error || 'gagal menyimpan'); return; }
      if (r.akun) BAHAN.kaggle_akun = r.akun;
      $('tr-akun-user').value = ''; $('tr-akun-token').value = '';
      $('tr-akun-form').hidden = true; setelTest('', '');
      BAHAN.kaggle_siap = true; BAHAN.kaggle_alasan = '';
      gambarAkun(); terapkanBackend();
    } catch (e) { if (tbl) tbl.disabled = false; setelTest('gagal', 'gagal menyimpan: ' + e); }
  }

  async function muatMonitorAkun() {
    const isi = $('tr-akun-monitor-isi');
    if (!isi) return;
    isi.textContent = 'memuat…';
    try {
      const r = await ambil('/api/latih/akun/semua');
      if (r.ok === false) { isi.textContent = r.error || 'gagal'; return; }
      const semua = r.semua || [];
      if (!semua.length) { isi.textContent = 'belum ada user yang mendaftarkan akun Kaggle.'; return; }
      isi.innerHTML = semua.map((u) =>
        `<div class="tr-akun-mon-user"><b>${esc(u.pemilik)}</b>: `
        + (u.akun || []).map((a) =>
          `${esc(a.user)} (${a.habis ? 'habis' : 'sisa ~' + a.sisa_jam + 'j'})`).join(', ')
        + '</div>').join('');
    } catch (e) { isi.textContent = 'gagal memuat: ' + e; }
  }

  /* Mengganti Arsitektur menukar SELURUH bentuk form: RF-DETR menyembunyikan
     kendali khas YOLO (preset SmartBin/Basket, panel Bentuk/Warna, jenis
     tugas, bobot awal) karena ia mengurus semua itu sendiri — deteksi kotak,
     backbone DINOv2 pralatih — dan menampilkan pemilih ukuran model + daftar
     parameternya sendiri. Dipanggil saat init dan tiap selektor arsitektur
     berubah. Gerbang tombol Jalankan (kesiapan) ikut disegarkan. */
  function terapkanArsitektur() {
    if (!BAHAN) return;
    const rf = arsitekturDipilih() === 'rfdetr';
    const tampil = (id, tampak) => { const el = $(id); if (el) el.hidden = !tampak; };
    tampil('tr-rfdetr-model-bungkus', rf);
    tampil('tr-preset-bungkus', !rf);
    tampil('tr-tugas-bungkus', !rf);
    tampil('tr-bobot-bungkus', !rf);
    const wbox = $('tr-warna');
    if (wbox) wbox.hidden = !presetPakaiWarna();   // false untuk rfdetr
    const ket = $('tr-arsitektur-ket');
    if (ket) ket.textContent = rf
      ? 'Bagus untuk objek kecil yang berdesakan, misalnya bola dan pemain '
        + 'basket. RF-DETR membuat variasi gambarnya sendiri saat berlatih '
        + '(warna, ukuran, dan posisi diubah-ubah otomatis), jadi pakai versi '
        + 'dataset yang biasa saja, tak perlu menyalakan efek tambahan '
        + '(variasi warna, blur, dan sejenisnya) waktu membuat versinya.'
      : 'Pilihan umum: cepat dan ringan, cocok untuk kebanyakan kebutuhan.';
    gambarForm();                 // tukar isian parameter (YOLO <-> RF-DETR)
    if (!rf) terapkanPreset();    // pulihkan bawaan preset YOLO
    terapkanBackend();            // segarkan gerbang tombol Jalankan
  }

  // ============================================================
  // DAFTAR: YANG BERJALAN DAN HASILNYA
  // ============================================================

  const LABEL_KEADAAN = {
    antre: 'Menunggu giliran', jalan: 'Berjalan', selesai: 'Selesai',
    gagal: 'Gagal', batal: 'Dihentikan', hilang: 'Terputus',
    tertunda: 'Tertunda',
  };

  /* Pita info khusus training yang di-offload ke Kaggle: lencana + akun + leg,
     tautan ke halaman kernel (log LANGSUNG ada di sana — API tak menyiarkannya,
     jadi inilah cara memantau detik-per-detik), dan pesan ramah dari poller. */
  function barisKaggle(t) {
    if (t.backend !== 'kaggle') return '';
    const k = t.kaggle || {};
    const akun = k.akun ? ` · ${esc(k.akun)}` : '';
    const leg = k.leg ? ` · leg ${esc(String(k.leg))}` : '';
    const url = k.kernel_url
      ? `<a class="chip" href="${esc(k.kernel_url)}" target="_blank"
            rel="noopener" title="Pantau log langsung di Kaggle">lihat di Kaggle ↗</a>`
      : '';
    const pesan = k.pesan ? `<span class="tr-kaggle-pesan">${esc(k.pesan)}</span>` : '';
    return `<div class="tr-kaggle">
      <span class="tr-pil tr-pil-kaggle">Kaggle${akun}${leg}</span>${url}${pesan}</div>`;
  }

  /* Lencana arsitektur di kartu. YOLO dibiarkan polos — ia bawaan dan mayoritas
     — sedang RF-DETR ditandai eksplisit beserta ukuran modelnya, supaya di
     daftar yang bercampur jelas mana yang mana tanpa harus membuka Rincian. */
  function pilArsitektur(t) {
    if (t.arsitektur !== 'rfdetr') return '';
    const m = t.rfdetr_model ? ' ' + esc(t.rfdetr_model) : '';
    return `<span class="tr-pil tr-pil-rfdetr"
      title="Dilatih dengan RF-DETR${m}">RF-DETR${m}</span>`;
  }

  function barisMetrik(m) {
    const k = Object.keys(m || {});
    if (!k.length) return '<span class="tr-diam">belum ada metrik</span>';
    return k.slice(0, 4).map((n) =>
      `<span class="tr-metrik"><i>${esc(n)}</i><b>${angka(m[n] * 100, 1)}%</b></span>`
    ).join('');
  }

  /* Kartu yang SEDANG BERJALAN. Bahasa tata letaknya sengaja sama persis
     dengan kartu hasil (tiga pita: identitas, angka, tindakan) supaya sebuah
     training tidak berubah bentuk hanya karena ia selesai. Bedanya cuma dua:
     ada bilah kemajuan, dan angka besarnya adalah PERSENNYA, bukan metrik.

     Persen yang jadi angka besar, bukan mAP: selagi berjalan yang ditanyakan
     orang "masih lama atau tidak", bukan "sudah sebagus apa". mAP-nya tetap
     ada di deretan pendukung. */
  function kartuJalan(t) {
    // Bilah kemajuan punya tiga keadaan:
    //  - epoch >= 2 berjalan  -> persen DI DALAM epoch itu (akurat, dari durasi
    //    epoch sebelumnya). Terlihat bergerak tiap beberapa menit.
    //  - epoch PERTAMA berjalan -> belum ada durasi acuan, jadi TIDAK ada
    //    persen; bilah "meluncur" + waktu berlalu, jelas bekerja tanpa angka
    //    palsu.
    //  - selesai -> persen keseluruhan.
    const jalan = t.keadaan === 'jalan' || t.keadaan === 'antre';
    const dalamEpoch = t.persen_epoch != null;
    const ep1 = jalan && !dalamEpoch && t.berlalu_epoch != null;
    const pj = Math.max(0, Math.min(100,
      dalamEpoch ? t.persen_epoch : (t.persen || 0)));
    const epNo = t.epoch_berjalan != null ? t.epoch_berjalan : t.epoch;
    const m = t.metrik || {};
    const mk = Object.keys(m).slice(0, 2);
    const rf = t.arsitektur === 'rfdetr';   // RF-DETR tak punya last.pt
    return `<article class="tr-kartu tr-kartu-jalan" data-nomor="${t.nomor}">
      <header class="tr-k-atas">
        <b class="tr-k-nama">${esc(t.nama)}</b>
        <span class="tr-pil tr-pil-${esc(t.keadaan)}">${LABEL_KEADAAN[t.keadaan] || t.keadaan}</span>
        ${pilArsitektur(t)}
        <span class="spacer"></span>
        <span class="tr-k-meta">L${t.nomor}<i>dari</i>v${t.versi}<i>oleh</i>${esc(t.oleh || '?')}</span>
      </header>
      ${barisKaggle(t)}

      <div class="tr-bar${ep1 ? ' tr-bar-kerja' : ''}" role="progressbar"
           aria-valuenow="${ep1 ? '' : pj.toFixed(0)}"
           aria-valuemin="0" aria-valuemax="100"><i style="width:${ep1 ? 100 : pj}%"></i></div>

      <div class="tr-k-isi">
        <div class="tr-skor">
          ${ep1
            ? `<b class="tr-skor-jam">${durasi(t.berlalu_epoch)}</b>
               <i>epoch ${angka(epNo)} dari ${angka(t.epochs)} · berjalan</i>`
            : `<b>${angka(pj, 0)}<span>%</span></b>
               <i>epoch ${angka(epNo)} dari ${angka(t.epochs)}${dalamEpoch ? ' (epoch ini)' : ''}</i>`}
        </div>
        <dl class="tr-k-angka">
          <div><dt>Terpakai</dt><dd>${durasi(t.detik)}</dd></div>
          <div><dt>Perkiraan sisa</dt><dd>${t.sisa ? durasi(t.sisa) : '&mdash;'}</dd></div>
          ${mk.map((k) => `<div><dt>${esc(k)}</dt>
            <dd>${angka(m[k] * 100, 1)}%</dd></div>`).join('')}
        </dl>
      </div>

      <footer class="tr-k-aksi">
        <button class="chip chip-utama" type="button" data-rinci="${t.nomor}">Rincian</button>
        ${t.punya_bobot ? `<span class="tr-unduh">Unduh sementara
          <a class="chip" href="/latih/bobot?nomor=${t.nomor}&jenis=best"
             download="L${t.nomor}-best.pt"
             title="Bobot mAP terbaik SEJAUH INI, bisa diunduh selagi training jalan">best.pt</a>
          <a class="chip" href="/latih/bobot?nomor=${t.nomor}&jenis=last"
             download="L${t.nomor}-last.pt"
             title="${rf ? 'Bobot EMA epoch terakhir SEJAUH INI (diperbarui tiap epoch)'
                         : 'Bobot epoch terakhir yang selesai. Untuk melanjutkan training. Kalau kebetulan diunduh tepat saat epoch berakhir bisa separuh; unduh ulang.'}">last.pt</a>
        </span>` : ''}
        <span class="spacer"></span>
        ${bolehKelola ? `<button class="chip chip-bahaya" type="button"
            data-batal="${t.nomor}">Hentikan</button>` : ''}
      </footer>
    </article>`;
  }

  /* Kartu hasil. Tata letaknya tiga pita mendatar, bukan tumpukan menurun:
     kartunya selebar 1.300px dan versi sebelumnya memakai seperempatnya saja,
     sehingga seluruh isinya menumpuk di kiri dan sisanya kosong melompong.

     Satu angka besar saja yang jadi kepala berita, dan label angka itu TIDAK
     diulang lagi di deretan bawahnya. Versi sebelumnya menampilkan
     "mAP50-95 mask" dua kali, di dua ukuran berbeda, pada kartu yang sama. */
  function kartuHasil(t) {
    const terbaik = t.terbaik || {};
    const utama = t.utama && terbaik[t.utama] !== undefined ? t.utama : null;
    const rusak = ['gagal', 'hilang'].includes(t.keadaan);
    // RF-DETR: dua tindakan berikut adalah jalur KHAS YOLO, jadi disembunyikan.
    // "Uji produksi" memuat .pt sebagai model Ultralytics untuk uji
    // ketergantungan warna; "Lanjutkan" melatih ulang dari .pt lewat Ultralytics.
    // Checkpoint RF-DETR (best.pt) bukan format itu — keduanya akan gagal.
    const rf = t.arsitektur === 'rfdetr';
    // Metrik pendukung: yang utama dibuang supaya tidak muncul dua kali.
    const lain = Object.keys(terbaik).filter((k) => k !== utama).slice(0, 3);

    return `<article class="tr-kartu" data-nomor="${t.nomor}">
      <header class="tr-k-atas">
        <b class="tr-k-nama">${esc(t.nama)}</b>
        <span class="tr-pil tr-pil-${esc(t.keadaan)}">${LABEL_KEADAAN[t.keadaan] || t.keadaan}</span>
        ${pilArsitektur(t)}
        <span class="spacer"></span>
        <span class="tr-k-meta">L${t.nomor}<i>dari</i>v${t.versi}<i>oleh</i>${esc(t.oleh || '?')}
          <i>pada</i>${esc(t.dibuat || '')}</span>
      </header>
      ${barisKaggle(t)}
      ${t.keadaan === 'tertunda' && t.galat
        ? `<p class="tr-k-tunda">${esc(t.galat)}</p>` : ''}

      ${rusak ? `<p class="tr-k-galat">${esc(t.galat || 'berhenti tanpa keterangan')}</p>`
        : `<div class="tr-k-isi">
        ${utama ? `<div class="tr-skor">
          <b>${angka(terbaik[utama] * 100, 1)}<span>%</span></b>
          <i>${esc(utama)}</i>
        </div>` : '<div class="tr-skor tr-skor-kosong"><b>&mdash;</b><i>belum ada metrik</i></div>'}
        <dl class="tr-k-angka">
          ${lain.map((k) => `<div><dt>${esc(k)}</dt>
            <dd>${angka(terbaik[k] * 100, 1)}%</dd></div>`).join('')}
          <div><dt>Epoch</dt><dd>${angka(t.epoch)} / ${angka(t.epochs)}</dd></div>
          <div><dt>Lama</dt><dd>${durasi(t.detik)}</dd></div>
        </dl>
      </div>`}

      <footer class="tr-k-aksi">
        <button class="chip chip-utama" type="button" data-rinci="${t.nomor}">Rincian</button>
        ${t.keadaan === 'tertunda' && t.backend === 'kaggle' && bolehKelola ? `
          <button class="chip chip-utama" type="button" data-sambung="${t.nomor}"
            title="Coba lagi sekarang: lanjutkan dari epoch terakhir di Kaggle">
            Lanjutkan di Kaggle</button>` : ''}
        ${t.punya_bobot && bolehKelola && !rf ? `<button class="chip" type="button"
            data-uji="${t.nomor}">Uji produksi</button>` : ''}
        ${(bolehKelola && (rf ? t.rfdetr_ckpt : t.punya_bobot)) ? `<button class="chip" type="button"
            data-lanjut="${t.nomor}" data-nama="${esc(t.nama)}"
            data-epochs="${t.epochs || 400}" data-ars="${rf ? 'rfdetr' : 'yolo'}"
            title="${rf ? 'Lanjutkan training ini sampai total epoch lebih banyak (resume dari checkpoint, bukan dari nol)' : 'Latih lagi mulai dari bobot training ini'}">Lanjutkan</button>` : ''}
        ${t.punya_bobot ? `<span class="tr-unduh">Unduh
          <a class="chip" href="/latih/bobot?nomor=${t.nomor}&jenis=best" download
             title="${rf ? 'Checkpoint RF-DETR terbaik (.pth dikemas sebagai .pt)'
                         : 'Bobot dengan metrik terbaik selama training'}">best.pt</a>
          <a class="chip" href="/latih/bobot?nomor=${t.nomor}&jenis=last" download
             title="${rf ? 'Bobot EMA epoch terakhir RF-DETR'
                         : 'Bobot epoch terakhir, untuk melanjutkan training'}">last.pt</a>
        </span>` : ''}
        <span class="spacer"></span>
        ${bolehKelola ? `<button class="chip chip-bahaya" type="button"
            data-hapus="${t.nomor}">Hapus</button>` : ''}
      </footer>
    </article>`;
  }

  function gambarMesin(s) {
    const g = s.gpu, r = s.ram;
    if (!g && !r) { $('tr-mesin-isi').innerHTML =
      '<span class="tr-diam">keadaan mesin tidak terbaca</span>'; return; }
    const vp = g ? Math.round(g.vram_pakai_mb / g.vram_total_mb * 100) : 0;
    $('tr-mesin-isi').innerHTML = `
      ${g ? `<div class="tr-m">
        <span class="tr-m-nama">${esc(g.nama)}</span>
        <div class="tr-m-bar"><i style="width:${g.util}%"></i></div>
        <span class="tr-m-nilai">${g.util}% · ${g.suhu}&deg;C</span>
      </div>
      <div class="tr-m">
        <span class="tr-m-nama">VRAM</span>
        <div class="tr-m-bar"><i style="width:${vp}%"></i></div>
        <span class="tr-m-nilai">${angka(g.vram_pakai_mb)} / ${angka(g.vram_total_mb)} MB</span>
      </div>` : ''}
      ${r ? `<div class="tr-m">
        <span class="tr-m-nama">RAM</span>
        <div class="tr-m-bar"><i style="width:${r.persen}%"></i></div>
        <span class="tr-m-nilai">${angka(r.pakai_gb, 1)} / ${angka(r.total_gb, 1)} GB</span>
      </div>` : ''}`;
  }

  async function muatDaftar() {
    let r;
    try { r = await ambil('/api/latih/daftar'); } catch { return; }
    if (!r.ok) return;
    gambarMesin(r.statistik || {});

    const semua = r.daftar || [];
    const jalan = semua.filter((t) => ['antre', 'jalan'].includes(t.keadaan));
    const usai = semua.filter((t) => !['antre', 'jalan'].includes(t.keadaan));

    $('tr-jalan-bagian').hidden = !jalan.length;
    $('tr-jalan').innerHTML = jalan.map(kartuJalan).join('');
    $('tr-hasil').innerHTML = usai.length ? usai.map(kartuHasil).join('')
      : '<span class="tr-diam">belum ada hasil training di projek ini</span>';

    document.querySelectorAll('[data-rinci]').forEach((b) => {
      b.onclick = () => bukaRincian(Number(b.dataset.rinci));
    });
    document.querySelectorAll('[data-batal]').forEach((b) => {
      b.onclick = async () => {
        if (!confirm('Hentikan training ini? Epoch yang sudah selesai tetap tersimpan.')) return;
        await fetch(`/api/latih/batal?nomor=${b.dataset.batal}`, {method: 'POST'});
        muatDaftar();
      };
    });
    document.querySelectorAll('[data-uji]').forEach((b) => {
      b.onclick = async () => {
        b.disabled = true;
        b.textContent = 'Menguji…';
        const j = await ambil(`/api/latih/evaluasi?nomor=${b.dataset.uji}`,
                              {method: 'POST'});
        if (!j.ok) { alert(j.error || 'gagal memulai evaluasi'); b.disabled = false;
                     b.textContent = 'Uji produksi'; return; }
        // Evaluasi selesai dalam belasan detik; panelnya dibuka supaya
        // hasilnya muncul di tempat ia akan dibaca, bukan hilang di daftar.
        setTimeout(() => bukaRincian(Number(b.dataset.uji)), 2500);
      };
    });
    document.querySelectorAll('[data-lanjut]').forEach((b) => {
      b.onclick = () => bukaLanjut(Number(b.dataset.lanjut), b.dataset.nama,
                                   Number(b.dataset.epochs) || 400,
                                   b.dataset.ars || 'yolo');
    });
    document.querySelectorAll('[data-sambung]').forEach((b) => {
      b.onclick = async () => {
        b.disabled = true;
        b.textContent = 'Menyambung…';
        const j = await ambil(`/api/latih/sambung-kaggle?nomor=${b.dataset.sambung}`,
                              {method: 'POST'});
        if (!j.ok) { alert(j.error || 'gagal menyambung'); b.disabled = false;
                     b.textContent = 'Lanjutkan di Kaggle'; return; }
        muatDaftar();
      };
    });
    document.querySelectorAll('[data-hapus]').forEach((b) => {
      b.onclick = async () => {
        if (!confirm('Hapus training ini beserta bobotnya? Tidak bisa dibatalkan.')) return;
        const j = await ambil(`/api/latih/hapus?nomor=${b.dataset.hapus}`, {method: 'POST'});
        if (!j.ok) alert(j.error || 'gagal menghapus');
        muatDaftar();
      };
    });

    // Berdenyut hanya selama ada yang berjalan DAN tabnya terlihat.
    clearTimeout(timer);
    if (jalan.length && !document.hidden) timer = setTimeout(muatDaftar, 4000);
  }

  /* Hasil uji produksi.
   *
   * Ditaruh DI ATAS metrik training, dan itu disengaja: mAP yang tinggi tanpa
   * uji ini tidak membuktikan apa pun. v13 melaporkan mAP50-95 0,9499 lalu
   * benar 0 dari 7 pada foto RVM sungguhan, dan yang menangkapnya justru
   * angka ketergantungan warna — bukan mAP.
   */
  /* Blok "Dijalankan di Kaggle" untuk panel Rincian: akun yang dipakai, berapa
     leg (sesi 11-jam) sudah dijalankan, epoch KUMULATIF lintas-leg, status
     remote terakhir, dan tautan ke halaman kernel (tempat log detik-per-detik,
     yang tak bisa disiarkan API). Hanya muncul untuk training backend Kaggle. */
  function blokKaggle(t) {
    if (t.backend !== 'kaggle') return '';
    const k = t.kaggle || {};
    const rem = (k.remote || '').replace(/.*status\s*"?KernelWorkerStatus\.?/i, '')
      .replace(/"$/, '').trim() || '-';
    const url = k.kernel_url
      ? `<a class="chip" href="${esc(k.kernel_url)}" target="_blank"
            rel="noopener">buka kernel ↗</a>` : '';
    return `<div class="tr-p-blok">
      <h4>Dijalankan di Kaggle (GPU cadangan)</h4>
      <div class="tr-kv-grid">
        <span class="tr-kv"><i>Akun</i><b>${esc(k.akun || '-')}</b></span>
        <span class="tr-kv"><i>Leg (sesi)</i><b>${angka(k.leg || 0)}</b></span>
        <span class="tr-kv"><i>Epoch kumulatif</i><b>${angka(k.epochs_done || 0)} / ${angka(t.epochs)}</b></span>
        <span class="tr-kv"><i>Status remote</i><b>${esc(rem)}</b></span>
      </div>
      ${k.pesan ? `<p class="tr-warna-pesan" style="margin-top:6px">${esc(k.pesan)}</p>` : ''}
      ${url ? `<p style="margin-top:6px">${url}
        <small class="tr-bantu">log langsung ada di halaman kernel. API tak menyiarkannya</small></p>` : ''}
    </div>`;
  }

  function blokEvaluasi(e) {
    if (!e) {
      return `<div class="tr-p-blok">
        <h4>Uji produksi</h4>
        <p class="tr-bantu">Belum diuji. Tekan <b>Uji produksi</b> di kartunya.
          mAP saja tidak cukup: ia diukur pada data yang sedomain dengan data
          latih, sedangkan yang menentukan adalah foto dari ruang detektor.</p>
      </div>`;
    }
    if (e.keadaan === 'jalan') {
      return `<div class="tr-p-blok"><h4>Uji produksi</h4>
        <p class="tr-diam">sedang berjalan…</p></div>`;
    }
    if (e.keadaan !== 'selesai') {
      return `<div class="tr-p-blok"><h4>Uji produksi</h4>
        <p class="tr-galat">${esc(e.galat || 'gagal tanpa keterangan')}</p></div>`;
    }
    const w = e.warna || {}, a = e.akurasi || {}, d = e.default || {},
          pu = e.putusan || {};
    const baris = (e.rinci || []).filter((x) => new Set(x.jawaban).size > 1);
    return `
      <div class="tr-p-blok">
        <h4>Uji produksi</h4>
        <div class="tr-putusan" data-tingkat="${esc(pu.tingkat)}">
          <b>${esc((pu.tingkat || '').toUpperCase())}</b>
          <span>${esc(pu.pesan || '')}</span>
        </div>
        ${e.mode_ket ? `<p class="tr-bantu tr-mode-baris">
          <b>Mode ${esc(e.mode)}.</b> ${esc(e.mode_ket.nilai)}</p>` : ''}
        <div class="tr-uji-angka">
          <span data-tingkat="${e.mode === 'warna' ? 'netral' : esc(w.tingkat)}">
            <i>Berubah karena RONA</i>
            <b>${w.tingkat === 'tak-terukur' ? '-' : angka(w.skor_rona, 0) + '%'}</b>
            <u>${e.mode === 'warna'
                 ? 'wajar di mode ini, rona memang penentu kelas'
                 : angka(w.berubah_rona) + ' dari ' + angka(w.total) + ' berubah'}</u></span>
          <span data-tingkat="${w.skor_terang >= 50 ? 'buruk'
                              : (w.skor_terang >= 20 ? 'sedang' : 'baik')}">
            <i>Berubah karena TERANG</i>
            <b>${w.tingkat === 'tak-terukur' ? '-' : angka(w.skor_terang, 0) + '%'}</b>
            <u>buruk di mode mana pun${w.kosong
                 ? ' · ' + angka(w.kosong) + ' tidak terdeteksi' : ''}</u></span>
          ${a.n ? `<span><i>Akurasi di test</i><b>${angka(a.persen, 0)}%</b>
            <u>${angka(a.benar)} dari ${angka(a.n)}</u></span>` : ''}
          <span data-tingkat="${esc(d.tingkat)}">
            <i>Kelas default</i>
            <b>${d.teratas ? esc(d.teratas) : 'tidak ada'}</b>
            <u>${d.teratas ? angka(d.porsi, 0) + '% dari ' + angka(d.n) + ' latar'
                           : angka(d.n) + ' latar kosong bersih'}</u></span>
        </div>
        ${blokPerKelas(a)}
        <p class="tr-bantu">${esc(w.pesan || '')}</p>
        <p class="tr-bantu">Latar kosong diambil dari ${esc(e.sumber_latar || '-')}.
          Diuji pada ${angka(e.n_foto)} foto dari split test v${e.versi},
          ${e.perlakuan ? e.perlakuan.length : 0} perlakuan warna
          (${esc((e.perlakuan || []).join(', '))}).</p>
        ${baris.length ? `<details class="tr-lanjut">
          <summary>${baris.length} gambar yang jawabannya goyah</summary>
          <table class="tr-tabel">
            <thead><tr><th>berkas</th><th>sebenarnya</th>
              ${(e.perlakuan || []).map((n) => `<th class="${
                (e.terang || []).includes(n) ? 'tr-kol-terang' : ''}">${esc(n)}</th>`
              ).join('')}</tr></thead>
            <tbody>${baris.map((x) => `<tr>
              <td>${esc(x.berkas)}</td><td>${esc(x.sebenarnya || '-')}</td>
              ${x.jawaban.map((c) => `<td class="${c === x.jawaban[0] ? '' : 'tr-beda'}">`
                + `${esc(c || 'none')}</td>`).join('')}</tr>`).join('')}</tbody>
          </table></details>` : ''}
      </div>`;
  }

  /* Kurva metrik per epoch, digambar sendiri sebagai SVG.
   *
   * Bukan memakai results.png buatan Ultralytics: gambar itu memuat sembilan
   * petak sekaligus (loss box, loss seg, loss cls, precision, recall, dan
   * seterusnya) dalam ukuran yang menuntut diperbesar untuk bisa dibaca.
   * Yang dicari orang saat membuka panel ini cuma dua hal — metriknya naik
   * atau mendatar, dan loss-nya turun atau tidak — jadi keduanya yang
   * digambar, sebesar mungkin.
   *
   * SVG, bukan canvas: ia ikut tajam di layar beresolusi tinggi tanpa perlu
   * mengurus devicePixelRatio, dan ikut berubah warna mengikuti tema.
   */
  /* Akurasi PER KELAS, dari matriks kebingungan hasil uji produksi.
   *
   * Angka gabungan menyembunyikan kelas yang gagal: 84,6% bisa berarti kedua
   * kelas sama-sama 85%, atau satu kelas 100% dan satunya 60%. Yang kedua
   * jauh lebih penting diketahui, dan cuma terlihat kalau dipisah.
   *
   * Yang ditampilkan juga KE MANA salahnya lari — "tetra sering dijawab
   * kaleng" bisa ditindak; "tetra 60%" tidak.
   */
  function blokPerKelas(a) {
    const b = (a && a.bingung) || {};
    const kelas = Object.keys(b);
    if (!kelas.length) return '';
    return `<div class="tr-kelas">
      <h5>Akurasi per kelas</h5>
      ${kelas.sort().map((nama) => {
        const baris = b[nama];
        const total = Object.values(baris).reduce((x, y) => x + y, 0);
        const benar = baris[nama] || 0;
        const pj = total ? benar / total * 100 : 0;
        const salah = Object.entries(baris)
          .filter(([k]) => k !== nama)
          .sort((x, y) => y[1] - x[1]);
        return `<div class="tr-kelas-baris">
          <span class="tr-kelas-nama" title="${esc(nama)}">${esc(nama)}</span>
          <span class="tr-kelas-bar"><i style="width:${pj.toFixed(1)}%"
            data-tingkat="${pj >= 90 ? 'baik' : (pj >= 70 ? 'sedang' : 'buruk')}"></i></span>
          <span class="tr-kelas-nilai">${angka(pj, 0)}%
            <u>${benar}/${total}</u></span>
          <span class="tr-kelas-salah">${salah.length
            ? 'sering jadi ' + salah.slice(0, 2).map(([k, n]) =>
                `${esc(k)} (${n})`).join(', ')
            : ''}</span>
        </div>`;
      }).join('')}
    </div>`;
  }

  /* Gambar yang digambar Ultralytics sendiri.
   *
   * Yang paling menjawab pertanyaan "model ini sebenarnya melihat apa" adalah
   * pasangan val_batch*_labels dan val_batch*_pred: petak gambar yang SAMA,
   * satu dengan poligon sebenarnya dan satu dengan poligon tebakan model.
   * Karena itu pasangan itu ditaruh paling atas dan DIBERDAMPINGKAN, bukan
   * ditumpuk: dua gambar yang harus dibandingkan tetapi berjauhan di layar
   * menuntut orang mengingat yang pertama sambil melihat yang kedua.
   *
   * Kelompok lain ditutup secara bawaan. Satu folder hasil berisi enam belas
   * gambar; membukanya semua sekaligus membuat panel ini panjang sekali dan
   * menenggelamkan yang penting.
   */
  /* Dipanggil SEBELUM blokEvaluasi dan sebelum deretan angka di panel
     rincian: satu petak berpoligon menjawab "model ini sebenarnya melihat
     apa" lebih cepat daripada angka mana pun. Sebelumnya ia di paling bawah,
     di balik empat bagian lain. */
  function blokGambar(kelompok, nomor) {
    if (!kelompok.length) return '';
    const src = (n) => `/latih/grafik?nomor=${nomor}&nama=${encodeURIComponent(n)}`;
    const label = (n) => n.includes('_labels') ? 'poligon sebenarnya'
      : (n.includes('_pred') ? 'tebakan model' : n.replace(/\.(png|jpg)$/, ''));
    const ubin = (n) => `<figure class="tr-gbr">
      <img loading="lazy" src="${src(n)}" alt="${esc(label(n))}"
           data-lup="${esc(src(n))}" data-cap="${esc(label(n))}">
      <figcaption>${esc(label(n))}</figcaption>
    </figure>`;

    /* Server mengirim SEMUA *_labels dulu baru SEMUA *_pred (dua glob,
       masing-masing di-sort). Dibiarkan begitu, tebakan petak 2 duduk jauh
       dari poligon petak 2 dan tidak ada yang bisa dibandingkan. Di sini
       keduanya dipasangkan ulang menurut nomor petaknya. */
    const berpasangan = (berkas) => {
      const petak = (n) => (n.match(/val_batch(\d+)/) || [, n])[1];
      const urut = [], oleh = new Map();
      berkas.forEach((n) => {
        const k = petak(n);
        if (!oleh.has(k)) { oleh.set(k, []); urut.push(k); }
        oleh.get(k).push(n);
      });
      return urut.map((k) => {
        const dua = oleh.get(k).sort((a, b) => a.includes('_labels') ? -1 : 1);
        return dua.length > 1
          ? `<div class="tr-pasang">${dua.map(ubin).join('')}</div>`
          : ubin(dua[0]);
      }).join('');
    };

    return kelompok.map((g, i) => {
      const pasangan = g.kunci === 'prediksi';
      const isi = `<div class="tr-galeri">
        ${pasangan ? berpasangan(g.berkas) : g.berkas.map(ubin).join('')}
      </div>`;
      // Kelompok pertama (prediksi) terbuka; sisanya ditutup.
      return i === 0
        ? `<div class="tr-p-blok"><h4>${esc(g.judul)}</h4>
             <p class="tr-bantu">${esc(g.ket)}</p>${isi}</div>`
        : `<details class="tr-p-blok tr-lipat">
             <summary><b>${esc(g.judul)}</b>
               <span class="tr-bantu">${esc(g.ket)}</span></summary>
             ${isi}</details>`;
    }).join('');
  }

  /* Kelas yang dikenal model ini, urut indeks (0..n-1) — persis urutan yang
     dipakai label YOLO. Nomor indeks ditampilkan karena itulah yang muncul di
     keluaran deteksi mentah. Kosong hanya kalau versinya sudah dihapus DAN
     training ini dibuat sebelum daftar kelas mulai dibekukan. */
  function blokKelas(kelas) {
    if (!Array.isArray(kelas) || !kelas.length) {
      return `<div class="tr-p-blok"><h4>Kelas model</h4>
        <p class="tr-bantu">Daftar kelas tidak tersedia. Versi sumbernya sudah dihapus.</p></div>`;
    }
    const cip = kelas.map((n, i) =>
      `<span class="tr-kcip"><i>${i}</i>${esc(n)}</span>`).join('');
    return `<div class="tr-p-blok">
      <h4>Kelas model <small>(${kelas.length})</small></h4>
      <div class="tr-kdaftar">${cip}</div></div>`;
  }

  function blokKurva(kurva, t) {
    const titik = kurva.filter((k) => k.nilai !== null && k.nilai !== undefined);
    if (titik.length < 2) {
      return `<div class="tr-p-blok"><h4>Kemajuan per epoch</h4>
        <p class="tr-bantu">Grafik muncul setelah dua epoch selesai.</p></div>`;
    }
    const W = 640, H = 190, PL = 44, PB = 26, PT = 10, PR = 10;
    const ex = titik.map((k) => k.epoch);
    const eMin = Math.min(...ex), eMax = Math.max(...ex);
    const sx = (e) => PL + (eMax === eMin ? 0 : (e - eMin) / (eMax - eMin)) * (W - PL - PR);

    // Batas atas dibulatkan ke kelipatan 20%: sumbu yang berakhir di 90%, 68%,
    // 45% menuntut orang membaca angka sebelum bisa menilai tingginya. Dengan
    // kelipatan bulat, posisi garisnya sendiri sudah bercerita.
    const nilai = titik.map((k) => k.nilai);
    const puncak = Math.max(...nilai, 0.01);
    const nMaks = Math.min(1, Math.ceil(puncak * 5) / 5);
    const sy = (v) => PT + (1 - v / nMaks) * (H - PT - PB);

    const box = titik.map((k) => k.box).filter((v) => v !== null && v !== undefined);
    const bMaks = box.length ? Math.max(...box) : 0;
    const syB = (v) => PT + (1 - v / (bMaks || 1)) * (H - PT - PB);

    const garis = (f, key) => titik.map((k, i) =>
      `${i ? 'L' : 'M'}${sx(k.epoch).toFixed(1)},${f(k[key] ?? 0).toFixed(1)}`).join('');

    // Garis bantu mendatar: tanpa skala, naik-turunnya tidak bisa dinilai
    // besarnya — cuma bentuknya.
    const langkahKisi = nMaks <= 0.4 ? 0.1 : 0.2;
    const kisi = [];
    for (let v = 0; v <= nMaks + 1e-9; v += langkahKisi) kisi.push(v / nMaks);
    const kisiSvg = kisi.map((f) => {
      const v = nMaks * f, y = sy(v);
      return `<line x1="${PL}" y1="${y.toFixed(1)}" x2="${W - PR}" y2="${y.toFixed(1)}"
                class="tr-kisi"/><text x="${PL - 6}" y="${(y + 3).toFixed(1)}"
                class="tr-sumbu" text-anchor="end">${(v * 100).toFixed(0)}%</text>`;
    }).join('');

    const tandaX = [eMin, Math.round((eMin + eMax) / 2), eMax].map((e) =>
      `<text x="${sx(e).toFixed(1)}" y="${H - 8}" class="tr-sumbu"
         text-anchor="middle">${e}</text>`).join('');

    const akhir = titik[titik.length - 1];
    return `<div class="tr-p-blok">
      <h4>Kemajuan per epoch</h4>
      <svg class="tr-kurva" viewBox="0 0 ${W} ${H}" role="img"
           aria-label="Kurva ${esc(t.utama || 'metrik')} per epoch">
        ${kisiSvg}
        ${bMaks ? `<path d="${garis(syB, 'box')}" class="tr-garis-loss"/>` : ''}
        <path d="${garis(sy, 'nilai')}" class="tr-garis-map"/>
        <circle cx="${sx(akhir.epoch).toFixed(1)}" cy="${sy(akhir.nilai).toFixed(1)}"
                r="3.5" class="tr-titik"/>
        ${tandaX}
      </svg>
      <div class="tr-legenda">
        <span class="tr-lg tr-lg-map">${esc(t.utama || 'metrik')}
          <b>${angka(akhir.nilai * 100, 1)}%</b></span>
        ${bMaks ? `<span class="tr-lg tr-lg-loss">loss kotak
          <b>${angka(akhir.box, 3)}</b></span>` : ''}
        <span class="tr-bantu">nilai terakhir, epoch ${eMin} sampai ${eMax}</span>
      </div>
    </div>`;
  }

  async function bukaRincian(nomor) {
    $('tr-tirai').hidden = false;
    $('tr-panel-isi').innerHTML = '<span class="tr-diam">memuat…</span>';
    let r;
    try { r = await ambil(`/api/latih/rincian?nomor=${nomor}`); }
    catch (e) { $('tr-panel-isi').innerHTML = `<p class="tr-galat">gagal memuat: ${esc(e)}</p>`; return; }
    if (!r.ok) { $('tr-panel-isi').innerHTML = `<p class="tr-galat">${esc(r.error)}</p>`; return; }
    const t = r.latih, w = t.warna || {}, par = t.par || {};
    // Rincian versi dataset yang dilatih (null kalau versinya sudah dihapus).
    const vm = r.versi_meta;
    const jm = (vm && vm.jumlah) || {};
    const splitStr = Object.keys(jm).length
      ? Object.entries(jm).map(([k, v]) => `${esc(k)} ${angka(v)}`).join(' / ')
      : '';
    // "L1 — Paragon v2 — uji evaluasi": dua em-dash beruntun membuat judulnya
    // terbaca seperti potongan yang disambung mesin. Nomornya jadi label
    // terpisah, namanya berdiri sendiri.
    $('tr-panel-judul').innerHTML =
      `<span class="tr-panel-no">L${t.nomor}</span>${esc(t.nama)}`;
    const parBaris = Object.keys(par).sort().map((k) =>
      `<span class="tr-kv"><i>${esc(k)}</i><b>${esc(par[k])}</b></span>`).join('');
    /* Keadaan naik jadi PITA di bawah judul, bukan bagian tersendiri.
       Empat angka pendek tidak memerlukan judul bagian dan garis pemisahnya
       sendiri; sebagai bagian ia memakan satu pita penuh untuk isi yang
       muat dalam satu baris, dan menambah satu judul lagi ke tumpukan yang
       sudah membuat panel ini terasa terpotong-potong. */
    $('tr-panel-isi').innerHTML = `
      <div class="tr-p-pita">
        <div><dt>Status</dt><dd>${LABEL_KEADAAN[t.keadaan] || t.keadaan}</dd></div>
        <div><dt>Arsitektur</dt><dd>${t.arsitektur === 'rfdetr'
          ? 'RF-DETR' + (t.rfdetr_model ? ' ' + esc(t.rfdetr_model) : '')
          : 'YOLO'}</dd></div>
        <div><dt>Epoch</dt><dd>${
          t.persen_epoch != null
            ? `${angka(t.epoch_berjalan)} / ${angka(t.epochs)} <small>(${angka(t.persen_epoch, 0)}% epoch ini)</small>`
            : (t.berlalu_epoch != null && (t.keadaan === 'jalan' || t.keadaan === 'antre')
                ? `${angka(t.epoch_berjalan)} / ${angka(t.epochs)} <small>(epoch ini berjalan ${durasi(t.berlalu_epoch)})</small>`
                : `${angka(t.epoch)} / ${angka(t.epochs)}`)}</dd></div>
        <div><dt>Lama</dt><dd>${durasi(t.detik)}</dd></div>
        <div><dt>Dataset</dt><dd>v${t.versi}${
          vm ? (vm.catatan ? ' · ' + esc(vm.catatan.slice(0, 50)) : '')
             : ' <small>(versi dihapus)</small>'}</dd></div>
        ${t.lanjut_dari ? `<div><dt>Lanjutan dari</dt><dd>L${t.lanjut_dari}</dd></div>` : ''}
        <div><dt>Oleh</dt><dd>${esc(t.oleh || '?')}</dd></div>
      </div>
      ${blokKaggle(t)}
      ${vm ? `<div class="tr-p-blok">
        <h4>Dataset yang dilatih, versi v${t.versi}</h4>
        <div class="tr-kv-grid">
          <span class="tr-kv"><i>Dibuat</i><b>${esc(vm.dibuat || '?')}${
            vm.oleh ? ' · ' + esc(vm.oleh) : ''}</b></span>
          <span class="tr-kv"><i>Gambar</i><b>${angka(vm.n)}</b></span>
          ${splitStr ? `<span class="tr-kv"><i>Split</i><b>${splitStr}</b></span>` : ''}
          <span class="tr-kv"><i>Kelas</i><b>${angka(vm.kelas)}</b></span>
          <span class="tr-kv"><i>Pembagian</i><b>${
            vm.berencana ? 'anti-bocor' : 'acak nama'}${
            vm.rasio ? ' · ' + esc(vm.rasio) : ''}</b></span>
        </div>
        ${vm.catatan ? `<p class="tr-warna-pesan" style="margin-top:6px">
          <i>Catatan versi:</i> ${esc(vm.catatan)}</p>` : ''}
      </div>` : `<div class="tr-p-blok">
        <h4>Dataset yang dilatih, versi v${t.versi}</h4>
        <p class="tr-diam">Versi ini sudah dihapus, jadi rincian dataset &amp;
          catatannya tak tersedia lagi. Kelas yang dibekukan tetap di bawah.</p>
      </div>`}
      ${w.pesan ? `<div class="tr-p-blok tr-warna">
        <h4>Warna: setelan latih mengikuti versinya</h4>
        <p class="tr-warna-pesan">${esc(w.pesan)}</p></div>` : ''}
      ${blokKelas(t.kelas)}
      ${blokKurva(r.kurva || [], t)}
      ${blokGambar(r.gambar || [], t.nomor)}
      ${blokEvaluasi(r.evaluasi)}
      <div class="tr-p-blok">
        <h4>Metrik terbaik</h4>
        <div class="tr-kartu-metrik">${barisMetrik(t.terbaik)}</div>
      </div>
      <div class="tr-p-blok">
        <h4>Setelan yang dipakai</h4>
        <div class="tr-kv-grid">${parBaris}</div>
      </div>
      <div class="tr-p-blok">
        <h4>Log</h4>
        <pre class="tr-log">${esc(r.log || '(kosong)')}</pre>
      </div>`;
  }

  // ---- Training lanjutan: latih lagi mulai dari bobot sebuah training ----
  let lanjutDari = 0;
  function bukaLanjut(nomor, nama, epochs, arsitektur) {
    lanjutDari = nomor;
    const rf = arsitektur === 'rfdetr';
    $('tr-lanjut-judul').textContent = `Lanjutkan L${nomor}`;
    // "Titik awal bobot" (best/last) hanya bermakna untuk YOLO (warm-start bobot).
    // RF-DETR resume dari checkpoint state-penuh (last.ckpt) — tak ada pilihan ini.
    if ($('tr-lanjut-jenis-baris')) $('tr-lanjut-jenis-baris').hidden = rf;
    if (rf) {
      // RF-DETR = resume sejati: epoch LANJUT (optimizer+epoch ikut), jadi yang
      // diisi adalah TOTAL epoch baru, wajib lebih besar dari epoch sumber.
      $('tr-lanjut-ket').textContent =
        `Melanjutkan L${nomor} dari checkpoint terakhirnya, resume state penuh `
        + `(optimizer & epoch ikut lanjut), bukan dari nol. Isi TOTAL epoch yang `
        + `diinginkan, harus lebih besar dari ${epochs}.`;
      $('tr-lanjut-epochs-label').textContent = `Total epoch (sumber ${epochs})`;
      $('tr-lanjut-epochs').min = epochs + 1;
      $('tr-lanjut-epochs').value = epochs + 100;
    } else {
      $('tr-lanjut-ket').textContent =
        `Training baru dimulai dari bobot L${nomor}. Setelannya (versi, hsv, mode `
        + `warna) diwarisi apa adanya, hanya jumlah epoch yang diganti.`;
      $('tr-lanjut-epochs-label').textContent = 'Jumlah epoch';
      $('tr-lanjut-epochs').min = 1;
      $('tr-lanjut-epochs').value = epochs || 400;
    }
    $('tr-lanjut-nama').value = '';
    $('tr-lanjut-nama').placeholder = `otomatis: '${nama || ('L' + nomor)} lanjutan'`;
    $('tr-lanjut-jenis').value = 'best';
    $('tr-lanjut').hidden = false;
  }
  function tutupLanjut() { $('tr-lanjut').hidden = true; lanjutDari = 0; }
  if ($('tr-lanjut')) {
    $('tr-lanjut-batal').onclick = tutupLanjut;
    // Klik latar gelap (bukan dialognya) menutup.
    $('tr-lanjut').addEventListener('click', (e) => {
      if (e.target === $('tr-lanjut')) tutupLanjut();
    });
    $('tr-lanjut-jalan').onclick = async () => {
      if (!lanjutDari) return;
      const btn = $('tr-lanjut-jalan');
      btn.disabled = true; btn.textContent = 'Meluncurkan…';
      const payload = {
        dari: lanjutDari,
        jenis: $('tr-lanjut-jenis').value,
        epochs: Number($('tr-lanjut-epochs').value) || 400,
        nama: $('tr-lanjut-nama').value.trim(),
      };
      const j = await ambil('/api/latih/lanjut', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).catch(() => null);
      btn.disabled = false; btn.textContent = 'Jalankan lanjutan';
      if (!j || !j.ok) { alert((j && j.error) || 'gagal memulai lanjutan'); return; }
      tutupLanjut();
      muatDaftar();
    };
  }

  // ============================================================
  // PASANG
  // ============================================================

  if (bolehKelola) {
    /* Tombolnya SEMBUNYI selama formnya terbuka, tidak cuma berganti makna.
       Formnya sudah punya "Batal" sendiri; membiarkan "+ Training baru" tetap
       di layar berarti ada dua tombol yang bertabrakan artinya, dan yang satu
       masih mengajak membuat training baru padahal orangnya sudah ada di
       dalamnya. */
    const bukaForm = (buka) => {
      $('tr-form').hidden = !buka;
      $('tr-kepala').classList.toggle('tr-kepala-sunyi', buka);
      if (buka) $('tr-nama').focus();
      else $('tr-mulai').focus();
    };
    $('tr-mulai').onclick = () => bukaForm(true);
    $('tr-tutup').onclick = () => bukaForm(false);
    document.querySelectorAll('input[name="tr-mode"]').forEach((r) => {
      r.onchange = () => {
        // Ditandai supaya pilihan orang tidak ditimpa bawaan versi saat ia
        // berpindah versi — yang sudah diputuskan orang harus bertahan.
        $('tr-warna').dataset.disentuh = '1';
        gambarMode();
      };
    });
    $('tr-reset').onclick = () => {
      // Kembalikan ke bawaan. Untuk RF-DETR: bawaan RF-DETR dari server. Untuk
      // YOLO: bawaan PRESET YANG DIPILIH (SmartBin / Basket), bukan selalu
      // preset default.
      let dasar;
      if (arsitekturDipilih() === 'rfdetr') {
        dasar = BAHAN.preset_rfdetr || {};
      } else {
        const p = presetTerpilih();
        dasar = (p && p.par) || BAHAN.preset;
      }
      document.querySelectorAll('#tr-form [data-par]').forEach((el) => {
        if (dasar[el.dataset.par] !== undefined) el.value = dasar[el.dataset.par];
      });
    };
    $('tr-tambah').onclick = () => {
      if (!$('tr-versi').value) { galat('pilih versi lebih dulu'); return; }
      const s = bacaSatu();
      if (!s.nama) s.nama = `Percobaan ${ANTREAN.length + 1}`;
      ANTREAN.push(s);
      galat('');
      gambarAntrean();
    };
    $('tr-jalankan').onclick = async () => {
      const versi = Number($('tr-versi').value || 0);
      if (!versi) { galat('pilih versi lebih dulu'); return; }
      const batch = ANTREAN.length ? ANTREAN : [bacaSatu()];
      // backend berlaku untuk SELURUH kiriman (satu pilihan "Jalankan di"),
      // bukan per-percobaan. Bawaan lokal kalau selektornya tak ada.
      const backend = ($('tr-backend') && $('tr-backend').value) || 'lokal';
      // preset (SmartBin/Basket) juga berlaku untuk seluruh kiriman.
      const preset = ($('tr-preset') && $('tr-preset').value) || 'rvm';
      // Arsitektur & ukuran model RF-DETR juga sekali untuk SELURUH kiriman —
      // seperti backend & preset — karena ia menukar bentuk parameternya.
      const arsitektur = arsitekturDipilih();
      const rfdetr_model = rfdetrModelDipilih();
      // Saklar 2-GPU (T4x2) — hanya saat RF-DETR + Kaggle; else selalu mati.
      const rfdetr_multigpu = (arsitektur === 'rfdetr' && backend === 'kaggle'
        && $('tr-rfdetr-multigpu') && $('tr-rfdetr-multigpu').checked) || false;
      $('tr-jalankan').disabled = true;
      try {
        const j = await ambil('/api/latih/mulai', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({versi, batch, backend, preset, arsitektur,
                                rfdetr_model, rfdetr_multigpu}),
        });
        if (!j.ok) { galat(j.error || 'gagal memulai'); return; }
        ANTREAN = [];
        gambarAntrean();
        galat('');
        bukaForm(false);
        muatDaftar();
      } catch (e) {
        galat(String(e));
      } finally {
        $('tr-jalankan').disabled = false;
      }
    };
  }

  /* -- kaca pembesar galeri ------------------------------------------------
   * Dipasang sekali lewat delegasi di seluruh dokumen, bukan per-gambar:
   * isi panel rincian ditulis ulang tiap kali dibuka, jadi penangan yang
   * ditempel ke <img> akan hilang bersama innerHTML-nya. */
  let lupDaftar = [], lupKe = 0;

  function lupGambar() {
    const g = lupDaftar[lupKe];
    if (!g) return;
    $('tr-lup-gbr').src = g.src;
    $('tr-lup-gbr').alt = g.cap;
    $('tr-lup-cap').textContent = lupDaftar.length > 1
      ? `${g.cap} · ${lupKe + 1}/${lupDaftar.length}` : g.cap;
    $('tr-lup-mundur').disabled = lupKe === 0;
    $('tr-lup-maju').disabled = lupKe === lupDaftar.length - 1;
  }

  function lupGeser(arah) {
    const ke = lupKe + arah;
    if (ke < 0 || ke >= lupDaftar.length) return;
    lupKe = ke;
    lupGambar();
  }

  function lupTutup() {
    $('tr-lup').hidden = true;
    $('tr-lup-gbr').src = '';
    lupDaftar = [];
  }

  document.addEventListener('click', (e) => {
    const img = e.target.closest('img[data-lup]');
    if (!img) return;
    // Semua gambar dalam SATU galeri jadi satu rangkaian, supaya panahnya
    // melangkah dari poligon sebenarnya ke tebakan model tanpa menutup lup.
    const galeri = img.closest('.tr-galeri');
    const semua = Array.from((galeri || document).querySelectorAll('img[data-lup]'));
    lupDaftar = semua.map((x) => ({ src: x.dataset.lup, cap: x.dataset.cap }));
    lupKe = Math.max(0, semua.indexOf(img));
    $('tr-lup').hidden = false;
    lupGambar();
  });

  $('tr-lup-tutup').onclick = lupTutup;
  $('tr-lup-mundur').onclick = () => lupGeser(-1);
  $('tr-lup-maju').onclick = () => lupGeser(1);
  $('tr-lup').onclick = (e) => { if (e.target === $('tr-lup')) lupTutup(); };
  document.addEventListener('keydown', (e) => {
    if ($('tr-lup').hidden) return;
    if (e.key === 'Escape') { lupTutup(); }
    else if (e.key === 'ArrowLeft') { lupGeser(-1); }
    else if (e.key === 'ArrowRight') { lupGeser(1); }
    else return;
    // Esc juga menutup panel rincian di bawahnya; lup yang terbuka menang.
    e.stopPropagation();
    e.preventDefault();
  }, true);

  $('tr-panel-tutup').onclick = () => { $('tr-tirai').hidden = true; };
  $('tr-tirai').onclick = (e) => {
    if (e.target === $('tr-tirai')) $('tr-tirai').hidden = true;
  };
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) muatDaftar();
  });

  (async () => {
    if (bolehKelola) {
      try {
        BAHAN = await ambil('/api/latih/bahan');
        if (BAHAN.ok) {
          $('tr-bobot').innerHTML = BAHAN.bobot.map((b) =>
            `<option value="${esc(b.path)}" data-tugas="${esc(b.tugas)}">`
            + `${esc(b.nama)}${b.lokal ? '' : ' (unduh)'}</option>`).join('');
          $('tr-bobot').onchange = () => {
            const o = $('tr-bobot').selectedOptions[0];
            $('tr-bobot-ket').textContent =
              (BAHAN.bobot.find((b) => b.path === $('tr-bobot').value) || {}).ket || '';
            if (o) $('tr-tugas').value = o.dataset.tugas || 'segment';
          };
          gambarForm();
          // Selektor "Jenis preset" (SmartBin / Basket) — ramah untuk awam.
          // Mengganti preset memuat ulang bawaan setelan + menampilkan/
          // menyembunyikan panel Bentuk/Warna sesuai presetnya.
          if ($('tr-preset') && BAHAN.preset_daftar) {
            $('tr-preset').innerHTML = BAHAN.preset_daftar.map((p) =>
              `<option value="${esc(p.id)}">${esc(p.nama)}</option>`).join('');
            $('tr-preset').value = BAHAN.preset_bawaan || 'rvm';
            $('tr-preset').onchange = () => { terapkanPreset(); gambarMode(); };
          }
          terapkanPreset();
          $('tr-bobot').onchange();
          gambarAntrean();
          // Backend Kaggle: buka selektor "Jalankan di" kalau paket kaggle ADA di
          // server (kaggle_mungkin) — BUKAN cuma kalau user sudah punya akun —
          // supaya user bisa MENAMBAH akun pertamanya lewat panel di bawahnya.
          if (BAHAN.kaggle_mungkin && $('tr-backend-bungkus')) {
            $('tr-backend-bungkus').hidden = false;
            if ($('tr-backend')) {
              $('tr-backend').value =
                (BAHAN.siap === false && BAHAN.kaggle_siap) ? 'kaggle' : 'lokal';
              $('tr-backend').onchange = terapkanBackend;
            }
          }
          // Handler panel akun Kaggle (statis di form; aman dipasang sekali).
          if ($('tr-akun-tambah')) {
            $('tr-akun-tambah').onclick = () => {
              const f = $('tr-akun-form');
              if (f) { f.hidden = !f.hidden; setelTest('', ''); }
            };
          }
          if ($('tr-akun-batal')) {
            $('tr-akun-batal').onclick = () => {
              $('tr-akun-user').value = ''; $('tr-akun-token').value = '';
              $('tr-akun-form').hidden = true; setelTest('', '');
            };
          }
          if ($('tr-akun-test')) $('tr-akun-test').onclick = testAkun;
          // Mengetik ulang membersihkan pesan hasil sebelumnya.
          ['tr-akun-user', 'tr-akun-token'].forEach((id) => {
            if ($(id)) $(id).oninput = () => setelTest('', '');
          });
          if ($('tr-akun-monitor')) {
            $('tr-akun-monitor').addEventListener('toggle', () => {
              if ($('tr-akun-monitor').open) muatMonitorAkun();
            });
          }
          // Arsitektur (YOLO / RF-DETR). Selektornya hanya muncul kalau RF-DETR
          // benar-benar bisa dipakai — terpasang di mesin ini ATAU lewat Kaggle
          // — dan bawaannya tetap YOLO, jadi tampilan lama tak berubah sedikit
          // pun bila RF-DETR tidak tersedia. Mengganti arsitektur menukar
          // seluruh bentuk form (lihat terapkanArsitektur).
          if ((BAHAN.rfdetr_siap || BAHAN.kaggle_siap) && $('tr-arsitektur-bungkus')) {
            $('tr-arsitektur-bungkus').hidden = false;
            if ($('tr-arsitektur')) {
              $('tr-arsitektur').value = 'yolo';
              $('tr-arsitektur').onchange = terapkanArsitektur;
            }
            if ($('tr-rfdetr-model')) {
              $('tr-rfdetr-model').value =
                (BAHAN.rfdetr_model && BAHAN.rfdetr_model[0]) || 'nano';
            }
          }
          // Dikatakan SEBELUM orang menyusun setelan, bukan sesudah ia menekan
          // Jalankan dan menunggu kegagalan yang tidak dijelaskan. Menata bentuk
          // form sesuai arsitektur awal (YOLO) + menyegarkan gerbang tombol
          // Jalankan (yang mengikuti backend DAN arsitektur yang dipilih).
          terapkanArsitektur();
        }
      } catch (e) {
        galat('gagal memuat bahan form: ' + e);
      }
    }
    muatDaftar();
  })();
})();
