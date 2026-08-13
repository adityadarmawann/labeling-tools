'use strict';
/*
 * Kanvas anotasi.
 *
 * Alur auto-labeling mengikuti AnyLabeling dan itu disengaja: prompt menumpuk,
 * poligonnya muncul lebih dulu sebagai PRATINJAU, diperbaiki dengan +Point /
 * -Point, baru disahkan jadi objek dengan Finish Object. Tanpa tahap pratinjau,
 * setiap klik langsung mengotori daftar objek dan memperbaikinya jadi lebih
 * mahal daripada mengulang.
 *
 * Koordinat: semua bentuk disimpan dalam koordinat GAMBAR (piksel asli), bukan
 * koordinat layar. Zoom dan pan hanya mengubah cara menggambar. Tanpa disiplin
 * ini, hasil anotasi bergeser setiap kali orang mengubah zoom.
 */

const D = JSON.parse(document.getElementById('data-awal').textContent);

const S = {
  shapes: D.shapes.map(s => ({ ...s, points: s.points.map(p => [p[0], p[1]]) })),
  kelas: D.kelas.slice(),
  label: D.kelas[0] || '',
  mode: 'p+',
  sel: -1,
  selv: -1,        // indeks titik terpilih, untuk Backspace
  zoom: 1, panx: 0, pany: 0,
  prompt: [],        // { x, y, label } — 1 = objek, 0 = bukan objek
  kotak: null,       // prompt kotak terakhir, dalam koordinat gambar
  pratinjau: null,   // { points } hasil SAM, belum jadi objek
  draft: null,       // poligon manual yang sedang digambar
  seret: null,
  kursor: null,      // posisi kursor dalam koordinat gambar, untuk garis silang
  undo: [],
  kotor: false,
};

const c = document.getElementById('c');
const g = c.getContext('2d');
const wrap = document.getElementById('wrap');
const img = new Image();
const el = id => document.getElementById(id);

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

/*
 * Gaya seleksi mengikuti bawaan AnyLabeling (~/.anylabelingrc):
 *   select_line_color [255,255,255,255]  vertex_fill_color [0,255,0,255]
 *   hvertex_fill_color [255,255,255,255] point_size 8
 * Warna isian tetap per kelas, bukan hijau seragam seperti AnyLabeling —
 * dengan banyak kelas itu jauh lebih terbaca, dan warnanya sama dengan grid.
 */
const GARIS_PILIH = 'rgba(255,255,255,1)';
const ISI_VERTEX = 'rgba(0,255,0,1)';
const UKURAN_TITIK = 8;

function bentukBaru(jenis, titik) {
  return { label: S.label, shape_type: jenis, points: titik,
           text: '', group_id: null, flags: {}, titipan: {} };
}

// ---------------------------------------------------------------- transform

const keLayarX = x => x * S.zoom + S.panx;
const keLayarY = y => y * S.zoom + S.pany;
const keGambarX = x => (x - S.panx) / S.zoom;
const keGambarY = y => (y - S.pany) / S.zoom;

function ukur() { c.width = wrap.clientWidth; c.height = wrap.clientHeight; }

function muatKeLayar() {
  S.zoom = Math.min(c.width / D.W, c.height / D.H) * 0.96;
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

  S.shapes.forEach((s, i) => { if (!s.sembunyi) gambarBentuk(s, i === S.sel); });

  if (S.pratinjau) gambarPratinjau(S.pratinjau);
  if (S.draft) gambarDraft(S.draft);
  if (S.seret && S.seret.jenis.startsWith('kotak')) gambarKotakSeret(S.seret);
  if (S.kotak && S.pratinjau) gambarKotakGambar(S.kotak);
  S.prompt.forEach(p => titikPrompt(p));
  if (S.kursor && el('silang').checked) garisSilang(S.kursor);
}

function jalur(p) {
  g.beginPath();
  g.moveTo(keLayarX(p[0][0]), keLayarY(p[0][1]));
  for (let i = 1; i < p.length; i++) g.lineTo(keLayarX(p[i][0]), keLayarY(p[i][1]));
  g.closePath();
}

