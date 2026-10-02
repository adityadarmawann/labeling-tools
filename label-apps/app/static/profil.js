'use strict';

/*
 * Halaman akun DIRI SENDIRI (/profil).
 *
 * Tiga borang kecil yang semuanya menulis akun orang yang sedang masuk — tak
 * satu pun mengirim nama akun lain; servernya pun hanya pernah menyentuh
 * users[sess.user]. Yang dikerjakan di sini cuma memanggil rute itu, menampung
 * hasilnya, dan — begitu tersimpan — menyusulkan perubahannya ke KEPALA halaman
 * tanpa memuat ulang (nama & avatar di pil akun), supaya "tersimpan" terlihat
 * seketika, bukan baru setelah pindah halaman.
 */
(() => {
  const akar = document.querySelector('.profil');
  if (!akar) return;

  const akun = akar.dataset.akun || '';
  const $ = (id) => document.getElementById(id);

  // Panggil satu rute POST, tampilkan progresnya di `note`. Mengembalikan objek
  // jawaban ({ok, ...}) atau null kalau jaringannya gagal. Sama polanya dengan
  // halaman kelola member.
  async function kirim(url, opts, judul, note) {
    const pr = Progres.mulai(judul, note ? {di: note} : {});
    pr.taktentu('menyimpan…');
    let j;
    try {
      j = await (await fetch(url, opts)).json();
    } catch (e) { pr.gagal('Gagal menghubungi server'); return null; }
    if (!j || !j.ok) { pr.gagal((j && j.error) || 'gagal'); if (j && j.error) toast(j.error); }
    else pr.selesai();
    return j;
  }

  const qs = (obj) => '?' + new URLSearchParams(obj);

  // -- kepala: susulkan nama & avatar tanpa muat ulang ---------------------

  function setNamaKepala(nama) {
    document.querySelectorAll('.whoami-nama').forEach(el => {
      el.textContent = nama;
    });
  }

  function setAvatarKepala(fotoCap) {
    // `fotoCap` kosong -> kembalikan inisial; ada -> pasang <img> ber-cap
    // (?v=) supaya peramban mengambil yang baru, bukan yang di-cache.
    const huruf = (($('profil-nama').value || akun || '?').trim()[0] || '?')
      .toUpperCase();
    document.querySelectorAll('.whoami-ava').forEach(el => {
      if (fotoCap) {
        el.innerHTML = '';
        const im = document.createElement('img');
        im.width = 23; im.height = 23; im.alt = '';
        im.src = '/profil/foto?akun=' + encodeURIComponent(akun)
          + '&v=' + encodeURIComponent(fotoCap);
        el.appendChild(im);
      } else {
        el.textContent = huruf;
      }
    });
  }

  // -- nama & email --------------------------------------------------------

  const simpan = $('profil-simpan');
  if (simpan) simpan.onclick = async () => {
    const nama = $('profil-nama').value.trim();
    const email = $('profil-email').value.trim();
    const note = $('profil-note');
    const j = await kirim('/api/profil' + qs({nama, email}),
                          {method: 'POST'}, 'Menyimpan profil', note);
    if (j && j.ok) {
      toast('Profil tersimpan');
      // Server merapikan nama (kosong -> slug); pakai yang IA kembalikan.
      $('profil-nama').value = j.nama || nama;
      setNamaKepala(j.nama || nama || akun);
    }
  };

  // -- ganti sandi ---------------------------------------------------------

  const sandiSimpan = $('profil-sandi-simpan');
  if (sandiSimpan) sandiSimpan.onclick = async () => {
    const punya = sandiSimpan.dataset.punyaSandi === '1';
    const lamaEl = $('profil-sandi-lama');
    const lama = lamaEl ? lamaEl.value : '';
    const baru = $('profil-sandi-baru').value;
    const note = $('profil-sandi-note');
    // Penjaga di sisi klien semata supaya salahnya ketahuan lebih cepat; server
    // tetap memeriksa keduanya.
    if (punya && !lama) { toast('Isi kata sandi sekarang dulu'); return; }
    if (!baru) { toast('Isi kata sandi baru'); return; }
    const j = await kirim('/api/profil/sandi' + qs({sandi_lama: lama, sandi_baru: baru}),
                          {method: 'POST'}, 'Mengganti sandi', note);
    if (j && j.ok) {
      toast('Kata sandi diganti');
      if (lamaEl) lamaEl.value = '';
      $('profil-sandi-baru').value = '';
    }
  };

  // -- foto ----------------------------------------------------------------

  const pilih = $('profil-foto-pilih');
  const berkas = $('profil-foto-berkas');
  const hapus = $('profil-foto-hapus');
  const fnote = $('profil-foto-note');

  if (pilih && berkas) {
    pilih.onclick = () => berkas.click();
    berkas.onchange = async () => {
      const f = berkas.files && berkas.files[0];
      if (!f) return;
      // Bodi mentah + nama berkas di query (server menyaring ekstensinya untuk
      // pesan ramah, lalu cv2 yang memutuskan ini sungguh gambar).
      const j = await kirim('/api/profil/foto' + qs({name: f.name}),
                            {method: 'POST', body: f}, 'Mengunggah foto', fnote);
      berkas.value = '';           // supaya memilih berkas sama lagi tetap memicu change
      if (j && j.ok) {
        toast('Foto diperbarui');
        setAvatarKepala(j.foto);
        gambarUlangPratinjau(j.foto);
        if (hapus) hapus.hidden = false;
      }
    };
  }

  if (hapus) hapus.onclick = async () => {
    const j = await kirim('/api/profil/foto/hapus', {method: 'POST'},
                          'Menghapus foto', fnote);
    if (j && j.ok) {
      toast('Foto dihapus');
      setAvatarKepala('');
      gambarUlangPratinjau('');
      hapus.hidden = true;
    }
  };

  function gambarUlangPratinjau(fotoCap) {
    const kotak = $('profil-ava');
    if (!kotak) return;
    if (fotoCap) {
      kotak.innerHTML = '';
      const im = document.createElement('img');
      im.id = 'profil-ava-img'; im.alt = '';
      im.src = '/profil/foto?akun=' + encodeURIComponent(akun)
        + '&v=' + encodeURIComponent(fotoCap);
      kotak.appendChild(im);
    } else {
      const huruf = (($('profil-nama').value || akun || '?').trim()[0] || '?')
        .toUpperCase();
      kotak.innerHTML = '<span id="profil-ava-huruf"></span>';
      $('profil-ava-huruf').textContent = huruf;
    }
  }
})();
