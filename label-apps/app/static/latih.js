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
  const isi = $('lt-isi');
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

  function kotakPar(kunci, label, satuan, jelas) {
    const p = BAHAN.preset[kunci];
    const b = BAHAN.batas[kunci];
    const langkah = Number.isInteger(p) ? 1 : (p < 0.01 ? 0.0001 : 0.01);
    return `<label class="lt-p" title="${esc(jelas || label)}">
      <span class="lt-p-nama">${esc(label)}</span>
      <input type="number" data-par="${esc(kunci)}" value="${p}"
             ${b ? `min="${b[0]}" max="${b[1]}"` : ''} step="${langkah}">
      ${satuan ? `<span class="lt-p-satuan">${esc(satuan)}</span>` : ''}
    </label>`;
  }

  function gambarForm() {
    $('lt-par').innerHTML = PAR_UTAMA.map((x) => kotakPar(...x)).join('');
    $('lt-par-lanjut').innerHTML = PAR_LANJUT.map((x) => kotakPar(...x)).join('');

    const sel = $('lt-versi');
    const siap = BAHAN.versi.filter((v) => v.siap);
    if (!siap.length) {
      sel.innerHTML = '<option value="">— belum ada versi yang bisa dilatih —</option>';
      $('lt-versi-ket').textContent =
        'Buat versi lebih dulu di halaman Versi. Training memakai pembagian '
        + 'train/valid/test yang sudah dibekukan di sana, bukan isi dataset mentah.';
      return;
    }
    sel.innerHTML = siap.map((v) => {
      const j = v.jumlah || {};
      const n = (j.train || 0) + (j.valid || 0) + (j.test || 0);
      return `<option value="${v.nomor}">v${v.nomor} — ${angka(n)} gambar`
           + `${v.catatan ? ' · ' + esc(v.catatan.slice(0, 40)) : ''}</option>`;
    }).join('');
    sel.onchange = pilihVersi;
    pilihVersi();
  }

  /* Versi berganti -> panel warna berganti -> angka warna ikut disetel.
     Inilah sinkronisasi yang diminta, dan ia berjalan otomatis supaya tidak
     bergantung pada orang mengingat untuk menyetelnya. */
  function pilihVersi() {
    const n = Number($('lt-versi').value || 0);
    const v = BAHAN.versi.find((x) => x.nomor === n);
    if (!v) return;

    const j = v.jumlah || {};
    const kelas = Object.keys(v.kelas || {}).length;
    $('lt-versi-ket').innerHTML =
      `train <b>${angka(j.train || 0)}</b> · valid <b>${angka(j.valid || 0)}</b>`
      + ` · test <b>${angka(j.test || 0)}</b>`
      + (kelas ? ` · ${kelas} kelas` : '')
      + (v.negatif ? ` · ${angka(v.negatif)} sampel negatif` : '')
      + (v.jenis ? ` · ${esc(v.jenis)}` : '');

    // Bentuk anotasi versinya menentukan tugas yang masuk akal.
    if (v.jenis && v.jenis.toLowerCase().includes('kotak')) {
      $('lt-tugas').value = 'detect';
      $('lt-tugas-ket').textContent =
        'Versi ini beranotasi kotak, jadi segmentasi tidak bisa dilatih darinya.';
    } else {
      $('lt-tugas-ket').textContent = '';
    }

    // Bawaannya mengikuti cara versinya dibuat — itu yang hampir selalu
    // benar. Orang tetap boleh menggantinya, dan kalau berbeda, peringatannya
    // menyebutkan akibatnya.
    const w = v.warna || {};
    const asal = w.mode || 'bentuk';
    const box = $('lt-warna');
    box.hidden = false;
    const r = box.querySelector(`input[name="lt-mode"][value="${asal}"]`);
    if (r && !box.dataset.disentuh) r.checked = true;
    gambarMode();
  }

  const modeDipilih = () => {
    const r = document.querySelector('input[name="lt-mode"]:checked');
    return r ? r.value : 'bentuk';
  };

  /* Satu tempat yang menggambar seluruh panel warna, dipanggil ulang tiap
     kali versinya atau modenya berganti. Isinya ditulis dari sudut pandang
     HASILNYA — apa yang akan dipelajari model — bukan dari sudut pandang
     setelannya. "Warna dibuka di versinya" itu benar tetapi hanya berarti
     bagi yang sudah tahu apa yang dibuka dan kenapa. */
  function gambarMode() {
    const n = Number($('lt-versi').value || 0);
    const v = BAHAN.versi.find((x) => x.nomor === n) || {};
    const w = v.warna || {};
    const asal = w.mode || 'bentuk';
    const m = modeDipilih();
    const box = $('lt-warna');
    const beda = m !== asal;

    // Satu arah yang MUSTAHIL, dan itu bukan soal selera: versi yang dibangun
    // dengan ronanya diacak ~50 derajat sudah kehilangan informasi warnanya
    // di dalam datanya sendiri. Tidak ada setelan waktu-latih yang bisa
    // mengembalikannya.
    const mustahil = (m === 'warna' && asal === 'bentuk');
    box.dataset.tingkat = mustahil ? 'awas' : (beda ? 'beda' : 'ok');
    $('lt-warna-ikon').textContent = mustahil ? '!' : (beda ? '~' : '✓');
    $('lt-warna-judul').textContent = m === 'warna'
      ? 'Model akan mengenali dari WARNA dan bentuk'
      : 'Model akan mengenali dari BENTUK, bukan warna';

    let pesan;
    if (mustahil) {
      pesan = `Versi v${n} dibuat dengan warnanya diacak lebar, jadi warna asli `
        + 'sudah hilang dari datanya. Melatih model yang bergantung warna dari '
        + 'versi ini tidak akan berhasil — buat versi baru dengan pilihan '
        + '"Warna ikut menentukan" di langkah Augmentasi.';
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
    $('lt-warna-pesan').textContent = pesan;

    /* Angkanya TERUKUR pada pipeline yang sebenarnya, bukan taksiran dari
       nilai setelannya. Rona digeser jauh lebih lebar oleh augmentasi versi
       (~50 derajat) daripada oleh setelan waktu-latih (~5 derajat), jadi
       kalimat yang cuma menyebut "saat melatih" akan menyesatkan. Dijaga
       test_klaim_layar_sesuai_kenyataan di tests/test_evaluasi.py. */
    $('lt-warna-nilai').textContent = m === 'warna'
      ? 'Warna asli dipertahankan — ronanya praktis tidak digeser. Yang '
        + 'divariasikan gelap-terangnya (0,5x sampai 1,2x) dan sedikit '
        + 'kepekatan warnanya, supaya model tetap tahan saat lampu berubah.'
      : 'Objek yang sama akan dilihat model dalam banyak warna berbeda — '
        + 'ronanya digeser rata-rata sekitar 50 derajat dari 360, dan pada 1 '
        + 'dari 10 gambar merah dan biru ditukar. Warna jadi tidak bisa '
        + 'diandalkan, sehingga model terpaksa belajar bentuknya.';
  }

  function bacaPar() {
    const out = {};
    document.querySelectorAll('#lt-form [data-par]').forEach((el) => {
      const v = Number(el.value);
      if (Number.isFinite(v)) out[el.dataset.par] = v;
    });
    return out;
  }

  function bacaSatu() {
    return {
      nama: $('lt-nama').value.trim(),
      catatan: $('lt-catatan').value.trim(),
      tugas: $('lt-tugas').value,
      bobot: $('lt-bobot').value,
      mode_warna: modeDipilih(),
      par: bacaPar(),
    };
  }

  // ============================================================
  // ANTREAN (batch)
  // ============================================================

  function gambarAntrean() {
    const d = $('lt-batch-daftar');
    if (!ANTREAN.length) {
      d.innerHTML = '<span class="lt-diam">belum ada yang diantrekan — '
        + '"Jalankan" akan memakai setelan di atas apa adanya</span>';
      return;
    }
    d.innerHTML = ANTREAN.map((x, i) => `
      <div class="lt-antre">
        <span class="lt-antre-no">${i + 1}</span>
        <span class="lt-antre-nama">${esc(x.nama || '(tanpa nama)')}</span>
        <span class="lt-antre-par">${x.par.epochs} epoch · batch ${x.par.batch}
          · ${x.par.imgsz}px · ${esc(x.tugas)}</span>
        <span class="spacer"></span>
        <button class="chip" type="button" data-buang="${i}">Buang</button>
      </div>`).join('');
    d.querySelectorAll('[data-buang]').forEach((b) => {
      b.onclick = () => { ANTREAN.splice(Number(b.dataset.buang), 1); gambarAntrean(); };
    });
  }

  function galat(pesan) {
    const g = $('lt-galat');
    g.hidden = !pesan;
    g.textContent = pesan || '';
  }

  // ============================================================
  // DAFTAR: YANG BERJALAN DAN HASILNYA
  // ============================================================

  const LABEL_KEADAAN = {
    antre: 'Menunggu giliran', jalan: 'Berjalan', selesai: 'Selesai',
    gagal: 'Gagal', batal: 'Dihentikan', hilang: 'Terputus',
  };

  function barisMetrik(m) {
    const k = Object.keys(m || {});
    if (!k.length) return '<span class="lt-diam">belum ada metrik</span>';
    return k.slice(0, 4).map((n) =>
      `<span class="lt-metrik"><i>${esc(n)}</i><b>${angka(m[n] * 100, 1)}%</b></span>`
    ).join('');
  }

  function kartuJalan(t) {
    const pj = Math.max(0, Math.min(100, t.persen || 0));
    return `<article class="lt-kartu lt-kartu-jalan" data-nomor="${t.nomor}">
      <div class="lt-kartu-atas">
        <b class="lt-kartu-nama">${esc(t.nama)}</b>
        <span class="lt-pil lt-pil-${esc(t.keadaan)}">${LABEL_KEADAAN[t.keadaan] || t.keadaan}</span>
        <span class="spacer"></span>
        <span class="lt-kartu-sub">dari v${t.versi} · ${esc(t.tugas)}</span>
      </div>
      <div class="lt-bar"><i style="width:${pj}%"></i></div>
      <div class="lt-kartu-angka">
        <span><i>Epoch</i><b>${angka(t.epoch)} / ${angka(t.epochs)}</b></span>
        <span><i>Terpakai</i><b>${durasi(t.detik)}</b></span>
        <span><i>Perkiraan sisa</i><b>${t.sisa ? durasi(t.sisa) : '—'}</b></span>
        <span><i>Kemajuan</i><b>${angka(pj, 1)}%</b></span>
      </div>
      <div class="lt-kartu-metrik">${barisMetrik(t.metrik)}</div>
      <div class="lt-kartu-aksi">
        <button class="chip" type="button" data-rinci="${t.nomor}">Rincian</button>
        ${bolehKelola ? `<button class="chip chip-bahaya" type="button"
            data-batal="${t.nomor}">Hentikan</button>` : ''}
      </div>
    </article>`;
  }

  function kartuHasil(t) {
    const terbaik = t.terbaik || {};
    const utama = t.utama && terbaik[t.utama] !== undefined
      ? `<span class="lt-skor"><i>${esc(t.utama)}</i>
           <b>${angka(terbaik[t.utama] * 100, 1)}%</b></span>` : '';
    const rusak = ['gagal', 'hilang'].includes(t.keadaan);
    return `<article class="lt-kartu" data-nomor="${t.nomor}">
      <div class="lt-kartu-atas">
        <b class="lt-kartu-nama">${esc(t.nama)}</b>
        <span class="lt-pil lt-pil-${esc(t.keadaan)}">${LABEL_KEADAAN[t.keadaan] || t.keadaan}</span>
        <span class="spacer"></span>
        <span class="lt-kartu-sub">L${t.nomor} · dari v${t.versi} ·
          ${esc(t.dibuat || '')}</span>
      </div>
      ${utama}
      <div class="lt-kartu-metrik">${rusak
        ? `<span class="lt-galat-kecil">${esc(t.galat || 'berhenti tanpa keterangan')}</span>`
        : barisMetrik(terbaik)}</div>
      <div class="lt-kartu-angka">
        <span><i>Epoch</i><b>${angka(t.epoch)} / ${angka(t.epochs)}</b></span>
        <span><i>Lama</i><b>${durasi(t.detik)}</b></span>
        <span><i>Oleh</i><b>${esc(t.oleh || '—')}</b></span>
      </div>
      <div class="lt-kartu-aksi">
        <button class="chip" type="button" data-rinci="${t.nomor}">Rincian</button>
        ${t.punya_bobot && bolehKelola ? `<button class="chip chip-uji" type="button"
            data-uji="${t.nomor}">Uji produksi</button>` : ''}
        ${t.punya_bobot ? `<a class="chip" href="/latih/bobot?nomor=${t.nomor}&jenis=best"
            download>Unduh best.pt</a>` : ''}
        ${bolehKelola ? `<button class="chip chip-bahaya" type="button"
            data-hapus="${t.nomor}">Hapus</button>` : ''}
      </div>
    </article>`;
  }

  function gambarMesin(s) {
    const g = s.gpu, r = s.ram;
    if (!g && !r) { $('lt-mesin-isi').innerHTML =
      '<span class="lt-diam">keadaan mesin tidak terbaca</span>'; return; }
    const vp = g ? Math.round(g.vram_pakai_mb / g.vram_total_mb * 100) : 0;
    $('lt-mesin-isi').innerHTML = `
      ${g ? `<div class="lt-m">
        <span class="lt-m-nama">${esc(g.nama)}</span>
        <div class="lt-m-bar"><i style="width:${g.util}%"></i></div>
        <span class="lt-m-nilai">${g.util}% · ${g.suhu}&deg;C</span>
      </div>
      <div class="lt-m">
        <span class="lt-m-nama">VRAM</span>
        <div class="lt-m-bar"><i style="width:${vp}%"></i></div>
        <span class="lt-m-nilai">${angka(g.vram_pakai_mb)} / ${angka(g.vram_total_mb)} MB</span>
      </div>` : ''}
      ${r ? `<div class="lt-m">
        <span class="lt-m-nama">RAM</span>
        <div class="lt-m-bar"><i style="width:${r.persen}%"></i></div>
        <span class="lt-m-nilai">${angka(r.pakai_gb, 1)} / ${angka(r.total_gb, 1)} GB</span>
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

    $('lt-jalan-bagian').hidden = !jalan.length;
    $('lt-jalan').innerHTML = jalan.map(kartuJalan).join('');
    $('lt-hasil').innerHTML = usai.length ? usai.map(kartuHasil).join('')
      : '<span class="lt-diam">belum ada hasil training di projek ini</span>';

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
  function blokEvaluasi(e) {
    if (!e) {
      return `<div class="lt-p-blok">
        <h4>Uji produksi</h4>
        <p class="lt-bantu">Belum diuji. Tekan <b>Uji produksi</b> di kartunya.
          mAP saja tidak cukup: ia diukur pada data yang sedomain dengan data
          latih, sedangkan yang menentukan adalah foto dari ruang detektor.</p>
      </div>`;
    }
    if (e.keadaan === 'jalan') {
      return `<div class="lt-p-blok"><h4>Uji produksi</h4>
        <p class="lt-diam">sedang berjalan…</p></div>`;
    }
    if (e.keadaan !== 'selesai') {
      return `<div class="lt-p-blok"><h4>Uji produksi</h4>
        <p class="lt-galat">${esc(e.galat || 'gagal tanpa keterangan')}</p></div>`;
    }
    const w = e.warna || {}, a = e.akurasi || {}, d = e.default || {},
          pu = e.putusan || {};
    const baris = (e.rinci || []).filter((x) => new Set(x.jawaban).size > 1);
    return `
      <div class="lt-p-blok">
        <h4>Uji produksi</h4>
        <div class="lt-putusan" data-tingkat="${esc(pu.tingkat)}">
          <b>${esc((pu.tingkat || '').toUpperCase())}</b>
          <span>${esc(pu.pesan || '')}</span>
        </div>
        ${e.mode_ket ? `<p class="lt-bantu lt-mode-baris">
          <b>Mode ${esc(e.mode)}</b> — ${esc(e.mode_ket.nilai)}</p>` : ''}
        <div class="lt-uji-angka">
          <span data-tingkat="${e.mode === 'warna' ? 'netral' : esc(w.tingkat)}">
            <i>Berubah karena RONA</i>
            <b>${w.tingkat === 'tak-terukur' ? '—' : angka(w.skor_rona, 0) + '%'}</b>
            <u>${e.mode === 'warna'
                 ? 'wajar di mode ini — rona memang penentu kelas'
                 : angka(w.berubah_rona) + ' dari ' + angka(w.total) + ' berubah'}</u></span>
          <span data-tingkat="${w.skor_terang >= 50 ? 'buruk'
                              : (w.skor_terang >= 20 ? 'sedang' : 'baik')}">
            <i>Berubah karena TERANG</i>
            <b>${w.tingkat === 'tak-terukur' ? '—' : angka(w.skor_terang, 0) + '%'}</b>
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
        <p class="lt-bantu">${esc(w.pesan || '')}</p>
        <p class="lt-bantu">Latar kosong diambil dari ${esc(e.sumber_latar || '-')}.
          Diuji pada ${angka(e.n_foto)} foto dari split test v${e.versi},
          ${e.perlakuan ? e.perlakuan.length : 0} perlakuan warna
          (${esc((e.perlakuan || []).join(', '))}).</p>
        ${baris.length ? `<details class="lt-lanjut">
          <summary>${baris.length} gambar yang jawabannya goyah</summary>
          <table class="lt-tabel">
            <thead><tr><th>berkas</th><th>sebenarnya</th>
              ${(e.perlakuan || []).map((n) => `<th class="${
                (e.terang || []).includes(n) ? 'lt-kol-terang' : ''}">${esc(n)}</th>`
              ).join('')}</tr></thead>
            <tbody>${baris.map((x) => `<tr>
              <td>${esc(x.berkas)}</td><td>${esc(x.sebenarnya || '-')}</td>
              ${x.jawaban.map((c) => `<td class="${c === x.jawaban[0] ? '' : 'lt-beda'}">`
                + `${esc(c || 'none')}</td>`).join('')}</tr>`).join('')}</tbody>
          </table></details>` : ''}
      </div>`;
  }

  async function bukaRincian(nomor) {
    $('lt-tirai').hidden = false;
    $('lt-panel-isi').innerHTML = '<span class="lt-diam">memuat…</span>';
    let r;
    try { r = await ambil(`/api/latih/rincian?nomor=${nomor}`); }
    catch (e) { $('lt-panel-isi').innerHTML = `<p class="lt-galat">gagal memuat: ${esc(e)}</p>`; return; }
    if (!r.ok) { $('lt-panel-isi').innerHTML = `<p class="lt-galat">${esc(r.error)}</p>`; return; }
    const t = r.latih, w = t.warna || {}, par = t.par || {};
    $('lt-panel-judul').textContent = `L${t.nomor} — ${t.nama}`;
    const parBaris = Object.keys(par).sort().map((k) =>
      `<span class="lt-kv"><i>${esc(k)}</i><b>${esc(par[k])}</b></span>`).join('');
    $('lt-panel-isi').innerHTML = `
      <div class="lt-p-blok">
        <h4>Keadaan</h4>
        <div class="lt-kartu-angka">
          <span><i>Status</i><b>${LABEL_KEADAAN[t.keadaan] || t.keadaan}</b></span>
          <span><i>Epoch</i><b>${angka(t.epoch)} / ${angka(t.epochs)}</b></span>
          <span><i>Lama</i><b>${durasi(t.detik)}</b></span>
          <span><i>Sumber</i><b>v${t.versi}</b></span>
        </div>
      </div>
      ${w.pesan ? `<div class="lt-p-blok lt-warna" data-tingkat="${esc(w.tingkat)}">
        <h4>Sinkronisasi warna dengan versinya</h4>
        <p class="lt-warna-pesan">${esc(w.pesan)}</p></div>` : ''}
      ${blokEvaluasi(r.evaluasi)}
      <div class="lt-p-blok">
        <h4>Metrik terbaik</h4>
        <div class="lt-kartu-metrik">${barisMetrik(t.terbaik)}</div>
      </div>
      ${t.punya_bobot ? `<div class="lt-p-blok">
        <h4>Grafik</h4>
        <img class="lt-grafik" alt="kurva hasil training"
             src="/latih/grafik?nomor=${t.nomor}&nama=results.png"
             onerror="this.replaceWith(Object.assign(document.createElement('span'),
                      {className:'lt-diam',textContent:'grafik belum ada'}))">
      </div>` : ''}
      <div class="lt-p-blok">
        <h4>Setelan yang dipakai</h4>
        <div class="lt-kv-grid">${parBaris}</div>
      </div>
      <div class="lt-p-blok">
        <h4>Log</h4>
        <pre class="lt-log">${esc(r.log || '(kosong)')}</pre>
      </div>`;
  }

  // ============================================================
  // PASANG
  // ============================================================

  if (bolehKelola) {
    $('lt-mulai').onclick = () => {
      $('lt-form').hidden = !$('lt-form').hidden;
      if (!$('lt-form').hidden) $('lt-nama').focus();
    };
    $('lt-tutup').onclick = () => { $('lt-form').hidden = true; };
    document.querySelectorAll('input[name="lt-mode"]').forEach((r) => {
      r.onchange = () => {
        // Ditandai supaya pilihan orang tidak ditimpa bawaan versi saat ia
        // berpindah versi — yang sudah diputuskan orang harus bertahan.
        $('lt-warna').dataset.disentuh = '1';
        gambarMode();
      };
    });
    $('lt-reset').onclick = () => {
      document.querySelectorAll('#lt-form [data-par]').forEach((el) => {
        el.value = BAHAN.preset[el.dataset.par];
      });
    };
    $('lt-tambah').onclick = () => {
      if (!$('lt-versi').value) { galat('pilih versi lebih dulu'); return; }
      const s = bacaSatu();
      if (!s.nama) s.nama = `Percobaan ${ANTREAN.length + 1}`;
      ANTREAN.push(s);
      galat('');
      gambarAntrean();
    };
    $('lt-jalankan').onclick = async () => {
      const versi = Number($('lt-versi').value || 0);
      if (!versi) { galat('pilih versi lebih dulu'); return; }
      const batch = ANTREAN.length ? ANTREAN : [bacaSatu()];
      $('lt-jalankan').disabled = true;
      try {
        const j = await ambil('/api/latih/mulai', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({versi, batch}),
        });
        if (!j.ok) { galat(j.error || 'gagal memulai'); return; }
        ANTREAN = [];
        gambarAntrean();
        galat('');
        $('lt-form').hidden = true;
        muatDaftar();
      } catch (e) {
        galat(String(e));
      } finally {
        $('lt-jalankan').disabled = false;
      }
    };
  }

  $('lt-panel-tutup').onclick = () => { $('lt-tirai').hidden = true; };
  $('lt-tirai').onclick = (e) => {
    if (e.target === $('lt-tirai')) $('lt-tirai').hidden = true;
  };
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) muatDaftar();
  });

  (async () => {
    if (bolehKelola) {
      try {
        BAHAN = await ambil('/api/latih/bahan');
        if (BAHAN.ok) {
          $('lt-bobot').innerHTML = BAHAN.bobot.map((b) =>
            `<option value="${esc(b.path)}" data-tugas="${esc(b.tugas)}">`
            + `${esc(b.nama)}${b.lokal ? '' : ' (unduh)'}</option>`).join('');
          $('lt-bobot').onchange = () => {
            const o = $('lt-bobot').selectedOptions[0];
            $('lt-bobot-ket').textContent =
              (BAHAN.bobot.find((b) => b.path === $('lt-bobot').value) || {}).ket || '';
            if (o) $('lt-tugas').value = o.dataset.tugas || 'segment';
          };
          gambarForm();
          $('lt-bobot').onchange();
          gambarAntrean();
        }
      } catch (e) {
        galat('gagal memuat bahan form: ' + e);
      }
    }
    muatDaftar();
  })();
})();
