/*
 * Halaman bagi tugas.
 * ===================
 * Satu keputusan, tiga bagian: berapa banyak, untuk siapa, dan dengan catatan
 * apa. Yang dikirim ke server daftar path gambarnya, bukan angka jumlah:
 * server tidak boleh menebak gambar mana yang dimaksud, karena isi projek bisa
 * berubah di antara halaman ini dibuka dan tombolnya ditekan.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const isi = $('bg-isi');
  if (!isi || !$('bg-kisi')) return;

  const DS = isi.dataset.ds || '';
  const PEMILIK = isi.dataset.pemilik || '';
  const ubin = [...document.querySelectorAll('.bg-ubin')];
  const MAKS = ubin.length;
  let pelabel = '';

  // ------------------------------------------------------------ jumlah
  const n = $('bg-n'), slider = $('bg-slider');

  function sorot() {
    const k = Math.max(1, Math.min(MAKS, parseInt(n.value, 10) || 1));
    // Sorotnya HANYA berlaku kalau urutannya tidak diacak. Dengan acak
    // menyala, gambar yang tersorot bukan gambar yang benar-benar akan
    // ditugaskan, dan penanda yang menunjuk hal yang salah lebih buruk
    // daripada tidak ada penanda sama sekali. Diganti satu kalimat.
    const acak = $('bg-acak').checked;
    ubin.forEach((el, i) => el.toggleAttribute('data-luar', !acak && i >= k));
    $('bg-acak-ket').textContent = acak
      ? `${k.toLocaleString('id-ID')} gambar diambil acak dari seluruh yang belum ditugaskan.`
      : '';
    perbarui();
  }

  function setel(v) {
    const k = Math.max(1, Math.min(MAKS, parseInt(v, 10) || 1));
    n.value = k;
    slider.value = k;
    sorot();
  }
  $('bg-acak').addEventListener('change', sorot);
  n.addEventListener('input', () => setel(n.value));
  slider.addEventListener('input', () => setel(slider.value));

  // ------------------------------------------------------------ orang
  async function muatOrang() {
    const wadah = $('bg-orang');
    let j;
    try { j = await (await fetch('/api/tugas/calon')).json(); }
    catch (e) { wadah.innerHTML = '<span class="halus">Gagal memuat daftar akun</span>'; return; }
    if (!j.ok) { wadah.innerHTML = `<span class="halus">${j.error}</span>`; return; }

    wadah.replaceChildren();

    // Undangan yang belum dipakai ditampilkan lebih dulu: ia pekerjaan yang
    // belum selesai, dan tautan yang tidak bisa dicabut berlaku selamanya.
    for (const u of (j.undangan || [])) {
      const el = document.createElement('div');
      el.className = 'bg-orang-baris bg-menunggu';
      el.innerHTML =
        '<span class="bg-avatar" aria-hidden="true">@</span>'
        + `<span class="bg-orang-teks"><b>${u.email}</b>`
        + '<span class="halus">diundang, belum diterima</span></span>';
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'bg-cabut';
      x.textContent = '\u00d7';
      x.title = 'Batalkan undangan ini';
      x.onclick = async () => {
        const r = await post('/api/tugas/batalkan-undangan?token='
                             + encodeURIComponent(u.token));
        if (!r.ok) { toast(r.error); return; }
        toast('Undangan dibatalkan');
        muatOrang();
      };
      el.appendChild(x);
      wadah.appendChild(el);
    }
    // Pemilik projek ikut didaftar: sering kali dialah yang mengerjakan
    // sendiri sebagian, dan tanpa barisnya ia harus mengundang dirinya.
    for (const a of j.akun) {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'bg-orang-baris';
      el.dataset.akun = a.akun;
      el.innerHTML =
        `<span class="bg-avatar" aria-hidden="true">${a.nama.slice(0, 1).toUpperCase()}</span>`
        + `<span class="bg-orang-teks"><b>${a.nama}</b>`
        + `<span class="halus">${a.email || a.akun}`
        + (a.akun === j.pemilik ? ' &middot; pemilik'
           : a.anggota ? ' &middot; anggota' : '') + '</span></span>'
        + '<span class="bg-orang-n"></span>';
      // Mengeluarkan anggota hanya untuk tamu, bukan pemilik: pemilik projek
      // tidak bisa mengeluarkan dirinya dari projeknya sendiri.
      if (a.anggota && a.akun !== j.pemilik) {
        const x = document.createElement('button');
        x.type = 'button';
        x.className = 'bg-cabut';
        x.textContent = '\u00d7';
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
      el.onclick = () => {
        pelabel = pelabel === a.akun ? '' : a.akun;
        for (const b of wadah.querySelectorAll('.bg-orang-baris')) {
          b.toggleAttribute('data-on', b.dataset.akun === pelabel);
        }
        perbarui();
      };
      wadah.appendChild(el);
    }
    perbarui();
  }

  function perbarui() {
    const k = parseInt(n.value, 10) || 0;
    const tombol = $('bg-mulai');
    tombol.disabled = !pelabel || !k;
    tombol.textContent = !pelabel ? 'Pilih pelabel dulu'
      : `Tugaskan ${k.toLocaleString('id-ID')} gambar ke ${pelabel}`;
    for (const b of document.querySelectorAll('.bg-orang-baris')) {
      b.querySelector('.bg-orang-n').textContent =
        b.dataset.akun === pelabel ? `${k} gambar` : '';
    }
  }

  // ------------------------------------------------------------ undang
  $('bg-tambah-orang').onclick = () => {
    const k = $('bg-undang');
    k.hidden = !k.hidden;
    if (!k.hidden) $('bg-email').focus();
  };

  $('bg-undang-kirim').onclick = async () => {
    const v = ($('bg-email').value || '').trim();
    if (!v) { toast('Isi akun atau emailnya dulu'); return; }
    const jalur = $('bg-undang-jalur');
    const pr = Progres.mulai('Mengundang ' + v, { di: jalur });
    pr.taktentu('menyiapkan undangan');
    // Tanpa "@" itu nama akun, bukan alamat. Keduanya lewat satu rute yang
    // sama supaya aturannya tidak bercabang di dua tempat.
    const url = v.includes('@')
      ? '/api/tugas/undang-email?email=' + encodeURIComponent(v)
      : '/api/tugas/undang?akun=' + encodeURIComponent(v);
    let j;
    try { j = await post(url); } catch (e) { pr.gagal('Gagal menghubungi server'); return; }
    if (!j.ok) { pr.gagal(j.error); return; }

    if (j.tautan) {
      pr.selesai('Undangan dibuat');
      // Tautannya ditahan di layar dengan tombol salin, bukan ditoast: ia
      // satu-satunya jalan orang itu masuk, dan toast hilang sebelum sempat
      // disalin.
      jalur.insertAdjacentHTML('beforeend',
        '<div class="bg-tautan"><span class="mono" id="bg-tautan-teks"></span>'
        + '<button class="chip" type="button" id="bg-salin">Salin</button></div>');
      $('bg-tautan-teks').textContent = j.tautan;
      $('bg-salin').onclick = async () => {
        try { await navigator.clipboard.writeText(j.tautan); toast('Tautan disalin'); }
        catch (e) { toast('Salin sendiri tautannya'); }
      };
    } else {
      pr.selesai(`${j.akun || v} jadi anggota`);
      $('bg-email').value = '';
      muatOrang();
    }
  };

  // ------------------------------------------------------------ kirim
  $('bg-mulai').onclick = async () => {
    const k = Math.max(1, Math.min(MAKS, parseInt(n.value, 10) || 1));
    let daftar = ubin.map(el => el.dataset.path);
    if ($('bg-acak').checked) {
      // Fisher-Yates. Mengacak lalu memotong, bukan memilih acak satu per
      // satu, supaya tidak ada gambar yang terpilih dua kali.
      for (let i = daftar.length - 1; i > 0; i--) {
        const j2 = Math.floor(Math.random() * (i + 1));
        [daftar[i], daftar[j2]] = [daftar[j2], daftar[i]];
      }
    }
    daftar = daftar.slice(0, k);

    const tombol = $('bg-mulai');
    tombol.disabled = true;
    const pr = Progres.mulai(`Menugaskan ${k} gambar ke ${pelabel}`,
                             { di: $('bg-jalur') });
    pr.taktentu('menyimpan pembagiannya');
    let j;
    try {
      j = await send('/api/tugas/bagi', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pelabel, gambar: daftar,
                               catatan: ($('bg-catatan').value || '').trim() }),
      });
    } catch (e) { pr.gagal('Gagal menghubungi server'); tombol.disabled = false; return; }
    if (!j.ok) { pr.gagal(j.error); tombol.disabled = false; return; }

    pr.selesai(`${j.n} gambar jadi tugas ${j.pelabel}`
               + (j.dilewati ? ` · ${j.dilewati} sudah ditugaskan lebih dulu` : ''));
    setTimeout(() => location.reload(), 900);
  };

  setel(MAKS);
  muatOrang();
})();
