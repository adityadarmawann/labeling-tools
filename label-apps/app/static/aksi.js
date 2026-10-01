'use strict';

/*
 * Pelabelan klip (classifier aksi video).
 *
 * Padanan label.js untuk klip: alih-alih canvas, sebuah <video> memutar klip
 * ~1 detik berulang, dan pelabel menekan angka kelas. Panah kiri-kanan
 * menyusuri subset penugasan ini; saring belum/sudah dikerjakan di peramban
 * atas subset yang sudah dimuat (sequential labeling, bukan grid besar).
 *
 * Resumable: label tiap klip datang dari server (D.klips[i].label) dan
 * ditampilkan. Aman banyak orang: penjaga sungguhannya di server
 * (tugas.boleh_labeli per-klip); yang di sini hanya UX.
 *
 * Koordinat kebenaran tetap di server — tombol apa pun memanggil rute yang
 * memeriksa izin; tampilan hanya menyusul balasannya.
 */

const D = JSON.parse(document.getElementById('ak-awal').textContent);
const EDS = encodeURIComponent(D.ds);
const EJOB = D.job ? '&job=' + encodeURIComponent(D.job) : '';
const el = id => document.getElementById(id);
const enc = encodeURIComponent;

// Keadaan. `clips` adalah SELURUH subset pelabel ini (urut); `saring` memilih
// yang tampil; `pos` indeks ke dalam daftar yang tampil.
let clips = (D.klips || []).map(c => ({ ...c }));
let saring = (document.querySelector('.ak-saring [data-on]') || {}).dataset
  ? document.querySelector('.ak-saring [data-on]').dataset.saring : 'semua';
let pos = 0;

const video = el('ak-video');

// Warna kelas: pakai warna projek kalau ada, kalau tidak turunkan HSL stabil
// dari nama — supaya tombol yang sama selalu berwarna sama antar muat.
function warnaKelas(nama, i) {
  if (D.warna && D.warna[i]) return D.warna[i];
  let h = 0;
  for (let k = 0; k < nama.length; k++) h = (h * 31 + nama.charCodeAt(k)) % 360;
  return `hsl(${h} 55% 45%)`;
}

// -- tombol kelas (sekali) ---------------------------------------------------
const wadahKelas = el('ak-kelas');
const tombolKelas = new Map();     // nama kelas -> <button>
(D.kelas || []).forEach((nama, i) => {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'ak-kls';
  b.dataset.cls = nama;
  b.style.setProperty('--w', warnaKelas(nama, i));
  // Pintasan angka untuk 10 kelas pertama: 1..9 lalu 0 (seperti review tool).
  const hot = i < 9 ? String(i + 1) : (i === 9 ? '0' : '');
  b.setAttribute('aria-label', hot ? `${nama} (pintasan ${hot})` : nama);
  b.innerHTML = (hot ? `<kbd class="ak-hot">${hot}</kbd>` : '') +
    `<span class="ak-kls-nama"></span>`;
  b.querySelector('.ak-kls-nama').textContent =
    nama + (nama === D.negatif ? ' (negatif)' : '');
  b.addEventListener('click', () => setLabel(nama));
  wadahKelas.appendChild(b);
  tombolKelas.set(nama, b);
});

// -- yang tampil & klip kini -------------------------------------------------
function tampil() {
  if (saring === 'belum') return clips.filter(c => !c.label);
  if (saring === 'sudah') return clips.filter(c => c.label);
  return clips;
}
function kini() {
  const v = tampil();
  if (!v.length) return null;
  pos = Math.min(Math.max(pos, 0), v.length - 1);
  return v[pos];
}