function gambarBentuk(s, terpilih) {
  const p = titikTampil(s);
  if (p.length < 2) return;
  jalur(p);
  g.fillStyle = warna(s.label, terpilih ? 0.38 : 0.2);
  g.fill();
  g.strokeStyle = terpilih ? GARIS_PILIH : warna(s.label, 1);
  g.lineWidth = terpilih ? 2.5 : 1.6;
  g.stroke();
  if (terpilih) {
    p.forEach(([x, y], v) => bulatan(x, y, UKURAN_TITIK / 2,
      v === S.selv ? '#fff' : ISI_VERTEX, GARIS_PILIH));
  }
}

// Rectangle disimpan 2 titik (konvensi labelme); untuk digambar dijadikan 4.
function titikTampil(s) {
  const p = s.points;
  if (s.shape_type === 'rectangle' && p.length === 2) {
    return [[p[0][0], p[0][1]], [p[1][0], p[0][1]], [p[1][0], p[1][1]], [p[0][0], p[1][1]]];
  }
  return p;
}

function gambarPratinjau(pv) {
  jalur(pv.points);
  g.fillStyle = 'rgba(56,209,106,.22)';
  g.fill();
  g.strokeStyle = '#38d16a';
  g.lineWidth = 2;
  g.setLineDash([7, 4]);
  g.stroke();
  g.setLineDash([]);
  pv.points.forEach(([x, y]) => bulatan(x, y, 3, '#fff', '#38d16a'));
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
  p.forEach(([x, y]) => bulatan(x, y, 3.5, '#fff', warna(S.label, 1)));
}

function gambarKotakSeret(k) {
  g.strokeStyle = '#fff'; g.lineWidth = 1.4; g.setLineDash([6, 4]);
  g.strokeRect(Math.min(k.x0, k.x1), Math.min(k.y0, k.y1),
               Math.abs(k.x1 - k.x0), Math.abs(k.y1 - k.y0));
  g.setLineDash([]);
}

function gambarKotakGambar(b) {
  g.strokeStyle = 'rgba(255,255,255,.5)'; g.lineWidth = 1; g.setLineDash([4, 4]);
  g.strokeRect(keLayarX(b[0]), keLayarY(b[1]),
               (b[2] - b[0]) * S.zoom, (b[3] - b[1]) * S.zoom);
  g.setLineDash([]);
}

function bulatan(x, y, r, isi, garis) {
  g.beginPath();
  g.arc(keLayarX(x), keLayarY(y), r, 0, 6.2832);
  g.fillStyle = isi; g.fill();
  g.strokeStyle = garis; g.lineWidth = 1.5; g.stroke();
}

function titikPrompt(p) {
  bulatan(p.x, p.y, 5, p.label ? '#38d16a' : '#e8483a', '#fff');
}

function garisSilang(k) {
  const x = keLayarX(k[0]), y = keLayarY(k[1]);
  g.strokeStyle = 'rgba(56,209,106,.55)';
  g.lineWidth = 1;
  g.setLineDash([5, 4]);
  g.beginPath();
  g.moveTo(0, y); g.lineTo(c.width, y);
  g.moveTo(x, 0); g.lineTo(x, c.height);
  g.stroke();
  g.setLineDash([]);
}

// ---------------------------------------------------------------- hit test

function dekatVertex(gx, gy) {
  const r = 7 / S.zoom;
  for (let i = S.shapes.length - 1; i >= 0; i--) {
    if (S.shapes[i].sembunyi) continue;
    const p = S.shapes[i].points;
    for (let v = 0; v < p.length; v++) {
      if (Math.abs(p[v][0] - gx) < r && Math.abs(p[v][1] - gy) < r) return { i, v };
    }
  }
  return null;
}

function didalam(s, gx, gy) {
  const p = titikTampil(s);
  let ada = false;
  for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
    if (((p[i][1] > gy) !== (p[j][1] > gy)) &&
        (gx < (p[j][0] - p[i][0]) * (gy - p[i][1]) / (p[j][1] - p[i][1]) + p[i][0])) ada = !ada;
  }
  return ada;
}

