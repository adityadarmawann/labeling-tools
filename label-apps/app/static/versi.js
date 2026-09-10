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
    gambarDetail(r.versi, r.isi || {});
  }

  function gambarDetail(v, isi) {
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
    const barisKelas = kelas.length
      ? `<div class="vs-bagian"><h4>Objek per kelas</h4><div class="vs-kelas">` +
        kelas.map(([n, c]) =>
          `<div><span>${n}</span><b>${c.toLocaleString('id')}</b></div>`).join('') +
        (isi.negatif ? `<div class="vs-neg"><span>sampel negatif</span>` +
          `<b>${isi.negatif.toLocaleString('id')}</b></div>` : '') +
        '</div></div>'
      : '';

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
  const resep = { pra: {}, aug: {}, fase: {}, volume: { per_gambar: 1 } };
  let katalog = null;
  let sumber = null;                     // hasil /api/versi/estimasi terakhir

  // ------------------------------------------------------------- langkah
  function buka(n) {
    wz.querySelectorAll('.wz-item').forEach((li) => {
      li.toggleAttribute('data-buka', Number(li.dataset.langkah) === n);
      li.toggleAttribute('data-lewat', Number(li.dataset.langkah) < n);
    });
    if (n === 5) hitungPerkiraan();
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
    gambarOperasi();
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
    el('wz-sumber').innerHTML =
      `<span><b>${r.n_sumber}</b> gambar</span>` +
      `<span><b>${r.objek_sumber}</b> objek</span>` +
      `<span><b>${r.kelas}</b> kelas</span>` +
      `<span><b>${r.negatif_sumber}</b> sampel negatif</span>`;
    el('wz-r1').textContent = `${r.n_sumber} gambar · ${r.kelas} kelas`;
    gambarSplit();
  }
  const jumlahVersi = () => document.querySelectorAll('.vs-kartu').length;

  // ---------------------------------------------------- langkah 2: split
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
      '[data-langkah="2"] [data-lanjut]');
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

  // ------------------------------------------- langkah 3 & 4: daftar operasi
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
      const urut = tahap === 'pra' ? katalog.urut_pra : Object.keys(katalog.aug);
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
          `Atur langkah · ${nyala.length} dari ${Object.keys(katalog[tahap]).length} menyala`;
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
    // belakang batangnya; daftar di langkah 3/4 baru digambar ulang saat
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

  // Satu layar untuk seluruh kelas projek: gabung, buang, atau ganti nama.
  // `peta` memakai INDEKS kelas (itu yang dibaca mesinnya) tetapi yang tampil
  // harus nama, jadi keduanya datang bersama dari /api/versi/estimasi.
  function layarKelas(tahap, oid) {
    const bagian = document.createElement('div');
    const daftar = (sumber && sumber.daftar_kelas) || [];
    if (!daftar.length) {
      bagian.innerHTML = '<p class="op-hampa">Dataset ini belum punya kelas '
        + 'bernama, jadi tidak ada yang bisa digabung atau diganti.</p>';
      return bagian;
    }
    const par = resep[tahap][oid] || {};
    const peta = par.peta || {};
    const nama = par.nama || {};

    // Perubahan di sini tidak berlaku selama saklarnya mati. Mengatakannya di
    // sini, bukan membiarkan orang menemukannya sesudah versinya jadi.
    if (!aktif(tahap, oid)) {
      const p = document.createElement('p');
      p.className = 'kl-mati';
      p.textContent = 'Modify Classes masih mati. Nyalakan saklarnya di daftar '
        + 'langkah supaya perubahan di sini ikut dipakai.';
      bagian.appendChild(p);
    }

    for (const k of daftar) {
      const baris = document.createElement('div');
      baris.className = 'kl';
      const lain = daftar.filter((x) => x.i !== k.i);
      const kini = peta[k.i] === null ? 'buang'
        : (peta[k.i] !== undefined ? `g:${peta[k.i]}`
          : (nama[k.i] !== undefined ? 'nama' : ''));
      baris.innerHTML =
        `<div class="kl-kiri"><b>${k.nama}</b>` +
        `<span class="kl-n">${k.objek.toLocaleString('id')} objek</span></div>` +
        '<select class="kl-sel">' +
        `<option value=""${kini === '' ? ' selected' : ''}>Biarkan</option>` +
        `<option value="nama"${kini === 'nama' ? ' selected' : ''}>Ganti nama</option>` +
        lain.map((x) => `<option value="g:${x.i}"` +
          `${kini === `g:${x.i}` ? ' selected' : ''}>Gabung ke ${x.nama}</option>`).join('') +
        `<option value="buang"${kini === 'buang' ? ' selected' : ''}>Buang kelas ini</option>` +
        '</select>' +
        `<input class="kl-nama" type="text" maxlength="60" placeholder="nama baru" ` +
        `value="${nama[k.i] !== undefined ? String(nama[k.i]).replace(/"/g, '&quot;') : ''}"` +
        `${kini === 'nama' ? '' : ' hidden'}>`;
      const sel = baris.querySelector('.kl-sel');
      const inp = baris.querySelector('.kl-nama');
      const tulis = () => {
        const e = entri(tahap, oid);
        e.peta = { ...(e.peta || {}) };
        e.nama = { ...(e.nama || {}) };
        delete e.peta[k.i];
        delete e.nama[k.i];
        if (sel.value === 'buang') e.peta[k.i] = null;
        else if (sel.value.startsWith('g:')) e.peta[k.i] = Number(sel.value.slice(2));
        else if (sel.value === 'nama') e.nama[k.i] = inp.value;
        gambarOperasi();
      };
      sel.onchange = () => {
        inp.hidden = sel.value !== 'nama';
        if (!inp.hidden && !inp.value) inp.value = k.nama;
        tulis();
        if (!inp.hidden) inp.focus();
      };
      inp.oninput = tulis;
      bagian.appendChild(baris);
    }
    return bagian;
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
    const semua = Object.keys(katalog[tahap]);
    const nyala = semua.filter((i) => aktif(tahap, i));

    // Dua kelompok, dinamai menurut AKIBATNYA — bukan asal-usulnya. Dengan
    // begitu tidak ada operasi yang pernah hilang dari popup: dulu kelompok
    // disaring "yang belum dipakai", dan begitu semuanya terpakai isinya jadi
    // kalimat "Semuanya sudah dipakai" — jalan buntu yang tidak menawarkan
    // satu pun tindakan.
    for (const [kunci, judul] of [[true, 'Bawaan (menyala sejak awal)'],
                                  [false, 'Opsional (mati sejak awal)']]) {
      const ids = semua.filter((i) => !!katalog[tahap][i].bawaan_aktif === kunci
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

  // ------------------------------------------------------- langkah 5: buat
  function kumpulkanResep() {
    resep.volume.per_gambar = Number(el('wz-salin').value) || 0;
    resep.fase = {
      crop_zoom: { aktif: el('wz-f-crop').checked },
      balans_skala: { aktif: el('wz-f-skala').checked },
      balans_kelas: { aktif: el('wz-f-kelas').checked },
      porsi_negatif: { aktif: el('wz-f-neg').checked },
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
    // baris ini langkah 5 diam soal keempatnya, sehingga satu-satunya cara
    // tahu balancer sedang menyala adalah kembali ke langkah 4 dan membukanya.
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
    t.innerHTML =
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
    // transform dimatikan di langkah 4, ketiganya tidak menghasilkan apa pun
    // — sementara sakelarnya di sana tetap tampak menyala dan tidak
    // menceritakan itu kepada siapa pun.
    const matiAug = r.ada_aug === false
      ? '<span class="split-warn">Semua transform augmentasi dimatikan, jadi '
        + 'Salinan per gambar, Seimbangkan jumlah kelas, dan Pulihkan porsi '
        + 'sampel negatif ikut tidak berjalan.</span><br>' : '';
    el('wz-perkira').innerHTML = keluhan + matiAug +
      `<b>${r.n.toLocaleString('id')}</b> gambar diperkirakan ` +
      `(${r.n_sumber.toLocaleString('id')} sumber + ${r.tambahan.toLocaleString('id')} hasil), ` +
      `sekitar <b>${mb(r.byte)}</b> di disk.` +
      (r.cukup ? '' : `<span class="split-warn"> Ruang disk tidak cukup, ` +
        `tersisa ${mb(r.disk_kosong)}.</span>`) +
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
  fetch('/api/versi/kemajuan').then((r) => r.json()).then((k) => {
    if (k && k.jalan) {
      wz.hidden = false;
      el('vs-mulai').hidden = true;
      wz.querySelector('.wz-langkah').hidden = true;
      el('wz-kerja').hidden = false;
      pantauKerja();
    }
  }).catch(() => {});
})();
