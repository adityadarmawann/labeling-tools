/*
 * Papan anotasi: menu kelola per kartu.
 *
 * "Tugaskan ulang" memakai dialog: ketik username HIGOLAB/email -> Periksa ->
 * kalau akunnya ada tinggal tugaskan (anggota) atau jadikan pelabel dulu (bukan
 * anggota); kalau belum ada, buatkan undangan link lewat email. Tidak menebar
 * seluruh daftar akun/email tim. Catatan & judul tetap prompt(): teks bebas.
 */
(() => {
  const isi = document.getElementById('an-isi');
  if (!isi) return;

  // ---- buka/tutup menu
  for (const b of document.querySelectorAll('.an-titik')) {
    b.onclick = (ev) => {
      ev.stopPropagation();
      const m = b.closest('.an-menu');
      const buka = m.hasAttribute('data-buka');
      // Menu lain ditutup lebih dulu: dua menu terbuka sekaligus membuat
      // tindakan yang diklik jadi ambigu bagi yang melihatnya.
      document.querySelectorAll('.an-menu[data-buka]')
        .forEach(x => x.removeAttribute('data-buka'));
      if (!buka) m.setAttribute('data-buka', '');
    };
  }
  document.addEventListener('click', () => {
    document.querySelectorAll('.an-menu[data-buka]')
      .forEach(x => x.removeAttribute('data-buka'));
  });
  for (const m of document.querySelectorAll('.an-menu .menu-isi')) {
    m.addEventListener('click', ev => ev.stopPropagation());
  }

  // ---- masukkan borongan yang sudah dianotasi tetapi belum ditugaskan
  for (const b of document.querySelectorAll('.an-siap')) {
    b.onclick = async () => {
      const batch = b.dataset.siap || '';
      const n = (b.textContent.match(/\d+/) || [''])[0];
      // Ditanyakan lebih dulu karena akibatnya tidak terlihat di halaman ini:
      // gambarnya pindah ke Dataset, dan sejak itu ikut terekspor.
      if (!confirm(`Masukkan ${n} gambar yang sudah dianotasi ke dataset?\n\n`
                   + 'Gambarnya pindah ke halaman Dataset dan ikut terekspor. '
                   + 'Bisa dikeluarkan lagi lewat halaman tugasnya.')) return;
      b.disabled = true;
      const j = await send('/api/tugas/dataset-siap', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch }),
      });
      if (!j.ok) { toast(j.error); b.disabled = false; return; }
      toast(`${j.ditambah} gambar masuk dataset`);
      location.reload();
    };
  }

  for (const b of document.querySelectorAll('.an-hapus-batch')) {
    b.onclick = async () => {
      const batch = b.dataset.hapusBatch || '';
      const n = b.dataset.n || '';
      const nama = b.dataset.nama || 'batch ini';
      // Ke tempat sampah, bisa dipulihkan — tetapi tetap ditanya: ia membuang
      // SELURUH batch sekaligus, dan akibatnya tidak langsung terlihat di papan.
      if (!confirm(`Hapus ${n} gambar dari batch "${nama}"?\n\n`
                   + 'Berkasnya pindah ke tempat sampah projek dan bisa '
                   + 'dipulihkan. Gambar batch ini yang sudah dibagi ke orang '
                   + 'atau sudah masuk dataset tidak ikut terhapus.')) return;
      b.disabled = true;
      const j = await send('/api/tugas/hapus-batch', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch }),
      });
      if (!j.ok) { toast(j.error); b.disabled = false; return; }
      toast(`${j.dibuang} gambar dibuang ke tempat sampah`);
      location.reload();
    };
  }

  async function kirim(url, pesan) {
    const j = await post(url);
    if (!j.ok) { toast(j.error); return false; }
    toast(pesan);
    location.reload();
    return true;
  }

  // ---- tindakan
  for (const b of document.querySelectorAll('.menu-aksi[data-aksi]')) {
    b.onclick = async () => {
      const id = b.dataset.id;
      const kartu = b.closest('.an-kartu');
      const q = (s) => '/api/tugas/ubah?id=' + encodeURIComponent(id) + s;

      if (b.dataset.aksi === 'pelabel') {
        // Popup daftar akun (bisa dicari), bukan prompt() isian manual.
        return bukaTugaskan(id, kartu.dataset.pelabel || '');
      }

      if (b.dataset.aksi === 'catatan') {
        const el = kartu.querySelector('.an-catatan');
        const v = prompt('Catatan untuk pelabelnya:', el ? el.textContent.trim() : '');
        if (v === null) return;
        return kirim(q('&catatan=' + encodeURIComponent(v)), 'Catatan disimpan');
      }

      if (b.dataset.aksi === 'judul') {
        const el = kartu.querySelector('.an-kartu-judul');
        const v = prompt('Judul tugas ini:', el ? el.textContent.trim() : '');
        if (v === null) return;
        return kirim(q('&judul=' + encodeURIComponent(v)), 'Judul diganti');
      }

      if (b.dataset.aksi === 'bubar') {
        const siapa = kartu.dataset.pelabel || 'orang ini';
        // Membubarkan TIDAK menghapus label yang sudah dibuat. Dikatakan di
        // pertanyaannya, karena "yakin?" tanpa akibat yang jelas membuat orang
        // menebak.
        if (!confirm(`Bubarkan tugas ${siapa}?\n\n`
                     + 'Label yang sudah dibuat tetap ada. Gambarnya kembali ke '
                     + 'kolom "Belum ditugaskan" dan bisa dibagikan lagi.')) return;
        return kirim('/api/tugas/bubarkan?id=' + encodeURIComponent(id),
                     'Tugas dibubarkan');
      }
    };
  }

  // ---- dialog "Tugaskan ulang": periksa username/email lalu tugaskan/undang --
  const tgDlg = document.getElementById('tg-dlg');
  let tgId = '';
  let tgKini = '';
  const tgEsc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const tgPesan = (html) => { const b = document.getElementById('tg-hasil'); if (b) b.innerHTML = html; };
  function tgTutup() { if (tgDlg) tgDlg.hidden = true; }

  function bukaTugaskan(id, kini) {
    if (!tgDlg) return;
    tgId = id; tgKini = kini || '';
    tgDlg.hidden = false;
    const q = document.getElementById('tg-q');
    if (q) q.value = '';
    tgPesan('');
    if (q) setTimeout(() => q.focus(), 30);
  }

  async function tgPeriksa() {
    const q = (document.getElementById('tg-q').value || '').trim();
    if (!q) { toast('Isi username HIGOLAB atau email dulu'); return; }
    tgPesan('<p class="halus" style="padding:12px 20px">memeriksa…</p>');
    const j = await send('/api/tugas/periksa-akun?q=' + encodeURIComponent(q));
    if (!j || !j.ok) {
      tgPesan('<p class="halus" style="padding:12px 20px">'
        + tgEsc((j && j.error) || 'gagal memeriksa') + '</p>');
      return;
    }
    if (j.ada) tgRenderKetemu(j);
    else tgRenderTakAda(j, q);
  }

  // Akun ADA: tombol tugaskan (anggota) / jadikan pelabel + tugaskan (bukan).
  function tgRenderKetemu(a) {
    const kini = a.akun === tgKini;
    const badge = a.peran
      ? `<span class="tg-badge">${tgEsc(a.peran)}</span>`
      : '<span class="tg-badge tg-badge-luar">bukan anggota</span>';
    const tombol = kini
      ? '<span class="halus">Tugas ini sudah di akun tersebut.</span>'
      : `<button class="wz-tbl wz-tbl-pri" type="button" id="tg-ok">${
        a.anggota ? 'Tugaskan ke sini' : 'Jadikan pelabel &amp; tugaskan'}</button>`;
    tgPesan(`<div class="tg-kartu"><div class="tg-nama"><b>${tgEsc(a.nama)}</b> `
      + `<span class="halus">@${tgEsc(a.akun)}</span></div>${badge}</div>`
      + `<div class="tg-aksi">${tombol}</div>`);
    const ok = document.getElementById('tg-ok');
    if (ok) ok.onclick = () => tgTugaskan(a);
  }

  async function tgTugaskan(a) {
    const ok = document.getElementById('tg-ok');
    if (ok) ok.disabled = true;
    if (!a.anggota) {   // beri akses dulu; menugaskan saja tak memberi akses
      const u = await send('/api/tugas/undang?akun=' + encodeURIComponent(a.akun)
        + '&peran=pelabel&akses=semua', { method: 'POST' });
      if (!u || !u.ok) { toast((u && u.error) || 'gagal menambahkan anggota'); if (ok) ok.disabled = false; return; }
    }
    tgTutup();
    kirim('/api/tugas/ubah?id=' + encodeURIComponent(tgId)
      + '&pelabel=' + encodeURIComponent(a.akun),
      'Tugas dipindahkan ke ' + (a.nama || a.akun));
  }

  // Akun TAK ADA: kalau yang diketik email -> buat undangan (link); kalau
  // username -> minta email.
  function tgRenderTakAda(j, q) {
    if (!j.email) {
      tgPesan(`<div class="tg-kartu"><div><b>"${tgEsc(q)}"</b> tidak ditemukan.</div></div>`
        + '<p class="halus" style="padding:4px 20px">Itu bukan username terdaftar. '
        + 'Untuk mengundang orang baru, masukkan <b>email</b>-nya.</p>');
      return;
    }
    tgPesan(`<div class="tg-kartu"><div>Email <b>${tgEsc(j.email)}</b> belum punya akun.</div></div>`
      + '<div class="tg-aksi"><button class="wz-tbl wz-tbl-pri" type="button" id="tg-undang">Buat undangan</button></div>'
      + '<p class="halus" style="padding:0 20px 8px">Dibuatkan link undangan untuk '
      + 'dibagikan. Setelah dia bergabung, tugaskan lagi tugas ini.</p>');
    const b = document.getElementById('tg-undang');
    if (b) b.onclick = async () => {
      b.disabled = true;
      const r = await send('/api/tugas/undang-email?email=' + encodeURIComponent(j.email)
        + '&peran=pelabel&akses=semua', { method: 'POST' });
      if (!r || !r.ok) { toast((r && r.error) || 'gagal membuat undangan'); b.disabled = false; return; }
      if (r.sudah_terdaftar) {   // ternyata sudah punya akun -> langsung tugaskan
        tgTutup();
        kirim('/api/tugas/ubah?id=' + encodeURIComponent(tgId)
          + '&pelabel=' + encodeURIComponent(r.akun), 'Tugas dipindahkan ke ' + r.akun);
        return;
      }
      const kepala = r.terkirim
        ? `Undangan sudah DIKIRIM ke <b>${tgEsc(j.email)}</b>. Link cadangan:`
        : `Link undangan dibuat untuk <b>${tgEsc(j.email)}</b>. Email belum `
          + 'terkirim otomatis, jadi bagikan link ini:';
      tgPesan(`<div class="tg-kartu"><div>${kepala}</div></div>`
        + `<div class="tg-aksi"><input class="op-cari" id="tg-link" readonly value="${tgEsc(r.tautan)}">`
        + '<button class="wz-tbl wz-tbl-sek" type="button" id="tg-salin">Salin</button></div>');
      const inp = document.getElementById('tg-link');
      if (inp) inp.onfocus = () => inp.select();
      const s = document.getElementById('tg-salin');
      if (s) s.onclick = async () => {
        try { await navigator.clipboard.writeText(r.tautan); toast('Link disalin'); }
        catch (e) { if (inp) { inp.focus(); inp.select(); } toast('Tekan Ctrl+C untuk menyalin'); }
      };
    };
  }

  if (tgDlg) {
    tgDlg.querySelectorAll('[data-tg-tutup]').forEach((x) => { x.onclick = tgTutup; });
    const pb = document.getElementById('tg-periksa');
    if (pb) pb.onclick = tgPeriksa;
    const q = document.getElementById('tg-q');
    if (q) q.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); tgPeriksa(); }
    });
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape' && !tgDlg.hidden) tgTutup();
    });
  }
})();

