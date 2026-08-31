/*
 * Papan anotasi: menu kelola per kartu.
 *
 * Tindakannya memakai prompt() dan confirm(), bukan dialog sendiri, karena
 * ketiganya menanyakan satu nilai saja dan dialog buatan untuk satu kotak isian
 * cuma menambah kode yang harus dijaga tanpa menambah apa pun yang terlihat.
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
        const kini = kartu.dataset.pelabel || '';
        const v = prompt('Tugaskan ulang ke siapa?\n\n'
                         + 'Isi nama akunnya. Gambar dan label yang sudah ada '
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
})();
