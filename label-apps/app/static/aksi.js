'use strict';

/*
 * Label Aksi (anotasi klip) — GRID + overlay pelabelan.
 *
 * Padanan papan job gambar, untuk KLIP: grid klip dengan thumbnail DIAM (poster
 * /aksi/thumb), filter Belum/Sudah dilabeli + Di/Belum dataset + kelas,
 * multi-select + toolbar borongan (tambah ke dataset). Melabeli dilakukan di
 * OVERLAY "Lihat / label": klip berputar (auto-play + loop) HANYA di sini +
 * tombol kelas + prev/next dalam subset terfilter.
 *
 * Kebenaran tetap di server — tiap tombol memanggil rute yang memeriksa izin
 * (tugas.boleh_labeli per-klip); tampilan hanya menyusul balasannya.
 * send() + toast() global dari app.js.
 */

const D = JSON.parse(document.getElementById('ak-awal').textContent);
const EDS = encodeURIComponent(D.ds);
const EJOB = D.job ? '&job=' + encodeURIComponent(D.job) : '';
const enc = encodeURIComponent;
const el = id => document.getElementById(id);
const PER = 60;

function warnaKelas(nama, i) {
  if (D.warna && D.warna[i]) return D.warna[i];
  let h = 0;
  for (let k = 0; k < nama.length; k++) h = (h * 31 + nama.charCodeAt(k)) % 360;
  return `hsl(${h} 55% 45%)`;
}
const IDX_KELAS = {};
(D.kelas || []).forEach((k, i) => { IDX_KELAS[k] = i; });

let clips = (D.klips || []).map(c => ({ ...c }));
let saring = 'semua';   // semua | belum | sudah (status label)
let dsf = 'semua';      // semua | di | belum (status dataset)
let fkelas = '';        // filter kelas
let hal = 0;
const terpilih = new Set();

const grid = el('ak-grid');
const kosongEl = el('ak-kosong');
const halEl = el('ak-hal');
const selFKelas = el('ak-f-kelas');
const cbSemua = el('ak-semua');
const btnBulkDs = el('ak-bulk-ds');
const nPilihEl = el('ak-npilih');

// -- subset tampil (filter) --------------------------------------------------
function tampil() {
  let v = clips;
  if (saring === 'belum') v = v.filter(c => !c.label);
  else if (saring === 'sudah') v = v.filter(c => c.label);
  if (dsf === 'di') v = v.filter(c => c.di_dataset);
  else if (dsf === 'belum') v = v.filter(c => !c.di_dataset);
  if (fkelas) v = v.filter(c => c.label === fkelas);
  return v;
}

function angka() {
  const n = clips.length;
  const nl = clips.filter(c => c.label).length;
  const nd = clips.filter(c => c.di_dataset).length;
  el('ak-n-total').textContent = n.toLocaleString('id-ID');
  el('ak-n-label').textContent = nl.toLocaleString('id-ID');
  el('ak-n-ds').textContent = nd.toLocaleString('id-ID');
}

function isiFilterKelas() {
  const ada = [...new Set(clips.map(c => c.label).filter(Boolean))].sort();
  const lama = selFKelas.value;
  selFKelas.innerHTML = '<option value="">Semua</option>'
    + ada.map(k => `<option value="${k}">${k}</option>`).join('');
  if (ada.includes(lama)) selFKelas.value = lama;
}

// -- grid --------------------------------------------------------------------
function render() {
  angka();
  const v = tampil();
  grid.innerHTML = '';
  kosongEl.hidden = v.length > 0;
  const potong = v.slice(hal * PER, hal * PER + PER);
  potong.forEach(it => grid.appendChild(kartu(it, v)));
  renderHal(v);
  if (cbSemua) cbSemua.checked = potong.length > 0
    && potong.every(it => terpilih.has(it.rel));
  perbaruiBulk();
}

function kartu(it, v) {
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
  lihat.addEventListener('click', () => bukaLabel(it, v));
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

function renderHal(v) {
  halEl.innerHTML = '';
  const n = Math.ceil(v.length / PER) || 1;
  if (n <= 1) return;
  const mk = (txt, ke) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'chip'; b.textContent = txt;
    b.addEventListener('click', () => {
      hal = ke; render(); window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    return b;
  };
  if (hal > 0) halEl.appendChild(mk('‹', hal - 1));
  halEl.appendChild(document.createTextNode(` ${hal + 1} / ${n} `));
  if (hal < n - 1) halEl.appendChild(mk('›', hal + 1));
}

function perbaruiBulk() {
  if (nPilihEl) nPilihEl.textContent = terpilih.size;
  if (btnBulkDs) btnBulkDs.disabled = terpilih.size === 0;
}

function gantiSaring(baru) { saring = baru; hal = 0; render(); }
function gantiDsf(baru) { dsf = baru; hal = 0; render(); }

document.querySelectorAll('.ak-saring [data-saring]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.ak-saring [data-saring]').forEach(b => b.removeAttribute('data-on'));
    btn.setAttribute('data-on', ''); gantiSaring(btn.dataset.saring);
  });
});
document.querySelectorAll('.ak-dsf [data-dsf]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.ak-dsf [data-dsf]').forEach(b => b.removeAttribute('data-on'));
    btn.setAttribute('data-on', ''); gantiDsf(btn.dataset.dsf);
  });
});
selFKelas.addEventListener('change', () => { fkelas = selFKelas.value; hal = 0; render(); });

