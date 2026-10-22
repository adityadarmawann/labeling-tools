// Unggah dari HP: halaman ponsel yang dibuka lewat QR. Tiap foto dikirim satu
// permintaan PUT bertoken ke /unggah-hp/upload (jalur yang sama dengan unggah
// laptop, lihat routers/uploads.py), berurutan supaya ponsel tak membuka
// belasan koneksi sekaligus dan progresnya bisa dihitung per berkas.
(function () {
  "use strict";
  var panel = document.getElementById("panel");
  if (!panel) return;
  var token = panel.dataset.token;
  var maksByte = (parseInt(panel.dataset.maks, 10) || 80) * 1024 * 1024;

  var daftar = document.getElementById("daftar");
  var ringkas = document.getElementById("ringkas");
  var prog = document.getElementById("prog");
  var fill = document.getElementById("fill");

  var antre = [];        // berkas menunggu dikirim
  var jalan = false;     // satu pengirim aktif
  var ok = 0, gagal = 0, total = 0;

  function dua(n) { return (n < 10 ? "0" : "") + n; }

  // Nama unik: kamera ponsel sering memberi nama sama ("image.jpg") untuk tiap
  // jepretan, dan server menolak berkas yang namanya sudah ada. Stempel waktu +
  // penghitung membuat tiap unggahan punya nama sendiri tanpa menimpa apa pun.
  function namaUnik(asli, i) {
    var d = new Date();
    var cap = d.getFullYear() + dua(d.getMonth() + 1) + dua(d.getDate()) + "-" +
      dua(d.getHours()) + dua(d.getMinutes()) + dua(d.getSeconds());
    var m = /\.[a-z0-9]+$/i.exec(asli || "");
    var ext = m ? m[0].toLowerCase() : ".jpg";
    return "hp-" + cap + "-" + i + ext;
  }

  function perbaruiRingkas() {
    var sel = ok + gagal;
    if (!total) { ringkas.textContent = ""; return; }
    var t = ok + " dari " + total + " terunggah";
    if (gagal) t += " · " + gagal + " gagal";
    ringkas.innerHTML = sel < total
      ? "Mengunggah… " + t
      : t + (gagal ? " <span class='g'>(coba ulang yang gagal nanti)</span>" : " ✓");
    var p = total ? Math.round(sel / total * 100) : 0;
    prog.classList.toggle("on", sel < total && total > 0);
    fill.style.width = p + "%";
  }

  function tambahBaris(file, i) {
    var row = document.createElement("div");
    row.className = "baris";
    var img = document.createElement("img");
    img.className = "mini";
    var url = URL.createObjectURL(file);
    img.src = url;
    img.onload = function () { URL.revokeObjectURL(url); };
    var nm = document.createElement("span");
    nm.className = "nama";
    nm.textContent = file.name || ("foto " + (i + 1));
    var tk = document.createElement("span");
    tk.className = "tanda jalan";
    tk.textContent = "…";
    row.appendChild(img); row.appendChild(nm); row.appendChild(tk);
    daftar.insertBefore(row, daftar.firstChild);
    return tk;
  }

  function kirim(item) {
    var url = "/unggah-hp/upload?t=" + encodeURIComponent(token) +
      "&name=" + encodeURIComponent(item.nama);
    return fetch(url, { method: "PUT", body: item.file })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (j) {
        if (j && j.ok) {
          ok++; item.tk.className = "tanda ok"; item.tk.textContent = "✓";
        } else {
          gagal++; item.tk.className = "tanda gagal"; item.tk.textContent = "✕";
          item.tk.title = (j && j.error) || "gagal";
        }
      })
      .catch(function () {
        gagal++; item.tk.className = "tanda gagal"; item.tk.textContent = "✕";
      })
      .then(perbaruiRingkas);
  }

  function pompa() {
    if (jalan) return;
    var item = antre.shift();
    if (!item) { jalan = false; return; }
    jalan = true;
    kirim(item).then(function () { jalan = false; pompa(); });
  }

  function terima(files) {
    if (!files || !files.length) return;
    var i = 0;
    Array.prototype.forEach.call(files, function (file) {
      if (!/^image\//.test(file.type || "")) return;
      if (file.size > maksByte) {
        var tk = tambahBaris(file, total);
        tk.className = "tanda gagal"; tk.textContent = "✕";
        tk.title = "lebih dari " + Math.round(maksByte / 1048576) + " MB";
        total++; gagal++; perbaruiRingkas();
        return;
      }
      var tk2 = tambahBaris(file, total);
      antre.push({ file: file, nama: namaUnik(file.name, total), tk: tk2 });
      total++;
      i++;
    });
    perbaruiRingkas();
    pompa();
  }

  document.getElementById("kamera").addEventListener("change", function (e) {
    terima(e.target.files); e.target.value = "";
  });
  document.getElementById("galeri").addEventListener("change", function (e) {
    terima(e.target.files); e.target.value = "";
  });
})();
