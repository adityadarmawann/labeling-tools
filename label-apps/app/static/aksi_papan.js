/*
 * Papan anotasi KLIP: menu kelola per kartu job.
 *
 * Padanan anotasi.js untuk klip. Kelola job (tugaskan ulang, catatan, judul,
 * bubarkan) memakai ulang rute /api/tugas/* apa adanya — job klip & job gambar
 * berbagi mesin yang sama di services/tugas, kuncinya cuma kebetulan berupa
 * `klip/<batch>/<file>`. Tak ada blok "format anotasi" (itu khas gambar) dan
 * tak ada tombol dataset-borongan di sini: memasukkan klip ke dataset dilakukan
 * di grid (Label Aksi) yang memang memutar & menyaringnya.
 *
 * prompt()/confirm() sengaja dipakai: tiap tindakan menanyakan satu nilai saja.
 */
(() => {
  const isi = document.getElementById('an-isi');
  if (!isi) return;

  // ---- buka/tutup menu tiga-titik
  for (const b of document.querySelectorAll('.an-titik')) {
    b.onclick = (ev) => {
      ev.stopPropagation();
      const m = b.closest('.an-menu');
      const buka = m.hasAttribute('data-buka');
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

  async function kirim(url, pesan) {
    const j = await post(url);
    if (!j.ok) { toast(j.error); return false; }
    toast(pesan);
    location.reload();
    return true;
  }

  // ---- Masukkan klip berlabel yang belum ditugaskan ke dataset (per batch /
  //      semua) — padanan an-siap papan gambar, untuk klip.
  const EDS = encodeURIComponent(isi.dataset.ds || '');
  for (const b of document.querySelectorAll('.an-siap[data-siap]')) {
    b.onclick = async () => {
      b.disabled = true;
      const r = await send(`/api/aksi/dataset-siap?ds=${EDS}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch: b.dataset.siap || '' }),
      });
      if (!r || !r.ok) { toast((r && r.error) || 'gagal'); b.disabled = false; return; }
      toast(`${r.ditambah || 0} klip masuk dataset`);
      location.reload();
    };
  }

  // ---- Hapus unggahan (batch) yang belum ditugaskan, ke keranjang sampah klip
  for (const b of document.querySelectorAll('.an-hapus-batch[data-hapus-batch]')) {
    b.onclick = async () => {
      const batch = b.dataset.hapusBatch || '';
      const nama = b.dataset.nama || 'unggahan ini';
      const n = b.dataset.n || '';
      if (!confirm(`Hapus unggahan "${nama}"${n ? ` (${n} klip)` : ''}?\n\n`
        + 'Klip dipindah ke keranjang sampah klip (bisa dipulihkan). Klip yang '
        + 'ditugaskan ke orang LAIN atau sudah masuk dataset dilindungi (tidak '
        + 'ikut terhapus).')) return;
      const r = await send(`/api/aksi/hapus-batch?ds=${EDS}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch }),
      });
      if (!r || !r.ok) { toast((r && r.error) || 'gagal'); return; }
      toast(`${r.dibuang || 0} klip dipindah ke sampah`
            + (r.dilindungi ? ` · ${r.dilindungi} dilindungi (orang lain/dataset)` : ''));
      location.reload();
    };
  }

  // ---- tindakan per job
  for (const b of document.querySelectorAll('.menu-aksi[data-aksi]')) {
    b.onclick = async () => {
      const id = b.dataset.id;
      const kartu = b.closest('.an-kartu');
      const q = (s) => '/api/tugas/ubah?id=' + encodeURIComponent(id) + s;

      if (b.dataset.aksi === 'pelabel') {
        const kini = kartu.dataset.pelabel || '';
        const v = prompt('Tugaskan ulang ke siapa?\n\n'
                         + 'Isi nama akunnya. Klip dan label yang sudah ada '
                         + 'tidak berubah; yang berpindah hanya siapa yang '
                         + 'boleh menyuntingnya.', kini);
        if (!v || v.trim() === kini) return;
        return kirim(q('&pelabel=' + encodeURIComponent(v.trim())),
                     'Tugas dipindahkan ke ' + v.trim());
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
        if (!confirm(`Bubarkan tugas ${siapa}?\n\n`
                     + 'Label yang sudah dibuat tetap ada. Klipnya kembali ke '
                     + 'kolom "Belum ditugaskan" dan bisa dibagikan lagi.')) return;
        return kirim('/api/tugas/bubarkan?id=' + encodeURIComponent(id),
                     'Tugas dibubarkan');
      }
    };
  }
})();