function bentukDi(gx, gy) {
  for (let i = S.shapes.length - 1; i >= 0; i--) {
    if (!S.shapes[i].sembunyi && didalam(S.shapes[i], gx, gy)) return i;
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
  el('btn-simpan').setAttribute('data-kotor', '');
}

// ---------------------------------------------------------------- mode

const NAMA_MODE = { 'p+': '+Point', 'p-': '−Point', rect: '+Rect',
                    kotak: 'Rectangle manual', poly: 'Poligon manual',
                    edit: 'Sunting' };

function setMode(m) {
  S.mode = m;
  S.draft = null;
  document.querySelectorAll('.tool[data-mode]').forEach(b => {
    b.toggleAttribute('data-on', b.dataset.mode === m);
  });
  ['p+', 'p-', 'rect'].forEach(k => {
    const b = el('ab-' + k);
    if (b) b.toggleAttribute('data-on', k === m);
  });
  wrap.dataset.mode = m;
  el('modeinfo').innerHTML = 'Mode: <b>' + NAMA_MODE[m] + '</b>';
  gambar();
}

// ---------------------------------------------------------------- SAM

let samSibuk = false;

async function mintaSam(muatan) {
  if (samSibuk) return null;
  samSibuk = true;
  el('busy').setAttribute('data-on', '');
  const t0 = performance.now();
  try {
    const r = await fetch('/api/sam', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path: D.path,
        model: el('model').value,
        eps: el('eps').value / 10000,
        ...muatan,
      }),
    });
    const j = await r.json();
    if (r.status === 401) { toast('Sesi habis — masuk lagi'); location.href = '/login'; return null; }
    if (!j.ok) { pesan('SAM: ' + (j.error || j.detail || 'gagal')); return null; }
    pesan(`Selesai · ${j.points.length} titik · ${Math.round(performance.now() - t0)} ms`
          + (j.dari_cache ? '' : ' (encoder jalan)') + ' — periksa hasilnya, lalu Finish Object (F)');
    return j;
  } catch (e) {
    pesan('Gagal menghubungi server');
    return null;
  } finally {
    samSibuk = false;
    el('busy').removeAttribute('data-on');
  }
}

/** Jalankan ulang SAM dengan seluruh prompt yang terkumpul. */
async function jalankanSam() {
  if (!S.prompt.length && !S.kotak) return;
  const muatan = S.kotak
    ? { box: S.kotak,
        points: S.prompt.length ? S.prompt.map(p => [p.x, p.y]) : undefined,
        point_labels: S.prompt.length ? S.prompt.map(p => p.label) : undefined }
    : { points: S.prompt.map(p => [p.x, p.y]),
        point_labels: S.prompt.map(p => p.label) };
  // Prompt kotak dan titik tidak bisa digabung di satu panggilan SAM: kotak
  // memakai label 2/3 sedangkan titik memakai 1/0. Kalau keduanya ada, titik
  // yang menang karena itu perbaikan yang baru saja diminta pengguna.
  if (S.kotak && S.prompt.length) delete muatan.box;
  const j = await mintaSam(muatan);
  if (!j) return;
  S.pratinjau = { points: j.points };
  el('ab-finish').setAttribute('data-siap', '');
  gambar();
}

function bersihkanPrompt() {
  S.prompt = [];
  S.kotak = null;
  S.pratinjau = null;
  el('ab-finish').removeAttribute('data-siap');
  pesan('Prompt dibersihkan');
  gambar();
}

