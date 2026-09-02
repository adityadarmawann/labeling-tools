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
  // Kelas yang dicentang. Kosong berarti tidak menyaring sama sekali —
  // bukan "tidak ada yang cocok".
  let kelasPilih = new Set();
  let latarPilih = false;

  // Daftar kelas tiap ubin dibaca sekali. JSON, bukan teks berpemisah: nama
  // kelas boleh berisi apa saja, dan pemisah karakter apa pun cepat atau
  // lambat muncul di dalam salah satu nama.
  for (const u of ubin) {
    try { u._kelas = new Set(JSON.parse(u.dataset.kelas || '[]')); }
    catch (e) { u._kelas = new Set(); }
  }

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
    // Tombol latar juga menyebut angkanya, dengan alasan yang sama.
    $('jb-latar').textContent = n ? `Tandai ${n} latar` : 'Tandai latar';
    $('jb-latar').disabled = !n;
    $('jb-batal-latar').disabled = !n;
    const semua = $('jb-centang-semua');
    const t = terlihat().length;
    semua.checked = n > 0 && n === t;
    semua.indeterminate = n > 0 && n < t;
  }

  function saringUlang() {
    for (const u of ubin) {
      const label = u.dataset.label === '1';
      const bg = u.dataset.bg === '1';
      // Latar TERMASUK "sudah dianotasi": menandai gambar tanpa objek adalah
      // keputusan yang sudah diambil, bukan pekerjaan yang belum dikerjakan.
      // Yang memisahkannya kelas, dan itu urusan saringan di sebelahnya.
      let tampak = saring === 'belum' ? !label
                 : saring === 'sudah' ? label : true;
      // Saringan kelas bersifat "punya salah satu", sama seperti di grid:
      // "botol atau latar" adalah satu pertanyaan, dan menuntut keduanya
      // sekaligus hampir tidak pernah yang dimaksud.
      if (tampak && (kelasPilih.size || latarPilih)) {
        let cocok = latarPilih && bg;
        if (!cocok) for (const k of kelasPilih) { if (u._kelas.has(k)) { cocok = true; break; } }
        tampak = cocok;
      }
      u.hidden = !tampak;
      // Yang tersembunyi ikut dilepas centangnya: mengirim gambar yang tidak
      // terlihat lagi adalah kejutan, bukan kemudahan.
      const c = u.querySelector('.jb-pilih');
      if (u.hidden && c) c.checked = false;
    }
    $('jb-kosong').hidden = terlihat().length > 0;
    perbarui();
  }

  // ---- saringan kelas
  (() => {
    const menu = $('jb-menu-kelas');
    if (!menu) return;
    const tombol = $('jb-kelas-tombol');
    const centang = [...menu.querySelectorAll('input[type=checkbox]')];

    function judul() {
      const n = kelasPilih.size + (latarPilih ? 1 : 0);
      // Tombolnya menyebut pilihannya sendiri. "Semua kelas" yang tidak
      // berubah padahal saringannya aktif membuat daftar yang menyusut
      // terbaca seperti gambar yang hilang.
      tombol.textContent = (n === 0 ? 'Semua kelas'
        : n === 1 ? (latarPilih ? 'Latar' : [...kelasPilih][0])
        : `${n} kelas`) + ' \u25be';
      tombol.toggleAttribute('data-on', n > 0);
    }

    function baca() {
      kelasPilih = new Set();
      latarPilih = false;
      for (const c of centang) {
        if (!c.checked) continue;
        if (c.dataset.latar === '1') latarPilih = true;
        else kelasPilih.add(c.dataset.kelas);
      }
      judul();
      saringUlang();
    }

    tombol.onclick = (ev) => { ev.stopPropagation(); menu.toggleAttribute('data-buka'); };
    menu.addEventListener('click', (ev) => ev.stopPropagation());
    document.addEventListener('click', () => menu.removeAttribute('data-buka'));
    centang.forEach(c => c.addEventListener('change', baca));
    $('jb-kelas-bersih').onclick = () => {
      centang.forEach(c => { c.checked = false; });
      baca();
    };
    judul();
  })();

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
        + 'berisi objek, dan itu akan dilewati — objeknya tidak dihapus.\n\n'
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
               + (j.ditolak ? ` \u00b7 ${j.ditolak} ditolak (bukan tugasmu)` : ''));
    setTimeout(() => location.reload(), 800);
  }

  $('jb-masukkan').onclick = () => pindahkan(false);
  $('jb-keluarkan').onclick = () => pindahkan(true);
  $('jb-latar').onclick = () => latar(false);
  $('jb-batal-latar').onclick = () => latar(true);
  saringUlang();
})();
