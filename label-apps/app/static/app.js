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
const UP_EXT = ['.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff',
                '.json', '.txt'];

async function uploadFiles(files) {
  const ds = (document.getElementById('dsname').value || '').trim();
  if (!ds) { toast('Beri nama dataset dulu'); return; }

  const pilih = [...files].filter(f => UP_EXT.some(e => f.name.toLowerCase().endsWith(e)));
  if (!pilih.length) { toast('Tidak ada gambar atau .json di pilihan itu'); return; }

  const bar = document.getElementById('prog');
  const fill = document.getElementById('fill');
  const note = document.getElementById('upnote');
  bar.setAttribute('data-on', '');

  let selesai = 0, gagal = 0;
  for (const f of pilih) {
    try {
      const j = await send('/upload?ds=' + encodeURIComponent(ds) +
                           '&name=' + encodeURIComponent(f.name),
                           { method: 'PUT', body: f });
      if (!j.ok) { gagal++; if (gagal <= 2) toast(f.name + ': ' + j.error); }
    } catch (e) {
      gagal++;
    }
    selesai++;
    fill.style.width = Math.round(selesai * 100 / pilih.length) + '%';
    note.textContent = selesai + ' / ' + pilih.length + ' terkirim' +
                       (gagal ? (' · ' + gagal + ' gagal') : '');
  }

  if (selesai > gagal) {
    toast('Selesai — membuka dataset');
    const j = await post('/useupload?ds=' + encodeURIComponent(ds));
    if (j.ok) location.href = '/'; else toast(j.error);
  } else {
    toast('Semua berkas gagal terkirim');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const d = document.getElementById('drop');
  const inp = document.getElementById('files');
  if (!d || !inp) return;
  d.onclick = () => inp.click();
  inp.onchange = () => uploadFiles(inp.files);
  d.ondragover = e => { e.preventDefault(); d.setAttribute('data-over', ''); };
  d.ondragleave = () => d.removeAttribute('data-over');
  d.ondrop = e => {
    e.preventDefault();
    d.removeAttribute('data-over');
    uploadFiles(e.dataTransfer.files);
  };
});

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
      info.innerHTML =
        `<b>nyata: ${n(pc.train)}% : ${n(pc.valid)}% : ${n(pc.test)}%</b><br>`
        + `train ${s.train} · valid ${s.valid} · test ${s.test}`
        + ` — dari ${j.gambar} gambar, ${j.objek} objek, ${j.kelas} kelas`
        + (j.tanpa_objek ? `<br>${j.tanpa_objek} tanpa objek (contoh negatif)` : '')
        + (j.bentuk_dilewati ? ` · ${j.bentuk_dilewati} bentuk dilewati` : '');
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
