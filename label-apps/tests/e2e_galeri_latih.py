"""
Uji end-to-end galeri gambar hasil training, lewat Chrome + CDP.

Bukan bagian dari `pytest` (namanya sengaja tidak diawali test_) karena butuh
google-chrome terpasang. Jalankan sendiri:

    .venv/bin/python tests/e2e_galeri_latih.py

KENAPA HARUS DI BROWSER, BUKAN CUKUP DI PYTEST
Versi pertama galeri ini LOLOS semua tes layanan: daftar berkasnya benar,
endpoint gambarnya melayani byte yang benar, tidak ada satu pun gambar rusak.
Yang salah cuma ukurannya — tiap petak val itu mozaik 640x640 yang dirender
selebar panel, jadi SATU pasang gambar memakan seluruh layar dan mendorong
kurva, akurasi per kelas, serta tombol unduh keluar dari pandangan. Cacat
seperti itu hanya ada di tata letak, dan tidak ada assert Python yang bisa
melihatnya.

Ada satu lagi yang cuma kelihatan di DOM: server mengirim SEMUA berkas
`*_labels` dulu baru SEMUA `*_pred` (dua glob terpisah, masing-masing
di-sort). Dibiarkan apa adanya, tebakan petak 2 duduk berjauhan dari poligon
petak 2 dan tidak ada yang bisa dibandingkan — padahal justru itu gunanya.
Pemasangannya dikerjakan di JavaScript, jadi hanya browser yang bisa
membuktikannya.

Yang dibuktikan di sini, berurutan:
  1. ubinnya kecil (132px, menyalin kartu foto latar di tab Versi) dan
     deretnya tidak meluber keluar panel
  2. poligon sebenarnya dan tebakan model petak yang SAMA menempel jadi satu
     kartu, berurutan kiri-kanan
  3. mengklik ubin membuka kaca pembesar dengan gambar ukuran penuh — karena
     poligonnya memang tidak terbaca pada 132px
  4. panah kanan berpindah dari poligon sebenarnya ke tebakan model
  5. Esc menutup kaca pembesarnya saja, panel rincian di bawahnya tetap hidup
"""
import base64, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, ".")
sys.path.insert(0, "tests")
import urllib.request
from websockets.sync.client import connect
from e2e_kanvas import Cdp
import numpy as np, cv2
from app.security import hash_password

TMP = Path(tempfile.mkdtemp(prefix="e2e-galeri-"))
PORT, CDP = 8053, 9334

gagal = 0
def cek(nama, ok, detail=""):
    global gagal
    print(f"  {'OK  ' if ok else 'GAGAL'} {nama}" + (f"  [{detail}]" if detail else ""))
    if not ok: gagal += 1

# -- projek + satu training yang sudah selesai --------------------------------
(TMP / "unggahan" / "uji" / "p1").mkdir(parents=True)
(TMP / "datasets").mkdir()
(TMP / "users.json").write_text(json.dumps(
    {"uji": {"hash": hash_password("s1"), "nama": "uji", "admin": True}}))
d = TMP / "unggahan" / "uji" / "p1"
for i in range(3):
    cv2.imwrite(str(d / f"g{i}.jpg"), np.full((100, 120, 3), 120, np.uint8))

L = d / ".latih" / "L1"
(L / "weights").mkdir(parents=True)
(L / "weights" / "best.pt").write_bytes(b"x")
(L / "weights" / "last.pt").write_bytes(b"x")
# Tiga petak val, ditulis dengan urutan yang SENGAJA dibuat memusingkan:
# semua labels dulu, semua pred belakangan — persis seperti yang dikirim
# server. Kalau pemasangannya di JS rusak, di sinilah kelihatan.
petak = np.full((672, 672, 3), 40, np.uint8)
for b in range(3):
    for jenis in ("labels", "pred"):
        cv2.imwrite(str(L / f"val_batch{b}_{jenis}.jpg"), petak)
