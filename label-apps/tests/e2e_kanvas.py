"""
Uji end-to-end kanvas lewat Chrome + CDP.

Bukan bagian dari `pytest` (namanya sengaja tidak diawali test_) karena
butuh google-chrome terpasang. Jalankan sendiri:

    .venv/bin/python tests/e2e_kanvas.py

Menguji empat perilaku baru dengan peristiwa mouse sungguhan, bukan dengan
memanggil fungsinya langsung — supaya urutan penanganan klik ikut teruji.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from websockets.sync.client import connect

APP = Path(__file__).resolve().parent.parent
PY = APP / ".venv/bin/python"
# Sengaja DI LUAR folder aplikasi: tes tidak boleh meninggalkan apa pun di
# dalam repo (lihat aturan di conftest.py).
TMP = Path(tempfile.gettempdir()) / "labelapp_e2e"
PORT, CDP = 8044, 9333

lolos, gagal = [], []

# Blok mana yang dijalankan. Tanpa argumen: semuanya. Dengan argumen, misalnya
#     .venv/bin/python tests/e2e_kanvas.py panel kanvas
# hanya blok itu — seluruh berkas ini butuh beberapa menit, dan menunggu semuanya
# hanya untuk memeriksa satu blok membuat orang berhenti menjalankannya.
BLOK = set(sys.argv[1:])


def cek(nama, syarat, detail=""):
    (lolos if syarat else gagal).append(nama)
    print(f"  {'OK  ' if syarat else 'GAGAL'} {nama}{'  ' + detail if detail else ''}")


# ---------------------------------------------------------------- siapkan
def siapkan():
    if TMP.exists():
        shutil.rmtree(TMP)
    ds = TMP / "datasets" / "uji"
    ds.mkdir(parents=True)
    # Autologin menuntut akunnya benar-benar ada di berkas akun (deps.py:75).
    sys.path.insert(0, str(APP))
    from app.security import hash_password
    (TMP / "users.json").write_text(json.dumps(
        {"devuser": {"hash": hash_password("sandi-uji-e2e-1"), "nama": "devuser"}}))
    im = np.full((60, 80, 3), 60, np.uint8)
    cv2.rectangle(im, (20, 15), (60, 45), (40, 200, 160), -1)
    ip = ds / "uji-00.jpg"
    cv2.imwrite(str(ip), im)
    # Poligon 4 titik yang seluruhnya jauh dari tepi, supaya tiap sisi dan
    # titiknya pasti berada di dalam area kanvas yang terlihat.
    (ds / "classes.txt").write_text("botol\nkaleng\nplastic-cup\n")
    ip.with_suffix(".json").write_text(json.dumps({
        "version": "0.4.36", "flags": {},
        "shapes": [{"label": "botol", "shape_type": "polygon",
                    "points": [[20, 15], [60, 15], [60, 45], [20, 45]]}],
        "imagePath": ip.name, "imageData": None,
        "imageHeight": 60, "imageWidth": 80,
    }))
    return ip


class Cdp:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    # Batas tunggu tiap perintah. Tanpa ini, satu dialog peramban yang menahan
    # halaman (confirm/alert/prompt) membuat Runtime.evaluate tidak pernah
    # dijawab, dan seluruh berkas uji menggantung sampai dibunuh dari luar —
    # tanpa satu baris pun yang memberi tahu perintah mana penyebabnya.
    BATAS_DETIK = 20

    def kirim(self, metode, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": metode, "params": params}))
        batas = time.time() + self.BATAS_DETIK
        while True:
            sisa = batas - time.time()
            if sisa <= 0:
                raise RuntimeError(
                    f"{metode} tidak dijawab dalam {self.BATAS_DETIK} detik — "
                    f"biasanya karena dialog peramban (confirm/alert) menahan "
                    f"halaman. params={str(params)[:120]}")
            try:
                pesan = json.loads(self.ws.recv(timeout=sisa))
            except TimeoutError:
                continue
            if pesan.get("id") == self.n:
                if "error" in pesan:
                    raise RuntimeError(f"{metode}: {pesan['error']}")
                return pesan.get("result", {})

    def js(self, ekspresi, tunggu=False):
        r = self.kirim("Runtime.evaluate", expression=ekspresi,
                       returnByValue=True, awaitPromise=tunggu)
        hasil = r.get("result", {})
        if r.get("exceptionDetails"):
            raise RuntimeError(f"JS: {r['exceptionDetails'].get('text')} :: {ekspresi[:80]}")
        return hasil.get("value")

    def mouse(self, tipe, x, y, modifier=0, tombol="left", klik=1):
        self.kirim("Input.dispatchMouseEvent", type=tipe, x=x, y=y,
                   button=tombol, buttons=1 if tipe == "mouseMoved" else 0,
                   clickCount=klik, modifiers=modifier)

    def klik(self, x, y, modifier=0):
        """Klik tanpa gerakan: hover dulu supaya S.sisi/S.hover terisi."""
        self.kirim("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y,
                   button="none", buttons=0, modifiers=modifier)
        time.sleep(0.08)
        self.mouse("mousePressed", x, y, modifier)
        time.sleep(0.05)
        self.mouse("mouseReleased", x, y, modifier)
        time.sleep(0.12)

    def layar(self, gx, gy):
        """Koordinat gambar -> koordinat viewport, lewat fungsi aplikasi sendiri."""
        return self.js(f"(() => {{ const r = c.getBoundingClientRect();"
                       f" return [r.left + keLayarX({gx}), r.top + keLayarY({gy})]; }})()")


def main():
    ip = siapkan()
    env = {**os.environ,
           "LABELAPP_USERS_FILE": str(TMP / "users.json"),
           "LABELAPP_DATASETS_ROOT": str(TMP / "datasets"),
           "LABELAPP_UPLOADS_ROOT": str(TMP / "unggahan"),
           "LABELAPP_THUMB_ROOT": str(TMP / "thumb"),
           "LABELAPP_DEV_AUTOLOGIN": "devuser",
           "LABELAPP_HOST": "127.0.0.1", "LABELAPP_PORT": str(PORT)}
    server = subprocess.Popen(
        [str(PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(PORT), "--log-level", "warning"],
        cwd=str(APP), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    chrome = None
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/login", timeout=1)
                break
            except Exception:
                if server.poll() is not None:
                    raise RuntimeError(server.stderr.read().decode()[-2000:])
                time.sleep(0.25)
        else:
            raise RuntimeError("server tidak menyala")

        chrome = subprocess.Popen(
            ["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--remote-debugging-port={CDP}", f"--user-data-dir={TMP / 'chrome'}",
             "--window-size=1400,900", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                daftar = json.loads(urllib.request.urlopen(
                    f"http://127.0.0.1:{CDP}/json/list", timeout=1).read())
                sasaran = [t for t in daftar if t["type"] == "page"]
                if sasaran:
                    break
            except Exception:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("chrome tidak menyala")

        with connect(sasaran[0]["webSocketDebuggerUrl"], max_size=None) as ws:
            d = Cdp(ws)
            d.kirim("Page.enable")
            d.kirim("Runtime.enable")

            # Login lewat form sungguhan supaya cookie sesi terpasang; tanpa
            # cookie, tiap permintaan memulai sesi baru dan dataset yang baru
            # dipilih ikut hilang.
            d.kirim("Page.navigate", url=f"http://127.0.0.1:{PORT}/login")
            for _ in range(60):
                time.sleep(0.2)
                if d.js("document.readyState") == "complete":
                    break
            d.js("fetch('/login', {method:'POST', headers:"
                 "{'Content-Type':'application/x-www-form-urlencoded'},"
                 "body:'user=devuser&pw=sandi-uji-e2e-1'}).then(r=>r.status)",
                 tunggu=True)
            terpasang = d.js(f"fetch('/setsrc?path={TMP / 'datasets' / 'uji'}',"
                             f" {{method:'POST'}}).then(r=>r.json())", tunggu=True)
            if not (terpasang or {}).get("ok"):
                raise RuntimeError(f"setsrc gagal: {terpasang}")

            d.kirim("Page.navigate",
                    url=f"http://127.0.0.1:{PORT}/label?path={ip}")
            for _ in range(80):
                time.sleep(0.2)
                if d.js("typeof S !== 'undefined' && !!S.shapes"):
                    break
            else:
                raise RuntimeError(
                    "halaman label tidak siap\n"
                    f"  url      : {d.js('location.href')}\n"
                    f"  readyState: {d.js('document.readyState')}\n"
                    f"  typeof S : {d.js('typeof S')}\n"
                    f"  judul    : {d.js('document.title')}\n"
                    f"  body     : {d.js('document.body.innerText.slice(0,300)')}")
            time.sleep(0.6)

            jalankan(d, ip)
    finally:
        for p in (chrome, server):
            if p and p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    p.kill()

    print(f"\n  {len(lolos)} lolos, {len(gagal)} gagal")
    if gagal:
        print("  gagal:", ", ".join(gagal))
    return 1 if gagal else 0


def jalankan(d, ip):
    d.js("S.mode='edit'; setMode && setMode('edit');") if d.js("typeof setMode") == "function" \
        else d.kirim("Input.dispatchKeyEvent", type="keyDown", text="v", key="v")
    d.kirim("Input.dispatchKeyEvent", type="keyUp", key="v")
    time.sleep(0.2)
    cek("mode Sunting aktif", d.js("S.mode") == "edit", f"mode={d.js('S.mode')}")

    n0 = d.js("S.shapes[0].points.length")
    cek("poligon awal 4 titik", n0 == 4, f"n={n0}")

    # -------- 1. klik di sisi menyisipkan titik
    # Titik tengah sisi atas (20,15)-(60,15) = (40,15).
    x, y = d.layar(40, 15)
    d.kirim("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y,
            button="none", buttons=0)
    time.sleep(0.15)
    cek("hover di sisi mengisi S.sisi", d.js("!!S.sisi"), f"S.sisi={d.js('JSON.stringify(S.sisi)')}")
    d.klik(x, y)
    n1 = d.js("S.shapes[0].points.length")
    cek("klik di sisi menyisipkan titik", n1 == 5, f"{n0} -> {n1}")

    # -------- 2. Shift+klik pada titik menghapusnya
    tx, ty = d.js("[S.shapes[0].points[1][0], S.shapes[0].points[1][1]]")
    x, y = d.layar(tx, ty)
    d.klik(x, y, modifier=8)                      # 8 = Shift
    n2 = d.js("S.shapes[0].points.length")
    cek("Shift+klik pada titik menghapusnya", n2 == 4, f"{n1} -> {n2}")

    # -------- 3. klik ulang membatalkan pilihan
    d.js("S.terpilih=[]; S.sel=-1; render();")
    x, y = d.layar(40, 30)                        # di dalam poligon
    d.klik(x, y)
    pilih1 = d.js("JSON.stringify(S.terpilih)")
    cek("klik pertama memilih objek", pilih1 == "[0]", f"terpilih={pilih1}")
    d.klik(x, y)
    pilih2 = d.js("JSON.stringify(S.terpilih)")
    cek("klik ulang membatalkan pilihan", pilih2 == "[]", f"terpilih={pilih2}")

    # -------- 4. menyeret objek terpilih TIDAK membatalkan pilihan
    d.klik(x, y)                                   # pilih lagi
    sebelum = d.js("JSON.stringify(S.shapes[0].points)")
    x2, y2 = d.layar(46, 30)
    d.kirim("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y,
            button="none", buttons=0)
    d.mouse("mousePressed", x, y)
    time.sleep(0.05)
    d.mouse("mouseMoved", x2, y2)
    time.sleep(0.05)
    d.mouse("mouseReleased", x2, y2)
    time.sleep(0.2)
    pilih3 = d.js("JSON.stringify(S.terpilih)")
    sesudah = d.js("JSON.stringify(S.shapes[0].points)")
    cek("menyeret objek tetap mempertahankan pilihan", pilih3 == "[0]", f"terpilih={pilih3}")
    cek("menyeret objek benar-benar memindahkannya", sebelum != sesudah)

    # -------- 5. autosave menulis ke disk tanpa Ctrl+S
    jp = ip.with_suffix(".json")
    d.js("S.kotor=false;")
    isi_awal = json.loads(jp.read_text())
    d.js("simpanUndo(); S.shapes[0].points[0][0] += 3; tandaiKotor(); render();")
    for _ in range(30):
        time.sleep(0.2)
        if not d.js("S.kotor"):
            break
    isi_baru = json.loads(jp.read_text())
    cek("autosave menulis ke disk tanpa Ctrl+S",
        isi_awal["shapes"][0]["points"] != isi_baru["shapes"][0]["points"],
        f"kotor={d.js('S.kotor')}")

    # -------- 6. objek tanpa kelas tidak memicu autosave
    d.js("S.shapes.push({label:'', shape_type:'polygon',"
         " points:[[5,5],[15,5],[15,15]], flags:{}, titipan:{}}); tandaiKotor();")
    time.sleep(1.2)
    cek("objek tanpa kelas tidak ikut tersimpan", d.js("S.kotor") is True,
        f"kotor={d.js('S.kotor')}")
    cek("berkas tidak berubah saat ada objek tanpa kelas",
        len(json.loads(jp.read_text())["shapes"]) == len(isi_baru["shapes"]))
    d.js("S.shapes.pop(); S.kotor=false; render();")

    for nama, fn in (("bentuk", lambda: jalankan_bentuk(d, jp)),
                     ("kelas", lambda: jalankan_kelas(d)),
                     ("dialog", lambda: jalankan_dialog(d)),
                     ("kanvas", lambda: jalankan_kanvas(d)),
                     ("panel", lambda: jalankan_panel(d))):
        if BLOK and nama not in BLOK:
            continue
        fn()


def jalankan_bentuk(d, jp):
    """Enam tipe bentuk, salin-tempel, grup, dan seret klik kanan."""
    print("  -- tipe bentuk --")
    # Dialog kelas dimatikan untuk blok ini — setara display_label_popup: false
    # di AnyLabeling, sehingga bentuk langsung memakai kelas panel. Dialognya
    # sendiri diuji terpisah di jalankan_dialog().
    d.js("S.v.tanyaKelas = false; S.label = S.kelas[0] || 'botol';"
         " S.shapes.length = 0; S.terpilih=[]; S.sel=-1; S.kotor=false; render();")

    # -------- point: satu klik satu objek
    d.js("setMode('point')")
    x, y = d.layar(30, 20)
    d.klik(x, y)
    cek("point: 1 klik jadi 1 objek",
        d.js("S.shapes.length") == 1 and d.js("S.shapes[0].shape_type") == "point"
        and d.js("S.shapes[0].points.length") == 1,
        f"{d.js('JSON.stringify(S.shapes.map(s=>s.shape_type))')}")

    # -------- line: tepat 2 klik
    d.js("setMode('line')")
    d.klik(*d.layar(20, 40))
    d.klik(*d.layar(60, 45))
    cek("line: 2 klik menutup bentuk",
        d.js("S.shapes.length") == 2 and d.js("S.shapes[1].shape_type") == "line"
        and d.js("S.shapes[1].points.length") == 2)

    # -------- linestrip: Ctrl+klik mengakhiri
    d.js("setMode('linestrip')")
    d.klik(*d.layar(15, 50))
    d.klik(*d.layar(35, 52))
    d.klik(*d.layar(55, 50), modifier=2)          # 2 = Ctrl
    cek("linestrip: Ctrl+klik mengakhiri",
        d.js("S.shapes.length") == 3 and d.js("S.shapes[2].shape_type") == "linestrip"
        and d.js("S.shapes[2].points.length") == 3,
        f"n={d.js('S.shapes.length')} titik={d.js('S.shapes[2] && S.shapes[2].points.length')}")

    # -------- circle: seret dari pusat ke tepi
    d.js("setMode('circle')")
    x0, y0 = d.layar(40, 30)
    x1, y1 = d.layar(52, 30)
    d.kirim("Input.dispatchMouseEvent", type="mouseMoved", x=x0, y=y0,
            button="none", buttons=0)
    d.mouse("mousePressed", x0, y0)
    time.sleep(0.05)
    d.mouse("mouseMoved", x1, y1)
    time.sleep(0.05)
    d.mouse("mouseReleased", x1, y1)
    time.sleep(0.2)
    cek("circle: seret pusat->tepi jadi 2 titik",
        d.js("S.shapes.length") == 4 and d.js("S.shapes[3].shape_type") == "circle"
        and d.js("S.shapes[3].points.length") == 2,
        f"n={d.js('S.shapes.length')}")

    # -------- circle dipilih dengan klik di dalam lingkarannya
    d.js("setMode('edit'); S.terpilih=[]; S.sel=-1; render();")
    d.klik(*d.layar(44, 30))
    cek("circle bisa dipilih dari dalam lingkarannya",
        d.js("S.terpilih.includes(3)"), f"terpilih={d.js('JSON.stringify(S.terpilih)')}")

    # -------- bulat-balik ke server: enam tipe tersimpan utuh
    d.js("S.shapes.forEach(s => s.label = 'botol'); S.kotor=true;")
    d.js("simpan(true)", tunggu=True)
    time.sleep(0.6)
    tersimpan = json.loads(jp.read_text())["shapes"]
    jenis = [s["shape_type"] for s in tersimpan]
    ntitik = {s["shape_type"]: len(s["points"]) for s in tersimpan}
    cek("empat tipe baru tersimpan ke berkas",
        jenis == ["point", "line", "linestrip", "circle"], f"{jenis}")
    cek("jumlah titik tiap tipe sesuai konvensi labelme",
        ntitik.get("point") == 1 and ntitik.get("line") == 2
        and ntitik.get("circle") == 2 and ntitik.get("linestrip") == 3, f"{ntitik}")

    print("  -- salin, grup, seret klik kanan --")
    # -------- Ctrl+C / Ctrl+V
    d.js("S.terpilih=[0]; S.sel=0; salinTerpilih(); tempel();")
    cek("salin lalu tempel menambah objek", d.js("S.shapes.length") == 5,
        f"n={d.js('S.shapes.length')}")

    # -------- grup
    d.js("S.terpilih=[0,1]; grupTerpilih();")
    gid = d.js("S.shapes[0].group_id")
    cek("G memberi group_id yang sama", gid is not None
        and d.js("S.shapes[1].group_id") == gid, f"gid={gid}")
    d.js("lepasGrupTerpilih();")
    cek("U melepas group_id", d.js("S.shapes[0].group_id") is None)

    # -------- seret klik kanan = duplikat-dan-pindah
    d.js("S.shapes.length=1; S.terpilih=[0]; S.sel=0; setMode('edit'); render();")
    n_awal = d.js("S.shapes.length")
    xa, ya = d.layar(30, 20)
    xb, yb = d.layar(50, 35)
    d.kirim("Input.dispatchMouseEvent", type="mouseMoved", x=xa, y=ya,
            button="none", buttons=0)
    d.mouse("mousePressed", xa, ya, tombol="right")
    time.sleep(0.05)
    d.kirim("Input.dispatchMouseEvent", type="mouseMoved", x=xb, y=yb,
            button="right", buttons=2)
    time.sleep(0.1)
    cek("seret klik kanan membuat bayangan salinan", d.js("!!S.salinanSeret"))
    d.mouse("mouseReleased", xb, yb, tombol="right")
    time.sleep(0.25)
    menu = d.js("[...document.querySelectorAll('#ctx button')].map(b=>b.textContent)")
    cek("menu kedua hanya berisi Salin/Pindahkan ke sini",
        menu == ["Salin ke sini", "Pindahkan ke sini"], f"{menu}")
    d.js("[...document.querySelectorAll('#ctx button')]"
         ".find(b=>b.textContent==='Salin ke sini').click()")
    time.sleep(0.2)
    cek("memilih 'Salin ke sini' menambah objek",
        d.js("S.shapes.length") == n_awal + 1,
        f"{n_awal} -> {d.js('S.shapes.length')}")

    print("  -- kecerahan & urut objek --")
    d.js("el('cerah').value = 100; el('cerah').oninput();")
    cek("slider kecerahan mengubah faktor", abs(d.js("S.cerah") - 2.0) < 1e-6,
        f"cerah={d.js('S.cerah')}")
    d.js("el('btn-reset-cerah').click();")
    cek("tombol kembalikan normal mengembalikan ke 1", d.js("S.cerah") == 1)

    d.js("S.shapes.length=0;"
         "['a','b','c'].forEach((n,i)=>S.shapes.push({label:n,shape_type:'polygon',"
         "points:[[5+i,5],[20+i,5],[20+i,20]],text:'',group_id:null,flags:{},titipan:{}}));"
         "render();")
    d.js("pindahkanObjek(0, 2);")
    urut = d.js("JSON.stringify(S.shapes.map(s=>s.label))")
    cek("urutan objek bisa dipindah", urut == '["b","c","a"]', f"{urut}")


def jalankan_kelas(d):
    """Penjaga salah ketik nama kelas."""
    print("  -- penjaga nama kelas --")
    resmi = d.js("JSON.stringify(D.kelas_resmi)")
    cek("daftar kelas resmi sampai ke kanvas",
        resmi == '["botol","kaleng","plastic-cup"]', f"{resmi}")

    def ketik(v, enter=2):
        d.js("el('kelasbaru').focus(); el('kelasbaru').value='';")
        d.js(f"el('kelasbaru').value={v!r};"
             "el('kelasbaru').dispatchEvent(new Event('input',{bubbles:true}));")
        for _ in range(enter):
            d.js("el('kelasbaru').dispatchEvent(new KeyboardEvent('keydown',"
                 "{key:'Enter',bubbles:true}));")
        return d.js("JSON.stringify(S.kelas)")

    # salah ketik ditahan pada Enter pertama
    d.js("S.kelas = ['botol','kaleng','plastic-cup']; S.sel=-1; S.terpilih=[];")
    hasil = ketik("Botol", enter=1)
    cek("salah huruf besar DITAHAN di Enter pertama",
        "Botol" not in json.loads(hasil), hasil)
    cek("pesannya menyebut kelas yang mirip",
        "botol" in (d.js("el('t').textContent") or ""), d.js("el('t').textContent"))

    # Enter kedua = penegasan, kelas tetap bisa dibuat
    d.js("el('kelasbaru').dispatchEvent(new KeyboardEvent('keydown',"
         "{key:'Enter',bubbles:true}));")
    cek("Enter kedua tetap membolehkan (langkah disengaja)",
        "Botol" in json.loads(d.js("JSON.stringify(S.kelas)")))

    # kelas yang memang baru: tetap ditahan sekali karena ada daftar resmi
    d.js("S.kelas = ['botol','kaleng','plastic-cup'];")
    hasil = ketik("kardus", enter=1)
    cek("kelas benar-benar baru ditahan sekali", "kardus" not in json.loads(hasil), hasil)
    hasil = ketik("kardus", enter=2)
    cek("lalu bisa ditambahkan", "kardus" in json.loads(hasil), hasil)

    # kelas yang sudah ada: langsung dipakai tanpa ditahan
    d.js("S.kelas = ['botol','kaleng','plastic-cup'];")
    ketik("kaleng", enter=1)
    cek("kelas yang sudah ada langsung dipakai", d.js("S.label") == "kaleng",
        f"label={d.js('S.label')}")


def jalankan_dialog(d):
    """
    Dialog kelas — padanan label_dialog.py + new_shape (label_widget.py:1909).

    Diuji lewat peristiwa mouse dan papan tombol sungguhan, bukan dengan
    memanggil tanyaKelas() langsung, supaya alur "gambar dulu, dialog yang
    bertanya" ikut teruji apa adanya.
    """
    print("  -- dialog kelas --")
    TERBUKA = "!document.getElementById('dlg').hidden"
    TEKS = "document.getElementById('dlg-teks')"
    GRUP = "document.getElementById('dlg-grup')"

    def tunggu_dialog(terbuka=True, batas=40):
        for _ in range(batas):
            if bool(d.js(TERBUKA)) is terbuka:
                return True
            time.sleep(0.05)
        return False

    def tombol(nama):
        d.js("document.getElementById('dlg-" + nama + "').click()")
        time.sleep(0.2)

    def gambar_poligon():
        d.js("S.v.tanyaKelas = true; S.label = ''; S.draft = null;"
             " S.shapes.length = 0; S.terpilih = []; S.sel = -1;"
             " S.kotor = false; setMode('poly'); render();")
        for gx, gy in ((10, 10), (60, 12), (58, 48)):
            d.klik(*d.layar(gx, gy))
        for tipe in ("rawKeyDown", "keyUp"):
            d.kirim("Input.dispatchKeyEvent", type=tipe, key="Enter",
                    windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
        time.sleep(0.2)

    # -------- dialog muncul walau tidak ada kelas yang dipilih lebih dulu
    gambar_poligon()
    cek("bentuk selesai memunculkan dialog kelas", tunggu_dialog(True))
    cek("bentuknya belum masuk sebelum dialog dijawab",
        d.js("S.shapes.length") == 0, "n=%s" % d.js("S.shapes.length"))

    # -------- isi kelas + group id, lalu Simpan
    d.js(TEKS + ".value = 'kaleng'; " + GRUP + ".value = '7';")
    tombol("ok")
    cek("Simpan membuat objek dengan kelas dari dialog",
        d.js("S.shapes.length") == 1 and d.js("S.shapes[0].label") == "kaleng",
        "n=%s label=%s" % (d.js("S.shapes.length"),
                           d.js("S.shapes[0] && S.shapes[0].label")))
    cek("group id dari dialog ikut tersimpan",
        d.js("S.shapes[0].group_id") == 7,
        "group_id=%s" % d.js("S.shapes[0] && S.shapes[0].group_id"))
    cek("dialog tertutup setelah Simpan", tunggu_dialog(False))

    # -------- Batal membuang bentuknya (undo_last_line, label_widget.py:1961)
    gambar_poligon()
    tunggu_dialog(True)
    tombol("batal")
    cek("Batal membuang bentuknya, tidak menyimpannya",
        d.js("S.shapes.length") == 0, "n=%s" % d.js("S.shapes.length"))

    # -------- Escape sama dengan Batal, dan tidak bocor ke pintasan kanvas
    gambar_poligon()
    tunggu_dialog(True)
    for tipe in ("rawKeyDown", "keyUp"):
        d.kirim("Input.dispatchKeyEvent", type=tipe, key="Escape",
                windowsVirtualKeyCode=27, nativeVirtualKeyCode=27)
    time.sleep(0.25)
    cek("Escape menutup dialog dan membuang bentuknya",
        not d.js(TERBUKA) and d.js("S.shapes.length") == 0,
        "terbuka=%s n=%s" % (d.js(TERBUKA), d.js("S.shapes.length")))

    # -------- nama satu huruf ditolak validator ^[^ \t].+
    gambar_poligon()
    tunggu_dialog(True)
    d.js(TEKS + ".value = 'x';")
    tombol("ok")
    galat = d.js("document.getElementById('dlg-galat').textContent")
    cek("nama satu huruf ditolak dialog", bool(d.js(TERBUKA)) and bool(galat),
        "galat=%s" % galat)
    tombol("batal")

    # -------- Ctrl+E memakai dialog yang sama, terisi nilai objeknya
    d.js("S.shapes.length = 0;"
         " S.shapes.push({label:'botol', shape_type:'polygon',"
         " points:[[5,5],[40,5],[40,40]], text:'', group_id:3, flags:{},"
         " titipan:{}}); S.sel = 0; S.terpilih = [0]; setMode('edit'); render();")
    d.js("ubahKelasTerpilih()")
    cek("Ctrl+E membuka dialog yang sama", tunggu_dialog(True))
    cek("dialog terisi kelas dan group id objeknya",
        d.js(TEKS + ".value") == "botol" and d.js(GRUP + ".value") == "3",
        "teks=%s grup=%s" % (d.js(TEKS + ".value"), d.js(GRUP + ".value")))
    tombol("batal")

    # -------- kelas resmi data.yaml tampil di panel walau belum terpakai
    nama_panel = ("[...document.querySelectorAll('#kelas .kelas span')]"
                  ".map(x => x.textContent)")
    cek("kelas resmi dataset tampil di panel Labels",
        bool(d.js(nama_panel + ".includes('plastic-cup')")),
        d.js("JSON.stringify(" + nama_panel + ")"))

    # -------- klik kelas di panel TIDAK mengubah objek yang sedang terpilih
    d.js("S.shapes.length = 0;"
         " S.shapes.push({label:'botol', shape_type:'polygon',"
         " points:[[5,5],[40,5],[40,40]], text:'', group_id:null, flags:{},"
         " titipan:{}}); S.sel = 0; S.terpilih = [0]; S.label = ''; render();")
    klik_kelas = ("[...document.querySelectorAll('#kelas .kelas')]"
                  ".find(x => x.textContent.trim() === 'kaleng').click()")
    d.js(klik_kelas)
    time.sleep(0.15)
    cek("memilih kelas di panel tidak melabeli ulang objek terpilih",
        d.js("S.shapes[0].label") == "botol" and d.js("S.label") == "kaleng",
        "label objek=%s kelas aktif=%s" % (d.js("S.shapes[0].label"), d.js("S.label")))
    d.js(klik_kelas)
    time.sleep(0.15)
    cek("klik ulang kelas yang sama melepas pilihannya", d.js("S.label") == "",
        "kelas aktif=%r" % d.js("S.label"))
def jalankan_kanvas(d):
    """Paritas kanvas: roda, Ctrl+Z saat menggambar, klik ganda, panah."""
    print("  -- paritas kanvas --")

    def roda(dy, ctrl=False):
        x, y = d.layar(40, 30)
        d.kirim("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y,
                deltaX=0, deltaY=dy, modifiers=2 if ctrl else 0)
        time.sleep(0.2)

    # -------- Ctrl+roda memperbesar, roda polos menggeser
    d.js("S.v.tanyaKelas = false; S.label = 'botol'; S.draft = null;"
         " S.shapes.length = 0; S.terpilih = []; S.sel = -1;"
         " setMode('edit'); muatKeLayar(); render();")
    z0, px0 = d.js("S.zoom"), d.js("S.panx")
    roda(-120, ctrl=True)
    cek("Ctrl+roda memperbesar", d.js("S.zoom") > z0,
        "%.3f -> %.3f" % (z0, d.js("S.zoom")))

    d.js("muatKeLayar();")
    z1, py1 = d.js("S.zoom"), d.js("S.pany")
    roda(120, ctrl=False)
    cek("roda polos menggeser, bukan memperbesar",
        abs(d.js("S.zoom") - z1) < 1e-9 and d.js("S.pany") != py1,
        "zoom %.3f pany %.1f -> %.1f" % (d.js("S.zoom"), py1, d.js("S.pany")))

    # -------- Ctrl+Z saat menggambar mencabut TITIK, bukan objek sebelumnya
    d.js("S.shapes.length = 0;"
         " S.shapes.push({label:'botol', shape_type:'polygon',"
         " points:[[5,5],[40,5],[40,40]], text:'', group_id:null, flags:{},"
         " titipan:{}}); S.sel=-1; S.terpilih=[]; setMode('poly'); render();")
    for gx, gy in ((10, 60), (30, 62), (50, 60)):
        d.klik(*d.layar(gx, gy))
    cek("draft terisi 3 titik", d.js("S.draft && S.draft.points.length") == 3,
        "n=%s" % d.js("S.draft && S.draft.points.length"))
    d.kirim("Input.dispatchKeyEvent", type="rawKeyDown", key="z",
            windowsVirtualKeyCode=90, nativeVirtualKeyCode=90, modifiers=2)
    d.kirim("Input.dispatchKeyEvent", type="keyUp", key="z",
            windowsVirtualKeyCode=90, nativeVirtualKeyCode=90, modifiers=2)
    time.sleep(0.2)
    cek("Ctrl+Z saat menggambar mencabut titik terakhir",
        d.js("S.draft && S.draft.points.length") == 2,
        "n=%s" % d.js("S.draft && S.draft.points.length"))
    cek("objek yang sudah jadi TIDAK ikut hilang", d.js("S.shapes.length") == 1,
        "n=%s" % d.js("S.shapes.length"))

    # -------- klik ganda tidak menyisakan titik kembar
    d.js("S.draft = null; S.shapes.length = 0; setMode('poly'); render();")
    for gx, gy in ((10, 10), (60, 12), (58, 48)):
        d.klik(*d.layar(gx, gy))
    # Klik keempat memakai d.klik (jalur yang sama dengan tiga klik di atas),
    # lalu klik kedua dari pasangan klik-ganda dikirim dengan clickCount=2 —
    # persis urutan yang dihasilkan peramban saat orang mengklik dua kali.
    x, y = d.layar(20, 45)
    d.klik(x, y)
    d.mouse("mousePressed", x, y, klik=2)
    time.sleep(0.08)
    d.mouse("mouseReleased", x, y, klik=2)
    time.sleep(0.4)
    # Yang diuji adalah SIFATNYA, bukan jumlah titiknya: aliran klik sintetis CDP
    # tidak sama dengan peramban sungguhan (satu mousedown tidak terkirim, satu
    # lagi datang dengan detail=2), jadi menuntut angka persis berarti menguji
    # harness-nya, bukan aplikasinya. Yang penting: poligonnya tertutup, dan
    # tidak ada titik kembar berdempetan — cacat yang jadi alasan perbaikan ini.
    n_titik = d.js("S.shapes[0] && S.shapes[0].points.length")
    kembar = d.js("(function(){"
                  " const t = S.shapes[0] ? S.shapes[0].points : [];"
                  " for (let i = 1; i < t.length; i++)"
                  "   if (Math.hypot(t[i][0]-t[i-1][0], t[i][1]-t[i-1][1]) < 1e-6)"
                  "     return true;"
                  " return false; })()")
    cek("klik ganda menutup poligon tanpa titik kembar",
        d.js("S.shapes.length") == 1 and n_titik >= 3 and kembar is False,
        "n=%s titik=%s kembar=%s" % (d.js("S.shapes.length"), n_titik, kembar))

    # -------- panah menggeser SELURUH bentuk terpilih
    d.js("S.shapes.length = 0;"
         " S.shapes.push({label:'a', shape_type:'polygon',"
         " points:[[10,10],[30,10],[30,30]], text:'', group_id:null, flags:{}, titipan:{}});"
         " S.shapes.push({label:'b', shape_type:'polygon',"
         " points:[[50,50],[70,50],[70,70]], text:'', group_id:null, flags:{}, titipan:{}});"
         " S.terpilih=[0,1]; S.sel=0; setMode('edit'); render();")
    ax0 = d.js("S.shapes[0].points[0][0]")
    bx0 = d.js("S.shapes[1].points[0][0]")
    for tipe in ("rawKeyDown", "keyUp"):
        d.kirim("Input.dispatchKeyEvent", type=tipe, key="ArrowRight",
                windowsVirtualKeyCode=39, nativeVirtualKeyCode=39)
    time.sleep(0.25)
    cek("panah menggeser SEMUA objek terpilih",
        d.js("S.shapes[0].points[0][0]") > ax0
        and d.js("S.shapes[1].points[0][0]") > bx0,
        "a %.1f->%.1f  b %.1f->%.1f" % (ax0, d.js("S.shapes[0].points[0][0]"),
                                        bx0, d.js("S.shapes[1].points[0][0]")))

    # -------- beralih ke mode menggambar melepas pilihan
    d.js("setMode('poly')")
    cek("beralih menggambar melepas pilihan dan sorotan",
        d.js("S.terpilih.length") == 0 and d.js("S.sel") == -1
        and d.js("S.hover") is None,
        "terpilih=%s sel=%s" % (d.js("JSON.stringify(S.terpilih)"), d.js("S.sel")))
def jalankan_panel(d):
    """Paritas panel: keterlihatan, gulir ke terpilih, catatan dua tingkat."""
    print("  -- paritas panel --")

    d.js("S.v.tanyaKelas = false; S.label = 'botol'; S.draft = null;"
         " S.shapes.length = 0;"
         " S.shapes.push({label:'a', shape_type:'polygon',"
         " points:[[5,5],[40,5],[40,40]], text:'', group_id:null, flags:{}, titipan:{}});"
         " S.terpilih=[]; S.sel=-1; S.kotor=false; setMode('edit'); render();")

    # -------- bentuk tersembunyi tidak bisa disorot maupun dipilih
    x, y = d.layar(20, 15)
    d.kirim("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y,
            button="none", buttons=0)
    time.sleep(0.15)
    cek("bentuk terlihat bisa disorot", d.js("S.hover !== null"),
        "hover=%s" % d.js("JSON.stringify(S.hover)"))

    d.js("S.shapes[0].sembunyi = true; render();")
    d.kirim("Input.dispatchMouseEvent", type="mouseMoved", x=x + 3, y=y + 3,
            button="none", buttons=0)
    time.sleep(0.15)
    cek("bentuk tersembunyi TIDAK bisa disorot", d.js("S.hover") is None,
        "hover=%s" % d.js("JSON.stringify(S.hover)"))

    d.klik(x, y)
    cek("bentuk tersembunyi TIDAK bisa dipilih dan diseret",
        d.js("S.terpilih.length") == 0,
        "terpilih=%s" % d.js("JSON.stringify(S.terpilih)"))

    # -------- "Tampilkan semua objek" mengembalikan yang disembunyikan
    d.js("document.getElementById('v-tampilsemua').click()")
    time.sleep(0.2)
    cek("Tampilkan semua objek mengembalikan yang disembunyikan",
        d.js("!!S.shapes[0].sembunyi") is False,
        "sembunyi=%s" % d.js("S.shapes[0].sembunyi"))

    d.js("document.getElementById('v-sembunyisemua').click()")
    time.sleep(0.2)
    cek("Sembunyikan semua objek bekerja", d.js("S.shapes[0].sembunyi") is True,
        "sembunyi=%s" % d.js("S.shapes[0].sembunyi"))
    d.js("document.getElementById('v-tampilsemua').click()")
    time.sleep(0.15)

    # -------- daftar objek menggulir ke objek yang dipilih di kanvas
    d.js("S.shapes.length = 0;"
         " for (let i = 0; i < 40; i++)"
         "   S.shapes.push({label:'k'+i, shape_type:'polygon',"
         "     points:[[5,5],[40,5],[40,40]], text:'', group_id:null,"
         "     flags:{}, titipan:{}});"
         " S.terpilih=[]; S.sel=-1; render();")
    d.js("pilihBentuk(38, false); render();")
    time.sleep(0.25)
    terlihat = d.js("(function(){"
                    " const b = document.getElementById('objek');"
                    " const a = b.querySelector('.obj[data-on]');"
                    " if (!a) return 'tidak ada baris terpilih';"
                    " const rb = b.getBoundingClientRect(), ra = a.getBoundingClientRect();"
                    " return (ra.bottom > rb.top - 1 && ra.top < rb.bottom + 1)"
                    "        ? 'terlihat' : 'di luar layar'; })()")
    cek("daftar objek menggulir ke objek terpilih", terlihat == "terlihat",
        "hasil=%s" % terlihat)

    # -------- klik ganda di daftar objek membuka dialog ubah kelas
    d.js("S.v.tanyaKelas = true;")
    d.js("(function(){ const a = document.querySelectorAll('#objek .obj')[3];"
         " a.dispatchEvent(new MouseEvent('dblclick', {bubbles:true})); })()")
    time.sleep(0.3)
    cek("klik ganda di daftar objek membuka dialog kelas",
        bool(d.js("!document.getElementById('dlg').hidden")))
    d.js("document.getElementById('dlg-batal').click()")
    time.sleep(0.15)

    # -------- catatan tingkat gambar saat tidak ada objek terpilih
    d.js("S.sel = -1; S.terpilih = []; render();")
    cek("panel teks beralih ke catatan tingkat gambar",
        d.js("document.getElementById('teksjudul').textContent") == "Image Text"
        and d.js("document.getElementById('teks').disabled") is False,
        "judul=%s" % d.js("document.getElementById('teksjudul').textContent"))

    d.js("(function(){ const t = document.getElementById('teks');"
         " t.value = 'catatan untuk gambar'; t.dispatchEvent(new Event('input')); })()")
    time.sleep(0.15)
    cek("mengetik saat tak ada objek mengisi catatan GAMBAR",
        d.js("S.teksGambar") == "catatan untuk gambar"
        and d.js("S.shapes.every(s => !s.text)"),
        "teksGambar=%r" % d.js("S.teksGambar"))

    d.js("pilihBentuk(0, false); render();")
    cek("memilih objek mengembalikan panel ke catatan objek",
        d.js("document.getElementById('teksjudul').textContent") == "Object Text")

    # -------- tema: gelap benar-benar mengubah warna, dan tersimpan
    bg = lambda: d.js("getComputedStyle(document.body).backgroundColor")
    ink = lambda: d.js("getComputedStyle(document.body).color")
    d.js("pasangTema('light')"); time.sleep(0.15)
    bg_terang, ink_terang = bg(), ink()
    d.js("pasangTema('dark')"); time.sleep(0.15)
    bg_gelap, ink_gelap = bg(), ink()
    cek("tema gelap mengubah latar DAN warna teks",
        bg_gelap != bg_terang and ink_gelap != ink_terang,
        "latar %s -> %s | teks %s -> %s" % (bg_terang, bg_gelap, ink_terang, ink_gelap))
    cek("pilihan tema tersimpan",
        d.js("localStorage.getItem('labelapp_tema')") == "dark")
    cek("tema 'sistem' tidak menstempel apa pun",
        (d.js("pasangTema('system')") is None
         or True) and d.js("document.documentElement.dataset.theme") in (None, ""),
        "data-theme=%r" % d.js("document.documentElement.dataset.theme"))

    # Latar tidak boleh transparan di tema mana pun: halaman akan meminjam
    # warna dasar host dan teksnya bisa berakhir di atas latar tema lain.
    for t in ("light", "dark"):
        d.js(f"pasangTema('{t}')"); time.sleep(0.1)
        cek(f"latar body jelas di tema {t}",
            "rgba(0, 0, 0, 0)" not in bg() and bg() != "transparent", bg())
    d.js("pasangTema('system')")

    # -------- deteksi dari prompt teks
    # fetch dibonekakan: yang diuji plumbing antarmukanya, bukan modelnya —
    # menarik 641 MB di tengah tes bukan sesuatu yang boleh terjadi diam-diam.
    d.js("""
      window.__fetchAsli = window.fetch;
      window.fetch = function (u, o) {
        if (String(u).indexOf('/api/deteksi') === 0) {
          return Promise.resolve(new Response(JSON.stringify({
            ok: true, model: 'yoloworld:latest', n: 2, bentuk: [
              {label:'botol',  shape_type:'polygon',
               points:[[5,5],[30,5],[30,30]], skor:0.9},
              {label:'kaleng', shape_type:'rectangle',
               points:[[40,40],[70,70]], skor:0.8}]}),
            {status: 200, headers: {'Content-Type': 'application/json'}}));
        }
        return window.__fetchAsli(u, o);
      };
    """)
    # confirm/alert/prompt bawaan peramban MENAHAN halaman, dan CDP lalu menunggu
    # balasan Runtime.evaluate yang tidak akan pernah datang. Dimatikan di sini
    # supaya tes berakhir dengan pesan, bukan menggantung; batas 20 detik di
    # kirim() adalah jaring terakhirnya.
    d.js("window.confirm = () => true; window.alert = () => {};")
    # Bobot ditandai sudah ada, supaya yang diuji jalur deteksinya — bukan
    # peringatan unduhan.
    d.js("[...document.querySelectorAll('#teks-model option')]"
         ".forEach(o => o.dataset.terunduh = '1')")
    d.js("S.shapes.length = 0; S.terpilih = []; S.sel = -1; S.kotor = false;"
         " document.getElementById('teks-kelas').value = 'botol, kaleng';"
         " render();")
    d.js("document.getElementById('teks-jalan').click()")
    time.sleep(0.6)
    cek("deteksi teks menambahkan semua objek yang ditemukan",
        d.js("S.shapes.length") == 2
        and d.js("JSON.stringify(S.shapes.map(s => s.label))") == '["botol","kaleng"]',
        "n=%s label=%s" % (d.js("S.shapes.length"),
                           d.js("JSON.stringify(S.shapes.map(s => s.label))")))
    cek("tipe bentuk mengikuti yang dikembalikan model",
        d.js("JSON.stringify(S.shapes.map(s => s.shape_type))")
        == '["polygon","rectangle"]')
    cek("kelas baru dari hasil deteksi masuk daftar kelas",
        bool(d.js("S.kelas.includes('botol') && S.kelas.includes('kaleng')")))
    d.js("urungkan()")
    time.sleep(0.2)
    cek("satu Ctrl+Z membatalkan SELURUH deteksi",
        d.js("S.shapes.length") == 0, "n=%s" % d.js("S.shapes.length"))
    d.js("window.fetch = window.__fetchAsli;")



if __name__ == "__main__":
    sys.exit(main())
