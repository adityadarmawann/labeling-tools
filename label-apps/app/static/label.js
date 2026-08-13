'use strict';
/*
 * Kanvas anotasi.
 *
 * Perilakunya mengikuti AnyLabeling: satu alat aktif, klik pada kanvas
 * bertindak sesuai alat itu, dan pintasan huruf memindah alat. Yang berbeda
 * hanya alat bawaannya — di sini "Klik SAM": klik satu kali di atas objek dan
 * poligonnya jadi sendiri.
 *
 * Koordinat: semua bentuk disimpan dalam koordinat GAMBAR (piksel asli), bukan
 * koordinat layar. Zoom dan pan hanya mengubah cara menggambar. Tanpa disiplin
 * ini, hasil anotasi akan bergeser setiap kali orang mengubah zoom.
 */

const D = JSON.parse(document.getElementById('data-awal').textContent);

const S = {
  shapes: D.shapes.map(s => ({ ...s, points: s.points.map(p => [p[0], p[1]]) })),
  kelas: D.kelas.slice(),
  label: D.kelas[0] || '',
  mode: 'sam',
  sel: -1,
  zoom: 1, panx: 0, pany: 0,
  draft: null,          // poligon manual yang sedang digambar
  seret: null,          // { jenis, i, v, x0, y0 }
  undo: [],
  kotor: false,
  sisip: [],            // titik prompt SAM untuk perbaikan bertahap
};

const c = document.getElementById('c');
const g = c.getContext('2d');
const wrap = document.getElementById('wrap');
const img = new Image();

// ---------------------------------------------------------------- warna kelas

// Rumus yang sama dengan cls_color di server, supaya warna sebuah kelas
// konsisten antara grid, thumbnail, dan kanvas.
function hashKode(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}
function warna(label, alpha) {
  const h = (hashKode(String(label)) % 997) / 997 * 360;
  return `hsla(${h.toFixed(0)},62%,55%,${alpha})`;
}

// ---------------------------------------------------------------- transform

const keLayarX = x => x * S.zoom + S.panx;
const keLayarY = y => y * S.zoom + S.pany;
const keGambarX = x => (x - S.panx) / S.zoom;
const keGambarY = y => (y - S.pany) / S.zoom;

function ukur() {
  c.width = wrap.clientWidth;
  c.height = wrap.clientHeight;
}

function muatKeLayar() {
  const sx = c.width / D.W, sy = c.height / D.H;
  S.zoom = Math.min(sx, sy) * 0.96;
  S.panx = (c.width - D.W * S.zoom) / 2;
  S.pany = (c.height - D.H * S.zoom) / 2;
  gambar();
}

function zoomDi(faktor, cx, cy) {
  const gx = keGambarX(cx), gy = keGambarY(cy);
  S.zoom = Math.min(Math.max(S.zoom * faktor, 0.05), 40);
  S.panx = cx - gx * S.zoom;
  S.pany = cy - gy * S.zoom;
  gambar();
}

// ---------------------------------------------------------------- gambar

function gambar() {
  g.clearRect(0, 0, c.width, c.height);
  if (img.complete && img.naturalWidth) {
    g.imageSmoothingEnabled = S.zoom < 4;
    g.drawImage(img, S.panx, S.pany, D.W * S.zoom, D.H * S.zoom);
  }

  S.shapes.forEach((s, i) => gambarBentuk(s, i === S.sel));

  if (S.draft) gambarDraft(S.draft);
  if (S.seret && S.seret.jenis === 'kotak') gambarKotak(S.seret);
  S.sisip.forEach(p => titikPrompt(p));
}

function gambarBentuk(s, terpilih) {
  const p = s.points;
  if (p.length < 2) return;
  g.beginPath();
  g.moveTo(keLayarX(p[0][0]), keLayarY(p[0][1]));
  for (let i = 1; i < p.length; i++) g.lineTo(keLayarX(p[i][0]), keLayarY(p[i][1]));
  g.closePath();
  g.fillStyle = warna(s.label, terpilih ? 0.38 : 0.22);
  g.fill();
  g.strokeStyle = warna(s.label, 1);
  g.lineWidth = terpilih ? 2.5 : 1.6;
  g.stroke();

  if (terpilih) {
    p.forEach(([x, y]) => {
      g.beginPath();
      g.arc(keLayarX(x), keLayarY(y), 4, 0, 6.2832);
      g.fillStyle = '#fff';
      g.fill();
      g.strokeStyle = warna(s.label, 1);
      g.lineWidth = 1.6;
      g.stroke();
    });
  }
}

