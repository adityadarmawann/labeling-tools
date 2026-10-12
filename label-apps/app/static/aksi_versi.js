'use strict';

/*
 * Tab VERSI untuk projek VIDEO (classifier aksi) — padanan halaman /versi
 * gambar, tetapi untuk KLIP. Tiga bagian, sama susunannya dengan versi.js:
 *
 *   1. Wizard "Buat versi klip" (pemilik/Editor): lima langkah accordion yang
 *      merakit sebuah `resep` {varian, rasio_val, cap, fps, ukuran, sasaran}
 *      lalu POST ke /api/aksi/versi/mulai; kemajuannya di-poll.
 *   2. Impor dataset jadi (.zip unggah / path server) — tetap seperti dulu.
 *   3. Penjelajah versi dua-kolom: daftar versi di kiri, detail satu versi di
 *      kanan (dibaca /api/aksi/versi/isi), untuk SEMUA anggota.
 *
 * Resep video jauh lebih sederhana dari gambar (resep_sah hanya menerima enam
 * medan; varian augmentasi dipilih otomatis), jadi tak ada popup operasi,
 * remap kelas, atau pelat latar. Pola langkah & kelas CSS sama dengan versi.js.
 *
 * send() + toast() global dari app.js.
 */

const D = JSON.parse(document.getElementById('akv-awal').textContent);
const EDS = encodeURIComponent(D.ds);
const el = id => document.getElementById(id);
const EST = D.estimasi || { n_berlabel: 0, per_kelas: {}, n_sumber: 0 };
const BAWAAN = D.bawaan || { varian: 2, rasio_val: 0.2, cap: 3, fps: 25, ukuran: [224, 224], sasaran: 'median' };

let versiData = (D.versi || []).map(v => ({ ...v }));

// ===========================================================================
// 3. PENJELAJAH VERSI (daftar kiri + detail kanan)
// ===========================================================================
const tata = el('akv-tata');
const kosong = el('akv-kosong');
const ringBox = el('akv-ring');
const detailBox = el('vs-detail');
let dibuka = null;              // nomor versi yang detailnya sedang tampil

function renderRing() {
  const ada = versiData.length > 0;
  if (tata) tata.hidden = !ada;
  if (kosong) kosong.hidden = ada;
  if (!ringBox) return;
  const urut = [...versiData].sort((a, b) => (b.nomor || 0) - (a.nomor || 0));
  ringBox.innerHTML = '';
  urut.forEach(v => {
    const j = v.jumlah || {};
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'vs-ring';
    b.dataset.nomor = v.nomor;
    if (v.nomor === dibuka) b.setAttribute('data-on', '');
    b.innerHTML =
      `<span class="vs-ring-atas"><b class="vs-nomor">v${v.nomor}</b></span>`
      + `<span class="vs-ring-cap"><span class="vs-pil">${v.n || 0} klip</span>`
      + `<span class="vs-pil">${v.kelas || 0} kelas</span></span>`
      + `<span class="vs-ring-oleh">train ${j.train || 0} / valid ${j.valid || 0}</span>`;
    b.addEventListener('click', () => bukaVersi(v.nomor));
    ringBox.appendChild(b);
  });
}

async function bukaVersi(nomor) {
  dibuka = nomor;
  renderRing();
  if (detailBox) detailBox.innerHTML = '<div class="vs-detail-muat">memuat…</div>';
  const r = await send(`/api/aksi/versi/isi?ds=${EDS}&nomor=${nomor}`);
  if (!r || !r.ok) {
    if (detailBox) detailBox.innerHTML = `<div class="vs-detail-muat">${(r && r.error) || 'gagal memuat'}</div>`;
    return;
  }
  renderDetail(r.versi);
}

