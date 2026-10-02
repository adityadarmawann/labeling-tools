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

// -- daftar versi terlatih (unduhan arsip klip) ------------------------------
// Padanan tombol ekspor gambar, untuk klip: tiap versi beku .versi/vN/ jadi
// satu tautan unduh arsip folder-per-kelas. Tampil untuk semua anggota (bukan
// cuma pemilik); dirender dari data awal dan bertambah saat sebuah build selesai.
const versiList = el('ak-versi-list');
const versiJudul = el('ak-versi-judul');
let versiData = (D.versi || []).map(v => ({ ...v }));
function renderVersi() {
  if (!versiList) return;
  versiList.innerHTML = '';
  if (versiJudul) versiJudul.hidden = !versiData.length;
  versiData.forEach(v => {
    const li = document.createElement('li');
    const a = document.createElement('a');
    a.className = 'chip ak-unduh';
    // Unduhan <a href> biasa: ini aplikasi (bukan artifact sandbox), jadi
    // lampiran langsung sudah benar. name tak perlu di-encode (angka nomor).
    a.href = `/api/aksi/versi/unduh?ds=${EDS}&nomor=${v.nomor}`;
    a.textContent = `Unduh v${v.nomor}`;
    a.title = `Unduh arsip klip versi v${v.nomor} (folder per kelas, train/valid)`;
    li.appendChild(a);
    const j = v.jumlah || {};
    if (v.n || j.train != null || j.valid != null) {
      const s = document.createElement('span');
      s.className = 'ak-versi-ket';
      const sp = (j.train != null || j.valid != null)
        ? ` · train ${j.train || 0} / valid ${j.valid || 0}` : '';
      s.textContent = ` ${v.n || 0} klip · ${v.kelas || 0} kelas${sp}`;
      li.appendChild(s);
    }
    versiList.appendChild(li);
  });
}
renderVersi();

