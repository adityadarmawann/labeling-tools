'use strict';

/*
 * Tab TRAINING untuk projek VIDEO (classifier aksi) — padanan /latih gambar,
 * untuk 3 backend klip (VideoMAE / SlowFast / PoseC3D) + Uji produksi
 * (confusion matrix). Susunan meniru latih.js gambar: bar mesin, form dua-kolom
 * di balik CTA, lalu kartu run "Sedang berjalan" dan "Hasil" (kelas tr-* yang
 * sama). Setelan sengaja hanya epoch & batch — backend aksi mengunci sisanya.
 *
 * Rute: /api/aksi/latih/{bahan,daftar,kemajuan,mulai,batal}, /aksi/latih/bobot,
 * /api/aksi/eval/{mulai,kemajuan}, /aksi/eval/gambar. send()+toast() dari app.js.
 *
 * Dibungkus IIFE: esc()/angka() di sini menaungi helper global app.js tanpa
 * bentrok "sudah dideklarasikan" (skrip klasik berbagi lingkup global).
 */
(function () {

const D = JSON.parse(document.getElementById('akl-awal').textContent);
const EDS = encodeURIComponent(D.ds);
const el = id => document.getElementById(id);

const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const angka = (n, dec = 0) => (n == null || isNaN(n)) ? '0'
  : Number(n).toLocaleString('id-ID', { minimumFractionDigits: dec, maximumFractionDigits: dec });
function durasi(dtk) {
  dtk = Math.max(0, Math.round(dtk || 0));
  if (dtk < 60) return `${dtk} dtk`;
  if (dtk < 3600) return `${Math.floor(dtk / 60)} mnt`;
  const j = Math.floor(dtk / 3600), m = Math.round((dtk % 3600) / 60);
  return `${j} jam ${m} mnt`;
}
const LABEL_KEADAAN = {
  antre: 'Antre', jalan: 'Berjalan', selesai: 'Selesai',
  gagal: 'Gagal', batal: 'Dibatalkan', hilang: 'Terputus',
};

// ===========================================================================
// Bar mesin (GPU/RAM) — dipinjam apa adanya dari jalur gambar.
// ===========================================================================
function gambarMesin(s) {
  const g = s && s.gpu, r = s && s.ram;
  const box = el('tr-mesin-isi');
  if (!box) return;
  if (!g && !r) { box.innerHTML = '<span class="tr-diam">keadaan mesin tidak terbaca</span>'; return; }
  const vp = g ? Math.round(g.vram_pakai_mb / g.vram_total_mb * 100) : 0;
  box.innerHTML =
    (g ? `<div class="tr-m"><span class="tr-m-nama">${esc(g.nama)}</span>
        <div class="tr-m-bar"><i style="width:${g.util}%"></i></div>
        <span class="tr-m-nilai">${g.util}% · ${g.suhu}&deg;C</span></div>
      <div class="tr-m"><span class="tr-m-nama">VRAM</span>
        <div class="tr-m-bar"><i style="width:${vp}%"></i></div>
        <span class="tr-m-nilai">${angka(g.vram_pakai_mb)} / ${angka(g.vram_total_mb)} MB</span></div>` : '')
    + (r ? `<div class="tr-m"><span class="tr-m-nama">RAM</span>
        <div class="tr-m-bar"><i style="width:${r.persen}%"></i></div>
        <span class="tr-m-nilai">${angka(r.pakai_gb, 1)} / ${angka(r.total_gb, 1)} GB</span></div>` : '');
}

// ===========================================================================
// Kartu run
// ===========================================================================
function skorUtama(t) {
  const m = (t.terbaik && t.utama && t.terbaik[t.utama] != null) ? t.terbaik[t.utama] : null;
  return m;
}
function metaBaris(t) {
  return `<span class="tr-k-meta">A${t.nomor}<i>dari</i>v${t.versi}`
    + `<i>·</i>${esc(t.backend_nama || t.backend || '')}`
    + (t.oleh ? `<i>oleh</i>${esc(t.oleh)}` : '')
    + (t.dibuat ? `<i>·</i>${esc(t.dibuat)}` : '') + '</span>';
}
function kepala(t) {
  return `<header class="tr-k-atas">
    <b class="tr-k-nama">${esc(t.nama || ('Training A' + t.nomor))}</b>
    <span class="tr-pil tr-pil-${esc(t.keadaan)}">${LABEL_KEADAAN[t.keadaan] || t.keadaan}</span>
    <span class="spacer"></span>${metaBaris(t)}</header>`;
}
function unduhBobot(t) {
  if (!t.punya_bobot) return '';
  return `<a class="chip" href="/aksi/latih/bobot?ds=${EDS}&nomor=${t.nomor}&jenis=best"
      download="A${t.nomor}-best.pt" title="Bobot akurasi terbaik">best.pt</a>`;
}

function kartuJalan(t) {
  const pj = Math.max(0, Math.min(100, t.persen || 0));
  const acc = skorUtama(t);
  return `<article class="tr-kartu tr-kartu-jalan" data-nomor="${t.nomor}">
    ${kepala(t)}
    <div class="tr-bar" role="progressbar" aria-valuenow="${pj.toFixed(0)}"
         aria-valuemin="0" aria-valuemax="100"><i style="width:${pj}%"></i></div>
    <div class="tr-k-isi">
      <div class="tr-skor"><b>${angka(pj, 0)}<span>%</span></b>
        <i>epoch ${angka(t.epoch)} dari ${angka(t.epochs)}</i></div>
      <dl class="tr-k-angka">
        <div><dt>Terpakai</dt><dd>${durasi(t.detik)}</dd></div>
        <div><dt>Perkiraan sisa</dt><dd>${t.sisa ? durasi(t.sisa) : '—'}</dd></div>
        ${acc != null ? `<div><dt>akurasi</dt><dd>${angka(acc * 100, 1)}%</dd></div>` : ''}
      </dl>
    </div>
    <footer class="tr-k-aksi">
      <button class="chip chip-utama" type="button" data-rinci="${t.nomor}">Rincian</button>
      ${t.punya_bobot ? `<span class="tr-unduh">Unduh sementara ${unduhBobot(t)}</span>` : ''}
      <span class="spacer"></span>
      ${BOLEH ? `<button class="chip chip-bahaya" type="button" data-batal="${t.nomor}">Hentikan</button>` : ''}
    </footer>`;
}

function kartuHasil(t) {
  const rusak = t.keadaan === 'gagal' || t.keadaan === 'hilang' || t.keadaan === 'batal';
  const acc = skorUtama(t);
  const lain = Object.keys(t.terbaik || {}).filter(k => k !== t.utama).slice(0, 3);
  let isi;
  if (rusak) {
    isi = `<p class="tr-k-galat">${esc(t.galat || 'berhenti tanpa checkpoint')}</p>`;
  } else {
    isi = `<div class="tr-k-isi">
      <div class="tr-skor">${acc != null
        ? `<b>${angka(acc * 100, 1)}<span>%</span></b><i>akurasi (${esc(t.utama || 'val')})</i>`
        : '<b>—</b><i>belum ada metrik</i>'}</div>
      <dl class="tr-k-angka">
        <div><dt>Epoch</dt><dd>${angka(t.epoch)} / ${angka(t.epochs)}</dd></div>
        <div><dt>Durasi</dt><dd>${durasi(t.detik)}</dd></div>
        ${lain.map(k => `<div><dt>${esc(k)}</dt><dd>${angka((t.terbaik[k] || 0) * 100, 1)}%</dd></div>`).join('')}
      </dl></div>`;
  }
  return `<article class="tr-kartu" data-nomor="${t.nomor}">
    ${kepala(t)}
    ${isi}
    <footer class="tr-k-aksi">
      <button class="chip chip-utama" type="button" data-rinci="${t.nomor}">Rincian</button>
      ${unduhBobot(t)}
      ${t.punya_bobot ? `<button class="chip" type="button" data-eval="${t.nomor}">Uji produksi</button>` : ''}
      <span class="spacer"></span>
    </footer>
    ${t.punya_bobot ? `<div class="ak-eval" id="ak-eval-${t.nomor}" hidden></div>` : ''}`;
}

// ===========================================================================
// Evaluasi (confusion matrix + akurasi + pasangan tertukar)
// ===========================================================================
const pollEval = {};

function renderEval(panel, ev, nomor, btn) {
  panel.hidden = false;
  if (ev.keadaan === 'antre' || ev.keadaan === 'jalan') {
    const m = ev.maju;
    panel.innerHTML = `<p class="halus">Evaluasi ${ev.keadaan}`
      + (m ? `, klip ${m.sudah}/${m.total}` : '…') + '</p>';
    if (btn) btn.disabled = true;
    return;
  }
  if (ev.keadaan === 'gagal' || ev.keadaan === 'hilang') {
    panel.innerHTML = `<p class="ak-eval-galat">Evaluasi ${ev.keadaan}`
      + (ev.galat ? ': ' + esc(ev.galat) : '') + '</p>';
    if (btn) { btn.disabled = false; btn.textContent = 'Uji produksi'; }
    return;
  }
  const mk = ev.metrik || {};
  const ak = mk.akurasi != null ? (mk.akurasi * 100).toFixed(1) + '%' : '-';
  const rk = mk.akurasi_rerata_kelas != null ? (mk.akurasi_rerata_kelas * 100).toFixed(1) + '%' : '-';
  let html = `<p class="ak-eval-vonis" data-tingkat="${esc(mk.tingkat || '')}">`
    + `<b>${esc((mk.tingkat || '').toUpperCase())}</b> · akurasi ${ak} · rata-kelas ${rk}</p>`;
  if (mk.pesan) html += `<p class="ak-eval-pesan">${esc(mk.pesan)}</p>`;
  if ((mk.pasangan_bingung || []).length) {
    html += '<ul class="ak-eval-bingung">' + mk.pasangan_bingung.slice(0, 5).map(b =>
      `<li>${esc(b.dari)} → ${esc(b.ke)}: ${b.jml}`
      + (b.porsi != null ? ` (${(b.porsi * 100).toFixed(0)}%)` : '') + '</li>').join('') + '</ul>';
  }
  if (ev.punya_gambar) {
    html += `<img class="ak-eval-img" alt="Confusion matrix" loading="lazy"`
      + ` src="/aksi/eval/gambar?ds=${EDS}&nomor=${nomor}&t=${Date.now()}">`;
  }
  panel.innerHTML = html;
  if (btn) { btn.disabled = false; btn.textContent = 'Uji ulang'; }
}

async function muatEval(nomor) {
  const panel = el('ak-eval-' + nomor);
  if (!panel) return;
  const btn = document.querySelector(`[data-eval="${nomor}"]`);
  const r = await send(`/api/aksi/eval/kemajuan?ds=${EDS}&nomor=${nomor}`);
  if (!r || !r.ok || !r.eval) return;
  renderEval(panel, r.eval, nomor, btn);
  const jalan = r.eval.keadaan === 'antre' || r.eval.keadaan === 'jalan';
  if (jalan && !pollEval[nomor]) pollEval[nomor] = setInterval(() => muatEval(nomor), 2000);
  if (!jalan && pollEval[nomor]) { clearInterval(pollEval[nomor]); pollEval[nomor] = null; }
}

async function jalankanEval(nomor) {
  const panel = el('ak-eval-' + nomor);
  const btn = document.querySelector(`[data-eval="${nomor}"]`);
  if (panel) { panel.hidden = false; panel.innerHTML = '<p class="halus">Memulai evaluasi…</p>'; }
  if (btn) btn.disabled = true;
  const r = await send(`/api/aksi/eval/mulai?ds=${EDS}&nomor=${nomor}`, { method: 'POST' });
  if (!r || !r.ok) {
    if (panel) panel.innerHTML = `<p class="ak-eval-galat">${esc((r && r.error) || 'gagal memulai')}</p>`;
    if (btn) btn.disabled = false; return;
  }
  if (!pollEval[nomor]) pollEval[nomor] = setInterval(() => muatEval(nomor), 2000);
  muatEval(nomor);
}

// ===========================================================================
// Daftar run + form
// ===========================================================================
const BOLEH = !!el('ak-latih');
const jalanBox = el('ak-tr-jalan');
const hasilBox = el('ak-tr-hasil');

function renderRun(daftar) {
  for (const k in pollEval) { if (pollEval[k]) clearInterval(pollEval[k]); delete pollEval[k]; }
  const jalan = (daftar || []).filter(s => s.keadaan === 'antre' || s.keadaan === 'jalan');
  const hasil = (daftar || []).filter(s => !(s.keadaan === 'antre' || s.keadaan === 'jalan'));
  if (jalanBox) jalanBox.innerHTML = jalan.map(kartuJalan).join('');
  if (hasilBox) hasilBox.innerHTML = hasil.map(kartuHasil).join('');
  const jb = el('ak-tr-jalan-bagian'), hb = el('ak-tr-hasil-bagian'), ks = el('ak-tr-kosong');
  if (jb) jb.hidden = !jalan.length;
  if (hb) hb.hidden = !hasil.length;
  if (ks) ks.hidden = (jalan.length + hasil.length) > 0;
  // Tampilkan hasil evaluasi yang sudah ada tanpa menjalankan ulang.
  hasil.forEach(s => { if (s.punya_bobot) muatEval(s.nomor); });
  return jalan.length > 0;
}

// ===========================================================================
// Panel rincian: kurva akurasi per-epoch + setelan + log (padanan tr-tirai)
// ===========================================================================
const tiraiR = el('ak-tr-tirai');
function tutupRincian() { if (tiraiR) tiraiR.hidden = true; }
if (el('ak-tr-panel-tutup')) el('ak-tr-panel-tutup').addEventListener('click', tutupRincian);
if (tiraiR) tiraiR.addEventListener('click', (e) => { if (e.target === tiraiR) tutupRincian(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') tutupRincian(); });

function kurvaSvg(kurva) {
  const pts = (kurva || []).filter(p => p.nilai != null);
  if (pts.length < 2) return '<p class="halus">Kurva muncul setelah beberapa epoch selesai.</p>';
  const W = 620, H = 220, pad = 36;
  const xs = pts.map(p => p.epoch), maxE = Math.max(...xs), minE = Math.min(...xs);
  const x = (e) => pad + (maxE === minE ? 0 : (e - minE) / (maxE - minE)) * (W - pad - 12);
  const y = (v) => H - pad - Math.max(0, Math.min(1, v)) * (H - pad - 12);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.epoch).toFixed(1)},${y(p.nilai).toFixed(1)}`).join(' ');
  let grid = '';
  [0, .25, .5, .75, 1].forEach(v => {
    grid += `<line x1="${pad}" y1="${y(v).toFixed(1)}" x2="${W - 12}" y2="${y(v).toFixed(1)}" class="tr-kurva-grid"/>`
      + `<text x="${pad - 6}" y="${(y(v) + 3).toFixed(1)}" text-anchor="end" class="tr-kurva-lbl">${(v * 100).toFixed(0)}%</text>`;
  });
  return `<svg class="tr-kurva" viewBox="0 0 ${W} ${H}" role="img" aria-label="Kurva akurasi per epoch">`
    + grid + `<path d="${line}" fill="none" class="tr-kurva-garis"/>`
    + `<text x="${(pad + (W - 12)) / 2}" y="${H - 8}" text-anchor="middle" class="tr-kurva-lbl">epoch</text></svg>`;
}

async function bukaRincian(nomor) {
  if (!tiraiR) return;
  el('ak-tr-panel-judul').textContent = 'Rincian training A' + nomor;
  el('ak-tr-panel-isi').innerHTML = '<p class="halus">memuat…</p>';
  tiraiR.hidden = false;
  const r = await send(`/api/aksi/latih/rincian?ds=${EDS}&nomor=${nomor}`);
  if (!r || !r.ok) { el('ak-tr-panel-isi').innerHTML = `<p class="halus">${esc((r && r.error) || 'gagal')}</p>`; return; }
  const t = r.rincian, acc = skorUtama(t), par = t.par || {};
  const kv = Object.entries(par).map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('');
  const accBest = (t.terbaik && t.utama != null && t.terbaik[t.utama] != null) ? t.terbaik[t.utama] : acc;
  el('ak-tr-panel-isi').innerHTML =
    '<div class="tr-p-blok"><div class="tr-kv-grid">'
    + `<div><dt>Keadaan</dt><dd>${esc(LABEL_KEADAAN[t.keadaan] || t.keadaan)}</dd></div>`
    + `<div><dt>Backend</dt><dd>${esc(t.backend_nama || t.backend || '')}</dd></div>`
    + `<div><dt>Versi</dt><dd>v${esc(t.versi)}</dd></div>`
    + `<div><dt>Epoch</dt><dd>${angka(t.epoch)} / ${angka(t.epochs)}</dd></div>`
    + `<div><dt>Durasi</dt><dd>${durasi(t.detik)}</dd></div>`
    + (accBest != null ? `<div><dt>Akurasi terbaik</dt><dd>${angka(accBest * 100, 1)}%</dd></div>` : '')
    + '</div></div>'
    + `<div class="tr-p-blok"><h4>Kurva akurasi (validasi)</h4>${kurvaSvg(t.kurva)}</div>`
    + (kv ? `<div class="tr-p-blok"><h4>Setelan</h4><dl class="tr-kv-grid">${kv}</dl></div>` : '')
    + (t.catatan ? `<div class="tr-p-blok"><h4>Catatan</h4><p class="halus">${esc(t.catatan)}</p></div>` : '')
    + `<div class="tr-p-blok"><h4>Log (ekor)</h4><pre class="tr-log">${esc(t.log || '(belum ada log)')}</pre></div>`;
}

// Delegasi klik: Rincian (data-rinci) + Hentikan (data-batal) + Uji produksi (data-eval).
el('akl-isi').addEventListener('click', (e) => {
  const rinci = e.target.closest('[data-rinci]');
  if (rinci) { bukaRincian(rinci.dataset.rinci); return; }
  const batal = e.target.closest('[data-batal]');
  if (batal) {
    send(`/api/aksi/latih/batal?ds=${EDS}&nomor=${batal.dataset.batal}`, { method: 'POST' })
      .then(() => setTimeout(pantauTr, 500));
    return;
  }
  const ev = e.target.closest('[data-eval]');
  if (ev) jalankanEval(ev.dataset.eval);
});

let pollTr = null;
async function pantauTr() {
  const r = await send(`/api/aksi/latih/kemajuan?ds=${EDS}`);
  if (!r || !r.ok) return;
  if (r.statistik) gambarMesin(r.statistik);
  const jalan = renderRun(r.daftar);
  const btn = el('ak-tr-mulai');
  if (jalan) {
    if (btn) btn.disabled = true;
    if (!pollTr) pollTr = setInterval(pantauTr, 3000);
  } else {
    if (pollTr) { clearInterval(pollTr); pollTr = null; }
    if (btn && selVersi) btn.disabled = !selVersi.value;
  }
}

// -- form (pemilik/Editor) --------------------------------------------------
let BACKEND = {};
let selBackend, selVersi;
if (BOLEH) {
  selBackend = el('ak-tr-backend');
  selVersi = el('ak-tr-versi');
  const inEpochs = el('ak-tr-epochs');
  const inBatch = el('ak-tr-batch');
  const ketEl = el('ak-tr-ket');
  const versiKet = el('ak-tr-versi-ket');
  const form = el('ak-latih');
  const kepalaEl = el('ak-tr-kepala');
  const noSiap = el('ak-tr-nosiap');
  const majuEl = el('ak-tr-maju');
  const btnMulai = el('ak-tr-mulai');

  function isiBackendPar() {
    const b = BACKEND[selBackend.value];
    if (!b) return;
    ketEl.textContent = b.ket || '';
    if (b.par) {
      if (b.par.epochs != null) inEpochs.value = b.par.epochs;
      if (b.par.batch != null) inBatch.value = b.par.batch;
    }
  }
  function isiVersiKet() {
    const o = selVersi.options[selVersi.selectedIndex];
    versiKet.textContent = o ? (o.dataset.ket || '') : '';
  }
  selBackend.addEventListener('change', isiBackendPar);
  selVersi.addEventListener('change', isiVersiKet);

  async function muatBahan() {
    const r = await send(`/api/aksi/latih/bahan?ds=${EDS}`);
    if (!r || !r.ok) return;
    BACKEND = r.backend || {};
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
      o.textContent = `v${v.nomor} · ${v.n || 0} klip · ${v.kelas || 0} kelas`;
      o.dataset.ket = (j.train != null || j.valid != null)
        ? `train ${j.train || 0} / valid ${j.valid || 0}` : '';
      selVersi.appendChild(o);
    });
    const adaSiap = siap.length > 0;
    const adaVersi = (r.versi || []).length > 0;
    if (kepalaEl) kepalaEl.hidden = !adaSiap;
    if (noSiap) noSiap.hidden = adaSiap;
    if (adaSiap) { isiBackendPar(); isiVersiKet(); }
    btnMulai.disabled = !(adaSiap && adaVersi);
    if (adaSiap && !adaVersi) ketEl.textContent =
      'Belum ada versi dataset aksi. Buat versi dulu sebelum melatih.';
  }

  el('ak-tr-baru').addEventListener('click', () => {
    if (kepalaEl) kepalaEl.hidden = true;
    form.hidden = false;
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  el('ak-tr-tutup').addEventListener('click', () => {
    form.hidden = true;
    if (kepalaEl) kepalaEl.hidden = false;
  });

  btnMulai.addEventListener('click', async () => {
    if (!selBackend.value || !selVersi.value) { toast('Pilih backend & versi'); return; }
    btnMulai.disabled = true;
    majuEl.hidden = false; majuEl.textContent = 'Memulai…';
    const r = await send(`/api/aksi/latih/mulai?ds=${EDS}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        versi: parseInt(selVersi.value, 10), backend: selBackend.value,
        nama: (el('ak-tr-nama').value || '').trim() || undefined,
        catatan: (el('ak-tr-catatan').value || '').trim() || undefined,
        par: { epochs: parseInt(inEpochs.value, 10) || undefined,
          batch: parseInt(inBatch.value, 10) || undefined },
      }),
    });
    if (!r || !r.ok) {
      toast((r && r.error) || 'gagal memulai'); majuEl.textContent = (r && r.error) || '';
      btnMulai.disabled = false; return;
    }
    toast(`Training A${r.nomor} dimulai`);
    majuEl.hidden = true;
    form.hidden = true; if (kepalaEl) kepalaEl.hidden = false;
    el('ak-tr-nama').value = ''; el('ak-tr-catatan').value = '';
    if (!pollTr) pollTr = setInterval(pantauTr, 3000);
    pantauTr();
  });

  muatBahan().then(pantauTr);
} else {
  pantauTr();
}

})();
