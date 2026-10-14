'use strict';

/*
 * Label Aksi (anotasi klip) — GRID + overlay pelabelan, DIPAGINASI SERVER.
 *
 * Padanan papan job gambar, untuk KLIP. Grid klip thumbnail DIAM (poster
 * /aksi/thumb), filter Belum/Sudah dilabeli + Di/Belum dataset + kelas,
 * multi-select + toolbar borongan. Melabeli di OVERLAY "Lihat / label": klip
 * berputar (auto-play + loop) HANYA di sini + tombol kelas + prev/next.
 *
 * Paginasi & penyaringan DI SERVER (/api/aksi/keterangan): peramban hanya
 * memegang SATU halaman, bukan seluruh klip — projek klip bisa puluhan ribu
 * ([[halaman-besar-wajib-paginasi]]). Angka ringkas & hitung-kelas datang dari
 * server (dihitung atas seluruh subset), jadi benar di halaman berapa pun.
 * Prev/next overlay melintasi batas halaman dengan memuat halaman tetangga.
 *
 * Kebenaran tetap di server — tiap tombol memanggil rute yang memeriksa izin
 * (tugas.boleh_labeli per-klip). send() + toast() global dari app.js.
 */

const D = JSON.parse(document.getElementById('ak-awal').textContent);
const EDS = encodeURIComponent(D.ds);
const JOB = D.job || '';
const enc = encodeURIComponent;
const el = id => document.getElementById(id);

function warnaKelas(nama, i) {
  if (D.warna && D.warna[i]) return D.warna[i];
  let h = 0;
  for (let k = 0; k < nama.length; k++) h = (h * 31 + nama.charCodeAt(k)) % 360;
  return `hsl(${h} 55% 45%)`;
}
const IDX_KELAS = {};
(D.kelas || []).forEach((k, i) => { IDX_KELAS[k] = i; });

// -- keadaan halaman (satu halaman klip + meta dari server) ------------------
let page = (D.klips || []).map(c => ({ ...c }));   // klip halaman kini
let meta = {                                        // ringkasan dari server
  hal: D.hal || 1, n_hal: D.n_hal || 1, per: D.per || 60,
  mulai: D.mulai || 0,
  n_tampil: D.n_tampil || 0, n_total: D.n_total || 0,
  n_label: D.n_label || 0, n_dataset: D.n_dataset || 0,
  kelas_hitung: D.kelas_hitung || {}, batch_hitung: D.batch_hitung || {},
};
let saring = 'semua';   // semua | belum | sudah
let dsf = 'semua';      // semua | di | belum
let fkelas = '';        // filter kelas (satu kelas)
let fbatch = '';        // filter unggahan/batch
let cari = '';          // cari nama berkas
let urut = 'nama';      // nama | nama-balik | batch
let perPage = D.per || 60;
const terpilih = new Set();   // rel terpilih (lintas halaman)
let memuat = false;

const grid = el('ak-grid');
const kosongEl = el('ak-kosong');
const halEl = el('ak-hal');
const selFKelas = el('ak-f-kelas');
const cbSemua = el('ak-semua');
const btnBulkDs = el('ak-bulk-ds');
const nPilihEl = el('ak-npilih');

function urlKet(hal) {
  let u = `/api/aksi/keterangan?ds=${EDS}&hal=${hal}&per=${perPage}`
    + `&saring=${saring}&dsf=${dsf}&urut=${urut}`;
  if (JOB) u += '&job=' + enc(JOB);
  if (fkelas) u += '&kelas=' + enc(fkelas);
  if (fbatch) u += '&batch=' + enc(fbatch);
  if (cari) u += '&q=' + enc(cari);
  return u;
}

// Muat satu halaman dari server (saringan kini + `hal`). Mengembalikan true
// kalau berhasil, supaya pemanggil async (overlay lintas-halaman) bisa menyusul.
async function muat(hal) {
  if (memuat) return false;
  memuat = true;
  const r = await send(urlKet(Math.max(1, hal)));
  memuat = false;
  if (!r || !r.ok) { toast((r && r.error) || 'gagal memuat'); return false; }
  page = (r.klips || []).map(c => ({ ...c }));
  meta = {
    hal: r.hal, n_hal: r.n_hal, per: r.per, mulai: r.mulai || 0,
    n_tampil: r.n_tampil, n_total: r.n_total, n_label: r.n_label,
    n_dataset: r.n_dataset, kelas_hitung: r.kelas_hitung || {},
    batch_hitung: r.batch_hitung || {},
  };
  perPage = r.per;
  render();
  return true;
}

