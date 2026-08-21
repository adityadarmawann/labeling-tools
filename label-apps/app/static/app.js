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
    toast(j.detail || 'Sesi habis — masuk lagi');
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
  toast('Memindai ulang...');
  const j = await post('/rescan');
  if (j.ok) location.reload(); else toast('Gagal: ' + j.error);
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
  toast('Memindai folder...');
  try {
    const j = await post('/setsrc?path=' + encodeURIComponent(p));
    if (j.ok) location.href = '/'; else toast(j.error);
  } catch (e) {
    toast('Gagal menghubungi server');
  }
}

function setsrcBox() {
  setsrc(document.getElementById('pathbox').value.trim());
}

/*
 * Ambil dari folder di server = MENYALIN, bukan membuka di tempat.
 *
 * Bedanya menentukan: /setsrc membuka folder itu juga, sehingga menyunting
 * berarti mengubah dataset aslinya. Di sini isinya disalin dulu ke ruang kerja
 * pemakai, jadi menyunting dan menambah gambar tidak pernah menyentuh sumber.
 * Ukurannya disurvei lebih dulu — menyalin beberapa GB tanpa pemberitahuan
 * bukan kejutan yang menyenangkan.
 */
async function imporBox() {
  const p = document.getElementById('pathbox').value.trim();
  const note = document.getElementById('impornote');
  if (!p) { toast('Path masih kosong'); return; }

  note.textContent = 'Memeriksa isi folder…';
  let s;
  try {
    s = await (await fetch('/api/impor/survei?path=' + encodeURIComponent(p))).json();
  } catch (e) { note.textContent = ''; toast('Gagal menghubungi server'); return; }
  if (!s.ok) { note.textContent = ''; toast(s.error); return; }

  const mb = s.bytes / 1048576;
  const ukuran = mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : mb.toFixed(0) + ' MB';
  const nama = prompt(
    `Akan menyalin ${s.berkas} berkas (${ukuran}) ke ruang kerjamu.\n`
    + `Folder asalnya tidak diubah sama sekali.\n\n`
    + `Beri nama untuk salinan ini:`, s.nama_usul || '');
  if (nama === null) { note.textContent = ''; return; }

  const tombol = document.getElementById('btn-impor');
  const bar = document.getElementById('prog-impor');
  const isi = document.getElementById('fill-impor');
  // Dimatikan selama berjalan: menekan dua kali memulai penyalinan kedua ke
  // folder tujuan yang sama, dan keduanya lalu berebut berkas yang sama.
  if (tombol) { tombol.disabled = true; tombol.textContent = 'Menyalin…'; }
  bar.setAttribute('data-on', '');
  isi.style.width = '0%';
  note.textContent = `Menyalin ${s.berkas} berkas (${ukuran})…`;

  // Penyalinan berjalan di thread terpisah di server sementara permintaan
  // /impor di bawah menggantung sampai selesai, jadi kemajuannya ditanyakan
  // lewat permintaan terpisah. Tanpa ini penyalinan 22 ribu berkas tampak
  // seperti halaman yang macet.
  const pantau = setInterval(async () => {
    let k;
    try { k = await (await fetch('/api/impor/kemajuan')).json(); } catch (e) { return; }
    if (!k || !k.tahap) return;
    if (k.tahap === 'pindai') {
      isi.style.width = '100%';
      bar.dataset.tahap = 'pindai';
      note.textContent =
        `${s.berkas.toLocaleString('id-ID')} berkas tersalin — memindai isinya…`;
      return;
    }
    if (k.tahap !== 'salin') return;
    const persen = k.total ? Math.min(100, k.berkas / k.total * 100) : 0;
    isi.style.width = persen.toFixed(1) + '%';
    note.textContent = `${k.berkas.toLocaleString('id-ID')} dari `
      + `${k.total.toLocaleString('id-ID')} berkas · `
      + `${(k.bytes / 1048576).toFixed(0)} MB · ${persen.toFixed(0)}%`;
  }, 500);

  const beres = () => {
    clearInterval(pantau);
    bar.removeAttribute('data-on');
    delete bar.dataset.tahap;
    if (tombol) { tombol.disabled = false; tombol.textContent = 'Salin ke ruang kerjaku'; }
  };

  try {
    const j = await post('/impor?path=' + encodeURIComponent(p)
                       + '&ds=' + encodeURIComponent(nama.trim() || s.nama_usul));
    beres();
    if (!j.ok) { note.textContent = j.error; toast(j.error); return; }

    // Berkas yang terlewat dilaporkan, tidak didiamkan: salinan yang kurang
    // beberapa gambar tetap terlihat berhasil, dan kekurangannya baru ketahuan
    // jauh belakangan saat dataset dilatih.
    const catatan = (j.peringatan || []).slice();
    if (j.dilewati) {
      const c = (j.contoh_dilewati || []).concat(j.bentrok || []);
      const sebab = (j.bentrok || []).length
        ? 'bukan gambar/anotasi, atau namanya bentrok' : 'bukan gambar/anotasi';
      const mis = c.length ? ' — mis. ' + c.slice(0, 3).join(', ') : '';
      catatan.push(`${j.dilewati} berkas dilewati (${sebab})${mis}`);
    }
    if (catatan.length) {
      note.innerHTML = `<b>${j.disalin} berkas disalin, ${j.n} gambar terbaca — perlu dicek:</b><br>`
        + catatan.map(x => '· ' + esc(x)).join('<br>')
        + '<br><button class="btn pri" id="lanjut-impor">Lanjut ke grid</button>';
      const b = document.getElementById('lanjut-impor');
      if (b) b.onclick = () => { location.href = '/'; };
      return;
    }
    toast(`${j.disalin} berkas disalin — membuka salinan`);
    location.href = '/';
  } catch (e) {
    beres();
    note.textContent = '';
    toast('Gagal menghubungi server');
  }
}

