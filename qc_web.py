#!/usr/bin/env python3
"""
qc_web.py — papan periksa anotasi berbasis web untuk dataset
AnyLabeling / labelme / YOLO-seg, bisa dipakai bersama satu tim.

Menjawab satu kebutuhan: melihat banyak anotasi sekaligus, menemukan yang
salah, lalu memperbaikinya.

  # sekali saja: buat akun (password diminta lewat prompt)
  python qc_web.py --adduser paul

  # pakai sendiri di laptop
  python qc_web.py --src ~/Downloads/sponge

  # dipakai tim lewat jaringan
  python qc_web.py --host 0.0.0.0 --datasets-root ~/computer-vision/datasets

Lalu buka http://127.0.0.1:8042 di browser (atau http://<ip-server>:8042).

Fitur
  - Grid thumbnail dengan mask ter-overlay
  - Saring: semua / bermasalah / belum dilabeli / per kelas
  - Klik kartu -> tampilan besar
  - Pindai ulang tanpa restart
  - Login per akun; tiap akun punya dataset dan thumbnail sendiri, jadi satu
    orang ganti folder tidak mengubah tampilan orang lain
  - Unggah gambar langsung dari laptop, masuk ke folder milik akun itu
  - Tombol "Buka di AnyLabeling" hanya aktif untuk akses dari mesin server,
    karena jendela Qt-nya muncul di layar server, bukan di layar pemakai

Prasyarat: opencv-python, numpy. Tidak perlu Flask — memakai http.server bawaan.

Keamanan: tidak ada TLS. Untuk dipakai lewat internet, taruh di belakang
reverse proxy ber-HTTPS, atau batasi aksesnya lewat firewall / VPN.
"""

import argparse
import colorsys
import errno
import getpass
import hashlib
import hmac
import html
import http.cookies
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse as up
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")

JSON_EXT = (".json", ".txt")

# Setelan server: sama untuk semua akun, ditentukan sekali lewat argumen CLI.
CFG = {"anylabeling": "anylabeling", "extra_labels": [], "lock_labels": False,
       "open_mode": "file", "datasets_root": None, "uploads_root": None,
       "thumbroot": None, "max_upload_mb": 80, "users": {}, "users_path": None,
       "default_src": None}

# Sesi aktif: sid (cookie) -> Session. Hilang saat server restart, jadi
# semua orang login ulang — itu disengaja, tidak ada sesi yang menggantung.
SESSIONS = {}
SESS_LOCK = threading.Lock()


class Session:
    """
    Keadaan milik satu akun: folder yang sedang dibuka, hasil pindai, dan
    folder thumbnail sendiri. Dipisah supaya satu orang ganti folder tidak
    mengubah tampilan orang lain.
    """

    def __init__(self, user):
        self.user = user
        self.src = None
        self.items = []
        self.names = {}
        self.labelfile = None
        self.thumbdir = Path(CFG["thumbroot"]) / safe_slug(user)
        self.thumbdir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def load(self, src: Path):
        """Pindai folder baru dan buang thumbnail folder sebelumnya."""
        self.src = Path(src).resolve()
        self.items, self.names = scan(self.src)
        self.reset_thumbs()
        write_label_file(self)
        return self.items

    def reset_thumbs(self):
        shutil.rmtree(self.thumbdir, ignore_errors=True)
        self.thumbdir.mkdir(parents=True, exist_ok=True)

    def upload_dir(self, ds: str) -> Path:
        return Path(CFG["uploads_root"]) / safe_slug(self.user) / safe_slug(ds)


# ============================================================ akun

