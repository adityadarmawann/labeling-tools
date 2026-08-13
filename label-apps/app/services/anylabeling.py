"""
Menjalankan AnyLabeling dan dialog folder milik sistem.

Keduanya memunculkan jendela di layar mesin tempat server berjalan, bukan di
layar orang yang membuka browser. Karena itu router yang memakai modul ini
hanya melayani permintaan dari localhost — lihat deps.require_local.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

DIALOG_TIMEOUT = 300


def launch(sess, img_path: Path) -> list[str]:
    """
    open_mode 'dir'  -> buka seluruh folder, sehingga tombol A/D (gambar
                        sebelumnya/berikutnya) di AnyLabeling berfungsi.
    open_mode 'file' -> buka satu berkas saja.
    """
    st = sess.settings
    target = str(img_path.parent if st.open_mode == "dir" else img_path)
    cmd = [st.anylabeling, target, "--autosave"]
    if sess.labelfile:
        cmd += ["--labels", str(sess.labelfile)]
        if st.lock_labels:
            cmd += ["--validatelabel", "exact"]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return cmd


def pick_dir(start: str | None = None):
    """
    Munculkan dialog pilih folder milik sistem di mesin server.
    Urutan percobaan: zenity -> kdialog -> tkinter.
    Return: (path | None, pesan_error | None)
    """
    start = start or str(Path.home())

    if shutil.which("zenity"):
        try:
            r = subprocess.run(
                ["zenity", "--file-selection", "--directory",
                 "--title=Pilih folder dataset", f"--filename={start}/"],
                capture_output=True, text=True, timeout=DIALOG_TIMEOUT)
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
                               capture_output=True, text=True, timeout=DIALOG_TIMEOUT)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip(), None
            return None, "dibatalkan"
        except Exception as e:
            return None, str(e)[:80]

    # Tkinter dijalankan sebagai proses terpisah supaya tidak bentrok dengan
    # loop utama server.
    code = (
        "import tkinter as tk;from tkinter import filedialog;"
        "r=tk.Tk();r.withdraw();r.attributes('-topmost',True);"
        f"p=filedialog.askdirectory(title='Pilih folder dataset',initialdir={start!r});"
        "print(p or '')"
    )
    try:
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=DIALOG_TIMEOUT)
        out = r.stdout.strip()
        return (out, None) if out else (None, "dibatalkan")
    except Exception:
        return None, ("tidak ada dialog sistem yang tersedia — pasang zenity, "
                      "atau pakai kotak path di halaman ini")