function gambarDraft(d) {
  const p = d.points;
  if (!p.length) return;
  g.beginPath();
  g.moveTo(keLayarX(p[0][0]), keLayarY(p[0][1]));
  for (let i = 1; i < p.length; i++) g.lineTo(keLayarX(p[i][0]), keLayarY(p[i][1]));
  if (d.hover) g.lineTo(keLayarX(d.hover[0]), keLayarY(d.hover[1]));
  g.strokeStyle = warna(S.label, 1);
  g.lineWidth = 1.8;
  g.setLineDash([5, 4]);
  g.stroke();
  g.setLineDash([]);
  p.forEach(([x, y]) => {
    g.beginPath(); g.arc(keLayarX(x), keLayarY(y), 3.5, 0, 6.2832);
    g.fillStyle = '#fff'; g.fill();
    g.strokeStyle = warna(S.label, 1); g.lineWidth = 1.4; g.stroke();
  });
}

function gambarKotak(k) {
  g.strokeStyle = '#fff';
  g.lineWidth = 1.4;
  g.setLineDash([6, 4]);
  g.strokeRect(Math.min(k.x0, k.x1), Math.min(k.y0, k.y1),
               Math.abs(k.x1 - k.x0), Math.abs(k.y1 - k.y0));
  g.setLineDash([]);
}

function titikPrompt(p) {
  g.beginPath();
  g.arc(keLayarX(p.x), keLayarY(p.y), 5, 0, 6.2832);
  g.fillStyle = p.label ? '#38d16a' : '#e8483a';
  g.fill();
  g.strokeStyle = '#fff'; g.lineWidth = 1.6; g.stroke();
}

// ---------------------------------------------------------------- hit test

function dekatVertex(gx, gy) {
  const r = 7 / S.zoom;
  for (let i = S.shapes.length - 1; i >= 0; i--) {
    const p = S.shapes[i].points;
    for (let v = 0; v < p.length; v++) {
      if (Math.abs(p[v][0] - gx) < r && Math.abs(p[v][1] - gy) < r) return { i, v };
    }
  }
  return null;
}

function didalam(s, gx, gy) {
  const p = s.points;
  let ada = false;
  for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
    if (((p[i][1] > gy) !== (p[j][1] > gy)) &&
        (gx < (p[j][0] - p[i][0]) * (gy - p[i][1]) / (p[j][1] - p[i][1]) + p[i][0])) {
      ada = !ada;
    }
  }
  return ada;
}

function bentukDi(gx, gy) {
  for (let i = S.shapes.length - 1; i >= 0; i--) {
    if (didalam(S.shapes[i], gx, gy)) return i;
  }
  return -1;
}

// ---------------------------------------------------------------- riwayat

function simpanUndo() {
  S.undo.push(JSON.stringify(S.shapes));
  if (S.undo.length > 40) S.undo.shift();
}
function urungkan() {
  if (!S.undo.length) { toast('Tidak ada yang bisa diurungkan'); return; }
  S.shapes = JSON.parse(S.undo.pop());
  S.sel = -1;
  tandaiKotor();
  render();
}
function tandaiKotor() {
  S.kotor = true;
  document.getElementById('btn-simpan').setAttribute('data-kotor', '');
}

// ---------------------------------------------------------------- mode

function setMode(m) {
  S.mode = m;
  S.draft = null;
  S.sisip = [];
  document.querySelectorAll('.tool[data-mode]').forEach(b => {
    if (b.dataset.mode === m) b.setAttribute('data-on', ''); else b.removeAttribute('data-on');
  });
  wrap.dataset.mode = m;
  const nama = { sam: 'Klik SAM', box: 'Kotak SAM', poly: 'Poligon manual', edit: 'Sunting' }[m];
  document.getElementById('modeinfo').innerHTML = 'Mode: <b>' + nama + '</b>';
  gambar();
}

// ---------------------------------------------------------------- SAM

let samSibuk = false;

async function mintaSam(muatan) {
  if (samSibuk) return null;
  if (!S.label) { toast('Pilih kelas dulu di panel Labels'); return null; }
  samSibuk = true;
  document.getElementById('busy').setAttribute('data-on', '');
  const t0 = performance.now();
  try {
    const r = await fetch('/api/sam', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path: D.path,
        model: document.getElementById('model').value,
        eps: document.getElementById('eps').value / 10000,
        ...muatan,
      }),
    });
    const j = await r.json();
    if (r.status === 401) { toast('Sesi habis — masuk lagi'); location.href = '/login'; return null; }
    if (!j.ok) { toast('SAM: ' + (j.error || j.detail || 'gagal')); return null; }
    const ms = Math.round(performance.now() - t0);
    status(`${j.points.length} titik · ${ms} ms${j.dari_cache ? '' : ' (encoder jalan)'}`);
    return j;
  } catch (e) {
    toast('Gagal menghubungi server');
    return null;
  } finally {
    samSibuk = false;
    document.getElementById('busy').removeAttribute('data-on');
  }
}