// -- angka + dropdown kelas + grid ------------------------------------------
function angka() {
  el('ak-n-total').textContent = meta.n_total.toLocaleString('id-ID');
  el('ak-n-label').textContent = meta.n_label.toLocaleString('id-ID');
  el('ak-n-ds').textContent = meta.n_dataset.toLocaleString('id-ID');
}

function isiFilterKelas() {
  const ada = Object.keys(meta.kelas_hitung).sort();
  const lama = selFKelas.value;
  selFKelas.innerHTML = '<option value="">Semua</option>'
    + ada.map(k => `<option value="${k}">${k} (${meta.kelas_hitung[k]})</option>`).join('');
  selFKelas.value = ada.includes(lama) ? lama : (fkelas || '');
}

function render() {
  angka();
  isiFilterKelas();
  isiFilterBatch();
  aktifkanBersih();
  grid.innerHTML = '';
  kosongEl.hidden = page.length > 0;
  page.forEach(it => grid.appendChild(kartu(it)));
  renderHal();
  if (cbSemua) cbSemua.checked = page.length > 0
    && page.every(it => terpilih.has(it.rel));
  perbaruiBulk();
}

// Dropdown unggahan/batch: hanya tampil kalau klip tersebar di >1 unggahan.
function isiFilterBatch() {
  const wrap = el('ak-fbatch-wrap'), sel = el('ak-f-batch');
  if (!wrap || !sel) return;
  const ada = Object.keys(meta.batch_hitung || {}).sort();
  wrap.hidden = ada.length < 2 && !fbatch;
  const lama = sel.value;
  sel.innerHTML = '<option value="">Semua unggahan</option>'
    + ada.map(b => `<option value="${b}">${b} (${meta.batch_hitung[b]})</option>`).join('');
  sel.value = ada.includes(lama) ? lama : (fbatch || '');
}

// Chip "Bersihkan saringan" muncul + menghitung saringan aktif.
function aktifkanBersih() {
  const chip = el('ak-bersih');
  if (!chip) return;
  const n = (saring !== 'semua') + (dsf !== 'semua') + (fkelas ? 1 : 0)
    + (fbatch ? 1 : 0) + (cari ? 1 : 0);
  chip.hidden = n === 0;
  chip.innerHTML = `&times; Bersihkan saringan${n ? ' ' + n : ''}`;
}

function kartu(it) {
  const li = document.createElement('li');
  li.className = 'akg-kartu';
  const img = document.createElement('img');
  img.className = 'akg-thumb'; img.loading = 'lazy'; img.alt = it.label || '';
  img.src = `/aksi/thumb?ds=${EDS}&rel=${enc(it.rel)}&s=240`;
  img.addEventListener('error', () => img.classList.add('akg-thumb-x'));
  li.appendChild(img);

  const lab = document.createElement('span');
  if (it.label) {
    lab.className = 'akg-label';
    lab.style.background = warnaKelas(it.label, IDX_KELAS[it.label] ?? 0);
    lab.textContent = it.label;
  } else {
    lab.className = 'akg-label akg-label-x';
    lab.textContent = 'belum';
  }
  li.appendChild(lab);
  if (it.di_dataset) {
    const d = document.createElement('span');
    d.className = 'akg-ds'; d.textContent = 'dataset'; li.appendChild(d);
  }

  const bar = document.createElement('div');
  bar.className = 'akg-kartu-bar';
  const lihat = document.createElement('button');
  lihat.type = 'button'; lihat.className = 'chip akg-lihat';
  lihat.textContent = 'Lihat / label';
  lihat.addEventListener('click', () => bukaLabel(it));
  bar.appendChild(lihat);
  if (cbSemua) {
    const cb = document.createElement('input');
    cb.type = 'checkbox'; cb.className = 'akg-cb';
    cb.checked = terpilih.has(it.rel);
    cb.addEventListener('change', () => {
      if (cb.checked) terpilih.add(it.rel); else terpilih.delete(it.rel);
      perbaruiBulk();
    });
    bar.appendChild(cb);
  }
  li.appendChild(bar);
  return li;
}

