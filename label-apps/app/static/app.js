'use strict';

function toast(m) {
  const t = document.getElementById('t');
  t.textContent = m;
  t.setAttribute('data-on', '');
  setTimeout(() => t.removeAttribute('data-on'), 2200);
}

// Dua bentuk galat bisa datang dari server: {ok:false,error} dari endpoint
// sendiri, dan {detail} dari HTTPException FastAPI (401 sesi habis, 403 bukan
// dari mesin server). Disatukan di sini supaya pemanggil tidak perlu peduli.
async function send(url, opts) {
  const r = await fetch(url, opts);
  let j = {};
  try { j = await r.json(); } catch (e) { /* bukan JSON */ }
  if (r.status === 401) {
    toast(j.detail || 'Sesi habis, masuk lagi');
    setTimeout(() => location.href = '/login', 900);
    return { ok: false, error: j.detail || 'sesi habis' };
  }
  if (typeof j.ok === 'boolean') return j;
  if (j.detail) return { ok: false, error: j.detail };
  return { ok: r.ok, error: r.ok ? null : 'galat ' + r.status };
}

const post = (url) => send(url, { method: 'POST' });

// Teks dari server sering memuat nama berkas milik pemakai, dan nama berkas
// boleh berisi '<'. Dipakai setiap kali teks itu masuk lewat innerHTML.
function esc(s) {
  const d = document.createElement('div');
  d.textContent = String(s == null ? '' : s);
  return d.innerHTML;
}

/*
 * Tema tampilan — padanan menu Theme (label_widget.py:715-750).
 *
 * "sistem" sengaja TIDAK menstempel apa pun ke <html>: pada keadaan itu yang
 * menentukan adalah prefers-color-scheme, dan stempel apa pun justru
 * mengunci pilihan sistemnya.
 */
const KUNCI_TEMA = 'labelapp_tema';

function pasangTema(nilai) {
  if (nilai === 'dark' || nilai === 'light') document.documentElement.dataset.theme = nilai;
  else delete document.documentElement.dataset.theme;
  try { localStorage.setItem(KUNCI_TEMA, nilai); } catch (e) { /* mode privat */ }
}

document.addEventListener('DOMContentLoaded', () => {
  const s = document.getElementById('tema');
  if (!s) return;
  let awal = 'system';
  try { awal = localStorage.getItem(KUNCI_TEMA) || 'system'; } catch (e) { /* abai */ }
  s.value = awal;
  pasangTema(awal);
  s.onchange = () => pasangTema(s.value);
});

// ---------------------------------------------------------------- papan periksa

async function openIn(p, btn) {
  const old = btn.textContent;
  btn.textContent = 'Membuka...';
  try {
    const j = await post('/open?path=' + encodeURIComponent(p));
    toast(j.ok ? ('AnyLabeling: ' + (j.msg || 'dibuka')) : 'Gagal: ' + j.error);
  } catch (e) {
    toast('Gagal menghubungi server');
  }
  btn.textContent = old;
}

async function markbg(p, on) {
  const j = await post((on ? '/markbg' : '/unmarkbg') + '?path=' + encodeURIComponent(p));
  toast(j.ok ? j.msg : 'Gagal: ' + j.error);
  if (j.ok) setTimeout(() => location.reload(), 450);
}

async function rescan() {
  // Terukur 5,9 detik pada dataset 11.319 gambar. Toast hilang jauh sebelum
  // itu, jadi sisa waktunya berlalu tanpa tanda apa pun.
  const pr = Progres.mulai('Memindai ulang dataset');
  pr.taktentu('membaca berkas anotasi…');
  try {
    const j = await post('/rescan');
    if (j.ok) { pr.selesai(); location.reload(); }
    else { pr.gagal(j.error); toast('Gagal: ' + j.error); }
  } catch (e) {
    pr.gagal('Gagal menghubungi server');
  }
}

// Panah kiri/kanan untuk pindah gambar di tampilan besar.
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  const nav = document.querySelectorAll('a.chip[href^="/view"]');
  if (e.key === 'ArrowLeft' && nav.length) location.href = nav[0].href;
  if (e.key === 'ArrowRight' && nav.length) location.href = nav[nav.length - 1].href;
});

// ---------------------------------------------------------------- pilih dataset

async function setsrc(p) {
  if (!p) { toast('Path masih kosong'); return; }
  const pr = Progres.mulai('Membuka dataset');
  pr.taktentu('memindai isinya…');
  try {
    const j = await post('/setsrc?path=' + encodeURIComponent(p));
    if (j.ok) { pr.selesai(); location.href = '/'; }
    else { pr.gagal(j.error); toast(j.error); }
  } catch (e) {
    pr.gagal('Gagal menghubungi server');
  }
}

// Item dataset membawa path di data-path. Dipasang lewat delegasi supaya path
// tidak perlu disisipkan ke dalam string JavaScript di atribut HTML — cara itu
// pernah membuat seluruh daftar tidak bisa diklik karena tanda kutipnya
// menutup atribut onclick lebih awal.
document.addEventListener('click', ev => {
  // Kartu dataset bersama. Kelasnya berganti dari .ds saat halaman
  // pilih dirombak jadi kartu; pemilihnya harus ikut, kalau tidak
  // seluruh daftar bersama diam-diam berhenti bisa diklik.
  const a = ev.target.closest && ev.target.closest('a[data-path]');
  if (!a) return;
  ev.preventDefault();
  setsrc(a.dataset.path);
});

// ---------------------------------------------------------------- unggah

// Tiap berkas dikirim satu-satu lewat PUT dengan bodi mentah. Tanpa multipart,
// jadi berkas besar tidak ditahan di memori server, progres terhitung per
// berkas, dan satu berkas gagal tidak menggagalkan seluruh batch.
// Harus sama dengan IMG_EXT + ANN_EXT + META_EXT + ARSIP_EXT di app/config.py.
// Kalau daftar ini tertinggal, berkas yang sebenarnya diterima server justru
// disaring di sini dan tidak pernah terkirim — persis yang dulu terjadi pada
// data.yaml, sehingga nama kelas dataset Roboflow hilang tanpa pesan apa pun.
const UP_EXT = ['.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff',
                '.json', '.txt', '.yaml', '.yml', '.zip'];
const ARSIP_EXT = ['.zip'];
const adalahArsip = n => ARSIP_EXT.some(e => n.toLowerCase().endsWith(e));

// Nama yang dikirim ke server memuat subfolder kalau ada. Struktur itu
// bermakna: pemindai mengenali dataset YOLO dari adanya images/ dan labels/,
// jadi meratakan semuanya membuat dataset yang diunggah tidak terbaca.
const namaKirim = f => f.webkitRelativePath || f.relPath || f.name;

/* Folder yang di-drop tidak muncul di dataTransfer.files — hanya entri
   foldernya. Isinya harus ditelusuri sendiri lewat webkitGetAsEntry. */
async function bacaEntri(entry, awalan = '') {
  if (entry.isFile) {
    return new Promise(res => entry.file(f => {
      f.relPath = awalan + entry.name;
      res([f]);
    }, () => res([])));
  }
  const reader = entry.createReader();
  const semua = [];
  // readEntries hanya mengembalikan sebagian per panggilan; harus diulang
  // sampai kosong, kalau tidak folder besar terpotong diam-diam.
  for (;;) {
    const batch = await new Promise(res => reader.readEntries(res, () => res([])));
    if (!batch.length) break;
    for (const e of batch) semua.push(...await bacaEntri(e, awalan + entry.name + '/'));
  }
  return semua;
}

async function dariDrop(dt) {
  const item = [...(dt.items || [])];
  const entri = item.map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (!entri.length) return [...dt.files];
  const out = [];
  for (const e of entri) out.push(...await bacaEntri(e));
  return out;
}


/*
 * Menu saringan kelas. Klik di DALAM menunya tidak menutupnya — memilih tiga
 * kelas mustahil kalau menunya hilang begitu centang pertama disentuh.
 */