def safe_slug(s: str) -> str:
    """Nama akun / dataset -> nama folder yang aman (tanpa '..' atau '/')."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(s or "").strip()).strip("-.")
    return s[:64] or "tanpa-nama"


def user_slug(s: str) -> str:
    """
    Nama akun selalu huruf kecil. Tanpa ini, akun dibuat 'Budi' tapi diketik
    'budi' saat login akan ditolak — jebakan yang tidak perlu.
    """
    return safe_slug(s).lower()


def safe_filename(s: str) -> str:
    """
    Ambil nama berkas saja dari kiriman klien dan buang komponen path apa pun.
    Menolak nama tanpa ekstensi yang dikenal.
    """
    base = os.path.basename(str(s or "").replace("\\", "/"))
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-.")
    if not base or base.startswith("."):
        return ""
    ext = Path(base).suffix.lower()
    if ext not in IMG_EXT + JSON_EXT:
        return ""
    return base[:120]


def hash_pw(pw: str, salt: str = None, iters: int = 200_000) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), iters)
    return f"pbkdf2_sha256${iters}${salt}${dk.hex()}"


def verify_pw(pw: str, stored: str) -> bool:
    try:
        algo, iters, salt, want = str(stored).split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), want)


def load_users(p) -> dict:
    if not p or not Path(p).exists():
        return {}
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError as e:
        raise SystemExit(f"\n  {p} bukan JSON yang sah — {e}\n")


def add_user(users_path: Path, name: str):
    """Buat atau ganti password satu akun, lalu keluar. Password tidak diketik
    di argumen supaya tidak tertinggal di riwayat shell."""
    users = load_users(users_path)
    akun = user_slug(name)
    ada = akun in users
    print(f"  {'Ganti password' if ada else 'Akun baru'} : {akun}")
    pw = getpass.getpass("  Password        : ")
    if len(pw) < 8:
        raise SystemExit("\n  Password minimal 8 karakter.\n")
    if pw != getpass.getpass("  Ulangi          : "):
        raise SystemExit("\n  Password tidak sama.\n")
    users[akun] = {"hash": hash_pw(pw), "nama": name.strip() or akun}
    users_path.parent.mkdir(parents=True, exist_ok=True)
    users_path.write_text(json.dumps(users, indent=2, ensure_ascii=False) + "\n")
    os.chmod(users_path, 0o600)
    print(f"\n  Tersimpan di {users_path} ({len(users)} akun).\n")


# ============================================================ data

def cls_color(key):
    h = (abs(hash(str(key))) % 997) / 997.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.78, 0.92)
    return int(r * 255), int(g * 255), int(b * 255)


def poly_area(p):
    x, y = p[:, 0], p[:, 1]
    return abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) / 2.0


def read_json(jp):
    d = json.loads(Path(jp).read_text(encoding="utf-8"))
    W, H = d.get("imageWidth"), d.get("imageHeight")
    shapes = []
    for s in d.get("shapes", []):
        pts = s.get("points") or []
        if s.get("shape_type") == "rectangle" and len(pts) == 2:
            (x1, y1), (x2, y2) = pts
            pts = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
        if len(pts) < 3:
            continue
        shapes.append({"label": s.get("label"), "type": s.get("shape_type", "polygon"),
                       "pts": np.array(pts, np.float32)})
    return shapes, W, H


def read_yolo(tp, W, H, names):
    shapes = []
    if not os.path.exists(tp):
        return shapes
    for line in Path(tp).read_text().strip().splitlines():
        p = line.split()
        if len(p) < 5:
            continue
        cid = int(float(p[0]))
        v = [float(x) for x in p[1:]]
        if len(v) == 4:
            cx, cy, bw, bh = v
            x1, y1, x2, y2 = (cx - bw / 2) * W, (cy - bh / 2) * H, (cx + bw / 2) * W, (cy + bh / 2) * H
            pts = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
        else:
            pts = [[v[i] * W, v[i + 1] * H] for i in range(0, len(v) - 1, 2)]
        if len(pts) < 3:
            continue
        shapes.append({"label": names.get(cid, str(cid)), "type": "polygon",
                       "pts": np.array(pts, np.float32)})
    return shapes


def inspect(shapes, W, H, has_json=False):
    if not shapes:
        return ["latar (tanpa objek)"] if has_json else ["belum dilabeli"]
    out, frame = [], (W or 1) * (H or 1)
    for s in shapes:
        if s["label"] is None:
            out.append("label kosong")
        if len(s["pts"]) < 8 and s["type"] != "rectangle":
            out.append(f"hanya {len(s['pts'])} titik")
        a = poly_area(s["pts"])
        if a / frame < 0.002:
            out.append("mask sangat kecil")
        if a / frame > 0.92:
            out.append("mask memenuhi frame")
        x, y = s["pts"][:, 0], s["pts"][:, 1]
        if (x < -1).any() or (y < -1).any() or (x > W + 1).any() or (y > H + 1).any():
            out.append("titik di luar gambar")
    return sorted(set(out))


def scan(src: Path):
    items, names = [], {}
    yolo = (src / "images").is_dir() and (src / "labels").is_dir()

    if yolo:
        cf = src / "classes.txt"
        if cf.exists():
            names = {i: n.strip() for i, n in enumerate(cf.read_text().splitlines()) if n.strip()}
        for ip in sorted(p for p in (src / "images").iterdir() if p.suffix.lower() in IMG_EXT):
            im = cv2.imread(str(ip))
            if im is None:
                continue
            H, W = im.shape[:2]
            tp = src / "labels" / (ip.stem + ".txt")
            sh = read_yolo(tp, W, H, names)
            items.append({"img": ip, "shapes": sh, "W": W, "H": H,
                          "issues": inspect(sh, W, H, tp.exists())})
    else:
        seen, broken = set(), set()
        for jp in sorted(src.rglob("*.json")):
            try:
                sh, W, H = read_json(jp)
            except Exception:
                broken.add(jp.stem)
                continue
            ip = None
            for e in IMG_EXT:
                for c in (jp.with_suffix(e), jp.with_suffix(e.upper())):
                    if c.exists():
                        ip = c
                        break
                if ip:
                    break
            if not ip:
                continue
            seen.add(ip.resolve())
            items.append({"img": ip, "shapes": sh, "W": W, "H": H,
                          "issues": inspect(sh, W, H, True)})
        # gambar yang belum punya JSON sama sekali
        for ip in sorted(p for p in src.rglob("*") if p.suffix.lower() in IMG_EXT):
            if ip.resolve() in seen:
                continue
            im = cv2.imread(str(ip))
            if im is None:
                continue
            H, W = im.shape[:2]
            iss = ["berkas anotasi rusak"] if ip.stem in broken else ["belum dilabeli"]
            items.append({"img": ip, "shapes": [], "W": W, "H": H, "issues": iss})

    items.sort(key=lambda it: it["img"].name)
    return items, names


def write_label_file(sess):
    """
    Kumpulkan semua label yang sudah dipakai di folder + label tambahan dari
    --labels, tulis ke satu berkas. Berkas ini diteruskan ke AnyLabeling lewat
    --labels sehingga daftar kelas sudah terisi dan tidak perlu diketik ulang.
    """
    used = {str(s["label"]).strip() for it in sess.items for s in it["shapes"]
            if s["label"] is not None and str(s["label"]).strip()}
    allv = sorted(used | set(CFG["extra_labels"]))
    p = sess.thumbdir / "labels.txt"
    p.write_text("\n".join(allv) + "\n")
    sess.labelfile = p if allv else None
    return allv


def launch_anylabeling(sess, img_path: Path):
    """
    open_mode 'dir'  -> buka seluruh folder, sehingga tombol A/D (gambar
                        sebelumnya/berikutnya) di AnyLabeling berfungsi.
    open_mode 'file' -> buka satu berkas saja.
    """
    target = str(img_path.parent if CFG["open_mode"] == "dir" else img_path)
    cmd = [CFG["anylabeling"], target, "--autosave"]
    if sess.labelfile:
        cmd += ["--labels", str(sess.labelfile)]
        if CFG["lock_labels"]:
            cmd += ["--validatelabel", "exact"]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return cmd


def render(item, side):
    im = cv2.imread(str(item["img"]))
    if im is None:
        return None
    ov = im.copy()
    for s in item["shapes"]:
        col = cls_color(s["label"])[::-1]
        pts = s["pts"].astype(np.int32)
        cv2.fillPoly(ov, [pts], col)
        cv2.polylines(im, [pts], True, col, max(2, int(min(im.shape[:2]) / 200)))
    im = cv2.addWeighted(ov, 0.34, im, 0.66, 0)
    h, w = im.shape[:2]
    sc = side / max(h, w)
    return cv2.resize(im, (max(1, int(w * sc)), max(1, int(h * sc))), interpolation=cv2.INTER_AREA)


def thumb_path(sess, item, side):
    key = f"{abs(hash(str(item['img'].resolve())))}_{side}.jpg"
    p = sess.thumbdir / key
    if not p.exists():
        im = render(item, side)
        if im is None:
            return None
        cv2.imwrite(str(p), im, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
    return p


# ============================================================ tampilan

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --ground:#f7f8fa; --panel:#ffffff; --line:#e3e7ec; --line2:#cdd4dc;
  --ink:#1b2430; --ink-dim:#5f6d7e; --ink-faint:#93a0ae;
  --ok:#1f9d8f; --warn:#c47a10; --stop:#d64a2c;
}
body{background:var(--ground);color:var(--ink);
  font:14px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.mono{font-family:ui-monospace,"JetBrains Mono","SF Mono",Menlo,monospace}

header{position:sticky;top:0;z-index:20;background:rgba(247,248,250,.94);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:14px 22px 0}
.top{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
h1{font-size:15px;font-weight:600;letter-spacing:.02em}
.path{font-size:12px;color:var(--ink-faint)}

/* rekaman kesehatan dataset: tiap tick = 1 gambar */
.strip{display:flex;gap:1px;height:16px;margin:12px 0 0;border-radius:2px;overflow:hidden}
.strip i{flex:1 1 auto;min-width:1px}
.strip i.ok{background:#bcd8d4}.strip i.warn{background:var(--warn)}
.strip i.stop{background:var(--stop)}

.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:12px 0 13px}
.chip{border:1px solid var(--line2);background:transparent;color:var(--ink-dim);
  padding:5px 11px;border-radius:999px;font-size:12.5px;cursor:pointer;
  text-decoration:none;display:inline-block;transition:.12s}
.chip:hover{border-color:var(--ink-faint);color:var(--ink)}
.chip[data-on]{background:var(--ink);color:#fff;border-color:var(--ink);font-weight:600}
.chip b{font-variant-numeric:tabular-nums}
.spacer{flex:1}

main{padding:20px 22px 60px}
.grid{display:grid;gap:14px;
  grid-template-columns:repeat(auto-fill,minmax(210px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  overflow:hidden;position:relative;transition:.14s;
  box-shadow:0 1px 2px rgba(20,30,45,.05)}
.card:hover{border-color:var(--line2);transform:translateY(-2px);
  box-shadow:0 6px 16px rgba(20,30,45,.10)}
.card .rail{position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--ok);z-index:2}
.card[data-s="warn"] .rail{background:var(--warn)}
.card[data-s="stop"] .rail{background:var(--stop)}
.card[data-s="bg"] .rail{background:#7398c4}
.strip i.bg{background:#b9c9dc}
.card img{width:100%;aspect-ratio:1;object-fit:contain;display:block;background:#eef1f5}
.meta{padding:9px 11px 10px}
.fn{font-size:11.5px;color:var(--ink-dim);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;direction:rtl;text-align:left}
.labs{margin-top:5px;display:flex;gap:4px;flex-wrap:wrap}
.lab{font-size:10.5px;padding:1px 6px;border-radius:3px;background:#eaeef3;color:#4a5a6b}
.iss{margin-top:6px;font-size:10.5px;color:var(--warn);line-height:1.35}
.iss.stop{color:var(--stop)}
.acts{display:flex;gap:6px;padding:0 11px 11px}
.btn{flex:1;text-align:center;border:1px solid var(--line2);background:#fbfcfd;
  color:var(--ink-dim);border-radius:5px;padding:5px 0;font-size:11.5px;cursor:pointer;
  text-decoration:none;transition:.12s}
.btn:hover{border-color:var(--ok);color:var(--ok)}
.btn.pri:hover{border-color:var(--warn);color:var(--warn)}

.hero{max-width:520px;margin:16vh auto 0;text-align:center;padding:0 24px}
.hero h2{font-size:22px;font-weight:600;letter-spacing:.01em}
.hero p{margin-top:10px;color:var(--ink-dim);font-size:13.5px;line-height:1.6}
.cta{margin-top:26px;background:var(--ink);color:#fff;border:0;
  padding:11px 26px;border-radius:7px;font-size:14px;font-weight:600;cursor:pointer;
  transition:.14s}
.cta:hover{background:var(--ok);color:#fff}
.empty{padding:70px 0;text-align:center;color:var(--ink-faint)}
.empty p{margin-top:8px;font-size:13px}

/* tampilan besar */
.big{max-width:min(1100px,100%);margin:0 auto}
.big img{max-width:100%;max-height:calc(100vh - 210px);width:auto;height:auto;display:block;margin:0 auto;border-radius:8px;border:1px solid var(--line);background:#eef1f5}
.side{margin-top:16px;display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}
.box{background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:12px 14px}
.box h3{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:var(--ink-faint);
  font-weight:600;margin-bottom:7px}
.box p{font-size:13px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
td{padding:3px 0;border-bottom:1px solid var(--line)}
td:last-child{text-align:right;color:var(--ink-dim);font-variant-numeric:tabular-nums}
.toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);
  background:var(--ink);color:#fff;padding:9px 18px;border-radius:6px;
  font-size:13px;font-weight:500;opacity:0;pointer-events:none;transition:.2s;z-index:50;
  box-shadow:0 4px 14px rgba(20,30,45,.22)}
.toast[data-on]{opacity:1}

/* login, pemilih dataset, upload */
.who{font-size:12px;color:var(--ink-faint)}
.who b{color:var(--ink-dim);font-weight:600}
.card-form{max-width:390px;margin:14vh auto 0;background:var(--panel);
  border:1px solid var(--line);border-radius:10px;padding:26px 26px 22px;
  box-shadow:0 4px 20px rgba(20,30,45,.06)}
.card-form h2{font-size:18px;font-weight:600;margin-bottom:4px}
.card-form .sub{color:var(--ink-dim);font-size:13px;margin-bottom:18px}
label{display:block;font-size:11.5px;text-transform:uppercase;letter-spacing:.07em;
  color:var(--ink-faint);font-weight:600;margin:12px 0 5px}
input[type=text],input[type=password]{width:100%;border:1px solid var(--line2);
  background:#fbfcfd;color:var(--ink);border-radius:6px;padding:9px 11px;font-size:13.5px;
  font-family:inherit}
input:focus{outline:0;border-color:var(--ok)}
.card-form .cta{width:100%;margin-top:20px}
.bad{margin-top:14px;color:var(--stop);font-size:12.5px}

.pick{max-width:660px;margin:8vh auto 0;padding:0 20px}
.pick h2{font-size:20px;font-weight:600;text-align:center}
.pick .sub{text-align:center;color:var(--ink-dim);font-size:13.5px;margin-top:8px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:9px;
  padding:16px 18px;margin-top:16px}
.panel h3{font-size:11px;text-transform:uppercase;letter-spacing:.09em;
  color:var(--ink-faint);font-weight:600;margin-bottom:11px}
.dslist{display:flex;flex-direction:column;gap:1px}
.ds{display:flex;align-items:center;gap:10px;padding:9px 11px;border-radius:6px;
  border:1px solid transparent;cursor:pointer;text-decoration:none;color:var(--ink);
  font-size:13.5px;transition:.12s}
.ds:hover{background:#f3f6f9;border-color:var(--line)}
.ds .n{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ds .c{font-size:11.5px;color:var(--ink-faint);font-variant-numeric:tabular-nums}
.row{display:flex;gap:8px;align-items:flex-end}
.row input{flex:1}
.row .cta{width:auto;margin-top:0;padding:9px 18px;font-size:13px}
.drop{border:1.5px dashed var(--line2);border-radius:8px;padding:22px 16px;
  text-align:center;color:var(--ink-dim);font-size:13px;cursor:pointer;transition:.14s}
.drop:hover,.drop[data-over]{border-color:var(--ok);background:#f2faf9;color:var(--ink)}
.prog{height:5px;background:#e9edf2;border-radius:99px;overflow:hidden;margin-top:12px;
  display:none}
.prog[data-on]{display:block}
.prog i{display:block;height:100%;width:0;background:var(--ok);transition:width .18s}
.upnote{margin-top:9px;font-size:12px;color:var(--ink-dim);text-align:center}
"""