/** Sahkan pratinjau jadi objek — setara Finish Object (f) di AnyLabeling. */
function finishObject() {
  if (!S.pratinjau) { toast('Belum ada pratinjau. Klik objeknya dulu.'); return; }
  if (!S.label) { toast('Pilih kelas dulu di panel Labels'); return; }
  simpanUndo();
  let titik = S.pratinjau.points;
  let jenis = 'polygon';
  if (el('output').value === 'rectangle') {
    const xs = titik.map(p => p[0]), ys = titik.map(p => p[1]);
    titik = [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]];
    jenis = 'rectangle';
  }
  S.shapes.push(bentukBaru(jenis, titik));
  S.sel = S.shapes.length - 1;
  S.prompt = [];
  S.kotak = null;
  S.pratinjau = null;
  el('ab-finish').removeAttribute('data-siap');
  tandaiKotor();
  pesan(`Objek disahkan sebagai "${S.label}"`);
  render();
}

// ---------------------------------------------------------------- mouse

let geser = null, spasi = false;

c.addEventListener('mousedown', ev => {
  const gx = keGambarX(ev.offsetX), gy = keGambarY(ev.offsetY);

  if (ev.button === 1 || spasi) {
    geser = { x: ev.offsetX, y: ev.offsetY, px: S.panx, py: S.pany };
    wrap.setAttribute('data-geser', '');
    return;
  }
  if (ev.button === 2) return;

  if (S.mode === 'edit') {
    const v = dekatVertex(gx, gy);
    if (v) {
      simpanUndo();
      S.sel = v.i;
      S.selv = v.v;
      S.seret = { jenis: 'vertex', i: v.i, v: v.v };
      render();
      return;
    }
    const i = bentukDi(gx, gy);
    S.sel = i;
    S.selv = -1;
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

  if (S.mode === 'rect' || S.mode === 'kotak') {
    S.seret = { jenis: S.mode === 'kotak' ? 'kotakmanual' : 'kotak',
                x0: ev.offsetX, y0: ev.offsetY, x1: ev.offsetX, y1: ev.offsetY };
    return;
  }

  // +Point / -Point: prompt menumpuk, lalu SAM dijalankan ulang.
  // Shift juga membalik tanda, supaya tidak perlu bolak-balik tombol.
  const negatif = (S.mode === 'p-') !== ev.shiftKey;
  S.prompt.push({ x: gx, y: gy, label: negatif ? 0 : 1 });
  gambar();
  jalankanSam();
});

c.addEventListener('mousemove', ev => {
  const gx = keGambarX(ev.offsetX), gy = keGambarY(ev.offsetY);
  S.kursor = [gx, gy];
  el('koord').textContent =
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
    } else {
      S.seret.x1 = ev.offsetX;
      S.seret.y1 = ev.offsetY;
    }
    gambar();
    return;
  }
  if (S.draft) S.draft.hover = [gx, gy];
  gambar();
});

c.addEventListener('mouseleave', () => { S.kursor = null; gambar(); });

window.addEventListener('mouseup', () => {
  if (geser) { geser = null; wrap.removeAttribute('data-geser'); return; }
  if (!S.seret) return;
  const s = S.seret;
  S.seret = null;
  if (s.jenis.startsWith('kotak')) {
    const x0 = keGambarX(Math.min(s.x0, s.x1)), y0 = keGambarY(Math.min(s.y0, s.y1));
    const x1 = keGambarX(Math.max(s.x0, s.x1)), y1 = keGambarY(Math.max(s.y0, s.y1));
    if (Math.abs(x1 - x0) <= 3 || Math.abs(y1 - y0) <= 3) {
      toast('Kotaknya terlalu kecil');
    } else if (s.jenis === 'kotakmanual') {
      // Rectangle manual (R): langsung jadi objek, tanpa SAM — persis
      // create_rectangle di AnyLabeling.
      if (!S.label) { toast('Pilih kelas dulu'); }
      else {
        simpanUndo();
        S.shapes.push(bentukBaru('rectangle', [[x0, y0], [x1, y1]]));
        S.sel = S.shapes.length - 1;
        tandaiKotor();
        render();
      }
    } else {
      S.prompt = [];
      S.kotak = [x0, y0, x1, y1];
      jalankanSam();
    }
    gambar();
  } else {
    render();
  }
});

c.addEventListener('dblclick', () => { if (S.mode === 'poly') tutupDraft(); });