(() => {
  const m = document.getElementById('menu-kelas');
  if (!m) return;
  document.getElementById('kelas-tombol').onclick = ev => {
    ev.stopPropagation();
    m.toggleAttribute('data-buka');
  };
  m.addEventListener('click', ev => ev.stopPropagation());

  // Penanda `data-on` pada segmen yang aktif. Dipasang lewat JS, bukan
  // selektor :has(), supaya tampilannya tidak bergantung pada dukungan
  // peramban terhadap :has() — yang belum lama ada.
  const seg = [...m.querySelectorAll('.seg-opt')];
  const tandai = () => {
    let aktif = 'atau';
    seg.forEach(o => {
      const c = o.querySelector('input');
      o.toggleAttribute('data-on', c.checked);
      if (c.checked) aktif = c.value;
    });
    // Keterangannya ikut berubah seketika. Tanpa ini ia baru menyusul setelah
    // Terapkan ditekan, dan sampai saat itu isinya berlawanan dengan segmen
    // yang tampak aktif.
    m.querySelectorAll('[data-ket]').forEach(n => {
      n.hidden = n.dataset.ket !== aktif;
    });
    // Latar dan Belum dilabeli tidak berlaku pada mode "semuanya": gambar
    // berobjek menurut definisinya bukan latar, jadi menggabungkannya selalu
    // memberi nol. Disembunyikan DAN dilepas centangnya — centang yang
    // tersembunyi tetapi masih terkirim adalah saringan yang tak terlihat.
    const tanpa = m.querySelector('.kelas-tanpa');
    if (tanpa) {
      tanpa.hidden = aktif === 'dan';
      if (aktif === 'dan') {
        tanpa.querySelectorAll('input[type=checkbox]').forEach(c => { c.checked = false; });
      }
    }
  };
  seg.forEach(o => o.querySelector('input').addEventListener('change', tandai));
  tandai();
  document.addEventListener('click', ev => {
    if (!m.contains(ev.target)) m.removeAttribute('data-buka');
  });
})();


/*
 * Progres bersama.
 * ================
 * Satu bentuk untuk seluruh aplikasi, dan satu jaminan: tidak ada pekerjaan
 * panjang yang berjalan tanpa terlihat. Panel bisa saja tertutup atau
 * halaman sudah tergulir jauh, jadi selain bilah di tempatnya sendiri ada
 * penanda tetap di pojok layar selama masih ada yang berjalan.
 *
 *   const p = Progres.mulai('Menyalin berkas', {di: elemen});
 *   p.set(0.42, '4.200 dari 10.000');   // atau p.taktentu('Memindai…')
 *   p.selesai('Selesai, 33 dtk');
 *   p.gagal('Gagal menghubungi server');
 */
