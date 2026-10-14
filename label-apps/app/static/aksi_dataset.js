'use strict';

/*
 * Tab DATASET projek VIDEO (classifier aksi) — padanan `/` gambar, untuk KLIP.
 * Grid klip yang SUDAH di dataset: thumbnail DIAM (poster /aksi/thumb), filter
 * kelas, pilih + Keluarkan (/api/aksi/dataset/keluarkan), "Lihat" memutar klip
 * (auto-play + loop) di overlay — satu-satunya tempat klip berputar.
 *
 * Dipaginasi DI SERVER lewat /api/aksi/keterangan?dsf=di: peramban hanya
 * memegang satu halaman, bukan seluruh dataset ([[halaman-besar-wajib-paginasi]]).
 * send() + toast() global dari app.js.
 */

const D = JSON.parse(document.getElementById('akd-awal').textContent);
const EDS = encodeURIComponent(D.ds);
const enc = encodeURIComponent;
const el = id => document.getElementById(id);

function warnaKelas(nama, i) {
  if (D.warna && D.warna[i] != null) return D.warna[i];
  let h = 0;
  for (let k = 0; k < nama.length; k++) h = (h * 31 + nama.charCodeAt(k)) % 360;
  return `hsl(${h} 55% 45%)`;
}
const IDX_KELAS = {};
(D.kelas || []).forEach((k, i) => { IDX_KELAS[k] = i; });

let page = [];        // klip halaman kini (sudah di dataset)
let meta = { hal: 1, n_hal: 1, per: 60, mulai: 0, n_tampil: 0,
             kelas_hitung: {}, batch_hitung: {} };
let fkelas = '';
let fbatch = '';
let cari = '';
let urut = 'nama';
let perPage = 60;
const terpilih = new Set();
let memuat = false;

const grid = el('akd-grid');
const kosongEl = el('akd-kosong');
const halEl = el('akd-hal');
const nEl = el('akd-n');
const selKelas = el('akd-kelas');
const btnKeluar = el('akd-keluarkan');
const cbSemua = el('akd-semua');

function urlKet(hal) {
  let u = `/api/aksi/keterangan?ds=${EDS}&dsf=di&hal=${hal}&per=${perPage}`
    + `&urut=${urut}`;
  if (fkelas) u += '&kelas=' + enc(fkelas);
  if (fbatch) u += '&batch=' + enc(fbatch);
  if (cari) u += '&q=' + enc(cari);
  return u;
}

async function muat(hal) {
  if (memuat) return;
  memuat = true;
  const r = await send(urlKet(Math.max(1, hal)));
  memuat = false;
  if (!r || !r.ok) {
    grid.innerHTML = ''; kosongEl.hidden = false;
    kosongEl.textContent = (r && r.error) || 'gagal memuat'; return;
  }
  page = (r.klips || []).map(c => ({ ...c }));
  meta = { hal: r.hal, n_hal: r.n_hal, per: r.per, mulai: r.mulai || 0,
           n_tampil: r.n_tampil, kelas_hitung: r.kelas_hitung || {},
           batch_hitung: r.batch_hitung || {} };
  perPage = r.per;
  isiKelas();
  isiFilterBatch();
  aktifkanBersih();
  render();
}

function isiKelas() {
  const ada = Object.keys(meta.kelas_hitung).sort();
  const lama = selKelas.value;
  selKelas.innerHTML = '<option value="">Semua</option>'
    + ada.map(k => `<option value="${k}">${k} (${meta.kelas_hitung[k]})</option>`).join('');
  selKelas.value = ada.includes(lama) ? lama : (fkelas || '');
}

function isiFilterBatch() {
  const wrap = el('akd-fbatch-wrap'), sel = el('akd-f-batch');
  if (!wrap || !sel) return;
  const ada = Object.keys(meta.batch_hitung || {}).sort();
  wrap.hidden = ada.length < 2 && !fbatch;
  const lama = sel.value;
  sel.innerHTML = '<option value="">Semua unggahan</option>'
    + ada.map(b => `<option value="${b}">${b} (${meta.batch_hitung[b]})</option>`).join('');
  sel.value = ada.includes(lama) ? lama : (fbatch || '');
}

