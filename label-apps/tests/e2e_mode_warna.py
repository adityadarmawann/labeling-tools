"""
Uji end-to-end mode warna di wizard versi, lewat Chrome + CDP.

Bukan bagian dari `pytest` (namanya sengaja tidak diawali test_) karena butuh
google-chrome terpasang. Jalankan sendiri:

    .venv/bin/python tests/e2e_mode_warna.py

KENAPA HARUS DI BROWSER, BUKAN CUKUP DI PYTEST
Pilihan mode warna menyetel saklar augmentasi lewat JavaScript, dan versi
pertamanya TIDAK BEKERJA sama sekali: ia menambahkan penanda visual
`data-mode-mati` pada baris operasinya, padahal operasi yang mati memang tidak
dirender di daftar itu — jadi penandanya tidak pernah mengenai apa pun.

Dari luar semuanya tampak benar: pilihannya tersimpan, servernya tetap
memaksa operasi rona mati saat membangun pipeline, dan datasetnya tidak pernah
salah. Yang salah adalah APA YANG DILIHAT ORANG — daftar langkah yang
berselisih dengan pilihannya sendiri. Tidak satu pun tes pytest bisa
menangkapnya, karena yang rusak ada di DOM.

Yang dibuktikan di sini, berurutan:
  1. memilih "warna" benar-benar mengurangi jumlah langkah di daftar
  2. kelima langkah penggeser rona hilang dari layar
  3. resep yang BENAR-BENAR DIKIRIM membawa keenamnya dalam keadaan mati
     (ini yang paling menentukan — layar bisa saja benar sementara yang
      terkirim salah)
  4. kembali ke "bentuk" memulihkan seluruh langkahnya
"""
import json, os, subprocess, sys, time, tempfile, shutil
from pathlib import Path
sys.path.insert(0, ".")
sys.path.insert(0, "tests")
from websockets.sync.client import connect
import urllib.request
from e2e_kanvas import Cdp

TMP = Path(tempfile.mkdtemp(prefix="e2e-mode-"))
PORT, CDP = 8052, 9333
import numpy as np, cv2
from app.security import hash_password

(TMP/"unggahan"/"uji"/"p1").mkdir(parents=True)
(TMP/"datasets").mkdir()
(TMP/"users.json").write_text(json.dumps(
    {"uji": {"hash": hash_password("s1"), "nama": "uji", "admin": True}}))
d = TMP/"unggahan"/"uji"/"p1"
rng = np.random.default_rng(1)
for i in range(6):
    im = rng.integers(60,200,(240,320,3),dtype=np.uint8)
    cv2.rectangle(im,(80,60),(240,180),(40,180,70),-1)
    p = d/f"g{i}.jpg"; cv2.imwrite(str(p), im)
    p.with_suffix(".json").write_text(json.dumps({
        "version":"0.4.36","flags":{},
        "shapes":[{"label":"a","shape_type":"polygon",
                   "points":[[80,60],[240,60],[240,180],[80,180]]}],
        "imagePath":p.name,"imageData":None,"imageHeight":240,"imageWidth":320}))

srv = subprocess.Popen([".venv/bin/python","run.py","--host","127.0.0.1",
    "--port",str(PORT),"--users",str(TMP/"users.json"),
    "--datasets-root",str(TMP/"datasets"),"--uploads-root",str(TMP/"unggahan")],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    env={**os.environ, "LABELAPP_DEV_AUTOLOGIN": ""})
for _ in range(60):
    try: urllib.request.urlopen(f"http://127.0.0.1:{PORT}/login", timeout=1); break
    except Exception: time.sleep(0.5)

