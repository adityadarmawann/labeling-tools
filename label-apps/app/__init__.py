"""
Labeling Tools — papan periksa anotasi berbasis web.

Tata letak:
  config.py      setelan dari environment
  security.py    password, nama aman
  session.py     keadaan per akun (dataset, thumbnail)
  deps.py        dependency: sesi, penjagaan localhost
  templating.py  instance Jinja2
  main.py        perakitan aplikasi
  routers/       satu berkas per kelompok URL
  services/      logika inti, tanpa HTTP
"""

__version__ = "0.1.0"