JS = """
function toast(m){const t=document.getElementById('t');t.textContent=m;
  t.setAttribute('data-on','');setTimeout(()=>t.removeAttribute('data-on'),2200);}
async function openIn(p,btn){
  const old=btn.textContent; btn.textContent='Membuka...';
  try{
    const r=await fetch('/open?path='+encodeURIComponent(p),{method:'POST'});
    const j=await r.json();
    toast(j.ok?('AnyLabeling: '+(j.msg||'dibuka')):'Gagal: '+j.error);
  }catch(e){toast('Gagal menghubungi server');}
  btn.textContent=old;
}
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT')return;
  const g=d=>{const a=document.querySelector(d);if(a&&a.href)location.href=a.href;};
  if(e.key==='ArrowLeft') g('a.chip[href^="/view"]');
  if(e.key==='ArrowRight'){const l=document.querySelectorAll('a.chip[href^="/view"]');
    if(l.length>1)location.href=l[l.length-1].href;else if(l.length)location.href=l[0].href;}
});
async function pickdir(){
  toast('Dialog terbuka di desktop...');
  try{
    const r=await fetch('/pickdir',{method:'POST'});
    const j=await r.json();
    if(j.ok){toast('Memindai folder...');location.href='/';}
    else{toast(j.error);}
  }catch(e){toast('Gagal membuka dialog');}
}
async function markbg(p,on){
  const r=await fetch((on?'/markbg':'/unmarkbg')+'?path='+encodeURIComponent(p),{method:'POST'});
  const j=await r.json();
  toast(j.ok?j.msg:'Gagal: '+j.error);
  if(j.ok)setTimeout(()=>location.reload(),450);
}
async function rescan(){toast('Memindai ulang...');
  await fetch('/rescan',{method:'POST'});location.reload();}

// ---- pemilih dataset ----
async function setsrc(p){
  if(!p){toast('Path masih kosong');return;}
  toast('Memindai folder...');
  try{
    const r=await fetch('/setsrc?path='+encodeURIComponent(p),{method:'POST'});
    const j=await r.json();
    if(j.ok)location.href='/'; else toast(j.error);
  }catch(e){toast('Gagal menghubungi server');}
}
function setsrcBox(){setsrc(document.getElementById('pathbox').value.trim());}

// ---- upload dari laptop ----
// Tiap berkas dikirim satu-satu lewat PUT dengan bodi mentah. Tanpa multipart,
// jadi tidak ada berkas besar yang ditahan di memori server, dan progres bisa
// dihitung per berkas.
const UP_EXT=['.jpg','.jpeg','.png','.bmp','.webp','.tif','.tiff','.json','.txt'];
async function uploadFiles(files){
  const ds=(document.getElementById('dsname').value||'').trim();
  if(!ds){toast('Beri nama dataset dulu');return;}
  const ok=[...files].filter(f=>UP_EXT.some(e=>f.name.toLowerCase().endsWith(e)));
  if(!ok.length){toast('Tidak ada gambar atau .json di pilihan itu');return;}
  const bar=document.getElementById('prog'),fill=document.getElementById('fill');
  bar.setAttribute('data-on','');
  let done=0,gagal=0;
  for(const f of ok){
    try{
      const r=await fetch('/upload?ds='+encodeURIComponent(ds)+'&name='+encodeURIComponent(f.name),
                          {method:'PUT',body:f});
      const j=await r.json();
      if(!j.ok){gagal++;if(gagal<3)toast(f.name+': '+j.error);}
    }catch(e){gagal++;}
    done++;
    fill.style.width=Math.round(done*100/ok.length)+'%';
    document.getElementById('upnote').textContent=
      done+' / '+ok.length+' terkirim'+(gagal?(' · '+gagal+' gagal'):'');
  }
  if(done>gagal){
    toast('Selesai — membuka dataset');
    const r=await fetch('/useupload?ds='+encodeURIComponent(ds),{method:'POST'});
    const j=await r.json();
    if(j.ok)location.href='/'; else toast(j.error);
  }else{toast('Semua berkas gagal terkirim');}
}
function wireDrop(){
  const d=document.getElementById('drop'),inp=document.getElementById('files');
  if(!d)return;
  d.onclick=()=>inp.click();
  inp.onchange=()=>uploadFiles(inp.files);
  d.ondragover=e=>{e.preventDefault();d.setAttribute('data-over','');};
  d.ondragleave=()=>d.removeAttribute('data-over');
  d.ondrop=e=>{e.preventDefault();d.removeAttribute('data-over');
    uploadFiles(e.dataTransfer.files);};
}
document.addEventListener('DOMContentLoaded',wireDrop);
"""


