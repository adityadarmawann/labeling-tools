'use strict';

/*
 * Tab VERSI untuk projek VIDEO (classifier aksi) — padanan halaman /versi
 * gambar, tetapi untuk KLIP. Dipisah dari halaman Label Aksi supaya alur video
 * punya tab sendiri seperti image (Unggah -> Anotasi -> Dataset -> Versi ->
 * Training), bukan menumpuk di satu halaman.
 *
 * Isi: daftar versi beku (.versi/vN/) yang bisa diunduh, tombol Buat versi
 * (aug+balance lewat klip_olah), dan Impor dataset jadi (.zip / path server).
 * Semua rutenya sudah ada: /api/aksi/versi/{mulai,kemajuan,batal,unduh} +
 * /api/aksi/impor + /api/aksi/impor-path + /api/aksi/impor/kemajuan.
 *
 * send() + toast() global dari app.js.
 */

const D = JSON.parse(document.getElementById('akv-awal').textContent);
const EDS = encodeURIComponent(D.ds);
const el = id => document.getElementById(id);

// -- daftar versi (unduhan arsip klip folder-per-kelas) ----------------------
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
// memulai; kemajuan di-poll (build bisa lama).
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

// -- impor dataset klip jadi (pemilik/Editor: unggah .zip; admin: path server) --
// Keduanya mendaftarkan dataset JADI sebagai versi tanpa proses ulang, dan
// berbagi kemajuan (kunci sama) -> satu poll. Elemen dirender bersyarat di
// templat (unggah: pemilik/Editor; path: admin), jadi bisa saja tak ada.
{
  const impFile = el('ak-impor-file');
  const pathBtn = el('ak-impor-path-btn');
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
          versiData.unshift({ nomor: k.nomor, n: rk.n || 0,
            kelas: rk.kelas || 0, jumlah: rk.jumlah || {} });
          renderVersi();
          toast(`Versi v${k.nomor} diimpor`);
        }
      }
    }

    function mulaiPoll() {
      if (!impPoll) impPoll = setInterval(impPantau, 1000);
      impPantau();
    }

    // Unggah .zip (data di komputermu).
    if (impFile) {
      impFile.addEventListener('change', async () => {
        const f = impFile.files && impFile.files[0];
        if (!f) return;
        impMaju.hidden = false;
        impMaju.textContent = `Mengunggah ${(f.size / 1048576).toFixed(0)} MB…`;
        const r = await send(`/api/aksi/impor?ds=${EDS}`, { method: 'POST', body: f });
        impFile.value = '';
        if (!r || !r.ok) {
          impMaju.textContent = (r && r.error) ? r.error : 'gagal';
          toast((r && r.error) || 'gagal impor');
          return;
        }
        mulaiPoll();
      });
    }

    // Path server (admin): data sudah ada di mesin server, tak perlu unggah.
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
        if (!r || !r.ok) {
          impMaju.textContent = (r && r.error) ? r.error : 'gagal';
          toast((r && r.error) || 'gagal impor');
          return;
        }
        mulaiPoll();
      });
    }
  }
}
