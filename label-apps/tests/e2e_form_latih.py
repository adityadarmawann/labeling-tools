"""
Uji end-to-end form training: tombol pembuka dan panel warna, lewat Chrome+CDP.

Bukan bagian dari `pytest` (namanya sengaja tidak diawali test_) karena butuh
google-chrome terpasang. Jalankan sendiri:

    .venv/bin/python tests/e2e_form_latih.py

DUA CACAT YANG DIJAGA DI SINI, keduanya hanya ada di layar.

1. "+ Training baru" dipakai sebagai toggle, jadi ia TETAP TERLIHAT selagi
   formnya terbuka — padahal form itu sudah punya "Batal" sendiri. Dua tombol
   yang artinya bertabrakan, dan yang satu masih mengajak membuat training
   baru padahal orangnya sudah ada di dalamnya.

2. Panel sinkronisasi warna memberi centang HIJAU pada mode yang cocok dengan
   versinya dan seru ORANYE pada yang tidak. Secara logika itu cuma menandai
   kecocokan. Tetapi versi hampir selalu dibuat dengan bawaan "bentuk", jadi
   dalam praktiknya: begitu orang memilih "Warna ikut menentukan", seluruh
   panel berubah oranye. Yang terbaca bukan "versi ini tidak menyimpan warna"
   melainkan "pilihanmu salah" — padahal di model industri keduanya sah, ada
   yang memang spesifik warna dan ada yang general.

   Yang diuji: keempat kombinasi (versi bentuk/warna x pilihan bentuk/warna)
   harus berlatar SAMA. Satu-satunya aksen yang tersisa adalah catatan untuk
   kombinasi yang datanya memang tidak mendukung, dan ia muncul TEPAT SEKALI
   dari empat.
"""
import base64, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, "."); sys.path.insert(0, "tests")
import urllib.request
from websockets.sync.client import connect
from e2e_kanvas import Cdp
import numpy as np, cv2
from app.security import hash_password

TMP = Path(tempfile.mkdtemp(prefix="e2e-form-")); PORT, CDP = 8054, 9335
(TMP/"unggahan"/"uji"/"p1").mkdir(parents=True); (TMP/"datasets").mkdir()
(TMP/"users.json").write_text(json.dumps({"uji":{"hash":hash_password("s1"),"nama":"uji","admin":True}}))
d = TMP/"unggahan"/"uji"/"p1"
for i in range(3):
    cv2.imwrite(str(d/f"g{i}.jpg"), np.full((100,120,3),120,np.uint8))

# dua versi: v1 dibangun mode BENTUK (rona diacak), v2 mode WARNA
(d/".versi").mkdir()
for n, mode in ((1, "bentuk"), (2, "warna")):
    (d/".versi"/f"v{n}").mkdir()
    (d/".versi"/f"v{n}"/"data.yaml").write_text("nc: 2\n")
    (d/".versi"/f"v{n}.json").write_text(json.dumps({
        "nomor": n, "dibuat": "2026-09-11 10:00", "catatan": f"uji {mode}",
        "resep": {"warna": {"mode": mode},
                  "aug": {} if mode == "bentuk"
                         else {o: {"aktif": False} for o in
                               ("hue_sat","blackbody","iluminan","color_jitter",
                                "grayscale","saturasi")}},
        "hasil": {"jumlah": {"train": 10, "val": 2}, "jenis": "segment",
                  "per_kelas": {"a": 5, "b": 5}, "negatif": 0}}))

