#!/usr/bin/env bash
#
# Nyalakan papan periksa anotasi.
#
#   ./start.sh                    # untuk tim, di semua alamat (0.0.0.0)
#   ./start.sh --lokal            # hanya mesin ini, tidak terbuka ke jaringan
#   PORT=8043 ./start.sh          # ganti port
#   DATASETS=~/lain ./start.sh    # ganti folder induk dataset
#   USERS=/path/users.json ./start.sh
#
# Argumen lain diteruskan ke run.py, mis:  ./start.sh --open-mode dir
#
# Ctrl+C untuk berhenti. Kalau dijalankan lewat SSH dan ingin tetap hidup
# setelah SSH ditutup, jalankan di dalam tmux:
#   tmux new -s label      lalu ./start.sh      lepas dengan Ctrl+B lalu D
#   tmux attach -t label   untuk kembali
#
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PORT="${PORT:-8042}"
DATASETS="${DATASETS:-$HOME/computer-vision/datasets}"
USERS="${USERS:-users.json}"
HOST="0.0.0.0"

# Pisahkan --lokal (milik skrip ini) dari argumen yang diteruskan ke run.py.
PASS=()
while (($#)); do
  case "$1" in
    --lokal|--local) HOST="127.0.0.1" ;;
    -h|--help) sed -n '3,18p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) PASS+=("$1") ;;
  esac
  shift
done

merah()  { printf '\033[31m%s\033[0m\n' "$1"; }
kuning() { printf '\033[33m%s\033[0m\n' "$1"; }

# -- prasyarat --------------------------------------------------------------

if [[ ! -x .venv/bin/python ]]; then
  merah "  Virtualenv belum ada."
  echo  "  Buat dulu:"
  echo  "    python3 -m venv .venv"
  echo  "    .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [[ ! -s "$USERS" ]]; then
  merah "  Belum ada akun di $USERS — server tidak akan mau jalan tanpa ini."
  echo  "  Buat akun untuk tiap anggota tim:"
  echo  "    .venv/bin/python run.py --adduser <nama>"
  exit 1
fi

if [[ ! -d "$DATASETS" ]]; then
  kuning "  Folder dataset tidak ada: $DATASETS"
  kuning "  Daftar dataset akan kosong. Timpa dengan:  DATASETS=~/folder ./start.sh"
  DATASETS=""
fi

# -- info --------------------------------------------------------------------

AKUN=$(.venv/bin/python run.py --users "$USERS" --list-users 2>/dev/null \
       | tail -n +2 | awk '{print $1}' | paste -sd' ')
IP=$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)

echo
echo "  Akun      : ${AKUN:-(tidak terbaca)}"
if [[ "$HOST" == "0.0.0.0" ]]; then
  echo "  Untuk tim : http://${IP:-<ip-mesin-ini>}:$PORT"
  # Tidak bisa memeriksa ufw tanpa sudo, jadi ini pengingat — bukan hasil cek.
  echo "  Firewall  : pastikan port $PORT diizinkan  ->  sudo ufw status | grep $PORT"
else
  echo "  Mode      : lokal, hanya bisa dibuka dari mesin ini"
fi

# -- jalan -------------------------------------------------------------------

ARGS=(--host "$HOST" --port "$PORT" --users "$USERS")
[[ -n "$DATASETS" ]] && ARGS+=(--datasets-root "$DATASETS")

exec .venv/bin/python run.py "${ARGS[@]}" ${PASS[@]+"${PASS[@]}"}