// Nomor halaman berjendela: 1, sekitar halaman kini (±2), dan terakhir, dengan
// "…" di selanya — sama polanya dengan pager grid gambar.
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

  // Per halaman
  const pp = document.createElement('div');
  pp.className = 'hal-pp'; pp.appendChild(document.createTextNode('Per halaman '));
  const selPer = document.createElement('select');
  [24, 60, 120, 200].forEach(n => {
    const o = document.createElement('option'); o.value = n; o.textContent = n;
    if (n === meta.per) o.selected = true; selPer.appendChild(o);
  });
  selPer.addEventListener('change', () => { perPage = +selPer.value; muat(1); });
  pp.appendChild(selPer); halEl.appendChild(pp);

  // Rentang "a–b dari N"
  const info = document.createElement('span');
  info.className = 'hal-info';
  const a = meta.mulai + 1, b = meta.mulai + page.length;
  info.innerHTML = `${a.toLocaleString('id-ID')}&ndash;${b.toLocaleString('id-ID')} dari `
    + `${meta.n_tampil.toLocaleString('id-ID')} <em>klip</em>`;
  halEl.appendChild(info);

  // Navigasi bernomor
  const nav = document.createElement('div'); nav.className = 'hal-nav';
  const arrow = (txt, ke, mati) => {
    const x = document.createElement('button');
    x.type = 'button'; x.className = 'hal-n'; x.textContent = txt;
    if (mati) x.setAttribute('data-mati', '');
    else x.addEventListener('click', ke);
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

  // Lompat ke halaman (saat banyak halaman)
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

function perbaruiBulk() {
  if (nPilihEl) nPilihEl.textContent = terpilih.size;
  if (btnBulkDs) btnBulkDs.disabled = terpilih.size === 0;
}

// -- saringan (muat ulang halaman 1) ----------------------------------------
document.querySelectorAll('.ak-saring [data-saring]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.ak-saring [data-saring]').forEach(b => b.removeAttribute('data-on'));
    btn.setAttribute('data-on', ''); saring = btn.dataset.saring; muat(1);
  });
});
document.querySelectorAll('.ak-dsf [data-dsf]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.ak-dsf [data-dsf]').forEach(b => b.removeAttribute('data-on'));
    btn.setAttribute('data-on', ''); dsf = btn.dataset.dsf; muat(1);
  });
});
selFKelas.addEventListener('change', () => { fkelas = selFKelas.value; muat(1); });

const selFBatch = el('ak-f-batch');
if (selFBatch) selFBatch.addEventListener('change', () => { fbatch = selFBatch.value; muat(1); });
const selUrut = el('ak-urut');
if (selUrut) selUrut.addEventListener('change', () => { urut = selUrut.value; muat(1); });
const cariForm = el('ak-cari-form');
if (cariForm) cariForm.addEventListener('submit', (e) => {
  e.preventDefault();
  cari = (el('ak-cari').value || '').trim();
  muat(1);
});
const btnBersih = el('ak-bersih');
if (btnBersih) btnBersih.addEventListener('click', () => {
  saring = 'semua'; dsf = 'semua'; fkelas = ''; fbatch = ''; cari = ''; urut = 'nama';
  document.querySelectorAll('.ak-saring [data-saring]').forEach(
    b => b.toggleAttribute('data-on', b.dataset.saring === 'semua'));
  document.querySelectorAll('.ak-dsf [data-dsf]').forEach(
    b => b.toggleAttribute('data-on', b.dataset.dsf === 'semua'));
  selFKelas.value = '';
  if (selFBatch) selFBatch.value = '';
  if (selUrut) selUrut.value = 'nama';
  const ci = el('ak-cari'); if (ci) ci.value = '';
  muat(1);
});

