/* Kamus parameter — satu sumber untuk penjelasan hsv, learning rate, dll.
 *
 * Dipakai di halaman Buat Versi & Training: badge "?" di sebelah parameter
 * membuka popover ringkas, dan tombol "Kamus" membuka daftar lengkap.
 * Ringkas & mudah dipahami — arti satu kalimat + rentang + bawaan + tip.
 *
 * Sengaja polos (window global), bukan modul: dimuat lewat base.html supaya
 * tersedia di semua halaman tanpa build step.
 */
(function () {
  'use strict';

  // grup -> {nama, warna} untuk pengelompokan visual
  const GRUP = {
    warna: { nama: 'Warna & Cahaya', warna: '#2e7d32', emoji: '🎨' },
    inti: { nama: 'Inti Training', warna: '#1565c0', emoji: '⚙️' },
    loss: { nama: 'Bobot Loss', warna: '#6a1b9a', emoji: '⚖️' },
    geo: { nama: 'Geometri', warna: '#e65100', emoji: '📐' },
    lanjut: { nama: 'Augmentasi Lanjut', warna: '#00838f', emoji: '🧩' },
    teknis: { nama: 'Segmentasi & Teknis', warna: '#546e7a', emoji: '🎭' },
  };

  // key -> {nama, grup, arti, rentang, bawaan, tip}
  const KAMUS = {
    // ---- warna ----
    hsv_h: { nama: 'Rona (Hue)', grup: 'warna',
      arti: 'Menggeser corak warna, memutar roda warna (merah↔hijau↔biru).',
      rentang: '0-0.5', bawaan: '0.03 · WARNA: 0',
      tip: 'Kunci ke 0 bila warna adalah identitas kelas.' },
    hsv_s: { nama: 'Kepekatan (Saturation)', grup: 'warna',
      arti: 'Mengubah pekat/pucatnya warna (kalem ↔ ngejreng).',
      rentang: '0-1', bawaan: '0.7 · WARNA: 0.20',
      tip: 'Tekan bila dua kelas beda hanya di kepekatan warna (mis. navy vs hitam).' },
    hsv_v: { nama: 'Kecerahan (Value)', grup: 'warna',
      arti: 'Mengubah gelap↔terang, meniru variasi pencahayaan.',
      rentang: '0-1', bawaan: '0.5',
      tip: 'Boleh lebar; tidak merusak rona, malah bikin tahan cahaya.' },
    bgr: { nama: 'Tukar Merah↔Biru', grup: 'warna',
      arti: 'Peluang menukar kanal warna (membalik warna total).',
      rentang: '0-1', bawaan: '0.1 · WARNA: 0',
      tip: 'Selalu 0 untuk tugas yang bergantung warna.' },
    // ---- inti ----
    epochs: { nama: 'Epoch', grup: 'inti',
      arti: 'Berapa kali seluruh data dibaca saat melatih.',
      rentang: '1-1000', bawaan: '250',
      tip: 'Naikkan bila belum konvergen; turunkan bila cepat overfit.' },
    imgsz: { nama: 'Ukuran Gambar', grup: 'inti',
      arti: 'Gambar diperkecil ke ukuran ini (piksel) sebelum dilatih.',
      rentang: '320-1280', bawaan: '640',
      tip: 'Naikkan (960/1280) untuk objek kecil, asal sumbernya resolusi tinggi.' },
    batch: { nama: 'Batch', grup: 'inti',
      arti: 'Berapa gambar diproses sekaligus tiap langkah.',
      rentang: '1-64', bawaan: '16',
      tip: 'Turunkan bila kehabisan VRAM (OOM).' },
    patience: { nama: 'Berhenti Otomatis', grup: 'inti',
      arti: 'Berhenti bila hasil tak membaik selama sekian epoch.',
      rentang: '0-500', bawaan: '50',
      tip: 'Naikkan agar lebih sabar. 0 = tak pernah berhenti dini.' },
    lr0: { nama: 'Laju Belajar Awal', grup: 'inti',
      arti: 'Seberapa besar langkah perbaikan model tiap update.',
      rentang: '0.000001-0.1', bawaan: '0.0003',
      tip: 'Jarang diubah; kecil untuk fine-tune dari bobot pra-latih.' },
    lrf: { nama: 'Laju Belajar Akhir', grup: 'inti',
      arti: 'Pecahan dari laju awal (lr akhir = lr0 × lrf).',
      rentang: '0.0001-1', bawaan: '0.01', tip: 'Umumnya dibiarkan.' },
    warmup_epochs: { nama: 'Pemanasan', grup: 'inti',
      arti: 'Epoch awal dengan laju belajar dinaikkan pelan agar stabil.',
      rentang: '0-20', bawaan: '3', tip: 'Umumnya dibiarkan.' },
    workers: { nama: 'Pembaca Data', grup: 'inti',
      arti: 'Berapa utas dipakai membaca gambar dari disk.',
      rentang: '0-16', bawaan: '8', tip: 'Turunkan bila CPU/RAM terbatas.' },
    // ---- loss ----
    box: { nama: 'Bobot Kotak', grup: 'loss',
      arti: 'Seberapa penting ketepatan letak & ukuran kotak.',
      rentang: '0-20', bawaan: '7.5', tip: 'Naikkan bila kotak sering meleset.' },
    cls: { nama: 'Bobot Kelas', grup: 'loss',
      arti: 'Seberapa penting ketepatan nama kelas.',
      rentang: '0-20', bawaan: '2.0', tip: 'Naikkan bila sering salah kelas.' },
    dfl: { nama: 'Bobot Tepi', grup: 'loss',
      arti: 'Seberapa penting ketepatan tepi kotak.',
      rentang: '0-20', bawaan: '1.5', tip: 'Ubah hati-hati.' },
    // ---- geo ----
    scale: { nama: 'Variasi Ukuran', grup: 'geo',
      arti: 'Objek diperbesar-perkecil acak sebanyak ini.',
      rentang: '0-1', bawaan: '0.3', tip: 'Membantu model tahan perubahan skala.' },
    degrees: { nama: 'Variasi Putaran', grup: 'geo',
      arti: 'Gambar diputar acak sampai sekian derajat.',
      rentang: '0-180', bawaan: '25', tip: '' },
    translate: { nama: 'Variasi Geser', grup: 'geo',
      arti: 'Gambar digeser acak sebanyak ini.',
      rentang: '0-1', bawaan: '0.15', tip: '' },
    fliplr: { nama: 'Balik Kiri-Kanan', grup: 'geo',
      arti: 'Peluang gambar dicerminkan mendatar.',
      rentang: '0-1', bawaan: '0.5', tip: '' },
    flipud: { nama: 'Balik Atas-Bawah', grup: 'geo',
      arti: 'Peluang gambar dicerminkan tegak.',
      rentang: '0-1', bawaan: '0.3', tip: '' },
    // ---- lanjut ----
    mosaic: { nama: 'Gabung 4 Gambar', grup: 'lanjut',
      arti: 'Empat gambar ditempel jadi satu (bagus untuk objek kecil).',
      rentang: '0-1', bawaan: '0.3', tip: 'Terlalu tinggi membuat objek jadi sangat kecil.' },
    close_mosaic: { nama: 'Matikan Gabung di Akhir', grup: 'lanjut',
      arti: 'Sekian epoch terakhir dilatih tanpa penggabungan.',
      rentang: '0-200', bawaan: '30', tip: '' },
    copy_paste: { nama: 'Tempel Objek Antar-Gambar', grup: 'lanjut',
      arti: 'Objek dari gambar lain ditempelkan.',
      rentang: '0-1', bawaan: '0', tip: 'Bisa merusak konteks ruangan detektor.' },
    // ---- teknis ----
    mask_ratio: { nama: 'Kehalusan Mask', grup: 'teknis',
      arti: 'Rasio downsample mask (khusus segmentasi). Kecil = presisi, berat.',
      rentang: '1-8', bawaan: '2', tip: '' },
  };

  function esc(s) {
    return String(s).replace(/[&<>"]/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  // -------------------------------------------------- popover satu parameter
  let popoverAktif = null;
  function tutupPopover() {
    if (popoverAktif) { popoverAktif.remove(); popoverAktif = null; }
  }

  function bukaPopover(key, jangkar, override) {
    tutupPopover();
    const e = KAMUS[key];
    if (!e) return;
    const g = GRUP[e.grup] || { nama: '', warna: '#607d8b', emoji: '' };
    const rentang = (override && override.rentang) || e.rentang;
    const bawaan = (override && override.bawaan) || e.bawaan;
    const pop = document.createElement('div');
    pop.className = 'km-pop';
    pop.innerHTML =
      `<div class="km-pop-atas" style="--km:${g.warna}">
         <span class="km-dot"></span><b>${esc(e.nama)}</b>
         <span class="km-grup">${g.emoji} ${esc(g.nama)}</span></div>
       <p class="km-arti">${esc(e.arti)}</p>
       <div class="km-cip">
         <span class="km-c"><i>Rentang</i>${esc(rentang)}</span>
         <span class="km-c"><i>Bawaan</i>${esc(bawaan)}</span></div>
       ${e.tip ? `<p class="km-tip">💡 ${esc(e.tip)}</p>` : ''}`;
    document.body.appendChild(pop);
    // posisikan di dekat jangkar
    const r = jangkar.getBoundingClientRect();
    const pr = pop.getBoundingClientRect();
    let top = r.bottom + window.scrollY + 8;
    let left = r.left + window.scrollX - pr.width / 2 + r.width / 2;
    left = Math.max(8, Math.min(left, window.scrollX + document.documentElement.clientWidth - pr.width - 8));
    pop.style.top = top + 'px';
    pop.style.left = left + 'px';
    popoverAktif = pop;
  }

  // ------------------------------------------------------ modal daftar penuh
  function bukaModal() {
    tutupPopover();
    const lama = document.getElementById('km-modal');
    if (lama) lama.remove();
    const grupUrut = ['warna', 'inti', 'loss', 'geo', 'lanjut', 'teknis'];
    const kartu = (key) => {
      const e = KAMUS[key];
      return `<div class="km-kartu" data-cari="${esc((e.nama + ' ' + key + ' ' + e.arti).toLowerCase())}">
        <div class="km-kartu-nama">${esc(e.nama)} <code>${esc(key)}</code></div>
        <div class="km-kartu-arti">${esc(e.arti)}</div>
        <div class="km-cip">
          <span class="km-c"><i>Rentang</i>${esc(e.rentang)}</span>
          <span class="km-c"><i>Bawaan</i>${esc(e.bawaan)}</span></div>
        ${e.tip ? `<div class="km-tip">💡 ${esc(e.tip)}</div>` : ''}
      </div>`;
    };
    const seksi = grupUrut.map((gk) => {
      const g = GRUP[gk];
      const keys = Object.keys(KAMUS).filter((k) => KAMUS[k].grup === gk);
      if (!keys.length) return '';
      return `<section class="km-seksi" data-grup="${gk}">
        <h3 style="--km:${g.warna}">${g.emoji} ${esc(g.nama)}</h3>
        <div class="km-grid">${keys.map(kartu).join('')}</div></section>`;
    }).join('');
    const modal = document.createElement('div');
    modal.id = 'km-modal';
    modal.className = 'km-tirai';
    modal.innerHTML =
      `<div class="km-panel" role="dialog" aria-label="Kamus parameter">
         <div class="km-panel-atas">
           <b>📖 Kamus Parameter</b>
           <input id="km-cari" class="km-cari" type="search" placeholder="Cari parameter…">
           <button class="chip" type="button" id="km-tutup">Tutup</button>
         </div>
         <div class="km-isi">${seksi}
           <p class="km-catatan">💡 <b>Mode Warna</b> menentukan hsv otomatis:
             <b>BENTUK</b> (warna diacak → belajar bentuk) untuk kemasan bebas warna;
             <b>WARNA</b> (warna dikunci) bila warna adalah identitas kelas.</p>
         </div>
       </div>`;
    document.body.appendChild(modal);
    const cari = modal.querySelector('#km-cari');
    cari.oninput = () => {
      const q = cari.value.trim().toLowerCase();
      modal.querySelectorAll('.km-kartu').forEach((k) => {
        k.hidden = q && !k.dataset.cari.includes(q);
      });
      modal.querySelectorAll('.km-seksi').forEach((s) => {
        s.hidden = ![...s.querySelectorAll('.km-kartu')].some((k) => !k.hidden);
      });
    };
    cari.focus();
    modal.querySelector('#km-tutup').onclick = () => modal.remove();
    modal.addEventListener('click', (ev) => { if (ev.target === modal) modal.remove(); });
  }

  // ---------------------------------------------------------------- pasang
  /** Suntik badge "?" ke tiap elemen ber-[data-kamus] di dalam root. */
  function pasang(root) {
    (root || document).querySelectorAll('[data-kamus]').forEach((el) => {
      if (el.querySelector('.km-tanya')) return;
      const key = el.getAttribute('data-kamus');
      if (!KAMUS[key]) return;
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'km-tanya';
      b.setAttribute('data-kamus-key', key);
      b.setAttribute('aria-label', 'Penjelasan ' + key);
      b.textContent = '?';
      el.appendChild(b);
    });
  }

  // klik global: badge "?" & tombol kamus
  document.addEventListener('click', (ev) => {
    const t = ev.target.closest('.km-tanya');
    if (t) {
      ev.preventDefault();
      ev.stopPropagation();
      if (popoverAktif && popoverAktif._key === t) { tutupPopover(); return; }
      // rentang/bawaan hidup dari input di sekitarnya, kalau ada
      const wadah = t.closest('[data-kamus]') || t.parentElement;
      const inp = wadah && wadah.querySelector('input[data-par]');
      let override = null;
      if (inp) {
        const min = inp.getAttribute('min'), max = inp.getAttribute('max');
        override = {
          rentang: (min != null && max != null) ? `${min}-${max}` : null,
          bawaan: inp.getAttribute('data-bawaan'),
        };
      }
      bukaPopover(t.getAttribute('data-kamus-key'), t, override);
      popoverAktif._key = t;
      return;
    }
    if (ev.target.closest('.km-buka')) { ev.preventDefault(); bukaModal(); return; }
    tutupPopover();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') {
      tutupPopover();
      const m = document.getElementById('km-modal'); if (m) m.remove();
    }
  });
  window.addEventListener('scroll', tutupPopover, true);

  window.KAMUS = KAMUS;
  window.pasangKamus = pasang;
  window.bukaKamus = bukaModal;
})();