c.addEventListener('contextmenu', ev => {
  ev.preventDefault();
  if (S.mode === 'poly' && S.draft && S.draft.points.length) {
    S.draft.points.pop();
    gambar();
  } else if (S.prompt.length) {
    S.prompt.pop();                     // klik kanan mencabut prompt terakhir
    if (S.prompt.length || S.kotak) jalankanSam();
    else bersihkanPrompt();
  }
});

wrap.addEventListener('wheel', ev => {
  ev.preventDefault();
  zoomDi(ev.deltaY < 0 ? 1.15 : 1 / 1.15, ev.offsetX, ev.offsetY);
}, { passive: false });

function tutupDraft() {
  if (!S.draft || S.draft.points.length < 3) { toast('Poligon perlu minimal 3 titik'); return; }
  if (!S.label) { toast('Pilih kelas dulu'); return; }
  simpanUndo();
  S.shapes.push(bentukBaru('polygon', S.draft.points));
  S.sel = S.shapes.length - 1;
  S.draft = null;
  tandaiKotor();
  render();
}

// ---------------------------------------------------------------- papan tombol

/*
 * Peta tombol mengikuti ~/.anylabelingrc supaya refleks orang yang sudah biasa
 * dengan AnyLabeling tetap berlaku. Yang paling penting: Backspace membuang
 * SATU TITIK, bukan seluruh objek — salah di sini berarti orang kehilangan
 * pekerjaan karena menekan tombol yang di aplikasi sebelahnya aman.
 */
window.addEventListener('keydown', ev => {
  if (ev.target.tagName === 'INPUT' || ev.target.tagName === 'SELECT') return;
  if (ev.key === ' ') { spasi = true; ev.preventDefault(); return; }

  if (ev.ctrlKey) {
    const ck = ev.key.toLowerCase();
    if (ck === 'z') { ev.preventDefault(); urungkan(); }
    else if (ck === 's') { ev.preventDefault(); simpan(); }
    else if (ck === 'f') { ev.preventDefault(); muatKeLayar(); }
    else if (ck === 'e') { ev.preventDefault(); ubahKelasTerpilih(); }
    else if (ck === 'd') { ev.preventDefault(); duplikatTerpilih(); }
    else if (ev.key === '0') { ev.preventDefault(); zoomAsli(); }
    else if (ev.key === '+' || ev.key === '=') { ev.preventDefault(); zoomDi(1.25, c.width / 2, c.height / 2); }
    else if (ev.key === '-') { ev.preventDefault(); zoomDi(1 / 1.25, c.width / 2, c.height / 2); }
    return;
  }

  const k = ev.key.toLowerCase();
  if (k === 'q') setMode('p+');
  else if (k === 'e') setMode('p-');
  else if (k === 'r') setMode('kotak');          // rectangle manual, seperti AnyLabeling
  else if (k === 'p') setMode('poly');
  else if (k === 'v') setMode('edit');
  else if (k === 'f') finishObject();
  else if (k === 'c') bersihkanPrompt();
  else if (k === 'a') pindah(D.prev);
  else if (k === 'd') pindah(D.next);
  else if (ev.key === 'Enter') { if (S.mode === 'poly') tutupDraft(); else finishObject(); }
  else if (ev.key === 'Escape') { S.draft = null; S.sel = -1; S.selv = -1; bersihkanPrompt(); render(); }
  else if (ev.key === 'Delete') hapusTerpilih();
  else if (ev.key === 'Backspace') { ev.preventDefault(); hapusTitikTerpilih(); }
});

window.addEventListener('keyup', ev => { if (ev.key === ' ') spasi = false; });

function hapusTerpilih() {
  if (S.sel < 0) { toast('Pilih bentuknya dulu (V untuk mode Sunting)'); return; }
  simpanUndo();
  S.shapes.splice(S.sel, 1);
  S.sel = -1;
  S.selv = -1;
  tandaiKotor();
  render();
}

