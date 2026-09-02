'use strict';

/* Kanvas baca-saja.
   Penjaga sungguhannya di server (tugas.tolak_tulis); yang di sini semata
   supaya orang tidak menggambar setengah jam lalu kehilangan semuanya saat
   Simpan menolak. Karena itu ia menghentikan di pintu masuk penulisan, bukan
   menyembunyikan kanvasnya. */
const BACA_SAJA = document.body.hasAttribute('data-baca-saja');
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
  // KOSONG di awal, seperti unique_label_list AnyLabeling yang mulai tanpa
  // seleksi (label_widget.py:1914-1917). Dulu diisi kelas pertama abjad, dan
  // karena pindah gambar memuat ulang halaman, nilai itu selalu direset ke
  // situ — orang yang lupa mengklik ulang melabeli objek dengan kelas yang
  // sama sekali tidak ia maksud, tanpa satu pun peringatan.
  label: '',
  mode: 'p+',
  sel: -1,           // bentuk utama terpilih (untuk Text Editor & vertex)
  terpilih: [],      // SEMUA bentuk terpilih; Ctrl+klik menambah/mengurangi
  selv: -1,        // indeks titik terpilih, untuk Backspace
  zoom: 1, panx: 0, pany: 0,
  prompt: [],        // { x, y, label } — 1 = objek, 0 = bukan objek
  kotak: null,       // prompt kotak terakhir, dalam koordinat gambar
  pratinjau: null,   // { points } hasil SAM, belum jadi objek
  draft: null,       // poligon manual yang sedang digambar
  seret: null,
  kursor: null,      // posisi kursor dalam koordinat gambar, untuk garis silang
  undo: [],
  flags: { ...(D.flags_gambar || {}) },
  // Catatan tingkat GAMBAR, berbeda dari catatan per objek
  // (other_data["image_text"], label_widget.py:1699).
  teksGambar: D.teks_gambar || '',
  hover: null,       // { i, v } vertex atau bentuk di bawah kursor
  sisi: null,        // { i, e, titik } sisi terdekat, untuk add_point_to_edge
  kotor: false,
  altDitekan: false, // Alt ditahan -> snapping ke titik awal dimatikan
  salinanSeret: null, // bayangan salinan saat seret klik kanan
  seretKanan: null,
  cerah: 1, kontras: 1,   // faktor tampilan, 1 = normal
  // Setelan menu View. Namanya mengikuti menu View AnyLabeling.
  v: { teks: false, grup: false, isi: true, silang: true,
       namaKelas: true, tanyaKelas: true, labelTerakhir: true, zoomTetap: false,
       autosave: true, keepPrev: false },
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
/* Konstanta ini nilainya diambil dari canvas.py AnyLabeling, bukan dikarang:
     epsilon = 10.0   ambang sentuh vertex/sisi, dibagi zoom supaya makin
                      presisi saat gambar diperbesar
     MOVE_SPEED = 5.0 langkah geser dengan tombol panah                     */
const EPSILON = 10.0;
const MOVE_SPEED = 5.0;

// ---------------------------------------------------------------- transform

const keLayarX = x => x * S.zoom + S.panx;
const keLayarY = y => y * S.zoom + S.pany;
const keGambarX = x => (x - S.panx) / S.zoom;
/* Setara out_off_pixmap + bounded_move_* di canvas.py: titik tidak boleh keluar
   gambar. Tanpa ini objek bisa diseret ke luar dan anotasinya jadi tidak sah —
   koordinat negatif atau melebihi lebar gambar. */
const kurungX = x => Math.min(Math.max(x, 0), D.W);
const kurungY = y => Math.min(Math.max(y, 0), D.H);
const keGambarY = y => (y - S.pany) / S.zoom;

function ukur() { c.width = wrap.clientWidth; c.height = wrap.clientHeight; }

function muatKeLayar() {
  S.zoomManual = false;
  S.zoom = Math.min(c.width / D.W, c.height / D.H) * 0.96;
  S.panx = (c.width - D.W * S.zoom) / 2;
  S.pany = (c.height - D.H * S.zoom) / 2;
  gambar();
}

/* Fit Width (label_widget.py:623-630, Ctrl+Shift+F). Untuk gambar tinggi-sempit,
   muat-jendela membuang seluruh lebar layar. */
function muatKeLebar() {
  S.zoomManual = false;
  S.zoom = (c.width / D.W) * 0.96;
  S.panx = (c.width - D.W * S.zoom) / 2;
  S.pany = 0;
  gambar();
}

function zoomDi(faktor, cx, cy) {
  S.zoomManual = true;             // sejak ini, ubah ukuran jendela tidak memaskan ulang
  const gx = keGambarX(cx), gy = keGambarY(cy);
  S.zoom = Math.min(Math.max(S.zoom * faktor, 0.05), 40);
  S.panx = cx - gx * S.zoom;
  S.pany = cy - gy * S.zoom;
  gambar();
}

// ---------------------------------------------------------------- gambar

/*
 * Gambar ulang dibatasi satu bingkai layar, meniru _update_interval = 0.016 di
 * canvas.py:96-97. Setiap gerakan tetikus dulu memicu penggambaran penuh; pada
 * gambar 4080 piksel dengan banyak poligon itu terasa berat. Permintaan yang
 * datang di tengah bingkai tidak dibuang, hanya digabung ke bingkai berikutnya.
 */
let bingkaiMenunggu = false;

function gambar() {
  if (bingkaiMenunggu) return;
  bingkaiMenunggu = true;
  requestAnimationFrame(() => { bingkaiMenunggu = false; gambarSekarang(); });
}

function gambarSekarang() {
  g.clearRect(0, 0, c.width, c.height);
  if (img.complete && img.naturalWidth) {
    g.imageSmoothingEnabled = S.zoom < 4;
    // Kecerahan dan kontras hanya mengenai GAMBARNYA, tidak pernah mengenai
    // bentuk — kalau bentuknya ikut meredup, warnanya tidak lagi menandakan
    // kelas. Filter selalu diterapkan pada citra asli, jadi tidak menumpuk,
    // sama seperti BrightnessContrastDialog yang selalu memakai self.img.
    const perluFilter = S.cerah !== 1 || S.kontras !== 1;
    if (perluFilter) g.filter = `brightness(${S.cerah}) contrast(${S.kontras})`;
    g.drawImage(img, S.panx, S.pany, D.W * S.zoom, D.H * S.zoom);
    if (perluFilter) g.filter = 'none';
  }

  S.shapes.forEach((s, i) => {
    if (terlihat(s)) gambarBentuk(s, S.terpilih.includes(i));
  });

  if (S.v.grup) gambarPenandaGrup();
  if (S.pratinjau) gambarPratinjau(S.pratinjau);
  if (S.salinanSeret) gambarBayanganSalinan();
  if (S.draft) gambarDraft(S.draft);
  if (S.seret && S.seret.jenis.startsWith('kotak')) gambarKotakSeret(S.seret);
  if (S.seret && S.seret.jenis === 'lingkaran') gambarLingkaranSeret(S.seret);
  if (S.kotak && S.pratinjau) gambarKotakGambar(S.kotak);
  S.prompt.forEach(p => titikPrompt(p));
  if (S.sisi) gambarSisiDisorot(S.sisi);
  if (S.kursor && S.v.silang) garisSilang(S.kursor);
}

/*
 * Penanda grup, meniru canvas.py:757-801: satu kotak PUTUS-PUTUS #EEEEEE
 * membungkus seluruh anggota grup, dan titik berwarna di pusat tiap anggota.
 * Dulu di sini hanya ada tulisan "grup N" di atas tiap bentuk, sehingga anggota
 * satu grup tidak pernah terlihat sebagai satu kesatuan.
 */
function gambarPenandaGrup() {
  const grup = new Map();
  S.shapes.forEach((s, i) => {
    if (s.group_id == null || !terlihat(s)) return;
    if (!grup.has(s.group_id)) grup.set(s.group_id, []);
    grup.get(s.group_id).push(i);
  });
  grup.forEach((anggota, gid) => {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const w = warna('grup:' + gid, 1);
    anggota.forEach(i => {
      const p = titikTampil(S.shapes[i]);
      const xs = p.map(a => a[0]), ys = p.map(a => a[1]);
      const a0 = Math.min(...xs), b0 = Math.min(...ys);
      const a1 = Math.max(...xs), b1 = Math.max(...ys);
      x0 = Math.min(x0, a0); y0 = Math.min(y0, b0);
      x1 = Math.max(x1, a1); y1 = Math.max(y1, b1);
      bulatan((a0 + a1) / 2, (b0 + b1) / 2, 3.5, w, w);
    });
    if (!isFinite(x0)) return;
    g.save();
    g.setLineDash([5, 4]);
    g.strokeStyle = '#EEEEEE';
    g.lineWidth = 1;
    g.strokeRect(keLayarX(x0), keLayarY(y0),
                 (x1 - x0) * S.zoom, (y1 - y0) * S.zoom);
    g.restore();
  });
}

/*
 * Padanan Canvas.is_visible (canvas.py:204-206). SATU sumber kebenaran, dan
 * ia menggerbangi penggambaran SEKALIGUS seluruh uji sentuh.
 *
 * Dulu penyembunyian hanya mematikan penggambaran, sementara pemilihan dan
 * sorotan memeriksa hal lain — sehingga bentuk yang tidak terlihat masih bisa
 * diklik dan DISERET. Perubahan yang tidak kasatmata, langsung diautosave.
 */
function terlihat(s) { return !s.sembunyi; }

function jalur(p) {
  g.beginPath();
  g.moveTo(keLayarX(p[0][0]), keLayarY(p[0][1]));
  for (let i = 1; i < p.length; i++) g.lineTo(keLayarX(p[i][0]), keLayarY(p[i][1]));
  g.closePath();
}

function gambarBentuk(s, terpilih) {
  const p = titikTampil(s);
  const disorot = S.hover && S.hover.i === S.shapes.indexOf(s);
  const lebar = terpilih ? 2.5 : (disorot ? 2.4 : 1.6);

  // `point` tidak punya garis maupun isian — di AnyLabeling vertex-nya SELALU
  // digambar, bahkan saat bentuknya tidak terpilih (shape.py:187-189).
  if (s.shape_type === 'point') {
    const [x, y] = p[0];
    bulatan(x, y, UKURAN_TITIK / 2 + (terpilih ? 1.5 : 0),
            terpilih ? '#fff' : warna(s.label, 1), terpilih ? GARIS_PILIH : '#fff');
    gambarTeksBentuk(s, p);
    return;
  }
  if (p.length < 2) return;

  if (TERTUTUP.has(s.shape_type)) {
    jalur(p);
    g.fillStyle = warna(s.label, terpilih ? 0.38 : 0.2);
    g.fill();
  } else {
    // line dan linestrip TIDAK ditutup dan tidak diisi: jalurnya memang
    // terbuka (shape.py:176-185).
    g.beginPath();
    g.moveTo(keLayarX(p[0][0]), keLayarY(p[0][1]));
    for (let i = 1; i < p.length; i++) g.lineTo(keLayarX(p[i][0]), keLayarY(p[i][1]));
  }
  g.strokeStyle = terpilih ? GARIS_PILIH : warna(s.label, 1);
  g.lineWidth = lebar;
  g.stroke();
  gambarTeksBentuk(s, p);
  if (terpilih) {
    // Titik TERSIMPAN untuk semua tipe, bukan hasil pemekaran — di AnyLabeling
    // vertexnya digambar `for i in range(len(self.points))` (shape.py:165-167).
    // Rectangle karena itu punya DUA pegangan, bukan empat. Dulu di sini
    // dipakai titik yang sudah dimekarkan, sehingga rectangle menampilkan empat
    // pegangan padahal hanya dua yang bisa diseret, dan penanda titik terpilih
    // menyorot sudut yang salah karena indeksnya beda ruang.
    s.points.forEach(([x, y], v) => bulatan(x, y, UKURAN_TITIK / 2,
      v === S.selv ? '#fff' : ISI_VERTEX, GARIS_PILIH));
  }
}

/**
 * Tulisan di atas bentuk.
 *
 * "Show Texts" di AnyLabeling menggambar shape.text — isi Text Editor — bukan
 * nama kelas (canvas.py:832-855). Dulu saklar itu di sini menampilkan nama
 * kelas, sehingga catatan per objek yang justru dijaga bolak-balik tidak pernah
 * terlihat di kanvas. Nama kelas tetap bisa ditampilkan, tetapi lewat saklarnya
 * sendiri — itu tambahan kita, bukan bawaan AnyLabeling.
 */
function gambarTeksBentuk(s, p) {
  if (!S.v.teks && !S.v.grup && !S.v.namaKelas) return;
  const bagian = [];
  if (S.v.namaKelas) bagian.push(s.label || '(tanpa kelas)');
  if (S.v.teks && s.text) bagian.push(s.text);
  if (S.v.grup && s.group_id != null) bagian.push('grup ' + s.group_id);
  if (!bagian.length) return;
  const xs = p.map(a => a[0]), ys = p.map(a => a[1]);
  const t = bagian.join(' · ');
  g.font = '600 11px ui-sans-serif, sans-serif';
  const lb = g.measureText(t).width + 8;
  const tx = keLayarX(Math.min(...xs)), ty = keLayarY(Math.min(...ys)) - 4;
  g.fillStyle = 'rgba(20,30,45,.78)';
  g.fillRect(tx, ty - 14, lb, 15);
  g.fillStyle = '#fff';
  g.fillText(t, tx + 4, ty - 3);
}

/*
 * Enam tipe bentuk AnyLabeling (shape.py:89-103) dan jumlah titik minimalnya.
 * Angka ini yang menentukan kapan sebuah bentuk selesai digambar.
 */
const JENIS_BENTUK = {
  polygon: 3, rectangle: 2, circle: 2, line: 2, linestrip: 2, point: 1,
};
// Bentuk yang punya bagian dalam: hanya ini yang diisi warna dan bisa dipilih
// dengan mengklik tengahnya. Padanan Shape.contains_point, yang untuk
// line/point/linestrip memang hampir selalu False (path-nya tanpa area).
const BERISI = new Set(['polygon', 'rectangle', 'circle']);
const TERTUTUP = new Set(['polygon', 'rectangle', 'circle']);
// Padanan Shape.can_add_point() (shape.py:116-118).
const DAPAT_SISIP = new Set(['polygon', 'linestrip']);
const SISI_LINGKARAN = 32;

/*
 * Titik yang DISIMPAN -> titik yang DIGAMBAR.
 *
 * Rectangle dan circle disimpan 2 titik mengikuti konvensi labelme; pemekaran
 * di sini hanya untuk menggambar. Memisahkan keduanya itu penting: pernah
 * terjadi rectangle 2 titik buatan AnyLabeling tersimpan balik jadi 4 titik,
 * dan berkasnya tidak bisa dibuka lagi di desktop.
 */