cv2.imwrite(str(L / "confusion_matrix_normalized.png"), petak)
(L / "results.csv").write_text(
    "epoch,time,train/box_loss,metrics/mAP50-95(M),metrics/mAP50(B)\n"
    + "".join(f"{e},{e*10}.0,{1.5-e*0.03},{0.3+e*0.02},{0.5+e*0.015}\n"
             for e in range(1, 31)))
(d / ".latih" / "L1.json").write_text(json.dumps({
    "nomor": 1, "nama": "Uji galeri", "versi": 2, "tugas": "segment",
    "bobot": "yolo26n-seg.pt", "oleh": "uji", "dibuat": "2026-09-11 14:24",
    "keadaan": "selesai", "pid": 0, "catatan": "",
    "par": {"epochs": 30, "batch": 8, "imgsz": 640},
    "warna": {"mode": "bentuk", "tingkat": "ok", "pesan": "x", "saran": {}}}))

srv = subprocess.Popen(
    [".venv/bin/python", "run.py", "--host", "127.0.0.1", "--port", str(PORT),
     "--users", str(TMP / "users.json"), "--datasets-root", str(TMP / "datasets"),
     "--uploads-root", str(TMP / "unggahan")],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    env={**os.environ, "LABELAPP_DEV_AUTOLOGIN": ""})
for _ in range(60):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/login", timeout=1)
        break
    except Exception:
        time.sleep(0.5)

