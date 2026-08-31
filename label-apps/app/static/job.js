/*
 * Rincian job: memilih gambar, lalu memindahkannya ke dataset.
 *
 * Melabeli dan menyatakan selesai sengaja dua tindakan terpisah. Yang pertama
 * dilakukan berkali-kali sambil ragu; yang kedua sekali dan berakibat, karena
 * isi dataset itulah yang nanti di-splitting, diberi versi, dan diekspor.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const isi = $('jb-isi');
  if (!isi || !$('jb-kisi')) return;

  const DS = isi.dataset.ds || '';
  const BOLEH = isi.dataset.boleh === '1';
  const ubin = [...document.querySelectorAll('.jb-ubin')];
  let saring = 'semua';

  const terlihat = () => ubin.filter(u => !u.hidden);
  const terpilih = () => ubin.filter(
    u => !u.hidden && u.querySelector('.jb-pilih') && u.querySelector('.jb-pilih').checked);

  function perbarui() {
    if (!BOLEH) return;
    const n = terpilih().length;
    $('jb-terpilih').textContent = n ? `${n} dipilih` : '0 dipilih';
    // Tombolnya menyebut angkanya. "Tambahkan ke dataset" tanpa jumlah membuat
    // orang menghitung sendiri centangnya sebelum berani menekan.
    $('jb-masukkan').textContent = n ? `Tambahkan ${n} ke dataset`
                                     : 'Tambahkan ke dataset';
    $('jb-masukkan').disabled = !n;
    $('jb-keluarkan').disabled = !n;
    const semua = $('jb-centang-semua');
    const t = terlihat().length;
    semua.checked = n > 0 && n === t;
    semua.indeterminate = n > 0 && n < t;
  }

  function saringUlang() {
    for (const u of ubin) {
      const label = u.dataset.label === '1';
      u.hidden = saring === 'belum' ? label
               : saring === 'sudah' ? !label : false;
      // Yang tersembunyi ikut dilepas centangnya: mengirim gambar yang tidak
      // terlihat lagi adalah kejutan, bukan kemudahan.
      const c = u.querySelector('.jb-pilih');
      if (u.hidden && c) c.checked = false;
    }
    $('jb-kosong').hidden = terlihat().length > 0;
    perbarui();
  }

  $('jb-tab').addEventListener('change', (e) => {
    saring = e.target.value;
    for (const l of $('jb-tab').querySelectorAll('.seg-opt')) {
      l.toggleAttribute('data-on', l.contains(e.target));
    }
    saringUlang();
  });

  if (!BOLEH) return;

  for (const u of ubin) {
    const c = u.querySelector('.jb-pilih');
    if (c) c.addEventListener('change', perbarui);
  }

  $('jb-centang-semua').addEventListener('change', (e) => {
    for (const u of terlihat()) {
      const c = u.querySelector('.jb-pilih');
      if (c) c.checked = e.target.checked;
    }
    perbarui();
  });

  async function pindahkan(keluarkan) {
    const dipilih = terpilih();
    if (!dipilih.length) return;

    // Memindahkan gambar yang BELUM dianotasi ke dataset hampir selalu tidak
    // disengaja: yang dimaksud biasanya "semua yang sudah selesai". Ditanyakan,
    // bukan ditolak, karena gambar latar yang memang tanpa objek juga sah.
    const polos = keluarkan ? [] : dipilih.filter(u => u.dataset.label !== '1');
    if (polos.length && !confirm(
        `${polos.length} dari ${dipilih.length} gambar yang dipilih belum `
        + 'dianotasi sama sekali.\n\nTetap masukkan ke dataset?')) return;

    const tombol = keluarkan ? $('jb-keluarkan') : $('jb-masukkan');
    tombol.disabled = true;
    const pr = Progres.mulai(
      keluarkan ? `Mengeluarkan ${dipilih.length} gambar dari dataset`
                : `Memasukkan ${dipilih.length} gambar ke dataset`,
      { di: $('jb-jalur') });
    pr.taktentu('menyimpan');
    let j;
    try {
      j = await send('/api/tugas/dataset', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gambar: dipilih.map(u => u.dataset.path),
                               keluarkan: !!keluarkan }),
      });
    } catch (e) { pr.gagal('Gagal menghubungi server'); tombol.disabled = false; return; }
    if (!j.ok) { pr.gagal(j.error); tombol.disabled = false; return; }

    pr.selesai(keluarkan ? `${j.dikeluarkan} gambar keluar dari dataset`
                         : `${j.ditambah} gambar masuk dataset`);
    setTimeout(() => location.reload(), 800);
  }

  $('jb-masukkan').onclick = () => pindahkan(false);
  $('jb-keluarkan').onclick = () => pindahkan(true);
  saringUlang();
})();