function titikTampil(s) {
  const p = s.points;
  if (s.shape_type === 'rectangle' && p.length === 2) {
    return [[p[0][0], p[0][1]], [p[1][0], p[0][1]], [p[1][0], p[1][1]], [p[0][0], p[1][1]]];
  }
  if (s.shape_type === 'circle' && p.length === 2) {
    const r = Math.hypot(p[1][0] - p[0][0], p[1][1] - p[0][1]);
    return Array.from({ length: SISI_LINGKARAN }, (_, i) => {
      const a = i / SISI_LINGKARAN * Math.PI * 2;
      return [p[0][0] + r * Math.cos(a), p[0][1] + r * Math.sin(a)];
    });
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
  const jenis = d.jenis || 'polygon';
  g.beginPath();
  g.moveTo(keLayarX(p[0][0]), keLayarY(p[0][1]));
  for (let i = 1; i < p.length; i++) g.lineTo(keLayarX(p[i][0]), keLayarY(p[i][1]));
  if (d.hover) g.lineTo(keLayarX(d.hover[0]), keLayarY(d.hover[1]));
  // Hanya poligon yang diisi saat digambar (fill_drawing di AnyLabeling hanya
  // berlaku untuk create_mode "polygon"); garis dan polyline memang terbuka.
  if (S.v.isi && jenis === 'polygon' && p.length > 2) {
    g.fillStyle = warna(S.label, 0.2);
    g.fill();
  }
  g.strokeStyle = warna(S.label, 1);
  g.lineWidth = 1.8;
  g.setLineDash([5, 4]);
  g.stroke();
  g.setLineDash([]);
  p.forEach(([x, y], i) => {
    // Titik pertama disorot lebih besar saat poligon sudah bisa ditutup —
    // padanan highlight_vertex(0, NEAR_VERTEX) saat snapping aktif, yang di
    // AnyLabeling jadi satu-satunya penanda "klik di sini untuk menutup".
    const siapTutup = jenis === 'polygon' && i === 0 && p.length > 2 && !S.altDitekan;
    bulatan(x, y, siapTutup ? 7 : 3.5, siapTutup ? ISI_VERTEX : '#fff',
            siapTutup ? '#fff' : warna(S.label, 1));
  });
}

function gambarKotakSeret(k) {
  g.strokeStyle = '#fff'; g.lineWidth = 1.4; g.setLineDash([6, 4]);
  g.strokeRect(Math.min(k.x0, k.x1), Math.min(k.y0, k.y1),
               Math.abs(k.x1 - k.x0), Math.abs(k.y1 - k.y0));
  g.setLineDash([]);
}

/* Bayangan salinan saat seret klik kanan: putus-putus supaya jelas ini belum
   jadi objek, dan belum tentu jadi — bergantung pilihan di menu. */
function gambarBayanganSalinan() {
  g.save();
  g.setLineDash([6, 4]);
  g.strokeStyle = '#fff';
  g.lineWidth = 1.8;
  S.salinanSeret.forEach(a => {
    const s = S.shapes[a.i];
    const p = titikTampil({ ...s, points: a.pts });
    if (s.shape_type === 'point') {
      bulatan(p[0][0], p[0][1], UKURAN_TITIK / 2, 'rgba(255,255,255,.6)', '#fff');
      return;
    }
    g.beginPath();
    g.moveTo(keLayarX(p[0][0]), keLayarY(p[0][1]));
    for (let i = 1; i < p.length; i++) g.lineTo(keLayarX(p[i][0]), keLayarY(p[i][1]));
    if (TERTUTUP.has(s.shape_type)) g.closePath();
    g.stroke();
  });
  g.restore();
  g.setLineDash([]);
}

function gambarLingkaranSeret(k) {
  const r = Math.hypot(k.x1 - k.x0, k.y1 - k.y0);
  g.strokeStyle = '#fff'; g.lineWidth = 1.4; g.setLineDash([6, 4]);
  g.beginPath();
  g.arc(k.x0, k.y0, r, 0, 6.2832);
  g.stroke();
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

/* Sisi yang disorot ditandai, dan calon titik baru ditampilkan sebagai kotak
   kecil — di AnyLabeling titik yang sedang digeser digambar P_SQUARE. */
function gambarSisiDisorot(sisi) {
  const p = S.shapes[sisi.i].points;
  const a = p[sisi.e], b = p[(sisi.e + 1) % p.length];
  g.strokeStyle = '#fff';
  g.lineWidth = 3;
  g.beginPath();
  g.moveTo(keLayarX(a[0]), keLayarY(a[1]));
  g.lineTo(keLayarX(b[0]), keLayarY(b[1]));
  g.stroke();
  const x = keLayarX(sisi.titik[0]), y = keLayarY(sisi.titik[1]);
  g.fillStyle = ISI_VERTEX;
  g.strokeStyle = '#fff';
  g.lineWidth = 1.5;
  g.fillRect(x - 4, y - 4, 8, 8);
  g.strokeRect(x - 4, y - 4, 8, 8);
}

function garisSilang(k) {
  const x = keLayarX(k[0]), y = keLayarY(k[1]);
  g.strokeStyle = 'rgba(56,209,106,.55)';
  g.lineWidth = 1;
  g.setLineDash([5, 4]);
  g.beginPath();
  // Membentang sepanjang gambar, bukan sepanjang kanvas (canvas.py:866-873).
  const x0 = keLayarX(0), x1 = keLayarX(D.W);
  const y0 = keLayarY(0), y1 = keLayarY(D.H);
  g.moveTo(x0, y); g.lineTo(x1, y);
  g.moveTo(x, y0); g.lineTo(x, y1);
  g.stroke();
  g.setLineDash([]);
}

// ---------------------------------------------------------------- hit test

/* Padanan Shape.nearest_vertex (shape.py:234-245): JARAK sesungguhnya, dan
   yang dikembalikan adalah titik TERDEKAT. Dulu daerah tangkapnya kotak
   (di sudutnya 1,41x lebih longgar) dan yang menang titik ber-indeks terkecil,
   bukan yang paling dekat — terasa saat dua titik berdempetan. */
function vertexTerdekatPada(s, gx, gy, r) {
  let terbaik = null, jarakMin = Infinity;
  for (let v = 0; v < s.points.length; v++) {
    const d = Math.hypot(s.points[v][0] - gx, s.points[v][1] - gy);
    if (d <= r && d < jarakMin) { jarakMin = d; terbaik = v; }
  }
  return terbaik;
}

function sisiTerdekatPada(s, gx, gy, r) {
  if (!DAPAT_SISIP.has(s.shape_type)) return null;
  const p = s.points;
  const nSisi = TERTUTUP.has(s.shape_type) ? p.length : p.length - 1;
  for (let j = 0; j < nSisi; j++) {
    const a = p[j], b = p[(j + 1) % p.length];
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const pj = dx * dx + dy * dy;
    if (pj === 0) continue;
    let t = ((gx - a[0]) * dx + (gy - a[1]) * dy) / pj;
    t = Math.min(Math.max(t, 0), 1);
    const cx = a[0] + t * dx, cy = a[1] + t * dy;
    if (Math.hypot(gx - cx, gy - cy) < r) return { e: j, titik: [cx, cy] };
  }
  return null;
}

/*
 * Satu penelusuran untuk semua sorotan — meniru urutan canvas.py:351-395:
 * bentuk ditelusuri DARI ATAS, dan untuk TIAP bentuk diuji titik -> sisi ->
 * isi, lalu berhenti. Dulu ketiganya ditelusuri sebagai tiga sapuan terpisah,
 * sehingga titik milik bentuk di bawah mengalahkan isi bentuk yang ada di
 * atasnya.
 */
function sorotDi(gx, gy) {
  const r = EPSILON / S.zoom;
  for (let i = S.shapes.length - 1; i >= 0; i--) {
    const s = S.shapes[i];
    if (!terlihat(s)) continue;
    const v = vertexTerdekatPada(s, gx, gy, r);
    if (v !== null) return { hover: { i, v }, sisi: null, jenis: 'titik', s };
    const e = sisiTerdekatPada(s, gx, gy, r);
    if (e) return { hover: null, sisi: { i, e: e.e, titik: e.titik }, jenis: 'sisi', s };
    if (didalam(s, gx, gy)) return { hover: { i, v: -1 }, sisi: null, jenis: 'isi', s };
  }
  return { hover: null, sisi: null, jenis: null, s: null };
}

function dekatVertex(gx, gy) {
  const r = EPSILON / S.zoom;
  for (let i = S.shapes.length - 1; i >= 0; i--) {
    if (!terlihat(S.shapes[i])) continue;
    const v = vertexTerdekatPada(S.shapes[i], gx, gy, r);
    if (v !== null) return { i, v };
  }
  return null;
}

/* Padanan Shape.nearest_edge: sisi terdekat dalam jarak epsilon/zoom.
   Dipakai add_point_to_edge untuk menyisipkan titik di tengah sisi. */
function dekatSisi(gx, gy) {
  const r = EPSILON / S.zoom;
  for (let i = S.shapes.length - 1; i >= 0; i--) {
    const s = S.shapes[i];
    // Padanan Shape.can_add_point(): HANYA polygon dan linestrip yang boleh
    // disisipi titik. Rectangle dan circle bentuknya ditentukan 2 titik, dan
    // point tidak punya sisi.
    if (!terlihat(s) || !DAPAT_SISIP.has(s.shape_type)) continue;
    const p = s.points;
    // Sisi penutup (titik terakhir -> pertama) hanya ada pada bentuk tertutup;
    // linestrip terbuka, jadi sisinya satu lebih sedikit.
    const nSisi = TERTUTUP.has(s.shape_type) ? p.length : p.length - 1;
    for (let j = 0; j < nSisi; j++) {
      const a = p[j], b = p[(j + 1) % p.length];
      const dx = b[0] - a[0], dy = b[1] - a[1];
      const pj = dx * dx + dy * dy;
      if (pj === 0) continue;
      let t = ((gx - a[0]) * dx + (gy - a[1]) * dy) / pj;
      t = Math.min(Math.max(t, 0), 1);
      const cx = a[0] + t * dx, cy = a[1] + t * dy;
      if (Math.hypot(gx - cx, gy - cy) < r) {
        return { i, e: j, titik: [cx, cy] };
      }
    }
  }
  return null;
}

/* Padanan add_point_to_edge: sisipkan titik pada sisi yang sedang disorot,
   lalu jadikan vertex itu yang aktif — di AnyLabeling titik baru langsung
   bisa digeser (moving_shape = True). */
function tambahTitikDiSisi() {
  if (!S.sisi) { toast('Dekatkan kursor ke sisi poligon dulu (mode Sunting)'); return; }
  const { i, e, titik } = S.sisi;
  simpanUndo();
  S.shapes[i].points.splice(e + 1, 0, [kurungX(titik[0]), kurungY(titik[1])]);
  S.sel = i;
  S.selv = e + 1;
  S.sisi = null;
  tandaiKotor();
  render();
}

/* Padanan move_by_keyboard: panah menggeser objek terpilih sejauh MOVE_SPEED,
   tetap terkurung di dalam gambar. */
/*
 * move_by_keyboard (canvas.py:1081-1086) menggeser SELURUH bentuk terpilih,
 * bukan hanya satu. Riwayat urungkan dicatat sekali saat tombolnya DILEPAS
 * (keyReleaseEvent, canvas.py:1119-1126) — dulu setiap keydown mencatat, jadi
 * menahan panah satu detik mendorong puluhan entri dan menghapus seluruh
 * riwayat sebelumnya.
 */
let geserBerjalan = false;

function geserDenganPanah(dx, dy) {
  const idx = adaTerpilih() ? S.terpilih : (S.sel >= 0 ? [S.sel] : []);
  if (!idx.length) { toast('Pilih objeknya dulu'); return; }
  const semua = idx.flatMap(i => S.shapes[i].points);
  const xs = semua.map(a => a[0]), ys = semua.map(a => a[1]);
  dx = Math.min(Math.max(dx, -Math.min(...xs)), D.W - Math.max(...xs));
  dy = Math.min(Math.max(dy, -Math.min(...ys)), D.H - Math.max(...ys));
  if (!dx && !dy) return;
  if (!geserBerjalan) { simpanUndo(); geserBerjalan = true; }
  idx.forEach(i => {
    S.shapes[i].points = S.shapes[i].points.map(a => [a[0] + dx, a[1] + dy]);
  });
  tandaiKotor();
  render();
}

function didalam(s, gx, gy) {
  /*
   * Hanya bentuk yang punya bagian dalam yang bisa dipilih dengan mengklik
   * tengahnya. Untuk line, linestrip, dan point, Shape.contains_point di
   * AnyLabeling memakai path tanpa area sehingga hampir selalu False —
   * bentuk-bentuk itu memang dipilih lewat titiknya (nearest_vertex).
   */
  if (!BERISI.has(s.shape_type)) return false;
  if (s.shape_type === 'circle' && s.points.length === 2) {
    const [[cx, cy], [ex, ey]] = s.points;
    return Math.hypot(gx - cx, gy - cy) <= Math.hypot(ex - cx, ey - cy);
  }
  const p = titikTampil(s);
  let ada = false;
  for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
    if (((p[i][1] > gy) !== (p[j][1] > gy)) &&
        (gx < (p[j][0] - p[i][0]) * (gy - p[i][1]) / (p[j][1] - p[i][1]) + p[i][0])) ada = !ada;
  }
  return ada;
}

/* Padanan select_shape_point(multiple_selection_mode) di canvas.py:
   Ctrl+klik menambahkan bentuk ke pilihan alih-alih menggantinya. */
function pilihBentuk(i, tambah) {
  if (i < 0) {
    if (!tambah) { S.sel = -1; S.terpilih = []; }
    return;
  }
  if (tambah) {
    const ada = S.terpilih.indexOf(i);
    if (ada >= 0) {
      S.terpilih.splice(ada, 1);
      S.sel = S.terpilih.length ? S.terpilih[S.terpilih.length - 1] : -1;
    } else {
      S.terpilih.push(i);
      S.sel = i;
    }
  } else {
    S.terpilih = [i];
    S.sel = i;
  }
}

/* Padanan canvas.py:503-512 — melepas satu bentuk dari seleksi tanpa
   mengosongkan sisanya. */
function batalPilih(i) {
  const k = S.terpilih.indexOf(i);
  if (k >= 0) S.terpilih.splice(k, 1);
  S.sel = S.terpilih.length ? S.terpilih[S.terpilih.length - 1] : -1;
  S.selv = -1;
}

const adaTerpilih = () => S.terpilih.length;

function bentukDi(gx, gy) {
  for (let i = S.shapes.length - 1; i >= 0; i--) {
    if (terlihat(S.shapes[i]) && didalam(S.shapes[i], gx, gy)) return i;
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
  jadwalkanAutosave();
}

/*
 * Autosave — begitu setiap objek sudah punya kelas, perubahan langsung ditulis
 * ke disk, seperti Roboflow. Tiga sifat yang dijaga:
 *
 *   1. Pratinjau SAM yang belum disahkan dengan F TIDAK pernah ikut tersimpan;
 *      ia hidup di S.pratinjau, bukan di S.shapes. Sama seperti AnyLabeling
 *      yang mengecualikan AUTOLABEL_* dari penyimpanan.
 *   2. Semuanya tetap bisa disunting sesudahnya. Salah klik atau salah nama
 *      kelas tinggal diperbaiki — tiap perbaikan menulis ulang, dan riwayat
 *      urungkan (40 langkah) tetap utuh.
 *   3. Objek tanpa kelas tidak memicu penyimpanan sama sekali, karena server
 *      memang menolaknya. Lebih baik diam daripada memunculkan galat berulang.
 *
 * Ditunda sesaat supaya menyeret titik tidak memicu puluhan permintaan.
 */
const JEDA_AUTOSAVE = 600;
let waktuAutosave = null;
const adaAnotasiAwal = (D.shapes || []).length > 0;

function jadwalkanAutosave() {
  clearTimeout(waktuAutosave);
  if (!S.v.autosave) return;
  if (S.shapes.some(s => !s.label)) return;
  // Menghapus objek terakhir pada gambar yang memang sudah beranotasi harus
  // ikut tersimpan. Tetapi gambar yang belum pernah dianotasi jangan diam-diam
  // ditandai "latar" hanya karena objeknya dibuat lalu dibatalkan.
  if (!S.shapes.length && !adaAnotasiAwal) return;
  waktuAutosave = setTimeout(() => { if (S.kotor) simpan(true); }, JEDA_AUTOSAVE);
}

// ---------------------------------------------------------------- mode

const NAMA_MODE = { 'p+': '+Point', 'p-': '−Point', rect: '+Rect',
                    kotak: 'Rectangle manual', poly: 'Poligon manual',
                    circle: 'Circle', line: 'Line', linestrip: 'LineStrip',
                    point: 'Point', edit: 'Sunting' };
// Mode menggambar -> tipe bentuk yang dihasilkannya.
const JENIS_DARI_MODE = { poly: 'polygon', kotak: 'rectangle', circle: 'circle',
                          line: 'line', linestrip: 'linestrip', point: 'point' };
const NAMA_JENIS = { polygon: 'Poligon', rectangle: 'Rectangle', circle: 'Circle',
                     line: 'Line', linestrip: 'LineStrip', point: 'Point' };

/* set_editing (canvas.py:234-239): berpindah ke mode menggambar melepas
   sorotan DAN pilihan. Tanpa itu, bentuk yang masih terpilih ikut bereaksi
   terhadap panah dan Delete padahal orang sudah beralih menggambar. */
function setMode(m) {
  S.mode = m;
  S.draft = null;
  if (m !== 'edit') {
    S.hover = null; S.sisi = null; S.selv = -1;
    S.sel = -1; S.terpilih = [];
    petunjuk(null);
  }
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
async function finishObject() {
  if (!S.pratinjau) { toast('Belum ada pratinjau. Klik objeknya dulu.'); return; }
  let titik = S.pratinjau.points;
  let jenis = 'polygon';
  if (el('output').value === 'rectangle') {
    const xs = titik.map(p => p[0]), ys = titik.map(p => p[1]);
    titik = [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]];
    jenis = 'rectangle';
  }
  const b = await sahkanBentuk(jenis, titik);
  if (!b) return;                       // dialog dibatalkan -> pratinjau tetap
  S.prompt = [];
  S.kotak = null;
  S.pratinjau = null;
  el('ab-finish').removeAttribute('data-siap');
  pesan(`Objek disahkan sebagai "${b.label}"`);
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
  if (ev.button === 2) {
    // Belum diputuskan apa-apa: menu atau duplikat-dan-pindah baru ditentukan
    // saat tombol dilepas, tergantung sempat diseret atau tidak.
    tutupMenu();
    S.seretKanan = { x0: gx, y0: gy, bergerak: false, ev };
    return;
  }

  if (S.mode === 'edit') {
    /*
     * Urutan di bawah menirukan canvas.py:463-478 AnyLabeling, dan urutan
     * itulah yang membuat menyunting terasa ringan di sana: sisi dan titik
     * ditangani lebih dulu, seleksi belakangan. Tanpa ini, merapikan hasil SAM
     * menuntut pintasan atau menu untuk pekerjaan yang dilakukan ratusan kali.
     */

    // 1. Klik biasa di sisi poligon LANGSUNG menyisipkan titik. Sorotan sisi
    //    sudah dihitung saat hover (S.sisi), dan hover menjamin S.sisi kosong
    //    kalau ada vertex lebih dekat — sama seperti h_edge vs h_vertex.
    if (S.sisi) { tambahTitikDiSisi(); return; }

    const v = dekatVertex(gx, gy);

    // 2. Shift+klik pada titik membuang titik itu. AnyLabeling membandingkan
    //    modifier secara eksak, jadi Ctrl+Shift sengaja tidak ikut memicu.
    if (v && ev.shiftKey && !ev.ctrlKey && !ev.altKey) {
      hapusTitikDi(v.i, v.v);
      return;
    }

    if (v) {
      simpanUndo();
      // select_shape_point (canvas.py:579-581): kalau ada vertex tersorot,
      // yang terjadi HANYA penyorotan titiknya — selected_shapes tidak
      // disentuh. Dulu di sini seleksi ditimpa, sehingga menggeser satu titik
      // membubarkan seleksi jamak yang baru saja disusun dengan Ctrl+klik.
      if (!S.terpilih.includes(v.i)) pilihBentuk(v.i, false);
      else S.sel = v.i;
      S.selv = v.v;
      S.seret = { jenis: 'vertex', i: v.i, v: v.v };
      render();
      return;
    }

    const i = bentukDi(gx, gy);
    const sudahTerpilih = i >= 0 && S.terpilih.includes(i);
    S.selv = -1;
    pilihBentuk(i, ev.ctrlKey);
    if (i >= 0 && !ev.ctrlKey) {
      simpanUndo();
      // Seret memindahkan SELURUH bentuk terpilih, seperti bounded_move_shapes.
      // 3. `klikUlang` menandai klik pada objek yang memang sudah terpilih.
      //    Kalau ternyata tidak jadi digeser, pilihannya dibatalkan saat tombol
      //    dilepas — AnyLabeling menguji `not moving_shape` untuk hal yang sama,
      //    supaya menyeret objek terpilih tidak ikut melepas pilihannya.
      S.seret = { jenis: 'bentuk', i, x0: gx, y0: gy, klikUlang: sudahTerpilih,
                  awal: S.terpilih.map(k => ({
                    i: k, pts: S.shapes[k].points.map(p => [p[0], p[1]]) })) };
    }
    render();
    return;
  }

  // `point`: satu klik = satu objek (canvas.py:454-455).
  if (S.mode === 'point') {
    sahkanBentuk('point', [[kurungX(gx), kurungY(gy)]]);
    return;
  }

  /*
   * Menggambar bertitik-banyak: poligon, garis, dan polyline. Bedanya hanya
   * pada cara mengakhiri, persis seperti canvas.py:436-449:
   *
   *   polygon   klik sampai kembali ke titik awal (snapping), atau Enter
   *   line      tepat 2 klik
   *   linestrip klik terus, diakhiri Ctrl+klik atau Enter
   */
  if (S.mode === 'poly' || S.mode === 'line' || S.mode === 'linestrip') {
    if (!S.draft) S.draft = { points: [], jenis: JENIS_DARI_MODE[S.mode] };
    const p0 = S.draft.points[0];
    // Snapping ke titik awal hanya untuk poligon, dan bisa dimatikan dengan
    // menahan Alt (canvas.py:292-302 + 1100-1101).
    if (S.mode === 'poly' && p0 && S.draft.points.length > 2 && !S.altDitekan &&
        Math.hypot(gx - p0[0], gy - p0[1]) < EPSILON / S.zoom) {
      tutupDraft();
      return;
    }
    S.draft.points.push([kurungX(gx), kurungY(gy)]);
    if (S.mode === 'line' && S.draft.points.length === 2) { tutupDraft(); return; }
    if (S.mode === 'linestrip' && ev.ctrlKey && S.draft.points.length >= 2) {
      tutupDraft();
      return;
    }
    gambar();
    return;
  }

  if (S.mode === 'rect' || S.mode === 'kotak' || S.mode === 'circle') {
    S.seret = { jenis: S.mode === 'kotak' ? 'kotakmanual'
                       : S.mode === 'circle' ? 'lingkaran' : 'kotak',
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
  // Seret klik kanan: yang bergerak adalah bayangan salinan, aslinya diam.
  if (S.seretKanan) {
    const dx = gx - S.seretKanan.x0, dy = gy - S.seretKanan.y0;
    if (!S.seretKanan.bergerak && Math.hypot(dx, dy) * S.zoom > 3) {
      if (!adaTerpilih()) {
        const i = bentukDi(S.seretKanan.x0, S.seretKanan.y0);
        if (i >= 0) pilihBentuk(i, false);
      }
      if (adaTerpilih()) { S.seretKanan.bergerak = true; mulaiSalinanSeret(); }
    }
    if (S.salinanSeret) {
      const semua = S.salinanSeret.flatMap(a => S.shapes[a.i].points);
      const xs = semua.map(p => p[0]), ys = semua.map(p => p[1]);
      const kx = Math.min(Math.max(dx, -Math.min(...xs)), D.W - Math.max(...xs));
      const ky = Math.min(Math.max(dy, -Math.min(...ys)), D.H - Math.max(...ys));
      S.salinanSeret.forEach(a => {
        a.pts = S.shapes[a.i].points.map(p => [p[0] + kx, p[1] + ky]);
      });
      gambar();
    }
    return;
  }
  if (S.seret) {
    /*
     * `bergerak` membedakan seret sungguhan dari klik yang kebetulan bergetar
     * satu piksel. Tiga hal bergantung padanya: klik-ulang hanya membatalkan
     * pilihan kalau objeknya tidak jadi digeser, riwayat urungkan tidak diisi
     * langkah kosong, dan autosave tidak menulis ulang berkas tanpa perubahan.
     */
    if (S.seret.jenis === 'vertex') {
      const p = S.shapes[S.seret.i].points[S.seret.v];
      const nx = kurungX(gx), ny = kurungY(gy);
      if (nx !== p[0] || ny !== p[1]) {
        S.shapes[S.seret.i].points[S.seret.v] = [nx, ny];
        S.seret.bergerak = true;
        tandaiKotor();
      }
    } else if (S.seret.jenis === 'bentuk') {
      // Geser seluruh bentuk terpilih, ditahan di tepi supaya tidak ada titik
      // yang keluar gambar — sama seperti bounded_move_shapes.
      let dx = gx - S.seret.x0, dy = gy - S.seret.y0;
      const semua = S.seret.awal.flatMap(a => a.pts);
      const xs = semua.map(p => p[0]), ys = semua.map(p => p[1]);
      dx = Math.min(Math.max(dx, -Math.min(...xs)), D.W - Math.max(...xs));
      dy = Math.min(Math.max(dy, -Math.min(...ys)), D.H - Math.max(...ys));
      // Selalu diterapkan, termasuk saat dx=dy=0: itu yang mengembalikan bentuk
      // ke posisi semula kalau kursor ditarik keluar lalu balik lagi.
      S.seret.awal.forEach(a => {
        S.shapes[a.i].points = a.pts.map(p => [p[0] + dx, p[1] + dy]);
      });
      if (dx || dy) { S.seret.bergerak = true; tandaiKotor(); }
    } else {
      S.seret.x1 = ev.offsetX;
      S.seret.y1 = ev.offsetY;
    }
    gambar();
    return;
  }
  if (S.draft) S.draft.hover = [gx, gy];
  if (S.mode === 'edit') {
    const h = sorotDi(gx, gy);
    S.hover = h.hover;
    S.sisi = h.sisi;
    petunjuk(h);
  } else {
    S.hover = null;
    S.sisi = null;
    petunjuk(null);
  }
  gambar();
});

/*
 * Kursor dan teks petunjuk menurut apa yang ada di bawahnya — canvas.py memasang
 * lima keadaan kursor beserta tooltipnya (canvas.py:364-393). Tanpa ini, tiga
 * jalan pintas terbaik di kanvas kita — sisip titik di sisi, Shift+klik hapus
 * titik, seret bentuk — sudah ada di kode tetapi tidak punya satu pun isyarat,
 * jadi tidak akan pernah ditemukan sendiri oleh pemakai.
 */
function petunjuk(h) {
  const n = el('petunjuk');
  if (!h || !h.jenis) { c.style.cursor = ''; n.textContent = ''; return; }
  if (h.jenis === 'titik') {
    c.style.cursor = 'pointer';
    n.textContent = 'Seret memindahkan titik · Shift+klik menghapusnya';
  } else if (h.jenis === 'sisi') {
    c.style.cursor = 'copy';
    n.textContent = 'Klik menyisipkan titik di sisi ini';
  } else {
    c.style.cursor = 'grab';
    n.textContent = `Seret memindahkan objek "${h.s.label || 'tanpa kelas'}"`;
  }
}

// leaveEvent (canvas.py:195-198): sorotan dibersihkan saat kursor meninggalkan
// kanvas. Dulu S.hover dan S.sisi dibiarkan, sehingga sorotan tepi tertinggal di
// layar dan butir menu "Sisip titik" tetap hidup di lokasi yang sudah basi.
c.addEventListener('mouseleave', () => {
  S.kursor = null;
  S.hover = null;
  S.sisi = null;
  petunjuk(null);
  gambar();
});

window.addEventListener('mouseup', ev => {
  if (S.seretKanan) {
    const k = S.seretKanan;
    S.seretKanan = null;
    if (k.bergerak && S.salinanSeret) bukaMenuSalinan(ev.clientX, ev.clientY);
    else klikKanan(k.ev);
    return;
  }
  if (geser) { geser = null; wrap.removeAttribute('data-geser'); return; }
  if (!S.seret) return;
  const s = S.seret;
  S.seret = null;
  if (s.jenis === 'lingkaran') {
    // Circle labelme = 2 titik: PUSAT lalu satu titik di tepi. Menyeret dari
    // titik tekan ke titik lepas persis memberi keduanya.
    const cx = keGambarX(s.x0), cy = keGambarY(s.y0);
    const ex = keGambarX(s.x1), ey = keGambarY(s.y1);
    if (Math.hypot(ex - cx, ey - cy) * S.zoom <= 4) {
      toast('Lingkarannya terlalu kecil');
    } else {
      sahkanBentuk('circle',
        [[kurungX(cx), kurungY(cy)], [kurungX(ex), kurungY(ey)]]);
    }
    gambar();
  } else if (s.jenis.startsWith('kotak')) {
    // Dikurung ke dalam gambar, sama seperti titik, poligon, dan lingkaran.
    // AnyLabeling mencapainya dengan memproyeksikan posisi seret ke tepi pixmap
    // (canvas.py:288-291). Tanpa ini `.json` menyimpan koordinat di luar gambar
    // sementara penulisan YOLO mengurungnya — satu gambar menghasilkan dua
    // bentuk yang berbeda di dua berkas.
    const x0 = kurungX(keGambarX(Math.min(s.x0, s.x1))), y0 = kurungY(keGambarY(Math.min(s.y0, s.y1)));
    const x1 = kurungX(keGambarX(Math.max(s.x0, s.x1))), y1 = kurungY(keGambarY(Math.max(s.y0, s.y1)));
    if (Math.abs(x1 - x0) <= 3 || Math.abs(y1 - y0) <= 3) {
      toast('Kotaknya terlalu kecil');
    } else if (s.jenis === 'kotakmanual') {
      // Rectangle manual (R): langsung jadi objek, tanpa SAM — persis
      // create_rectangle di AnyLabeling.
      sahkanBentuk('rectangle', [[x0, y0], [x1, y1]]);
    } else {
      S.prompt = [];
      S.kotak = [x0, y0, x1, y1];
      jalankanSam();
    }
    gambar();
  } else if (s.jenis === 'vertex' || s.jenis === 'bentuk') {
    // Klik yang tidak jadi menggeser apa pun bukan sebuah perubahan: buang
    // lagi cadangan urungkan yang terlanjur didorong saat tombol ditekan,
    // supaya Ctrl+Z tidak menghabiskan langkah untuk hal yang tidak terjadi.
    if (!s.bergerak) S.undo.pop();
    if (s.jenis === 'bentuk' && s.klikUlang && !s.bergerak) batalPilih(s.i);
    render();
  } else {
    render();
  }
});

/*
 * Klik ganda menutup bentuk (canvas.py:557-569). Dua hal yang dulu terlewat:
 * (1) klik kedua sudah menambah satu titik lewat mousedown, jadi titik itu
 *     harus DICABUT lebih dulu — di sana `self.current.pop_point()` tepat
 *     sebelum finalise(). Tanpa itu poligon selalu berakhir dengan sepasang
 *     titik nyaris bertumpuk.
 * (2) syaratnya can_close_shape(), yang tidak memeriksa create_mode — jadi
 *     linestrip pun bisa diakhiri dengan klik ganda, bukan hanya poligon.
 */
c.addEventListener('dblclick', () => {
  if (!S.draft) return;
  const jenis = S.draft.jenis || 'polygon';
  if (!DAPAT_SISIP.has(jenis)) return;          // polygon & linestrip saja
  // AnyLabeling mencabut satu titik tanpa syarat, karena Qt menjamin klik
  // ganda menghasilkan TEPAT satu mousePressEvent tambahan (canvas.py:561-568).
  // DOM tidak menjamin itu: sebagian jalur mengirim dua mousedown, sebagian
  // satu. Jadi yang dicabut di sini adalah titik yang benar-benar KEMBAR —
  // hasilnya sama dengan AnyLabeling saat dua mousedown datang, dan tidak
  // memakan titik yang disengaja saat cuma satu yang datang.
  const t = S.draft.points;
  while (t.length > JENIS_BENTUK[jenis] && t.length > 1) {
    const a = t[t.length - 1], b = t[t.length - 2];
    if (Math.hypot(a[0] - b[0], a[1] - b[1]) > 1e-6) break;
    t.pop();
  }
  tutupDraft();
});

/*
 * Klik kanan mengikuti canvas.py AnyLabeling:
 *   - saat menggambar  -> mencabut titik / prompt terakhir
 *   - saat menyunting  -> memilih bentuk di bawah kursor lalu membuka menu
 *
 * Aturan aktifnya juga dari sana (label_widget.py:1650-1653):
 *   hapus & duplikat butuh >=1 terpilih, ubah kelas tepat 1.
 */
/* Dijalankan kalau menu ditutup tanpa memilih apa pun. Dipakai seret klik
   kanan: menutup menunya membatalkan bayangan salinan, seperti AnyLabeling
   yang membuang selected_shapes_copy saat menu tidak mengembalikan aksi
   (canvas.py:499-502). */
let menuBatal = null;

function tutupMenu() {
  el('ctx').removeAttribute('data-on');
  const f = menuBatal;
  menuBatal = null;
  if (f) f();
}

function bukaMenu(x, y) {
  const n = S.terpilih.length;
  const adaSalinan = !!(sessionStorage.getItem(KUNCI_TEMPEL) || '').length;
  pasangMenu(x, y, [
    ['judul', n ? `${n} objek terpilih` : 'tidak ada yang terpilih'],
    ['aksi', 'Ubah kelas', 'Ctrl+E', n === 1, ubahKelasTerpilih, false],
    ['aksi', 'Duplikat', 'Ctrl+D', n >= 1, duplikatTerpilih, false],
    ['aksi', 'Salin', 'Ctrl+C', n >= 1, salinTerpilih, false],
    ['aksi', 'Tempel', 'Ctrl+V', adaSalinan, tempel, false],
    ['aksi', n > 1 ? `Sembunyikan ${n} objek` : 'Sembunyikan', '', n >= 1,
     () => { S.terpilih.forEach(i => { S.shapes[i].sembunyi = true; }); render(); }, false],
    ['pisah'],
    ['aksi', 'Gabungkan jadi satu grup', 'G', n >= 1, grupTerpilih, false],
    ['aksi', 'Lepas dari grup', 'U',
     n >= 1 && S.terpilih.some(i => S.shapes[i].group_id != null),
     lepasGrupTerpilih, false],
    ['pisah'],
    ['aksi', 'Sisip titik di sisi', 'klik di sisi', !!S.sisi, tambahTitikDiSisi, false],
    ['aksi', 'Hapus titik terpilih', 'Shift+klik', S.sel >= 0 && S.selv >= 0,
     hapusTitikTerpilih, false],
    ['pisah'],
    ['aksi', n > 1 ? `Hapus ${n} objek` : 'Hapus objek', 'Del', n >= 1,
     hapusTerpilih, true],
    ['aksi', 'Urungkan', 'Ctrl+Z', S.undo.length > 0, urungkan, false],
  ]);
}

/*
 * Menu KEDUA, yang hanya muncul setelah seret klik kanan — padanan `menus[1]`
 * di AnyLabeling, yang isinya memang cuma dua pilihan itu.
 */
function bukaMenuSalinan(x, y) {
  const n = S.salinanSeret ? S.salinanSeret.length : 0;
  pasangMenu(x, y, [
    ['judul', n > 1 ? `${n} objek` : '1 objek'],
    ['aksi', 'Salin ke sini', '', true, () => akhiriSalinanSeret(true), false],
    ['aksi', 'Pindahkan ke sini', '', true, () => akhiriSalinanSeret(false), false],
  ], batalSalinanSeret);
}

/**
 * Bangun dan tampilkan menu konteks.
 * @param batal dijalankan kalau menu ditutup TANPA memilih apa pun. Dipakai
 *   seret klik kanan untuk membatalkan bayangan salinannya.
 */
function pasangMenu(x, y, isi, batal) {
  const m = el('ctx');
  m.dataset.batal = batal ? '1' : '';
  menuBatal = batal || null;
  m.innerHTML = '';
  for (const baris of isi) {
    if (baris[0] === 'pisah') { m.appendChild(document.createElement('hr')); continue; }
    if (baris[0] === 'judul') {
      const d = document.createElement('div');
      d.className = 'judul';
      d.textContent = baris[1];
      m.appendChild(d);
      continue;
    }
    const [, teks, kunci, aktif, fn, bahaya] = baris;
    const b = document.createElement('button');
    b.disabled = !aktif;
    if (bahaya) b.setAttribute('data-bahaya', '');
    b.innerHTML = '<span></span><i></i>';
    b.querySelector('span').textContent = teks;
    b.querySelector('i').textContent = kunci;
    b.onclick = () => { menuBatal = null; tutupMenu(); fn(); };
    m.appendChild(b);
  }
  // Jangan sampai menu terpotong tepi layar.
  m.setAttribute('data-on', '');
  const r = m.getBoundingClientRect();
  m.style.left = Math.min(x, window.innerWidth - r.width - 8) + 'px';
  m.style.top = Math.min(y, window.innerHeight - r.height - 8) + 'px';
}

/*
 * Klik kanan cuma DICEGAH di sini, keputusannya diambil saat tombol dilepas.
 * Sebabnya: di Linux, Chrome memunculkan contextmenu pada saat tombol DITEKAN,
 * sehingga menu akan terbuka sebelum sempat diketahui apakah orangnya sedang
 * menyeret. Padahal seret klik kanan itulah yang memicu duplikat-dan-pindah.
 */
c.addEventListener('contextmenu', ev => ev.preventDefault());

/** Padanan undo_last_point (canvas.py:1153-1163): cabut titik terakhir dari
    bentuk yang sedang digambar; kalau habis, draftnya sekalian dibuang. */
function cabutTitikTerakhir() {
  if (!S.draft || !S.draft.points.length) return;
  S.draft.points.pop();
  if (!S.draft.points.length) S.draft = null;
  gambar();
}

/** Klik kanan tanpa seret — perilaku lama, tidak berubah. */
function klikKanan(ev) {
  if (S.draft && S.draft.points.length) {
    cabutTitikTerakhir();
    return;
  }
  if (S.mode !== 'edit' && S.prompt.length) {
    S.prompt.pop();                     // mencabut prompt SAM terakhir
    if (S.prompt.length || S.kotak) jalankanSam();
    else bersihkanPrompt();
    return;
  }
  if (S.mode !== 'edit') { setMode('edit'); }
  // Padanan select_shape_point pada klik kanan: kalau yang diklik belum
  // terpilih, dia yang dipilih; kalau sudah, pilihan yang ada dipertahankan
  // supaya menu berlaku untuk semuanya.
  const i = bentukDi(keGambarX(ev.offsetX), keGambarY(ev.offsetY));
  if (i >= 0 && !S.terpilih.includes(i)) pilihBentuk(i, ev.ctrlKey);
  render();
  bukaMenu(ev.clientX, ev.clientY);
}

/*
 * Seret klik kanan = duplikat-dan-pindah (canvas.py:323-331).
 *
 * Selama diseret, yang bergerak adalah BAYANGAN salinan, bukan objek aslinya.
 * Saat dilepas muncul menu kedua berisi hanya dua pilihan — di AnyLabeling itu
 * `menus[1]` (label_widget.py:1019-1024). Memilih "Salin ke sini" menambahkan
 * salinannya; "Pindahkan ke sini" memindahkan yang asli. Menutup menu tanpa
 * memilih membatalkan keduanya.
 */
function mulaiSalinanSeret() {
  S.salinanSeret = S.terpilih.map(i => ({
    i, pts: S.shapes[i].points.map(p => [p[0], p[1]]) }));
}

function akhiriSalinanSeret(salin) {
  if (!S.salinanSeret) return;
  simpanUndo();
  if (salin) {
    S.terpilih = [];
    S.salinanSeret.forEach(a => {
      S.shapes.push({ ...JSON.parse(JSON.stringify(S.shapes[a.i])), points: a.pts });
      S.terpilih.push(S.shapes.length - 1);
    });
    pesan(`${S.salinanSeret.length} objek disalin ke sini`);
  } else {
    S.salinanSeret.forEach(a => { S.shapes[a.i].points = a.pts; });
    pesan(`${S.salinanSeret.length} objek dipindahkan`);
  }
  S.sel = S.terpilih.length ? S.terpilih[S.terpilih.length - 1] : -1;
  S.salinanSeret = null;
  tandaiKotor();
  render();
}

function batalSalinanSeret() {
  if (!S.salinanSeret) return;
  S.salinanSeret = null;
  render();
}

document.addEventListener('click', ev => {
  if (!el('ctx').contains(ev.target)) tutupMenu();
});
window.addEventListener('blur', tutupMenu);
wrap.addEventListener('wheel', tutupMenu, { passive: true });

/*
 * wheelEvent (canvas.py:1067-1079): TEPAT Ctrl memperbesar, selain itu
 * menggulir — horizontal dari delta.x, vertikal dari delta.y. Dulu roda apa pun
 * memperbesar dan tidak pernah ada penggeseran, sehingga refleks menggulir
 * gambar besar justru mengubah zoom.
 */
wrap.addEventListener('wheel', ev => {
  ev.preventDefault();
  if (ev.ctrlKey) {
    zoomDi(ev.deltaY < 0 ? 1.15 : 1 / 1.15, ev.offsetX, ev.offsetY);
    return;
  }
  // Shift+roda menggeser mendatar, mengikuti kebiasaan peramban; roda polos
  // menggeser tegak. Keduanya jadi padanan scroll_request.
  if (ev.shiftKey) S.panx -= ev.deltaY;
  else { S.panx -= ev.deltaX; S.pany -= ev.deltaY; }
  S.zoomManual = true;
  gambar();
}, { passive: false });

/** Sahkan bentuk bertitik-banyak yang sedang digambar (padanan finalise). */
function tutupDraft() {
  if (!S.draft) return;
  const jenis = S.draft.jenis || 'polygon';
  const minimal = JENIS_BENTUK[jenis];
  if (S.draft.points.length < minimal) {
    toast(`${NAMA_JENIS[jenis]} perlu minimal ${minimal} titik`);
    return;
  }
  // `line` disimpan tepat 2 titik; klik berlebih diabaikan, bukan disimpan.
  const titik = jenis === 'line' ? S.draft.points.slice(0, 2) : S.draft.points;
  const draft = S.draft;
  S.draft = null;
  render();
  sahkanBentuk(jenis, titik).then(b => {
    // Dibatalkan: bentuknya dibuang, persis undo_last_line di AnyLabeling.
    if (!b) { S.draft = draft; render(); }
  });
}

// ------------------------------------------------------- melipat bagian panel

/*
 * Tiap judul bagian di kolom kanan bisa diklik untuk melipat isinya.
 *
 * Bukan penggantian menu View: di sana panel disembunyikan sama sekali, di sini
 * ia tetap ada dan judulnya tetap terlihat. Gunanya untuk bagian yang sedang
 * tidak dipakai — enam bagian bertumpuk membuat kolomnya panjang sekali, dan
 * yang dicari jadi tenggelam. Pilihannya diingat per peramban.
 */
const KUNCI_LIPAT = 'labelapp_panel_lipat';

(() => {
  let lipat = {};
  try { lipat = JSON.parse(localStorage.getItem(KUNCI_LIPAT)) || {}; } catch (e) { /* abai */ }

  document.querySelectorAll('.lab-side .pan').forEach((pan, i) => {
    const h = pan.querySelector('h3');
    if (!h) return;
    // Kunci diambil dari judulnya, bukan urutannya: menambah panel baru di
    // tengah tidak boleh menggeser pilihan panel lain.
    const kunci = (h.textContent || String(i)).trim().split('\n')[0].slice(0, 24);
    if (lipat[kunci]) pan.setAttribute('data-lipat', '');
    h.tabIndex = 0;
    h.setAttribute('role', 'button');
    h.title = 'Klik untuk melipat / membuka bagian ini';
    const alih = () => {
      pan.toggleAttribute('data-lipat');
      lipat[kunci] = pan.hasAttribute('data-lipat');
      try { localStorage.setItem(KUNCI_LIPAT, JSON.stringify(lipat)); } catch (e) { /* abai */ }
    };
    h.onclick = alih;
    h.onkeydown = ev => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); alih(); }
    };
  });
})();

