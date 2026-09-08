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
          ${isi.byte ? `<div><span>Ukuran di disk</span><b>${mb(isi.byte)}</b></div>` : ''}
        </div>
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
    const p = Object.entries(meta.param || {});
    if (!p.length) return '';
    return p.map(([k, def]) => `${k} ${par[k] !== undefined ? par[k] : def.bawaan}`)
      .join(' · ');
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

  function bukaPopup(tahap) {
    if (!katalog) return;
    tahapPopup = tahap;
    el('op-judul').textContent = tahap === 'pra' ? 'Preprocessing' : 'Augmentasi';
    el('op-ket').textContent = tahap === 'pra'
      ? 'Dikenakan ke setiap gambar di train, valid, dan test.'
      : 'Menghasilkan salinan tambahan dari data train.';
    // Kotak cari hanya kalau daftarnya memang panjang. Pada 8 operasi
    // preprocessing ia cuma satu kotak lagi untuk dilewati.
    const cari = el('op-cari');
    cari.hidden = Object.keys(katalog[tahap]).length <= 12;
    cari.value = '';
    gambarPopup();
    dlg.hidden = false;
    (cari.hidden ? dlg.querySelector('.sk-in') : cari)?.focus();
  }

  function gambarPopup() {
    const tahap = tahapPopup;
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
        wadah.appendChild(lab);
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
    `${url}?split=${encodeURIComponent(rasioTeks())}`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ resep: kumpulkanResep() }) })
    .then((r) => r.json()).catch(() => null);

  async function hitungPerkiraan() {
    const t = el('wz-tinjau');
    const daftar = (tahap) => Object.keys(katalog ? katalog[tahap] : {})
      .filter((i) => aktif(tahap, i))
      .map((i) => katalog[tahap][i].nama);
    t.innerHTML =
      `<div><span>Preprocessing</span><b>${daftar('pra').join(', ') || '-'}</b></div>` +
      `<div><span>Augmentasi</span><b>${daftar('aug').join(', ') || 'dimatikan'}</b></div>` +
      `<div><span>Salinan per gambar</span><b>${el('wz-salin').value}</b></div>` +
      `<div><span>Pembagian</span><b>${rasioTeks().replace(/,/g, ' / ')}</b></div>`;
    el('wz-perkira').textContent = 'menghitung perkiraan…';
    const r = await kirimResep('/api/versi/estimasi');
    if (!r || !r.ok) { el('wz-perkira').textContent = (r && r.error) || 'gagal'; return; }
    // Sama seperti panel ekspor: rasio yang tidak terbaca tetap dipakai
    // sebagai bawaan, tetapi dikatakan di sebelah angka yang terpengaruh.
    const keluhan = r.rasio_pesan
      ? `<span class="split-warn">${r.rasio_pesan}</span><br>` : '';
    el('wz-perkira').innerHTML = keluhan +
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
      `/api/versi/mulai?split=${encodeURIComponent(rasioTeks())}&catatan=${c}`,
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
        (k.fase_nama || '') + (k.fase_ke ? ` (${k.fase_ke}/${k.fase_dari})` : '');
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