// -- pilih semua (halaman ini) + toolbar borongan ---------------------------
if (cbSemua) {
  cbSemua.addEventListener('change', () => {
    page.forEach(it => { if (cbSemua.checked) terpilih.add(it.rel); else terpilih.delete(it.rel); });
    render();
  });
}
if (btnBulkDs) {
  btnBulkDs.addEventListener('click', () => kirimDataset([...terpilih]));
}
const btnDsSemua = el('ak-ds-semua');
if (btnDsSemua) {
  btnDsSemua.addEventListener('click', async () => {
    // Daftar lengkap ada di server (peramban dipaginasi): kirim bendera, bukan rel.
    const r = await send(`/api/aksi/dataset?ds=${EDS}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ semua_berlabel: true, job: JOB }),
    });
    if (!r || !r.ok) { toast((r && r.error) || 'gagal memasukkan'); return; }
    toast(`${r.ditambah || 0} klip masuk dataset`);
    terpilih.clear();
    muat(meta.hal);
  });
}

async function kirimDataset(rels) {
  rels = [...new Set(rels)];
  if (!rels.length) { toast('Tidak ada klip untuk dimasukkan'); return; }
  const r = await send(`/api/aksi/dataset?ds=${EDS}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ klip: rels }),
  });
  if (!r || !r.ok) { toast((r && r.error) || 'gagal memasukkan'); return; }
  toast(`${r.ditambah || 0} klip masuk dataset`);
  terpilih.clear();
  muat(meta.hal);
}

// ============================================================
// OVERLAY LABEL: pemutar + tombol kelas + prev/next. Auto-play HANYA di sini.
// Prev/next menyusuri halaman kini; di batas halaman memuat halaman tetangga.
// ============================================================
const overlay = el('ak-lbl');
const lvid = el('ak-lbl-video');
const lkelas = el('ak-lbl-kelas');
let vpos = 0;           // indeks dalam `page` (halaman kini)

const tombolKelas = new Map();
(D.kelas || []).forEach((nama, i) => {
  const b = document.createElement('button');
  b.type = 'button'; b.className = 'ak-kls';
  b.dataset.cls = nama;
  b.style.setProperty('--w', warnaKelas(nama, i));
  const hot = i < 9 ? String(i + 1) : (i === 9 ? '0' : '');
  b.setAttribute('aria-label', hot ? `${nama} (pintasan ${hot})` : nama);
  b.innerHTML = (hot ? `<kbd class="ak-hot">${hot}</kbd>` : '')
    + '<span class="ak-kls-nama"></span>';
  b.querySelector('.ak-kls-nama').textContent =
    nama + (nama === D.negatif ? ' (negatif)' : '');
  b.addEventListener('click', () => setLabelKini(nama));
  lkelas.appendChild(b);
  tombolKelas.set(nama, b);
});

function bukaLabel(it) {
  vpos = page.findIndex(c => c.rel === it.rel);
  if (vpos < 0) vpos = 0;
  overlay.hidden = false;
  renderLabel();
}
function tutupLabel() {
  lvid.pause(); lvid.removeAttribute('src'); lvid.load();
  overlay.hidden = true;
  muat(meta.hal);          // rekonsiliasi grid + angka otoritatif dari server
}
function renderLabel() {
  const c = page[vpos];
  if (!c) { tutupLabel(); return; }
  const url = `/klip?ds=${EDS}&name=${enc(c.rel)}`;
  if (lvid.getAttribute('src') !== url) {
    lvid.setAttribute('src', url); lvid.load();
    lvid.play().catch(() => {});
  }
  const absolut = (meta.hal - 1) * meta.per + vpos + 1;
  el('ak-lbl-pos').textContent = `${absolut} / ${meta.n_tampil}`;
  el('ak-lbl-nama').textContent = c.rel.split('/').pop();
  el('ak-lbl-ket').textContent = c.label
    ? (c.di_dataset ? `${c.label} · di dataset` : c.label) : '(belum dilabeli)';
  tombolKelas.forEach((b, nm) => {
    if (nm === c.label) b.setAttribute('data-on', ''); else b.removeAttribute('data-on');
  });
}
async function setLabelKini(cls) {
  const c = page[vpos];
  if (!c) return;
  const r = await send(
    `/api/aksi/label?ds=${EDS}&klip=${enc(c.rel)}&label=${enc(cls)}`,
    { method: 'POST' });
  if (!r || !r.ok) { toast((r && r.error) || 'gagal menyimpan label'); return; }
  // Angka ringkas disesuaikan optimistik (otoritatif saat overlay ditutup).
  const adaDulu = !!c.label;
  if (r.label && !adaDulu) meta.n_label++;
  else if (!r.label && adaDulu) meta.n_label--;
  c.label = r.label;
  angka();
  renderLabel();            // segarkan sorotan kelas + keterangan klip kini
  // Setelah memberi label (bukan menghapus), maju ke klip berikut (lintas
  // halaman bila di ujung). Di ujung mutlak, tetap di klip ini (sudah disegarkan).
  if (cls) await geserLabel(1);
}
// Geser dengan kesadaran batas halaman: di ujung, muat halaman tetangga.
async function geserLabel(d) {
  const tujuan = vpos + d;
  if (tujuan >= 0 && tujuan < page.length) { vpos = tujuan; renderLabel(); return; }
  if (d > 0 && meta.hal < meta.n_hal) {
    if (await muat(meta.hal + 1)) { vpos = 0; renderLabel(); }
  } else if (d < 0 && meta.hal > 1) {
    if (await muat(meta.hal - 1)) { vpos = page.length - 1; renderLabel(); }
  }
  // Di ujung mutlak: diam (sudah di klip pertama/terakhir subset).
}