// Item dataset membawa path di data-path. Dipasang lewat delegasi supaya path
// tidak perlu disisipkan ke dalam string JavaScript di atribut HTML — cara itu
// pernah membuat seluruh daftar tidak bisa diklik karena tanda kutipnya
// menutup atribut onclick lebih awal.
document.addEventListener('click', ev => {
  const a = ev.target.closest && ev.target.closest('a.ds[data-path]');
  if (!a) return;
  ev.preventDefault();
  setsrc(a.dataset.path);
});

// Riwayat path: sekali klik hanya MENGISI kotaknya, tidak langsung membuka.
// Dua tombol di sebelahnya berbeda akibat (menyalin vs membuka di tempat),
// jadi pilihan itu tetap harus dibuat sadar, bukan tersirat dari satu klik.
document.addEventListener('click', async ev => {
  const t = ev.target.closest && ev.target.closest('button.riw, button.lupa');
  if (!t) return;
  ev.preventDefault();
  const p = t.dataset.path;
  if (t.classList.contains('riw')) {
    const box = document.getElementById('pathbox');
    box.value = p;
    box.focus();
    toast('Path terisi — pilih menyalin atau membuka di tempat');
    return;
  }
  const j = await post('/lupakan-path?path=' + encodeURIComponent(p));
  if (j.ok) t.closest('.row').remove();
});

async function pickdir() {
  toast('Dialog terbuka di layar server...');
  try {
    const j = await post('/pickdir');
    if (j.ok) { toast('Memindai folder...'); location.href = '/'; }
    else toast(j.error);
  } catch (e) {
    toast('Gagal membuka dialog');
  }
}

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

async function uploadFiles(files) {
  const ds = (document.getElementById('dsname').value || '').trim();
  if (!ds) { toast('Beri nama dataset dulu'); return; }

  const pilih = [...files].filter(f => UP_EXT.some(e => f.name.toLowerCase().endsWith(e)));
  if (!pilih.length) {
    toast('Tidak ada gambar, anotasi, data.yaml, atau .zip di pilihan itu');
    return;
  }

  const bar = document.getElementById('prog');
  const fill = document.getElementById('fill');
  const note = document.getElementById('upnote');
  bar.setAttribute('data-on', '');

  let selesai = 0, gagal = 0;
  const arsip = [];
  for (const f of pilih) {
    const nama = namaKirim(f);
    try {
      const j = await send('/upload?ds=' + encodeURIComponent(ds) +
                           '&name=' + encodeURIComponent(nama),
                           { method: 'PUT', body: f });
      if (!j.ok) { gagal++; if (gagal <= 2) toast(f.name + ': ' + j.error); }
      else if (j.arsip) arsip.push(j.name);
    } catch (e) {
      gagal++;
    }
    selesai++;
    fill.style.width = Math.round(selesai * 100 / pilih.length) + '%';
    note.textContent = selesai + ' / ' + pilih.length + ' terkirim' +
                       (gagal ? (' · ' + gagal + ' gagal') : '');
  }

  if (selesai <= gagal) { toast('Semua berkas gagal terkirim'); return; }

  // Arsip dibongkar di server. Untuk berkas 1 GB ini bisa memakan waktu, jadi
  // keadaannya dikatakan, bukan dibiarkan tampak menggantung.
  for (const nama of arsip) {
    note.textContent = 'Membongkar ' + nama + '… (berkas besar perlu waktu)';
    fill.style.width = '100%';
    try {
      const j = await post('/unzip?ds=' + encodeURIComponent(ds) +
                           '&name=' + encodeURIComponent(nama));
      if (!j.ok) { toast('Gagal membongkar: ' + j.error); note.textContent = j.error; return; }
      note.textContent = `${nama}: ${j.n} berkas dibongkar`
                       + (j.dilewati ? ` · ${j.dilewati} dilewati` : '');
    } catch (e) {
      toast('Gagal menghubungi server saat membongkar');
      return;
    }
  }

  const j = await post('/useupload?ds=' + encodeURIComponent(ds));
  if (!j.ok) { toast(j.error); return; }

  // Peringatan ditahan di layar dan menunggu diklik. Kalau ditoast lalu
  // langsung pindah halaman, orang tidak akan sempat membacanya — padahal
  // isinya justru menentukan apakah datasetnya benar.
  if ((j.peringatan || []).length) {
    bar.removeAttribute('data-on');
    note.innerHTML = '<b>Dataset terbuka (' + j.n + ' gambar), tapi perlu dicek:</b><br>'
      + j.peringatan.map(p => '· ' + p).join('<br>')
      + '<br><button class="btn pri" id="lanjut-grid">Lanjut ke grid</button>';
    const b = document.getElementById('lanjut-grid');
    if (b) b.onclick = () => { location.href = '/'; };
    return;
  }
  toast('Selesai — membuka dataset');
  location.href = '/';
}