/** Backspace di AnyLabeling: buang satu titik, bukan seluruh objek. */
function hapusTitikTerpilih() {
  if (S.sel < 0 || S.selv < 0) {
    toast('Klik dulu titik yang mau dibuang (mode Sunting)');
    return;
  }
  const s = S.shapes[S.sel];
  if (s.shape_type === 'rectangle') { toast('Rectangle tidak bisa dikurangi titiknya'); return; }
  if (s.points.length <= 3) { toast('Poligon minimal 3 titik — pakai Delete untuk membuang objeknya'); return; }
  simpanUndo();
  s.points.splice(S.selv, 1);
  S.selv = -1;
  tandaiKotor();
  render();
}

/** Ctrl+E: ubah kelas objek terpilih. */
function ubahKelasTerpilih() {
  if (S.sel < 0) { toast('Pilih objeknya dulu'); return; }
  const v = (prompt('Kelas untuk objek ini:', S.shapes[S.sel].label) || '').trim();
  if (!v) return;
  simpanUndo();
  S.shapes[S.sel].label = v;
  if (!S.kelas.includes(v)) { S.kelas.push(v); S.kelas.sort(); }
  S.label = v;
  tandaiKotor();
  render();
}

/** Ctrl+D: duplikat objek terpilih, digeser sedikit supaya terlihat. */
function duplikatTerpilih() {
  if (S.sel < 0) { toast('Pilih objeknya dulu'); return; }
  simpanUndo();
  const s = S.shapes[S.sel];
  S.shapes.push({ ...s, points: s.points.map(p => [p[0] + 8, p[1] + 8]) });
  S.sel = S.shapes.length - 1;
  tandaiKotor();
  render();
}

function zoomAsli() {
  const cx = c.width / 2, cy = c.height / 2;
  zoomDi(1 / S.zoom, cx, cy);
}

// ---------------------------------------------------------------- panel

function renderKelas() {
  const box = el('kelas');
  box.innerHTML = '';
  if (!S.kelas.length) {
    box.innerHTML = '<div class="obj-kosong">Belum ada kelas — tulis di bawah.</div>';
  }
  S.kelas.forEach(k => {
    const d = document.createElement('div');
    d.className = 'kelas';
    if (k === S.label) d.setAttribute('data-on', '');
    d.innerHTML = `<i style="background:${warna(k, 1)}"></i><span></span>`;
    d.querySelector('span').textContent = k;
    d.onclick = () => {
      S.label = k;
      if (S.sel >= 0) { simpanUndo(); S.shapes[S.sel].label = k; tandaiKotor(); }
      render();
    };
    box.appendChild(d);
  });
}

function renderObjek() {
  const box = el('objek');
  el('nobj').textContent = S.shapes.length;
  box.innerHTML = '';
  if (!S.shapes.length) {
    box.innerHTML = '<div class="obj-kosong">Belum ada objek. Klik objeknya, '
                  + 'lalu Finish Object.</div>';
    return;
  }
  S.shapes.forEach((s, i) => {
    const d = document.createElement('div');
    d.className = 'obj';
    if (i === S.sel) d.setAttribute('data-on', '');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = !s.sembunyi;
    cb.title = 'Tampilkan / sembunyikan';
    cb.onclick = e => { e.stopPropagation(); s.sembunyi = !cb.checked; gambar(); };
    d.appendChild(cb);
    const ik = document.createElement('i');
    ik.style.background = warna(s.label, 1);
    d.appendChild(ik);
    const sp = document.createElement('span');
    sp.textContent = s.label || '(tanpa kelas)';
    d.appendChild(sp);
    const b = document.createElement('b');
    b.textContent = s.shape_type === 'rectangle' ? 'kotak' : `${s.points.length} titik`;
    d.appendChild(b);
    d.onclick = () => { S.sel = i; setMode('edit'); render(); };
    box.appendChild(d);
  });
}

function renderBerkas() {
  const box = el('berkas');
  const q = el('cari').value.trim().toLowerCase();
  box.innerHTML = '';
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
    box.appendChild(a);
  });
}

