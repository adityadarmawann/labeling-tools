/* Papan anotasi: satu tindakan saja di sini, membubarkan tugas. */
(() => {
  const isi = document.getElementById('an-isi');
  if (!isi) return;

  for (const b of document.querySelectorAll('.an-buang')) {
    b.onclick = async () => {
      const kartu = b.closest('.an-kartu');
      const siapa = kartu.querySelector('.an-kartu-judul').textContent.trim();
      // Membubarkan TIDAK menghapus label yang sudah dibuat; yang hilang cuma
      // penugasannya. Dikatakan di pertanyaannya, karena kalimat "yakin?"
      // tanpa akibat yang jelas membuat orang menebak.
      if (!confirm(`Bubarkan tugas ${siapa}?\n\n`
                   + 'Label yang sudah dibuat tetap ada. Gambarnya kembali ke '
                   + 'kolom "Belum ditugaskan" dan bisa dibagikan lagi.')) return;
      b.disabled = true;
      const j = await post('/api/tugas/bubarkan?id=' + encodeURIComponent(b.dataset.id));
      if (!j.ok) { toast(j.error); b.disabled = false; return; }
      toast('Tugas dibubarkan');
      location.reload();
    };
  }
})();