el('ak-lbl-tutup').addEventListener('click', tutupLabel);
el('ak-lbl-prev').addEventListener('click', () => geserLabel(-1));
el('ak-lbl-next').addEventListener('click', () => geserLabel(1));
el('ak-lbl-hapus').addEventListener('click', () => setLabelKini(''));
overlay.addEventListener('click', e => { if (e.target === overlay) tutupLabel(); });

// -- papan tik (hanya saat overlay label terbuka) ---------------------------
document.addEventListener('keydown', e => {
  if (overlay.hidden) return;
  const t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA'
    || t.tagName === 'SELECT' || t.isContentEditable)) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === 'Escape') { tutupLabel(); e.preventDefault(); return; }
  if (e.key === 'ArrowLeft') { geserLabel(-1); e.preventDefault(); return; }
  if (e.key === 'ArrowRight') { geserLabel(1); e.preventDefault(); return; }
  if (e.key === 'Backspace') { setLabelKini(''); e.preventDefault(); return; }
  if (/^[0-9]$/.test(e.key)) {
    const idx = e.key === '0' ? 9 : (parseInt(e.key, 10) - 1);
    const nama = (D.kelas || [])[idx];
    if (nama) { setLabelKini(nama); e.preventDefault(); }
  }
});

// -- editor kelas aksi (pemilik) ---------------------------------------------
(function editorKelas() {
  const buka = el('ak-set-kelas');
  const dlg = el('dlg-aksi');
  if (!buka || !dlg) return;
  const taKelas = el('aksi-kelas');
  const inNeg = el('aksi-negatif');
  const simpan = el('aksi-kelas-simpan');
  const tampilDlg = v => {
    dlg.hidden = !v;
    if (v) {
      taKelas.value = (D.kelas || []).join('\n');
      inNeg.value = D.negatif || '';
      setTimeout(() => taKelas.focus(), 30);
    }
  };
  buka.addEventListener('click', () => tampilDlg(true));
  el('dlg-aksi-tutup').addEventListener('click', () => tampilDlg(false));
  el('aksi-kelas-batal').addEventListener('click', () => tampilDlg(false));
  dlg.addEventListener('click', e => { if (e.target === dlg) tampilDlg(false); });
  simpan.addEventListener('click', async () => {
    const kelas = taKelas.value.split('\n').map(s => s.trim()).filter(Boolean);
    if (!kelas.length) { toast('Isi minimal satu kelas aksi'); return; }
    const negatif = (inNeg.value || '').trim();
    if (negatif && !kelas.includes(negatif)) {
      toast('Kelas negatif harus salah satu kelas di daftar'); return;
    }
    simpan.disabled = true;
    const r = await send(`/api/tugas/aksi?ds=${EDS}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ aksi: { kelas, negatif } }),
    });
    simpan.disabled = false;
    if (!r || !r.ok) { toast((r && r.error) || 'gagal menyimpan kelas'); return; }
    location.reload();
  });
})();

render();