chrome = subprocess.Popen(["google-chrome","--headless=new","--disable-gpu",
    "--no-sandbox",f"--remote-debugging-port={CDP}",
    f"--user-data-dir={TMP/'chrome'}","about:blank"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ws_url = None
for _ in range(60):
    try:
        j = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json", timeout=1))
        t = [x for x in j if x["type"]=="page"]
        if t: ws_url = t[0]["webSocketDebuggerUrl"]; break
    except Exception: pass
    time.sleep(0.5)
assert ws_url, "chrome tidak menyala"

gagal = 0
def cek(nama, ok, detail=""):
    global gagal
    print(f"  {'OK  ' if ok else 'GAGAL'} {nama}" + (f"  [{detail}]" if detail else ""))
    if not ok: gagal += 1

with connect(ws_url, max_size=None) as ws:
    dd = Cdp(ws)
    dd.kirim("Runtime.enable"); dd.kirim("Page.enable")
    dd.kirim("Page.navigate", url=f"http://127.0.0.1:{PORT}/login"); time.sleep(1.5)
    dd.js("fetch('/login',{method:'POST',headers:{'Content-Type':"
          "'application/x-www-form-urlencoded'},body:'user=uji&pw=s1'})", tunggu=True)
    time.sleep(1)
    dd.js(f"fetch('/setsrc?path={d}',{{method:'POST'}})", tunggu=True); time.sleep(1)
    dd.kirim("Page.navigate", url=f"http://127.0.0.1:{PORT}/versi?ds=p1"); time.sleep(2.5)

    dd.js("document.getElementById('vs-mulai').click()"); time.sleep(2.5)
    dd.js("document.querySelector('[data-langkah=\"4\"] .wz-ubah').click()"); time.sleep(0.8)

    n_bentuk = dd.js("document.querySelectorAll('#wz-aug .op-baris').length")
    cek("mode bentuk: daftar langkah terisi", n_bentuk >= 10, f"{n_bentuk} langkah")

    RONA = ['hue_sat','blackbody','iluminan','color_jitter','grayscale','saturasi']
    dd.js("document.querySelector('input[name=\"wz-warna\"][value=\"warna\"]').click()")
    time.sleep(1.0)
    n_warna = dd.js("document.querySelectorAll('#wz-aug .op-baris').length")
    cek("memilih 'warna' MENGURANGI jumlah langkah",
        n_warna < n_bentuk, f"{n_bentuk} -> {n_warna}")

    teks = dd.js("document.getElementById('wz-aug').innerText.toLowerCase()") or ""
    for nm, label in (("hue_sat","Hue / saturasi"), ("iluminan","Lampu berwarna"),
                      ("blackbody","Suhu warna"), ("color_jitter","Color jitter"),
                      ("grayscale","Grayscale")):
        cek(f"langkah '{label}' hilang dari daftar", label.lower() not in teks)

    cek("keterangan menyebut jumlah yang dimatikan",
        "dimatikan" in (dd.js("document.getElementById('wz-mode-ket').textContent") or ""))

    # Inilah yang paling menentukan: apa yang benar-benar AKAN DIKIRIM.
    dd.js("window.__resep = null;"
          "const _f = window.fetch;"
          "window.fetch = (u, o) => { if (String(u).includes('/api/versi/estimasi'))"
          " { try { window.__resep = JSON.parse(o.body).resep; } catch(e){} }"
          " return _f(u, o); };")
    dd.js("document.querySelector('[data-langkah=\"4\"] [data-lanjut]').click()")
    time.sleep(2.5)
    r = dd.js("window.__resep && JSON.stringify(window.__resep)")
    cek("resep tertangkap saat dikirim", bool(r))
    if r:
        resep = json.loads(r)
        cek("resep membawa mode warna",
            (resep.get('warna') or {}).get('mode') == 'warna',
            str(resep.get('warna')))
        mati = [o for o in RONA if (resep.get('aug') or {}).get(o, {}).get('aktif') is False]
        cek("keenam langkah rona dikirim dalam keadaan MATI",
            len(mati) == 6, f"{len(mati)}/6: {mati}")

    # Kembali ke bentuk: harus pulih
    dd.js("document.querySelector('[data-langkah=\"4\"] .wz-ubah').click()"); time.sleep(0.6)
    dd.js("document.querySelector('input[name=\"wz-warna\"][value=\"bentuk\"]').click()")
    time.sleep(1.0)
    n2 = dd.js("document.querySelectorAll('#wz-aug .op-baris').length")
    cek("kembali ke 'bentuk' memulihkan langkahnya", n2 == n_bentuk, f"{n_warna} -> {n2}")

for p in (chrome, srv):
    p.terminate()
    try: p.wait(5)
    except Exception: p.kill()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n  GAGAL: {gagal}")
sys.exit(1 if gagal else 0)
