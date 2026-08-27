#!/usr/bin/env bash
#
# Nyalakan papan periksa anotasi.
#
#   ./start.sh              mode dev (bawaan) — localhost, muat ulang otomatis
#   ./start.sh dev
#   ./start.sh prod         mode produksi — dipakai tim
#
# Setelan tiap mode ada di env/dev.env dan env/prod.env. Argumen tambahan
# diteruskan ke run.py, mis:  ./start.sh prod --open-mode dir
#
# dev BAWAAN dan prod harus diminta, bukan sebaliknya: menyalakan produksi
# adalah tindakan yang perlu disengaja.
#
# Ctrl+C untuk berhenti. Supaya tetap hidup setelah SSH ditutup:
#   tmux new -s label   lalu   ./start.sh prod   lalu Ctrl+B D
#
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

MODE="dev"
PASS=()
while (($#)); do
  case "$1" in
    dev|prod) MODE="$1" ;;
    -h|--help) sed -n '3,16p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) PASS+=("$1") ;;
  esac
  shift
done

BERKAS="env/${MODE}.env"
merah()  { printf '\033[31m%s\033[0m\n' "$1"; }
kuning() { printf '\033[33m%s\033[0m\n' "$1"; }

[[ -f "$BERKAS" ]] || { merah "  $BERKAS tidak ada."; exit 1; }
[[ -x .venv/bin/python ]] || {
  merah "  Virtualenv belum ada."
  echo  "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
}

# Muat setelan mode ini. Nilai yang sudah ada di environment TIDAK ditimpa,
# supaya PORT=9000 ./start.sh dev tetap berlaku.
set -a
while IFS='=' read -r k v; do
  [[ "$k" =~ ^LABELAPP_ ]] || continue
  [[ -n "${!k-}" ]] || printf -v "$k" '%s' "$v"
  export "$k"
done < <(grep -E '^LABELAPP_' "$BERKAS")

# Kredensial yang tidak boleh masuk git dibaca dari berkas terpisah.
# env/prod.env ikut ter-commit; menaruh client secret di situ berarti
# mengunggahnya ke repositori.
if [[ -f env/rahasia.env ]]; then
  while IFS='=' read -r k v; do
    [[ "$k" =~ ^LABELAPP_ ]] || continue
    [[ -n "${!k-}" ]] || printf -v "$k" '%s' "$v"
    export "$k"
  done < <(grep -E '^LABELAPP_' env/rahasia.env)
fi
set +a

PORT="${LABELAPP_PORT:-8042}"
HOST="${LABELAPP_HOST:-127.0.0.1}"
USERS="${LABELAPP_USERS_FILE:-users.json}"

if [[ ! -s "$USERS" ]]; then
  merah "  Belum ada akun di $USERS (mode $MODE)."
  echo  "  Buat dulu:"
  echo  "    LABELAPP_USERS_FILE=$USERS .venv/bin/python run.py --users $USERS --adduser <nama>"
  exit 1
fi

if [[ ! -d "${LABELAPP_DATASETS_ROOT:-}" ]]; then
  kuning "  Folder dataset tidak ada: ${LABELAPP_DATASETS_ROOT:-(kosong)}"
  if [[ "$MODE" == "dev" && -n "${LABELAPP_DATASETS_ROOT:-}" ]]; then
    mkdir -p "$LABELAPP_DATASETS_ROOT"
    kuning "  dibuatkan untuk dev."
  fi
fi

AKUN=$(.venv/bin/python run.py --users "$USERS" --list-users 2>/dev/null \
       | tail -n +2 | awk '{print $1}' | paste -sd' ')
IP=$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)

printf '\n  \033[1mmode %s\033[0m  ·  %s\n' "$MODE" "$BERKAS"
echo "  Akun    : ${AKUN:-(tidak terbaca)}   <- dari $USERS"
echo "  Dataset : ${LABELAPP_DATASETS_ROOT:-(tidak diisi)}"
if [[ "$HOST" == "0.0.0.0" ]]; then
  echo "  Alamat  : http://${IP:-<ip-mesin-ini>}:$PORT"
  echo "  Firewall: pastikan $PORT diizinkan  ->  sudo ufw status | grep $PORT"
else
  echo "  Alamat  : http://127.0.0.1:$PORT   (hanya mesin ini)"
fi

ARGS=(--host "$HOST" --port "$PORT" --users "$USERS")
# Bentuk `if`, bukan `[[ ... ]] && ...`: dengan set -e, baris && yang kondisinya
# salah membuat skrip berhenti tanpa pesan — dan itu justru terjadi pada mode
# prod, yang paling tidak boleh gagal diam-diam.
if [[ -n "${LABELAPP_DATASETS_ROOT:-}" ]]; then
  ARGS+=(--datasets-root "$LABELAPP_DATASETS_ROOT")
fi
if [[ -n "${LABELAPP_UPLOADS_ROOT:-}" ]]; then
  ARGS+=(--uploads-root "$LABELAPP_UPLOADS_ROOT")
fi
# Muat ulang otomatis hanya di dev: di produksi restart mendadak berarti semua
# orang kehilangan sesinya di tengah pekerjaan.
if [[ "$MODE" == "dev" ]]; then
  ARGS+=(--reload)
fi

exec .venv/bin/python run.py "${ARGS[@]}" ${PASS[@]+"${PASS[@]}"}