// -- buat versi aksi (pemilik saja) -----------------------------------------
// Membekukan klip berlabel jadi dataset aug+balanced di .versi/vN/. POST hanya
// memulai; kemajuan di-poll (build bisa lama). Tombolnya cuma ada untuk pemilik
// (dirender bersyarat di aksi.html), jadi elemennya bisa saja tak ada.
const btnVersi = el('ak-buat-versi');
if (btnVersi) {
  const btnBatal = el('ak-batal-versi');
  const majuEl = el('ak-versi-maju');
  let poll = null;

  function tampilMaju(k) {
    majuEl.hidden = false;
    if (k.galat) { majuEl.textContent = 'Gagal: ' + k.galat; return; }
    if (k.batal) { majuEl.textContent = 'Dibatalkan.'; return; }
    if (k.selesai) { majuEl.textContent = `Versi v${k.nomor || ''} selesai.`; return; }
    const p = (k.persen != null) ? ` ${k.persen}%` : '';
    majuEl.textContent = (k.fase_nama || 'Menyiapkan') + p;
  }

  function berhenti() {
    if (poll) { clearInterval(poll); poll = null; }
    btnVersi.disabled = false;
    btnBatal.hidden = true;
  }

  async function pantau() {
    const k = await send(`/api/aksi/versi/kemajuan?ds=${EDS}`);
    if (!k || !k.ok) return;
    tampilMaju(k);
    if (k.selesai || k.batal || k.galat) {
      berhenti();
      if (k.selesai) {
        toast(`Versi v${k.nomor || ''} dibuat`);
        // Versi baru langsung bisa diunduh tanpa muat ulang halaman.
        if (k.nomor && !versiData.some(v => v.nomor === k.nomor)) {
          const rk = k.ringkas || {};
          versiData.unshift({ nomor: k.nomor, n: rk.n || 0,
            kelas: rk.kelas || 0, jumlah: rk.jumlah || {} });
          renderVersi();
        }
      }
    }
  }

  btnVersi.addEventListener('click', async () => {
    btnVersi.disabled = true;
    btnBatal.hidden = false;
    majuEl.hidden = false;
    majuEl.textContent = 'Memulai...';
    const r = await send(`/api/aksi/versi/mulai?ds=${EDS}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ resep: {} }),
    });
    if (!r.ok) { toast(r.error || 'gagal memulai'); majuEl.textContent = r.error || ''; berhenti(); return; }
    poll = setInterval(pantau, 1000);
    pantau();
  });

  btnBatal.addEventListener('click', async () => {
    await send(`/api/aksi/versi/batal?ds=${EDS}`, { method: 'POST' });
    btnBatal.hidden = true;
  });
}

// -- latih model (Langkah 9, pemilik/Editor) --------------------------------
// Pemilih backend hanya menawarkan yang pustakanya terpasang (siap_latih_aksi).
// POST hanya memulai; kemajuan + daftar run di-poll dari server (status dibaca
// dari disk, jadi bertahan melewati muat ulang / restart server).
const trWrap = el('ak-latih');
if (trWrap) {
  const selBackend = el('ak-tr-backend');
  const selVersi = el('ak-tr-versi');
  const inEpochs = el('ak-tr-epochs');
  const inBatch = el('ak-tr-batch');
  const btnMulai = el('ak-tr-mulai');
  const majuEl = el('ak-tr-maju');
  const ketEl = el('ak-tr-ket');
  const daftarEl = el('ak-tr-daftar');
  const noSiap = el('ak-tr-nosiap');
  let BACKEND = {};     // key -> {nama, siap, alasan, ket, par}
  let pollTr = null;

  function isiBackendPar() {
    const b = BACKEND[selBackend.value];
    if (!b) return;
    ketEl.textContent = b.ket || '';
    if (b.par) {
      if (b.par.epochs != null) inEpochs.value = b.par.epochs;
      if (b.par.batch != null) inBatch.value = b.par.batch;
    }
  }
  selBackend.addEventListener('change', isiBackendPar);

  async function muatBahan() {
    const r = await send(`/api/aksi/latih/bahan?ds=${EDS}`);
    if (!r || !r.ok) return;
    BACKEND = r.backend || {};
    // Hanya backend yang SIAP ditawarkan — yang tidak siap tak ditampilkan
    // sebagai pilihan yang menuntun ke kegagalan.
    const siap = Object.entries(BACKEND).filter(([, v]) => v.siap);
    selBackend.innerHTML = '';
    siap.forEach(([k, v]) => {
      const o = document.createElement('option');
      o.value = k; o.textContent = v.nama || k;
      selBackend.appendChild(o);
    });
    selVersi.innerHTML = '';
    (r.versi || []).forEach(v => {
      const o = document.createElement('option');
      o.value = v.nomor;
      const j = v.jumlah || {};
      o.textContent = `v${v.nomor} · ${v.n || 0} klip · ${v.kelas || 0} kelas`
        + ((j.train != null || j.valid != null)
          ? ` (train ${j.train || 0}/valid ${j.valid || 0})` : '');
      selVersi.appendChild(o);
    });
    const adaSiap = siap.length > 0;
    const adaVersi = (r.versi || []).length > 0;
    trWrap.hidden = !adaSiap;
    noSiap.hidden = adaSiap;
    if (adaSiap) { isiBackendPar(); }
    // Tak ada versi -> beri tahu, tombol dimatikan (tak ada yang bisa dilatih).
    btnMulai.disabled = !(adaSiap && adaVersi);
    if (adaSiap && !adaVersi) ketEl.textContent =
      'Belum ada versi dataset aksi — buat versi dulu sebelum melatih.';
  }

  function barisRun(s) {
    const li = document.createElement('li');
    li.className = 'ak-tr-run';
    const metr = s.terbaik && s.utama ? s.terbaik[s.utama] : null;
    const bagian = [`A${s.nomor}`, s.backend_nama || s.backend || '',
      `v${s.versi}`, s.keadaan];
    if (s.epochs) bagian.push(`${s.epoch || 0}/${s.epochs}`);
    if (metr != null) bagian.push(`acc ${(metr * 100).toFixed(1)}%`);
    const sp = document.createElement('span');
    sp.textContent = bagian.filter(Boolean).join(' · ');
    li.appendChild(sp);
    if (s.punya_bobot) {
      const a = document.createElement('a');
      a.className = 'chip ak-unduh';
      a.href = `/aksi/latih/bobot?ds=${EDS}&nomor=${s.nomor}&jenis=best`;
      a.textContent = 'best.pt';
      li.appendChild(a);
      pasangEval(li, s);     // tombol Evaluasi + panel hasil (confusion matrix)
    }
    if (s.keadaan === 'antre' || s.keadaan === 'jalan') {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'chip';
      b.textContent = 'Hentikan';
      b.addEventListener('click', async () => {
        await send(`/api/aksi/latih/batal?ds=${EDS}&nomor=${s.nomor}`,
          { method: 'POST' });
        setTimeout(pantauTr, 500);
      });
      li.appendChild(b);
    }
    return li;
  }

  // -- evaluasi (Langkah 10): confusion matrix + akurasi + pasangan tertukar --
  // Tombol per run yang punya best.pt. Memulai eval (subproses terlepas, lajur
  // GPU aksi), lalu memantau sampai selesai dan menampilkan gambar + angka.
  // "Alat ukur harus diuji pada model buruk": vonis `tingkat` ikut ditampilkan,
  // jadi model degenerat tampak sebagai "buruk", bukan diam-diam hijau.
  const pollEval = {};     // nomor -> interval id

  function pasangEval(li, s) {
    const btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'chip';
    btn.textContent = 'Evaluasi';
    const panel = document.createElement('div');
    panel.className = 'ak-eval halus'; panel.hidden = true;
    li.appendChild(btn); li.appendChild(panel);
    btn.addEventListener('click', () => jalankanEval(s.nomor, panel, btn));
    // Tampilkan hasil yang sudah ada (tanpa menjalankan ulang).
    muatEval(s.nomor, panel, btn);
  }

  async function muatEval(nomor, panel, btn) {
    const r = await send(`/api/aksi/eval/kemajuan?ds=${EDS}&nomor=${nomor}`);
    if (!r || !r.ok || !r.eval) return false;
    renderEval(panel, r.eval, nomor, btn);
    const jalan = r.eval.keadaan === 'antre' || r.eval.keadaan === 'jalan';
    if (jalan && !pollEval[nomor]) {
      pollEval[nomor] = setInterval(() => muatEval(nomor, panel, btn), 2000);
    }
    if (!jalan && pollEval[nomor]) {
      clearInterval(pollEval[nomor]); pollEval[nomor] = null;
      btn.disabled = false; btn.textContent = 'Evaluasi ulang';
    }
    return true;
  }

  async function jalankanEval(nomor, panel, btn) {
    btn.disabled = true;
    panel.hidden = false; panel.textContent = 'Memulai evaluasi...';
    const r = await send(`/api/aksi/eval/mulai?ds=${EDS}&nomor=${nomor}`,
      { method: 'POST' });
    if (!r || !r.ok) {
      panel.textContent = (r && r.error) || 'gagal memulai evaluasi';
      btn.disabled = false; return;
    }
    if (!pollEval[nomor]) {
      pollEval[nomor] = setInterval(() => muatEval(nomor, panel, btn), 2000);
    }
    muatEval(nomor, panel, btn);
  }

  function renderEval(panel, ev, nomor, btn) {
    panel.hidden = false;
    if (ev.keadaan === 'antre' || ev.keadaan === 'jalan') {
      const m = ev.maju;
      panel.textContent = 'Evaluasi ' + ev.keadaan
        + (m ? ` — klip ${m.sudah}/${m.total}` : '...');
      btn.disabled = true;
      return;
    }
    panel.innerHTML = '';
    if (ev.keadaan === 'gagal' || ev.keadaan === 'hilang') {
      const p = document.createElement('p');
      p.className = 'ak-eval-galat';
      p.textContent = 'Evaluasi ' + ev.keadaan + (ev.galat ? ': ' + ev.galat : '');
      panel.appendChild(p);
      return;
    }
    const mk = ev.metrik || {};
    // Vonis: warna dari `tingkat` supaya model buruk tak tampak baik.
    const vonis = document.createElement('p');
    vonis.className = 'ak-eval-vonis';
    vonis.dataset.tingkat = mk.tingkat || '';
    const ak = mk.akurasi != null ? (mk.akurasi * 100).toFixed(1) + '%' : '—';
    const rk = mk.akurasi_rerata_kelas != null
      ? (mk.akurasi_rerata_kelas * 100).toFixed(1) + '%' : '—';
    vonis.innerHTML = `<b>${(mk.tingkat || '').toUpperCase()}</b> · `
      + `akurasi ${ak} · rata-kelas ${rk}`;
    panel.appendChild(vonis);
    if (mk.pesan) {
      const pz = document.createElement('p');
      pz.className = 'ak-eval-pesan'; pz.textContent = mk.pesan;
      panel.appendChild(pz);
    }
    // Pasangan paling sering tertukar — itu yang menuntun perbaikan berikutnya.
    if ((mk.pasangan_bingung || []).length) {
      const ul = document.createElement('ul');
      ul.className = 'ak-eval-bingung';
      mk.pasangan_bingung.slice(0, 5).forEach(b => {
        const liB = document.createElement('li');
        liB.textContent = `${b.dari} → ${b.ke}: ${b.jml}`
          + (b.porsi != null ? ` (${(b.porsi * 100).toFixed(0)}%)` : '');
        ul.appendChild(liB);
      });
      panel.appendChild(ul);
    }
    if (ev.punya_gambar) {
      const img = document.createElement('img');
      img.className = 'ak-eval-img'; img.alt = 'Confusion matrix';
      img.loading = 'lazy';
      // cache-bust supaya evaluasi ulang menampilkan gambar baru.
      img.src = `/aksi/eval/gambar?ds=${EDS}&nomor=${nomor}&t=${Date.now()}`;
      panel.appendChild(img);
    }
  }

  function renderRun(daftar) {
    // Daftar dibangun ulang: hentikan poll eval lama (panel-panelnya kini
    // terlepas) — tiap baris baru memasang kembali poll-nya sendiri.
    for (const k in pollEval) { if (pollEval[k]) clearInterval(pollEval[k]); }
    for (const k in pollEval) delete pollEval[k];
    daftarEl.innerHTML = '';
    (daftar || []).forEach(s => daftarEl.appendChild(barisRun(s)));
    const jalan = (daftar || []).some(
      s => s.keadaan === 'antre' || s.keadaan === 'jalan');
    if (jalan) { majuEl.hidden = false; btnMulai.disabled = true; }
    else { majuEl.hidden = true; }
    return jalan;
  }

  async function pantauTr() {
    const r = await send(`/api/aksi/latih/kemajuan?ds=${EDS}`);
    if (!r || !r.ok) return;
    const jalan = renderRun(r.daftar);
    if (jalan) {
      const aktif = (r.daftar || []).find(
        s => s.keadaan === 'jalan' || s.keadaan === 'antre');
      if (aktif) majuEl.textContent =
        `${aktif.backend_nama || ''} ${aktif.keadaan} — epoch `
        + `${aktif.epoch || 0}/${aktif.epochs || 0} (${aktif.persen || 0}%)`;
    } else {
      if (pollTr) { clearInterval(pollTr); pollTr = null; }
      // selesai -> tombol boleh dipakai lagi (kalau masih ada versi).
      btnMulai.disabled = !selVersi.value;
    }
  }

  btnMulai.addEventListener('click', async () => {
    if (!selBackend.value || !selVersi.value) { toast('Pilih backend & versi'); return; }
    btnMulai.disabled = true;
    majuEl.hidden = false; majuEl.textContent = 'Memulai...';
    const r = await send(`/api/aksi/latih/mulai?ds=${EDS}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        versi: parseInt(selVersi.value, 10), backend: selBackend.value,
        par: { epochs: parseInt(inEpochs.value, 10) || undefined,
          batch: parseInt(inBatch.value, 10) || undefined },
      }),
    });
    if (!r || !r.ok) {
      toast((r && r.error) || 'gagal memulai'); majuEl.textContent = (r && r.error) || '';
      btnMulai.disabled = false; return;
    }
    toast(`Training A${r.nomor} dimulai`);
    if (!pollTr) pollTr = setInterval(pantauTr, 2000);
    pantauTr();
  });

  muatBahan().then(pantauTr);
}

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