function renderDetail(v) {
  if (!detailBox) return;
  const j = v.jumlah || {};
  const pk = v.per_kelas || {};
  const maks = Math.max(1, ...Object.values(pk).map(x => (x.train || 0) + (x.valid || 0)));
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  let kelasBar = '';
  Object.keys(pk).sort().forEach(nm => {
    const t = pk[nm].train || 0, vv = pk[nm].valid || 0, tot = t + vv;
    kelasBar += `<div class="vk-baris"><span class="vk-nama">${esc(nm)}</span>`
      + `<span class="vk-bar"><i style="width:${Math.round(100 * tot / maks)}%;background:var(--aksi)"></i></span>`
      + `<span class="vk-n">${tot}</span>`
      + `<span class="vk-persen">tr ${t}/va ${vv}</span></div>`;
  });

  const rs = v.resep || {};
  let resepHtml;
  if (v.diimpor) {
    resepHtml = '<p class="halus">Diimpor apa adanya dari dataset jadi — '
      + 'tidak diproses ulang di HIGOLAB.</p>';
  } else {
    const uk = rs.ukuran || [];
    resepHtml = '<div class="vs-tinjau">'
      + `<div><span>Pembagian</span><b>train ${Math.round(100 * (1 - (rs.rasio_val ?? 0.2)))}% / valid ${Math.round(100 * (rs.rasio_val ?? 0.2))}%</b></div>`
      + `<div><span>Ukuran</span><b>${(uk[0] || '?')}×${(uk[1] || '?')} · ${rs.fps || '?'} fps (letterbox)</b></div>`
      + `<div><span>Augmentasi</span><b>${rs.varian ?? '?'} salinan/klip di train</b></div>`
      + `<div><span>Penyeimbang</span><b>cap ${rs.cap ?? '?'}× · sasaran ${esc(rs.sasaran || 'median')}</b></div>`
      + '</div>';
  }

  const contoh = (v.contoh || []).map(n => `<li>${esc(n)}</li>`).join('');

  detailBox.innerHTML =
    '<div class="vs-detail-atas"><b class="vs-nomor">v' + v.nomor + '</b>'
    + `<span class="vs-detail-judul">${esc(v.dibuat || '')}</span>`
    + `<span class="halus">oleh ${esc(v.oleh || '')}</span></div>`
    + (v.catatan ? `<p class="halus vs-contoh-nota">“${esc(v.catatan)}”</p>` : '')
    + '<div class="vs-bagian"><h4>Unduh</h4>'
    + `<a class="chip ak-unduh" href="/api/aksi/versi/unduh?ds=${EDS}&nomor=${v.nomor}">Unduh arsip klip v${v.nomor}</a>`
    + '<p class="halus">Folder per kelas, train/valid bersebelahan.</p></div>'
    + '<div class="vs-bagian"><h4>Pembagian</h4><div class="vs-split">'
    + `<div class="vs-kotak vs-k-train"><b>${j.train || 0}</b><span>train</span></div>`
    + `<div class="vs-kotak vs-k-valid"><b>${j.valid || 0}</b><span>valid</span></div>`
    + `<div class="vs-kotak"><b>${v.n || 0}</b><span>total · ${v.kelas || 0} kelas</span></div>`
    + '</div></div>'
    + (kelasBar ? `<div class="vs-bagian"><h4>Per kelas</h4><div class="vk-bagan">${kelasBar}</div></div>` : '')
    + `<div class="vs-bagian"><h4>Resep</h4>${resepHtml}</div>`
    + (contoh ? `<div class="vs-bagian"><h4>Contoh klip</h4><ul class="vs-contoh halus">${contoh}</ul></div>` : '');
}

renderRing();
if (versiData.length) bukaVersi(Math.max(...versiData.map(v => v.nomor || 0)));