def page(body, title="Periksa anotasi"):
    return f"""<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{CSS}</style></head><body>
{body}<div class="toast" id="t"></div><script>{JS}</script></body></html>"""


def native_pick_dir(start: str = None):
    """
    Munculkan dialog pilih folder milik sistem (GTK/KDE/Tk) di mesin tempat
    server berjalan, lalu kembalikan path yang dipilih.

    Urutan percobaan: zenity -> kdialog -> tkinter.
    Return: (path | None, pesan_error | None)
    """
    start = start or str(Path.home())

    if shutil.which("zenity"):
        try:
            r = subprocess.run(
                ["zenity", "--file-selection", "--directory",
                 "--title=Pilih folder dataset", f"--filename={start}/"],
                capture_output=True, text=True, timeout=300)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip(), None
            return None, "dibatalkan"
        except subprocess.TimeoutExpired:
            return None, "dialog terlalu lama, dibatalkan"
        except Exception as e:
            return None, str(e)[:80]

    if shutil.which("kdialog"):
        try:
            r = subprocess.run(["kdialog", "--getexistingdirectory", start],
                               capture_output=True, text=True, timeout=300)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip(), None
            return None, "dibatalkan"
        except Exception as e:
            return None, str(e)[:80]

    # Tkinter dijalankan sebagai proses terpisah supaya tidak bentrok
    # dengan thread server.
    code = (
        "import tkinter as tk;from tkinter import filedialog;"
        "r=tk.Tk();r.withdraw();r.attributes('-topmost',True);"
        f"p=filedialog.askdirectory(title='Pilih folder dataset',initialdir={start!r});"
        "print(p or '')"
    )
    try:
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=300)
        out = r.stdout.strip()
        if out:
            return out, None
        return None, "dibatalkan"
    except Exception:
        return None, ("tidak ada dialog sistem yang tersedia — "
                      "pasang zenity, atau pakai penjelajah di halaman ini")


class Menolak(Exception):
    """Operasi ditolak karena akan menghapus data."""


