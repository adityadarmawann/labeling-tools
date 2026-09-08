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


def terlihat_kosong(v):
    """'' berarti tidak ada tumpang tindih; string apa pun berarti ada."""
    return v == ""


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

    # Dua projek di ruang unggahan akun uji. Halaman projek hanya menampilkan
    # yang ada DI SITU — folder dataset bersama sengaja tidak bisa diganti nama
    # atau dibuang dari sana, jadi tanpa ini kartunya kosong dan
    # pemeriksaannya tidak menguji apa pun.
    for nama, warna in (("projek-satu", 90), ("projek-dua", 150)):
        pd = TMP / "unggahan" / "devuser" / nama
        pd.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            q = pd / f"{nama}-IMG_2026063{i}_08{i:02d}00.jpg"
            cv2.imwrite(str(q), np.full((60, 80, 3), warna + i * 12, np.uint8))
    ip.with_suffix(".json").write_text(json.dumps({
        "version": "0.4.36", "flags": {},
        "shapes": [{"label": "botol", "shape_type": "polygon",
                    "points": [[20, 15], [60, 15], [60, 45], [20, 45]]}],
        "imagePath": ip.name, "imageData": None,
        "imageHeight": 60, "imageWidth": 80,
    }))

    # Dataset kedua, khusus untuk sapuan halaman grid. Dibuat besar dan
    # bercampur dengan sengaja: dataset `uji` cuma satu gambar, dan pada satu
    # gambar hampir tiap saringan menghasilkan jawaban yang sama sehingga tidak
    # ada saringan yang benar-benar teruji. Di sini:
    #   0..69   berlabel (botol / kaleng bergantian)
    #   70..79  berlabel tetapi CACAT (poligon 3 titik) -> "perlu dicek"
    #   80..129 tanpa berkas anotasi sama sekali        -> "belum dilabeli"
    gd = TMP / "datasets" / "grid"
    gd.mkdir(parents=True, exist_ok=True)
    (gd / "classes.txt").write_text("botol\nkaleng\nplastic-cup\n")
    for i in range(130):
        q = gd / f"g-{i:03d}.jpg"
        cv2.imwrite(str(q), np.full((60, 80, 3), (30 + i * 7) % 240, np.uint8))
        if i >= 80:
            continue
        # 8 titik supaya TIDAK ditandai "hanya N titik": ambangnya di
        # scanner.inspect adalah 8, bukan 4 (poligon sesegi itu biasanya kotak
        # kasar, bukan bentuk objek). Yang 3 titik memang sengaja dibuat cacat.
        titik = ([[10, 10], [60, 10], [60, 45]] if i >= 70
                 else [[10, 10], [35, 8], [60, 10], [64, 27], [60, 45],
                       [35, 48], [10, 45], [6, 27]])
        q.with_suffix(".json").write_text(json.dumps({
            "version": "0.4.36", "flags": {},
            "shapes": [{"label": "botol" if i % 2 == 0 else "kaleng",
                        "shape_type": "polygon", "points": titik}],
            "imagePath": q.name, "imageData": None,
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
                     ("panel", lambda: jalankan_panel(d)),
                     ("kontrol", lambda: jalankan_kontrol(d)),
                     ("grid", lambda: jalankan_grid(d)),
                     ("potret", lambda: jalankan_potret(d))):
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
    # muatKeLayar() penting: uji roda di atas menggeser gambar, dan tanpa
    # dipaskan ulang titik pertama bisa jatuh DI LUAR kanvas — kliknya hilang
    # dan yang teruji tinggal tiga titik, bukan empat.
    d.js("S.draft = null; S.shapes.length = 0; setMode('poly');"
         " muatKeLayar(); render();")
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

    # -------- pintasan menahan aksi bawaan peramban
    #
    # Sebagian pintasan membuka dialog dan memindahkan fokus ke kotak isian di
    # dalamnya. Aksi bawaan keydown berjalan SESUDAH penangannya selesai, dan
    # yang menerimanya adalah elemen yang fokusnya baru saja pindah — jadi
    # menekan F mengisi kotak "Kelas untuk objek ini" dengan huruf "f", lalu
    # daftar kelas di bawahnya tersaring ke nama berawalan "f" sehingga tampak
    # kosong juga.
    #
    # Diperiksa lewat defaultPrevented, bukan lewat isi kotaknya: dialog
    # kelasnya menuntut pratinjau SAM, dan aturannya berlaku untuk SEMUA
    # pintasan — termasuk yang belum membuka dialog apa pun hari ini.
    d.js("setMode('edit'); S.draft = null; S.sel = -1; S.terpilih = [];")
    bocor = d.js("""(() => {
        const keluar = [];
        for (const k of ['q','e','r','p','v','g','u','f','c','a','d']) {
            const ev = new KeyboardEvent('keydown', {key: k, bubbles: true,
                                                     cancelable: true});
            window.dispatchEvent(ev);
            if (!ev.defaultPrevented) keluar.push(k);
        }
        return keluar.join(',');
    })()""")
    cek("pintasan huruf tidak bocor jadi ketikan", bocor == "", f"bocor: {bocor}")

    # Sebaliknya, huruf yang BUKAN pintasan harus dibiarkan lewat: menahan
    # semuanya membuat mengetik di halaman ini mustahil.
    lewat = d.js("""(() => {
        const ev = new KeyboardEvent('keydown', {key: 'z', bubbles: true,
                                                 cancelable: true});
        window.dispatchEvent(ev);
        return ev.defaultPrevented;
    })()""")
    cek("huruf yang bukan pintasan tetap lewat", lewat is False)

    # -------- panduan halaman grid
    #
    # Chip di grid menyebut keadaan yang tidak dijelaskan namanya sendiri, dan
    # "Perlu dicek" yang paling sering disalahpahami: orang menyangka ia
    # menilai gambar yang sengaja ditandai latar. Sebelum ada panduan di sini,
    # tidak ada satu pun tempat di layar yang bisa membantahnya.
    # S.kotor menahan navigasi dengan dialog "yakin mau keluar?", dan dialog
    # itu tidak pernah dijawab siapa pun di sini. Bentuk uji di atas memang
    # tidak perlu disimpan.
    d.js("S.kotor = false")
    d.kirim("Page.navigate", url=f"http://127.0.0.1:{PORT}/")
    time.sleep(1.2)
    cek("grid punya tombol panduan",
        d.js("!!document.getElementById('btn-panduan')"))
    d.js("document.getElementById('btn-panduan').click()")
    time.sleep(0.3)
    cek("panduan grid terbuka", d.js("!document.getElementById('panduan').hidden"))
    isi = d.js("document.getElementById('panduan-isi').innerText")
    cek("menjelaskan Perlu dicek", "mask sangat kecil" in isi and
        "mask memenuhi frame" in isi and "label kosong" in isi)
    cek("menyatakan latar tidak termasuk",
        "Gambar latar tidak pernah masuk sini" in isi)
    d.js("document.getElementById('panduan-kotak-cari').value='latar';"
         "document.getElementById('panduan-kotak-cari')"
         ".dispatchEvent(new Event('input'))")
    time.sleep(0.3)
    n = d.js("[...document.querySelectorAll('#panduan-isi dt')]"
             ".filter(x => !x.hidden).length")
    cek("kotak cari menyaring panduan grid", 0 < n < 19, f"{n} dari 19")
    d.js("document.getElementById('panduan-tutup').click()")
    time.sleep(0.2)
    cek("panduan grid tertutup", d.js("document.getElementById('panduan').hidden"))

    # Kembali ke kanvas: pemeriksaan sesudah ini melanjutkan di halaman itu,
    # dan meninggalkannya di grid membuat semuanya gagal dengan pesan yang
    # tidak menyebut sebabnya sama sekali.
    jalur = d.js("(document.querySelector('a[href^=\"/label?path=\"]')||{})"
                 ".getAttribute ? document.querySelector"
                 "('a[href^=\"/label?path=\"]').getAttribute('href') : ''")
    d.kirim("Page.navigate", url=f"http://127.0.0.1:{PORT}{jalur}")
    for _ in range(80):
        time.sleep(0.2)
        if d.js("typeof S !== 'undefined' && !!S.shapes"):
            break
    else:
        raise RuntimeError("gagal kembali ke kanvas sesudah panduan grid")
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

    # -------- panduan pintasan
    d.js("document.getElementById('btn-panduan').click()")
    time.sleep(0.25)
    cek("tombol Panduan membuka panduan",
        bool(d.js("!document.getElementById('panduan').hidden")))
    n = d.js("document.querySelectorAll('#panduan-isi dt').length")
    cek("panduan memuat seluruh pintasan", n >= 50, "jumlah=%s" % n)
    cek("bar atas tidak lagi memuat dinding teks pintasan",
        d.js("document.querySelectorAll('.lab-hint').length") == 0)

    d.js("(function(){ const c = document.getElementById('panduan-kotak-cari');"
         " c.value = 'grup'; c.dispatchEvent(new Event('input')); })()")
    time.sleep(0.2)
    terlihat = d.js("[...document.querySelectorAll('#panduan-isi dt')]"
                    ".filter(x => !x.hidden).length")
    cek("kotak cari menyaring pintasan", 0 < terlihat < n,
        "%s dari %s" % (terlihat, n))

    d.js("(function(){ const c = document.getElementById('panduan-kotak-cari');"
         " c.value = 'zzzz'; c.dispatchEvent(new Event('input')); })()")
    time.sleep(0.2)
    cek("pencarian tanpa hasil mengatakannya",
        bool(d.js("!document.getElementById('panduan-kosong').hidden")))

    for tipe in ("rawKeyDown", "keyUp"):
        d.kirim("Input.dispatchKeyEvent", type=tipe, key="Escape",
                windowsVirtualKeyCode=27, nativeVirtualKeyCode=27)
    time.sleep(0.25)
    cek("Escape menutup panduan",
        bool(d.js("document.getElementById('panduan').hidden")))

    # -------- panel samping tidak boleh saling tumpang tindih
    tumpang = d.js("""(function(){
      const p = [...document.querySelectorAll('.lab-side .pan')]
        .filter(x => x.offsetParent !== null).map(x => x.getBoundingClientRect());
      for (let i = 1; i < p.length; i++)
        if (p[i].top < p[i-1].bottom - 1) return `${i}: ${p[i].top} < ${p[i-1].bottom}`;
      return ''; })()""")
    cek("panel samping tidak tumpang tindih", terlihat_kosong(tumpang),
        tumpang or "tidak ada")

    # -------- rel kiri: satu panel pada satu waktu
    d.js("document.querySelector('.lab-rel .rel[data-pan=\\'pan-objects\\']').click()")
    time.sleep(0.2)
    cek("rel menampilkan panel yang dipilih saja",
        bool(d.js("document.getElementById('pan-objects').offsetParent !== null"))
        and d.js("document.getElementById('pan-labels').offsetParent === null"))
    tampak = d.js("""[...document.querySelectorAll('.lab-side .pan')]
        .filter(x => x.offsetParent !== null).length""")
    cek("tepat satu panel tampak", tampak == 1, "tampak=%s" % tampak)

    # Mematikan panel di menu View juga menghapus tombol relnya, dan kalau yang
    # dimatikan sedang tampil, pilihannya pindah — bukan meninggalkan kolom kosong.
    d.js("document.querySelector('#view-isi input[data-panel=\\'pan-objects\\']').click()")
    time.sleep(0.2)
    cek("mematikan panel menyembunyikan tombol relnya",
        bool(d.js("document.querySelector('.lab-rel .rel[data-pan=\\'pan-objects\\']').hidden")))
    tampak = d.js("""[...document.querySelectorAll('.lab-side .pan')]
        .filter(x => x.offsetParent !== null).length""")
    cek("panel lain menggantikan yang dimatikan", tampak == 1, "tampak=%s" % tampak)
    d.js("document.querySelector('#view-isi input[data-panel=\\'pan-objects\\']').click()")
    time.sleep(0.2)

    # -------- melipat bagian panel: menyembunyikan isi, TIDAK menghapusnya
    d.js("document.querySelector('.lab-rel .rel[data-pan=\\'pan-labels\\']').click()")
    time.sleep(0.2)
    judul = "document.querySelector('#pan-labels h3')"
    isi_awal = d.js("document.querySelectorAll('#kelas .kelas').length")
    d.js(judul + ".click()")
    time.sleep(0.2)
    cek("mengklik judul melipat bagiannya",
        bool(d.js("document.getElementById('pan-labels').hasAttribute('data-lipat')")))
    cek("isinya masih ada di DOM, cuma tidak tampil",
        d.js("document.querySelectorAll('#kelas .kelas').length") == isi_awal
        and d.js("document.getElementById('kelas').offsetParent === null"),
        "jumlah kelas tetap %s" % isi_awal)
    d.js(judul + ".click()")
    time.sleep(0.2)
    cek("mengklik lagi membukanya kembali",
        not d.js("document.getElementById('pan-labels').hasAttribute('data-lipat')")
        and d.js("document.getElementById('kelas').offsetParent !== null"))
def jalankan_potret(d):
    """
    Simpan tangkapan layar ke /tmp untuk dinilai dengan mata.

    Bukan pemeriksaan lolos/gagal: tata letak dan kepadatan visual tidak bisa
    diputuskan oleh assert. Dijalankan sendiri lewat
    `tests/e2e_kanvas.py potret`.
    """
    import base64

    print("  -- potret --")
    d.kirim("Emulation.setDeviceMetricsOverride", width=1500, height=880,
            deviceScaleFactor=1, mobile=False)
    time.sleep(0.4)

    def simpan(nama):
        r = d.kirim("Page.captureScreenshot", format="png")
        f = Path("/tmp") / f"tampilan-{nama}.png"
        f.write_bytes(base64.b64decode(r["data"]))
        print(f"     {f}  ({f.stat().st_size // 1024} kB)")

    # Alamat halaman label disimpan, bukan diandalkan lewat history.back():
    # begitu ada satu lompatan tambahan di grid, "back" mendarat di tempat lain
    # dan langkah panduan di bawah gagal dengan pesan yang tidak menjelaskan apa-apa.
    asal = d.js("location.href")
    d.js("document.getElementById('panduan').hidden = true;"
         " S.v.tanyaKelas = false; render();")
    time.sleep(0.3)
    simpan("halaman")

    # Blok ini menguji saringan KELAS, jadi dataset harus punya kelas. Blok
    # `panel` di atas berakhir dengan seluruh bentuk terhapus, dan tanpa ini
    # dropdown kelasnya kosong: pemeriksaannya lolos-lolos saja tanpa pernah
    # menguji apa pun, atau gagal dengan pesan yang tidak menjelaskan sebabnya.
    # Dipasang sendiri di sini, bukan diwarisi dari blok lain.
    d.js("""
      (async () => {
        await fetch('/api/simpan', {method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({path: D.path, shapes: [
            {label:'botol', shape_type:'rectangle',
             points:[[8,8],[52,40]], group_id:null, flags:{}, description:''}]})});
        window.__siap = 1;
      })()
    """)
    for _ in range(40):
        if d.js("window.__siap === 1"):
            break
        time.sleep(0.25)
    cek("bentuk contoh untuk menguji saringan kelas tersimpan",
        d.js("window.__siap === 1"))

    # Kotak cari tidak boleh melar memenuhi barisnya. Gaya input di aplikasi
    # ini didaftar per TIPE, dan pemilih [type=...] mengalahkan pemilih kelas
    # — menambahkan satu tipe tanpa sadar bisa merentangkan kotak cari.
    d.js("location.href = '/'")
    time.sleep(1.2)
    lebar = d.js("document.getElementById('cari').getBoundingClientRect().width")
    cek("kotak cari tetap ringkas, tidak selebar barisnya",
        100 < lebar < 400, "lebar=%.0f" % lebar)

    # Bilah kemajuan. Yang diperiksa geometrinya, karena di situlah kekeliruan
    # yang tidak terlihat di HTML muncul: potongan yang lebarnya nol, ujung yang
    # tidak membulat, atau bilah yang melebihi barisnya.
    d.js("location.href = '/'")
    time.sleep(1.2)
    bilah = d.js("(function(){ const l = document.querySelector('.lajur');"
                 " if (!l) return null;"
                 " const r = l.getBoundingClientRect();"
                 " const p = [...l.children].map(i => i.getBoundingClientRect().width);"
                 " return {h: Math.round(r.height), w: Math.round(r.width),"
                 "  radius: getComputedStyle(l).borderTopLeftRadius,"
                 "  n: p.length, min: Math.min(...p),"
                 "  jumlah: Math.round(p.reduce((a, b) => a + b, 0)),"
                 "  titik: document.querySelectorAll('#keadaan-isi .titik.t-ok,"
                 "    #keadaan-isi .titik.t-warn, #keadaan-isi .titik.t-bg,"
                 "    #keadaan-isi .titik.t-stop').length,"
                 "  luber: r.right > innerWidth}; })()")
    cek("bilah kemajuan: satu bilah membulat, tidak melebihi layar",
        bilah and bilah["radius"] == "999px" and not bilah["luber"], f"{bilah}")
    cek("bilah kemajuan: tiap potongan punya lebar, jumlahnya penuh",
        bilah and bilah["n"] >= 1 and bilah["min"] >= 3
        and abs(bilah["jumlah"] - bilah["w"]) <= 2, f"{bilah}")
    # Keempat keadaan berwarna pindah dari chip berjajar ke baris dropdown
    # "Keadaan"; warnanyalah yang menghubungkannya ke potongan bilah di atas,
    # jadi keempatnya harus tetap ada.
    cek("empat keadaan berwarna membawa titiknya di dropdown Keadaan",
        bilah and bilah["titik"] == 4, f"{bilah}")

    # Grid: baris urutkan/cari dan dropdown kelas
    d.js("location.href = '/'")
    time.sleep(1.2)
    simpan("grid")
    d.js("document.getElementById('kelas-tombol').click()")
    time.sleep(0.4)
    simpan("grid-kelas")
    luber = d.js("(function(){ const r = document.getElementById('kelas-isi')"
                 ".getBoundingClientRect();"
                 " return (r.left < 0 || r.right > innerWidth)"
                 "        ? `left=${r.left|0} right=${r.right|0} lebar=${innerWidth}` : ''; })()")
    cek("dropdown kelas tidak terpotong tepi layar", luber == "", luber)

    # Keadaan tercentang & segmen aktif: di situlah gaya khusus bisa gagal
    # diam-diam, karena bawaannya (tidak tercentang) selalu terlihat benar.
    d.js("""
      document.querySelectorAll('#kelas-isi input[type=checkbox]')
        .forEach(c => { c.checked = true; });
      const dan = document.querySelector('#kelas-isi input[value=dan]');
      dan.checked = true; dan.dispatchEvent(new Event('change', {bubbles:true}));
    """)
    time.sleep(0.35)
    simpan("grid-kelas-tercentang")
    aktif = d.js("[...document.querySelectorAll('.seg-opt')]"
                 ".filter(o => o.hasAttribute('data-on')).length")
    cek("tepat satu segmen ditandai aktif", aktif == 1, "jumlah=%s" % aktif)
    ket = d.js("[...document.querySelectorAll('#kelas-isi [data-ket]')]"
               ".filter(n => !n.hidden).map(n => n.dataset.ket).join(',')")
    cek("keterangan ikut segmen yang aktif, bukan yang lama", ket == "dan",
        "tampil=%r" % ket)
    cek("mode 'semuanya' menyembunyikan Latar dan Belum dilabeli",
        bool(d.js("document.querySelector('.kelas-tanpa').hidden")))
    cek("centangnya ikut dilepas, bukan tersembunyi tapi masih terkirim",
        d.js("[...document.querySelectorAll('.kelas-tanpa input[type=checkbox]')]"
             ".every(c => !c.checked)") is True)
    d.js("(function(){ const a = document.querySelector('#kelas-isi input[value=atau]');"
         " a.checked = true; a.dispatchEvent(new Event('change', {bubbles:true})); })()")
    time.sleep(0.25)
    cek("kembali ke 'salah satu' memunculkannya lagi",
        d.js("document.querySelector('.kelas-tanpa').hidden") is False)
    simpan("grid-kelas-semuanya")
    n_kelas = d.js("document.querySelectorAll('#kelas-isi .kelas-daftar"
                   ":not(.kelas-tanpa) .kelas-baris').length")
    cek("dropdown kelas benar-benar berisi kelas", n_kelas > 0,
        "jumlah=%s" % n_kelas)
    tanda = d.js("(function(){ const cb = document.querySelector("
                 "'#kelas-isi input:checked ~ .cb');"
                 " if (!cb) return 'tidak ada .cb setelah input tercentang';"
                 " const g = getComputedStyle(cb, '::after');"
                 " return g.transform && g.transform !== 'none' ? '' : 'centang tak tergambar';"
                 " })()")
    cek("kotak centang menampilkan tanda saat tercentang", tanda == "", tanda)

    # Keadaan setelah saringannya benar-benar dipakai. Dulu di sinilah tiap
    # kelas tercentang muncul lagi sebagai chip di samping tombolnya.
    kelas_ada = d.js(
        "[...document.querySelectorAll("
        "  '#kelas-isi .kelas-daftar:not(.kelas-tanpa) .kelas-baris input')]"
        ".slice(0, 2).map(c => 'c=' + encodeURIComponent(c.value)).join('&')")
    d.js("location.href = '/?' + %r" % kelas_ada)
    time.sleep(1.2)
    simpan("grid-kelas-terpakai")
    n_chip = d.js("document.querySelectorAll('#menu-kelas ~ a.chip[data-on]').length")
    cek("tidak ada chip saringan tambahan di samping tombol", n_chip == 0,
        "jumlah=%s" % n_chip)
    lbl = d.js("document.getElementById('kelas-tombol').textContent.trim()")
    cek("tombolnya sendiri yang menyebut apa yang tersaring",
        "Semua kelas" not in lbl, "label=%r" % lbl)
    print("     label tombol: %r" % lbl)
    # Menu Ekspor beserta splitting anti-bocornya. Bilah progres dan angka
    # persennya cuma bisa dinilai dengan mata; yang bisa di-assert hanyalah
    # bahwa hasilnya benar-benar muncul dan menyebut angka.
    #
    # Tempat menunya sekarang bergantung pada jenis datasetnya, dan itu
    # disengaja: projek punya halaman Versi, dataset yang dibuka lewat path
    # server tidak — /versi mengalihkannya ke daftar projek. Kalau menunya
    # dipindah tanpa pengecualian itu, dataset bersama sama sekali tidak bisa
    # diekspor lagi.
    cek("menu Ekspor ada di halaman Versi milik projek",
        d.js("fetch('/versi?ds=projek-satu').then(r=>r.text())"
             ".then(t=>t.includes('id=\"ekspor-tombol\"'))", tunggu=True))
    cek("dan sudah tidak ada di grid projek",
        not d.js("fetch('/?ds=projek-satu').then(r=>r.text())"
                 ".then(t=>t.includes('id=\"ekspor-tombol\"'))", tunggu=True))
    # Dataset uji ini dibuka lewat path server, jadi menunya justru tetap di
    # grid — dan seluruh pemeriksaan splitting di bawah berjalan di situ.
    d.js(f"fetch('/setsrc?path={TMP / 'datasets' / 'uji'}', {{method:'POST'}})"
         ".then(r => r.json())", tunggu=True)
    d.js("location.href = '/'")
    time.sleep(1.4)
    cek("dataset tanpa halaman Versi tetap punya menu Ekspor di grid",
        bool(d.js("!!document.getElementById('ekspor-tombol')")))
    d.js("document.getElementById('ekspor-tombol').click()")
    time.sleep(1.0)
    simpan("ekspor-menu")
    peringatan = d.js("document.getElementById('ekspor-info').textContent")
    cek("sebelum dijalankan, ringkasan mengaku isi gambar belum diperiksa",
        "belum diperiksa" in peringatan, "teks=%r" % peringatan[-90:])
    cek("peringatannya dilipat, bukan berupa paragraf penuh",
        d.js("!!document.querySelector('#ekspor-info .catatan-lipat.peringatan')")
        and d.js("document.querySelector('#ekspor-info .catatan-lipat')"
                 ".hasAttribute('open')") is False)
    cek("keterangan panel juga dilipat",
        d.js("document.getElementById('split-jelas').tagName") == "DETAILS")
    cek("baris ringkas peringatan tetap berwarna, bukan abu biasa",
        d.js("getComputedStyle(document.querySelector("
             "'#ekspor-info .catatan-lipat.peringatan > summary')).color")
        != d.js("getComputedStyle(document.getElementById('ekspor-info')).color"))

    d.js("document.getElementById('split-jalan').click()")
    time.sleep(0.35)
    simpan("ekspor-split-jalan")
    for _ in range(60):
        if d.js("document.getElementById('split-jalan').disabled") is False:
            break
        time.sleep(0.25)
    time.sleep(0.4)
    simpan("ekspor-split-hasil")
    teks = d.js("document.getElementById('split-hasil').textContent.trim()")
    cek("hasil splitting muncul di menu ekspor", "sesi pemotretan" in teks,
        "teks=%r" % teks[:80])
    cek("bilah progres penuh dan angkanya 100%",
        d.js("document.getElementById('split-persen').textContent") == "100%",
        d.js("document.getElementById('split-persen').textContent"))
    cek("peringatan 'belum diperiksa' hilang setelah splitting dijalankan",
        "belum diperiksa" not in
        d.js("document.getElementById('ekspor-info').textContent"))
    cek("catatan dilipat, bukan berderet sebagai kotak kuning",
        d.js("document.querySelectorAll('#split-hasil .catatan-lipat').length") == 1
        and d.js("document.querySelector('#split-hasil .catatan-lipat')"
                 ".hasAttribute('open')") is False)
    cek("isinya tetap ada dan bisa dibuka",
        d.js("document.querySelectorAll('#split-hasil .split-warn').length") > 0)
    cek("tombol jalankan memakai warna aksi, bukan sekadar garis tepi",
        d.js("getComputedStyle(document.getElementById('split-jalan'))"
             ".backgroundColor") not in ("rgba(0, 0, 0, 0)", "transparent"),
        d.js("getComputedStyle(document.getElementById('split-jalan')).backgroundColor"))
    # Kontras tombol aksi harus terjaga di DUA tema. Yang bawaan selalu
    # terlihat benar; yang gelap hanya ketahuan kalau sengaja diperiksa.
    for tema in ("dark", "light"):
        d.js("document.documentElement.setAttribute('data-theme', %r)" % tema)
        time.sleep(0.2)
        warna = d.js("(function(){ const b = getComputedStyle("
                     "document.getElementById('split-jalan'));"
                     " return b.backgroundColor + ' | ' + b.color; })()")
        cek("tombol jalankan tetap berwarna di tema %s" % tema,
            "rgba(0, 0, 0, 0)" not in warna, warna)
        print("     tema %-5s -> %s" % (tema, warna))
    d.js("document.documentElement.removeAttribute('data-theme')")
    time.sleep(0.2)

    cek("daftar format ditandai sebagai unduhan, bukan setelan",
        d.js("document.querySelectorAll('#ekspor-isi a.unduh').length") == 6)
    cek("sesudah splitting, tertulis unduhan memakai hasilnya",
        "hasil splitting" in d.js("document.getElementById('unduh-pakai').textContent"),
        d.js("document.getElementById('unduh-pakai').textContent"))

    cek("selesai, belum ada peringatan rasio berubah",
        d.js("document.getElementById('split-ulang').hidden") is True)
    d.js("(function(){ const k = document.getElementById('s-valid');"
         " k.value = '25'; k.dispatchEvent(new Event('input')); })()")
    time.sleep(0.25)
    cek("mengubah rasio sesudahnya memunculkan ajakan jalankan ulang",
        d.js("document.getElementById('split-ulang').hidden") is False)
    d.js("(function(){ const k = document.getElementById('s-valid');"
         " k.value = '10'; k.dispatchEvent(new Event('input')); })()")
    time.sleep(0.25)
    cek("dikembalikan ke angka semula, ajakannya hilang lagi",
        d.js("document.getElementById('split-ulang').hidden") is True)

    cek("tombol Lupakan muncul setelah ada rencana",
        d.js("document.getElementById('split-lupa').hidden") is False)
    cek("tombol Hentikan menghilang setelah selesai",
        d.js("document.getElementById('split-batal').hidden") is True)
    cek("bilahnya tetap tampil di 100%, tidak berkedip lalu lenyap",
        d.js("document.getElementById('prog-split').offsetParent !== null"))
    luber = d.js("(function(){ const r = document.getElementById('ekspor-isi')"
                 ".getBoundingClientRect();"
                 " return (r.left < 0 || r.right > innerWidth || r.bottom > innerHeight)"
                 "        ? `l=${r.left|0} r=${r.right|0} b=${r.bottom|0}` : ''; })()")
    cek("menu ekspor tidak terpotong tepi layar", luber == "", luber)

    # Halaman projek: kartu beserta menu titik-tiganya.
    d.js("location.href = '/pilih'")
    time.sleep(1.4)
    simpan("projek")
    n_kartu = d.js("document.querySelectorAll('#projek-grid .pcard').length")
    cek("kartu projek tampil", n_kartu > 0, "jumlah=%s" % n_kartu)
    d.js("document.querySelector('#projek-grid .ptitik').click()")
    time.sleep(0.35)
    simpan("projek-menu")
    cek("menu titik-tiga terbuka",
        d.js("document.querySelector('#projek-grid .pmenu').hidden") is False)
    aksi = d.js("[...document.querySelector('#projek-grid .pcard .pmenu')"
                ".querySelectorAll('[data-aksi]')].map(a => a.dataset.aksi)"
                ".join(',')")
    cek("menunya memuat seluruh fitur projek",
        aksi == "buka,salin,ganti,duplikat,gabung,sampah", aksi)
    luber = d.js("(function(){ const r = document.querySelector("
                 "'#projek-grid .pmenu').getBoundingClientRect();"
                 " return (r.right > innerWidth || r.left < 0)"
                 "        ? `l=${r.left|0} r=${r.right|0}` : ''; })()")
    cek("menu projek tidak terpotong tepi layar", luber == "", luber)
    d.js("document.body.click()")
    time.sleep(0.2)
    cek("klik di luar menutup menunya",
        d.js("document.querySelector('#projek-grid .pmenu').hidden") is True)

    # Satu pintu untuk membuat projek: tombolnya membuka dialog, dan tidak
    # ada panel kembar di bawah grid yang isinya sama.
    cek("tidak ada lagi panel tambah kembar di bawah grid",
        d.js("!document.getElementById('lipat-tambah')"))
    cek("dialog projek tertutup saat halaman dibuka",
        d.js("document.getElementById('dlg-projek').hidden") is True)
    d.js("document.getElementById('buka-tambah').click()")
    time.sleep(0.35)
    simpan("projek-dialog")
    cek("tombol Projek baru membuka dialognya",
        d.js("document.getElementById('dlg-projek').hidden") is False)
    # Dialognya sekarang HANYA menanyakan nama. Pemilihan sumber berkas pindah
    # ke halaman Unggah data, yang bisa memperlihatkan apa yang akan terkirim
    # sebelum benar-benar terkirim.
    cek("dialog projek cuma menanyakan nama",
        d.js("!!document.querySelector('#dlg-projek #dsname')")
        and d.js("!!document.querySelector('#dlg-projek #buat-projek')")
        and d.js("!document.querySelector('#dlg-projek #drop')"))
    d.js("(function(){ const d = document.getElementById('dlg-projek');"
         " d.dispatchEvent(new MouseEvent('click', {bubbles:true})); })()")
    time.sleep(0.25)
    cek("mengklik tirainya menutup dialog",
        d.js("document.getElementById('dlg-projek').hidden") is True)

    # ---- aksi menunya benar-benar dijalankan, bukan cuma ada ----
    #
    # prompt() memblokir seluruh laman dan menggantung CDP, jadi ia diganti
    # lebih dulu dengan jawaban yang sudah ditentukan. Tanpa ini pemeriksaan
    # di bawah berhenti tanpa pesan, persis seperti confirm() dulu.
    def aksi_projek(nama_kartu, aksi, jawab=None):
        d.js("window.__jwb = %s; window.prompt = () => window.__jwb;"
             " window.__toast = [];" % json.dumps(jawab))
        d.js("""
          (function(nama, aksi){
            const k = [...document.querySelectorAll('#projek-grid .pcard')]
              .find(x => x.dataset.nama === nama);
            if (!k) { window.__hasil = 'kartu tidak ada'; return; }
            k.querySelector(`.pmenu [data-aksi="${aksi}"]`).click();
            window.__hasil = 'diklik';
          })(%s, %s)
        """ % (json.dumps(nama_kartu), json.dumps(aksi)))
        for _ in range(60):
            time.sleep(0.25)
            if d.js("document.getElementById('projek-note').textContent")\
                    .find("\u2026") < 0:
                break
        time.sleep(0.5)
        return d.js("[...document.querySelectorAll('#projek-grid .pcard')]"
                    ".map(x => x.dataset.nama).sort().join(',')")

    nama = aksi_projek("projek-dua", "ganti", "projek-dua-baru")
    cek("Ganti nama benar-benar mengganti namanya",
        "projek-dua-baru" in nama and "projek-dua," not in nama + ",", nama)

    # Komponen progresnya diuji LANGSUNG, bukan lewat operasi sungguhan.
    # Duplikat tiga berkas selesai lebih cepat daripada pengambil sampel mana
    # pun, jadi mengintipnya di tengah jalan tidak membuktikan apa-apa.
    d.js("window.__pr = Progres.mulai('Uji lama', {di: document.getElementById("
         "'projek-note')}); window.__pr.taktentu('menunggu…');")
    time.sleep(0.3)
    cek("Progres menggambar bilah di tempat yang diminta",
        d.js("!!document.querySelector('#projek-note .pr-kotak .prog')"))
    cek("tanpa persentase bilahnya bergerak sendiri, bukan diam",
        d.js("document.querySelector('#projek-note .prog')"
             ".hasAttribute('data-tak-tentu')") is True)

    # -- penanda pojok: pil, bukan kotak --
    cek("penanda pojok menyala",
        d.js("document.getElementById('kerja-global').hasAttribute('data-on')")
        is True)
    lebar = d.js("document.querySelector('.kg-pil').getBoundingClientRect().width")
    cek("pil tertutup tidak menutupi kanvas (< 260px panel kanan)",
        lebar < 200, "lebar=%.0f" % lebar)
    cek("pil itu tombol sungguhan, bisa difokus papan tik",
        d.js("document.querySelector('.kg-pil').tagName") == "BUTTON")
    cek("z-index di bawah dialog (60), bukan menimpanya",
        int(d.js("getComputedStyle(document.getElementById('kerja-global'))"
                 ".zIndex")) < 60)
    cek("wadahnya tembus klik, hanya pil yang menangkap kursor",
        d.js("getComputedStyle(document.getElementById('kerja-global'))"
             ".pointerEvents") == "none")

    # -- klik untuk melihat detail --
    d.js("document.querySelector('.kg-pil').click()")
    time.sleep(0.35)
    simpan("progres-panel")
    cek("mengklik pil membuka panel detail",
        d.js("document.getElementById('kg-panel').hidden") is False)
    cek("aria-expanded ikut berubah",
        d.js("document.querySelector('.kg-pil').getAttribute('aria-expanded')")
        == "true")
    n = d.js("document.querySelectorAll('#kg-panel .kg-item').length")
    cek("panel memuat barisnya", n >= 1, "jumlah=%s" % n)

    # Yang paling tua di atas: ekspor panjang tidak boleh terdorong keluar
    # oleh pekerjaan pendek yang dimulai belakangan.
    d.js("window.__pr2 = Progres.mulai('Uji baru'); window.__pr2.set(0.5)")
    time.sleep(0.35)
    urut = d.js("[...document.querySelectorAll('#kg-panel .kg-nama')]"
                ".map(x => x.textContent).join('|')")
    cek("yang paling dulu dimulai tetap di baris atas", urut.startswith("Uji lama"),
        urut)

    d.js("window.__pr2.gagal('server tidak menjawab')")
    time.sleep(0.4)
    cek("kegagalan ditandai di barisnya",
        d.js("!!document.querySelector('#kg-panel .kg-item[data-keadaan=gagal]')"))
    d.js("window.__pr.selesai('beres')")
    time.sleep(0.4)
    cek("pil menyatakan gagal selama masih ada yang gagal",
        d.js("document.getElementById('kerja-global').dataset.keadaan") == "gagal")

    d.js("document.querySelector('.kg-tutup').click()")
    time.sleep(0.25)
    cek("tombol tutup menutup panelnya",
        d.js("document.getElementById('kg-panel').hidden") is True)
    d.js("document.querySelectorAll('#kg-panel .kg-buang').forEach(b => b.click())")
    d.js("document.querySelector('.kg-pil').click()")
    time.sleep(0.3)
    d.js("document.querySelectorAll('#kg-panel .kg-buang').forEach(b => b.click())")
    time.sleep(0.4)
    cek("membuang semua barisnya memadamkan penanda",
        d.js("document.getElementById('kerja-global').hasAttribute('data-on')")
        is False)

    d.js("document.getElementById('projek-note').innerHTML = ''")

    nama = aksi_projek("projek-satu", "duplikat")
    # Sesudah operasi sungguhan, penandanya sengaja BERTAHAN sebentar
    # menyatakan "Selesai", lalu padam sendiri. Hilang seketika berarti orang
    # yang sedang melihat ke tempat lain tidak pernah tahu hasilnya.
    cek("penanda menyatakan selesai, bukan langsung hilang",
        d.js("document.getElementById('kerja-global').dataset.keadaan") == "selesai",
        d.js("document.getElementById('kerja-global').dataset.keadaan"))
    for _ in range(30):
        time.sleep(0.4)
        if not d.js("document.getElementById('kerja-global').hasAttribute('data-on')"):
            break
    cek("lalu padam sendiri tanpa perlu disentuh",
        d.js("document.getElementById('kerja-global').hasAttribute('data-on')")
        is False)
    cek("Duplikat menghasilkan salinan baru",
        "projek-satu 2" in nama and "projek-satu" in nama, nama)

    nama = aksi_projek("projek-satu 2", "sampah", "projek-satu 2")
    cek("Buang ke sampah mengeluarkannya dari daftar",
        "projek-satu 2" not in nama, nama)
    cek("dan muncul di tempat sampah",
        d.js("document.getElementById('sampah-lipat').hidden") is False
        and d.js("document.querySelectorAll('#sampah-isi [data-pulih]').length") >= 1)

    d.js("document.querySelector('#sampah-isi [data-pulih]').click()")
    time.sleep(1.2)
    nama = d.js("[...document.querySelectorAll('#projek-grid .pcard')]"
                ".map(x => x.dataset.nama).sort().join(',')")
    cek("Kembalikan memulihkannya dari sampah", "projek-satu 2" in nama, nama)

    # Nama salah saat membuang HARUS membatalkan, bukan tetap jalan.
    nama = aksi_projek("projek-satu", "sampah", "salah-ketik")
    cek("mengetik nama yang salah membatalkan pembuangan",
        "projek-satu" in nama, nama)

    n_seb = d.js("document.querySelectorAll('#projek-grid .pcard').length")
    nama = aksi_projek("projek-satu 2", "gabung", "projek-satu")
    cek("Gabungkan menyalin isinya tanpa menghapus sumbernya",
        "projek-satu 2" in nama and "projek-satu" in nama, nama)

    # Halaman Lihat: gambar dan seluruh kotak keterangan harus terlihat
    # BERSAMAAN, tanpa halaman perlu digulir sama sekali.
    #
    d.js("location.href = '/'")
    time.sleep(1.2)
    d.js("location.href = document.querySelector('a[href^=\"/view\"]').href")
    time.sleep(1.5)
    simpan("lihat")
    # Diperiksa lewat JUDULNYA, bukan jumlahnya. Angka telanjang membuat uji
    # ini gagal tiap kali ada kotak yang ditambahkan, tanpa memberi tahu kotak
    # mana yang sebenarnya hilang.
    judul = d.js("[...document.querySelectorAll('.lh-sisi .lh-kotak h3')]"
                 ".map(h => h.firstChild.textContent.trim())")
    cek("kotak keterangan lengkap",
        judul == ["Temuan", "Objek per kelas", "Tag", "Berkas"], str(judul))
    cek("halaman Lihat tidak bisa digulir",
        d.js("document.documentElement.scrollHeight <= "
             "document.documentElement.clientHeight + 2"),
        d.js("document.documentElement.scrollHeight + ' vs ' + "
             "document.documentElement.clientHeight"))
    luber = d.js("(function(){"
                 " const b = [...document.querySelectorAll('.lh-sisi .lh-kotak')]"
                 "   .map(n => n.getBoundingClientRect());"
                 " const bad = b.filter(r => r.bottom > innerHeight + 1);"
                 " return bad.length ? bad.length + ' kotak terpotong' : ''; })()")
    cek("tidak ada kotak yang terpotong di bawah layar", luber == "", luber)
    lebar = d.js("(function(){ const a = document.querySelector('.lh-panggung')"
                 ".getBoundingClientRect(), i = document.querySelector("
                 "'.lh-panggung img').getBoundingClientRect();"
                 " return Math.round(a.width - i.width); })()")
    cek("bingkai memeluk gambarnya, bukan kotak lebar berisi pita sempit",
        lebar < 40, "selisih lebar %s px" % lebar)

    # Kasus yang sebenarnya dikeluhkan: foto potret 2296x4080. Rasionya
    # dipaksakan ke panggung supaya bisa diukur tanpa menyiapkan foto
    # sebesar itu di lingkungan uji.
    ukur = d.js("(function(){"
                " const a = document.querySelector('.lh-panggung');"
                " a.style.setProperty('--arw', 2296);"
                " a.style.setProperty('--arh', 4080);"
                " const r = a.getBoundingClientRect();"
                " const s = document.querySelector('.lh-sisi').getBoundingClientRect();"
                " return JSON.stringify({w: Math.round(r.width),"
                "   h: Math.round(r.height), sisiBawah: Math.round(s.bottom),"
                "   vp: innerHeight}); })()")
    import json as _j
    u = _j.loads(ukur)
    cek("pada foto potret, bingkainya jadi ramping bukan melebar",
        u["w"] < u["h"] * 0.7, ukur)
    cek("dan tingginya tetap muat di layar", u["h"] <= u["vp"], ukur)
    cek("kolom kanan tetap tidak terpotong pada foto potret",
        u["sisiBawah"] <= u["vp"] + 1, ukur)
    d.js("(function(){ const a = document.querySelector('.lh-panggung');"
         " a.style.removeProperty('--arw'); a.style.removeProperty('--arh'); })()")

    d.js("location.href = %r" % asal)
    time.sleep(1.2)

    d.js("document.getElementById('btn-panduan').click()")
    time.sleep(0.5)
    simpan("panduan")

    d.js("(function(){ const c = document.getElementById('panduan-kotak-cari');"
         " c.value = 'titik'; c.dispatchEvent(new Event('input')); })()")
    time.sleep(0.3)
    simpan("panduan-cari")
    d.js("document.getElementById('panduan-tutup').click()")


def jalankan_kontrol(d):
    """
    Sapuan kontrol: SETIAP tombol, saringan, dan pintasan huruf di halaman
    kanvas ditekan sungguhan, lalu akibatnya diperiksa.

    Blok lain menguji perilaku (bentuk, dialog, paritas kanvas). Yang ini
    menguji PEMASANGANNYA — tombol yang pindah tempat saat tata letak diubah
    dan diam-diam kehilangan penanganannya tetap terlihat normal di layar, dan
    tidak satu pun uji perilaku menangkapnya karena uji-uji itu memanggil
    fungsinya langsung, bukan menekan tombolnya.
    """
    print("  -- sapuan kontrol --")

    def bersih():
        d.js("S.draft=null; S.prompt=[]; S.shapes.length=0; S.terpilih=[]; S.sel=-1;"
             " S.redo.length=0; S.undo.length=0; S.kotor=false;"
             " S.v.tanyaKelas=false; S.label='botol'; muatKeLayar(); render();")
        time.sleep(0.15)

    def satu_bentuk():
        bersih()
        d.js("S.shapes.push({label:'botol', shape_type:'polygon',"
             " points:[[10,10],[40,10],[40,35]], text:'', group_id:null,"
             " flags:{}, titipan:{}}); S.terpilih=[0]; S.sel=0; render();")
        time.sleep(0.15)

    def tekan(huruf, kode, ctrl=False, shift=False, alt=False):
        mod = (2 if ctrl else 0) | (8 if shift else 0) | (1 if alt else 0)
        for tipe in ("rawKeyDown", "keyUp"):
            d.kirim("Input.dispatchKeyEvent", type=tipe, key=huruf,
                    windowsVirtualKeyCode=kode, nativeVirtualKeyCode=kode,
                    modifiers=mod)
        time.sleep(0.18)

    # ---------------------------------------------------------------- bar atas
    cek("panah kembali menuju daftar gambar",
        d.js("document.querySelector('.lab-balik').getAttribute('href')") == "/")
    cek("nama projek menuju pemilih dataset",
        d.js("document.querySelector('.lab-projek').getAttribute('href')") == "/pilih")
    cek("Keluar menuju /logout",
        d.js("""[...document.querySelectorAll('.lab-top a')]
             .some(a => a.getAttribute('href') === '/logout')"""))
    for arah in ("prev", "next"):
        h = d.js(f"document.getElementById('{arah}').getAttribute('href')")
        mati = d.js(f"document.getElementById('{arah}').hasAttribute('data-off')")
        cek(f"tombol {arah} punya tujuan atau ditandai mati",
            (h or "").startswith("/label?path=") or mati, h or "mati")

    satu_bentuk()
    d.js("document.getElementById('btn-dup').click()")
    time.sleep(0.25)
    cek("tombol Gandakan menambah objek", d.js("S.shapes.length") == 2,
        "n=%s" % d.js("S.shapes.length"))

    d.js("document.getElementById('btn-del').click()")
    time.sleep(0.25)
    n_sesudah_hapus = d.js("S.shapes.length")
    cek("tombol Hapus membuang objek terpilih", n_sesudah_hapus == 1,
        "n=%s" % n_sesudah_hapus)

    d.js("document.getElementById('btn-undo').click()")
    time.sleep(0.25)
    cek("tombol Urungkan mengembalikannya", d.js("S.shapes.length") == 2,
        "n=%s" % d.js("S.shapes.length"))

    d.js("document.getElementById('btn-redo').click()")
    time.sleep(0.25)
    cek("tombol Ulangi menghapusnya lagi", d.js("S.shapes.length") == 1,
        "n=%s" % d.js("S.shapes.length"))

    d.js("document.getElementById('btn-simpan').click()")
    time.sleep(0.8)
    cek("tombol Simpan benar-benar menyimpan",
        d.js("document.getElementById('status').textContent") == "Tersimpan"
        and not d.js("S.kotor"),
        d.js("document.getElementById('status').textContent"))

    d.js("document.getElementById('btn-panduan').click()")
    time.sleep(0.25)
    cek("tombol Panduan membuka panduan (dari bar atas yang baru)",
        bool(d.js("!document.getElementById('panduan').hidden")))
    d.js("document.getElementById('panduan-tutup').click()")
    time.sleep(0.2)

    d.js("document.getElementById('view-tombol').click()")
    time.sleep(0.2)
    buka = d.js("document.getElementById('menu-view').hasAttribute('data-buka')")
    d.js("document.getElementById('view-tombol').click()")
    time.sleep(0.2)
    cek("tombol View membuka lalu menutup menunya",
        buka and not d.js("document.getElementById('menu-view').hasAttribute('data-buka')"))

    # Sepuluh centang di menu View dipasang lewat satu peta; kalau petanya
    # tidak sepadan dengan templatnya, centangnya tampil tetapi tidak mengubah
    # apa pun. Diperiksa sekaligus di sini.
    peta_view = {"v-kelas": "namaKelas", "v-teks": "teks", "v-grup": "grup",
                 "v-isi": "isi", "v-silang": "silang",
                 "v-tanyakelas": "tanyaKelas", "v-labelterakhir": "labelTerakhir",
                 "v-zoomtetap": "zoomTetap", "v-keepprev": "keepPrev",
                 "v-autosave": "autosave"}
    salah = []
    for cid, kunci in peta_view.items():
        # Yang ditagih: setelan MENGIKUTI centangnya. Bukan "nilainya berubah" —
        # blok uji sebelumnya menyetel S.v langsung lewat JS, jadi centang dan
        # setelan bisa sudah tidak sepadan sebelum sapuan ini mulai, dan
        # menagih perubahan di situ menguji urutan blok, bukan pemasangannya.
        cb_awal = d.js(f"document.getElementById('{cid}').checked")
        d.js(f"document.getElementById('{cid}').click()")
        time.sleep(0.1)
        cb_kini = d.js(f"document.getElementById('{cid}').checked")
        if cb_kini == cb_awal:
            salah.append(cid + " (centang tidak berubah)")
        elif d.js(f"!!S.v.{kunci}") != cb_kini:
            salah.append(f"{cid}: centang={cb_kini} tapi S.v.{kunci}="
                         f"{d.js(f'!!S.v.{kunci}')}")
        d.js(f"document.getElementById('{cid}').click()")   # kembalikan
        time.sleep(0.06)
        if d.js(f"!!S.v.{kunci}") != cb_awal:
            salah.append(cid + " (tidak kembali)")
    cek("sepuluh centang menu View menyetel perilakunya", not salah,
        "; ".join(salah) or "10/10")

    # ------------------------------------------------------------------ palet
    modes = {"p+": "+Point", "p-": "−Point", "rect": "+Rect",
             "poly": "Poligon manual", "kotak": "Rectangle manual",
             "circle": "Circle", "line": "Line", "linestrip": "LineStrip",
             "point": "Point", "edit": "Sunting"}
    salah = []
    for m, nama in modes.items():
        d.js(f"document.querySelector('.lab-palet .tool[data-mode=\\'{m}\\']').click()")
        time.sleep(0.12)
        if d.js("S.mode") != m:
            salah.append(f"{m}: S.mode={d.js('S.mode')}")
        elif not d.js(f"document.querySelector('.lab-palet .tool[data-mode=\\'{m}\\']')"
                      ".hasAttribute('data-on')"):
            salah.append(f"{m}: tidak menyala")
        elif nama not in (d.js("document.getElementById('modeinfo').textContent") or ""):
            salah.append(f"{m}: modeinfo={d.js('document.getElementById(\'modeinfo\').textContent')}")
    cek("sepuluh alat di palet memilih modenya dan menyala",
        not salah, "; ".join(salah) or "10/10")

    cek("tepat satu alat menyala pada satu waktu",
        d.js("document.querySelectorAll('.lab-palet .tool[data-on]').length") == 1)

    # -------------------------------------------------------------- pil Auto
    for tombol, mode in (("ab-p+", "p+"), ("ab-p-", "p-"), ("ab-rect", "rect")):
        d.js(f"document.getElementById('{tombol}').click()")
        time.sleep(0.12)
        cek(f"pil Auto: {tombol} memilih mode {mode}", d.js("S.mode") == mode,
            "S.mode=%s" % d.js("S.mode"))
        cek(f"alat palet yang sama ikut menyala untuk {mode}",
            bool(d.js(f"document.querySelector('.lab-palet .tool[data-mode=\\'{mode}\\']')"
                      ".hasAttribute('data-on')")))

    d.js("S.prompt.push({x:10,y:10,label:1}); gambar();")
    d.js("document.getElementById('ab-clear').click()")
    time.sleep(0.25)
    cek("pil Auto: Bersih mengosongkan prompt", d.js("S.prompt.length") == 0,
        "n=%s" % d.js("S.prompt.length"))
    cek("pil Auto: Jadikan objek terpasang",
        bool(d.js("typeof document.getElementById('ab-finish').onclick === 'function'")))
    cek("setelan model & bentuk keluaran ada di panel AI",
        bool(d.js("document.getElementById('pan-ai').contains(document.getElementById('model'))"))
        and bool(d.js("document.getElementById('pan-ai')"
                      ".contains(document.getElementById('output'))")))
    cek("pilihan model tidak kosong",
        d.js("document.getElementById('model').options.length") > 0,
        "n=%s" % d.js("document.getElementById('model').options.length"))

    # ------------------------------------------------------------------- dok
    d.js("muatKeLayar()")
    time.sleep(0.2)
    z0 = d.js("S.zoom")
    d.js("document.getElementById('btn-zin').click()")
    time.sleep(0.2)
    z1 = d.js("S.zoom")
    cek("dok: Perbesar menaikkan zoom", z1 > z0, "%.3f -> %.3f" % (z0, z1))
    d.js("document.getElementById('btn-zout').click()")
    time.sleep(0.2)
    cek("dok: Perkecil menurunkannya lagi", d.js("S.zoom") < z1,
        "%.3f -> %.3f" % (z1, d.js("S.zoom")))
    d.js("document.getElementById('btn-fit').click()")
    time.sleep(0.2)
    cek("dok: Muat ke jendela memaskan ulang", not d.js("S.zoomManual"))
    cek("penunjuk zoom mengikuti zoom sebenarnya",
        d.js("document.getElementById('lab-zoom').textContent")
        == str(round(d.js("S.zoom") * 100)) + "%",
        d.js("document.getElementById('lab-zoom').textContent"))

    x, y = d.layar(30, 25)
    d.kirim("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y,
            button="none", buttons=0)
    time.sleep(0.25)
    cek("dok: koordinat kursor terisi saat tetikus di atas kanvas",
        d.js("document.getElementById('koord').textContent") != "-",
        d.js("document.getElementById('koord').textContent"))

    # ----------------------------------------------------------------- panel
    d.js("document.querySelector('.lab-rel .rel[data-pan=\\'pan-labels\\']').click()")
    time.sleep(0.25)
    n_kelas = d.js("document.querySelectorAll('#kelas .kelas').length")
    enter = """(() => { const i = document.getElementById('kelasbaru');
        i.value = 'kelas-sapuan';
        i.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); })()"""
    # Dataset uji punya classes.txt, jadi kelas di luar daftar itu harus
    # ditegaskan dua kali — Enter pertama sengaja hanya memperingatkan.
    d.js(enter)
    time.sleep(0.3)
    setelah_sekali = d.js("document.querySelectorAll('#kelas .kelas').length")
    d.js(enter)
    time.sleep(0.35)
    cek("panel Kelas: kelas di luar daftar resmi minta ditegaskan dulu",
        setelah_sekali == n_kelas, "%s -> %s" % (n_kelas, setelah_sekali))
    cek("panel Kelas: penegasan kedua menambahkannya",
        d.js("document.querySelectorAll('#kelas .kelas').length") == n_kelas + 1,
        "%s -> %s" % (n_kelas, d.js("document.querySelectorAll('#kelas .kelas').length")))

    d.js("S.label = ''; render();")
    time.sleep(0.15)
    nama_pertama = d.js("document.querySelector('#kelas .kelas span').textContent")
    d.js("document.querySelector('#kelas .kelas').click()")
    time.sleep(0.2)
    cek("panel Kelas: mengklik kelas memilihnya untuk objek berikutnya",
        d.js("S.label") == nama_pertama, "S.label=%s" % d.js("S.label"))
    d.js("document.querySelector('#kelas .kelas').click()")
    time.sleep(0.2)
    cek("panel Kelas: mengklik ulang melepas pilihannya",
        not d.js("S.label"), "S.label=%s" % d.js("S.label"))

    satu_bentuk()
    d.js("document.querySelector('.lab-rel .rel[data-pan=\\'pan-objects\\']').click()")
    time.sleep(0.25)
    cek("panel Objek: daftarnya memuat objeknya",
        d.js("document.querySelectorAll('#objek .obj').length") == 1)
    d.js("document.querySelector('#objek .obj input[type=checkbox]').click()")
    time.sleep(0.25)
    cek("panel Objek: centang menyembunyikan objeknya",
        bool(d.js("S.shapes[0].sembunyi")))
    d.js("document.querySelector('#objek .obj input[type=checkbox]').click()")
    time.sleep(0.2)
    cek("dan mengembalikannya", not d.js("S.shapes[0].sembunyi"))

    d.js("document.querySelector('.lab-rel .rel[data-pan=\\'pan-teks\\']').click()")
    time.sleep(0.25)
    d.js("""(() => { const t = document.getElementById('teks');
        t.value = 'catatan sapuan'; t.dispatchEvent(new Event('input')); })()""")
    time.sleep(0.25)
    cek("panel Catatan: mengetik menulis ke objek terpilih",
        d.js("S.shapes[0].text") == "catatan sapuan",
        "text=%s" % d.js("S.shapes[0].text"))

    d.js("document.querySelector('.lab-rel .rel[data-pan=\\'pan-flags\\']').click()")
    time.sleep(0.25)
    d.js("""(() => { const i = document.getElementById('flagbaru');
        i.value = 'flag-sapuan';
        i.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); })()""")
    time.sleep(0.3)
    cek("panel Flag: menambah flag baru", "flag-sapuan" in (d.js("Object.keys(S.flags)") or []),
        str(d.js("Object.keys(S.flags)")))
    d.js("""[...document.querySelectorAll('#flags .flag')]
        .find(f => f.textContent.includes('flag-sapuan')).querySelector('button').click()""")
    time.sleep(0.3)
    cek("panel Flag: tombol × membuangnya lagi",
        "flag-sapuan" not in (d.js("Object.keys(S.flags)") or []),
        str(d.js("Object.keys(S.flags)")))

    d.js("document.querySelector('.lab-rel .rel[data-pan=\\'pan-setelan\\']').click()")
    time.sleep(0.25)
    d.js("""(() => { const b = document.getElementById('cerah');
        b.value = 90; b.dispatchEvent(new Event('input')); })()""")
    time.sleep(0.25)
    cek("panel Setelan: geser kecerahan mengubah nilainya",
        abs((d.js("S.cerah") or 0) - 1.8) < 0.01
        and d.js("document.getElementById('cerah-nilai').textContent") == "1.80",
        "S.cerah=%s teks=%s" % (d.js("S.cerah"),
                                d.js("document.getElementById('cerah-nilai').textContent")))
    d.js("document.getElementById('btn-reset-cerah').click()")
    time.sleep(0.25)
    cek("panel Setelan: Kembalikan normal mengembalikannya ke 1,00",
        abs((d.js("S.cerah") or 0) - 1) < 1e-9 and abs((d.js("S.kontras") or 0) - 1) < 1e-9,
        "cerah=%s kontras=%s" % (d.js("S.cerah"), d.js("S.kontras")))

    d.js("document.querySelector('.lab-rel .rel[data-pan=\\'pan-files\\']').click()")
    time.sleep(0.25)
    semua = d.js("document.querySelectorAll('#berkas .fitem').length")
    d.js("""(() => { const c = document.getElementById('cari');
        c.value = 'zzzz-tidak-ada'; c.dispatchEvent(new Event('input')); })()""")
    time.sleep(0.3)
    kosong = d.js("document.querySelectorAll('#berkas .fitem').length")
    d.js("""(() => { const c = document.getElementById('cari');
        c.value = ''; c.dispatchEvent(new Event('input')); })()""")
    time.sleep(0.3)
    cek("panel Berkas: kotak cari menyaring daftarnya",
        semua > 0 and kosong == 0
        and d.js("document.querySelectorAll('#berkas .fitem').length") == semua,
        "%s -> %s -> %s" % (semua, kosong,
                            d.js("document.querySelectorAll('#berkas .fitem').length")))

    # ------------------------------------------------------------- pintasan
    # Fokus dilepas dulu. Pintasan huruf memang SENGAJA tidak berlaku selagi
    # kursor berada di kotak isian (label.js:1808-1814) — tanpa baris ini, uji
    # ini menagih perilaku yang justru salah, dan lolos-tidaknya bergantung
    # pada blok mana yang kebetulan berjalan sebelumnya.
    d.js("document.activeElement && document.activeElement.blur()")
    bersih()
    huruf_mode = (("q", 81, "p+"), ("e", 69, "p-"), ("r", 82, "kotak"),
                  ("p", 80, "poly"), ("v", 86, "edit"))
    salah = []
    for huruf, kode, mode in huruf_mode:
        tekan(huruf, kode)
        if d.js("S.mode") != mode:
            salah.append(f"{huruf} -> {d.js('S.mode')}, bukan {mode}")
    cek("pintasan huruf Q/E/R/P/V memilih alatnya", not salah,
        "; ".join(salah) or "5/5")

    satu_bentuk()
    tekan("g", 71)
    cek("G menggabungkan objek terpilih jadi grup",
        bool(d.js("S.shapes[0].group_id !== null && S.shapes[0].group_id !== undefined")),
        "group_id=%s" % d.js("S.shapes[0].group_id"))
    tekan("u", 85)
    cek("U melepasnya dari grup",
        d.js("S.shapes[0].group_id === null || S.shapes[0].group_id === undefined"),
        "group_id=%s" % d.js("S.shapes[0].group_id"))

    satu_bentuk()
    tekan("c", 67, ctrl=True)
    tekan("v", 86, ctrl=True)
    cek("Ctrl+C lalu Ctrl+V menempel salinannya", d.js("S.shapes.length") == 2,
        "n=%s" % d.js("S.shapes.length"))
    tekan("d", 68, ctrl=True)
    cek("Ctrl+D menggandakan di tempat", d.js("S.shapes.length") == 3,
        "n=%s" % d.js("S.shapes.length"))

    d.js("S.terpilih=[S.shapes.length-1]; S.sel=S.shapes.length-1; render();")
    tekan("Delete", 46)
    cek("Del menghapus objek terpilih", d.js("S.shapes.length") == 2,
        "n=%s" % d.js("S.shapes.length"))
    tekan("z", 90, ctrl=True)
    cek("Ctrl+Z mengembalikannya", d.js("S.shapes.length") == 3,
        "n=%s" % d.js("S.shapes.length"))
    tekan("y", 89, ctrl=True)
    cek("Ctrl+Y menghapusnya lagi", d.js("S.shapes.length") == 2,
        "n=%s" % d.js("S.shapes.length"))

    d.js("muatKeLayar()")
    time.sleep(0.15)
    z_muat = d.js("S.zoom")
    tekan("0", 48, ctrl=True)
    cek("Ctrl+0 mengembalikan ukuran asli", abs(d.js("S.zoom") - 1) < 1e-6,
        "zoom=%.3f" % d.js("S.zoom"))
    tekan("f", 70, ctrl=True)
    cek("Ctrl+F memuat ke jendela lagi", abs(d.js("S.zoom") - z_muat) < 1e-6,
        "zoom=%.3f" % d.js("S.zoom"))
    tekan("f", 70, ctrl=True, shift=True)
    cek("Ctrl+Shift+F memuat ke lebar",
        abs(d.js("S.zoom") - d.js("(c.width / D.W) * 0.96")) < 1e-6,
        "zoom=%.3f" % d.js("S.zoom"))

    d.js("S.kotor = true;")
    tekan("s", 83, ctrl=True)
    time.sleep(0.9)
    cek("Ctrl+S menyimpan", not d.js("S.kotor"))

    satu_bentuk()
    tekan("e", 69, ctrl=True)
    time.sleep(0.35)
    dialog_buka = d.js("!document.getElementById('dlg').hidden")
    if dialog_buka:
        d.js("document.getElementById('dlg-batal').click()")
        time.sleep(0.25)
    cek("Ctrl+E membuka dialog ganti kelas", dialog_buka)

    tekan("?", 191, shift=True)
    time.sleep(0.3)
    tanda = d.js("!document.getElementById('panduan').hidden")
    if tanda:
        d.js("document.getElementById('panduan-tutup').click()")
        time.sleep(0.2)
    cek("tanda tanya membuka panduan", tanda)

    bersih()




def jalankan_grid(d):
    """
    Sapuan kontrol halaman GRID: tiap saringan, tiap dropdown, paginasi, dan
    tiap tombol di kartu ditekan sungguhan, lalu akibatnya diperiksa.

    Pasangan dari jalankan_kontrol untuk halaman kanvas. pytest sudah menjaga
    sisi servernya — rute, saringan, angka — tetapi tidak satu pun uji itu
    membuktikan bahwa TOMBOLNYA benar-benar memanggil rute itu. Sesudah baris
    saringannya dirombak (chip jadi dropdown, paginasi baru, ekspor pindah),
    justru sambungan itulah yang paling mungkin putus tanpa terlihat.
    """
    print("  -- sapuan grid --")
    GD = TMP / "datasets" / "grid"

    def buka(url="/"):
        d.js(f"location.href = {url!r}")
        time.sleep(1.3)

    def n_kartu():
        return d.js("document.querySelectorAll('.card').length")

    def teks(sel):
        return d.js(f"(document.querySelector({sel!r}) || {{}}).textContent") or ""

    # Dataset besar bercampur dibuka lewat rute yang sama dengan yang dipakai
    # halaman pemilih, bukan dengan menyuntik keadaan.
    ok = d.js(f"fetch('/setsrc?path={GD}', {{method:'POST'}}).then(r => r.json())",
              tunggu=True)
    cek("dataset uji grid terbuka", bool((ok or {}).get("ok")), str(ok)[:80])
    buka("/")

    # ---------------------------------------------------------------- kerangka
    cek("bar atas tidak lagi memuat path folder",
        not d.js("!!document.querySelector('.path-src')"))
    cek("tombol Tambah gambar sudah tidak ada",
        not d.js("document.body.innerHTML.includes('Tambah gambar')"))
    hal_awal = n_kartu()
    cek("grid memotong di 50 kartu, bukan merender 130",
        hal_awal == 50, "n=%s" % hal_awal)
    cek("penunjuk halaman menyebut rentang dan jumlahnya",
        "1" in teks(".hal-info") and "130" in teks(".hal-info"),
        teks(".hal-info").strip())

    # -------------------------------------------------------------- saringan
    d.js("document.getElementById('keadaan-tombol').click()")
    time.sleep(0.35)
    cek("dropdown Keadaan terbuka",
        bool(d.js("document.getElementById('menu-keadaan').hasAttribute('data-buka')")))
    baris = d.js("[...document.querySelectorAll('#keadaan-isi .kd-baris')]"
                 ".map(a => a.textContent.replace(/\\s+/g,' ').trim())")
    cek("berisi lima keadaan beserta angkanya", len(baris or []) == 5,
        " | ".join(baris or []))
    # Nama dan angkanya elemen bersebelahan tanpa spasi di antaranya, jadi
    # textContent-nya menyatu ("Belum dilabeli50"). Yang ditagih angkanya.
    cek("angka di dropdown sepadan dengan isi dataset",
        any(b.replace(" ", "") == "Belumdilabeli50" for b in baris or [])
        and any(b.replace(" ", "") == "Perludicek10" for b in baris or []),
        " | ".join(baris or []))

    d.js("[...document.querySelectorAll('#keadaan-isi .kd-baris')]"
         ".find(a => a.textContent.includes('Perlu dicek')).click()")
    time.sleep(1.3)
    cek("memilih 'Perlu dicek' benar-benar menyaring", n_kartu() == 10,
        "n=%s" % n_kartu())
    cek("tombolnya sendiri menyebut keadaan yang berlaku",
        "Perlu dicek" in teks("#keadaan-tombol"), teks("#keadaan-tombol").strip())
    cek("tombolnya menyala", bool(d.js(
        "document.getElementById('keadaan-tombol').hasAttribute('data-on')")))
    cek("tombol Bersihkan saringan muncul beserta jumlahnya",
        "1" in teks(".saring-bersih"), teks(".saring-bersih").strip())

    d.js("document.querySelector('.saring-bersih').click()")
    time.sleep(1.3)
    cek("Bersihkan saringan mengembalikan seluruh isi", n_kartu() == 50,
        "n=%s" % n_kartu())
    cek("dan tombolnya ikut hilang", not d.js("!!document.querySelector('.saring-bersih')"))

    # -------------------------------------------------------------- cari nama
    d.js("""(() => { const c = document.getElementById('cari');
        c.value = 'g-01'; c.form.submit(); })()""")
    time.sleep(1.4)
    cek("kotak cari menyaring seluruh dataset, bukan halaman ini",
        n_kartu() == 10, "n=%s" % n_kartu())
    cek("pencarian ikut terhitung di Bersihkan saringan",
        "cari" in (d.js("document.querySelector('.saring-bersih').title") or ""),
        d.js("document.querySelector('.saring-bersih').title"))
    d.js("document.querySelector('.saring-bersih').click()")
    time.sleep(1.3)

    # -------------------------------------------------------------- urutkan
    pertama = d.js("document.querySelector('.card .fn').textContent.trim()")
    d.js("""(() => { const u = document.getElementById('urut');
        u.value = 'nama-turun'; u.dispatchEvent(new Event('change')); })()""")
    time.sleep(1.4)
    cek("mengubah urutan benar-benar membalik daftarnya",
        d.js("document.querySelector('.card .fn').textContent.trim()") != pertama,
        "%s -> %s" % (pertama,
                      d.js("document.querySelector('.card .fn').textContent.trim()")))
    cek("urutan bertahan sebagai pilihan, bukan ikut dibersihkan",
        d.js("document.getElementById('urut').value") == "nama-turun")

    # -------------------------------------------------------- saringan kelas
    d.js("document.getElementById('kelas-tombol').click()")
    time.sleep(0.35)
    d.js("""(() => { const k = [...document.querySelectorAll('#kelas-isi input[name=c]')]
        .find(i => i.value === 'botol'); k.checked = true;
        k.form.querySelector('button[type=submit]').click(); })()""")
    time.sleep(1.5)
    cek("saringan kelas 'botol' menyaring", 0 < n_kartu() <= 40,
        "n=%s" % n_kartu())
    cek("tombol kelas menyebut kelas yang dipilih",
        "botol" in teks("#kelas-tombol"), teks("#kelas-tombol").strip())
    cek("urutan tidak ikut hilang saat saringan kelas dipakai",
        d.js("document.getElementById('urut').value") == "nama-turun")

    d.js("document.getElementById('kelas-tombol').click()")
    time.sleep(0.35)
    d.js("""(() => { const m = [...document.querySelectorAll('#kelas-isi input[name=m]')]
        .find(i => i.value === 'dan'); m.checked = true;
        m.dispatchEvent(new Event('change', {bubbles:true})); })()""")
    time.sleep(0.4)
    cek("mode 'semuanya' menyembunyikan pilihan tanpa-kelas",
        bool(d.js("document.querySelector('#kelas-isi .kelas-tanpa').hidden")))
    d.js("document.querySelector('#kelas-isi a.chip').click()")   # Bersihkan
    time.sleep(1.4)
    cek("Bersihkan di dropdown kelas mengembalikan isinya", n_kartu() == 50,
        "n=%s" % n_kartu())

    # ---------------------------------------------------------- saringan tag
    # Dropdown Tag hanya dirender kalau sudah ada yang ditandai; dua gambar
    # ditandai lewat rutenya sendiri supaya menunya benar-benar muncul.
    # Path lengkapnya diambil dari tautan kartunya sendiri; rutenya menerima
    # path, bukan nama berkas.
    hasil_tag = d.js("""fetch('/api/tag/pasang', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({
          paths: [...document.querySelectorAll('.card')].slice(0, 2)
            .map(k => decodeURIComponent(k.querySelector('a[href^="/view?path="]')
              .getAttribute('href').split('path=')[1])),
          tambah: ['lampu-redup']})})
        .then(r => r.json())""", tunggu=True)
    cek("dua gambar berhasil ditandai lewat rutenya",
        bool((hasil_tag or {}).get("ok")), str(hasil_tag)[:90])
    buka("/")
    cek("dropdown Tag muncul setelah ada tag",
        bool(d.js("!!document.getElementById('menu-tag')")))
    d.js("document.getElementById('tag-tombol').click()")
    time.sleep(0.35)
    cek("dropdown Tag terbuka dan memuat tagnya",
        "lampu-redup" in teks("#tag-isi"), teks("#tag-isi").replace("\n", " ")[:80])
    d.js("""(() => { const t = [...document.querySelectorAll('#tag-isi input[name=tg]')]
        .find(i => i.value === 'lampu-redup'); t.checked = true;
        t.form.querySelector('button[type=submit]').click(); })()""")
    time.sleep(1.5)
    cek("saringan tag menyaring ke gambar yang ditandai saja", n_kartu() == 2,
        "n=%s" % n_kartu())
    cek("tombol Tag menyebut tag yang dipakai",
        "lampu-redup" in teks("#tag-tombol"), teks("#tag-tombol").strip())
    d.js("document.querySelector('.saring-bersih').click()")
    time.sleep(1.4)
    cek("Bersihkan saringan juga membatalkan tag", n_kartu() == 50,
        "n=%s" % n_kartu())

    # ------------------------------------------------------------- paginasi
    d.js("""(() => { const s = document.getElementById('per');
        s.value = '100'; s.dispatchEvent(new Event('change')); })()""")
    time.sleep(1.5)
    cek("mengubah per-halaman jadi 100 menampilkan 100 kartu", n_kartu() == 100,
        "n=%s" % n_kartu())
    d.js("[...document.querySelectorAll('.hal-n')].find(a => a.textContent.trim() === '2').click()")
    time.sleep(1.5)
    cek("tombol halaman 2 menampilkan sisanya", n_kartu() == 30,
        "n=%s" % n_kartu())
    cek("penunjuknya ikut pindah", "101" in teks(".hal-info"),
        teks(".hal-info").strip())
    cek("tombol '>' mati di halaman terakhir", bool(d.js(
        "[...document.querySelectorAll('.hal-n')].pop().hasAttribute('data-mati')")))
    cek("nomor halaman yang sedang dibuka ditandai",
        d.js("(document.querySelector('.hal-n[data-on]')||{}).textContent") == "2")

    d.js("""(() => { const s = document.getElementById('per');
        s.value = '50'; s.dispatchEvent(new Event('change')); })()""")
    time.sleep(1.5)
    cek("kembali ke 50 mendarat di halaman yang memuat gambar yang sama",
        "101" in teks(".hal-info"), teks(".hal-info").strip())

    # --------------------------------------------------------- keadaan kosong
    buka("/?q=zzz-tidak-ada")
    cek("pencarian tanpa hasil mengatakan tidak ada yang cocok",
        n_kartu() == 0 and "Tidak ada yang cocok" in d.js("document.body.innerText"))
    cek("dan menyebutkan saringan yang sedang berlaku",
        bool(d.js("!!document.querySelector('.empty-saring')")),
        teks(".empty-saring").strip()[:80])
    cek("bar paginasi tidak dirender saat nol hasil",
        not d.js("!!document.querySelector('.hal')"))
    d.js("document.querySelector('.empty .saring-bersih').click()")
    time.sleep(1.4)
    cek("tombol bersihkan di kotak kosong mengembalikan isinya", n_kartu() == 50,
        "n=%s" % n_kartu())

    # ------------------------------------------------------------ kartu & aksi
    cek("tiap kartu punya tautan Edit label dan Lihat",
        bool(d.js("!!document.querySelector('.card a[href^=\"/label?path=\"]')"))
        and bool(d.js("!!document.querySelector('.card a[href^=\"/view?path=\"]')")))
    buka("/?f=unlab")
    sebelum = n_kartu()
    d.js("document.querySelector('.card button[onclick^=\"markbg\"]').click()")
    time.sleep(1.6)
    cek("tombol Latar menandai gambarnya sebagai latar",
        d.js("document.querySelectorAll('.card').length") == sebelum - 1
        or "Batal latar" in d.js("document.body.innerText"),
        "%s -> %s" % (sebelum, n_kartu()))
    buka("/?f=bg")
    cek("gambar itu pindah ke keadaan Latar", n_kartu() >= 1, "n=%s" % n_kartu())
    d.js("document.querySelector('.card button[onclick^=\"markbg\"]').click()")
    time.sleep(1.6)
    buka("/?f=bg")
    cek("Batal latar mengembalikannya", n_kartu() == 0, "n=%s" % n_kartu())

    # ------------------------------------------------------------- pindai ulang
    buka("/")
    d.js("document.querySelector('button[onclick=\"rescan()\"]').click()")
    time.sleep(2.2)
    cek("Pindai ulang tidak merusak halaman", n_kartu() == 50, "n=%s" % n_kartu())

    # ---------------------------------------------------------------- panduan
    d.js("document.getElementById('btn-panduan').click()")
    time.sleep(0.4)
    cek("tombol Panduan grid membuka panduannya",
        bool(d.js("!document.getElementById('panduan').hidden")))
    cek("panduannya menjelaskan saringan yang baru",
        "Per halaman" in d.js("document.getElementById('panduan').innerText")
        and "Bersihkan saringan" in d.js("document.getElementById('panduan').innerText"))
    d.js("document.getElementById('panduan-tutup').click()")
    time.sleep(0.3)

    # Dataset semula dibuka lagi DAN halaman kanvasnya dikembalikan: blok
    # sesudah ini mulai dengan menyentuh `S`, yang cuma ada di halaman label.
    # Meninggalkan peramban di grid membuatnya gagal dengan pesan yang tidak
    # menyebut penyebabnya sama sekali.
    d.js(f"fetch('/setsrc?path={TMP / 'datasets' / 'uji'}', {{method:'POST'}})"
         ".then(r => r.json())", tunggu=True)
    buka(f"/label?path={TMP / 'datasets' / 'uji' / 'uji-00.jpg'}")
    for _ in range(40):
        if d.js("typeof S !== 'undefined' && !!S.shapes"):
            break
        time.sleep(0.2)
    cek("halaman kanvas kembali terbuka untuk blok berikutnya",
        bool(d.js("typeof S !== 'undefined' && !!S.shapes")))



if __name__ == "__main__":
    sys.exit(main())