document.addEventListener('DOMContentLoaded', () => {
  const d = document.getElementById('drop');
  const berkas = document.getElementById('files');
  const folder = document.getElementById('folder');
  if (!d || !berkas) return;

  // Klik pada area tarik-lepas membuka pemilih FOLDER, bukan pemilih berkas —
  // unggahan dataset hampir selalu satu folder, bukan berkas satu-satu.
  d.onclick = () => (folder || berkas).click();
  if (folder) folder.onchange = () => uploadFiles(folder.files);
  berkas.onchange = () => uploadFiles(berkas.files);

  const tf = document.getElementById('pilih-folder');
  const tb = document.getElementById('pilih-berkas');
  if (tf) tf.onclick = ev => { ev.preventDefault(); (folder || berkas).click(); };
  if (tb) tb.onclick = ev => { ev.preventDefault(); berkas.click(); };

  d.ondragover = e => { e.preventDefault(); d.setAttribute('data-over', ''); };
  d.ondragleave = () => d.removeAttribute('data-over');
  d.ondrop = async e => {
    e.preventDefault();
    d.removeAttribute('data-over');
    document.getElementById('upnote').textContent = 'membaca isi folder…';
    uploadFiles(await dariDrop(e.dataTransfer));
  };
});

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
  document.addEventListener('click', ev => {
    if (!m.contains(ev.target)) m.removeAttribute('data-buka');
  });
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
        + ` — dari ${j.gambar} gambar, ${j.objek} objek, ${j.kelas} kelas`
        + (j.split_bawaan
          ? '<br>Dataset ini sudah terbagi train/valid/test, jadi pembagiannya '
            + 'dipertahankan dan angka rasio di atas tidak dipakai.'
          : '')
        + (j.tanpa_objek ? `<br>${j.tanpa_objek} tanpa objek (contoh negatif)` : '')
        + (j.bentuk_dilewati ? ` · ${j.bentuk_dilewati} bentuk dilewati` : '');
      kotak.forEach(k => { if (k) k.disabled = !!j.split_bawaan; });
    } catch (e) {
      info.textContent = 'Gagal menghubungi server';
    }
    perbaruiTautan();
  }

  kotak.forEach(k => { if (k) k.onchange = muatRingkasan; });

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

/*
 * Menambah gambar ke dataset yang SEDANG dibuka, bukan membuat dataset baru.
 *
 * Bedanya dengan panel unggah di halaman pilih: di sana berkasnya mendarat di
 * folder bernama tersendiri, di sini menyatu ke dataset yang terbuka. Letak
 * persisnya ditentukan server dari tata letak dataset tujuan — gambar ke
 * images/, label ke labels/, dan pada dataset bersplit keduanya ke split yang
 * sama mengikuti rasio yang sudah ada.
 */