// ---------------------------------------------------------------- panduan

/*
 * Panduan pintasan.
 *
 * Isinya dulu ditulis sebagai dua baris teks selebar layar di bar atas. Semua
 * ada di sana, tetapi tidak ada yang benar-benar terbaca — dan justru karena
 * penuh, beberapa pintasan yang sudah lama bekerja tidak pernah ikut ditulis.
 * Tidak ada satu pun yang dibuang saat dipindah ke sini; yang berubah cuma
 * ruangnya.
 */
(() => {
  const kotak = el('panduan');
  const tombol = el('btn-panduan');
  if (!kotak || !tombol) return;
  const cari = el('panduan-kotak-cari');
  const isi = el('panduan-isi');
  const kosong = el('panduan-kosong');

  function saring() {
    const q = cari.value.trim().toLowerCase();
    let ada = 0;
    isi.querySelectorAll('section').forEach(sec => {
      let cocokDiBagian = 0;
      const judul = sec.querySelector('h4').textContent.toLowerCase();
      sec.querySelectorAll('dt').forEach(dt => {
        const dd = dt.nextElementSibling;
        const teks = (dt.textContent + ' ' + (dd ? dd.textContent : '')
                      + ' ' + judul).toLowerCase();
        const cocok = !q || teks.includes(q);
        dt.hidden = !cocok;
        if (dd) dd.hidden = !cocok;
        if (cocok) cocokDiBagian++;
      });
      sec.hidden = cocokDiBagian === 0;
      ada += cocokDiBagian;
    });
    kosong.hidden = ada > 0;
  }

  function buka() {
    kotak.hidden = false;
    cari.value = '';
    saring();
    cari.focus();
  }
  function tutup() { kotak.hidden = true; }

  tombol.onclick = buka;
  el('panduan-tutup').onclick = tutup;
  cari.oninput = saring;
  // Klik di luar kotaknya menutup — kebiasaan yang dipakai dialog mana pun.
  kotak.addEventListener('mousedown', ev => { if (ev.target === kotak) tutup(); });

  window.addEventListener('keydown', ev => {
    if (!kotak.hidden) {
      if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); tutup(); }
      return;
    }
    // `?` dan F1 membuka panduan dari mana saja, kecuali saat sedang mengetik.
    if (sedangMengetik(ev.target)) return;
    if (ev.key === '?' || ev.key === 'F1') { ev.preventDefault(); buka(); }
  }, true);
})();