// ===========================================================================
// 1. WIZARD BUAT VERSI (pemilik/Editor)
// ===========================================================================
const wz = el('wz');
if (wz) {
  const items = [...wz.querySelectorAll('.wz-item')];
  const kerja = el('wz-kerja');
  let poll = null;

  function bukaLangkah(n) {
    items.forEach(li => {
      if (+li.dataset.langkah === n) li.setAttribute('data-buka', '');
      else li.removeAttribute('data-buka');
    });
    ringkas();
  }

  // Header & tombol "Ubah" membuka langkahnya; tombol Lanjut/Kembali pindah.
  items.forEach(li => {
    const n = +li.dataset.langkah;
    li.querySelector('.wz-judul').addEventListener('click', (e) => {
      if (e.target.closest('[data-lanjut],[data-mundur]')) return;
      bukaLangkah(n);
    });
    const maju = li.querySelector('[data-lanjut]');
    if (maju) maju.addEventListener('click', () => bukaLangkah(Math.min(n + 1, items.length)));
    const mundur = li.querySelector('[data-mundur]');
    if (mundur) mundur.addEventListener('click', () => bukaLangkah(n - 1));
  });

  // ---- Langkah 1: klip sumber ----
  function renderSumber() {
    const pk = EST.per_kelas || {};
    const baris = Object.keys(pk).sort().map(nm =>
      `<span class="akv-sum-kelas"><b>${pk[nm]}</b> ${nm}</span>`).join('');
    el('wz-sumber').innerHTML =
      `<div class="akv-sum-besar"><b>${EST.n_berlabel}</b> klip berlabel`
      + `<span class="halus"> · dari ${EST.n_sumber} video sumber</span></div>`
      + (baris ? `<div class="akv-sum-kelas-baris">${baris}</div>` : '')
      + (EST.n_berlabel < 2 ? '<p class="halus wz-peringatan">Butuh minimal beberapa klip berlabel untuk membuat versi.</p>' : '');
  }

  // ---- Langkah 2: pembagian ----
  const inTrain = el('wz-train'), inValid = el('wz-valid');
  function sinkronSplit(dari) {
    let tr = parseInt(inTrain.value, 10); let va = parseInt(inValid.value, 10);
    if (dari === 'valid' && !isNaN(va)) tr = 100 - va;
    else if (!isNaN(tr)) va = 100 - tr;
    tr = Math.min(95, Math.max(50, isNaN(tr) ? 80 : tr)); va = 100 - tr;
    inTrain.value = tr; inValid.value = va;
    hitungSplit();
  }
  function rasioVal() { return (100 - Math.min(95, Math.max(50, parseInt(inTrain.value, 10) || 80))) / 100; }
  function hitungSplit() {
    const n = EST.n_berlabel; const rv = rasioVal();
    const nv = Math.round(n * rv); const nt = n - nv;
    const bilah = el('wz-bilah');
    bilah.innerHTML = `<i class="p-ok" style="flex-grow:${nt || 0.001}"></i>`
      + `<i class="p-stop" style="flex-grow:${nv || 0.001}"></i>`;
    el('wz-split').innerHTML = `≈ <b>${nt}</b> train · <b>${nv}</b> valid `
      + `<span class="halus">(dibelah per video sumber, jadi bisa meleset sedikit)</span>`;
  }
  inTrain.addEventListener('input', () => sinkronSplit('train'));
  inValid.addEventListener('input', () => sinkronSplit('valid'));

  // ---- Langkah 3: pra-proses ----
  function ketUkuran() {
    const w = +el('wz-lebar').value, h = +el('wz-tinggi').value;
    el('wz-uk-ket').textContent = (w && h)
      ? `Tiap klip jadi ${w}×${h}. Dimensi dibulatkan ke angka genap (syarat encoder).`
      : '';
  }
  ['wz-lebar', 'wz-tinggi', 'wz-fps'].forEach(id => el(id).addEventListener('input', ketUkuran));

  // ---- Langkah 4: augmentasi preview ----
  function renderVarian() {
    const pri = (D.katalog && D.katalog.prioritas) || [];
    el('wz-varian-pratinjau').innerHTML = pri.length
      ? '<span class="halus akv-varian-judul">Katalog varian (urut dari paling alami):</span>'
        + pri.map(n => `<span class="akv-varian-chip">${n}</span>`).join('')
      : '';
  }

  // ---- Ringkasan tiap langkah (saat terkuncup) + tinjauan langkah 5 ----
  function ringkas() {
    el('wz-r1').textContent = `${EST.n_berlabel} klip`;
    el('wz-r2').textContent = `${inTrain.value} / ${inValid.value}`;
    el('wz-r3').textContent = `${el('wz-lebar').value}×${el('wz-tinggi').value} · ${el('wz-fps').value} fps`;
    el('wz-r4').textContent = `${el('wz-varian').value}× aug · cap ${el('wz-cap').value}`;
    tinjau();
  }
  function tinjau() {
    const r = bacaResep();
    const nv = Math.round(EST.n_berlabel * r.rasio_val), nt = EST.n_berlabel - nv;
    el('wz-tinjau').innerHTML =
      `<div><span>Klip sumber</span><b>${EST.n_berlabel} klip · ${Object.keys(EST.per_kelas).length} kelas</b></div>`
      + `<div><span>Pembagian</span><b>≈ ${nt} train / ${nv} valid</b></div>`
      + `<div><span>Ukuran</span><b>${r.ukuran[0]}×${r.ukuran[1]} · ${r.fps} fps (letterbox)</b></div>`
      + `<div><span>Augmentasi</span><b>${r.varian}× per klip · cap ${r.cap}× (${r.sasaran})</b></div>`;
  }

  function bacaResep() {
    return {
      varian: Math.min(8, Math.max(0, parseInt(el('wz-varian').value, 10) || 0)),
      rasio_val: rasioVal(),
      cap: Math.min(10, Math.max(1, parseFloat(el('wz-cap').value) || 3)),
      fps: Math.min(120, Math.max(1, parseInt(el('wz-fps').value, 10) || 25)),
      ukuran: [parseInt(el('wz-lebar').value, 10) || 224, parseInt(el('wz-tinggi').value, 10) || 224],
      sasaran: el('wz-sasaran').value || 'median',
    };
  }

  // Semua kontrol memicu ringkas() ulang, supaya tinjauan selalu mutakhir.
  ['wz-varian', 'wz-cap', 'wz-sasaran', 'wz-lebar', 'wz-tinggi', 'wz-fps'].forEach(id =>
    el(id).addEventListener('input', ringkas));

  // ---- Buka / tutup wizard ----
  function setBawaan() {
    inTrain.value = Math.round(100 * (1 - BAWAAN.rasio_val));
    inValid.value = Math.round(100 * BAWAAN.rasio_val);
    el('wz-lebar').value = (BAWAAN.ukuran || [224, 224])[0];
    el('wz-tinggi').value = (BAWAAN.ukuran || [224, 224])[1];
    el('wz-fps').value = BAWAAN.fps;
    el('wz-varian').value = BAWAAN.varian;
    el('wz-cap').value = BAWAAN.cap;
    el('wz-sasaran').value = BAWAAN.sasaran;
    el('wz-nama').value = 'v' + ((versiData.reduce((m, v) => Math.max(m, v.nomor || 0), 0)) + 1);
    el('wz-catatan').value = '';
  }
  el('vs-mulai').addEventListener('click', () => {
    setBawaan();
    renderSumber(); hitungSplit(); ketUkuran(); renderVarian();
    wz.hidden = false; if (kerja) kerja.hidden = true;
    bukaLangkah(1);
    wz.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  el('wz-tutup').addEventListener('click', () => { wz.hidden = true; });

  // ---- Membuat versi ----
  function tampilKerja(k) {
    kerja.hidden = false;
    const fase = el('wz-fase'), persen = el('wz-persen'), fill = el('wz-fill');
    if (k.galat) { fase.textContent = 'Gagal: ' + k.galat; return; }
    if (k.batal) { fase.textContent = 'Dibatalkan.'; return; }
    if (k.selesai) { fase.textContent = `Versi v${k.nomor || ''} selesai.`; persen.textContent = '100%'; fill.style.width = '100%'; return; }
    fase.textContent = k.fase_nama || 'Menyiapkan…';
    const p = (k.persen != null) ? k.persen : 0;
    persen.textContent = p + '%'; fill.style.width = p + '%';
  }
  function berhenti() { if (poll) { clearInterval(poll); poll = null; } }
  async function pantau() {
    const k = await send(`/api/aksi/versi/kemajuan?ds=${EDS}`);
    if (!k || !k.ok) return;
    tampilKerja(k);
    if (k.selesai || k.batal || k.galat) {
      berhenti();
      if (k.selesai && k.nomor) {
        toast(`Versi v${k.nomor} dibuat`);
        if (!versiData.some(v => v.nomor === k.nomor)) {
          const rk = k.ringkas || {};
          versiData.unshift({ nomor: k.nomor, n: rk.n || 0, kelas: rk.kelas || 0, jumlah: rk.jumlah || {} });
        }
        renderRing(); bukaVersi(k.nomor);
        setTimeout(() => { wz.hidden = true; }, 1200);
      }
    }
  }
  el('wz-buat').addEventListener('click', async () => {
    const resep = bacaResep();
    el('wz-buat').disabled = true;
    tampilKerja({ fase_nama: 'Memulai…', persen: 0 });
    const r = await send(`/api/aksi/versi/mulai?ds=${EDS}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ resep }),
    });
    el('wz-buat').disabled = false;
    if (!r || !r.ok) { toast((r && r.error) || 'gagal memulai'); tampilKerja({ galat: (r && r.error) || 'gagal' }); return; }
    poll = setInterval(pantau, 1000); pantau();
  });
  el('wz-batal').addEventListener('click', async () => {
    await send(`/api/aksi/versi/batal?ds=${EDS}`, { method: 'POST' });
  });

  // Build mungkin sudah jalan saat halaman dibuka (ditinggal lalu kembali).
  (async () => {
    const k = await send(`/api/aksi/versi/kemajuan?ds=${EDS}`);
    if (k && k.ok && k.jalan) { wz.hidden = false; bukaLangkah(5); tampilKerja(k); poll = setInterval(pantau, 1000); }
  })();
}

// ===========================================================================
// 2. IMPOR dataset klip jadi (unggah .zip / path server) — tetap seperti dulu
// ===========================================================================
{
  const impFile = el('ak-impor-file');
  const pathBtn = el('ak-impor-path-btn');
  const pathTombol = el('ak-impor-path-tombol');
  const pathBaris = el('ak-impor-path-baris');
  const impMaju = el('ak-impor-maju');
  if ((impFile || pathBtn) && impMaju) {
    let impPoll = null;

    function impTampil(k) {
      impMaju.hidden = false;
      if (k.galat) { impMaju.textContent = 'Gagal: ' + k.galat; return; }
      if (k.selesai) { impMaju.textContent = `Versi v${k.nomor || ''} selesai.`; return; }
      const p = (k.persen != null) ? ` ${k.persen}%` : '';
      impMaju.textContent = (k.fase_nama || 'Memproses') + p;
    }
    async function impPantau() {
      const k = await send(`/api/aksi/impor/kemajuan?ds=${EDS}`);
      if (!k || !k.ok) return;
      impTampil(k);
      if (k.selesai || k.galat) {
        if (impPoll) { clearInterval(impPoll); impPoll = null; }
        if (k.selesai && k.nomor && !versiData.some(v => v.nomor === k.nomor)) {
          const rk = k.ringkas || {};
          versiData.unshift({ nomor: k.nomor, n: rk.n || 0, kelas: rk.kelas || 0, jumlah: rk.jumlah || {} });
          renderRing(); bukaVersi(k.nomor);
          toast(`Versi v${k.nomor} diimpor`);
        }
      }
    }
    function mulaiPoll() { if (!impPoll) impPoll = setInterval(impPantau, 1000); impPantau(); }

    if (pathTombol && pathBaris) {
      pathTombol.addEventListener('click', () => { pathBaris.hidden = !pathBaris.hidden; });
    }

    if (impFile) {
      impFile.addEventListener('change', async () => {
        const f = impFile.files && impFile.files[0];
        if (!f) return;
        impMaju.hidden = false;
        impMaju.textContent = `Mengunggah ${(f.size / 1048576).toFixed(0)} MB…`;
        const r = await send(`/api/aksi/impor?ds=${EDS}`, { method: 'POST', body: f });
        impFile.value = '';
        if (!r || !r.ok) { impMaju.textContent = (r && r.error) || 'gagal'; toast((r && r.error) || 'gagal impor'); return; }
        mulaiPoll();
      });
    }
    if (pathBtn) {
      const pathInp = el('ak-impor-path');
      pathBtn.addEventListener('click', async () => {
        const path = (pathInp && pathInp.value || '').trim();
        if (!path) { toast('isi path-nya dulu'); return; }
        impMaju.hidden = false;
        impMaju.textContent = 'Memuat dari server…';
        const r = await send(`/api/aksi/impor-path?ds=${EDS}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path }),
        });
        if (!r || !r.ok) { impMaju.textContent = (r && r.error) || 'gagal'; toast((r && r.error) || 'gagal impor'); return; }
        mulaiPoll();
      });
    }
  }
}