async function samDariTitik() {
  if (!S.sisip.length) return;
  const j = await mintaSam({
    points: S.sisip.map(p => [p.x, p.y]),
    point_labels: S.sisip.map(p => p.label),
  });
  if (!j) return;
  // Titik tambahan memperbaiki bentuk yang sama, tidak menambah bentuk baru.
  if (S.sisip.length === 1) {
    simpanUndo();
    S.shapes.push({ label: S.label, shape_type: 'polygon', points: j.points });
    S.sel = S.shapes.length - 1;
  } else {
    S.shapes[S.sel].points = j.points;
  }
  tandaiKotor();
  render();
}

async function samDariKotak(x0, y0, x1, y1) {
  const j = await mintaSam({ box: [x0, y0, x1, y1] });
  if (!j) return;
  simpanUndo();
  S.shapes.push({ label: S.label, shape_type: 'polygon', points: j.points });
  S.sel = S.shapes.length - 1;
  S.sisip = [];
  tandaiKotor();
  render();
}

// ---------------------------------------------------------------- mouse

let geser = null;

c.addEventListener('mousedown', ev => {
  const gx = keGambarX(ev.offsetX), gy = keGambarY(ev.offsetY);

  // Geser tampilan: tombol tengah, atau Space ditahan.
  if (ev.button === 1 || spasi) {
    geser = { x: ev.offsetX, y: ev.offsetY, px: S.panx, py: S.pany };
    wrap.setAttribute('data-geser', '');
    return;
  }
  if (ev.button === 2) return;                     // klik kanan diurus contextmenu

  if (S.mode === 'edit') {
    const v = dekatVertex(gx, gy);
    if (v) {
      simpanUndo();
      S.sel = v.i;
      S.seret = { jenis: 'vertex', i: v.i, v: v.v };
      render();
      return;
    }
    const i = bentukDi(gx, gy);
    S.sel = i;
    if (i >= 0) {
      simpanUndo();
      S.seret = { jenis: 'bentuk', i, x0: gx, y0: gy,
                  awal: S.shapes[i].points.map(p => [p[0], p[1]]) };
    }
    render();
    return;
  }

  if (S.mode === 'poly') {
    if (!S.draft) S.draft = { points: [] };
    S.draft.points.push([gx, gy]);
    gambar();
    return;
  }

  if (S.mode === 'box') {
    S.seret = { jenis: 'kotak', x0: ev.offsetX, y0: ev.offsetY,
                x1: ev.offsetX, y1: ev.offsetY };
    return;
  }

  if (S.mode === 'sam') {
    // Klik biasa = titik objek. Shift+klik = titik BUKAN objek, untuk
    // memangkas mask yang meluber — setara klik kanan di AnyLabeling.
    if (!ev.shiftKey) S.sisip = [];
    S.sisip.push({ x: gx, y: gy, label: ev.shiftKey ? 0 : 1 });
    gambar();
    samDariTitik();
  }
});

c.addEventListener('mousemove', ev => {
  const gx = keGambarX(ev.offsetX), gy = keGambarY(ev.offsetY);
  document.getElementById('koord').textContent =
    `${Math.round(gx)}, ${Math.round(gy)}  ·  ${Math.round(S.zoom * 100)}%`;

  if (geser) {
    S.panx = geser.px + (ev.offsetX - geser.x);
    S.pany = geser.py + (ev.offsetY - geser.y);
    gambar();
    return;
  }
  if (S.seret) {
    if (S.seret.jenis === 'vertex') {
      S.shapes[S.seret.i].points[S.seret.v] = [gx, gy];
      tandaiKotor();
    } else if (S.seret.jenis === 'bentuk') {
      const dx = gx - S.seret.x0, dy = gy - S.seret.y0;
      S.shapes[S.seret.i].points = S.seret.awal.map(p => [p[0] + dx, p[1] + dy]);
      tandaiKotor();
    } else if (S.seret.jenis === 'kotak') {
      S.seret.x1 = ev.offsetX;
      S.seret.y1 = ev.offsetY;
    }
    gambar();
    return;
  }
  if (S.draft) { S.draft.hover = [gx, gy]; gambar(); }
});

