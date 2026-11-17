#!/bin/bash
# Menyalakan KEDUA server HIGOLAB di bawah satu unit systemd: produksi dan dev.
#
# Kenapa satu wrapper, bukan dua unit: satu unit berarti satu hal yang harus
# diingat. Sesudah mesin reboot, dua-duanya hidup; tak ada lagi keadaan "prod
# jalan, dev diam" yang dari luar tak bisa dibedakan dari "HIGOLAB mati".
#
# Bentuknya: dev dijalankan di LATAR, produksi di DEPAN, dan skrip ini menunggu
# produksi. Produksi yang dijadikan patokan karena ia yang dipakai orang; kalau
# ia berhenti, unitnya memang harus dianggap berhenti.
#
# `exec` SENGAJA tidak dipakai untuk produksi. exec menggantikan skrip ini,
# dan bersamanya hilang pula jebakan sinyal di bawah -- dev lalu jadi yatim
# dan tetap memegang port 8043 sesudah unitnya dihentikan, sehingga restart
# berikutnya gagal mengikat port yang sama tanpa ada yang menjelaskan kenapa.
#
# Jalur kedua server bisa ditimpa lewat env (dipakai pengujian); bawaannya
# jalur sebenarnya di mesin ini.
set -uo pipefail

PROD_DIR="${HIGOLAB_PROD_DIR:-/home/paul/computer-vision/smartbin/rvm/labeling-tools/label-apps}"
DEV_DIR="${HIGOLAB_DEV_DIR:-/home/paul/computer-vision/smartbin/rvm/labeling-tools-dev/label-apps}"

dev_pid=""
prod_pid=""

# Dipanggil saat systemd mengirim SIGTERM (systemctl stop/restart) maupun saat
# produksi berhenti sendiri. Keduanya diberi kesempatan berhenti baik-baik.
berhenti() {
  for p in "$prod_pid" "$dev_pid"; do
    [ -n "$p" ] && kill -TERM "$p" 2>/dev/null
  done
  # Ditunggu, bukan langsung keluar: keluar lebih dulu membuat systemd
  # menganggap unitnya mati sementara portnya masih dipegang.
  for p in "$prod_pid" "$dev_pid"; do
    [ -n "$p" ] && wait "$p" 2>/dev/null
  done
}
trap 'berhenti; exit 0' TERM INT

# Dev lebih dulu dan di latar. Kegagalan dev TIDAK boleh menjatuhkan produksi:
# dev kerap berisi kode setengah jadi, dan itu bukan alasan prod ikut mati.
if [ -d "$DEV_DIR" ]; then
  # Log dev dipisah ke berkasnya sendiri. Unitnya mengarahkan stdout ke
  # logs/prod.log, dan dua server yang menulis ke satu berkas membuat jejak
  # keduanya saling menyisip -- justru saat sedang dicari sebabnya.
  mkdir -p "$DEV_DIR/logs"
  ( cd "$DEV_DIR" && exec /bin/bash start.sh dev ) >>"$DEV_DIR/logs/dev.log" 2>&1 &
  dev_pid=$!
else
  echo "higolab-mulai: folder dev tak ada ($DEV_DIR), hanya produksi yang dinyalakan" >&2
fi

( cd "$PROD_DIR" && exec /bin/bash start.sh prod ) &
prod_pid=$!

# Menunggu PRODUKSI saja. `wait` bisa disela sinyal, jadi jebakan di atas tetap
# sempat berjalan saat systemd menghentikan unit ini.
wait "$prod_pid"
kode=$?

# Produksi berhenti sendiri: dev ikut dirapikan supaya tak ada proses yatim
# yang memegang 8043.
[ -n "$dev_pid" ] && kill -TERM "$dev_pid" 2>/dev/null
[ -n "$dev_pid" ] && wait "$dev_pid" 2>/dev/null
exit "$kode"