function aktifkanBersih() {
  const chip = el('akd-bersih');
  if (!chip) return;
  const n = (fkelas ? 1 : 0) + (fbatch ? 1 : 0) + (cari ? 1 : 0);
  chip.hidden = n === 0;
  chip.innerHTML = `&times; Bersihkan saringan${n ? ' ' + n : ''}`;
}

function render() {
  nEl.textContent = meta.n_tampil.toLocaleString('id-ID');
  grid.innerHTML = '';
  kosongEl.hidden = page.length > 0;
  if (!page.length && !kosongEl.textContent) kosongEl.textContent =
    'Belum ada klip di dataset pada saringan ini.';
  page.forEach(it => grid.appendChild(kartu(it)));
  renderHal();
  if (cbSemua) cbSemua.checked = page.length > 0
    && page.every(it => terpilih.has(it.rel));
  perbaruiKeluar();
}

function kartu(it) {
  const li = document.createElement('li');
  li.className = 'akg-kartu';
  const img = document.createElement('img');
  img.className = 'akg-thumb'; img.loading = 'lazy'; img.alt = it.label || '';
  img.src = `/aksi/thumb?ds=${EDS}&rel=${enc(it.rel)}&s=240`;
  img.addEventListener('error', () => { img.classList.add('akg-thumb-x'); });
  li.appendChild(img);

  if (it.label) {
    const b = document.createElement('span');
    b.className = 'akg-label';
    b.style.background = warnaKelas(it.label, IDX_KELAS[it.label] ?? 0);
    b.textContent = it.label;
    li.appendChild(b);
  }

  const bar = document.createElement('div');
  bar.className = 'akg-kartu-bar';
  const lihat = document.createElement('button');
  lihat.type = 'button'; lihat.className = 'chip akg-lihat';
  lihat.textContent = 'Lihat';
  lihat.addEventListener('click', () => putar(it));
  bar.appendChild(lihat);

  if (cbSemua || btnKeluar) {       // kontrol pilih hanya kalau boleh_kelola
    const cb = document.createElement('input');
    cb.type = 'checkbox'; cb.className = 'akg-cb';
    cb.checked = terpilih.has(it.rel);
    cb.addEventListener('change', () => {
      if (cb.checked) terpilih.add(it.rel); else terpilih.delete(it.rel);
      perbaruiKeluar();
    });
    bar.appendChild(cb);
  }
  li.appendChild(bar);
  return li;
}

function nomorHalaman(cur, total) {
  const out = []; let last = 0;
  for (let p = 1; p <= total; p++) {
    if (p === 1 || p === total || (p >= cur - 2 && p <= cur + 2)) {
      if (last && p - last > 1) out.push('…');
      out.push(p); last = p;
    }
  }
  return out;
}

function renderHal() {
  halEl.className = 'hal';
  halEl.innerHTML = '';
  if (meta.n_tampil === 0) { halEl.hidden = true; return; }
  halEl.hidden = false;
  const goto = (h) => { muat(h); window.scrollTo({ top: 0, behavior: 'smooth' }); };

  const pp = document.createElement('div');
  pp.className = 'hal-pp'; pp.appendChild(document.createTextNode('Per halaman '));
  const selPer = document.createElement('select');
  [24, 60, 120, 200].forEach(n => {
    const o = document.createElement('option'); o.value = n; o.textContent = n;
    if (n === meta.per) o.selected = true; selPer.appendChild(o);
  });
  selPer.addEventListener('change', () => { perPage = +selPer.value; muat(1); });
  pp.appendChild(selPer); halEl.appendChild(pp);

  const info = document.createElement('span');
  info.className = 'hal-info';
  const a = meta.mulai + 1, b = meta.mulai + page.length;
  info.innerHTML = `${a.toLocaleString('id-ID')}&ndash;${b.toLocaleString('id-ID')} dari `
    + `${meta.n_tampil.toLocaleString('id-ID')} <em>klip</em>`;
  halEl.appendChild(info);

  const nav = document.createElement('div'); nav.className = 'hal-nav';
  const arrow = (txt, ke, mati) => {
    const x = document.createElement('button');
    x.type = 'button'; x.className = 'hal-n'; x.textContent = txt;
    if (mati) x.setAttribute('data-mati', ''); else x.addEventListener('click', ke);
    return x;
  };
  nav.appendChild(arrow('‹', () => goto(meta.hal - 1), meta.hal <= 1));
  nomorHalaman(meta.hal, meta.n_hal).forEach(p => {
    if (p === '…') {
      const g = document.createElement('span'); g.className = 'hal-gap'; g.textContent = '…';
      nav.appendChild(g);
    } else {
      const bn = document.createElement('button');
      bn.type = 'button'; bn.className = 'hal-n'; bn.textContent = p;
      if (p === meta.hal) bn.setAttribute('data-on', '');
      else bn.addEventListener('click', () => goto(p));
      nav.appendChild(bn);
    }
  });
  nav.appendChild(arrow('›', () => goto(meta.hal + 1), meta.hal >= meta.n_hal));
  halEl.appendChild(nav);

  if (meta.n_hal > 10) {
    const j = document.createElement('div'); j.className = 'hal-loncat';
    j.appendChild(document.createTextNode('Ke halaman '));
    const inp = document.createElement('input');
    inp.type = 'number'; inp.min = 1; inp.max = meta.n_hal; inp.value = meta.hal;
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') goto(Math.min(meta.n_hal, Math.max(1, +inp.value || 1)));
    });
    j.appendChild(inp); halEl.appendChild(j);
  }
}