window.addEventListener('mouseup', ev => {
  if (geser) { geser = null; wrap.removeAttribute('data-geser'); return; }
  if (!S.seret) return;
  const s = S.seret;
  S.seret = null;
  if (s.jenis === 'kotak') {
    const x0 = keGambarX(Math.min(s.x0, s.x1)), y0 = keGambarY(Math.min(s.y0, s.y1));
    const x1 = keGambarX(Math.max(s.x0, s.x1)), y1 = keGambarY(Math.max(s.y0, s.y1));
    gambar();
    if (Math.abs(x1 - x0) > 3 && Math.abs(y1 - y0) > 3) samDariKotak(x0, y0, x1, y1);
    else toast('Kotaknya terlalu kecil');
  } else {
    render();
  }
});

c.addEventListener('dblclick', () => { if (S.mode === 'poly') tutupDraft(); });

c.addEventListener('contextmenu', ev => {
  ev.preventDefault();
  if (S.mode === 'poly' && S.draft && S.draft.points.length) {
    S.draft.points.pop();                 // klik kanan membatalkan titik terakhir
    gambar();
  }
});

wrap.addEventListener('wheel', ev => {
  ev.preventDefault();
  zoomDi(ev.deltaY < 0 ? 1.15 : 1 / 1.15, ev.offsetX, ev.offsetY);
}, { passive: false });

function tutupDraft() {
  if (!S.draft || S.draft.points.length < 3) { toast('Poligon perlu minimal 3 titik'); return; }
  simpanUndo();
  S.shapes.push({ label: S.label, shape_type: 'polygon', points: S.draft.points });
  S.sel = S.shapes.length - 1;
  S.draft = null;
  tandaiKotor();
  render();
}

// ---------------------------------------------------------------- papan tombol

let spasi = false;

window.addEventListener('keydown', ev => {
  if (ev.target.tagName === 'INPUT' || ev.target.tagName === 'SELECT') return;

  if (ev.key === ' ') { spasi = true; ev.preventDefault(); return; }
  if (ev.ctrlKey && ev.key.toLowerCase() === 'z') { ev.preventDefault(); urungkan(); return; }
  if (ev.ctrlKey && ev.key.toLowerCase() === 's') { ev.preventDefault(); simpan(); return; }

  const k = ev.key.toLowerCase();
  if (k === 's') setMode('sam');
  else if (k === 'b') setMode('box');
  else if (k === 'p') setMode('poly');
  else if (k === 'e') setMode('edit');
  else if (k === 'f') muatKeLayar();
  else if (k === 'a') pindah(D.prev);
  else if (k === 'd') pindah(D.next);
  else if (ev.key === 'Enter') { if (S.mode === 'poly') tutupDraft(); }
  else if (ev.key === 'Escape') { S.draft = null; S.sisip = []; S.sel = -1; render(); }
  else if (ev.key === 'Delete' || ev.key === 'Backspace') hapusTerpilih();
});

window.addEventListener('keyup', ev => { if (ev.key === ' ') spasi = false; });

function hapusTerpilih() {
  if (S.sel < 0) { toast('Pilih bentuknya dulu (mode Sunting)'); return; }
  simpanUndo();
  S.shapes.splice(S.sel, 1);
  S.sel = -1;
  tandaiKotor();
  render();
}

// ---------------------------------------------------------------- panel

function renderKelas() {
  const el = document.getElementById('kelas');
  el.innerHTML = '';
  if (!S.kelas.length) {
    el.innerHTML = '<div class="obj-kosong">Belum ada kelas — tulis di bawah.</div>';
  }
  S.kelas.forEach(k => {
    const d = document.createElement('div');
    d.className = 'kelas';
    if (k === S.label) d.setAttribute('data-on', '');
    d.innerHTML = `<i style="background:${warna(k, 1)}"></i><span></span>`;
    d.querySelector('span').textContent = k;
    d.onclick = () => {
      S.label = k;
      // Kelas dipilih saat ada bentuk terpilih -> ganti kelas bentuk itu.
      if (S.sel >= 0) { simpanUndo(); S.shapes[S.sel].label = k; tandaiKotor(); }
      render();
    };
    el.appendChild(d);
  });
}

