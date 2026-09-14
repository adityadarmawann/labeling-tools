/*
 * Rincian job: memilih gambar, lalu memindahkannya ke dataset.
 *
 * Melabeli dan menyatakan selesai sengaja dua tindakan terpisah. Yang pertama
 * dilakukan berkali-kali sambil ragu; yang kedua sekali dan berakibat, karena
 * isi dataset itulah yang nanti di-splitting, diberi versi, dan diekspor.
 *
 * Saringan (tab, kelas) dan paginasi dikerjakan DI SERVER — lihat job.html dan
 * routers/tugas.py. Halaman ini dulu merender seluruh isi job sekali jalan
 * lalu menyaringnya di peramban; pada job 11.409 gambar itu berarti HTML 15,8
 * MB dan sebelas ribu permintaan thumbnail sekaligus. Yang tersisa di sini
 * cuma memilih kartu di halaman yang sedang tampil dan menekan aksinya.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const isi = $('jb-isi');
  if (!isi || !$('jb-kisi')) return;

  const BOLEH = isi.dataset.boleh === '1';
  // Hanya kartu di halaman yang sedang tampil. Tidak ada lagi yang tersembunyi
  // oleh saringan: yang tidak cocok saringan tidak dikirim server sama sekali.
  const ubin = [...document.querySelectorAll('.jb-ubin')];

  // Menu kelas: buka/tutup saja. Isinya form GET yang disubmit tombol
  // "Terapkan" — penyaringannya di server, jadi di sini tidak ada lagi logika
  // sembunyi-tampilkan.
  (() => {
    const menu = $('jb-menu-kelas');
    if (!menu) return;
    const tombol = $('jb-kelas-tombol');
    tombol.onclick = (ev) => { ev.stopPropagation(); menu.toggleAttribute('data-buka'); };
    menu.addEventListener('click', (ev) => ev.stopPropagation());
    document.addEventListener('click', () => menu.removeAttribute('data-buka'));
  })();

  if (!BOLEH) return;

  const terpilih = () => ubin.filter(
    u => u.querySelector('.jb-pilih') && u.querySelector('.jb-pilih').checked);

  function perbarui() {
    const n = terpilih().length;
    $('jb-terpilih').textContent = n ? `${n} dipilih` : '0 dipilih';
    // Tombolnya menyebut angkanya. "Tambahkan ke dataset" tanpa jumlah membuat
    // orang menghitung sendiri centangnya sebelum berani menekan.
    $('jb-masukkan').textContent = n ? `Tambahkan ${n} ke dataset`
                                     : 'Tambahkan ke dataset';
    $('jb-masukkan').disabled = !n;
    $('jb-keluarkan').disabled = !n;
    $('jb-latar').textContent = n ? `Tandai ${n} latar` : 'Tandai latar';
    $('jb-latar').disabled = !n;
    $('jb-batal-latar').disabled = !n;
    $('jb-hapus').textContent = n ? `Hapus ${n} dari projek` : 'Hapus dari projek';
    $('jb-hapus').disabled = !n;
    const semua = $('jb-centang-semua');
    semua.checked = n > 0 && n === ubin.length;
    semua.indeterminate = n > 0 && n < ubin.length;
  }

  for (const u of ubin) {
    const c = u.querySelector('.jb-pilih');
    if (c) c.addEventListener('change', perbarui);
  }

  $('jb-centang-semua').addEventListener('change', (e) => {
    for (const u of ubin) {
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

    pr.selesai(keluarkan
      ? `${j.diunassign} gambar keluar dari dataset dan kembali ke belum ditugaskan`
      : `${j.ditambah} gambar masuk dataset`);
    setTimeout(() => location.reload(), 800);
  }

  /*
   * Hapus dari projek: pindahkan gambar terpilih ke tempat sampah projek.
   *
   * Bisa dipulihkan, bukan hilang — tetapi tetap destruktif: berkasnya pindah
   * dari folder yang dipindai, jadi ia lenyap dari grid, job, dan dataset
   * seketika. Karena itu ditanyakan dulu, dengan angkanya, dan tombolnya
   * berwarna bahaya.
   */
  async function hapus() {
    const dipilih = terpilih();
    if (!dipilih.length) return;
    if (!confirm(
        `Hapus ${dipilih.length} gambar dari projek?\n\n`
        + 'Gambar dan anotasinya dipindah ke tempat sampah projek — bisa '
        + 'dipulihkan dari sana, tetapi hilang dari grid, tugas, dan dataset.')) return;

    $('jb-hapus').disabled = true;
    const pr = Progres.mulai(`Menghapus ${dipilih.length} gambar dari projek`,
                             { di: $('jb-jalur') });
    pr.taktentu('memindahkan ke sampah');
    let j;
    try {
      j = await send('/api/tugas/hapus-gambar', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gambar: dipilih.map(u => u.dataset.path) }),
      });
    } catch (e) { pr.gagal('Gagal menghubungi server'); $('jb-hapus').disabled = false; return; }
    if (!j.ok) { pr.gagal(j.error); $('jb-hapus').disabled = false; return; }

    pr.selesai(`${j.dibuang} gambar dipindah ke sampah`
               + (j.ditolak ? ` · ${j.ditolak} ditolak (bukan tugasmu)` : ''));
    setTimeout(() => location.reload(), 800);
  }

  /*
   * Tandai latar: nyatakan gambar terpilih tidak berisi objek apa pun.
   *
   * Contoh negatif, dan ia ikut terekspor sebagai berkas label kosong. Karena
   * itu ia MENULIS anotasi, dan servernya memeriksa tiap gambar satu per satu
   * dengan penjaga yang sama seperti menyimpan bentuk.
   */
  async function latar(lepas) {
    const dipilih = terpilih();
    if (!dipilih.length) return;

    // Gambar yang sudah berisi objek DITOLAK server, bukan dikosongkan
    // (annotations.mark_background). Dikatakan apa adanya di sini: "akan
    // membuang objeknya" akan membuat orang membatalkan tindakan yang
    // sebenarnya aman, dan diam soal itu membuat mereka mengira gagal.
    const berisi = lepas ? []
      : dipilih.filter(u => u.dataset.label === '1' && u.dataset.bg !== '1');
    if (berisi.length && !confirm(
        `${berisi.length} dari ${dipilih.length} gambar yang dipilih sudah `
        + 'berisi objek, dan itu akan dilewati. Objeknya tidak dihapus.\n\n'
        + `Tandai ${dipilih.length - berisi.length} sisanya sebagai latar?`)) return;

    const tombol = lepas ? $('jb-batal-latar') : $('jb-latar');
    tombol.disabled = true;
    const pr = Progres.mulai(
      lepas ? `Melepas tanda latar dari ${dipilih.length} gambar`
            : `Menandai ${dipilih.length} gambar sebagai latar`,
      { di: $('jb-jalur') });
    pr.taktentu('menyimpan');
    let j;
    try {
      j = await send('/api/latar', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gambar: dipilih.map(u => u.dataset.path),
                               lepas: !!lepas }),
      });
    } catch (e) { pr.gagal('Gagal menghubungi server'); tombol.disabled = false; return; }
    if (!j.ok) { pr.gagal(j.error); tombol.disabled = false; return; }

    // Yang ditolak disebutkan, tidak ditelan. Satu daftar bisa memuat gambar
    // milik pelabel lain, dan diam soal itu membuat orang mengira semuanya
    // berhasil.
    pr.selesai(`${j.n} gambar ${lepas ? 'lepas dari' : 'ditandai'} latar`
               + (j.ditolak ? ` · ${j.ditolak} ditolak (bukan tugasmu)` : ''));
    setTimeout(() => location.reload(), 800);
  }

  $('jb-masukkan').onclick = () => pindahkan(false);
  $('jb-keluarkan').onclick = () => pindahkan(true);
  $('jb-latar').onclick = () => latar(false);
  $('jb-batal-latar').onclick = () => latar(true);
  $('jb-hapus').onclick = hapus;
  perbarui();
})();