// ---------------------------------------------------------- deteksi dari teks

/*
 * Sebut nama kelasnya, seluruh gambar dipindai sekaligus.
 *
 * Hasilnya LANGSUNG jadi objek, tidak lewat dialog kelas satu per satu — kelas
 * tiap objek sudah ditentukan oleh kata yang memicunya, dan menanyakannya lagi
 * untuk tiga puluh objek sekaligus bukan bantuan. Sama seperti
 * new_shapes_from_auto_labeling di AnyLabeling, yang juga memasukkan hasilnya
 * apa adanya. Semuanya masuk dalam SATU langkah urungkan, jadi satu Ctrl+Z
 * membatalkan seluruh deteksi kalau hasilnya tidak sesuai.
 */
(() => {
  const tombol = el('teks-jalan');
  if (!tombol) return;
  const kotak = el('teks-kelas');
  const pilih = el('teks-model');
  const info = el('teks-info');

  function biaya() {
    const o = pilih.selectedOptions[0];
    if (!o) return '';
    return o.dataset.terunduh === '1'
      ? ''
      : `model ini perlu diunduh dulu, ±${o.dataset.unduh} MB (sekali saja)`;
  }
  pilih.onchange = () => { info.textContent = biaya(); };
  info.textContent = biaya();

  tombol.onclick = async () => {
    const teks = kotak.value.trim();
    if (!teks) { toast('Tulis dulu nama kelas yang dicari'); kotak.focus(); return; }
    const o = pilih.selectedOptions[0];
    if (o && o.dataset.terunduh !== '1' &&
        !confirm(`Model "${o.textContent}" belum ada di server dan perlu diunduh `
               + `sekitar ${o.dataset.unduh} MB. Unduhannya sekali saja, tapi `
               + `deteksi pertama akan lama. Lanjutkan?`)) return;

    tombol.disabled = true;
    const semula = tombol.textContent;
    tombol.textContent = 'Mendeteksi…';
    info.textContent = o && o.dataset.terunduh !== '1'
      ? 'mengunduh model dulu — ini bisa beberapa menit' : 'memindai gambar…';
    try {
      const j = await send('/api/deteksi', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: D.path, teks, model: pilih.value,
          ambang: parseFloat(el('teks-ambang').value) || 0.1 }),
      });
      if (!j.ok) { info.textContent = ''; toast(j.error); return; }
      if (!j.bentuk.length) { info.textContent = ''; toast('Tidak ada yang cocok'); return; }

      simpanUndo();
      j.bentuk.forEach(b => {
        S.shapes.push({ label: b.label, shape_type: b.shape_type,
                        points: b.points, text: '', group_id: null,
                        flags: {}, titipan: {} });
        if (!S.kelas.includes(b.label)) S.kelas.push(b.label);
      });
      S.kelas.sort();
      S.sel = S.shapes.length - 1;
      S.terpilih = [S.sel];
      tandaiKotor();
      render();
      info.textContent = `${j.n} objek ditemukan — Ctrl+Z membatalkan semuanya`;
      toast(`${j.n} objek ditambahkan dari "${teks}"`);
      if (o) o.dataset.terunduh = '1';
    } catch (e) {
      info.textContent = '';
      toast('Gagal menghubungi server');
    } finally {
      tombol.disabled = false;
      tombol.textContent = semula;
    }
  };
})();

