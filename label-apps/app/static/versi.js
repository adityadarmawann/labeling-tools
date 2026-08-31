/* Halaman versi: membekukan pembagian, dan membuang yang sudah tidak dipakai. */
(() => {
  const $ = (id) => document.getElementById(id);
  if (!$('vs-isi')) return;

  const buat = $('vs-buat');
  if (buat) {
    buat.onclick = async () => {
      buat.disabled = true;
      const pr = Progres.mulai('Membekukan pembagian', { di: $('vs-jalur') });
      pr.taktentu('menghitung dan menyimpan');
      let j;
      try {
        j = await post('/api/versi/buat?split=' + encodeURIComponent($('vs-rasio').value)
                       + '&catatan=' + encodeURIComponent($('vs-catatan').value));
      } catch (e) { pr.gagal('Gagal menghubungi server'); buat.disabled = false; return; }
      if (!j.ok) { pr.gagal(j.error); buat.disabled = false; return; }
      // Versi yang dibuat TANPA rencana anti-bocor disebutkan apa adanya:
      // isinya sah, tapi pembagiannya cuma menebak dari nama berkas.
      pr.selesai(`v${j.nomor} dibuat, ${j.n} gambar`
                 + (j.berencana ? '' : ' · tanpa pemeriksaan isi gambar'));
      setTimeout(() => location.reload(), 900);
    };
  }

  for (const b of document.querySelectorAll('.vs-kartu .an-buang')) {
    b.onclick = async () => {
      const n = b.dataset.nomor;
      // Yang hilang catatan pembagiannya, bukan gambarnya. Dikatakan, karena
      // "yakin?" tanpa akibat yang jelas membuat orang menebak.
      if (!confirm(`Hapus versi v${n}?\n\n`
                   + 'Gambar dan labelnya tidak tersentuh. Yang hilang catatan '
                   + 'bahwa pembagian itu pernah ada, dan ia tidak bisa '
                   + 'diunduh ulang.')) return;
      const j = await post('/api/versi/hapus?nomor=' + encodeURIComponent(n));
      if (!j.ok) { toast(j.error); return; }
      toast(`v${n} dihapus`);
      location.reload();
    };
  }
})();
