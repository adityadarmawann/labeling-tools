"""
Catatan jalannya pekerjaan panjang, ke stdout.

KENAPA TIDAK MENUMPANG ROOT
---------------------------
uvicorn dijalankan dengan `log_level="warning"` supaya derap permintaan HTTP
tidak menenggelamkan layar. Level itu berlaku untuk root logger, jadi pesan
INFO kita sendiri ikut terbungkam — dan pekerjaan yang berjam-jam seperti
splitting tidak meninggalkan jejak apa pun saat ada yang janggal.

Karena itu logger ini memasang salurannya sendiri dan `propagate = False`:
levelnya tidak bergantung pada setelan uvicorn, dan pesannya tidak muncul
dua kali.

Keluarannya ke stdout, jadi ikut tertangkap `./start.sh prod > berkas.log`
maupun `journalctl` kalau nanti dijalankan lewat systemd.
"""
from __future__ import annotations

import logging
import sys

_SIAP: set[str] = set()


def catat(nama: str = "labelapp") -> logging.Logger:
    log = logging.getLogger(nama)
    if nama not in _SIAP:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("  %(asctime)s  %(name)s  %(message)s",
                                         "%H:%M:%S"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
        _SIAP.add(nama)
    return log
