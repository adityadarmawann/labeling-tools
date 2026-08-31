/* Penyunting tag di halaman Lihat.
   Satu gambar sekali sunting; menandai banyak gambar sekaligus dilakukan saat
   mengunggah, tempat pilihannya memang sedang di tangan. */
(() => {
  const wadah = document.getElementById('lh-tag');
  if (!wadah || wadah.dataset.boleh !== '1') return;
  const path = wadah.dataset.path;
  const isian = document.getElementById('lh-tag-baru');

  async function kirim(tambah, buang) {
    const j = await send('/api/tag/pasang', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths: [path], tambah, buang }),
    });
    if (!j.ok) { toast(j.error); return false; }
    return true;
  }

  function pasangBuang(el) {
    const b = el.querySelector('.tg-buang');
    if (!b) return;
    b.onclick = async () => {
      b.disabled = true;
      if (!await kirim([], [b.dataset.tag])) { b.disabled = false; return; }
      el.remove();
      if (!wadah.querySelector('.tg-pil')) {
        // Kotak yang mendadak kosong tanpa kalimat apa pun terbaca seperti
        // bagian yang gagal dimuat.
        wadah.insertAdjacentHTML('beforeend',
          '<span class="halus" id="lh-tag-kosong">Belum ada tag.</span>');
      }
    };
  }
  wadah.querySelectorAll('.tg-pil').forEach(pasangBuang);

  isian.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const v = (isian.value || '').trim();
    if (!v) return;
    if ([...wadah.querySelectorAll('.tg-pil')].some(x => x.dataset.tag === v)) {
      toast('Tag itu sudah ada'); isian.value = ''; return;
    }
    isian.disabled = true;
    const ok = await kirim([v], []);
    isian.disabled = false;
    isian.focus();
    if (!ok) return;
    isian.value = '';
    const kosong = document.getElementById('lh-tag-kosong');
    if (kosong) kosong.remove();
    const el = document.createElement('span');
    el.className = 'tg-pil tg-sunting';
    el.dataset.tag = v;
    el.innerHTML = `${v}<button type="button" class="tg-buang" data-tag="${v}"
                     aria-label="Hapus tag ${v}">&times;</button>`;
    wadah.appendChild(el);
    pasangBuang(el);
  });
})();