srv = subprocess.Popen([".venv/bin/python","run.py","--host","127.0.0.1","--port",str(PORT),
  "--users",str(TMP/"users.json"),"--datasets-root",str(TMP/"datasets"),
  "--uploads-root",str(TMP/"unggahan")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
  env={**os.environ,"LABELAPP_DEV_AUTOLOGIN":""})
for _ in range(60):
    try: urllib.request.urlopen(f"http://127.0.0.1:{PORT}/login",timeout=1); break
    except Exception: time.sleep(0.5)
chrome = subprocess.Popen(["google-chrome","--headless=new","--disable-gpu","--no-sandbox",
  f"--remote-debugging-port={CDP}",f"--user-data-dir={TMP/'c'}","--window-size=1600,1000",
  "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ws_url=None
for _ in range(60):
    try:
        j=json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json",timeout=1))
        t=[x for x in j if x["type"]=="page"]
        if t: ws_url=t[0]["webSocketDebuggerUrl"]; break
    except Exception: pass
    time.sleep(0.5)

gagal = 0
def cek(nama, ok, detail=""):
    global gagal
    print(f"  {'OK  ' if ok else 'GAGAL'} {nama}" + (f"  [{detail}]" if detail else ""))
    if not ok: gagal += 1

def tampak(dd, sel):
    return dd.js(f"(() => {{const e=document.querySelector('{sel}');"
                 "if(!e) return null;"
                 "const r=e.getBoundingClientRect();"
                 "return r.width>0 && r.height>0;})()")

with connect(ws_url, max_size=None) as ws:
    dd=Cdp(ws); dd.kirim("Runtime.enable"); dd.kirim("Page.enable")
    dd.kirim("Page.navigate",url=f"http://127.0.0.1:{PORT}/login"); time.sleep(1.5)
    dd.js("fetch('/login',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'user=uji&pw=s1'})",tunggu=True); time.sleep(1)
    dd.js(f"fetch('/setsrc?path={d}',{{method:'POST'}})",tunggu=True); time.sleep(1)
    dd.kirim("Page.navigate",url=f"http://127.0.0.1:{PORT}/latih?ds=p1"); time.sleep(3)

    # -- 1. tombol pembuka --------------------------------------------------
    cek("mula-mula tombol tampak, form tertutup",
        tampak(dd,'#tr-mulai') is True and tampak(dd,'#tr-form') is False)
    dd.js("document.getElementById('tr-mulai').click()"); time.sleep(0.8)
    cek("form terbuka MENYEMBUNYIKAN tombol pembuka",
        tampak(dd,'#tr-mulai') is False, "dua tombol yang bertabrakan artinya")
    cek("formnya sendiri terbuka", tampak(dd,'#tr-form') is True)
    cek("fokus pindah ke isian pertama",
        dd.js("document.activeElement.id") == 'tr-nama')

    # -- 2. panel warna: keempat kombinasi harus berlatar sama --------------
    latar, bercatat = set(), []
    for versi, mode in ((1,"bentuk"),(1,"warna"),(2,"bentuk"),(2,"warna")):
        dd.js(f"(() => {{const s=document.getElementById('tr-versi');"
              f"s.value='{versi}'; s.dispatchEvent(new Event('change'));}})()")
        time.sleep(0.5)
        dd.js(f"document.querySelector('input[name=\"tr-mode\"][value=\"{mode}\"]').click()")
        time.sleep(0.6)
        info = json.loads(dd.js("""JSON.stringify((() => {
          const b=document.getElementById('tr-warna');
          const cs=getComputedStyle(b);
          return {latar: cs.backgroundColor + '/' + cs.borderTopColor,
                  ikon: !!document.getElementById('tr-warna-ikon'),
                  catat: !document.getElementById('tr-warna-catat').hidden,
                  nilai: !document.getElementById('tr-warna-nilai').hidden};
        })())"""))
        latar.add(info["latar"])
        if info["catat"]: bercatat.append(f"v{versi}+{mode}")
        cek(f"v{versi} + '{mode}': tidak ada lingkaran ceklis/seru",
            info["ikon"] is False)
        # Dua paragraf yang mengatakan hal yang sama membuat panelnya terbaca
        # seperti dokumen; saat catatannya muncul, baris angkanya mundur.
        cek(f"v{versi} + '{mode}': keterangan tidak berputar",
            not (info["catat"] and info["nilai"]))

    cek("keempat kombinasi berlatar SAMA", len(latar) == 1,
        " | ".join(sorted(latar)))
    cek("catatan muncul TEPAT untuk kombinasi yang mustahil saja",
        bercatat == ["v1+warna"], str(bercatat))

    # -- Batal mengembalikan keadaan awal -----------------------------------
    dd.js("document.getElementById('tr-tutup').click()"); time.sleep(0.8)
    cek("Batal memunculkan kembali tombol pembuka",
        tampak(dd,'#tr-mulai') is True and tampak(dd,'#tr-form') is False)
    cek("fokus kembali ke tombol pembuka, tidak hilang",
        dd.js("document.activeElement.id") == 'tr-mulai')

for p in (chrome,srv):
    p.terminate()
    try: p.wait(5)
    except Exception: p.kill()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n  GAGAL: {gagal}")
sys.exit(1 if gagal else 0)