function render() { gambar(); renderKelas(); renderObjek(); }
function status(t) { el('status').textContent = t; }
function pesan(t) { el('pesan').textContent = t; status(t); }

// ---------------------------------------------------------------- simpan

async function simpan() {
  const tanpaKelas = S.shapes.filter(s => !s.label).length;
  if (tanpaKelas) { toast(`${tanpaKelas} bentuk belum punya kelas`); return; }
  status('Menyimpan…');
  try {
    const r = await fetch('/api/simpan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path: D.path,
        // Seluruh field dikirim balik, termasuk `titipan` berisi field asing
        // dari AnyLabeling (difficult, attributes, ...). Kalau tidak, berkas
        // buatan desktop kehilangan datanya begitu disimpan dari web.
        shapes: S.shapes.map(s => ({
          label: s.label, shape_type: s.shape_type, points: s.points,
          text: s.text || '', group_id: s.group_id ?? null,
          flags: s.flags || {}, titipan: s.titipan || {},
        })),
        flags: D.flags_gambar || {},
      }),
    });
    const j = await r.json();
    if (!j.ok) { toast('Gagal simpan: ' + (j.error || j.detail)); status('Gagal simpan'); return; }
    S.kotor = false;
    el('btn-simpan').removeAttribute('data-kotor');
    const f = D.berkas.find(x => x.path === D.path);
    if (f) { f.n = j.n; f.sev = j.sev; renderBerkas(); }
    pesan(`Tersimpan · ${j.n} objek · ${(j.issues || []).join(' · ') || 'tidak ada temuan'}`);
    toast('Tersimpan');
  } catch (e) {
    toast('Gagal menghubungi server');
    status('Gagal simpan');
  }
}

async function pindah(path) {
  if (!path) { toast('Sudah di ujung'); return; }
  // AnyLabeling bawaannya auto_save: True — pindah gambar menyimpan sendiri.
  if (S.kotor && el('autosave').checked) {
    await simpan();
    if (S.kotor) { toast('Belum tersimpan — pindah dibatalkan'); return; }
  } else if (S.kotor && !confirm('Ada perubahan belum disimpan. Tinggalkan?')) {
    return;
  }
  location.href = '/label?path=' + encodeURIComponent(path);
}

window.addEventListener('beforeunload', ev => {
  if (S.kotor) { ev.preventDefault(); ev.returnValue = ''; }
});

// ---------------------------------------------------------------- pasang

document.querySelectorAll('.tool[data-mode]').forEach(b => {
  b.onclick = () => setMode(b.dataset.mode);
});
el('ab-p+').onclick = () => setMode('p+');
el('ab-p-').onclick = () => setMode('p-');
el('ab-rect').onclick = () => setMode('rect');
el('ab-clear').onclick = bersihkanPrompt;
el('ab-finish').onclick = finishObject;
el('btn-del').onclick = hapusTerpilih;
el('btn-undo').onclick = urungkan;
el('btn-simpan').onclick = simpan;
el('btn-fit').onclick = muatKeLayar;
el('btn-zin').onclick = () => zoomDi(1.25, c.width / 2, c.height / 2);
el('btn-zout').onclick = () => zoomDi(1 / 1.25, c.width / 2, c.height / 2);
el('cari').oninput = renderBerkas;
el('silang').onchange = gambar;
el('model').onchange = () => { if (S.prompt.length || S.kotak) jalankanSam(); };

el('kelasbaru').addEventListener('keydown', ev => {
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

el('prev').onclick = e => { e.preventDefault(); pindah(D.prev); };
el('next').onclick = e => { e.preventDefault(); pindah(D.next); };
window.addEventListener('resize', () => { ukur(); gambar(); });

img.onload = () => { ukur(); muatKeLayar(); render(); pesan(`${D.nama} siap`); };
img.onerror = () => { pesan('Gambar gagal dimuat'); toast('Gambar gagal dimuat'); };
img.src = '/gambar?path=' + encodeURIComponent(D.path);

setMode('p+');
renderBerkas();
render();