// ---------------------------------------------------------------- dialog kelas

/*
 * Padanan LabelDialog (widgets/label_dialog.py) beserta pemanggilnya new_shape
 * (label_widget.py:1909-1961).
 *
 * Alur AnyLabeling: GAMBAR DULU, dialognya yang bertanya. Panel Labels hanya
 * memberi nilai awal kotak teks, bukan pengganti dialog. Dulu aplikasi ini
 * membalik alurnya — kelas wajib dipilih sebelum menggambar, dan bentuknya
 * DITOLAK kalau belum. Akibat sampingannya lebih besar daripada rasa tidak
 * nyaman: karena dialog itulah satu-satunya tempat mengisi group_id dan flag
 * per objek, keduanya tidak punya titik masuk sama sekali.
 *
 * Membatalkan dialog membuang bentuknya, sama seperti undo_last_line di sana.
 */

const KUNCI_KELAS_TERAKHIR = 'labelapp_kelas_terakhir';

/** Padanan LabelDialog._last_label. Di AnyLabeling ia hidup di objek dialog;
    di sini pindah gambar memuat ulang halaman, jadi harus dititipkan. */
function kelasTerakhir() {
  try { return sessionStorage.getItem(KUNCI_KELAS_TERAKHIR) || ''; }
  catch (e) { return ''; }
}
function simpanKelasTerakhir(v) {
  try { sessionStorage.setItem(KUNCI_KELAS_TERAKHIR, v); } catch (e) { /* mode privat */ }
}

/** Semua kelas yang boleh dipilih: yang resmi dari data.yaml lebih dulu, lalu
    yang sudah dipakai di folder ini. Panel dan dialog memakai daftar yang sama. */
function semuaKelas() {
  return [...new Set([...(D.kelas_resmi || []), ...S.kelas])];
}

/*
 * `^[^ \t].+` — utils.qt.label_validator di AnyLabeling: tidak boleh diawali
 * spasi atau tab, dan minimal dua karakter.
 */
function namaKelasSah(v) {
  return /^[^ \t].+/.test(v);
}

let dlgSelesai = null;          // resolve() dialog yang sedang terbuka

/**
 * Tanyakan kelas, group id, dan flag untuk satu objek.
 * Mengembalikan {label, group_id, flags} atau null kalau dibatalkan.
 */
function tanyaKelas({ label = '', group_id = null, flags = {}, judul = 'Kelas untuk objek ini' } = {}) {
  const kotak = el('dlg');
  const teks = el('dlg-teks');
  const grup = el('dlg-grup');
  const galat = el('dlg-galat');
  el('dlg-judul').textContent = judul;
  galat.textContent = '';
  teks.value = label || '';
  grup.value = group_id === null || group_id === undefined ? '' : String(group_id);
  el('dlg-flagbaru').value = '';

  let flagSekarang = { ...flags };

  function gambarFlags() {
    const box = el('dlg-flags');
    box.innerHTML = '';
    Object.keys(flagSekarang).sort().forEach(k => {
      const l = document.createElement('label');
      const c = document.createElement('input');
      c.type = 'checkbox';
      c.checked = !!flagSekarang[k];
      c.onchange = () => { flagSekarang[k] = c.checked; };
      const sp = document.createElement('span');
      sp.textContent = k;
      l.append(c, sp);
      box.appendChild(l);
    });
  }

  function gambarDaftar() {
    const box = el('dlg-daftar');
    const resmi = new Set(D.kelas_resmi || []);
    const ketik = teks.value.trim().toLowerCase();
    box.innerHTML = '';
    semuaKelas()
      .filter(k => !ketik || k.toLowerCase().startsWith(ketik))
      .forEach(k => {
        const d = document.createElement('div');
        d.className = 'it';
        if (k === teks.value.trim()) d.setAttribute('data-on', '');
        const i = document.createElement('i');
        i.style.background = warna(k, 1);
        const sp = document.createElement('span');
        sp.textContent = k;
        d.append(i, sp);
        if (resmi.has(k)) {
          const r = document.createElement('span');
          r.className = 'resmi';
          r.textContent = 'data.yaml';
          d.appendChild(r);
        }
        // Satu klik mengisi kotak teks (label_selected), klik ganda langsung
        // menyimpan (label_double_clicked -> validate).
        d.onclick = () => { teks.value = k; gambarDaftar(); teks.focus(); };
        d.ondblclick = () => { teks.value = k; setuju(); };
        box.appendChild(d);
      });
  }

  // Pelengkapan sebaris, mode "startswith" bawaan AnyLabeling
  // (configs/anylabeling_config.yaml:63). Yang diketik tetap yang menentukan;
  // sisanya cuma tersorot dan hilang begitu diketik lagi.
  let hapusTerakhir = false;
  teks.oninput = () => {
    const v = teks.value;
    if (!hapusTerakhir && v) {
      const cocok = semuaKelas().find(
        k => k.toLowerCase().startsWith(v.toLowerCase()) && k.length > v.length);
      if (cocok) {
        teks.value = cocok;
        teks.setSelectionRange(v.length, cocok.length);
      }
    }
    hapusTerakhir = false;
    galat.textContent = '';
    gambarDaftar();
  };
  teks.onkeydown = ev => {
    if (ev.key === 'Backspace' || ev.key === 'Delete') hapusTerakhir = true;
    // Panah Atas/Bawah menelusuri daftar dari dalam kotak teks
    // (LabelQLineEdit.keyPressEvent).
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault();
      const daftar = [...el('dlg-daftar').querySelectorAll('.it span:first-of-type')]
        .map(x => x.textContent);
      if (!daftar.length) return;
      const i = daftar.indexOf(teks.value.trim());
      const n = ev.key === 'ArrowDown'
        ? (i < 0 ? 0 : Math.min(i + 1, daftar.length - 1))
        : (i < 0 ? daftar.length - 1 : Math.max(i - 1, 0));
      teks.value = daftar[n];
      gambarDaftar();
    }
  };
  el('dlg-flagbaru').onkeydown = ev => {
    if (ev.key !== 'Enter') return;
    ev.preventDefault();
    const v = ev.target.value.trim();
    if (!v) return;
    flagSekarang[v] = true;
    ev.target.value = '';
    gambarFlags();
  };

  function setuju() {
    const v = teks.value.trim();
    // validate() di AnyLabeling hanya menerima teks tidak kosong; validator
    // kotaknya yang menolak nama yang diawali spasi atau cuma satu huruf.
    if (!v) { galat.textContent = 'Nama kelas belum diisi.'; teks.focus(); return; }
    if (!namaKelasSah(v)) {
      galat.textContent = 'Nama kelas minimal dua karakter dan tidak boleh diawali spasi.';
      teks.focus(); return;
    }
    const pesanGuard = periksaKelas(v);
    if (pesanGuard) { galat.textContent = pesanGuard; teks.focus(); return; }
    const g = grup.value.trim();
    if (g && !/^\d+$/.test(g)) {
      galat.textContent = 'Group ID hanya boleh angka.'; grup.focus(); return;
    }
    tutupDialog({ label: v, group_id: g === '' ? null : parseInt(g, 10),
                  flags: flagSekarang });
  }

  el('dlg-ok').onclick = setuju;
  el('dlg-batal').onclick = () => tutupDialog(null);

  gambarFlags();
  gambarDaftar();
  kotak.hidden = false;
  teks.focus();
  teks.setSelectionRange(0, teks.value.length);
  return new Promise(res => { dlgSelesai = res; });
}

function tutupDialog(hasil) {
  el('dlg').hidden = true;
  const res = dlgSelesai;
  dlgSelesai = null;
  if (res) res(hasil);
}

const dialogTerbuka = () => !el('dlg').hidden;

// Enter menyimpan, Escape membatalkan — dan keduanya ditangkap di fase penangkap
// supaya tidak pernah sampai ke pintasan kanvas.
window.addEventListener('keydown', ev => {
  if (!dialogTerbuka()) return;
  if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); tutupDialog(null); }
  else if (ev.key === 'Enter' && ev.target.id !== 'dlg-flagbaru') {
    ev.preventDefault(); ev.stopPropagation(); el('dlg-ok').click();
  }
}, true);

/*
 * Padanan new_shape (label_widget.py:1909-1961): satu-satunya pintu bentuk baru
 * masuk ke dataset. Semua mode menggambar mengalir ke sini supaya aturan
 * kelasnya cuma ada di satu tempat.
 */
