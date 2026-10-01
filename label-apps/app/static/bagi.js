/*
 * Halaman bagi tugas + kelola anggota.
 * =====================================
 * Dua hal di satu halaman: (1) mengelola ANGGOTA projek — mengundang orang,
 * memberi peran (Editor/Labeler) dan scope — dan (2) membagi gambar ke pelabel
 * satu per satu (opsional, alat pemilik). Yang kedua hanya muncul kalau memang
 * ada gambar yang belum ditugaskan; yang pertama selalu ada untuk pemilik,
 * termasuk pada projek yang masih kosong — di situlah Editor diundang untuk
 * mengisinya.
 *
 * Yang dikirim ke server saat membagi daftar jumlahnya, bukan daftar path:
 * server yang memilih dari seluruh yang belum ditugaskan, karena isi projek
 * bisa berubah antara halaman dibuka dan tombolnya ditekan.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const isi = $('bg-isi');
  if (!isi) return;

  const PEMILIK = isi.dataset.pemilik || '';

  // Keadaan peran/scope yang datang dari /api/tugas/calon, dipakai bersama oleh
  // daftar anggota dan kotak undang/atur.
  let batchTersedia = [];
  let akunData = [];                 // j.akun terakhir dari calon
  let edit = null;                   // null = undang baru; {akun,nama} = atur

  // ================================================== BAGI TUGAS (opsional)
  // Hanya ada kalau halaman menggambar ubin "belum ditugaskan".
  const adaBagi = !!$('bg-kisi') && !!$('bg-n');
  const SEMUA = parseInt(isi.dataset.nBelum, 10) || 0;
  let pelabel = '';

  if (adaBagi) {
    const ubin = [...document.querySelectorAll('.bg-ubin')];
    const n = $('bg-n'), slider = $('bg-slider');

    const sorot = () => {
      const k = Math.max(1, Math.min(SEMUA, parseInt(n.value, 10) || 1));
      const acak = $('bg-acak').checked;
      ubin.forEach((el, i) => el.toggleAttribute('data-luar', !acak && i >= k));
      $('bg-acak-ket').textContent = acak
        ? `${k.toLocaleString('id-ID')} gambar diambil acak dari seluruh yang belum ditugaskan.`
        : '';
      perbaruiBagi();
    };
    const setel = (v) => {
      const k = Math.max(1, Math.min(SEMUA, parseInt(v, 10) || 1));
      n.value = k; slider.value = k; sorot();
    };
    $('bg-acak').addEventListener('change', sorot);
    n.addEventListener('input', () => setel(n.value));
    slider.addEventListener('input', () => setel(slider.value));

    $('bg-mulai').onclick = async () => {
      const k = Math.max(1, Math.min(SEMUA, parseInt(n.value, 10) || 1));
      const tombol = $('bg-mulai');
      tombol.disabled = true;
      const pr = Progres.mulai(`Menugaskan ${k} gambar ke ${pelabel}`,
                               { di: $('bg-jalur') });
      pr.taktentu('menyimpan pembagiannya');
      let j;
      try {
        j = await send('/api/tugas/bagi', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pelabel, n: k,
                                 batch: isi.dataset.batch || '',
                                 acak: $('bg-acak').checked,
                                 judul: isi.dataset.batch || '',
                                 catatan: ($('bg-catatan').value || '').trim() }),
        });
      } catch (e) { pr.gagal('Gagal menghubungi server'); tombol.disabled = false; return; }
      if (!j.ok) { pr.gagal(j.error); tombol.disabled = false; return; }
      pr.selesai(`${j.n} gambar jadi tugas ${j.pelabel}`
                 + (j.dilewati ? ` · ${j.dilewati} sudah ditugaskan lebih dulu` : ''));
      setTimeout(() => location.reload(), 900);
    };

    setel(SEMUA);
  }

  function perbaruiBagi() {
    if (!adaBagi) return;
    const k = parseInt($('bg-n').value, 10) || 0;
    const tombol = $('bg-mulai');
    tombol.disabled = !pelabel || !k;
    tombol.textContent = !pelabel ? 'Pilih pelabel dulu'
      : `Tugaskan ${k.toLocaleString('id-ID')} gambar ke ${pelabel}`;
    for (const b of document.querySelectorAll('.bg-orang-baris')) {
      const nEl = b.querySelector('.bg-orang-n');
      if (nEl) nEl.textContent = b.dataset.akun === pelabel ? `${k} gambar` : '';
    }
  }

  // ===================================================== ANGGOTA & PERAN
  // Label peran untuk badge di baris anggota.
  function labelPeran(a) {
    if (a.akun === PEMILIK || a.peran === 'pemilik') return 'Pemilik';
    if (a.peran === 'editor') return 'Editor';
    if (a.peran === 'pelabel') {
      if (a.akses === 'spesifik') {
        const n = (a.batch_scope || []).length;
        return n ? `Labeler · ${n} batch` : 'Labeler · scope kosong';
      }
      if (a.akses === 'semua') return 'Labeler · menyeluruh';
      return 'Labeler · jatah papan';      // warisan (akses belum disetel)
    }
    return 'Anggota';
  }

  async function muatOrang() {
    const wadah = $('bg-orang');
    let j;
    try { j = await (await fetch('/api/tugas/calon')).json(); }
    catch (e) { wadah.innerHTML = '<span class="halus">Gagal memuat daftar akun</span>'; return; }
    if (!j.ok) { wadah.innerHTML = `<span class="halus">${esc(j.error)}</span>`; return; }

    batchTersedia = j.batch_tersedia || [];
    akunData = j.akun || [];
    wadah.replaceChildren();

    // Undangan yang belum dipakai ditampilkan lebih dulu: pekerjaan yang belum
    // selesai, dan tautan yang tak bisa dicabut berlaku selamanya.
    for (const u of (j.undangan || [])) {
      const el = document.createElement('div');
      el.className = 'bg-orang-baris bg-menunggu';
      el.innerHTML =
        '<span class="bg-avatar" aria-hidden="true">@</span>'
        + `<span class="bg-orang-teks"><b>${esc(u.email)}</b>`
        + `<span class="halus">diundang ${esc(u.dibuat || '')}, belum diterima</span></span>`;
      const x = document.createElement('button');
      x.type = 'button'; x.className = 'bg-cabut'; x.textContent = '×';
      x.title = 'Batalkan undangan ini';
      x.onclick = async () => {
        const r = await post('/api/tugas/batalkan-undangan?token='
                             + encodeURIComponent(u.token));
        if (!r.ok) { toast(r.error); return; }
        toast('Undangan dibatalkan'); muatOrang();
      };
      el.appendChild(x);
      wadah.appendChild(el);
    }

    for (const a of j.akun) {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'bg-orang-baris';
      el.dataset.akun = a.akun;
      const badge = (a.anggota || a.akun === j.pemilik)
        ? ` &middot; ${esc(labelPeran(a))}` : '';
      el.innerHTML =
        `<span class="bg-avatar" aria-hidden="true">${esc(a.nama.slice(0, 1).toUpperCase())}</span>`
        + `<span class="bg-orang-teks"><b>${esc(a.nama)}</b>`
        + `<span class="halus">${esc(a.email || a.akun)}${badge}</span></span>`
        + '<span class="bg-orang-n"></span>';

      // Anggota non-pemilik: tombol Atur peran + tombol Keluarkan.
      if (a.anggota && a.akun !== j.pemilik) {
        const atur = document.createElement('button');
        atur.type = 'button'; atur.className = 'bg-atur';
        atur.textContent = 'Atur';
        atur.title = 'Atur peran & akses';
        atur.onclick = (ev) => { ev.stopPropagation(); bukaAtur(a); };
        el.appendChild(atur);

        const x = document.createElement('button');
        x.type = 'button'; x.className = 'bg-cabut'; x.textContent = '×';
        x.title = 'Keluarkan dari projek ini';
        x.onclick = async (ev) => {
          ev.stopPropagation();
          if (!confirm(`Keluarkan ${a.nama} dari projek ini?\n\n`
                       + 'Tugasnya ikut dibubarkan. Label yang sudah dibuat '
                       + 'tetap ada.')) return;
          const r = await post('/api/tugas/keluarkan-anggota?akun='
                               + encodeURIComponent(a.akun));
          if (!r.ok) { toast(r.error); return; }
          toast(`${a.nama} dikeluarkan`);
          if (pelabel === a.akun) pelabel = '';
          muatOrang();
        };
        el.appendChild(x);
      }

      // Memilih baris = memilih pelabel untuk bagi tugas (kalau bagian itu ada).
      el.onclick = () => {
        if (!adaBagi) return;
        pelabel = pelabel === a.akun ? '' : a.akun;
        for (const b of wadah.querySelectorAll('.bg-orang-baris')) {
          b.toggleAttribute('data-on', b.dataset.akun === pelabel);
        }
        perbaruiBagi();
      };
      wadah.appendChild(el);
    }
    perbaruiBagi();
  }

  // ------------------------------------------------- kotak undang / atur
  const segNilai = (id) => {
    const c = $(id).querySelector('input:checked');
    return c ? c.value : '';
  };
  const setSeg = (id, nilai) => {
    const seg = $(id);
    for (const l of seg.querySelectorAll('.seg-opt')) {
      const r = l.querySelector('input');
      r.checked = r.value === nilai;
      l.toggleAttribute('data-on', r.checked);
    }
  };

  function renderScope(terpilih) {
    const box = $('bg-scope-list');
    const pilih = new Set(terpilih || []);
    if (!batchTersedia.length) {
      box.innerHTML = '<span class="halus">Projek ini belum punya batch '
        + 'bernama. Beri nama batch saat mengunggah untuk membatasi scope.</span>';
      return;
    }
    box.replaceChildren();
    for (const b of batchTersedia) {
      const lab = document.createElement('label');
      lab.className = 'bg-centang bg-scope-opt';
      lab.innerHTML = '<input type="checkbox" value="' + esc(b) + '"'
        + (pilih.has(b) ? ' checked' : '') + '><span class="cb"></span>'
        + '<span>' + esc(b) + '</span>';
      box.appendChild(lab);
    }
  }
  const scopeTerpilih = () =>
    [...$('bg-scope-list').querySelectorAll('input:checked')].map(c => c.value);

  function perbaruiFormPeran() {
    const peran = segNilai('bg-peran');
    const akses = segNilai('bg-akses');
    $('bg-labeler-opsi').hidden = peran !== 'pelabel';
    $('bg-scope').hidden = !(peran === 'pelabel' && akses === 'spesifik');
    const ket = $('bg-peran-ket');
    ket.textContent = peran === 'editor'
      ? 'Editor: boleh mengunggah media + melabeli SEMUA. Tak mengelola projek/anggota.'
      : akses === 'spesifik'
        ? 'Labeler spesifik: hanya melabeli batch yang dicentang.'
        : 'Labeler menyeluruh: melabeli semua gambar, tak mengunggah.';
  }

  function resetForm() {
    edit = null;
    $('bg-undang-akun').hidden = false;
    $('bg-undang-nama').hidden = true;
    $('bg-email').value = '';
    setSeg('bg-peran', 'pelabel');
    setSeg('bg-akses', 'semua');
    renderScope([]);
    $('bg-undang-kirim').textContent = 'Undang';
    $('bg-undang-batal').hidden = true;
    $('bg-undang-jalur').replaceChildren();
    perbaruiFormPeran();
  }

  function bukaAtur(a) {
    edit = { akun: a.akun, nama: a.nama };
    $('bg-undang').hidden = false;
    $('bg-undang-akun').hidden = true;
    const nm = $('bg-undang-nama');
    nm.hidden = false;
    nm.textContent = 'Atur peran: ' + a.nama;
    setSeg('bg-peran', a.peran === 'editor' ? 'editor' : 'pelabel');
    setSeg('bg-akses', a.akses === 'spesifik' ? 'spesifik' : 'semua');
    renderScope(a.batch_scope || []);
    $('bg-undang-kirim').textContent = 'Simpan';
    $('bg-undang-batal').hidden = false;
    $('bg-undang-jalur').replaceChildren();
    perbaruiFormPeran();
    $('bg-undang').scrollIntoView({ block: 'nearest' });
  }

  $('bg-tambah-orang').onclick = () => {
    const k = $('bg-undang');
    if (k.hidden) { resetForm(); k.hidden = false; $('bg-email').focus(); }
    else { k.hidden = true; }
  };
  $('bg-undang-batal').onclick = () => { $('bg-undang').hidden = true; resetForm(); };
  $('bg-peran').addEventListener('change', perbaruiFormPeran);
  $('bg-akses').addEventListener('change', perbaruiFormPeran);
  // Jaga data-on seg tetap benar saat radio diklik.
  for (const id of ['bg-peran', 'bg-akses']) {
    $(id).addEventListener('change', (e) => {
      for (const l of $(id).querySelectorAll('.seg-opt')) {
        l.toggleAttribute('data-on', l.contains(e.target));
      }
    });
  }

  $('bg-undang-kirim').onclick = async () => {
    const peran = segNilai('bg-peran');
    const akses = peran === 'pelabel' ? segNilai('bg-akses') : '';
    const batch = akses === 'spesifik' ? scopeTerpilih().join(',') : '';
    const qp = `&peran=${encodeURIComponent(peran)}`
      + `&akses=${encodeURIComponent(akses)}`
      + `&batch=${encodeURIComponent(batch)}`;
    const jalur = $('bg-undang-jalur');

    // Mode ATUR: anggota yang sudah ada.
    if (edit) {
      const pr = Progres.mulai('Menyimpan peran ' + edit.nama, { di: jalur });
      pr.taktentu('menyimpan');
      let j;
      try {
        j = await post('/api/tugas/atur-anggota?akun='
                       + encodeURIComponent(edit.akun) + qp);
      } catch (e) { pr.gagal('Gagal menghubungi server'); return; }
      if (!j.ok) { pr.gagal(j.error); return; }
      pr.selesai('Peran disimpan');
      $('bg-undang').hidden = true; resetForm(); muatOrang();
      return;
    }

    // Mode UNDANG: akun/email baru.
    const v = ($('bg-email').value || '').trim();
    if (!v) { toast('Isi akun atau emailnya dulu'); return; }
    const pr = Progres.mulai('Mengundang ' + v, { di: jalur });
    pr.taktentu('menyiapkan undangan');
    const url = (v.includes('@')
      ? '/api/tugas/undang-email?email=' + encodeURIComponent(v)
      : '/api/tugas/undang?akun=' + encodeURIComponent(v)) + qp;
    let j;
    try { j = await post(url); } catch (e) { pr.gagal('Gagal menghubungi server'); return; }
    if (!j.ok) { pr.gagal(j.error); return; }

    if (j.tautan) {
      pr.selesai('Undangan dibuat');
      jalur.insertAdjacentHTML('beforeend',
        '<div class="bg-tautan"><span class="mono" id="bg-tautan-teks"></span>'
        + '<button class="chip" type="button" id="bg-salin">Salin</button></div>');
      $('bg-tautan-teks').textContent = j.tautan;
      $('bg-salin').onclick = async () => {
        if (await salinTeks(j.tautan, $('bg-tautan-teks'))) toast('Tautan disalin');
        else toast('Tautannya sudah disorot, tekan Ctrl+C untuk menyalin');
      };
    } else {
      pr.selesai(`${j.akun || v} jadi anggota`);
      $('bg-undang').hidden = true; resetForm(); muatOrang();
    }
  };

  resetForm();
  $('bg-undang').hidden = true;
  muatOrang();
})();
