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
let meta = { hal: 1, n_hal: 1, per: 60, n_tampil: 0, kelas_hitung: {} };
let fkelas = '';
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
  let u = `/api/aksi/keterangan?ds=${EDS}&dsf=di&hal=${hal}&per=${meta.per}`;
  if (fkelas) u += '&kelas=' + enc(fkelas);
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
  meta = { hal: r.hal, n_hal: r.n_hal, per: r.per, n_tampil: r.n_tampil,
           kelas_hitung: r.kelas_hitung || {} };
  isiKelas();
  render();
}

function isiKelas() {
  const ada = Object.keys(meta.kelas_hitung).sort();
  const lama = selKelas.value;
  selKelas.innerHTML = '<option value="">Semua</option>'
    + ada.map(k => `<option value="${k}">${k} (${meta.kelas_hitung[k]})</option>`).join('');
  selKelas.value = ada.includes(lama) ? lama : (fkelas || '');
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

function renderHal() {
  halEl.innerHTML = '';
  if (meta.n_hal <= 1) return;
  const mk = (txt, ke) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'chip'; b.textContent = txt;
    b.addEventListener('click', () => { muat(ke);
      window.scrollTo({ top: 0, behavior: 'smooth' }); });
    return b;
  };
  if (meta.hal > 1) halEl.appendChild(mk('‹', meta.hal - 1));
  halEl.appendChild(document.createTextNode(` ${meta.hal} / ${meta.n_hal} `));
  if (meta.hal < meta.n_hal) halEl.appendChild(mk('›', meta.hal + 1));
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