// -- editor kelas aksi (pemilik) ---------------------------------------------
// Tempat pemilik MENDEFINISIKAN/mengubah daftar kelas. Tanpa ini, projek video
// baru tak punya cara menyetel kelas, dan halaman cuma menampilkan pesan "belum
// ada kelas" tanpa jalan keluar. Tombol & dialog hanya ada untuk pemilik.
(function editorKelas() {
  const buka = el('ak-set-kelas');
  const dlg = el('dlg-aksi');
  if (!buka || !dlg) return;                 // non-pemilik: tombol/dialog absen
  const taKelas = el('aksi-kelas');
  const inNeg = el('aksi-negatif');
  const simpan = el('aksi-kelas-simpan');
  const tampil = v => {
    dlg.hidden = !v;
    if (v) {
      taKelas.value = (D.kelas || []).join('\n');
      inNeg.value = D.negatif || '';
      setTimeout(() => taKelas.focus(), 30);
    }
  };
  buka.addEventListener('click', () => tampil(true));
  el('dlg-aksi-tutup').addEventListener('click', () => tampil(false));
  el('aksi-kelas-batal').addEventListener('click', () => tampil(false));
  dlg.addEventListener('click', e => { if (e.target === dlg) tampil(false); });

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
    // Muat ulang: begitu kelas ada, tombol kelas + pelabelan aktif dan pesan
    // "belum ada kelas" hilang — lebih sederhana & tak bisa salah-sinkron
    // dibanding merender ulang semua keadaan di tempat.
    location.reload();
  });
})();

render();