async function sahkanBentuk(jenis, titik) {
  const dipanel = S.label;
  let hasil = null;

  if (!S.v.tanyaKelas && dipanel) {
    // Kelas sudah dipilih di panel dan dialog dimatikan -> langsung pakai,
    // seperti AnyLabeling saat display_label_popup mati.
    hasil = { label: dipanel, group_id: null, flags: {} };
  } else if (!S.v.tanyaKelas && S.v.labelTerakhir && kelasTerakhir()) {
    hasil = { label: kelasTerakhir(), group_id: null, flags: {} };
  } else {
    hasil = await tanyaKelas({ label: dipanel || kelasTerakhir() });
  }
  if (!hasil) return null;              // dibatalkan -> bentuknya dibuang

  simpanUndo();
  const b = { label: hasil.label, shape_type: jenis, points: titik, text: '',
              group_id: hasil.group_id, flags: hasil.flags || {}, titipan: {} };
  S.shapes.push(b);
  S.sel = S.shapes.length - 1;
  S.terpilih = [S.sel];
  if (!S.kelas.includes(hasil.label)) { S.kelas.push(hasil.label); S.kelas.sort(); }
  simpanKelasTerakhir(hasil.label);
  tandaiKotor();
  render();
  return b;
}

// ---------------------------------------------------------------- papan tombol

/*
 * Peta tombol mengikuti ~/.anylabelingrc supaya refleks orang yang sudah biasa
 * dengan AnyLabeling tetap berlaku. Yang paling penting: Backspace membuang
 * SATU TITIK, bukan seluruh objek — salah di sini berarti orang kehilangan
 * pekerjaan karena menekan tombol yang di aplikasi sebelahnya aman.
 */
/*
 * Di AnyLabeling pemetaan tombol ini adalah metode Canvas (canvas.py:1089),
 * jadi ia hanya berlaku saat kanvas yang memegang fokus dan dock Text Editor
 * menelan tombolnya sendiri. Di peramban pendengarnya menempel di window, jadi
 * pengecualian itu harus ditulis sendiri — dan TEXTAREA sempat tertinggal.
 * Akibatnya saat mengetik catatan objek: spasi tidak bisa diketik, Backspace
 * membuang TITIK BENTUK alih-alih huruf, dan Delete menghapus objeknya.
 */
function sedangMengetik(t) {
  return t && (t.tagName === 'INPUT' || t.tagName === 'SELECT'
               || t.tagName === 'TEXTAREA' || t.isContentEditable);
}

window.addEventListener('keydown', ev => {
  if (sedangMengetik(ev.target)) return;
  if (ev.key === ' ') { spasi = true; ev.preventDefault(); return; }

  if (ev.ctrlKey && ev.shiftKey && ev.key.toLowerCase() === 'p') {
    ev.preventDefault(); tambahTitikDiSisi(); return;
  }
  if (ev.key.startsWith('Arrow')) {
    ev.preventDefault();
    // canvas.py memasang panah di dalam `elif self.editing():` — jadi panah
    // menggeser bentuk HANYA di mode Sunting. Di mode lain panah dipakai
    // pindah gambar, sama seperti di grid dan tampilan besar aplikasi ini.
    // Tanpa pembatasan mode, Finish Object yang menyisakan bentuk terpilih
    // membuat panah menggeser bentuk padahal orang bermaksud pindah gambar.
    if (S.mode === 'edit' && S.sel >= 0) {
      const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0],
                  ArrowUp: [0, -1], ArrowDown: [0, 1] }[ev.key];
      if (d) geserDenganPanah(d[0] * MOVE_SPEED, d[1] * MOVE_SPEED);
    } else if (ev.key === 'ArrowLeft') {
      pindah(D.prev);
    } else if (ev.key === 'ArrowRight') {
      pindah(D.next);
    }
    return;
  }
  /*
   * Ctrl+Shift diperiksa LEBIH DULU dan terpisah. Sebelumnya blok Ctrl di bawah
   * tidak pernah menengok shiftKey, sehingga Ctrl+Shift+D — yang di AnyLabeling
   * berarti "gambar berikutnya" — jatuh ke penggandaan objek, lalu autosave
   * menuliskan gandaannya ke berkas tanpa satu pun tanda.
   */
  if (ev.ctrlKey && ev.shiftKey) {
    const sk = ev.key.toLowerCase();
    if (sk === 'd') { ev.preventDefault(); pindah(D.next); }
    else if (sk === 'a') { ev.preventDefault(); pindah(D.prev); }
    else if (sk === 'f') { ev.preventDefault(); muatKeLebar(); }
    else if (sk === 's') { ev.preventDefault(); simpan(); }
    return;
  }
  if (ev.ctrlKey && !ev.shiftKey) {
    const ck = ev.key.toLowerCase();
    if (ck === 'z') {
      ev.preventDefault();
      // Saat sedang menggambar, Ctrl+Z mencabut TITIK TERAKHIR, bukan
      // membatalkan suntingan sebelumnya. AnyLabeling memisahkannya lewat
      // enable/disable timbal balik (label_widget.py:1433-1441): selama
      // menggambar, `undo` mati dan `undo_last_point` hidup. Dulu di sini
      // keduanya satu jalur, sehingga refleks Ctrl+Z di tengah poligon memutar
      // balik objek yang sudah jadi — perubahan yang tidak diminta dan tidak
      // terlihat karena perhatian ada di poligon yang sedang digambar.
      if (S.draft) cabutTitikTerakhir(); else urungkan();
    }
    else if (ck === 's') { ev.preventDefault(); simpan(); }
    else if (ck === 'f') { ev.preventDefault(); muatKeLayar(); }
    else if (ck === 'e') { ev.preventDefault(); ubahKelasTerpilih(); }
    else if (ck === 'd') { ev.preventDefault(); duplikatTerpilih(); }
    else if (ck === 'c') { ev.preventDefault(); salinTerpilih(); }
    else if (ck === 'v') { ev.preventDefault(); tempel(); }
    else if (ev.key === '0') { ev.preventDefault(); zoomAsli(); }
    else if (ev.key === '+' || ev.key === '=') { ev.preventDefault(); zoomDi(1.25, c.width / 2, c.height / 2); }
    else if (ev.key === '-') { ev.preventDefault(); zoomDi(1 / 1.25, c.width / 2, c.height / 2); }
    else if (ck === 'p') {
      // Keep Previous Annotation (label_widget.py:387-394). Ditahan juga supaya
      // dialog cetak peramban tidak terbuka di tengah pekerjaan.
      ev.preventDefault();
      const kb = el('v-keepprev');
      if (kb) { kb.checked = !kb.checked; kb.dispatchEvent(new Event('change')); }
      toast('Pertahankan anotasi sebelumnya: ' + (kb && kb.checked ? 'nyala' : 'mati'));
    }
    return;
  }

  const k = ev.key.toLowerCase();
  let tertangani = true;
  if (k === 'q') setMode('p+');
  else if (k === 'e') setMode('p-');
  else if (k === 'r') setMode('kotak');          // rectangle manual, seperti AnyLabeling
  else if (k === 'p') setMode('poly');
  else if (k === 'v') setMode('edit');
  else if (k === 'g') grupTerpilih();
  else if (k === 'u') lepasGrupTerpilih();
  else if (k === 'f') finishObject();
  else if (k === 'c') bersihkanPrompt();
  else if (k === 'a') pindah(D.prev);
  else if (k === 'd') pindah(D.next);
  else if (ev.key === 'Enter') {
    // Enter mengakhiri bentuk bertitik-banyak apa pun yang sedang digambar.
    if (S.draft) tutupDraft(); else finishObject();
  }
  else if (ev.key === 'Escape') { S.draft = null; S.sel = -1; S.terpilih = []; S.selv = -1; S.sisi = null; tutupMenu(); bersihkanPrompt(); render(); }
  else if (ev.key === 'Delete') hapusTerpilih();
  else if (ev.key === 'Backspace') hapusTitikTerpilih();
  else tertangani = false;

  /*
   * Pintasan yang DITANGANI selalu menahan aksi bawaan peramban.
   *
   * Bukan kerapian. Sebagian pintasan ini membuka dialog dan memindahkan fokus
   * ke kotak isian di dalamnya — F membuka "Kelas untuk objek ini". Aksi
   * bawaan keydown berjalan SESUDAH penangan ini selesai, dan yang menerimanya
   * adalah elemen yang fokusnya baru saja pindah. Akibatnya menekan F mengisi
   * kotak kelas dengan huruf "f": kotaknya tidak lagi kosong, daftar kelas di
   * bawahnya tersaring ke nama yang berawalan "f" sehingga tampak kosong juga,
   * dan orang mengetik nama kelasnya di belakang huruf yang tidak pernah ia
   * maksud ketikkan.
   *
   * Ditahan untuk semuanya, bukan cuma F, supaya pintasan berikutnya yang
   * membuka dialog tidak mengulang bug yang sama.
   */
  if (tertangani) ev.preventDefault();
});

window.addEventListener('keyup', ev => {
  if (ev.key === ' ') spasi = false;
  // Satu tahanan panah = satu langkah urungkan.
  if (ev.key.startsWith('Arrow')) geserBerjalan = false;
  // Snapping menyala lagi begitu Alt dilepas (canvas.py:1116-1118).
  if (!ev.altKey && S.altDitekan) { S.altDitekan = false; gambar(); }
});
// Alt mematikan tarik-magnet ke titik awal poligon (canvas.py:1100-1101).
// Tanpa ini, titik yang memang harus diletakkan dekat titik awal jadi mustahil.
window.addEventListener('keydown', ev => {
  if (ev.key === 'Alt' && !S.altDitekan) { S.altDitekan = true; gambar(); }
}, true);
// Kehilangan fokus jendela membuat keyup tidak pernah datang; tanpa ini
// snapping bisa tertinggal mati tanpa alasan yang terlihat.
window.addEventListener('blur', () => { S.altDitekan = false; spasi = false; });

// ---------------------------------------------------------------- salin & grup

/*
 * Papan tempel milik aplikasi, bukan papan tempel sistem — isinya bentuk, dan
 * disimpan di sessionStorage supaya bertahan saat pindah gambar. Itu intinya:
 * di AnyLabeling `_copied_shapes` hidup di objek jendela sehingga menyalin di
 * satu gambar lalu menempel di gambar lain memang bisa.
 */
const KUNCI_TEMPEL = 'labelapp_salinan';

function salinTerpilih() {
  if (!adaTerpilih()) { toast('Pilih objeknya dulu'); return; }
  const salinan = S.terpilih.map(i => JSON.parse(JSON.stringify(S.shapes[i])));
  try {
    sessionStorage.setItem(KUNCI_TEMPEL, JSON.stringify(salinan));
  } catch (e) {
    toast('Gagal menyalin');
    return;
  }
  pesan(`${salinan.length} objek disalin — Ctrl+V untuk menempel, juga di gambar lain`);
}

function tempel() {
  let salinan;
  try {
    salinan = JSON.parse(sessionStorage.getItem(KUNCI_TEMPEL) || '[]');
  } catch (e) {
    salinan = [];
  }
  if (!salinan.length) { toast('Belum ada yang disalin'); return; }
  simpanUndo();
  S.terpilih = [];
  salinan.forEach(s => {
    // Ditempel dengan geseran kecil supaya tidak menumpuk persis di atas
    // aslinya dan jadi tak terlihat — AnyLabeling memakai offset (2,2).
    const p = s.points.map(([x, y]) => [kurungX(x + 2), kurungY(y + 2)]);
    S.shapes.push({ ...s, points: p });
    S.terpilih.push(S.shapes.length - 1);
  });
  S.sel = S.shapes.length - 1;
  tandaiKotor();
  pesan(`${salinan.length} objek ditempel`);
  render();
}

/*
 * Grup mengikuti canvas.py:1236-1285. Dua hal yang mudah salah dan sengaja
 * ditiru: id baru diambil dari id terkecil yang sudah ada di seleksi (bukan
 * selalu id baru), dan melepas grup melepaskan SEMUA bentuk dengan id itu,
 * termasuk yang tidak sedang terpilih.
 */
function idGrupBaru() {
  const ada = S.shapes.map(s => s.group_id).filter(v => v != null);
  return (ada.length ? Math.max(...ada) : 0) + 1;
}

function grupTerpilih() {
  if (!adaTerpilih()) { toast('Pilih dulu objek yang mau digabung'); return; }
  simpanUndo();
  const id = S.terpilih.map(i => S.shapes[i].group_id).filter(v => v != null);
  const baru = id.length ? Math.min(...id) : idGrupBaru();
  if (id.length > 1) {
    const gabung = new Set(id);
    S.shapes.forEach(s => { if (gabung.has(s.group_id)) s.group_id = baru; });
  }
  S.terpilih.forEach(i => { S.shapes[i].group_id = baru; });
  tandaiKotor();
  pesan(`${S.terpilih.length} objek jadi grup ${baru}`);
  render();
}

function lepasGrupTerpilih() {
  if (!adaTerpilih()) { toast('Pilih objeknya dulu'); return; }
  const id = new Set(S.terpilih.map(i => S.shapes[i].group_id).filter(v => v != null));
  if (!id.size) { toast('Objek terpilih memang belum bergrup'); return; }
  simpanUndo();
  let n = 0;
  S.shapes.forEach(s => { if (id.has(s.group_id)) { s.group_id = null; n++; } });
  tandaiKotor();
  pesan(`${n} objek dilepas dari grup`);
  render();
}

function hapusTerpilih() {
  if (!adaTerpilih()) { toast('Pilih objeknya dulu — klik objek, Ctrl+klik untuk beberapa'); return; }
  simpanUndo();
  const n = S.terpilih.length;
  // Dihapus dari indeks terbesar supaya indeks yang belum dihapus tidak bergeser.
  [...S.terpilih].sort((a, b) => b - a).forEach(i => S.shapes.splice(i, 1));
  S.sel = -1;
  S.selv = -1;
  S.terpilih = [];
  tandaiKotor();
  pesan(`${n} objek dihapus`);
  render();
}

/** Backspace di AnyLabeling: buang satu titik, bukan seluruh objek. */
/**
 * Buang satu titik dari sebuah bentuk. Dipisah dari `hapusTitikTerpilih`
 * supaya Shift+klik bisa membuang titik yang sedang di bawah kursor tanpa
 * harus memilihnya lebih dulu — itu yang membuatnya terasa satu gerakan.
 */
function hapusTitikDi(i, v) {
  const s = S.shapes[i];
  if (!s || v < 0 || v >= s.points.length) return;
  if (s.shape_type === 'rectangle') { toast('Rectangle tidak bisa dikurangi titiknya'); return; }
  if (s.points.length <= 3) { toast('Poligon minimal 3 titik — pakai Delete untuk membuang objeknya'); return; }
  simpanUndo();
  s.points.splice(v, 1);
  if (S.sel === i && S.selv === v) S.selv = -1;
  else if (S.sel === i && S.selv > v) S.selv--;
  tandaiKotor();
  render();
}