/* ------------------------------------------------------ format anotasi
 * Setelan projek, bukan tampilan: ia menentukan bentuk mana yang dianggap
 * tidak sesuai, dan bentuk tak sesuai dikembalikan ke kolom "Belum
 * ditugaskan" saat versi dibuat. Karena itu halamannya dimuat ulang sesudah
 * disimpan -- temuan "perlu dicek" di seluruh papan ikut berubah, dan daftar
 * yang masih menunjukkan keadaan lama lebih menyesatkan daripada menunggu
 * sebentar.
 */
(() => {
  const sel = document.getElementById('an-jenis');
  if (!sel) return;
  sel.onchange = async () => {
    const pesan = document.getElementById('an-jenis-pesan');
    sel.disabled = true;
    if (pesan) pesan.textContent = 'menyimpan…';
    try {
      const r = await fetch(
        `/api/tugas/jenis?ds=${encodeURIComponent(sel.dataset.ds)}`
        + `&jenis=${encodeURIComponent(sel.value)}`, { method: 'POST' })
        .then((x) => x.json());
      if (!r || !r.ok) throw new Error((r && r.error) || 'gagal menyimpan');
      location.reload();
    } catch (e) {
      sel.disabled = false;
      if (pesan) pesan.textContent = String(e.message || e);
    }
  };
})();
