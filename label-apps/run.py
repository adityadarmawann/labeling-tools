#!/usr/bin/env python3
"""
Entrypoint aplikasi.

Argumen CLI diterjemahkan menjadi environment, lalu uvicorn dijalankan. Dengan
begitu tiga cara menjalankan aplikasi memakai konfigurasi yang sama:

  python run.py --host 0.0.0.0 --datasets-root ~/datasets
  LABELAPP_DATASETS_ROOT=~/datasets uvicorn app.main:app
  systemd unit dengan Environment=LABELAPP_...

Contoh:
  python run.py --adduser paul                 # sekali per anggota tim
  python run.py                                # pakai sendiri, localhost
  python run.py --host 0.0.0.0 --datasets-root ~/computer-vision/datasets
"""
from __future__ import annotations

import argparse
import errno
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

DEFAULT_PORT = 8042


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Papan periksa anotasi untuk dataset AnyLabeling / labelme / YOLO-seg.",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    g = ap.add_argument_group("jaringan")
    g.add_argument("--host", default="127.0.0.1",
                   help="127.0.0.1 (default) = hanya mesin ini. "
                        "0.0.0.0 = bisa diakses tim lewat jaringan.")
    g.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"default {DEFAULT_PORT}. Hindari 8000/8001 (backend "
                        "smart-vision-cl) dan 6006 (tensorboard).")
    g.add_argument("--reload", action="store_true",
                   help="muat ulang otomatis saat kode berubah (untuk ngoding).")

    g = ap.add_argument_group("akun")
    g.add_argument("--users", type=Path, default=ROOT / "users.json",
                   help="berkas akun (default: users.json di folder ini).")
    g.add_argument("--adduser", metavar="NAMA",
                   help="buat akun atau ganti passwordnya, lalu keluar. "
                        "Password diminta lewat prompt, bukan lewat argumen.")
    g.add_argument("--deluser", metavar="NAMA", help="hapus akun, lalu keluar.")
    g.add_argument("--list-users", action="store_true", help="tampilkan daftar akun, lalu keluar.")

    g = ap.add_argument_group("dataset")
    g.add_argument("--datasets-root", type=Path,
                   help="folder induk berisi dataset; subfoldernya muncul "
                        "sebagai daftar pilihan di halaman awal.")
    g.add_argument("--uploads-root", type=Path,
                   help="tempat menyimpan unggahan, dipisah per akun. "
                        "Default: <datasets-root>/_unggahan atau ~/labelapp-unggahan.")
    g.add_argument("--src", type=Path,
                   help="folder yang langsung dibuka setiap akun saat masuk.")
    g.add_argument("--max-upload-mb", type=int, default=80,
                   help="batas ukuran per berkas yang diunggah (default 80).")

    g = ap.add_argument_group("AnyLabeling")
    g.add_argument("--anylabeling", default="anylabeling",
                   help="perintah untuk menjalankan AnyLabeling.")
    g.add_argument("--open-mode", choices=["file", "dir"], default="file",
                   help="file = buka tepat berkas yang diklik (default). "
                        "dir = buka seluruh folder; tombol A/D aktif, tapi "
                        "AnyLabeling membuka berkas dari sesi sebelumnya.")
    g.add_argument("--labels", type=Path,
                   help="berkas berisi kelas tambahan (satu per baris) yang "
                        "belum pernah dipakai di dataset.")
    g.add_argument("--flags", type=Path,
                   help="berkas berisi flag tingkat gambar (satu per baris) "
                        "yang selalu ditawarkan di setiap gambar, padanan "
                        "`flags:` di anylabeling_config.yaml. Tanpa ini, nama "
                        "flag harus diketik ulang persis di tiap gambar.")
    g.add_argument("--lock-labels", action="store_true",
                   help="tolak label di luar daftar. Aktifkan setelah "
                        "taksonomi kelas final.")
    return ap


