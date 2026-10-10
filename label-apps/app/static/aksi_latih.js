'use strict';

/*
 * Tab TRAINING untuk projek VIDEO (classifier aksi) — padanan /latih gambar,
 * untuk 3 backend klip (VideoMAE / SlowFast / PoseC3D) + Uji produksi
 * (confusion matrix). Dipisah dari halaman Label Aksi supaya alur video bertab
 * seperti image. Rutenya sudah ada: /api/aksi/latih/{bahan,daftar,kemajuan,
 * mulai,batal}, /aksi/latih/bobot, /api/aksi/eval/{mulai,kemajuan}, /aksi/eval/gambar.
 *
 * send() + toast() global dari app.js.
 */

const D = JSON.parse(document.getElementById('akl-awal').textContent);
const EDS = encodeURIComponent(D.ds);
const el = id => document.getElementById(id);

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
      'Belum ada versi dataset aksi. Buat versi dulu sebelum melatih.';
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
        + (m ? `, klip ${m.sudah}/${m.total}` : '...');
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
    const ak = mk.akurasi != null ? (mk.akurasi * 100).toFixed(1) + '%' : '-';
    const rk = mk.akurasi_rerata_kelas != null
      ? (mk.akurasi_rerata_kelas * 100).toFixed(1) + '%' : '-';
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
        `${aktif.backend_nama || ''} ${aktif.keadaan}, epoch `
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