chrome = subprocess.Popen(
    ["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox",
     f"--remote-debugging-port={CDP}", f"--user-data-dir={TMP/'c'}",
     "--window-size=1600,900", "about:blank"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ws_url = None
for _ in range(60):
    try:
        j = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json", timeout=1))
        t = [x for x in j if x["type"] == "page"]
        if t:
            ws_url = t[0]["webSocketDebuggerUrl"]
            break
    except Exception:
        pass
    time.sleep(0.5)

with connect(ws_url, max_size=None) as ws:
    dd = Cdp(ws)
    dd.kirim("Runtime.enable")
    dd.kirim("Page.enable")
    dd.kirim("Page.navigate", url=f"http://127.0.0.1:{PORT}/login")
    time.sleep(1.5)
    dd.js("fetch('/login',{method:'POST',headers:{'Content-Type':"
          "'application/x-www-form-urlencoded'},body:'user=uji&pw=s1'})", tunggu=True)
    time.sleep(1)
    dd.js(f"fetch('/setsrc?path={d}',{{method:'POST'}})", tunggu=True)
    time.sleep(1)
    dd.kirim("Page.navigate", url=f"http://127.0.0.1:{PORT}/latih?ds=p1")
    time.sleep(3.5)
    dd.js("document.querySelector('[data-rinci]').click()")
    time.sleep(2.5)

    # 1. ubin kecil, deret tidak meluber
    ukur = json.loads(dd.js("""JSON.stringify((() => {
      const g = document.querySelector('.tr-galeri');
      if (!g) return {};
      const ub = g.querySelector('.tr-gbr img');
      const isi = document.querySelector('.tr-panel-isi');
      return {ada: true,
              lebar_ubin: Math.round(ub.getBoundingClientRect().width),
              tinggi_ubin: Math.round(ub.getBoundingClientRect().height),
              tinggi_deret: Math.round(g.getBoundingClientRect().height),
              geser_x: getComputedStyle(g).overflowX,
              meluber: isi.scrollWidth > isi.clientWidth + 2,
              rusak: [...document.querySelectorAll('.tr-gbr img')]
                       .filter(i => i.complete && i.naturalWidth === 0).length};
    })())"""))
    cek("galeri terender", ukur.get("ada") is True)
    cek("ubin seukuran kartu foto latar (132px)",
        ukur.get("lebar_ubin") == 132 and ukur.get("tinggi_ubin") == 132,
        f"{ukur.get('lebar_ubin')}x{ukur.get('tinggi_ubin')}")
    cek("deretnya pendek, tidak memakan seluruh layar",
        (ukur.get("tinggi_deret") or 999) < 200, f"{ukur.get('tinggi_deret')}px")
    cek("deretnya digeser mendatar, bukan dibungkus",
        ukur.get("geser_x") == "auto", str(ukur.get("geser_x")))
    cek("panel tidak meluber mendatar", ukur.get("meluber") is False)
    cek("tidak ada gambar rusak", ukur.get("rusak") == 0, str(ukur.get("rusak")))

    # 2. label & prediksi petak yang sama menempel
    pas = json.loads(dd.js("""JSON.stringify(
      [...document.querySelectorAll('.tr-galeri .tr-pasang')].map(
        p => [...p.querySelectorAll('img')].map(i => i.getAttribute('src'))))"""))
    cek("tiga petak jadi tiga kartu pasangan", len(pas) == 3, f"{len(pas)} kartu")
    benar = all(len(p) == 2 and "_labels" in p[0] and "_pred" in p[1]
                and p[0].split("val_batch")[1][0] == p[1].split("val_batch")[1][0]
                for p in pas)
    cek("tiap kartu = poligon sebenarnya + tebakan petak yang SAMA", benar,
        "; ".join(
            "+".join(x.split("nama=")[-1] for x in p) for p in pas)[:120])

    # 3. klik membuka kaca pembesar berukuran penuh
    dd.js("document.querySelector('.tr-galeri img[data-lup]').click()")
    time.sleep(1.2)
    lup = json.loads(dd.js("""JSON.stringify((() => {
      const L = document.getElementById('tr-lup'), g = document.getElementById('tr-lup-gbr');
      return {terbuka: !L.hidden,
              lebar: Math.round(g.getBoundingClientRect().width),
              asli: g.naturalWidth,
              cap: document.getElementById('tr-lup-cap').textContent,
              mundur_mati: document.getElementById('tr-lup-mundur').disabled};
    })())"""))
    cek("klik ubin membuka kaca pembesar", lup.get("terbuka") is True)
    cek("gambarnya jauh lebih besar daripada ubinnya",
        (lup.get("lebar") or 0) > 400, f"{lup.get('lebar')}px dari asli {lup.get('asli')}px")
    cek("mulai dari poligon sebenarnya",
        lup.get("cap", "").startswith("poligon sebenarnya"), lup.get("cap", ""))
    cek("panah mundur mati di gambar pertama", lup.get("mundur_mati") is True)

    # 4. panah kanan pindah ke tebakan model
    dd.js("document.dispatchEvent(new KeyboardEvent('keydown',"
          "{key:'ArrowRight',bubbles:true}))")
    time.sleep(0.8)
    cap2 = dd.js("document.getElementById('tr-lup-cap').textContent")
    cek("panah kanan pindah ke tebakan model petak yang sama",
        str(cap2).startswith("tebakan model"), str(cap2))

    # 5. Esc menutup lup saja
    dd.js("document.dispatchEvent(new KeyboardEvent('keydown',"
          "{key:'Escape',bubbles:true}))")
    time.sleep(0.6)
    tutup = json.loads(dd.js("""JSON.stringify({
      lup: document.getElementById('tr-lup').hidden,
      panel: document.getElementById('tr-tirai').hidden})"""))
    cek("Esc menutup kaca pembesar", tutup.get("lup") is True)
    cek("panel rincian di bawahnya tetap terbuka", tutup.get("panel") is False)

    r = dd.kirim("Page.captureScreenshot", format="png")
    (TMP / "galeri.png").write_bytes(base64.b64decode(r["data"]))

for p in (chrome, srv):
    p.terminate()
    try:
        p.wait(5)
    except Exception:
        p.kill()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n  GAGAL: {gagal}")
sys.exit(1 if gagal else 0)