// -- pilih semua (halaman ini) + toolbar borongan ---------------------------
if (cbSemua) {
  cbSemua.addEventListener('change', () => {
    const potong = tampil().slice(hal * PER, hal * PER + PER);
    potong.forEach(it => { if (cbSemua.checked) terpilih.add(it.rel); else terpilih.delete(it.rel); });
    render();
  });
}
if (btnBulkDs) {
  btnBulkDs.addEventListener('click', () => kirimDataset([...terpilih]));
}
const btnDsSemua = el('ak-ds-semua');
if (btnDsSemua) {
  btnDsSemua.addEventListener('click', () =>
    kirimDataset(clips.filter(c => c.label && !c.di_dataset).map(c => c.rel)));
}

async function kirimDataset(rels) {
  rels = [...new Set(rels)];
  if (!rels.length) { toast('Tidak ada klip berlabel untuk dimasukkan'); return; }
  const r = await send(`/api/aksi/dataset?ds=${EDS}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ klip: rels }),
  });
  if (!r || !r.ok) { toast((r && r.error) || 'gagal memasukkan'); return; }
  toast(`${r.ditambah || 0} klip masuk dataset`);
  terpilih.clear();
  await segarkan();
}

// Muat ulang subset dari server (di_dataset & angka benar setelah operasi).
async function segarkan() {
  const r = await send(`/api/aksi/keterangan?ds=${EDS}${EJOB}`);
  if (!r || !r.ok) return;
  clips = (r.klips || []).map(c => ({ ...c }));
  isiFilterKelas();
  render();
}

// ============================================================
// OVERLAY LABEL: pemutar + tombol kelas + prev/next. Auto-play HANYA di sini.
// ============================================================
const overlay = el('ak-lbl');
const lvid = el('ak-lbl-video');
const lkelas = el('ak-lbl-kelas');
let vlist = [];     // snapshot subset saat overlay dibuka
let vpos = 0;

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

function bukaLabel(it, v) {
  vlist = v.slice();
  vpos = vlist.findIndex(c => c.rel === it.rel);
  if (vpos < 0) vpos = 0;
  overlay.hidden = false;
  renderLabel();
}
function tutupLabel() {
  lvid.pause(); lvid.removeAttribute('src'); lvid.load();
  overlay.hidden = true;
  render();          // segarkan grid (badge + angka) sekembalinya
}
function renderLabel() {
  const c = vlist[vpos];
  if (!c) { tutupLabel(); return; }
  const url = `/klip?ds=${EDS}&name=${enc(c.rel)}`;
  if (lvid.getAttribute('src') !== url) {
    lvid.setAttribute('src', url); lvid.load();
    lvid.play().catch(() => {});
  }
  el('ak-lbl-pos').textContent = `${vpos + 1} / ${vlist.length}`;
  el('ak-lbl-nama').textContent = c.rel.split('/').pop();
  el('ak-lbl-ket').textContent = c.label
    ? (c.di_dataset ? `${c.label} · di dataset` : c.label) : '(belum dilabeli)';
  tombolKelas.forEach((b, nm) => {
    if (nm === c.label) b.setAttribute('data-on', ''); else b.removeAttribute('data-on');
  });
}
async function setLabelKini(cls) {
  const c = vlist[vpos];
  if (!c) return;
  const r = await send(
    `/api/aksi/label?ds=${EDS}&klip=${enc(c.rel)}&label=${enc(cls)}`,
    { method: 'POST' });
  if (!r || !r.ok) { toast((r && r.error) || 'gagal menyimpan label'); return; }
  c.label = r.label;              // objek sama dengan di `clips` (ref)
  angka();
  // Setelah memberi label (bukan menghapus), maju ke klip berikut.
  if (cls && vpos < vlist.length - 1) vpos++;
  renderLabel();
}
function geserLabel(d) { vpos = Math.min(Math.max(vpos + d, 0), vlist.length - 1); renderLabel(); }

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

isiFilterKelas();
render();
