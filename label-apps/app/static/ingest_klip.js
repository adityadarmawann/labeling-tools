'use strict';

/*
 * Ingest klip (projek VIDEO), panel "Tambah klip" di halaman Unggah.
 *
 * Alur: video sumber (unggah berkas / URL YouTube / scrape) -> POTONG jadi klip
 * -> (opsional) SARING per objek. Klip hasilnya langsung terbaca klip_scan,
 * jadi muncul di Label Aksi tanpa langkah lain. Memakai endpoint yang sudah ada:
 *   GET  /api/video/siap, /api/video/sumber, /api/video/kemajuan
 *   POST /api/video/url, /api/video/scrape, /api/video/potong, /api/video/filter
 *   PUT  /upload (video -> _sumber/)
 * send()/toast() global dari app.js. Terpisah dari wizard unggah gambar.
 */
(() => {
  const panel = document.getElementById('ik-panel');
  if (!panel) return;
  const EDS = encodeURIComponent(panel.dataset.ds || '');
  const el = (id) => document.getElementById(id);
  const enc = encodeURIComponent;

  let terpilih = '';   // nama video sumber yang dipilih untuk dipotong

  // ---- kesiapan: tampilkan tab scrape, matikan YOLO kalau tak siap ----
  (async () => {
    const r = await send(`/api/video/siap?ds=${EDS}`);
    if (!r || !r.ok) return;
    const s = r.siap || {};
    if (s.scraper) el('ik-tab-scrape').hidden = false;
    if (s.filter_yolo && !s.filter_yolo.ok) {
      const o = el('ik-filter-metode').querySelector('option[value="yolo"]');
      if (o) { o.disabled = true; o.textContent += ' — tidak tersedia'; }
    }
  })();

  // ---- tab sumber ----
  panel.querySelectorAll('.ik-sumber-tab [data-tab]').forEach(btn => {
    btn.addEventListener('click', () => {
      panel.querySelectorAll('.ik-sumber-tab [data-tab]').forEach(b => b.removeAttribute('data-on'));
      btn.setAttribute('data-on', '');
      panel.querySelectorAll('.ik-tabisi').forEach(x => { x.hidden = x.dataset.isi !== btn.dataset.tab; });
    });
  });

  // ---- daftar video sumber ----
  async function muatSumber() {
    const box = el('ik-sumber-daftar');
    const r = await send(`/api/video/sumber?ds=${EDS}`);
    if (!r || !r.ok) { box.innerHTML = ''; return; }
    const list = r.sumber || [];
    if (!list.length) {
      box.innerHTML = '<p class="halus">Belum ada video sumber. Unggah berkas atau tempel URL di atas.</p>';
      return;
    }
    box.innerHTML = '<div class="ik-sumber-judul halus">Video di server:</div>';
    list.forEach(v => {
      const row = document.createElement('div');
      row.className = 'ik-sumber-baris' + (v.nama === terpilih ? ' ik-on' : '');
      const nm = document.createElement('span');
      nm.className = 'ik-sumber-nama'; nm.textContent = `${v.nama} · ${v.mb} MB`;
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'chip'; b.textContent = 'Potong →';
      b.addEventListener('click', () => pilihSumber(v.nama));
      row.appendChild(nm); row.appendChild(b);
      box.appendChild(row);
    });
  }

  function pilihSumber(nama) {
    terpilih = nama;
    el('ik-potong-nama').textContent = '· ' + nama;
    el('ik-potong-kartu').hidden = false;
    el('ik-batch').placeholder = nama.replace(/\.[^.]+$/, '');
    muatSumber();
    el('ik-potong-kartu').scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // PUT video lewat XHR (bukan fetch) supaya ADA timeout: klip bisa beberapa GB,
  // dan fetch yang menggantung tak pernah selesai. Lantai 2 menit + skala ukuran.
  function putKlip(url, file) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', url);
      xhr.timeout = Math.max(120000, Math.ceil((file.size || 0) / 250000) * 1000);
      xhr.onload = () => {
        let j = {};
        try { j = JSON.parse(xhr.responseText || '{}'); } catch (e) { j = {}; }
        resolve({ ok: xhr.status >= 200 && xhr.status < 300, j });
      };
      xhr.onerror = () => reject(new Error('jaringan'));
      xhr.ontimeout = () => reject(new Error('timeout (unggahan menggantung)'));
      xhr.send(file);
    });
  }

  // ---- unggah berkas video (PUT /upload -> _sumber/) ----
  const fin = el('ik-unggah-file');
  if (fin) fin.addEventListener('change', async () => {
    const f = fin.files && fin.files[0];
    if (!f) return;
    const maju = el('ik-unggah-maju');
    maju.textContent = `Mengunggah ${(f.size / 1048576).toFixed(0)} MB…`;
    try {
      const { ok, j } = await putKlip(`/upload?ds=${EDS}&name=${enc(f.name)}`, f);
      fin.value = '';
      if (!ok || j.ok === false) { maju.textContent = j.error || 'gagal unggah'; toast(j.error || 'gagal unggah'); return; }
      maju.textContent = 'Terunggah.';
      await muatSumber();
      pilihSumber(f.name);
    } catch (e) { maju.textContent = 'gagal menghubungi server'; }
  });

  // ---- URL ----
  const urlBtn = el('ik-url-btn');
  if (urlBtn) urlBtn.addEventListener('click', async () => {
    const url = (el('ik-url').value || '').trim();
    if (!url) { toast('Tempel URL dulu'); return; }
    const maju = el('ik-url-maju');
    urlBtn.disabled = true; maju.textContent = 'Mengunduh… (bisa beberapa menit)';
    const r = await send(`/api/video/url?ds=${EDS}&url=${enc(url)}`, { method: 'POST' });
    urlBtn.disabled = false;
    if (!r || !r.ok) { maju.textContent = (r && r.error) || 'gagal'; toast((r && r.error) || 'gagal unduh'); return; }
    maju.textContent = `Terunduh: ${r.judul || r.nama}`;
    el('ik-url').value = '';
    await muatSumber();
    if (r.nama) pilihSumber(r.nama);
  });

  // ---- scrape ----
  const scrBtn = el('ik-scrape-btn');
  if (scrBtn) {
    let scrPoll = null;
    async function scrPantau() {
      const k = await send(`/api/video/kemajuan?ds=${EDS}`);
      if (!k || !k.ok) return;
      const maju = el('ik-scrape-maju'), prog = el('ik-scrape-prog'), fill = el('ik-scrape-fill');
      const p = Math.round((k.persen || 0) * 100);
      prog.hidden = false; fill.style.width = p + '%';
      maju.textContent = `${k.tahap || ''} ${p}% · ${k.diunduh || 0} unduh / ${k.dilewati || 0} lewat / ${k.gagal || 0} gagal`;
      if (k.tahap === 'selesai' || k.tahap === 'gagal') {
        if (scrPoll) { clearInterval(scrPoll); scrPoll = null; }
        scrBtn.disabled = false;
        maju.textContent = k.tahap === 'gagal'
          ? 'Gagal: ' + (k.error || '')
          : `Selesai: ${k.diunduh || 0} video terunduh.`;
        muatSumber();
      }
    }
    scrBtn.addEventListener('click', async () => {
      const q = (el('ik-scrape-q').value || '').trim();
      if (!q) { toast('Isi kata kunci'); return; }
      const maks = Math.min(50, Math.max(1, parseInt(el('ik-scrape-maks').value, 10) || 10));
      scrBtn.disabled = true;
      const r = await send(`/api/video/scrape?ds=${EDS}&query=${enc(q)}&maks=${maks}`, { method: 'POST' });
      if (!r || !r.ok) { el('ik-scrape-maju').textContent = (r && r.error) || 'gagal'; toast((r && r.error) || 'gagal'); scrBtn.disabled = false; return; }
      if (!scrPoll) scrPoll = setInterval(scrPantau, 800);
      scrPantau();
    });
  }

  // ---- potong ----
  const potBtn = el('ik-potong-btn');
  if (potBtn) potBtn.addEventListener('click', async () => {
    if (!terpilih) { toast('Pilih video sumber dulu'); return; }
    const maju = el('ik-potong-maju');
    potBtn.disabled = true; maju.textContent = 'Memotong… (bisa beberapa menit, jangan tutup tab)';
    const resep = {
      fps: parseInt(el('ik-fps').value, 10),
      potong: { setiap_detik: parseFloat(el('ik-detik').value) || 1 },
      ukuran: [parseInt(el('ik-w').value, 10) || 224, parseInt(el('ik-h').value, 10) || 224],
      letterbox: el('ik-letterbox').checked,
    };
    const batch = (el('ik-batch').value || '').trim();
    let u = `/api/video/potong?ds=${EDS}&name=${enc(terpilih)}`;
    if (batch) u += `&batch=${enc(batch)}`;
    const r = await send(u, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(resep) });
    potBtn.disabled = false;
    if (!r || !r.ok) { maju.textContent = (r && r.error) || 'gagal'; toast((r && r.error) || 'gagal potong'); return; }
    maju.innerHTML = `${r.ditulis} klip dibuat${r.dikarantina ? ` (${r.dikarantina} dikarantina)` : ''} di unggahan "${r.batch}". `
      + `<a href="/aksi?ds=${EDS}">Lihat di Label Aksi &rarr;</a>`;
    toast(`${r.ditulis} klip dibuat`);
    el('ik-filter-kartu').hidden = false;
    isiFilterBatch(r.batch);
  });

  // ---- filter objek ----
  function isiFilterBatch(pilih) {
    send(`/api/aksi/keterangan?ds=${EDS}&per=6`).then(r => {
      const sel = el('ik-filter-batch');
      const bh = (r && r.ok && r.batch_hitung) || {};
      const names = Object.keys(bh).sort();
      sel.innerHTML = names.map(n => `<option value="${n}">${n} (${bh[n]})</option>`).join('');
      if (pilih && names.includes(pilih)) sel.value = pilih;
    });
  }
  const selMetode = el('ik-filter-metode');
  if (selMetode) selMetode.addEventListener('change', () => {
    el('ik-filter-conf-wrap').hidden = selMetode.value !== 'yolo';
  });
  function konfigFilter() {
    const metode = selMetode.value;
    const c = { metode, min_frame: parseInt(el('ik-filter-minframe').value, 10) || 2 };
    if (metode === 'yolo') c.conf = parseFloat(el('ik-filter-conf').value) || 0.25;
    return c;
  }
  async function jalankanFilter(pratinjau) {
    const batch = el('ik-filter-batch').value;
    if (!batch) { toast('Pilih unggahan dulu'); return; }
    const maju = el('ik-filter-maju');
    maju.textContent = pratinjau ? 'Memeriksa…' : 'Menyaring…';
    const r = await send(`/api/video/filter?ds=${EDS}&batch=${enc(batch)}&pratinjau=${pratinjau}`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(konfigFilter()) });
    if (!r || !r.ok) { maju.textContent = (r && r.error) || 'gagal'; toast((r && r.error) || 'gagal'); return; }
    const pr = Math.round((r.pass_rate || 0) * 100);
    if (pratinjau) {
      maju.textContent = `${r.lolos}/${r.total} lolos (${pr}%); ${r.ditolak} akan dibuang.`;
      el('ik-filter-terapkan').disabled = r.ditolak === 0;
    } else {
      maju.innerHTML = `${(r.dipindah || []).length} klip dibuang. <a href="/aksi?ds=${EDS}">Lihat &rarr;</a>`;
      toast(`${(r.dipindah || []).length} klip dibuang`);
      el('ik-filter-terapkan').disabled = true;
    }
  }
  if (el('ik-filter-pratinjau')) el('ik-filter-pratinjau').addEventListener('click', () => jalankanFilter(true));
  if (el('ik-filter-terapkan')) el('ik-filter-terapkan').addEventListener('click', () => jalankanFilter(false));

  muatSumber();
})();
