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
  const PAR_UTAMA = [
    ['epochs', 'Epoch', 'Berapa kali seluruh dataset dilewati. v14 memakai 250.'],
    ['batch', 'Batch', 'Gambar per langkah. Terlalu besar = VRAM habis.'],
    ['imgsz', 'Ukuran gambar', 'Sisi gambar saat dilatih, piksel.'],
    ['patience', 'Sabar', 'Berhenti kalau tidak membaik sekian epoch. 0 = tidak pernah.'],
  ];
  const PAR_LANJUT = [
    ['lr0', 'Laju belajar awal', ''],
    ['lrf', 'Laju belajar akhir', 'Pecahan dari lr0.'],
    ['warmup_epochs', 'Pemanasan', ''],
    ['workers', 'Worker', 'Utas pembaca data.'],
    ['box', 'Bobot loss kotak', ''],
    ['cls', 'Bobot loss kelas', 'v14 memakai 2.0. Bukan obat untuk pintasan warna.'],
    ['dfl', 'Bobot loss DFL', ''],
    ['scale', 'Skala', 'v14 menahannya di 0.3 — variasi ukuran sudah diurus versinya.'],
    ['degrees', 'Rotasi (derajat)', ''],
    ['translate', 'Geser', ''],
    ['fliplr', 'Balik kiri-kanan', ''],
    ['flipud', 'Balik atas-bawah', ''],
    ['mosaic', 'Mosaic', 'v14 menurunkannya ke 0.3; 0.6 membuat objek terlalu kecil.'],
    ['close_mosaic', 'Tutup mosaic', 'Epoch terakhir tanpa mosaic.'],
    ['copy_paste', 'Copy-paste', 'v14 mematikannya — merusak konteks RVM.'],
    ['mask_ratio', 'Rasio mask', 'Khusus segmentasi.'],
  ];
  const PAR_WARNA = ['hsv_h', 'hsv_s', 'hsv_v', 'bgr'];

  function kotakPar(kunci, label, bantu) {
    const p = BAHAN.preset[kunci];
    const b = BAHAN.batas[kunci];
    const langkah = Number.isInteger(p) ? 1 : (p < 0.01 ? 0.0001 : 0.01);
    return `<label class="lt-p">
      <span class="lt-p-nama">${esc(label)}</span>
      <input type="number" data-par="${esc(kunci)}" value="${p}"
             ${b ? `min="${b[0]}" max="${b[1]}"` : ''} step="${langkah}">
      ${bantu ? `<span class="lt-bantu">${esc(bantu)}</span>` : ''}
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

    const w = v.warna || {};
    const box = $('lt-warna');
    box.hidden = false;
    box.dataset.tingkat = w.tingkat || 'ok';
    $('lt-warna-ikon').textContent = w.tingkat === 'awas' ? '!' : '✓';
    $('lt-warna-judul').textContent = w.dibuka
      ? 'Warna dibuka di versinya' : 'Warna terkunci di versinya';
    $('lt-warna-pesan').textContent = w.pesan || '';
    const s = w.saran || {};
    $('lt-warna-nilai').textContent =
      `hsv_h ${s.hsv_h} · hsv_s ${s.hsv_s} · hsv_v ${s.hsv_v} · bgr ${s.bgr}`;
    terapkanWarna();
  }

  /* Saat "ikuti warna versinya" dicentang, keempat angka warna dikunci ke
     saran dan kotaknya dinonaktifkan — bukan sekadar diisi. Kotak yang
     terisi tapi bisa diubah mengundang orang mengubahnya tanpa tahu bahwa
     yang ia ubah adalah separuh dari pasangan yang harus sejalan. */
  function terapkanWarna() {
    const n = Number($('lt-versi').value || 0);
    const v = BAHAN.versi.find((x) => x.nomor === n);
    const ikut = $('lt-warna-ikut').checked;
    const s = (v && v.warna && v.warna.saran) || {};
    let kotak = document.querySelector('#lt-par-warna');
    if (!kotak) {
      kotak = document.createElement('div');
      kotak.id = 'lt-par-warna';
      kotak.className = 'lt-par lt-par-warna';
      $('lt-par').after(kotak);
    }
    kotak.innerHTML = PAR_WARNA.map((k) => kotakPar(k, k, '')).join('');
    PAR_WARNA.forEach((k) => {
      const el = kotak.querySelector(`[data-par="${k}"]`);
      if (!el) return;
      if (ikut) { el.value = s[k] ?? BAHAN.preset[k]; el.disabled = true; }
      else el.disabled = false;
    });
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
    $('lt-warna-ikut').onchange = terapkanWarna;
    $('lt-reset').onclick = () => {
      document.querySelectorAll('#lt-form [data-par]').forEach((el) => {
        el.value = BAHAN.preset[el.dataset.par];
      });
      $('lt-warna-ikut').checked = true;
      terapkanWarna();
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
