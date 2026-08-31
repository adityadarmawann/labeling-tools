/*
 * Halaman unggah: tiga tahap, dan berkasnya baru terbang di tahap ketiga.
 * =====================================================================
 * Tahap 1 memilih, tahap 2 memeriksa, tahap 3 mengirim. Yang membedakannya
 * dari alur lama bukan tampilannya melainkan kapan berkasnya terkirim: dulu
 * memilih folder berarti sudah mengirim, sehingga satu salah pilih folder
 * berarti menunggu seluruh unggahan selesai lalu membereskan sisanya di
 * server. Di sini berkasnya ditahan di peramban sampai ada yang menekan
 * "Simpan dan lanjutkan".
 *
 * Thumbnail dibuat MALAS. Satu objectURL per gambar untuk sepuluh ribu gambar
 * berarti sepuluh ribu bitmap terdekode hidup sekaligus, dan tabnya mati
 * sebelum sempat menampilkan apa pun. Yang dibuat hanya yang terlihat, dan
 * dilepas begitu keluar layar.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const halaman = $('ug-halaman');
  if (!halaman) return;

  const PROJEK = halaman.dataset.projek || '';
  const PATH = halaman.dataset.path || '';
  /* Projek yang SUDAH berisi gambar dikirimi lewat /tambah, yang menaruh tiap
     berkas mengikuti tata letak di sana: dataset bersplit tetap terbagi
     train/valid/test, dataset YOLO tetap punya images/ dan labels/. Projek
     kosong lewat /upload, yang menerima apa saja termasuk .zip lalu dipindai
     dari nol. Yang menentukan keadaan projeknya, bukan pilihan pengguna. */
  const BERISI = halaman.dataset.berisi === '1';

  // Nama-nama ini harus sama dengan UP_EXT di app.js dan dengan
  // IMG_EXT + ANN_EXT + META_EXT + ARSIP_EXT di app/config.py.
  const GAMBAR_EXT = ['.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff'];
  const adalahGambar = (n) => GAMBAR_EXT.some(e => n.toLowerCase().endsWith(e));
  const stem = (n) => {
    const dasar = n.split('/').pop();
    const t = dasar.lastIndexOf('.');
    return (t > 0 ? dasar.slice(0, t) : dasar).toLowerCase();
  };

  /* Satu daftar untuk semua yang dipilih, dikunci nama relatifnya supaya
     memilih folder yang sama dua kali tidak menggandakan isinya. */
  const berkas = new Map();        // nama -> {file, nama, gambar, arsip}
  const tag = [];
  let batal = false;

  // ------------------------------------------------------------- tahap
  function keTahap(n) {
    for (const i of [1, 2, 3]) {
      const s = $('ug-tahap' + i);
      if (s) s.hidden = i !== n;
      const l = $('ug-langkah-' + i);
      if (!l) continue;
      l.toggleAttribute('data-on', i === n);
      l.toggleAttribute('data-selesai', i < n);
    }
    // Nama batch dan tag ikut tahap 1 dan 2, tapi tidak lagi bisa diubah
    // setelah pengirimannya mulai.
    const isian = document.querySelector('.ug-isian');
    if (isian) isian.hidden = n === 3;
  }

  // ------------------------------------------------------------- staging
  function anotasiStem() {
    const s = new Set();
    for (const b of berkas.values()) {
      const n = b.nama.toLowerCase();
      if (n.endsWith('.json') || (n.endsWith('.txt') && !n.endsWith('classes.txt'))) {
        s.add(stem(b.nama));
      }
    }
    return s;
  }

  function gambarTerdaftar() {
    const ann = anotasiStem();
    return [...berkas.values()]
      .filter(b => b.gambar)
      .map(b => ({ ...b, punyaAnotasi: ann.has(stem(b.nama)) }));
  }

  function tambah(daftarFile) {
    let baru = 0, ditolak = 0;
    let arsipDitolak = 0;
    for (const f of daftarFile) {
      const nama = namaKirim(f);
      if (!UP_EXT.some(e => nama.toLowerCase().endsWith(e))) { ditolak++; continue; }
      // Membongkar arsip ke dalam dataset yang sudah terbagi akan menumpahkan
      // isinya di akar, di luar train/valid/test. Ditolak di sini, bukan
      // dibiarkan gagal belakangan setelah berkasnya terlanjur naik.
      if (BERISI && adalahArsip(nama)) { arsipDitolak++; continue; }
      if (berkas.has(nama)) continue;
      berkas.set(nama, { file: f, nama, gambar: adalahGambar(nama),
                         arsip: adalahArsip(nama) });
      baru++;
    }
    if (!berkas.size) {
      toast('Tidak ada gambar, anotasi, data.yaml, atau .zip di pilihan itu');
      return;
    }
    if (ditolak) toast(ditolak + ' berkas dilewati karena formatnya tidak didukung');
    if (arsipDitolak) {
      toast(arsipDitolak + ' berkas .zip dilewati: dataset ini sudah berisi, '
            + 'bongkar dulu di laptop lalu tarik isinya');
    }
    if (baru) gambarUlang();
    keTahap(2);
  }

  // ------------------------------------------------------------- thumbnail
  /* Digambar saat ubinnya masuk layar, dilepas saat keluar. rootMargin memberi
     satu layar tambahan di atas dan bawah supaya gambarnya sudah siap saat
     benar-benar terlihat, bukan muncul belakangan sambil menggulir.

     Dipakai createImageBitmap dengan resizeWidth, BUKAN <img src=blob>. Foto
     di dataset ini 2296x4080: sebuah <img> mendekodenya utuh, 37 MB piksel per
     gambar, dan satu layar berisi 14 ubin sudah menghabiskan setengah giga.
     Terukur: dengan <img>, mengambil tangkapan layar halaman ini membuat
     Chrome gagal dengan "Internal error". Dikecilkan saat mendekode, yang
     tersimpan cuma sebesar ubinnya. */
  const SISI_UBIN = 240;

  /* Batas waktu per gambar.
     Foto 2296x4080 di dataset ini pernah membuat createImageBitmap tidak
     pernah menjawab sama sekali di satu lingkungan tanpa GPU. Tanpa batas
     waktu, antrean berhenti di gambar itu dan SELURUH sisa ubin tidak pernah
     digambar. Yang hilang kalau batasnya kena cuma thumbnailnya; berkasnya
     tetap ikut terunggah. */
  const BATAS_LUKIS_MS = 8000;

  const berbatas = (janji) => Promise.race([
    janji,
    new Promise((_, tolak) => setTimeout(() => tolak(new Error('lewat waktu')),
                                         BATAS_LUKIS_MS)),
  ]);

  async function lukis(kanvas, file) {
    let bmp;
    try {
      // resizeWidth membuat pengecilan terjadi DI DALAM pendekodean.
      bmp = await berbatas(createImageBitmap(file, {
        resizeWidth: SISI_UBIN, resizeQuality: 'medium' }));
    } catch (e) {
      try {
        // Peramban tanpa opsi resize: masih jauh lebih baik daripada <img>,
        // karena bitmapnya dilepas begitu selesai digambar.
        bmp = await berbatas(createImageBitmap(file));
      } catch (e2) {
        kanvas.closest('.ug-ubin').dataset.tanpaGambar = '1';
        return;
      }
    }
    const k = Math.min(SISI_UBIN / bmp.width, SISI_UBIN / bmp.height, 1);
    kanvas.width = Math.max(1, Math.round(bmp.width * k));
    kanvas.height = Math.max(1, Math.round(bmp.height * k));
    kanvas.getContext('2d').drawImage(bmp, 0, 0, kanvas.width, kanvas.height);
    bmp.close();
    kanvas.dataset.terlukis = '1';
  }

  /* Antrean, bukan semuanya sekaligus.
     Mendekode satu foto 2296x4080 makan ratusan milidetik, dan satu layar
     berisi 14 ubin. Dilepas bersamaan, keempat belasnya berebut thread yang
     sama dan tabnya membeku beberapa detik: tombol tidak menjawab, guliran
     tersendat. Terukur di uji e2e, halaman berhenti membalas selama lebih
     dari 20 detik. Tiga sekaligus sudah cukup mengisi layar tanpa terasa. */
  const SEKALIGUS = 3;
  const antre = [];
  let jalan = 0;

  function pompa() {
    while (jalan < SEKALIGUS && antre.length) {
      const { kanvas, file } = antre.shift();
      // Ubin yang keluar layar selagi mengantre dilewati: menggulir cepat
      // melewati seribu ubin tidak boleh meninggalkan seribu pekerjaan yang
      // hasilnya tidak akan dilihat siapa pun.
      if (kanvas.dataset.antre !== '1') continue;
      delete kanvas.dataset.antre;
      jalan++;
      lukis(kanvas, file).finally(() => { jalan--; pompa(); });
    }
  }

  const pengamat = new IntersectionObserver((entri) => {
    for (const e of entri) {
      const kanvas = e.target.querySelector('canvas');
      const b = berkas.get(e.target.dataset.nama);
      if (!kanvas || !b) continue;
      if (e.isIntersecting) {
        if (!kanvas.dataset.terlukis && !kanvas.dataset.antre) {
          kanvas.dataset.antre = '1';
          antre.push({ kanvas, file: b.file });
          pompa();
        }
      } else if (kanvas.dataset.antre) {
        delete kanvas.dataset.antre;
      } else if (kanvas.dataset.terlukis) {
        // Mengubah lebar kanvas melepas buffernya. Tanpa ini, menggulir
        // sepuluh ribu ubin berarti sepuluh ribu kanvas terisi yang tidak
        // pernah dilihat lagi.
        kanvas.width = kanvas.width;
        delete kanvas.dataset.terlukis;
      }
    }
  }, { root: null, rootMargin: '400px 0px' });

  // ------------------------------------------------------------- kisi
  // Batas ubin yang digambar sekaligus. Di atas ini daftarnya dipotong dan
  // sisanya cukup dihitung: dua puluh ribu <figure> membuat halaman berat
  // padahal tidak ada yang memeriksa gambar ke-6.000 satu per satu.
  const MAKS_UBIN = 600;
  let saring = 'semua';

  function gambarUlang() {
    const semua = gambarTerdaftar();
    const berlabel = semua.filter(g => g.punyaAnotasi);
    $('ug-n-semua').textContent = semua.length;
    $('ug-n-anotasi').textContent = berlabel.length;
    $('ug-n-tanpa').textContent = semua.length - berlabel.length;

    const tampil = saring === 'anotasi' ? berlabel
                 : saring === 'tanpa' ? semua.filter(g => !g.punyaAnotasi)
                 : semua;

    const kisi = $('ug-kisi');
    pengamat.disconnect();
    kisi.replaceChildren();
    $('ug-kosong').hidden = tampil.length > 0;

    const tpl = $('ug-ubin-tpl');
    for (const g of tampil.slice(0, MAKS_UBIN)) {
      const el = tpl.content.firstElementChild.cloneNode(true);
      el.dataset.nama = g.nama;
      el.dataset.anotasi = g.punyaAnotasi ? 'ya' : 'tidak';
      el.querySelector('.ug-tanda').hidden = !g.punyaAnotasi;
      const cap = el.querySelector('.ug-nama');
      cap.textContent = g.nama.split('/').pop();
      cap.title = g.nama;
      el.querySelector('.ug-buang').onclick = () => buang(g.nama);
      kisi.appendChild(el);
      pengamat.observe(el);
    }
    if (tampil.length > MAKS_UBIN) {
      const sisa = document.createElement('div');
      sisa.className = 'ug-lebih';
      sisa.textContent = (tampil.length - MAKS_UBIN).toLocaleString('id-ID')
        + ' gambar lagi tidak digambar di sini. Semuanya tetap ikut diunggah.';
      kisi.appendChild(sisa);
    }

    const arsip = [...berkas.values()].filter(b => b.arsip).length;
    const ket = document.querySelector('.ug-bilah-ket b');
    if (ket) {
      ket.textContent = arsip
        ? `${semua.length} gambar dan ${arsip} arsip .zip siap diunggah`
        : `${semua.length} gambar siap diunggah`;
    }
  }

  function buang(nama) {
    const b = berkas.get(nama);
    berkas.delete(nama);
    // Anotasinya ikut dibuang. Ditinggal, ia terkirim sebagai label yatim
    // yang menunjuk gambar yang tidak pernah ada di sana.
    if (b && b.gambar) {
      const s = stem(nama);
      for (const [k, v] of [...berkas]) {
        if (!v.gambar && !v.arsip && stem(k) === s) berkas.delete(k);
      }
    }
    if (!berkas.size) { keTahap(1); return; }
    gambarUlang();
  }

  // ------------------------------------------------------------- tag
  function gambarTag() {
    const kotak = $('ug-tag-kotak');
    kotak.querySelectorAll('.ug-tag').forEach(e => e.remove());
    const input = $('ug-tag-input');
    for (const t of tag) {
      const el = document.createElement('span');
      el.className = 'ug-tag';
      el.textContent = t;
      const x = document.createElement('button');
      x.type = 'button';
      x.setAttribute('aria-label', 'Hapus tag ' + t);
      x.textContent = '×';
      x.onclick = () => { tag.splice(tag.indexOf(t), 1); gambarTag(); };
      el.appendChild(x);
      kotak.insertBefore(el, input);
    }
  }

  function tambahTag(teks) {
    for (const bagian of String(teks).split(',')) {
      const t = bagian.trim().slice(0, 40);
      if (t && !tag.includes(t) && tag.length < 20) tag.push(t);
    }
    gambarTag();
  }

  // ------------------------------------------------------------- kirim
  async function kirim() {
    const semua = [...berkas.values()];
    const namaBatch = ($('ug-nama-batch').value || '').trim();
    batal = false;
    keTahap(3);
    $('ug-unggah-nama').textContent = namaBatch || PROJEK;
    $('ug-lanjut').hidden = true;

    // Progres.mulai menghapus isi wadahnya, jadi tidak boleh ada rujukan ke
    // bilah di dalamnya yang disimpan lebih dulu.
    const pr = Progres.mulai('Mengunggah ke ' + PROJEK, { di: $('ug-progres-jalur') });

    // /tambah bekerja pada dataset yang SEDANG dibuka sesi ini, jadi projeknya
    // harus dibuka lebih dulu. Dilakukan sekali di sini, bukan per berkas.
    if (BERISI) {
      pr.taktentu('Membuka dataset…');
      const buka = await post('/setsrc?path=' + encodeURIComponent(PATH));
      if (!buka.ok) { pr.gagal(buka.error || 'Gagal membuka dataset'); return; }
    }

    let selesai = 0, gagal = 0;
    const arsip = [];
    for (const b of semua) {
      if (batal) { pr.gagal(`Dibatalkan setelah ${selesai} berkas`); return; }
      try {
        const url = BERISI
          ? '/tambah?name=' + encodeURIComponent(b.nama)
          : '/upload?ds=' + encodeURIComponent(PROJEK)
            + '&name=' + encodeURIComponent(b.nama);
        const j = await send(url, { method: 'PUT', body: b.file });
        if (!j.ok) { gagal++; if (gagal <= 2) toast(b.nama + ': ' + j.error); }
        else if (j.arsip) arsip.push(j.name);
      } catch (e) { gagal++; }
      selesai++;
      pr.set(selesai / semua.length,
             `${selesai.toLocaleString('id-ID')} dari ${semua.length.toLocaleString('id-ID')} berkas`
             + (gagal ? ` · ${gagal} gagal` : ''));
    }

    if (selesai <= gagal) { pr.gagal('Semua berkas gagal terkirim'); return; }

    for (const nama of arsip) {
      // Persentasenya memang tidak diketahui, tetapi bahwa ia masih berjalan
      // itu diketahui, dan justru itu yang perlu terlihat.
      pr.taktentu('Membongkar ' + nama + '… (berkas besar perlu waktu)');
      try {
        const j = await post('/unzip?ds=' + encodeURIComponent(PROJEK) +
                             '&name=' + encodeURIComponent(nama));
        if (!j.ok) { pr.gagal('Gagal membongkar: ' + j.error); return; }
      } catch (e) { pr.gagal('Gagal menghubungi server saat membongkar'); return; }
    }

    pr.taktentu('Memindai isi dataset…');
    // /useupload membuka folder unggahan sebagai dataset; untuk yang sudah
    // terbuka cukup dipindai ulang supaya gambar barunya ikut terhitung.
    const buka = BERISI
      ? await post('/rescan')
      : await post('/useupload?ds=' + encodeURIComponent(PROJEK));
    if (!buka.ok) { pr.gagal(buka.error || 'Gagal memindai dataset'); return; }

    if (namaBatch || tag.length) {
      pr.taktentu('Menandai gambar…');
      try {
        // tanpa_batch: yang baru masuk adalah yang belum punya nama batch.
        // Isi .zip tidak pernah dilihat peramban, jadi menandainya lewat
        // daftar nama yang dikirim dari sini akan melewatkan semuanya.
        await send('/api/tag/pasang', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tanpa_batch: true, batch: namaBatch, tambah: tag }),
        });
      } catch (e) { toast('Gambar terunggah, tapi tagnya gagal disimpan'); }
    }

    const pesan = `${(buka.n || 0).toLocaleString('id-ID')} gambar di dataset`
                + (gagal ? ` · ${gagal} berkas gagal` : '');
    pr.selesai(pesan);
    $('ug-batal').hidden = true;
    $('ug-lanjut').hidden = false;
    const ket = $('ug-unggah-ket');
    if ((buka.peringatan || []).length) {
      // Ditahan di layar, bukan ditoast: isinya justru yang menentukan apakah
      // datasetnya benar, dan toast hilang sebelum sempat dibaca.
      ket.innerHTML = '<b>Perlu dicek:</b><br>'
        + buka.peringatan.map(p => '· ' + p).join('<br>');
    } else {
      ket.textContent = 'Selesai. Semua berkas sudah ada di ' + PROJEK + '.';
    }
  }

  // ------------------------------------------------------------- server
  /* Menyalin dari folder di server ini, bukan membukanya di tempat.
     Bedanya menentukan: membuka di tempat berarti menyunting mengubah dataset
     aslinya, yang mungkin dipakai orang lain atau proyek lain di mesin yang
     sama. Di sini isinya disalin dulu ke projek ini.

     Ukurannya disurvei lebih dulu dan tombolnya berubah jadi konfirmasi.
     Menyalin beberapa GB tanpa pemberitahuan bukan kejutan yang menyenangkan,
     dan sebuah dialog confirm() menutupi persis angka yang perlu dibaca. */
  let survei = null;

  async function imporServer() {
    const tombol = $('ug-server-impor');
    const jalur = $('ug-server-jalur');
    const path = ($('ug-server-path').value || '').trim();
    if (!path) { toast('Path masih kosong'); return; }

    if (!survei || survei.path !== path) {
      const pr = Progres.mulai('Memeriksa isi folder', { di: jalur });
      pr.taktentu('Menghitung berkas di ' + path);
      let s;
      try {
        s = await (await fetch('/api/impor/survei?path='
                               + encodeURIComponent(path))).json();
      } catch (e) { pr.gagal('Gagal menghubungi server'); return; }
      if (!s.ok) { pr.gagal(s.error); return; }
      const mb = s.bytes / 1048576;
      const ukuran = mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB'
                                : mb.toFixed(0) + ' MB';
      pr.selesai(`${s.berkas.toLocaleString('id-ID')} berkas · ${ukuran}`);
      survei = { path, ...s };
      tombol.textContent = `Salin ${s.berkas.toLocaleString('id-ID')} berkas (${ukuran})`;
      tombol.dataset.siap = '1';
      return;
    }

    tombol.disabled = true;
    const pr = Progres.mulai('Menyalin ke ' + PROJEK, { di: jalur });
    // Penyalinan berjalan di thread terpisah di server sementara permintaan
    // /impor menggantung sampai selesai, jadi kemajuannya ditanyakan lewat
    // permintaan terpisah. Tanpa ini, menyalin 22 ribu berkas tampak seperti
    // halaman yang macet.
    const pantau = setInterval(async () => {
      let k;
      try { k = await (await fetch('/api/impor/kemajuan')).json(); } catch (e) { return; }
      if (!k || !k.tahap) return;
      if (k.tahap === 'pindai') { pr.taktentu('Tersalin, memindai isinya…'); return; }
      if (k.tahap !== 'salin' || !k.total) return;
      pr.set(k.berkas / k.total,
             `${k.berkas.toLocaleString('id-ID')} dari ${k.total.toLocaleString('id-ID')} berkas`);
    }, 500);

    try {
      const j = await post('/impor?path=' + encodeURIComponent(path)
                         + '&ds=' + encodeURIComponent(PROJEK));
      clearInterval(pantau);
      if (!j.ok) { pr.gagal(j.error); tombol.disabled = false; return; }
      pr.selesai(`${(j.n || 0).toLocaleString('id-ID')} gambar masuk ke ${PROJEK}`);
      $('ug-lanjut').hidden = false;
      keTahap(3);
      $('ug-unggah-nama').textContent = PROJEK;
      $('ug-unggah-ket').textContent =
        'Salinan selesai. Berkasnya sekarang ada di ' + PROJEK + '.';
      $('ug-batal').hidden = true;
    } catch (e) {
      clearInterval(pantau);
      pr.gagal('Gagal menghubungi server saat menyalin');
      tombol.disabled = false;
    }
  }

  // ------------------------------------------------------------- pasang
  function pasangDrop(el) {
    el.addEventListener('dragover', (e) => {
      e.preventDefault();
      el.setAttribute('data-over', '');
    });
    el.addEventListener('dragleave', () => el.removeAttribute('data-over'));
    el.addEventListener('drop', async (e) => {
      e.preventDefault();
      el.removeAttribute('data-over');
      tambah(await dariDrop(e.dataTransfer));
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    keTahap(1);
    pasangDrop($('ug-drop'));
    pasangDrop($('ug-panggung'));

    // stopPropagation: tanpa itu kliknya menggelembung ke .ug-drop dan
    // pemilih berkas terbuka dua kali.
    //
    // Inputnya dikosongkan SEBELUM dibuka, bukan sesudah berkasnya diambil.
    // Mengosongkannya sesudah membuat File yang sudah kita pegang jadi kosong:
    // isinya masih tercatat, ukurannya nol, dan thumbnail-nya gagal dimuat
    // tanpa satu pesan galat pun. Dikosongkan di sini, memilih folder yang
    // sama dua kali tetap memicu `change`.
    const buka = (input) => (e) => {
      e.stopPropagation();
      input.value = '';
      input.click();
    };
    $('ug-pilih-berkas').onclick = buka($('ug-berkas'));
    $('ug-pilih-folder').onclick = buka($('ug-folder'));
    $('ug-tambah-berkas').onclick = buka($('ug-berkas'));
    $('ug-tambah-folder').onclick = buka($('ug-folder'));
    for (const id of ['ug-berkas', 'ug-folder']) {
      $(id).addEventListener('change', (e) => tambah([...e.target.files]));
    }

    $('ug-tab').addEventListener('change', (e) => {
      saring = e.target.value;
      for (const l of $('ug-tab').querySelectorAll('.seg-opt')) {
        l.toggleAttribute('data-on', l.contains(e.target));
      }
      gambarUlang();
    });

    const ti = $('ug-tag-input');
    ti.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ',') {
        e.preventDefault();
        tambahTag(ti.value);
        ti.value = '';
      } else if (e.key === 'Backspace' && !ti.value && tag.length) {
        tag.pop();
        gambarTag();
      }
    });
    ti.addEventListener('blur', () => { tambahTag(ti.value); ti.value = ''; });

    $('ug-simpan').onclick = kirim;
    $('ug-server-impor').onclick = imporServer;
    const jelajah = $('ug-server-jelajah');
    if (jelajah) {
      jelajah.onclick = async () => {
        toast('Dialog terbuka di layar server…');
        const j = await post('/pickdir');
        if (!j.ok) { toast(j.error); return; }
        // /pickdir membuka folder itu di tempat, bukan menyalin. Halaman ini
        // tentang mengisi projek, jadi yang benar setelahnya adalah gridnya.
        location.href = '/';
      };
    }
    $('ug-server-path').addEventListener('input', () => {
      // Path berubah berarti hasil surveinya sudah bukan milik path itu lagi.
      survei = null;
      const b = $('ug-server-impor');
      delete b.dataset.siap;
      b.textContent = 'Salin';
    });
    for (const b of document.querySelectorAll('.ug-riwayat-path')) {
      b.onclick = () => {
        $('ug-server-path').value = b.dataset.path;
        $('ug-server-path').dispatchEvent(new Event('input'));
      };
    }
    for (const b of document.querySelectorAll('.ug-riwayat-lupa')) {
      b.onclick = async () => {
        await post('/lupakan-path?path=' + encodeURIComponent(b.dataset.path));
        b.closest('.ug-riwayat-baris').remove();
      };
    }
    $('ug-batal').onclick = () => { batal = true; };
    $('ug-lanjut').href = '/';

    // Nama batch bawaan: tanggal dan jam hari ini. Kolom kosong membuat orang
    // mengarangnya sendiri tiap kali, dan yang dikarang jarang konsisten.
    const kini = new Date();
    $('ug-nama-batch').value = 'Unggahan ' + kini.toLocaleDateString('id-ID',
      { day: 'numeric', month: 'short', year: 'numeric' }) + ' '
      + String(kini.getHours()).padStart(2, '0') + '.'
      + String(kini.getMinutes()).padStart(2, '0');
  });
})();