function renderObjek() {
  const el = document.getElementById('objek');
  document.getElementById('nobj').textContent = S.shapes.length;
  el.innerHTML = '';
  if (!S.shapes.length) {
    el.innerHTML = '<div class="obj-kosong">Belum ada objek. Klik objek di gambar.</div>';
    return;
  }
  S.shapes.forEach((s, i) => {
    const d = document.createElement('div');
    d.className = 'obj';
    if (i === S.sel) d.setAttribute('data-on', '');
    d.innerHTML = `<i style="background:${warna(s.label, 1)}"></i><span></span>`
                + `<b>${s.points.length} titik</b>`;
    d.querySelector('span').textContent = s.label || '(tanpa kelas)';
    d.onclick = () => { S.sel = i; setMode('edit'); render(); };
    el.appendChild(d);
  });
}

function renderBerkas() {
  const el = document.getElementById('berkas');
  const q = document.getElementById('cari').value.trim().toLowerCase();
  el.innerHTML = '';
  D.berkas.filter(f => !q || f.nama.toLowerCase().includes(q)).forEach(f => {
    const a = document.createElement('a');
    a.className = 'fitem';
    a.href = '/label?path=' + encodeURIComponent(f.path);
    a.dataset.sev = f.sev;
    if (f.path === D.path) a.setAttribute('data-on', '');
    a.innerHTML = '<i></i><span></span>' + (f.n ? `<b>${f.n}</b>` : '');
    a.querySelector('span').textContent = f.nama;
    a.onclick = e => {
      if (S.kotor && !confirm('Ada perubahan belum disimpan. Tinggalkan?')) e.preventDefault();
    };
    el.appendChild(a);
  });
}

function render() {
  gambar();
  renderKelas();
  renderObjek();
}

function status(t) { document.getElementById('status').textContent = t; }

// ---------------------------------------------------------------- simpan

async function simpan() {
  const tanpaKelas = S.shapes.filter(s => !s.label).length;
  if (tanpaKelas) { toast(`${tanpaKelas} bentuk belum punya kelas`); return; }
  status('Menyimpan…');
  try {
    const r = await fetch('/api/simpan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: D.path, shapes: S.shapes }),
    });
    const j = await r.json();
    if (!j.ok) { toast('Gagal simpan: ' + (j.error || j.detail)); status('Gagal simpan'); return; }
    S.kotor = false;
    document.getElementById('btn-simpan').removeAttribute('data-kotor');
    const f = D.berkas.find(x => x.path === D.path);
    if (f) { f.n = j.n; f.sev = j.sev; renderBerkas(); }
    status(`Tersimpan · ${j.n} objek · ${(j.issues || []).join(' · ') || 'tidak ada temuan'}`);
    toast('Tersimpan');
  } catch (e) {
    toast('Gagal menghubungi server');
    status('Gagal simpan');
  }
}

function pindah(path) {
  if (!path) { toast('Sudah di ujung'); return; }
  if (S.kotor && !confirm('Ada perubahan belum disimpan. Tinggalkan?')) return;
  location.href = '/label?path=' + encodeURIComponent(path);
}

window.addEventListener('beforeunload', ev => {
  if (S.kotor) { ev.preventDefault(); ev.returnValue = ''; }
});

// ---------------------------------------------------------------- pasang

document.querySelectorAll('.tool[data-mode]').forEach(b => {
  b.onclick = () => setMode(b.dataset.mode);
});
document.getElementById('btn-del').onclick = hapusTerpilih;
document.getElementById('btn-undo').onclick = urungkan;
document.getElementById('btn-simpan').onclick = simpan;
document.getElementById('btn-fit').onclick = muatKeLayar;
document.getElementById('btn-zin').onclick = () => zoomDi(1.25, c.width / 2, c.height / 2);
document.getElementById('btn-zout').onclick = () => zoomDi(1 / 1.25, c.width / 2, c.height / 2);
document.getElementById('cari').oninput = renderBerkas;

document.getElementById('kelasbaru').addEventListener('keydown', ev => {
  if (ev.key !== 'Enter') return;
  const v = ev.target.value.trim();
  if (!v) return;
  if (!S.kelas.includes(v)) S.kelas.push(v);
  S.kelas.sort();
  S.label = v;
  ev.target.value = '';
  if (S.sel >= 0) { simpanUndo(); S.shapes[S.sel].label = v; tandaiKotor(); }
  render();
});

document.getElementById('prev').onclick = e => { e.preventDefault(); pindah(D.prev); };
document.getElementById('next').onclick = e => { e.preventDefault(); pindah(D.next); };

window.addEventListener('resize', () => { ukur(); gambar(); });

img.onload = () => { ukur(); muatKeLayar(); render(); status(`${D.nama} siap`); };
img.onerror = () => { status('Gambar gagal dimuat'); toast('Gambar gagal dimuat'); };
img.src = '/gambar?path=' + encodeURIComponent(D.path);

setMode('sam');
renderBerkas();
render();