const Progres = (() => {
  const jalan = new Set();        // yang masih berjalan
  const arsip = [];               // yang sudah selesai/gagal, belum dibuang
  let wadah = null, pil = null, panel = null, daftar = null;
  let liveSopan = null, liveTegas = null;
  let terbuka = false, detik = null, kenalan = null, rafMinta = false;
  let noUrut = 0;

  const pad = v => String(v).padStart(2, '0');
  const jam = d => d >= 60 ? `${Math.floor(d / 60)}:${pad(Math.round(d % 60))}`
                           : `${Math.round(d)} dtk`;
  const esc = t => String(t == null ? '' : t).replace(/[&<>"']/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

  function ucap(teks, tegas) {
    const el = tegas ? liveTegas : liveSopan;
    if (!el) return;
    // Pesan yang sama dua kali berturut-turut diabaikan pembaca layar kalau
    // isinya tidak sempat kosong lebih dulu.
    el.textContent = '';
    setTimeout(() => { el.textContent = teks; }, 60);
  }

  function bangun() {
    if (wadah) return;
    wadah = document.createElement('div');
    wadah.id = 'kerja-global';
    // Pil DULU di DOM, panel sesudahnya; column-reverse yang membalik
    // urutan visualnya. Disengaja: mata melihat panel di atas pil, tapi Tab
    // mencapai pil dulu lalu isi panelnya.
    wadah.innerHTML =
      '<button class="kg-pil" type="button" aria-expanded="false"'
      + ' aria-controls="kg-panel">'
      + '<span class="kg-cincin" aria-hidden="true"></span>'
      + '<span class="kg-judul"></span><span class="kg-teks"></span></button>'
      + '<div class="kg-panel" id="kg-panel" role="group"'
      + ' aria-label="Pekerjaan berjalan" hidden>'
      + '<div class="kg-kepala"><h4>Pekerjaan berjalan</h4>'
      + '<button class="kg-tutup" type="button"'
      + ' aria-label="Tutup daftar pekerjaan">×</button></div>'
      + '<ul class="kg-daftar"></ul></div>';
    document.body.appendChild(wadah);
    pil = wadah.querySelector('.kg-pil');
    panel = wadah.querySelector('.kg-panel');
    daftar = wadah.querySelector('.kg-daftar');

    liveSopan = document.createElement('div');
    liveSopan.className = 'kg-live';
    liveSopan.setAttribute('aria-live', 'polite');
    liveSopan.setAttribute('aria-atomic', 'true');
    liveTegas = document.createElement('div');
    liveTegas.className = 'kg-live';
    liveTegas.setAttribute('role', 'alert');
    document.body.append(liveSopan, liveTegas);

    pil.addEventListener('click', () => setBuka(!terbuka));
    wadah.querySelector('.kg-tutup').addEventListener('click', () => {
      setBuka(false);
      pil.focus();
    });
    daftar.addEventListener('click', ev => {
      const b = ev.target.closest('.kg-buang');
      if (!b) return;
      const id = b.closest('.kg-item').dataset.id;
      const i = arsip.findIndex(x => String(x.id) === id);
      if (i >= 0) arsip.splice(i, 1);
      minta();
    });
    document.addEventListener('keydown', ev => {
      if (ev.key === 'Escape' && terbuka && wadah.contains(document.activeElement)) {
        setBuka(false);
        pil.focus();
      }
    });
    // Klik di luar menutup panel TANPA memindahkan fokus: kalau fokus sedang
    // di kanvas, merebutnya di tengah menggambar jauh lebih mengganggu
    // daripada panel yang terbuka.
    document.addEventListener('click', ev => {
      if (terbuka && !wadah.contains(ev.target)) setBuka(false);
    });
  }

  function setBuka(v) {
    terbuka = v;
    panel.hidden = !v;
    pil.setAttribute('aria-expanded', String(v));
    if (v) {
      // Membuka panel = mengakui kegagalan. Sejak saat itu barisnya punya
      // batas waktu; sebelum dilihat, ia menetap selamanya.
      const t = Date.now();
      arsip.forEach(a => { if (a.keadaan === 'gagal' && !a.dilihat) a.dilihat = t; });
    }
    minta();
  }

  function minta() {
    if (rafMinta) return;
    rafMinta = true;
    requestAnimationFrame(() => { rafMinta = false; gambar(); });
  }

  function baris() {
    // Yang masih berjalan di atas, dari yang PALING TUA. Versi lama
    // menampilkan yang terakhir dimulai, sehingga ekspor 13 menit hilang dari
    // layar begitu orang menekan "Pindai ulang" yang cuma 6 detik.
    const aktif = [...jalan].sort((a, b) => a.mulai - b.mulai);
    return aktif.concat(arsip.slice().sort((a, b) => b.tutupPada - a.tutupPada));
  }

  function gambar() {
    if (!wadah) return;
    const semua = baris();
    if (!semua.length) {
      wadah.removeAttribute('data-on');
      wadah.removeAttribute('data-keadaan');
      if (terbuka) setBuka(false);
      return;
    }
    wadah.setAttribute('data-on', '');

    const aktif = semua.filter(x => jalan.has(x));
    const gagal = arsip.filter(a => a.keadaan === 'gagal');
    const cincin = pil.querySelector('.kg-cincin');
    const teks = pil.querySelector('.kg-teks');
    const judul = pil.querySelector('.kg-judul');

    let keadaan = 'jalan', nilai = null, label, sebut;
    if (aktif.length) {
      const berpersen = aktif.filter(x => x.persen != null);
      nilai = berpersen.length === aktif.length
        ? berpersen.reduce((s, x) => s + x.persen, 0) / aktif.length : null;
      const utama = aktif[0];
      judul.textContent = utama.judul;
      label = aktif.length > 1 ? `${aktif.length} kerja`
            : nilai != null ? `${Math.round(nilai * 100)}%`
            : jam((Date.now() - utama.mulai) / 1000);
      sebut = aktif.length > 1
        ? `${aktif.length} pekerjaan berjalan. Buka daftar pekerjaan.`
        : `${utama.judul}${nilai != null ? ', ' + Math.round(nilai * 100)
            + ' persen' : ''}. Buka daftar pekerjaan.`;
    } else if (gagal.length) {
      keadaan = 'gagal';
      nilai = 1;
      judul.textContent = gagal[0].judul;
      label = gagal.length > 1 ? `${gagal.length} gagal` : 'Gagal';
      sebut = `${gagal.length} pekerjaan gagal. Buka daftar untuk melihatnya.`;
    } else {
      keadaan = 'selesai';
      nilai = 1;
      judul.textContent = semua[0].judul;
      label = 'Selesai';
      sebut = 'Pekerjaan selesai.';
    }
    wadah.setAttribute('data-keadaan', keadaan);
    cincin.toggleAttribute('data-tak-tentu', nilai == null);
    cincin.style.setProperty('--v', nilai == null ? 0 : (nilai * 100).toFixed(0));
    teks.textContent = label;
    pil.title = sebut;
    pil.setAttribute('aria-label', sebut);

    if (terbuka) gambarDaftar(semua);
  }

  function gambarDaftar(semua) {
    const ada = new Map(
      [...daftar.children].map(li => [li.dataset.id, li]));
    semua.forEach((s, i) => {
      let li = ada.get(String(s.id));
      if (!li) {
        li = document.createElement('li');
        li.className = 'kg-item';
        li.dataset.id = s.id;
        li.innerHTML =
          `<div class="kg-baris"><span class="kg-nama" id="kg-n${s.id}"></span>`
          + '<b class="kg-angka"></b></div>'
          + `<div class="prog" data-on role="progressbar" aria-labelledby="kg-n${s.id}"`
          + ' aria-valuemin="0" aria-valuemax="100"><i></i></div>'
          + '<div class="kg-rinci"></div>';
      }
      ada.delete(String(s.id));
      const hidup = jalan.has(s);
      li.dataset.keadaan = hidup ? 'jalan' : s.keadaan;
      li.querySelector('.kg-nama').textContent = s.judul;
      const dtk = ((hidup ? Date.now() : s.tutupPada) - s.mulai) / 1000;
      li.querySelector('.kg-angka').textContent = !hidup
        ? (s.keadaan === 'gagal' ? 'Gagal' : jam(dtk))
        : (s.persen != null ? `${Math.round(s.persen * 100)}%` : jam(dtk));
      const bar = li.querySelector('.prog');
      const isi = bar.querySelector('i');
      bar.toggleAttribute('data-tak-tentu', hidup && s.persen == null);
      isi.style.width = hidup
        ? (s.persen == null ? '' : (s.persen * 100).toFixed(1) + '%') : '100%';
      // Tanpa aria-valuenow berarti "tak tentu" menurut ARIA. valuenow="0"
      // justru dibacakan "0 persen" dan terdengar macet.
      if (hidup && s.persen == null) bar.removeAttribute('aria-valuenow');
      else bar.setAttribute('aria-valuenow',
                            String(Math.round((s.persen == null ? 1 : s.persen) * 100)));
      bar.setAttribute('aria-valuetext', hidup && s.persen == null
        ? `sedang berjalan, ${jam(dtk)}` : `${li.querySelector('.kg-angka').textContent}`
          + (s.rinci ? ', ' + s.rinci : ''));
      li.querySelector('.kg-rinci').textContent = s.rinci || '';
      if (!hidup && !li.querySelector('.kg-buang')) {
        const b = document.createElement('button');
        b.className = 'kg-buang';
        b.type = 'button';
        b.setAttribute('aria-label', 'Buang: ' + s.judul);
        b.innerHTML = '×';
        li.querySelector('.kg-baris').appendChild(b);
      }
      if (daftar.children[i] !== li) daftar.insertBefore(li, daftar.children[i] || null);
    });
    ada.forEach(li => li.remove());
  }

  function detakMulai() {
    if (detik) return;
    detik = setInterval(() => {
      // Detak hidup untuk pekerjaan tanpa persentase: tanpa ini orang menutup
      // tab karena mengira macet.
      const now = Date.now();
      jalan.forEach(s => {
        if (s.persen == null && now - (s.ucapPada || s.mulai) > 60000) {
          s.ucapPada = now;
          ucap(`${s.judul} masih berjalan, ${jam((now - s.mulai) / 1000)}.`);
        }
      });
      bersihkanArsip();
      minta();
    }, 1000);
  }

  function detakBerhenti() {
    // Jangan biarkan hidup di tab yang menganggur.
    if (detik && !jalan.size && !arsip.length) { clearInterval(detik); detik = null; }
  }

  function bersihkanArsip() {
    const now = Date.now();
    let ubah = false;
    for (let i = arsip.length - 1; i >= 0; i--) {
      const a = arsip[i];
      const batas = a.keadaan === 'gagal'
        ? (a.dilihat ? a.dilihat + 12000 : Infinity)   // menetap sampai dilihat
        : a.tutupPada + 5000;
      if (now > batas) { arsip.splice(i, 1); ubah = true; }
    }
    if (ubah) minta();
    detakBerhenti();
  }

  class Sesi {
    constructor(judul, opsi) {
      this.id = ++noUrut;
      this.judul = judul;
      this.persen = null;
      this.rinci = '';
      this.mulai = Date.now();
      this.tonggak = 0;
      this.el = null;
      bangun();
      const di = opsi && opsi.di;
      if (di) {
        this.el = document.createElement('div');
        this.el.className = 'pr-kotak';
        this.el.innerHTML = '<div class="prog" data-on><i></i></div>'
          + '<div class="pr-teks"><span></span><b></b></div>';
        di.innerHTML = '';
        di.appendChild(this.el);
      }
      jalan.add(this);
      detakMulai();
      ucap(`${judul} dimulai.`);
      // Perkenalan: pil melebar menyebut namanya, lalu mengecil sendiri.
      // Dilewati kalau panelnya memang sedang terbuka.
      if (!terbuka) {
        wadah.setAttribute('data-kenalan', '');
        clearTimeout(kenalan);
        kenalan = setTimeout(() => wadah.removeAttribute('data-kenalan'), 3500);
      }
      minta();
    }
    _tulis() {
      if (this.el) {
        const bar = this.el.querySelector('.prog');
        bar.toggleAttribute('data-tak-tentu', this.persen == null);
        bar.querySelector('i').style.width =
          this.persen == null ? '' : (this.persen * 100).toFixed(1) + '%';
        this.el.querySelector('.pr-teks span').textContent = this.rinci;
        this.el.querySelector('.pr-teks b').textContent =
          this.persen == null ? '' : Math.round(this.persen * 100) + '%';
      }
      minta();
    }
    set(persen, rinci) {
      this.persen = Math.max(0, Math.min(1, persen));
      if (rinci != null) this.rinci = rinci;
      // Tonggak 25/50/75 saja. Mengumumkan tiap persen akan mengubur seluruh
      // percakapan lain pada ekspor yang berjalan belasan menit.
      const t = Math.floor(this.persen * 4) * 25;
      if (t > this.tonggak && t < 100) {
        this.tonggak = t;
        ucap(`${this.judul}, ${t} persen.`);
      }
      this._tulis();
      return this;
    }
    taktentu(rinci) {
      this.persen = null;
      if (rinci != null) this.rinci = rinci;
      this._tulis();
      return this;
    }
    _tutup(keadaan, pesan) {
      if (!jalan.delete(this)) return this;
      this.keadaan = keadaan;
      this.tutupPada = Date.now();
      this.rinci = pesan || (keadaan === 'gagal' ? 'Gagal' : 'Selesai');
      const dtk = (this.tutupPada - this.mulai) / 1000;
      if (this.el) {
        this.el.dataset.keadaan = keadaan;
        this.el.querySelector('.prog').removeAttribute('data-tak-tentu');
        this.el.querySelector('.prog i').style.width = '100%';
        this.el.querySelector('.pr-teks span').textContent = this.rinci;
        this.el.querySelector('.pr-teks b').textContent =
          keadaan === 'gagal' ? 'gagal' : jam(dtk);
      }
      arsip.push(this);
      if (keadaan === 'gagal') ucap(`${this.judul} gagal: ${this.rinci}`, true);
      else ucap(`${this.judul} selesai, ${jam(dtk)}.`);
      minta();
      return this;
    }
    selesai(pesan) { return this._tutup('selesai', pesan || 'Selesai'); }
    gagal(pesan) { return this._tutup('gagal', pesan || 'Gagal'); }
    buang() {
      jalan.delete(this);
      const i = arsip.indexOf(this);
      if (i >= 0) arsip.splice(i, 1);
      if (this.el) this.el.remove();
      minta();
      detakBerhenti();
      return this;
    }
  }

  return {
    mulai: (judul, opsi) => new Sesi(judul, opsi || {}),
    adaYangJalan: () => jalan.size > 0,
    _buka: v => { bangun(); setBuka(v); },      // dipakai uji e2e
  };
})();

// ---------------------------------------------------------------- menu Ekspor

// Ringkasan diminta saat menu dibuka, bukan saat halaman dimuat: menghitung
// objek berarti menyusun ulang seluruh baris label, dan itu tidak perlu
// dilakukan untuk orang yang tidak berniat mengekspor.
(() => {
  const m = document.getElementById('menu-ekspor');
  if (!m) return;
  const tombol = document.getElementById('ekspor-tombol');
  const info = document.getElementById('ekspor-info');
  const kotak = ['s-train', 's-valid', 's-test'].map(i => document.getElementById(i));
  const rasio = () => kotak.map(k => k.value || '0').join(',');

  // Menunggui unduhan yang tidak punya kejadian sendiri.
  //
  // <a href> tidak memberi tahu apa pun setelah diklik. ZIP dibentuk di server
  // lebih dulu (paragon 1,27 GB butuh 33 detik; 11 ribu gambar sekitar 13
  // menit), dan selama itu layar diam sehingga tombolnya tampak rusak. Server
  // menaruh cookie saat balasannya dikirim, dan cookie itu yang ditunggu.
  const unduhInfo = document.getElementById('unduh-pakai');
  let unduhAsli = '';
  function tungguiUnduhan(a) {
    const tanda = String(Date.now());
    const u = new URL(a.href, location.origin);
    u.searchParams.set('tanda', tanda);
    a.href = u.pathname + '?' + u.searchParams.toString();
    if (!unduhInfo) return;
    if (!unduhAsli) unduhAsli = unduhInfo.innerHTML;
    // Server membentuk seluruh ZIP lebih dulu, jadi persentasenya tidak bisa
    // diketahui — yang bisa cuma menandai bahwa ia masih bekerja.
    const pr = Progres.mulai('Menyiapkan ZIP', {di: unduhInfo});
    pr.taktentu('jangan tutup tab ini');
    const mulai = Date.now();
    const pantau = setInterval(() => {
      const siap = document.cookie.includes('unduh_siap=' + tanda);
      const lewat = (Date.now() - mulai) / 1000;
      if (siap) {
        clearInterval(pantau);
        document.cookie = 'unduh_siap=; Max-Age=0; path=/';
        pr.selesai('ZIP siap, unduhan dimulai');
        setTimeout(() => { unduhInfo.innerHTML = unduhAsli; }, 8000);
      } else if (lewat > 1800) {
        clearInterval(pantau);
        pr.gagal('Belum selesai setelah 30 menit; periksa log server');
      } else {
        pr.taktentu(`${lewat.toFixed(0)} dtk, jangan tutup tab ini`);
      }
    }, 500);
  }

  // Tautan unduh membawa rasio yang sedang diketik, supaya angka di layar dan
  // isi ZIP selalu cocok.
  function perbaruiTautan() {
    m.querySelectorAll('a[href^="/ekspor"]').forEach(a => {
      const u = new URL(a.href, location.origin);
      u.searchParams.set('split', rasio());
      a.href = u.pathname + '?' + u.searchParams.toString();
    });
  }

  async function muatRingkasan() {
    info.textContent = 'menghitung…';
    try {
      const r = await fetch('/api/ekspor/ringkasan?format=yolo-seg&split='
                            + encodeURIComponent(rasio()));
      const j = await r.json();
      if (!j.ok) { info.textContent = 'Gagal: ' + (j.error || j.detail); return; }
      const s = j.split, pc = j.persen;
      const n = v => v.toFixed(1).replace('.', ',');
      // Baris pertama: persentase yang benar-benar tercapai, karena angka yang
      // diminta di kotak atas tidak selalu sama persis.
      // Kalau datasetnya sudah punya split sendiri, pembagian itu yang dipakai
      // dan rasio yang diketik tidak berlaku. Itu harus dikatakan, bukan
      // dibiarkan jadi teka-teki kenapa angkanya tidak berubah.
      info.innerHTML =
        (j.split_bawaan
          ? '<b>memakai split asli dataset</b><br>'
          : `<b>nyata: ${n(pc.train)}% : ${n(pc.valid)}% : ${n(pc.test)}%</b><br>`)
        + `train ${s.train} · valid ${s.valid} · test ${s.test}`
        + `, dari ${j.gambar} gambar, ${j.objek} objek, ${j.kelas} kelas`
        + (j.split_bawaan
          ? '<br>Dataset ini sudah terbagi train/valid/test, jadi pembagiannya '
            + 'dipertahankan dan angka rasio di atas tidak dipakai.'
          : '')
        + (j.tanpa_objek ? `<br>${j.tanpa_objek} tanpa objek (contoh negatif)` : '')
        + (j.bentuk_dilewati ? ` · ${j.bentuk_dilewati} bentuk dilewati` : '')
        // Tanpa rencana, splitting-nya cuma mengelompokkan lewat nama berkas
        // dan isi gambarnya tidak pernah dibuka. Itu HARUS terbaca sebelum
        // orang menekan unduh: kalau tidak, satu-satunya penanda bahwa hasil
        // ekspornya bocor adalah tombol yang kebetulan tidak ditekan.
        // Dilipat, tapi baris ringkasnya tetap berwarna peringatan: yang
        // penting terbaca sekilas, penjelasannya sekali klik.
        + (j.rencana ? '' : lipat('peringatan', 'Isi gambar belum diperiksa',
           'Proses splitting ini masih mengandalkan nama berkas saja, sehingga '
           + 'foto yang sebenarnya sama bisa masuk ke data train dan validasi. '
           + 'Akibatnya, hasil validasi bisa terlihat lebih tinggi dari kondisi '
           + 'sebenarnya. Karena itu, sebaiknya jalankan <b>splitting '
           + 'anti-bocor</b> di bawah terlebih dahulu agar pembagian datanya '
           + 'lebih aman dan hasil evaluasinya lebih akurat.'))
        + (!j.split_bawaan ? '' : lipat('peringatan',
           'Memakai pembagian bawaan dataset',
           'Dipakai apa adanya, termasuk kebocorannya kalau ada. Ekspor '
           + 'Roboflow sering membelah per gambar. Menjalankan splitting '
           + 'anti-bocor akan menyatukannya ulang lalu membelah dari nol.'));
      kotak.forEach(k => { if (k) k.disabled = !!j.split_bawaan; });
      // Rencana bertahan di sesi, jadi setelah halaman dimuat ulang keadaannya
      // harus ikut tampil kembali — kalau tidak, tombolnya tampak belum
      // pernah ditekan padahal splitting-nya masih berlaku.
      // Sambungan antara hasil splitting dan tombol unduh harus terlihat.
      // Tanpa ini, orang menjalankan splitting lalu tidak yakin apakah ZIP
      // yang diunduh benar-benar memakainya.
      const up = document.getElementById('unduh-pakai');
      if (up && !up.textContent.includes('ZIP')) {
        up.innerHTML = j.rencana
          ? 'memakai <b>hasil splitting</b> di bawah'
          : 'memakai pembagian cepat berbasis nama berkas';
      }
      if (j.rencana) {
        // Bilahnya hanya disembunyikan kalau splitting-nya tidak dijalankan
        // di sesi layar ini — mis. sesudah halaman dimuat ulang, ketika
        // rencananya datang dari server. Sesudah menekan tombolnya,
        // "Selesai 100%" justru penanda bahwa kerjanya kelar; menyembunyikan
        // bilah tepat saat penuh membuatnya berkedip lalu lenyap.
        if (!sudahJalan) sProg.hidden = true;
        tampilkanRencana(j.rencana);
      }
    } catch (e) {
      info.textContent = 'Gagal menghubungi server';
    }
    perbaruiTautan();
  }

  // Rencana yang sudah jadi mengunci pembagiannya, jadi mengubah angka rasio
  // sesudah itu tidak mengubah apa pun sampai splitting dijalankan ulang.
  // Tanpa diberitahu, kotaknya terlihat seperti tidak berfungsi.
  let rasioSaatJalan = null;
  function periksaRasioBerubah() {
    if (!sLupa || sLupa.hidden) return;
    const beda = rasioSaatJalan && rasio() !== rasioSaatJalan;
    sUlang.hidden = !beda;
  }

  kotak.forEach(k => {
    if (!k) return;
    k.onchange = () => { muatRingkasan(); periksaRasioBerubah(); };
    k.oninput = periksaRasioBerubah;
  });

  // ---- splitting anti-bocor -------------------------------------------
  //
  // Dijalankan sebagai langkah tersendiri karena membaca isi tiap gambar
  // makan waktu (56 ms/gambar terukur), dan karena angkanya harus bisa
  // dibaca SEBELUM ZIP-nya dibuat, bukan sesudah.

  const sJalan = document.getElementById('split-jalan');
  const sBatal = document.getElementById('split-batal');
  const sLupa  = document.getElementById('split-lupa');
  const sProg  = document.getElementById('prog-split');
  const sIsi   = document.getElementById('fill-split');
  const sFase  = document.getElementById('split-fase');
  const sPersen = document.getElementById('split-persen');
  const sHasil = document.getElementById('split-hasil');
  const sUlang = document.getElementById('split-ulang');
  const angka = v => (v || 0).toLocaleString('id-ID');

  // Satu bentuk lipatan untuk semua: ringkasnya terbaca, isinya sekali klik.
  const lipat = (kelas, judul, isi) =>
    `<details class="catatan-lipat ${kelas}"><summary>${judul}</summary>`
    + `<span class="lipat-isi">${isi}</span></details>`;

  function tampilkanRencana(r) {
    if (!r) { sHasil.innerHTML = ''; sLupa.hidden = true; return; }
    sLupa.hidden = false;
    const n = v => v.toFixed(1).replace('.', ',');
    const pindah = (r.dipindah.valid || 0) + (r.dipindah.test || 0);
    const objek = s => Object.values(r.kelas[s] || {}).reduce((a, b) => a + b, 0);
    let h = '<span class="split-angka">'
      + `<b>${angka(r.n_sesi)} sesi pemotretan</b> (kunci per-${r.granularitas}`
      + `, terbesar ${n(r.grup_terbesar_pct)}%)<br>`;
    for (const s of ['train', 'valid', 'test']) {
      h += `${s} <b>${angka(r.jumlah[s])}</b> (${n(r.persen[s])}%)`
         + ` · ${angka(objek(s))} objek<br>`;
    }
    if (pindah) {
      h += `${angka(pindah)} gambar dipindah ke train karena punya kembaran `
         + 'di sana<br>';
    }
    if (r.tanpa_stempel) {
      h += `${angka(r.tanpa_stempel)} berkas tanpa stempel waktu; untuk yang `
         + 'ini hanya isi gambarnya yang menjaga<br>';
    }
    // Ambangnya diukur dari dataset ini sendiri, bukan angka tetap. Disebut
    // supaya angkanya bisa ditelusuri kalau hasilnya terasa aneh.
    const kal = r.kalibrasi;
    if (kal && kal.contoh) {
      h += `ambang kemiripan <b>${r.ambang}</b> dari 256 bit, diukur dari `
         + `${kal.contoh} foto dataset ini`
         + (kal.beda_p1 != null
            ? ` (olah ulang ${Math.round(kal.kembaran_p99)} · `
              + `beda sesi ${Math.round(kal.beda_p1)})`
            : '')
         + '<br>';
    }
    // Kebocoran nol tidak sama dengan angka yang bisa dipercaya, dan bedanya
    // bisa dihitung — jadi dihitung, bukan diserahkan ke firasat.
    const m = r.kemandirian || {};
    if (m.patokan) {
      for (const s of ['valid', 'test']) {
        const b = m[s];
        if (!b || !b.n || b.kemandirian == null) continue;
        const nilai = b.kemandirian;
        const kata = nilai >= 0.95 ? 'semandiri yang data ini bisa'
                   : nilai >= 0.8 ? 'cukup mandiri'
                   : 'masih mirip train';
        h += `${s} <b>${nilai.toFixed(2)}x</b> mandiri (${kata}) · `
           + `${angka(b.n_sesi)} sesi<br>`;
      }
    }
    h += '</span>';
    // Catatannya dilipat, bukan dibuang. Deretan kotak kuning membuat panel
    // ini terbaca seperti daftar kesalahan padahal hasilnya benar — sementara
    // isinya tetap satu-satunya tempat yang menjelaskan kalau angkanya aneh.
    const w = r.peringatan || [];
    if (w.length) {
      h += `<details class="catatan-lipat"><summary>${w.length} catatan `
         + 'tentang hasil ini</summary>'
         + w.map(x => `<span class="split-warn">${x}</span>`).join('')
         + '</details>';
    }
    sHasil.innerHTML = h;
  }

  let sudahJalan = false;
  let pantauSplit = null;
  function hentikanPantau() {
    if (pantauSplit) { clearInterval(pantauSplit); pantauSplit = null; }
  }

  sJalan.onclick = async ev => {
    ev.preventDefault();
    sudahJalan = true;
    sJalan.disabled = true;
    sBatal.hidden = false;
    sLupa.hidden = true;
    sHasil.innerHTML = '';
    sProg.hidden = false;
    sIsi.style.width = '0%';
    sPersen.textContent = '0%';
    sFase.textContent = 'Menyiapkan…';
    // Kemajuannya ditanyakan berkala: /api/split/jalankan menggantung sampai
    // selesai, jadi tidak bisa melaporkan apa pun di tengah jalan.
    pantauSplit = setInterval(async () => {
      let k;
      try { k = await (await fetch('/api/split/kemajuan')).json(); } catch (e) { return; }
      if (!k || k.persen == null) return;
      sIsi.style.width = k.persen.toFixed(1) + '%';
      sPersen.textContent = Math.round(k.persen) + '%';
      sFase.textContent = k.fase === 'dhash' && k.total
        ? `${k.fase_nama}: ${angka(k.n)} dari ${angka(k.total)}`
        : (k.fase_nama || '…');
    }, 400);
    try {
      const j = await post('/api/split/jalankan?split=' + encodeURIComponent(rasio()));
      if (!j.ok) {
        sHasil.innerHTML = j.batal
          ? '<span class="split-warn">Dihentikan. Splitting cepat berbasis '
            + 'nama berkas tetap dipakai.</span>'
          : `<span class="split-warn">Gagal: ${j.error || ''}</span>`;
        return;
      }
      sIsi.style.width = '100%';
      sPersen.textContent = '100%';
      sFase.textContent = 'Selesai';
      rasioSaatJalan = rasio();
      sUlang.hidden = true;
      tampilkanRencana(j);
      // Ringkasan di atas masih menyebut pembagian yang lama.
      muatRingkasan();
    } catch (e) {
      sHasil.innerHTML = '<span class="split-warn">Gagal menghubungi server</span>';
    } finally {
      hentikanPantau();
      sJalan.disabled = false;
      sBatal.hidden = true;
    }
  };

  sBatal.onclick = async ev => {
    ev.preventDefault();
    sBatal.disabled = true;
    sFase.textContent = 'Menghentikan…';
    try { await post('/api/split/batal'); } catch (e) {}
    sBatal.disabled = false;
  };

  sLupa.onclick = async ev => {
    ev.preventDefault();
    try { await post('/api/split/lupakan'); } catch (e) { return; }
    sHasil.innerHTML = '';
    sudahJalan = false;
    rasioSaatJalan = null;
    sProg.hidden = true;
    sLupa.hidden = true;
    sUlang.hidden = true;
    muatRingkasan();
  };

  m.querySelectorAll('a.unduh').forEach(a => {
    a.addEventListener('click', () => tungguiUnduhan(a));
  });

  let sudah = false;
  tombol.onclick = ev => {
    ev.stopPropagation();
    m.toggleAttribute('data-buka');
    if (!m.hasAttribute('data-buka') || sudah) return;
    sudah = true;
    muatRingkasan();
  };
  document.addEventListener('click', ev => {
    if (!m.contains(ev.target)) m.removeAttribute('data-buka');
  });
})();

// ---------------------------------------------------------------- menu Tambah

// ------------------------------------------------------- halaman Projek
/*
 * Kartu projek beserta menu ⋮-nya.
 *
 * Semua operasinya menyentuh berkas sungguhan, sebagian ribuan sekaligus,
 * jadi tiap yang tidak bisa dibatalkan meminta ketikan nama projeknya lebih
 * dulu. Konfirmasi "yakin?" biasa terlalu mudah dilewati dengan Enter, dan
 * yang hilang di sini adalah berjam-jam pekerjaan pelabelan.
 */
(() => {
  const grid = document.getElementById('projek-grid');
  if (!grid) return;
  const note = document.getElementById('projek-note');
  const cari = document.getElementById('projek-cari');
  const urut = document.getElementById('projek-urut');
  const lipatSampah = document.getElementById('sampah-lipat');
  const judulSampah = document.getElementById('sampah-judul');
  const isiSampah = document.getElementById('sampah-isi');
  const esc = t => String(t).replace(/[&<>"']/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const n = v => (v || 0).toLocaleString('id-ID');

  let semua = [];


  function hitung(tampil) {
    const q = (cari.value || '').trim();
    const hit = document.getElementById('projek-hitung');
    hit.hidden = !q;
    hit.textContent = q ? `menampilkan ${tampil} dari ${semua.length}` : '';
    const nm = document.getElementById('n-milik');
    if (nm) nm.textContent = semua.length;
  }

  async function muat() {
    let j;
    const pr = Progres.mulai('Memuat daftar projek', {di: note});
    pr.taktentu('menghitung isi tiap folder…');
    try { j = await (await fetch('/api/projek/daftar')).json(); }
    catch (e) { pr.gagal('Gagal menghubungi server'); return; }
    // Jalur !ok DULU diam saja: progresnya dibuang, fungsinya return, dan
    // panelnya tetap tersembunyi selamanya. Yang hilang bukan cuma daftar
    // projek — tempat sampah ada di dalam panel yang sama, jadi folder yang
    // baru dibuang tampak lenyap tanpa satu pun keterangan.
    if (!j || !j.ok) {
      pr.gagal((j && j.error) || 'Gagal memuat daftar projek');
      return;
    }
    pr.buang();
    semua = j.projek || [];
    grid.hidden = false;
    gambarSampah(j.sampah || []);
    render();
  }

  function render() {
    const q = (cari.value || '').trim().toLowerCase();
    let baris = semua.filter(p => !q || p.nama.toLowerCase().includes(q));
    const cara = urut.value;
    baris.sort((a, b) =>
      cara === 'nama' ? a.nama.localeCompare(b.nama, 'id')
      : cara === 'besar' ? b.jumlah - a.jumlah
      : cara === 'label' ? b.anotasi - a.anotasi
      : b.diubah - a.diubah);

    if (!baris.length) {
      grid.innerHTML = semua.length
        ? '<div class="pkosong"><b>Tidak ada yang cocok</b>'
          + 'Ubah kata pencarianmu.</div>'
        : '<div class="pkosong"><b>Belum ada projek</b>'
          + 'Buka "+ Projek baru" di atas untuk membuat yang pertama.</div>';
      hitung(0);
      return;
    }
    grid.innerHTML = baris.map(p => {
      const pct = p.jumlah ? Math.min(100, Math.round(p.anotasi / p.jumlah * 100)) : 0;
      return `
      <div class="pcard${p.dibuka ? ' dibuka' : ''}" data-nama="${esc(p.nama)}">
        <a class="psampul${p.sampul ? '' : ' kosong'}" href="#" tabindex="-1"
           aria-hidden="true" data-buka="${esc(p.path)}">
          ${p.sampul ? `<img loading="lazy" alt="" src="/api/projek/sampul?path=`
                       + `${encodeURIComponent(p.sampul)}">` : '\u25a4'}
        </a>
        <div class="pisi">
          <a class="pnama pnama-link" href="#" data-buka="${esc(p.path)}"
            >${esc(p.nama)}${p.dibuka
              ? '<span class="pbuka">sedang dibuka</span>' : ''}</a>
          <div class="pmeta">${p.lebih ? '\u2265' : ''}${n(p.jumlah)} gambar &middot;
            ${n(p.anotasi)} dilabeli &middot; <b>${pct}%</b></div>
          <div class="pbar" title="${n(p.anotasi)} dari ${n(p.jumlah)} gambar sudah dilabeli">
            <i style="width:${pct}%"></i></div>
          <div class="pmeta halus">diubah ${esc(p.usia)}</div>
        </div>
        <button class="ptitik" type="button" title="Fitur projek"
                aria-haspopup="menu" aria-expanded="false">\u22ee</button>
        <div class="pmenu" role="menu" hidden>
          <button type="button" role="menuitem" data-aksi="buka">Buka projek ini</button>
          <button type="button" role="menuitem" data-aksi="salin">Salin path</button>
          <span class="menu-pisah"></span>
          <button type="button" role="menuitem" data-aksi="ganti">Ganti nama\u2026</button>
          <button type="button" role="menuitem" data-aksi="duplikat">Duplikat</button>
          <button type="button" role="menuitem" data-aksi="gabung">Gabungkan ke projek lain\u2026</button>
          <span class="menu-pisah"></span>
          <button type="button" role="menuitem" class="bahaya" data-aksi="sampah">
            Buang ke tempat sampah\u2026</button>
        </div>
      </div>`;
    }).join('');
    // #projek-note dipakai Progres sebagai wadahnya. Menulis penghitung ke
    // sana berarti dua makna dalam satu kotak: memulai duplikasi menghapus
    // angkanya, dan angkanya menolak muncul selama ada operasi berjalan.
    hitung(baris.length);
  }

  function gambarSampah(isi) {
    lipatSampah.hidden = !isi.length;
    const chip = document.getElementById('buka-sampah');
    chip.hidden = !isi.length;
    document.getElementById('sampah-n').textContent = isi.length || '';
    if (!isi.length) return;
    judulSampah.textContent = `Tempat sampah (${isi.length})`;
    isiSampah.innerHTML = isi.map(s => `
      <div class="row" style="gap:8px;align-items:center;margin-bottom:5px">
        <span style="flex:1">${esc(s.nama)}
          <span class="halus">· dibuang ${esc(s.usia)}</span></span>
        <button class="chip" data-pulih="${esc(s.folder)}">Kembalikan</button>
      </div>`).join('')
      + '<div class="halus" style="margin-top:8px">Isinya masih memakan ruang '
      + 'disk. Untuk membuangnya betulan, hapus foldernya lewat berkas manajer '
      + 'atau terminal.</div>';
  }

  /*
   * Tiap operasi memakai bilah progres yang sama.
   *
   * `pantau` menyebutkan rute kemajuan yang harus ditanya berkala. Yang tidak
   * punya (ganti nama, buang, pulihkan) memakai bilah tak-tentu: ketiganya
   * memindahkan folder, biasanya seketika, tapi bisa lama kalau berkasnya di
   * disk jaringan. Yang tidak boleh terjadi adalah layar diam tanpa tanda.
   */
  async function kirim(url, params, pesan, pantau, batalUrl) {
    const pr = Progres.mulai(pesan, {di: note});
    pr.taktentu('menunggu server…');
    // Tombolnya disisipkan SEBELAH panel progres, bukan di dalamnya: modul
    // Progres memiliki isi wadahnya dan menulisnya ulang tiap kali berubah.
    let stop = null;
    if (batalUrl) {
      stop = document.createElement('button');
      stop.className = 'chip';
      stop.type = 'button';
      stop.textContent = 'Hentikan';
      stop.onclick = () => {
        stop.disabled = true;
        stop.textContent = 'Menghentikan…';
        post(batalUrl);
      };
      note.parentElement.appendChild(stop);
    }
    let ketuk = null;
    if (pantau) {
      ketuk = setInterval(async () => {
        let k;
        try { k = await (await fetch(pantau)).json(); } catch (e) { return; }
        if (!k) return;
        const persen = k.persen != null ? k.persen
                     : (k.total ? (k.berkas || k.n || 0) / k.total : null);
        if (persen == null) return;
        const n = k.n != null ? k.n : (k.berkas || 0);
        pr.set(persen, k.total ? `${n.toLocaleString('id-ID')} dari `
                                 + `${k.total.toLocaleString('id-ID')} berkas` : '');
      }, 400);
    }
    let j;
    try {
      j = await (await fetch(url + '?' + new URLSearchParams(params),
                             {method: 'POST'})).json();
    } catch (e) {
      pr.gagal('Gagal menghubungi server');
      return null;
    } finally {
      if (ketuk) clearInterval(ketuk);
      if (stop) stop.remove();
    }
    if (!j.ok) { pr.gagal(j.error || 'gagal'); toast(j.error || 'gagal'); }
    else pr.selesai();
    return j;
  }

  grid.addEventListener('click', async ev => {
    const kartu = ev.target.closest('.pcard');
    if (!kartu) return;
    const nama = kartu.dataset.nama;

    if (ev.target.closest('.ptitik')) {
      ev.preventDefault();
      const m = kartu.querySelector('.pmenu');
      const buka = m.hidden;
      grid.querySelectorAll('.pmenu').forEach(x => { x.hidden = true; });
      grid.querySelectorAll('.ptitik').forEach(
        x => x.setAttribute('aria-expanded', 'false'));
      m.hidden = !buka;
      kartu.querySelector('.ptitik').setAttribute('aria-expanded', String(buka));
      return;
    }
    const bukaLink = ev.target.closest('[data-buka]');
    const aksi = ev.target.closest('[data-aksi]');
    if (!bukaLink && !aksi) return;
    ev.preventDefault();
    const a = bukaLink ? 'buka' : aksi.dataset.aksi;
    kartu.querySelector('.pmenu').hidden = true;

    if (a === 'buka') {
      const p = bukaLink ? bukaLink.dataset.buka
                         : semua.find(x => x.nama === nama).path;
      const j = await kirim('/setsrc', {path: p}, 'Membuka projek');
      if (j && j.ok) location.href = '/';
      return;
    }
    if (a === 'salin') {
      const p = semua.find(x => x.nama === nama).path;
      try { await navigator.clipboard.writeText(p); toast('Path disalin'); }
      catch (e) { prompt('Salin path ini:', p); }
      return;
    }
    if (a === 'ganti') {
      const baru = prompt(`Nama baru untuk "${nama}":`, nama);
      if (!baru || baru === nama) return;
      const j = await kirim('/api/projek/ganti-nama', {nama, baru},
                            'Mengganti nama');
      if (j && j.ok) { toast(`Jadi "${j.nama}"`); muat(); }
      return;
    }
    if (a === 'duplikat') {
      // Menggandakan projek 1,3 GB memakan menit; tanpa tombol henti,
      // satu klik keliru harus ditunggu sampai selesai. Server sudah bisa
      // dihentikan (projek.duplikat membuang salinan setengah jadi), yang
      // belum ada cuma tombolnya.
      const j = await kirim('/api/projek/duplikat', {nama},
                            `Menduplikat ${nama}`, '/api/projek/kemajuan',
                            '/api/projek/batal');
      if (j && j.ok) { toast(`Salinan dibuat: ${j.nama}`); muat(); }
      return;
    }
    if (a === 'gabung') {
      const lain = semua.filter(x => x.nama !== nama).map(x => x.nama);
      if (!lain.length) { toast('Belum ada projek lain untuk digabung'); return; }
      const tujuan = prompt(
        `Salin isi "${nama}" ke projek mana?\n\nPilihan: ${lain.join(', ')}`
        + `\n\n"${nama}" sendiri TIDAK dihapus.`, lain[0]);
      if (!tujuan) return;
      const j = await kirim('/api/projek/gabung', {sumber: nama, tujuan},
                            `Menggabungkan ${nama} ke ${tujuan}`,
                            '/api/impor/kemajuan');
      if (j && j.ok) {
        toast(`${n(j.ditambah)} berkas masuk ke ${j.tujuan}`);
        note.textContent = `${n(j.ditambah)} berkas ditambahkan ke `
          + `${j.tujuan}, ${n(j.sudah_ada)} sudah ada di sana.`;
        muat();
      }
      return;
    }
    if (a === 'sampah') {
      // Namanya harus DIKETIK. Ini memindahkan ribuan berkas sekaligus, dan
      // dialog "yakin?" biasa hilang hanya dengan menekan Enter.
      const jwb = prompt(
        `Buang projek "${nama}" ke tempat sampah?\n\n`
        + 'Berkasnya dipindah, bukan dihapus, dan bisa dikembalikan.\n\n'
        + `Ketik nama projeknya untuk melanjutkan:`);
      if (jwb !== nama) {
        if (jwb !== null) toast('Nama tidak cocok, dibatalkan');
        return;
      }
      const j = await kirim('/api/projek/sampah', {nama},
                            'Memindahkan ke tempat sampah');
      if (j && j.ok) { toast(`"${j.nama}" masuk tempat sampah`); muat(); }
    }
  });

  isiSampah.addEventListener('click', async ev => {
    const b = ev.target.closest('[data-pulih]');
    if (!b) return;
    ev.preventDefault();
    const j = await kirim('/api/projek/pulihkan', {folder: b.dataset.pulih},
                          'Mengembalikan dari tempat sampah');
    if (j && j.ok) { toast(`"${j.nama}" dikembalikan`); muat(); }
  });

  document.addEventListener('click', ev => {
    if (!ev.target.closest('.pcard')) {
      grid.querySelectorAll('.pmenu').forEach(x => { x.hidden = true; });
    }
  });

  /* Penanda data-on pada segmen aktif. Pola yang sama dengan saringan
     kelas: dipasang lewat JS, bukan :has(), supaya tidak bergantung pada
     dukungan peramban terhadap :has(). */
  function segPasang(wadah, saatGanti) {
    if (!wadah) return;
    const opsi = [...wadah.querySelectorAll('.seg-opt')];
    const tandai = () => {
      let aktif = '';
      opsi.forEach(o => {
        const c = o.querySelector('input');
        o.toggleAttribute('data-on', c.checked);
        if (c.checked) aktif = c.value;
      });
      saatGanti(aktif);
    };
    wadah.addEventListener('change', tandai);
    tandai();
  }

  segPasang(document.getElementById('ptab'), v => {
    const milik = v === 'milik';
    grid.hidden = !milik || !semua.length && false;
    const b = document.getElementById('grid-bersama');
    if (b) b.hidden = milik;
    // Cari dan urut hanya berlaku untuk projek sendiri; membiarkannya aktif
    // tapi tak berpengaruh lebih membingungkan daripada meredupkannya.
    cari.disabled = !milik;
    urut.disabled = !milik;
    document.getElementById('projek-hitung').hidden = !milik;
  });

  /* Dialog "Projek baru": menanyakan nama, itu saja.

     Dulu ia juga terbuka sendiri begitu ada berkas diseret ke halaman ini,
     karena kotak seretnya memang berada di dalamnya. Sekarang kotak seretnya
     ada di halaman Unggah data, jadi membuka dialog nama saat orang menyeret
     folder justru menyesatkan: yang muncul bukan tempat menaruhnya. */
  const dlgProjek = document.getElementById('dlg-projek');
  let fokusSemula = null;

  function bukaDlg(v) {
    dlgProjek.hidden = !v;
    if (v) {
      fokusSemula = document.activeElement;
      const isian = document.getElementById('dsname');
      if (isian) setTimeout(() => isian.focus(), 30);
    } else if (fokusSemula) {
      fokusSemula.focus();
      fokusSemula = null;
    }
  }

  document.getElementById('buka-tambah').onclick = () => bukaDlg(true);

  /* Membuat projek sekarang cuma soal nama. Berkasnya diurus di halaman
     Unggah data, yang bisa memperlihatkan APA yang akan terkirim sebelum
     benar-benar terkirim. */
  const tombolBuat = document.getElementById('buat-projek');
  if (tombolBuat) {
    const buat = async () => {
      const isian = document.getElementById('dsname');
      const nama = (isian.value || '').trim();
      if (!nama) { toast('Beri nama projeknya dulu'); isian.focus(); return; }
      tombolBuat.disabled = true;
      const pr = Progres.mulai('Membuat projek ' + nama,
                               { di: document.getElementById('projek-baru-jalur') });
      pr.taktentu('Menyiapkan foldernya');
      try {
        const j = await post('/api/projek/baru?nama=' + encodeURIComponent(nama));
        if (!j.ok) { pr.gagal(j.error); tombolBuat.disabled = false; return; }
        pr.selesai('Projek dibuat, membuka halaman unggah');
        location.href = '/unggah?ds=' + encodeURIComponent(j.nama);
      } catch (e) {
        pr.gagal('Gagal menghubungi server');
        tombolBuat.disabled = false;
      }
    };
    tombolBuat.onclick = buat;
    document.getElementById('dsname').addEventListener('keydown', ev => {
      if (ev.key === 'Enter') { ev.preventDefault(); buat(); }
    });
  }
  document.getElementById('dlg-projek-tutup').onclick = () => bukaDlg(false);
  // Klik pada tirainya menutup; klik di dalam kotaknya tidak.
  dlgProjek.addEventListener('click', ev => {
    if (ev.target === dlgProjek) bukaDlg(false);
  });
  document.addEventListener('keydown', ev => {
    if (ev.key === 'Escape' && !dlgProjek.hidden) bukaDlg(false);
  });
  document.getElementById('buka-sampah').onclick = () => {
    lipatSampah.open = true;
    lipatSampah.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  };

  /* Urutan diingat. Sebelumnya tiap kunjungan kembali ke "Terakhir diubah",
     dan orang yang bekerja berdasarkan nama menggantinya lagi setiap kali. */
  try {
    const simpan = localStorage.getItem('labelapp_projek_urut');
    if (simpan) urut.value = simpan;
  } catch (e) { /* mode privat */ }
  urut.addEventListener('change', () => {
    try { localStorage.setItem('labelapp_projek_urut', urut.value); } catch (e) {}
  });

  // Escape menutup menu titik-tiga dan mengembalikan fokus ke tombolnya.
  document.addEventListener('keydown', ev => {
    if (ev.key !== 'Escape') return;
    const buka = grid.querySelector('.pmenu:not([hidden])');
    if (!buka) return;
    buka.hidden = true;
    const t = buka.closest('.pcard').querySelector('.ptitik');
    t.setAttribute('aria-expanded', 'false');
    t.focus();
  });

  cari.addEventListener('input', render);
  urut.addEventListener('change', render);
  muat();
})();

// ------------------------------------------------------- halaman kelola akun
/*
 * Menambah anggota tim sebelumnya hanya bisa lewat terminal server, sehingga
 * satu orang harus punya akses SSH untuk pekerjaan yang sebenarnya
 * administratif.
 *
 * Dua penjaga yang tidak boleh dilepas: yang menghapus akun harus mengetik
 * nama akunnya, dan admin terakhir tidak bisa dihapus atau diturunkan.
 * Keduanya juga ditegakkan di server; yang di sini semata supaya salahnya
 * ketahuan sebelum permintaan dikirim.
 */
(() => {
  const daftar = document.getElementById('akun-daftar');
  if (!daftar) return;
  const note = document.getElementById('akun-note');
  const cari = document.getElementById('akun-cari');
  const esc = t => String(t == null ? '' : t).replace(/[&<>"']/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  let semua = [], nAdmin = 1;

  async function muat() {
    let j;
    try { j = await (await fetch('/api/akun/daftar')).json(); } catch (e) { return; }
    if (!j || !j.ok) { note.textContent = (j && j.error) || 'gagal memuat'; return; }
    semua = j.akun; nAdmin = j.n_admin;
    render();
  }

  function render() {
    const q = (cari.value || '').trim().toLowerCase();
    const baris = semua.filter(a => !q
      || a.akun.includes(q) || (a.email || '').toLowerCase().includes(q)
      || (a.nama || '').toLowerCase().includes(q));
    // Yang menunggu persetujuan naik ke atas. Merekalah yang butuh keputusan;
    // sisanya cuma daftar.
    baris.sort((a, b) => (b.menunggu ? 1 : 0) - (a.menunggu ? 1 : 0));
    daftar.innerHTML = baris.map(a => `
      <div class="akun-baris${a.menunggu ? ' menunggu' : ''}"
           data-akun="${esc(a.akun)}">
        <div class="akun-kiri">
          <div class="akun-nama">${esc(a.nama)}
            ${a.menunggu ? '<span class="lencana tunggu">menunggu persetujuan</span>' : ''}
            ${a.admin ? '<span class="lencana adm">admin</span>' : ''}
            ${a.diri_sendiri ? '<span class="lencana aku">kamu</span>' : ''}</div>
          <div class="akun-email${a.email ? '' : ' kosong'}">${
            a.email ? esc(a.email) : 'belum ada email'}</div>
          ${a.dibuat ? `<div class="akun-meta">dibuat ${esc(a.dibuat)}${
            a.oleh ? ' oleh ' + esc(a.oleh) : ''}</div>` : ''}
        </div>
        <div class="akun-aksi">
          ${a.menunggu
            ? '<button class="chip aksi" data-aksi="setujui">Setujui</button>' : ''}
          <button class="chip" data-aksi="email">Email</button>
          <button class="chip" data-aksi="sandi">Sandi</button>
          <button class="chip" data-aksi="admin">${
            a.admin ? 'Cabut admin' : 'Jadikan admin'}</button>
          ${a.diri_sendiri ? ''
            : '<button class="chip bahaya" data-aksi="hapus">Hapus</button>'}
        </div>
      </div>`).join('') || '<p class="sub" style="margin:0">Tidak ada yang cocok.</p>';
    const tunggu = semua.filter(a => a.menunggu).length;
    note.textContent = `${baris.length} dari ${semua.length} akun`
      + ` · ${nAdmin} admin`
      + (tunggu ? ` · ${tunggu} menunggu persetujuan` : '');
  }

  async function kirim(url, params, judul) {
    const pr = Progres.mulai(judul, {di: note});
    pr.taktentu('menyimpan…');
    let j;
    try {
      j = await (await fetch(url + '?' + new URLSearchParams(params),
                             {method: 'POST'})).json();
    } catch (e) { pr.gagal('Gagal menghubungi server'); return null; }
    if (!j.ok) { pr.gagal(j.error); toast(j.error); } else pr.selesai();
    return j;
  }

  daftar.addEventListener('click', async ev => {
    const b = ev.target.closest('[data-aksi]');
    if (!b) return;
    const akun = b.closest('.akun-baris').dataset.akun;
    const rec = semua.find(x => x.akun === akun);
    const a = b.dataset.aksi;

    if (a === 'setujui') {
      const j = await kirim('/api/akun/setujui', {akun}, `Menyetujui ${akun}`);
      if (j && j.ok) { toast(`"${akun}" sekarang bisa masuk`); muat(); }
      return;
    }
    if (a === 'email') {
      const email = prompt(`Email untuk "${akun}":\n\n`
        + 'Dipakai untuk login Google. Kosongkan untuk menghapusnya.',
        rec.email || '');
      if (email === null) return;
      const j = await kirim('/api/akun/ubah', {akun, email}, 'Menyimpan email');
      if (j && j.ok) muat();
      return;
    }
    if (a === 'sandi') {
      const sandi = prompt(`Kata sandi baru untuk "${akun}":\n\n`
        + 'Minimal 8 karakter. Beri tahu orangnya lewat jalur lain,\n'
        + 'jangan lewat pesan yang bisa dibaca orang banyak.');
      if (!sandi) return;
      const j = await kirim('/api/akun/ubah', {akun, sandi}, 'Mengganti sandi');
      if (j && j.ok) { toast(`Sandi "${akun}" diganti`); muat(); }
      return;
    }
    if (a === 'admin') {
      const jadi = rec.admin ? 0 : 1;
      if (!jadi && nAdmin <= 1) {
        toast('Ini admin terakhir; angkat admin lain lebih dulu');
        return;
      }
      const j = await kirim('/api/akun/ubah', {akun, admin: jadi},
                            jadi ? 'Mengangkat admin' : 'Mencabut admin');
      if (j && j.ok) muat();
      return;
    }
    if (a === 'hapus') {
      // Namanya harus DIKETIK: ini melenyapkan akses orang, dan dialog
      // "yakin?" biasa hilang hanya dengan menekan Enter.
      const jwb = prompt(`Hapus akun "${akun}"?\n\n`
        + 'Orangnya langsung kehilangan akses. Dataset yang sudah dia unggah\n'
        + 'TIDAK ikut terhapus.\n\nKetik nama akunnya untuk melanjutkan:');
      if (jwb !== akun) {
        if (jwb !== null) toast('Nama tidak cocok, dibatalkan');
        return;
      }
      const j = await kirim('/api/akun/hapus', {akun}, `Menghapus ${akun}`);
      if (j && j.ok) { toast(`Akun "${akun}" dihapus`); muat(); }
    }
  });

  const simpan = document.getElementById('a-simpan');
  simpan.onclick = async () => {
    const nama = document.getElementById('a-nama').value.trim();
    const email = document.getElementById('a-email').value.trim();
    const sandi = document.getElementById('a-sandi').value;
    const admin = document.getElementById('a-admin').checked ? 1 : 0;
    const n2 = document.getElementById('a-note');
    if (!nama) { toast('Nama akun masih kosong'); return; }
    if (sandi.length < 8) { toast('Kata sandi minimal 8 karakter'); return; }
    const pr = Progres.mulai(`Menambah ${nama}`, {di: n2});
    pr.taktentu('menyimpan…');
    let j;
    try {
      j = await (await fetch('/api/akun/tambah?' + new URLSearchParams(
        {nama, sandi, email, admin}), {method: 'POST'})).json();
    } catch (e) { pr.gagal('Gagal menghubungi server'); return; }
    if (!j.ok) { pr.gagal(j.error); toast(j.error); return; }
    pr.selesai(`Akun "${j.akun}" dibuat`);
    ['a-nama', 'a-email', 'a-sandi'].forEach(i => {
      document.getElementById(i).value = '';
    });
    document.getElementById('a-admin').checked = false;
    muat();
  };

  cari.addEventListener('input', render);
  muat();
})();