(() => {
  const m = document.getElementById('menu-tambah');
  if (!m) return;
  const tombol = document.getElementById('tambah-tombol');
  const info = document.getElementById('tambah-info');
  const d = document.getElementById('drop-tambah');

  tombol.onclick = ev => { ev.stopPropagation(); m.toggleAttribute('data-buka'); };
  document.addEventListener('click', ev => {
    if (!m.contains(ev.target)) m.removeAttribute('data-buka');
  });
  // Klik di dalam menu tidak boleh menutupnya — mengetik path mustahil kalau
  // menunya hilang begitu kotaknya disentuh.
  m.addEventListener('click', ev => ev.stopPropagation());

  if (!d) return;                       // dataset ini tidak boleh ditambahi

  const berkas = document.getElementById('tambah-berkas');
  const folder = document.getElementById('tambah-folder');
  d.onclick = () => (folder || berkas).click();
  if (folder) folder.onchange = () => kirimTambahan(folder.files);
  berkas.onchange = () => kirimTambahan(berkas.files);
  document.getElementById('tambah-pilih-folder').onclick =
    ev => { ev.preventDefault(); (folder || berkas).click(); };
  document.getElementById('tambah-pilih-berkas').onclick =
    ev => { ev.preventDefault(); berkas.click(); };
  d.ondragover = e => { e.preventDefault(); d.setAttribute('data-over', ''); };
  d.ondragleave = () => d.removeAttribute('data-over');
  d.ondrop = async e => {
    e.preventDefault();
    d.removeAttribute('data-over');
    info.textContent = 'membaca isi folder…';
    kirimTambahan(await dariDrop(e.dataTransfer));
  };

  async function kirimTambahan(files) {
    // Gambar didahulukan atas berkas labelnya. Kalau ada nama yang bentrok,
    // gambarnya yang menentukan nama pengganti dan labelnya mengikuti; urutan
    // terbalik membuat label mendapat akhiran sendiri dan menjadi yatim.
    const pilih = [...files]
      .filter(f => UP_EXT.some(e => f.name.toLowerCase().endsWith(e))
                   && !adalahArsip(f.name)
                   && !/\.ya?ml$/i.test(f.name))
      .sort((a, b) => (/\.(json|txt)$/i.test(a.name) ? 1 : 0)
                    - (/\.(json|txt)$/i.test(b.name) ? 1 : 0));
    if (!pilih.length) { toast('Tidak ada gambar atau anotasi di pilihan itu'); return; }

    let ok = 0, lewat = 0, ganti = 0, gagal = 0;
    for (let i = 0; i < pilih.length; i++) {
      const f = pilih[i];
      try {
        const j = await send('/tambah?name=' + encodeURIComponent(namaKirim(f)),
                             { method: 'PUT', body: f });
        if (!j.ok) { gagal++; if (gagal <= 2) toast(f.name + ': ' + j.error); }
        else if (j.hasil === 'sudah-ada') lewat++;
        else { ok++; if (j.hasil === 'senama') ganti++; }
      } catch (e) { gagal++; }
      info.textContent = `${i + 1} / ${pilih.length} terkirim`;
    }
    selesaikan(ok, lewat, ganti, gagal);
  }

  const impor = document.getElementById('tambah-impor');
  if (impor) impor.onclick = async ev => {
    ev.preventDefault();
    const p = (document.getElementById('tambah-path').value || '').trim();
    if (!p) { toast('Path masih kosong'); return; }
    const bar = document.getElementById('prog-tambah');
    const isi = document.getElementById('fill-tambah');
    impor.disabled = true;
    bar.setAttribute('data-on', '');
    info.textContent = 'Menggabungkan…';
    const pantau = setInterval(async () => {
      let k;
      try { k = await (await fetch('/api/impor/kemajuan')).json(); } catch (e) { return; }
      if (!k || k.tahap !== 'salin' || !k.total) return;
      const persen = Math.min(100, k.berkas / k.total * 100);
      isi.style.width = persen.toFixed(1) + '%';
      info.textContent = `${k.berkas.toLocaleString('id-ID')} dari `
        + `${k.total.toLocaleString('id-ID')} berkas · ${persen.toFixed(0)}%`;
    }, 500);
    try {
      const j = await post('/tambah/impor?path=' + encodeURIComponent(p));
      if (!j.ok) { info.textContent = j.error; toast(j.error); return; }
      selesaikan(j.ditambah, j.sudah_ada, (j.bentrok || []).length, j.dilewati);
    } catch (e) {
      toast('Gagal menghubungi server');
    } finally {
      clearInterval(pantau);
      bar.removeAttribute('data-on');
      impor.disabled = false;
    }
  };

  // Halaman TIDAK dimuat ulang sendiri. Angkanya dilaporkan lebih dulu —
  // "0 ditambah, 40 sudah ada" adalah hasil yang perlu dibaca, dan memuat
  // ulang seketika akan menghapusnya sebelum sempat terlihat.
  function selesaikan(ok, lewat, ganti, gagal) {
    const bagian = [`<b>${ok} gambar/label ditambahkan</b>`];
    if (lewat) bagian.push(`${lewat} sudah ada di dataset (isinya sama persis, dilewati)`);
    if (ganti) bagian.push(`${ganti} namanya sudah terpakai — disimpan dengan akhiran`);
    if (gagal) bagian.push(`${gagal} gagal`);
    info.innerHTML = bagian.join('<br>')
      + '<br><button class="chip" id="tambah-pindai">Pindai ulang & lihat</button>';
    const b = document.getElementById('tambah-pindai');
    if (b) b.onclick = () => rescan();
  }
})();