function perbaruiKeluar() {
  if (btnKeluar) btnKeluar.disabled = terpilih.size === 0;
}

// -- pilih semua (halaman ini) ----------------------------------------------
if (cbSemua) {
  cbSemua.addEventListener('change', () => {
    page.forEach(it => {
      if (cbSemua.checked) terpilih.add(it.rel); else terpilih.delete(it.rel);
    });
    perbaruiKeluar();
    render();
  });
}

// -- keluarkan terpilih dari dataset ----------------------------------------
if (btnKeluar) {
  btnKeluar.addEventListener('click', async () => {
    const rels = [...terpilih];
    if (!rels.length) return;
    btnKeluar.disabled = true;
    const r = await send(`/api/aksi/dataset/keluarkan?ds=${EDS}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ klip: rels }),
    });
    if (!r || !r.ok) { toast((r && r.error) || 'gagal'); btnKeluar.disabled = false; return; }
    toast(`${r.dikeluarkan || rels.length} klip dikeluarkan`);
    terpilih.clear();
    muat(meta.hal);
  });
}

selKelas.addEventListener('change', () => { fkelas = selKelas.value; muat(1); });

const selFBatch = el('akd-f-batch');
if (selFBatch) selFBatch.addEventListener('change', () => { fbatch = selFBatch.value; muat(1); });
const selUrut = el('akd-urut');
if (selUrut) selUrut.addEventListener('change', () => { urut = selUrut.value; muat(1); });
const cariForm = el('akd-cari-form');
if (cariForm) cariForm.addEventListener('submit', (e) => {
  e.preventDefault();
  cari = (el('akd-cari').value || '').trim();
  muat(1);
});
const btnBersih = el('akd-bersih');
if (btnBersih) btnBersih.addEventListener('click', () => {
  fkelas = ''; fbatch = ''; cari = ''; urut = 'nama';
  selKelas.value = '';
  if (selFBatch) selFBatch.value = '';
  if (selUrut) selUrut.value = 'nama';
  const ci = el('akd-cari'); if (ci) ci.value = '';
  muat(1);
});

// -- overlay pemutar (Lihat): auto-play + loop DI SINI saja -----------------
const overlay = el('akg-overlay');
const vid = el('akg-video');
const vnama = el('akg-vnama');
function putar(it) {
  vid.src = `/klip?ds=${EDS}&name=${enc(it.rel)}`;
  vnama.textContent = `${it.label || '(belum dilabeli)'} · ${it.rel.split('/').pop()}`;
  overlay.hidden = false;
  vid.play().catch(() => {});
}
function tutup() {
  vid.pause(); vid.removeAttribute('src'); vid.load();
  overlay.hidden = true;
}
el('akg-tutup').addEventListener('click', tutup);
overlay.addEventListener('click', e => { if (e.target === overlay) tutup(); });
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !overlay.hidden) tutup();
});

muat(1);