def mark_background(it):
    """
    Tulis berkas anotasi kosong (shapes: []) di samping gambar.
    Setara 'Mark Null' di Roboflow: gambar ikut ke dataset sebagai contoh
    negatif, bukan dibuang. AnyLabeling sendiri menolak menyimpan berkas
    tanpa shape, jadi ditulis langsung dari sini.
    """
    if it["shapes"]:
        raise Menolak("gambar ini punya %d objek — hapus dulu anotasinya di AnyLabeling"
                      % len(it["shapes"]))
    if "berkas anotasi rusak" in it["issues"]:
        raise Menolak("berkas anotasi rusak — periksa atau hapus manual dulu")
    jp = it["img"].with_suffix(".json")
    json.dump({
        "version": "0.4.36", "flags": {}, "shapes": [],
        "imagePath": it["img"].name, "imageData": None,
        "imageHeight": it["H"], "imageWidth": it["W"],
    }, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    it["shapes"] = []
    it["issues"] = ["latar (tanpa objek)"]
    return jp


def unmark_background(it):
    if it["shapes"]:
        raise Menolak("gambar ini punya anotasi — tidak dihapus")
    jp = it["img"].with_suffix(".json")
    if jp.exists():
        try:
            d = json.loads(jp.read_text(encoding="utf-8"))
            if d.get("shapes"):
                raise Menolak("berkas anotasi tidak kosong — tidak dihapus")
        except Menolak:
            raise
        except Exception:
            pass
        jp.unlink()
    it["issues"] = ["belum dilabeli"]
    return jp


def login_html(err=None, akun=""):
    bad = f'<p class="bad">{html.escape(err)}</p>' if err else ""
    return page(f"""<main><form class="card-form" method="POST" action="/login">
<h2>Periksa anotasi</h2><p class="sub">Masuk dengan akun tim.</p>
<label for="u">Nama akun</label>
<input id="u" name="user" type="text" autocomplete="username" autofocus
 value="{html.escape(akun)}">
<label for="p">Password</label>
<input id="p" name="pw" type="password" autocomplete="current-password">
<button class="cta" type="submit">Masuk</button>{bad}
</form></main>""", "Masuk")


def count_images(d: Path, cap=2000):
    """Hitung gambar di dalam folder, berhenti di cap supaya folder raksasa
    tidak membuat halaman awal lambat."""
    n = 0
    for p in d.rglob("*"):
        if p.suffix.lower() in IMG_EXT:
            n += 1
            if n >= cap:
                return n, True
    return n, False


def list_dirs(root):
    """Subfolder berisi gambar di dalam root -> [(nama, path, jumlah, lebih)]."""
    if not root or not Path(root).is_dir():
        return []
    out = []
    for d in sorted(Path(root).iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        n, more = count_images(d)
        if n:
            out.append((d.name, d, n, more))
    return out


def landing_html(sess, err=None, local=False):
    warn = f'<p class="bad" style="text-align:center">{html.escape(err)}</p>' if err else ""

    def dslist(rows):
        out = []
        for nama, d, n, more in rows:
            out.append(
                '<a class="ds" onclick="setsrc(\'%s\');return false" href="#">'
                '<span class="n">%s</span><span class="c">%s%d gambar</span></a>'
                % (html.escape(str(d.resolve()), quote=True), html.escape(nama),
                   "≥" if more else "", n))
        return "".join(out)

    blocks = ""

    rows = list_dirs(CFG["datasets_root"])
    if CFG["datasets_root"]:
        isi = dslist(rows) or ('<p class="sub" style="text-align:left;margin:0">'
                               'Belum ada subfolder berisi gambar di '
                               f'<span class="mono">{html.escape(str(CFG["datasets_root"]))}</span>.</p>')
        blocks += f'<div class="panel"><h3>Dataset tersedia</h3><div class="dslist">{isi}</div></div>'

    mine = list_dirs(Path(CFG["uploads_root"]) / safe_slug(sess.user))
    if mine:
        blocks += ('<div class="panel"><h3>Hasil unggahanmu</h3>'
                   f'<div class="dslist">{dslist(mine)}</div></div>')

    blocks += f"""<div class="panel"><h3>Unggah dari laptop</h3>
<label for="dsname">Nama dataset</label>
<input id="dsname" type="text" placeholder="mis. sponge-batch-3">
<div class="drop" id="drop" style="margin-top:12px">
Tarik gambar ke sini, atau klik untuk memilih<br>
<span style="font-size:11.5px;color:var(--ink-faint)">
gambar dan berkas .json / .txt · maks {CFG['max_upload_mb']} MB per berkas</span></div>
<input id="files" type="file" multiple hidden
 accept=".jpg,.jpeg,.png,.bmp,.webp,.tif,.tiff,.json,.txt">
<div class="prog" id="prog"><i id="fill"></i></div>
<p class="upnote" id="upnote"></p></div>"""

    blocks += """<div class="panel"><h3>Path folder di server</h3>
<div class="row"><div style="flex:1"><input id="pathbox" type="text"
 placeholder="/home/paul/computer-vision/datasets/nama-folder"></div>
<button class="cta" onclick="setsrcBox()">Buka</button></div></div>"""

    if local:
        blocks += ('<div class="panel"><h3>Dialog desktop</h3>'
                   '<button class="cta" style="width:auto;margin-top:0;padding:9px 18px;'
                   'font-size:13px" onclick="pickdir()">Buka penjelajah berkas</button>'
                   '<p class="sub" style="text-align:left;margin:10px 0 0">Hanya muncul '
                   'saat kamu membuka dari mesin server sendiri.</p></div>')

    return page(f"""<header><div class="top"><h1>Periksa anotasi</h1>
<span class="spacer"></span>
<span class="who">masuk sebagai <b>{html.escape(sess.user)}</b></span>
<a class="chip" href="/logout">Keluar</a></div><div style="height:12px"></div></header>
<main><div class="pick"><h2>Pilih dataset</h2>
<p class="sub">Folder berisi gambar beserta anotasi <span class="mono">.json</span>, atau
dataset YOLO dengan subfolder <span class="mono">images</span> dan
<span class="mono">labels</span>.</p>{warn}{blocks}</div></main>""", "Pilih dataset")


def severity(it):
    if not it["shapes"]:
        if "latar (tanpa objek)" in it["issues"]:
            return "bg"
        return "stop"
    return "warn" if it["issues"] else "ok"


def index_html(sess, flt, cls_filter, local=False):
    items = sess.items
    total = len(items)
    n_warn = sum(1 for i in items if severity(i) == "warn")
    n_stop = sum(1 for i in items if severity(i) == "stop")
    n_obj = sum(len(i["shapes"]) for i in items)

    sel = items
    if flt == "issue":
        sel = [i for i in items if i["issues"] and i["shapes"]]
    elif flt == "unlab":
        sel = [i for i in items if not i["shapes"]]
    if cls_filter:
        sel = [i for i in sel if any(str(s["label"]) == cls_filter for s in i["shapes"])]

    labs = sorted({str(s["label"]) for i in items for s in i["shapes"]})

    strip = "".join(f'<i class="{severity(i)}"></i>' for i in items[:400])

    def chip(name, val, count, active):
        q = f"?f={val}" + (f"&c={up.quote(cls_filter)}" if cls_filter else "")
        on = " data-on" if active else ""
        return f'<a class="chip"{on} href="{q}">{name} <b>{count}</b></a>'

    chips = (chip("Semua", "all", total, flt == "all")
             + chip("Perlu dicek", "issue", n_warn, flt == "issue")
             + chip("Belum dilabeli", "unlab", n_stop, flt == "unlab"))

    clschips = '<a class="chip"%s href="?f=%s">semua kelas</a>' % ("" if cls_filter else " data-on", flt)
    for l in labs:
        on = " data-on" if cls_filter == l else ""
        c = sum(1 for i in items for s in i["shapes"] if str(s["label"]) == l)
        clschips += f'<a class="chip"{on} href="?f={flt}&c={up.quote(l)}">{html.escape(l)} <b>{c}</b></a>'

    cards = []
    for it in sel:
        p = up.quote(str(it["img"].resolve()))
        ll = sorted({str(s["label"]) for s in it["shapes"]})
        labhtml = "".join(f'<span class="lab">{html.escape(x)}</span>' for x in ll[:4])
        sev = severity(it)
        if sev == "bg":
            bgbtn = f"""<button class="btn" onclick="markbg('{p}',false)">Batal latar</button>"""
        elif not it["shapes"]:
            bgbtn = f"""<button class="btn" onclick="markbg('{p}',true)">Latar</button>"""
        else:
            bgbtn = ""
        iss = ""
        if it["issues"]:
            iss = (f'<div class="iss{" stop" if sev == "stop" else ""}">'
                   f'{html.escape(" · ".join(it["issues"]))}</div>')
        fixbtn = (f"""<button class="btn pri" onclick="openIn('{p}',this)">Perbaiki</button>"""
                  if local else "")
        cards.append(f"""<div class="card" data-s="{sev}"><span class="rail"></span>
<a href="/view?path={p}"><img loading="lazy" src="/thumb?path={p}&s=320"></a>
<div class="meta"><div class="fn mono">{html.escape(it['img'].name)}</div>
<div class="labs">{labhtml}</div>{iss}</div>
<div class="acts"><a class="btn" href="/view?path={p}">Lihat</a>
{fixbtn}{bgbtn}</div></div>""")

    if not cards:
        cards = ['<div class="empty" style="grid-column:1/-1"><strong>Tidak ada yang cocok</strong>'
                 '<p>Ubah saringan di atas, atau pindai ulang setelah menambah anotasi.</p></div>']

    return page(f"""<header>
<div class="top"><h1>Periksa anotasi</h1>
<span class="path mono">{html.escape(str(sess.src))}</span>
<span class="spacer"></span>
<span class="who">masuk sebagai <b>{html.escape(sess.user)}</b></span>
<a class="chip" href="/logout">Keluar</a></div>
<div class="strip">{strip}</div>
<div class="bar">{chips}<span class="spacer"></span>
<span class="path mono">{n_obj} objek</span>
<button class="chip" onclick="rescan()">Pindai ulang</button>
<a class="chip" href="/pilih">Ganti dataset</a></div>
<div class="bar" style="padding-top:0">{clschips}</div>
</header><main><div class="grid">{''.join(cards)}</div></main>""")


def view_html(it, seq=None, local=False):
    p = up.quote(str(it["img"].resolve()))
    nav = ""
    if seq:
        idx, prev_it, next_it = seq
        def lk(target, txt, dis):
            if dis:
                return f'<span class="chip" style="opacity:.35">{txt}</span>'
            q = up.quote(str(target["img"].resolve()))
            return f'<a class="chip" href="/view?path={q}">{txt}</a>'
        nav = (lk(prev_it, "&larr; Sebelumnya", prev_it is None)
               + lk(next_it, "Berikutnya &rarr;", next_it is None)
               + f'<span class="path mono" style="margin-left:6px">{idx[0]} / {idx[1]}</span>')
    cnt = {}
    for s in it["shapes"]:
        cnt[str(s["label"])] = cnt.get(str(s["label"]), 0) + 1
    rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{v}</td></tr>"
                   for k, v in sorted(cnt.items())) or "<tr><td colspan=2>—</td></tr>"
    if severity(it) == "bg":
        bgchip = f"""<button class="chip" onclick="markbg('{p}',false)">Batal tandai latar</button>"""
    elif not it["shapes"]:
        bgchip = f"""<button class="chip" onclick="markbg('{p}',true)">Tandai sebagai latar</button>"""
    else:
        bgchip = ""
    iss = " · ".join(it["issues"]) if it["issues"] else "Tidak ada temuan"
    col = "var(--warn)" if it["issues"] else "var(--ok)"
    return page(f"""<header><div class="top"><h1>{html.escape(it['img'].name)}</h1>
<span class="path mono">{it['W']}×{it['H']} px</span></div>
<div class="bar"><a class="chip" href="/">Kembali</a>
{'<button class="chip" onclick="openIn(&apos;%s&apos;,this)">Perbaiki di AnyLabeling</button>' % p if local else ''}
{bgchip}<span class="spacer"></span>{nav}</div>
</header><main><div class="big">
<img src="/thumb?path={p}&s=1100">
<div class="side">
<div class="box"><h3>Temuan</h3><p style="color:{col}">{html.escape(iss)}</p></div>
<div class="box"><h3>Objek per kelas</h3><table>{rows}</table></div>
<div class="box"><h3>Berkas</h3><p class="mono" style="font-size:11.5px;color:var(--ink-dim);
word-break:break-all">{html.escape(str(it['img']))}</p></div>
</div></div></main>""", it["img"].name)


# ============================================================ server

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj), "application/json")

    def _redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- sesi ----

    def _local(self):
        """True kalau permintaan datang dari mesin server sendiri. Dipakai
        untuk membatasi endpoint yang memunculkan jendela di desktop."""
        return self.client_address[0] in ("127.0.0.1", "::1")

    def _sess(self):
        c = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        m = c.get("qcsid")
        if not m:
            return None
        with SESS_LOCK:
            return SESSIONS.get(m.value)

    def _body(self, cap=64 * 1024):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return b""
        return self.rfile.read(min(n, cap)) if n > 0 else b""

    def _item(self, sess, path):
        rp = Path(path).resolve()
        for it in sess.items:
            if it["img"].resolve() == rp:
                return it
        return None

    def do_GET(self):
        try:
            self._get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._send(500, page(f"<main><div class='hero'><h2>Terjadi galat</h2>"
                                     f"<p>{html.escape(str(e)[:200])}</p></div></main>"))
            except Exception:
                pass

    def do_POST(self):
        try:
            self._post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._json({"ok": False, "error": str(e)[:120]})
            except Exception:
                pass

    def do_PUT(self):
        try:
            self._put()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._json({"ok": False, "error": str(e)[:120]})
            except Exception:
                pass

    def _get(self):
        u = up.urlparse(self.path)
        q = up.parse_qs(u.query)

        if u.path == "/login":
            # Sudah punya sesi sah -> tidak perlu lihat form lagi.
            return self._redirect("/") if self._sess() else self._send(200, login_html())

        sess = self._sess()
        if sess is None:
            return self._redirect("/login")

        if u.path == "/logout":
            c = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            m = c.get("qcsid")
            if m:
                with SESS_LOCK:
                    SESSIONS.pop(m.value, None)
            self.send_response(303)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "qcsid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            self.send_header("Content-Length", "0")
            return self.end_headers()

        if u.path in ("/", "/pilih"):
            if sess.src is None or u.path == "/pilih":
                return self._send(200, landing_html(sess, local=self._local()))
            with sess.lock:
                return self._send(200, index_html(sess, q.get("f", ["all"])[0],
                                                  q.get("c", [None])[0], self._local()))
        if u.path == "/view":
            it = self._item(sess, q.get("path", [""])[0])
            if not it:
                return self._send(404, page("<main>Tidak ditemukan</main>"))
            with sess.lock:
                lst = sess.items
                i = lst.index(it)
                seq = ((i + 1, len(lst)),
                       lst[i - 1] if i > 0 else None,
                       lst[i + 1] if i < len(lst) - 1 else None)
            return self._send(200, view_html(it, seq, self._local()))
        if u.path == "/thumb":
            it = self._item(sess, q.get("path", [""])[0])
            if not it:
                return self._send(404, b"", "text/plain")
            try:
                side = int(q.get("s", ["320"])[0])
            except (TypeError, ValueError):
                side = 320
            side = min(max(side, 32), 2000)
            tp = thumb_path(sess, it, side)
            if not tp:
                return self._send(404, b"", "text/plain")
            return self._send(200, tp.read_bytes(), "image/jpeg")
        self._send(404, page("<main>404</main>"))

    def _post(self):
        u = up.urlparse(self.path)

        if u.path == "/login":
            f = up.parse_qs(self._body().decode("utf-8", "replace"))
            akun = user_slug(f.get("user", [""])[0])
            pw = f.get("pw", [""])[0]
            rec = CFG["users"].get(akun)
            if not rec or not verify_pw(pw, rec.get("hash", "")):
                # Perlambat sedikit supaya menebak password lewat jaringan mahal.
                time.sleep(0.6)
                return self._send(401, login_html("Akun atau password salah.",
                                                  f.get("user", [""])[0]))
            sid = secrets.token_urlsafe(32)
            s = Session(akun)
            with SESS_LOCK:
                SESSIONS[sid] = s
            # Pindai folder awal di luar SESS_LOCK supaya folder besar tidak
            # menahan login orang lain.
            if CFG["default_src"]:
                try:
                    s.load(CFG["default_src"])
                except Exception:
                    pass
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie",
                             f"qcsid={sid}; Path=/; HttpOnly; SameSite=Lax")
            self.send_header("Content-Length", "0")
            return self.end_headers()

        sess = self._sess()
        if sess is None:
            return self._json({"ok": False, "error": "sesi habis — muat ulang lalu masuk lagi"}, 401)

        if u.path == "/setsrc":
            q = up.parse_qs(u.query)
            raw = (q.get("path", [""])[0] or "").strip()
            if not raw:
                return self._json({"ok": False, "error": "path kosong"})
            d = Path(raw).expanduser()
            if not d.is_dir():
                return self._json({"ok": False, "error": "folder tidak ada"})
            with sess.lock:
                n = len(sess.load(d))
            if not n:
                return self._json({"ok": False,
                                   "error": "tidak ada gambar terbaca di folder itu"})
            return self._json({"ok": True, "dir": str(d.resolve()), "n": n})

        if u.path == "/useupload":
            q = up.parse_qs(u.query)
            d = sess.upload_dir(q.get("ds", [""])[0])
            if not d.is_dir():
                return self._json({"ok": False, "error": "folder unggahan belum ada"})
            with sess.lock:
                n = len(sess.load(d))
            if not n:
                return self._json({"ok": False, "error": "tidak ada gambar terbaca"})
            return self._json({"ok": True, "dir": str(d), "n": n})

        if u.path == "/pickdir":
            if not self._local():
                return self._json({"ok": False, "error": "dialog desktop hanya bisa dari mesin server"})
            path, err = native_pick_dir(str(sess.src or Path.home()))
            if not path:
                return self._json({"ok": False, "error": err or "dibatalkan"})
            d = Path(path)
            if not d.is_dir():
                return self._json({"ok": False, "error": "bukan folder"})
            with sess.lock:
                n = len(sess.load(d))
            return self._json({"ok": True, "dir": str(d), "n": n})

        if u.path in ("/markbg", "/unmarkbg"):
            q = up.parse_qs(u.query)
            it = self._item(sess, q.get("path", [""])[0])
            if not it:
                return self._json({"ok": False, "error": "berkas tidak dikenal"}, 404)
            try:
                with sess.lock:
                    if u.path == "/markbg":
                        mark_background(it)
                        msg = "ditandai sebagai latar"
                    else:
                        unmark_background(it)
                        msg = "tanda latar dilepas"
            except Menolak as e:
                return self._json({"ok": False, "error": str(e)})
            try:
                for f in sess.thumbdir.glob(
                        f"{abs(hash(str(it['img'].resolve())))}_*.jpg"):
                    f.unlink(missing_ok=True)
                return self._json({"ok": True, "msg": msg})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:80]})

        if u.path == "/rescan":
            if sess.src is None:
                return self._json({"ok": False, "error": "belum ada dataset terbuka"})
            with sess.lock:
                sess.load(sess.src)
            return self._json({"ok": True})

        if u.path == "/open":
            if not self._local():
                return self._json({"ok": False, "error":
                                   "AnyLabeling hanya bisa dibuka dari mesin server"})
            q = up.parse_qs(u.query)
            it = self._item(sess, q.get("path", [""])[0])
            if not it:
                return self._json({"ok": False, "error": "berkas tidak dikenal"}, 404)
            try:
                launch_anylabeling(sess, it["img"])
                msg = ("folder dibuka — pakai A / D untuk pindah gambar"
                       if CFG["open_mode"] == "dir" else it["img"].name)
                return self._json({"ok": True, "msg": msg})
            except FileNotFoundError:
                return self._json({"ok": False,
                                   "error": f"perintah '{CFG['anylabeling']}' tidak ditemukan"})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:80]})

        self._send(404, b"", "text/plain")

    def _put(self):
        """
        PUT /upload?ds=<nama>&name=<berkas> dengan bodi mentah berisi isi berkas.
        Ditulis mengalir ke disk, jadi berkas besar tidak menumpuk di memori.
        """
        u = up.urlparse(self.path)
        if u.path != "/upload":
            return self._send(404, b"", "text/plain")

        sess = self._sess()
        if sess is None:
            return self._json({"ok": False, "error": "sesi habis"}, 401)

        q = up.parse_qs(u.query)
        ds = safe_slug(q.get("ds", [""])[0])
        fn = safe_filename(q.get("name", [""])[0])
        if not fn:
            return self._json({"ok": False, "error": "nama berkas tidak didukung"})

        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        cap = CFG["max_upload_mb"] * 1024 * 1024
        if n <= 0:
            return self._json({"ok": False, "error": "berkas kosong"})
        if n > cap:
            return self._json({"ok": False,
                               "error": f"lebih dari {CFG['max_upload_mb']} MB"})

        d = sess.upload_dir(ds)
        d.mkdir(parents=True, exist_ok=True)
        dest = d / fn
        tmp = dest.with_suffix(dest.suffix + ".part")
        left = n
        try:
            with open(tmp, "wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(256 * 1024, left))
                    if not chunk:
                        raise ConnectionError("koneksi terputus di tengah unggahan")
                    f.write(chunk)
                    left -= len(chunk)
            tmp.replace(dest)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            return self._json({"ok": False, "error": str(e)[:90]})
        return self._json({"ok": True, "name": fn, "bytes": n})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=None,
                    help="Folder dataset awal untuk setiap akun yang baru masuk. "
                         "Kalau tidak diisi, tiap akun memilih sendiri lewat browser.")
    ap.add_argument("--port", type=int, default=8042,
                    help="default 8042. Hindari 8000/8001 — dipakai backend "
                         "smart-vision-cl, dan 6006 dipakai tensorboard.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--users", type=Path, default=Path(__file__).with_name("users.json"),
                    help="Berkas akun (default: users.json di samping skrip ini).")
    ap.add_argument("--adduser", metavar="NAMA", default=None,
                    help="Buat akun atau ganti passwordnya, lalu keluar. "
                         "Password diminta lewat prompt, tidak lewat argumen.")
    ap.add_argument("--datasets-root", type=Path, default=None,
                    help="Folder induk berisi dataset. Subfolder di dalamnya "
                         "muncul sebagai daftar pilihan di halaman awal.")
    ap.add_argument("--uploads-root", type=Path, default=None,
                    help="Tempat menyimpan unggahan dari browser, dipisah per akun. "
                         "Default: <datasets-root>/_unggahan, atau ~/qcweb-unggahan.")
    ap.add_argument("--max-upload-mb", type=int, default=80,
                    help="Batas ukuran per berkas yang diunggah (default 80 MB).")
    ap.add_argument("--anylabeling", default="anylabeling",
                    help="perintah untuk menjalankan AnyLabeling")
    ap.add_argument("--open-mode", choices=["file", "dir"], default="file",
                    help="file = buka tepat berkas yang diklik (default). "
                         "dir  = buka seluruh folder; tombol A/D aktif, tapi AnyLabeling "
                         "membuka berkas dari sesi sebelumnya, bukan yang diklik.")
    ap.add_argument("--labels", type=Path, default=None,
                    help="Berkas berisi kelas tambahan (1 per baris) yang belum pernah dipakai, "
                         "mis. daftar 20 kelas bentuk. Digabung dengan label yang sudah ada.")
    ap.add_argument("--lock-labels", action="store_true",
                    help="Tolak label di luar daftar (--validatelabel exact). "
                         "Aktifkan setelah taksonomi kelas final.")
    a = ap.parse_args()

    if a.adduser:
        return add_user(a.users, a.adduser)

    if a.src is not None and not a.src.exists():
        raise SystemExit(f"Folder tidak ada: {a.src}")

    CFG["users"] = load_users(a.users)
    CFG["users_path"] = a.users
    if not CFG["users"]:
        raise SystemExit(
            f"\n  Belum ada akun di {a.users}\n"
            f"  Buat dulu:  python3 {Path(__file__).name} --adduser paul\n")

    CFG["anylabeling"] = a.anylabeling
    CFG["open_mode"] = a.open_mode
    CFG["lock_labels"] = a.lock_labels
    CFG["max_upload_mb"] = max(1, a.max_upload_mb)
    CFG["default_src"] = a.src.resolve() if a.src else None
    CFG["datasets_root"] = a.datasets_root.resolve() if a.datasets_root else None
    CFG["uploads_root"] = (a.uploads_root.resolve() if a.uploads_root
                           else (CFG["datasets_root"] / "_unggahan" if CFG["datasets_root"]
                                 else Path.home() / "qcweb-unggahan"))
    CFG["uploads_root"].mkdir(parents=True, exist_ok=True)
    if a.labels and a.labels.exists():
        CFG["extra_labels"] = [l.strip() for l in a.labels.read_text().splitlines() if l.strip()]
    CFG["thumbroot"] = Path(os.environ.get("TMPDIR", "/tmp")) / f"qcweb_{os.getpid()}"
    CFG["thumbroot"].mkdir(parents=True, exist_ok=True)

    print(f"  Akun      : {len(CFG['users'])}  ({', '.join(sorted(CFG['users']))})")
    print(f"  Folder awal: {CFG['default_src'] or 'tiap akun memilih sendiri'}")
    print(f"  Daftar dari: {CFG['datasets_root'] or '(--datasets-root tidak diisi)'}")
    print(f"  Unggahan  : {CFG['uploads_root']}  (maks {CFG['max_upload_mb']} MB/berkas)")
    print(f"  Thumbnail : {CFG['thumbroot']}  (per akun, dihapus saat berhenti)")
    print(f"  Mode buka : {a.open_mode}" +
          ("  (A / D aktif, tapi berkas yang terbuka bisa tertinggal satu langkah)"
           if a.open_mode == "dir" else "  (buka tepat berkas yang diklik)"))
    if a.lock_labels:
        print("  Kunci     : label di luar daftar akan ditolak")
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print("  Catatan   : terbuka ke jaringan — tombol AnyLabeling dan dialog\n"
              "              desktop otomatis dimatikan untuk akses dari luar.")
    try:
        srv = ThreadingHTTPServer((a.host, a.port), H)
    except OSError as e:
        shutil.rmtree(CFG["thumbroot"], ignore_errors=True)
        if e.errno == errno.EADDRINUSE:
            raise SystemExit(
                f"\n  Port {a.port} sudah dipakai proses lain.\n"
                f"  Lihat pemakainya : ss -tlnp | grep :{a.port}\n"
                f"  Atau pakai port lain: --port {a.port + 1}\n")
        if e.errno == errno.EADDRNOTAVAIL:
            raise SystemExit(f"\n  Alamat {a.host} tidak ada di mesin ini.\n")
        raise SystemExit(f"\n  Gagal membuka {a.host}:{a.port} — {e}\n")

    shown = "127.0.0.1" if a.host in ("0.0.0.0", "::") else a.host
    print(f"  Buka      : http://{shown}:{a.port}"
          + ("   (juga dari jaringan: http://<ip-mesin-ini>:%d)" % a.port
             if a.host in ("0.0.0.0", "::") else ""))
    print("  Ctrl+C untuk berhenti.\n")

    # SIGTERM (systemctl stop, pkill) diperlakukan seperti Ctrl+C supaya
    # blok finally sempat menghapus folder thumbnail sementara.
    def on_term(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_term)

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  Berhenti.")
    finally:
        shutil.rmtree(CFG["thumbroot"], ignore_errors=True)


if __name__ == "__main__":
    main()