// -- render ------------------------------------------------------------------
function render() {
  const v = tampil();
  const c = kini();
  // Angka & bilah kemajuan dihitung atas SELURUH subset, bukan yang tampil.
  const nTotal = clips.length;
  const nLabel = clips.filter(x => x.label).length;
  const nDs = clips.filter(x => x.di_dataset).length;
  el('ak-n-total').textContent = nTotal;
  el('ak-n-label').textContent = nLabel;
  el('ak-n-belum').textContent = nTotal - nLabel;
  el('ak-n-ds').textContent = nDs;
  el('ak-bar-ok').style.flexGrow = nLabel || 0;
  el('ak-bar-stop').style.flexGrow = (nTotal - nLabel) || 0;

  const kosong = el('ak-kosong');
  if (!c) {
    video.removeAttribute('src');
    video.load();
    video.hidden = true;
    kosong.hidden = false;
    el('ak-posisi').textContent = `0 / 0`;
    el('ak-nama').textContent = '';
    el('ak-kini').textContent = '';
    tombolKelas.forEach(b => { b.removeAttribute('data-on'); b.setAttribute('aria-pressed', 'false'); });
    return;
  }
  video.hidden = false;
  kosong.hidden = true;
  // name di-encode penuh: FastAPI men-decode %2F kembali jadi '/', jadi
  // "klip/b1/x.mp4" sampai utuh ke rute yang menyelesaikannya aman.
  const url = `/klip?ds=${EDS}&name=${enc(c.rel)}`;
  if (video.getAttribute('src') !== url) {
    video.setAttribute('src', url);
    video.load();
    video.play().catch(() => { /* autoplay bisa ditolak; controls tetap ada */ });
  }
  el('ak-posisi').textContent = `${pos + 1} / ${v.length}`;
  const nama = c.rel.split('/').pop();
  const el_nama = el('ak-nama');
  el_nama.textContent = nama;
  el_nama.title = c.rel + (c.batch ? `  ·  batch ${c.batch}` : '');
  el('ak-kini').textContent = c.label
    ? (c.di_dataset ? `${c.label} · di dataset` : c.label)
    : '(belum dilabeli)';
  tombolKelas.forEach((b, nm) => {
    const on = nm === c.label;
    if (on) b.setAttribute('data-on', ''); else b.removeAttribute('data-on');
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

// -- tulis label -------------------------------------------------------------
async function setLabel(cls) {
  const c = kini();
  if (!c) return;
  const r = await send(
    `/api/aksi/label?ds=${EDS}&klip=${enc(c.rel)}&label=${enc(cls)}`,
    { method: 'POST' });
  if (!r.ok) { toast(r.error || 'gagal menyimpan label'); return; }
  c.label = r.label;
  // Di saringan "belum", klip yang baru dilabeli keluar dari yang tampil; pos
  // dibiarkan supaya klip berikutnya yang belum maju sendiri. Di saringan lain,
  // maju ke klip berikut (tekan angka = labeli lalu lanjut, seperti review tool).
  if (saring !== 'belum') pos++;
  render();
}
async function hapus() {
  const c = kini();
  if (!c || !c.label) { render(); return; }
  await setLabel('');
}
function lewati() { pos++; render(); }
function ulang() { if (video.src) { video.currentTime = 0; video.play().catch(() => {}); } }
function geser(d) { pos += d; render(); }

// -- tambah berlabel ke dataset ---------------------------------------------
el('ak-ke-dataset').addEventListener('click', async () => {
  const minta = clips.filter(c => c.label && !c.di_dataset).map(c => c.rel);
  if (!minta.length) { toast('Tidak ada klip berlabel baru untuk dimasukkan'); return; }
  const r = await send(`/api/aksi/dataset?ds=${EDS}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ klip: minta }),
  });
  if (!r.ok) { toast(r.error || 'gagal memasukkan'); return; }
  toast(`${r.ditambah || 0} klip masuk dataset`);
  await segarkan();
});

// Muat ulang subset dari server (dipakai setelah masuk-dataset supaya penanda
// di_dataset & angka benar), mempertahankan klip yang sedang dibuka.
async function segarkan() {
  const relKini = (kini() || {}).rel;
  const r = await send(`/api/aksi/keterangan?ds=${EDS}${EJOB}`);
  if (!r.ok) return;
  clips = (r.klips || []).map(c => ({ ...c }));
  const v = tampil();
  const i = v.findIndex(c => c.rel === relKini);
  if (i >= 0) pos = i;
  render();
}

// -- saring belum/sudah ------------------------------------------------------
document.querySelectorAll('.ak-saring [data-saring]').forEach(btn => {
  btn.addEventListener('click', () => {
    const relKini = (kini() || {}).rel;
    saring = btn.dataset.saring;
    document.querySelectorAll('.ak-saring [data-saring]').forEach(
      b => b.removeAttribute('data-on'));
    btn.setAttribute('data-on', '');
    // Pertahankan klip yang sedang dibuka kalau masih tampil; kalau tidak,
    // mulai dari awal daftar baru.
    const v = tampil();
    const i = v.findIndex(c => c.rel === relKini);
    pos = i >= 0 ? i : 0;
    render();
  });
});

// -- tombol navigasi ---------------------------------------------------------
el('ak-prev').addEventListener('click', () => geser(-1));
el('ak-next').addEventListener('click', () => geser(1));
el('ak-replay').addEventListener('click', ulang);
el('ak-skip').addEventListener('click', lewati);
el('ak-hapus').addEventListener('click', hapus);

// -- papan tik ---------------------------------------------------------------
document.addEventListener('keydown', e => {
  const t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA'
    || t.tagName === 'SELECT' || t.isContentEditable)) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === 'ArrowLeft') { geser(-1); e.preventDefault(); return; }
  if (e.key === 'ArrowRight') { geser(1); e.preventDefault(); return; }
  if (e.key === 'r' || e.key === 'R') { ulang(); e.preventDefault(); return; }
  if (e.key === 's' || e.key === 'S') { lewati(); e.preventDefault(); return; }
  if (e.key === 'Backspace') { hapus(); e.preventDefault(); return; }
  // Angka: 1..9 -> kelas 0..8, 0 -> kelas 10 (indeks 9).
  if (/^[0-9]$/.test(e.key)) {
    const idx = e.key === '0' ? 9 : (parseInt(e.key, 10) - 1);
    const nama = (D.kelas || [])[idx];
    if (nama) { setLabel(nama); e.preventDefault(); }
  }
});

render();