function hapusTitikTerpilih() {
  if (S.sel < 0 || S.selv < 0) {
    toast('Klik dulu titik yang mau dibuang, atau Shift+klik langsung di titiknya');
    return;
  }
  hapusTitikDi(S.sel, S.selv);
}

/**
 * Ctrl+E: ubah kelas objek terpilih — padanan edit_label (label_widget.py:1558).
 *
 * Memakai dialog yang sama dengan objek baru, jadi group_id, flag per objek,
 * dan penjaga salah ketik berlaku di sini juga. Dulu jalur ini cuma prompt()
 * bawaan peramban tanpa satu pun pemeriksaan.
 */
async function ubahKelasTerpilih() {
  if (S.sel < 0) { toast('Pilih objeknya dulu'); return; }
  const s0 = S.shapes[S.sel];
  const hasil = await tanyaKelas({
    label: s0.label, group_id: s0.group_id, flags: s0.flags || {},
    judul: S.terpilih.length > 1
      ? `Ubah ${S.terpilih.length} objek terpilih` : 'Ubah kelas objek ini' });
  if (!hasil) return;
  const v = hasil.label;
  simpanUndo();
  // group_id dan flag hanya dikenakan ke objek utama; menyeragamkannya ke
  // seluruh seleksi akan menghapus grup yang berbeda-beda tanpa diminta.
  s0.group_id = hasil.group_id;
  s0.flags = hasil.flags || {};
  simpanKelasTerakhir(v);
  (adaTerpilih() ? S.terpilih : [S.sel]).forEach(i => { S.shapes[i].label = v; });
  if (!S.kelas.includes(v)) { S.kelas.push(v); S.kelas.sort(); }
  tandaiKotor();
  render();
}

/** Ctrl+D: duplikat objek terpilih, digeser sedikit supaya terlihat. */
function duplikatTerpilih() {
  if (!adaTerpilih()) { toast('Pilih objeknya dulu'); return; }
  simpanUndo();
  const baru = [];
  S.terpilih.forEach(i => {
    const s = S.shapes[i];
    S.shapes.push({ ...s, points: s.points.map(p => [kurungX(p[0] + 8), kurungY(p[1] + 8)]) });
    baru.push(S.shapes.length - 1);
  });
  S.terpilih = baru;
  S.sel = baru[baru.length - 1];
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
  // Kelas resmi dataset (data.yaml / classes.txt) ikut tampil. Dulu daftar ini
  // hanya berisi kelas yang KEBETULAN sudah terpakai, sehingga dataset YOLO
  // yang belum dianotasi membuka panel kosong padahal nama kelasnya ada di
  // data.yaml — dan seluruhnya harus diketik ulang dengan tangan.
  const daftar = semuaKelas();
  if (!daftar.length) {
    box.innerHTML = '<div class="obj-kosong">Belum ada kelas — tulis di bawah.</div>';
  }
  const resmi = new Set(D.kelas_resmi || []);
  daftar.forEach(k => {
    const d = document.createElement('div');
    d.className = 'kelas';
    if (k === S.label) d.setAttribute('data-on', '');
    const i = document.createElement('i');
    i.style.background = warna(k, 1);
    const sp = document.createElement('span');
    sp.textContent = k;
    d.append(i, sp);
    if (resmi.has(k)) d.title = 'kelas resmi dataset (data.yaml)';
    // Klik ulang melepas pilihan, seperti Esc / klik area kosong pada
    // EscapableQListWidget (escapable_qlist_widget.py:5-10). Tanpa keadaan
    // "belum memilih kelas", tidak ada rem terhadap pelabelan yang salah.
    d.onclick = () => { S.label = (S.label === k) ? '' : k; render(); };
    box.appendChild(d);
  });
}

const RINGKAS_JENIS = { rectangle: 'kotak', circle: 'lingkaran',
                        point: 'titik', line: 'garis' };

/** Pindahkan objek ke posisi lain di daftar (padanan label_order_changed). */
function pindahkanObjek(dari, ke) {
  if (dari === ke || dari < 0 || dari >= S.shapes.length) return;
  simpanUndo();
  const [s] = S.shapes.splice(dari, 1);
  S.shapes.splice(ke, 0, s);
  // Seleksi mengikuti objeknya, bukan posisinya — kalau tidak, objek lain
  // tiba-tiba jadi terpilih setelah urutannya digeser.
  S.terpilih = [ke];
  S.sel = ke;
  S.selv = -1;
  tandaiKotor();
  render();
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
    if (S.terpilih.includes(i)) d.setAttribute('data-on', '');
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
    b.textContent = RINGKAS_JENIS[s.shape_type]
                    || `${s.points.length} titik`;
    if (s.group_id != null) b.textContent += ` · g${s.group_id}`;
    d.appendChild(b);
    d.onclick = ev => { pilihBentuk(i, ev.ctrlKey); setMode('edit'); render(); };
    // item_double_clicked -> edit_label (label_widget.py:214)
    d.ondblclick = ev => {
      ev.preventDefault();
      pilihBentuk(i, false);
      setMode('edit');
      render();
      ubahKelasTerpilih();
    };
    // customContextMenuRequested -> menus.label_list, isinya Edit Label +
    // Delete (label_widget.py:801). Dulu klik kanan di sini memunculkan menu
    // peramban, dan satu-satunya jalan mengubah kelas dari daftar adalah
    // memutar lewat kanvas.
    d.oncontextmenu = ev => {
      ev.preventDefault();
      ev.stopPropagation();
      pilihBentuk(i, false);
      setMode('edit');
      render();
      pasangMenu(ev.clientX, ev.clientY, [
        ['judul', s.label || '(tanpa kelas)'],
        ['aksi', 'Ubah kelas', 'Ctrl+E', true, ubahKelasTerpilih, false],
        ['aksi', s.sembunyi ? 'Tampilkan' : 'Sembunyikan', '', true,
         () => { s.sembunyi = !s.sembunyi; render(); }, false],
        ['pisah'],
        ['aksi', 'Hapus objek', 'Del', true, hapusTerpilih, true],
      ]);
    };

    /*
     * Urutan objek bisa diubah dengan menyeret, seperti LabelListWidget yang
     * memakai InternalMove. Urutannya BERMAKNA: yang di bawah digambar paling
     * akhir sehingga menang saat objek bertumpuk, dan urutan itu ikut
     * tersimpan ke berkas.
     */
    d.draggable = true;
    d.dataset.i = i;
    d.ondragstart = e => {
      e.dataTransfer.setData('text/plain', String(i));
      e.dataTransfer.effectAllowed = 'move';
      d.setAttribute('data-seret', '');
    };
    d.ondragend = () => {
      d.removeAttribute('data-seret');
      box.querySelectorAll('.obj').forEach(n => n.removeAttribute('data-jatuh'));
    };
    d.ondragover = e => { e.preventDefault(); d.setAttribute('data-jatuh', ''); };
    d.ondragleave = () => d.removeAttribute('data-jatuh');
    d.ondrop = e => {
      e.preventDefault();
      d.removeAttribute('data-jatuh');
      const dari = Number(e.dataTransfer.getData('text/plain'));
      pindahkanObjek(dari, i);
    };
    box.appendChild(d);
  });
  // scroll_to_item (label_widget.py:1645-1647). Tanpa ini, pada gambar berisi
  // puluhan objek, memilih objek di kanvas menyorot baris yang berada di luar
  // layar — orang mengira tidak terjadi apa-apa.
  const aktif = box.querySelector('.obj[data-on]');
  if (aktif) aktif.scrollIntoView({ block: 'nearest' });
}

/*
 * Daftar berkas.
 *
 * Dibatasi MAKS_BERKAS baris. QListWidget di AnyLabeling hanya menggambar baris
 * yang terlihat; di sini tiap baris adalah simpul DOM sungguhan, dan daftarnya
 * dibangun ulang pada SETIAP huruf yang diketik di kotak cari. Pada dataset
 * puluhan ribu gambar itu membuat halaman berat sejak dimuat dan tersendat saat
 * mengetik. Yang dipotong selalu dikatakan jumlahnya — daftar yang diam-diam
 * terpotong lebih buruk daripada daftar yang mengaku terpotong.
 */
const MAKS_BERKAS = 300;

function renderBerkas() {
  const box = el('berkas');
  const q = el('cari').value.trim().toLowerCase();
  box.innerHTML = '';
  const cocok = D.berkas.filter(f => !q || f.nama.toLowerCase().includes(q));
  // Berkas yang sedang dibuka selalu ikut ditampilkan walau di luar potongan,
  // supaya penanda "sedang dibuka" tidak pernah hilang dari daftar.
  const tampil = cocok.slice(0, MAKS_BERKAS);
  if (!tampil.some(f => f.path === D.path)) {
    const ini = cocok.find(f => f.path === D.path);
    if (ini) tampil.push(ini);
  }
  tampil.forEach(f => {
    const a = document.createElement('a');
    a.className = 'fitem';
    a.href = '/label?path=' + encodeURIComponent(f.path);
    a.dataset.sev = f.sev;
    if (f.path === D.path) a.setAttribute('data-on', '');
    const i = document.createElement('i');
    const sp = document.createElement('span');
    sp.textContent = f.nama;
    a.append(i, sp);
    if (f.split) {
      const t = document.createElement('em');
      t.className = 'fsplit';
      t.textContent = f.split;
      a.appendChild(t);
    }
    if (f.n) {
      const b = document.createElement('b');
      b.textContent = f.n;
      a.appendChild(b);
    }
    a.onclick = e => {
      if (S.kotor && !confirm('Ada perubahan belum disimpan. Tinggalkan?')) e.preventDefault();
    };
    box.appendChild(a);
  });
  const sisa = cocok.length - Math.min(cocok.length, MAKS_BERKAS);
  const info = el('berkasinfo');
  if (sisa > 0) {
    info.textContent = `${sisa.toLocaleString('id-ID')} berkas lagi tidak `
      + `ditampilkan — pakai kotak cari di atas untuk mempersempit.`;
  } else {
    info.textContent = `${cocok.length.toLocaleString('id-ID')} berkas`;
  }
}

/* Panel Text Editor: terikat ke objek terpilih. Isinya masuk field `text`
   pada .json — field yang sama dipakai AnyLabeling, jadi catatan yang ditulis
   di sini terbaca di desktop dan sebaliknya. */
/*
 * Satu panel melayani DUA tingkat catatan, seperti shape_text_changed
 * (label_widget.py:1692-1700): ada objek terpilih -> catatan objek itu; tidak
 * ada -> catatan tingkat GAMBAR (other_data["image_text"]). Judul panelnya ikut
 * berganti, sama seperti label "Object Text" / "Image Text" di sana.
 *
 * Dulu panelnya sekadar dimatikan saat tidak ada yang terpilih, sehingga
 * catatan tingkat gambar yang dibuat di desktop tidak pernah terlihat dari web
 * dan tidak bisa dibuat dari web.
 */
function renderTeks() {
  const ta = el('teks'), info = el('teksinfo'), judul = el('teksjudul');
  ta.disabled = false;
  if (S.sel < 0) {
    if (document.activeElement !== ta) ta.value = S.teksGambar || '';
    judul.textContent = 'Image Text';
    info.textContent = 'Catatan untuk gambar ini (tanpa objek terpilih)';
    return;
  }
  const s = S.shapes[S.sel];
  if (document.activeElement !== ta) ta.value = s.text || '';
  judul.textContent = 'Object Text';
  info.textContent = `Catatan untuk objek "${s.label || 'tanpa kelas'}"`;
}

/* Panel Flags: flag tingkat gambar, disimpan di `flags` tingkat atas .json. */
function renderFlags() {
  const box = el('flags'), nama = Object.keys(S.flags).sort();
  el('nflag').textContent = nama.filter(k => S.flags[k]).length;
  box.innerHTML = '';
  if (!nama.length) {
    box.innerHTML = '<div class="flag-kosong">Belum ada flag — tulis di bawah.</div>';
    return;
  }
  nama.forEach(k => {
    const d = document.createElement('label');
    d.className = 'flag';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = !!S.flags[k];
    cb.onchange = () => { S.flags[k] = cb.checked; tandaiKotor(); renderFlags(); };
    const sp = document.createElement('span');
    sp.textContent = k;
    const x = document.createElement('button');
    x.textContent = '\u00d7';
    x.title = 'Buang flag ini';
    x.onclick = e => { e.preventDefault(); delete S.flags[k]; tandaiKotor(); renderFlags(); };
    d.append(cb, sp, x);
    box.appendChild(d);
  });
}

function render() { gambar(); renderKelas(); renderObjek(); renderTeks(); renderFlags(); }
function status(t) { el('status').textContent = t; }
function pesan(t) { el('pesan').textContent = t; status(t); }

// ---------------------------------------------------------------- simpan

/**
 * Tulis keadaan gambar ini ke berkas.
 * @param {boolean} diam  true saat dipanggil autosave — kabar sukses cukup di
 *   baris status, tidak perlu toast yang muncul tiap kali titik digeser.
 *   Kegagalan tetap selalu ditoastkan; itu justru yang harus terlihat.
 */
async function simpan(diam = false) {
  clearTimeout(waktuAutosave);
  // Satu pintu untuk seluruh jalur penulisan: tombol Simpan, Ctrl+S, dan
  // simpan otomatis semuanya lewat sini. Menjaganya di sini saja berarti tidak
  // ada jalur yang lupa dijaga.
  if (BACA_SAJA) {
    if (!diam) toast('Gambar ini ditugaskan ke orang lain; kamu hanya bisa melihat');
    return;
  }
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
        flags: S.flags,
        // Catatan tingkat gambar ikut dikirim supaya bisa DISUNTING dari web,
        // bukan sekadar terbawa lewat jalur field asing.
        teks_gambar: S.teksGambar,
      }),
    });
    const j = await r.json();
    if (!j.ok) { toast('Gagal simpan: ' + (j.error || j.detail)); status('Gagal simpan'); return; }
    S.kotor = false;
    el('btn-simpan').removeAttribute('data-kotor');
    const f = D.berkas.find(x => x.path === D.path);
    if (f) { f.n = j.n; f.sev = j.sev; renderBerkas(); }
    pesan(`Tersimpan · ${j.n} objek · ${(j.issues || []).join(' · ') || 'tidak ada temuan'}`);
    // Peringatan dari penulisan label YOLO selalu ditampilkan, termasuk saat
    // autosave: kalau sebuah bentuk atau kelas tidak ikut tersimpan ke berkas
    // latihan, itu justru hal yang tidak boleh lewat tanpa terlihat.
    if ((j.peringatan || []).length) toast(j.peringatan.join(' · '));
    else if (!diam) toast('Tersimpan');
  } catch (e) {
    toast('Gagal menghubungi server');
    status('Gagal simpan');
  }
}