def to_environ(a: argparse.Namespace) -> None:
    """Argumen CLI -> environment yang dibaca app.config."""
    env = {
        "USERS_FILE": a.users,
        "DATASETS_ROOT": a.datasets_root,
        "UPLOADS_ROOT": a.uploads_root,
        "DEFAULT_SRC": a.src,
        "LABELS_FILE": a.labels,
        "FLAGS_FILE": a.flags,
        "MAX_UPLOAD_MB": a.max_upload_mb,
        "ANYLABELING": a.anylabeling,
        "OPEN_MODE": a.open_mode,
        "LOCK_LABELS": "1" if a.lock_labels else "",
    }
    for k, v in env.items():
        if v not in (None, ""):
            os.environ["LABELAPP_" + k] = str(v)


def main() -> None:
    # Tanpa ini, stdout yang diarahkan ke berkas (systemd, nohup) ter-buffer
    # dan banner baru muncul saat proses berhenti — menyesatkan saat mendiagnosa.
    sys.stdout.reconfigure(line_buffering=True)

    a = build_parser().parse_args()

    from app.security import add_user, load_users, remove_user

    if a.adduser:
        return add_user(a.users, a.adduser)
    if a.deluser:
        return remove_user(a.users, a.deluser)
    if a.list_users:
        users = load_users(a.users)
        if not users:
            return print(f"  Belum ada akun di {a.users}")
        print(f"  {len(users)} akun di {a.users}:")
        for k, v in sorted(users.items()):
            print(f"    {k:20s} {v.get('nama', '')}")
        return

    if a.src and not a.src.is_dir():
        raise SystemExit(f"\n  Folder tidak ada: {a.src}\n")
    if a.datasets_root and not a.datasets_root.is_dir():
        raise SystemExit(f"\n  Folder tidak ada: {a.datasets_root}\n")

    # Menolak jalan tanpa akun: tanpa ini, menjalankan --host 0.0.0.0 berarti
    # membuka seluruh papan periksa ke jaringan tanpa login sama sekali.
    if not load_users(a.users):
        raise SystemExit(
            f"\n  Belum ada akun di {a.users}\n"
            f"  Buat dulu:  python {Path(__file__).name} --adduser paul\n")

    to_environ(a)

    # Bind dicoba lebih dulu supaya port yang terpakai memberi pesan jelas,
    # bukan traceback uvicorn di tengah log.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((a.host, a.port))
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            raise SystemExit(
                f"\n  Port {a.port} sudah dipakai proses lain.\n"
                f"  Lihat pemakainya : ss -tlnp | grep :{a.port}\n"
                f"  Atau pakai port lain: --port {a.port + 1}\n") from e
        if e.errno == errno.EADDRNOTAVAIL:
            raise SystemExit(f"\n  Alamat {a.host} tidak ada di mesin ini.\n") from e
        raise SystemExit(f"\n  Gagal membuka {a.host}:{a.port} — {e}\n") from e
    finally:
        probe.close()

    terbuka = a.host not in ("127.0.0.1", "localhost", "::1")
    tampil = "127.0.0.1" if a.host in ("0.0.0.0", "::") else a.host
    print(f"\n  Labeling Tools")
    print(f"  Buka      : http://{tampil}:{a.port}"
          + (f"   (dari jaringan: http://<ip-mesin-ini>:{a.port})" if terbuka else ""))
    if terbuka:
        print("  Catatan   : terbuka ke jaringan. Tombol AnyLabeling dan dialog\n"
              "              desktop otomatis mati untuk akses dari luar, karena\n"
              "              jendelanya muncul di layar server. Tidak ada TLS —\n"
              "              pakai reverse proxy HTTPS atau batasi lewat firewall.")

    import uvicorn
    uvicorn.run("app.main:app", host=a.host, port=a.port, reload=a.reload,
                log_level="warning")


if __name__ == "__main__":
    main()