async function pindah(path) {
  if (!path) { toast('Sudah di ujung'); return; }
  // AnyLabeling bawaannya auto_save: True — pindah gambar menyimpan sendiri.
  if (S.kotor && S.v.autosave) {
    await simpan();
    if (S.kotor) { toast('Belum tersimpan — pindah dibatalkan'); return; }
  } else if (S.kotor && !confirm('Ada perubahan belum disimpan. Tinggalkan?')) {
    return;
  }
  titipUntukKeepPrev();
  titipZoom();
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
el('btn-dup').onclick = duplikatTerpilih;
el('teks').oninput = () => {
  if (S.sel < 0) S.teksGambar = el('teks').value;
  else S.shapes[S.sel].text = el('teks').value;
  tandaiKotor();
};
el('flagbaru').addEventListener('keydown', ev => {
  if (ev.key !== 'Enter') return;
  const v = ev.target.value.trim();
  if (!v) return;
  S.flags[v] = true;
  ev.target.value = '';
  tandaiKotor();
  renderFlags();
});
el('model').onchange = () => { if (S.prompt.length || S.kotak) jalankanSam(); };

/*
 * Penjaga salah ketik nama kelas.
 *
 * Tanpa ini, mengetik "Botol" pada dataset yang kelasnya "botol" langsung
 * membuat kelas keenam tanpa peringatan apa pun. Baru ketahuan saat membuka
 * data.yaml hasil ekspor — atau lebih buruk, saat model dilatih dan satu kelas
 * cuma punya tiga contoh. Padanan `validate_label: exact` di AnyLabeling
 * (label_widget.py:1542-1556), tetapi menahan alih-alih menolak mentah:
 * kelas yang memang baru tetap bisa ditambahkan, hanya perlu disengaja.
 */

/** Jarak sunting Levenshtein, dibatasi supaya berhenti lebih awal. */
function jarakNama(a, b, batas = 2) {
  if (Math.abs(a.length - b.length) > batas) return batas + 1;
  let baris = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    let prev = baris[0];
    baris[0] = i;
    let min = i;
    for (let j = 1; j <= b.length; j++) {
      const simpan = baris[j];
      baris[j] = a[i - 1] === b[j - 1]
        ? prev
        : 1 + Math.min(prev, baris[j], baris[j - 1]);
      prev = simpan;
      if (baris[j] < min) min = baris[j];
    }
    if (min > batas) return batas + 1;      // tidak mungkin membaik lagi
  }
  return baris[b.length];
}

/** Kelas yang paling mirip dengan `v`, atau null kalau tidak ada yang dekat. */
function kelasMirip(v) {
  const p = v.toLowerCase();
  const daftar = [...new Set([...(D.kelas_resmi || []), ...S.kelas])];
  // Beda huruf besar-kecil saja itu tanda paling kuat, jadi didahulukan.
  const sama = daftar.find(k => k.toLowerCase() === p && k !== v);
  if (sama) return sama;
  let terbaik = null, terdekat = 3;
  for (const k of daftar) {
    if (k === v) continue;
    // Nama pendek terlalu mudah "mirip"; 1 huruf beda dari "cup" bisa jadi
    // kelas yang benar-benar lain.
    const batas = Math.min(k.length, v.length) >= 5 ? 2 : 1;
    const d = jarakNama(p, k.toLowerCase(), batas);
    if (d <= batas && d < terdekat) { terdekat = d; terbaik = k; }
  }
  return terbaik;
}

let kelasMenunggu = null;    // nama yang sudah diperingatkan, tinggal ditegaskan

/**
 * Periksa nama kelas. Mengembalikan pesan peringatan, atau '' kalau boleh.
 *
 * Dipakai oleh SEMUA jalur pemberian kelas — dialog objek baru, Ctrl+E, dan
 * kotak "kelas baru" di panel. Dulu penjaganya hanya terpasang di kotak panel,
 * sehingga Ctrl+E — jalur tercepat mengubah kelas — justru satu-satunya yang
 * tanpa pengaman, dan mengetik "Botol" di sana langsung membuat kelas keenam.
 *
 * Menahan, bukan menolak: menegaskan sekali lagi tetap membuat kelasnya.
 */
function periksaKelas(v) {
  if (S.kelas.includes(v) || (D.kelas_resmi || []).includes(v)) { kelasMenunggu = null; return ''; }
  if (kelasMenunggu === v) { kelasMenunggu = null; return ''; }
  const mirip = kelasMirip(v);
  const resmi = D.kelas_resmi || [];
  if (mirip) {
    kelasMenunggu = v;
    return `"${v}" mirip dengan "${mirip}" — salah ketik? Tegaskan sekali lagi kalau memang kelas baru.`;
  }
  if (resmi.length) {
    kelasMenunggu = v;
    return `"${v}" belum ada di daftar ${resmi.length} kelas dataset. Tegaskan sekali lagi kalau memang kelas baru.`;
  }
  return '';                       // dataset tanpa daftar resmi: tidak ada yang dilanggar
}

/*
 * Pilih kelas untuk bentuk BERIKUTNYA. Objek yang sedang terpilih sengaja tidak
 * disentuh: di AnyLabeling panel Labels sama sekali tidak punya jalur ke bentuk
 * yang sudah ada — tooltipnya sendiri berbunyi "Select label to start
 * annotating for it" (label_widget.py:227-229). Dulu memilih kelas di sini
 * diam-diam melabeli ulang objek yang masih terpilih dari pekerjaan sebelumnya,
 * lalu autosave menuliskannya. Untuk mengubah objek yang ada, pakai Ctrl+E.
 */
function pakaiKelas(v) {
  if (!S.kelas.includes(v)) S.kelas.push(v);
  S.kelas.sort();
  S.label = v;
  el('kelasbaru').value = '';
  kelasMenunggu = null;
  render();
}

el('kelasbaru').addEventListener('input', () => { kelasMenunggu = null; });

el('kelasbaru').addEventListener('keydown', ev => {
  if (ev.key !== 'Enter') return;
  const v = ev.target.value.trim();
  if (!v) return;

  if (!namaKelasSah(v)) {
    toast('Nama kelas minimal dua karakter dan tidak boleh diawali spasi.');
    return;
  }
  const pesanGuard = periksaKelas(v);
  if (pesanGuard) { toast(pesanGuard); return; }
  pakaiKelas(v);
});

el('prev').onclick = e => { e.preventDefault(); pindah(D.prev); };
el('next').onclick = e => { e.preventDefault(); pindah(D.next); };
window.addEventListener('resize', () => {
  // resizeEvent -> adjust_scale() di AnyLabeling: selama bukan zoom manual,
  // gambar dipaskan ulang. Tanpa ini, membuka/menutup panel membuat gambar
  // tidak lagi muat dan sebagiannya keluar area tanpa cara mudah kembali.
  ukur();
  if (S.zoomManual) gambar(); else muatKeLayar();
});

img.onload = () => {
  ukur();
  // Zoom dipertahankan hanya kalau setelannya menyala; kalau tidak, kembali
  // muat-jendela seperti biasa.
  if (!pakaiZoomTitipan()) muatKeLayar();
  render();
  pesan(`${D.nama} siap`);
};
img.onerror = () => { pesan('Gambar gagal dimuat'); toast('Gambar gagal dimuat'); };
img.src = '/gambar?path=' + encodeURIComponent(D.path);

setMode('p+');
renderBerkas();
render();

// ------------------------------------------------- kecerahan & kontras

/*
 * Nilainya diingat PER GAMBAR, sama seperti brightness_contrast_values di
 * AnyLabeling — bukan setelan global. Gambar yang gelap biasanya cuma
 * sebagian, jadi menyeret setelan itu ke seluruh dataset lebih sering
 * mengganggu daripada menolong.
 */
const KUNCI_CERAH = 'labelapp_cerah';

function bacaCerah() {
  try { return JSON.parse(sessionStorage.getItem(KUNCI_CERAH)) || {}; }
  catch (e) { return {}; }
}

function terapkanCerah(simpan) {
  const bs = el('cerah'), ks = el('kontras');
  if (!bs || !ks) return;
  S.cerah = bs.value / 50;
  S.kontras = ks.value / 50;
  el('cerah-nilai').textContent = S.cerah.toFixed(2);
  el('kontras-nilai').textContent = S.kontras.toFixed(2);
  if (simpan) {
    const d = bacaCerah();
    if (bs.value == 50 && ks.value == 50) delete d[D.path];
    else d[D.path] = [Number(bs.value), Number(ks.value)];
    try { sessionStorage.setItem(KUNCI_CERAH, JSON.stringify(d)); } catch (e) { /* abai */ }
  }
  gambar();
}

(function pasangCerah() {
  const bs = el('cerah'), ks = el('kontras');
  if (!bs || !ks) return;
  const tersimpan = bacaCerah()[D.path];
  if (Array.isArray(tersimpan)) { bs.value = tersimpan[0]; ks.value = tersimpan[1]; }
  bs.oninput = ks.oninput = () => terapkanCerah(true);
  el('btn-reset-cerah').onclick = () => {
    bs.value = 50; ks.value = 50; terapkanCerah(true);
  };
  terapkanCerah(false);
})();

// ------------------------------------------------- keep_prev

/*
 * Padanan "Keep Previous Annotation" (label_widget.py:2184-2195).
 *
 * Syaratnya sama persis dan itu penting: objek gambar sebelumnya hanya disalin
 * kalau gambar yang baru dibuka BELUM punya objek sama sekali. Tanpa syarat
 * itu, anotasi yang sudah benar akan tertimpa.
 */
const KUNCI_KEEPPREV = 'labelapp_bentuk_sebelumnya';

function titipUntukKeepPrev() {
  if (!S.v.keepPrev) return;
  try {
    sessionStorage.setItem(KUNCI_KEEPPREV, JSON.stringify(
      S.shapes.filter(s => s.label)));
  } catch (e) { /* abai */ }
}

// Dipanggil SETELAH muatView(), karena baru di sana S.v.keepPrev punya nilai
// yang sebenarnya. Dipanggil lebih awal, fiturnya diam-diam tidak pernah aktif.
function pakaiKeepPrev() {
  if (!S.v.keepPrev || S.shapes.length) return;
  let sebelumnya = [];
  try { sebelumnya = JSON.parse(sessionStorage.getItem(KUNCI_KEEPPREV)) || []; }
  catch (e) { /* abai */ }
  if (!sebelumnya.length) return;
  S.shapes = sebelumnya.map(s => ({
    ...s, points: s.points.map(([x, y]) => [kurungX(x), kurungY(y)]) }));
  tandaiKotor();
  pesan(`${S.shapes.length} objek disalin dari gambar sebelumnya — periksa dulu`);
  render();
}

// ---------------------------------------------------------------- menu View

/*
 * Meniru menu View AnyLabeling: panel bisa disembunyikan satu-satu, dan
 * beberapa hal tampilan dimatikan. Semua tersimpan di localStorage supaya
 * pilihan orang tidak hilang saat pindah gambar — di AnyLabeling setelan ini
 * juga bertahan lewat ~/.anylabelingrc.
 */
const VKUNCI = 'labelapp_view';

function simpanView() {
  const panel = {};
  document.querySelectorAll('#view-isi input[data-panel]').forEach(cb => {
    panel[cb.dataset.panel] = cb.checked;
  });
  localStorage.setItem(VKUNCI, JSON.stringify({ v: S.v, panel }));
}

/*
 * keep_prev_scale (label_widget.py:606-612). Di AnyLabeling zoom per gambar
 * disimpan di memori aplikasi; di sini pindah gambar memuat ulang halaman, jadi
 * nilainya dititipkan. Sebelumnya kotak centangnya sama sekali tidak pernah
 * dibaca — menyalakannya tidak mengubah apa pun.
 */
const KUNCI_ZOOM = 'labelapp_zoom_tetap';

function titipZoom() {
  if (!S.v.zoomTetap) return;
  try {
    sessionStorage.setItem(KUNCI_ZOOM, JSON.stringify(
      { zoom: S.zoom, panx: S.panx, pany: S.pany }));
  } catch (e) { /* mode privat */ }
}

function pakaiZoomTitipan() {
  if (!S.v.zoomTetap) return false;
  try {
    const d = JSON.parse(sessionStorage.getItem(KUNCI_ZOOM) || 'null');
    if (!d || !d.zoom) return false;
    S.zoom = d.zoom; S.panx = d.panx; S.pany = d.pany;
    gambar();
    return true;
  } catch (e) { return false; }
}

function terapkanPanel(id, tampil) {
  const n = el(id);
  if (n) n.hidden = !tampil;
}

function muatView() {
  let d = {};
  try { d = JSON.parse(localStorage.getItem(VKUNCI)) || {}; } catch (e) { /* abai */ }
  Object.assign(S.v, d.v || {});
  const peta = { 'v-teks': 'teks', 'v-grup': 'grup',
                 'v-kelas': 'namaKelas',
                 'v-isi': 'isi', 'v-silang': 'silang',
                 'v-tanyakelas': 'tanyaKelas',
                 'v-labelterakhir': 'labelTerakhir', 'v-zoomtetap': 'zoomTetap',
                 'v-autosave': 'autosave', 'v-keepprev': 'keepPrev' };
  for (const [id, kunci] of Object.entries(peta)) {
    const cb = el(id);
    if (!cb) continue;
    cb.checked = !!S.v[kunci];
    cb.onchange = () => { S.v[kunci] = cb.checked; simpanView(); gambar(); };
  }
  document.querySelectorAll('#view-isi input[data-panel]').forEach(cb => {
    const p = cb.dataset.panel;
    if (d.panel && p in d.panel) cb.checked = d.panel[p];
    terapkanPanel(p, cb.checked);
    cb.onchange = () => { terapkanPanel(p, cb.checked); simpanView(); };
  });
}

/*
 * toggle_polygons (label_widget.py:2069-2073): mengubah centang SETIAP objek di
 * panel Objects, bukan menyalakan sebuah saringan tersendiri. Karena itulah di
 * AnyLabeling ia dua aksi terpisah, bukan satu centang — dan karena itu pula
 * objek yang disembunyikan satu-satu bisa dikembalikan sekaligus. Dulu di sini
 * tidak ada padanannya sama sekali: yang sudah disembunyikan hanya bisa
 * dimunculkan satu per satu.
 */
function tampilkanSemuaObjek(tampil) {
  S.shapes.forEach(s => { s.sembunyi = !tampil; });
  if (!tampil) { S.hover = null; S.sisi = null; petunjuk(null); }
  render();
  toast(tampil ? 'Semua objek ditampilkan' : 'Semua objek disembunyikan');
}

el('v-tampilsemua').onclick = () => tampilkanSemuaObjek(true);
el('v-sembunyisemua').onclick = () => tampilkanSemuaObjek(false);

const menuView = el('menu-view');
el('view-tombol').onclick = ev => {
  ev.stopPropagation();
  menuView.toggleAttribute('data-buka');
};
document.addEventListener('click', ev => {
  if (!menuView.contains(ev.target)) menuView.removeAttribute('data-buka');
});

muatView();
pakaiKeepPrev();
render();
