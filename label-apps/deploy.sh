#!/usr/bin/env bash
#
# Naikkan kode yang sudah dicoba di dev (8043) ke produksi (8042).
#
#   ./deploy.sh                 uji dulu, minta konfirmasi, lalu restart prod
#   ./deploy.sh -y              tanpa bertanya
#   ./deploy.sh --lewati-uji    lewati pytest (hanya kalau baru saja dijalankan)
#   ./deploy.sh --status        lihat apa yang sedang jalan, tanpa mengubah apa pun
#
# KENAPA PERLU SKRIP, BUKAN SEKADAR RESTART
# -----------------------------------------
# Prod tidak memuat ulang kode sendiri (--reload sengaja hanya di dev), jadi
# perubahan Python baru hidup setelah proses lamanya diganti. Templat dan CSS
# lain ceritanya: keduanya dibaca dari disk tiap permintaan, jadi perubahan
# tampilan sudah tayang tanpa restart. Skrip ini yang tahu bedanya dan
# mengatakannya, supaya tidak ada restart yang sebenarnya tidak perlu.
#
# Restart memutus SEMUA sesi yang sedang berjalan: orang harus login lagi, dan
# splitting atau ekspor yang sedang jalan mati di tengah jalan. Karena itu ia
# bertanya lebih dulu.
set -euo pipefail
cd "$(dirname "$0")"

UJI=1; TANYA=1; STATUS=0
for a in "$@"; do
  case "$a" in
    --lewati-uji) UJI=0 ;;
    -y|--ya)      TANYA=0 ;;
    --status)     STATUS=1 ;;
    -h|--help)    sed -n '3,9p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "  Argumen tidak dikenal: $a"; exit 2 ;;
  esac
done

merah()  { printf '\033[31m%s\033[0m\n' "$1"; }
hijau()  { printf '\033[32m%s\033[0m\n' "$1"; }
kuning() { printf '\033[33m%s\033[0m\n' "$1"; }
redup()  { printf '\033[2m%s\033[0m\n' "$1"; }

PORT=$(grep -oP '^LABELAPP_PORT=\K.*' env/prod.env)
LOG=logs/prod.log
CAP=run/prod.commit
mkdir -p logs run

pid_prod() { pgrep -f "run\.py .*--port $PORT" || true; }

ringkas() {
  local p; p=$(pid_prod)
  if [[ -n "$p" ]]; then
    echo "  prod   : hidup (pid $p, sejak $(ps -o lstart= -p "$p" | xargs))"
  else
    echo "  prod   : MATI"
  fi
  local d; d=$(pgrep -f "run\.py .*--port 8043" || true)
  [[ -n "$d" ]] && echo "  dev    : hidup (pid $d)" || echo "  dev    : mati"
  echo "  kode   : $(git rev-parse --short HEAD) $(git log -1 --pretty=%s)"
  [[ -f "$CAP" ]] && echo "  ternaik: $(cat "$CAP")" || true
}

if [[ "$STATUS" == 1 ]]; then ringkas; exit 0; fi

# ---------------------------------------------------------------- 1. periksa
echo "1/5  Memeriksa keadaan repo"
if [[ -n "$(git status --porcelain -- . )" ]]; then
  merah "     Ada perubahan yang belum di-commit di label-apps."
  git status --short -- . | sed 's/^/       /'
  echo   "     Commit dulu; prod harus selalu bisa ditunjuk ke satu commit."
  exit 1
fi
CABANG=$(git rev-parse --abbrev-ref HEAD)
[[ "$CABANG" == "main" ]] || kuning "     Cabangnya '$CABANG', bukan main."
KINI=$(git rev-parse --short HEAD)

LAMA=""
[[ -f "$CAP" ]] && LAMA=$(cat "$CAP")
if [[ -n "$LAMA" ]] && git rev-parse --verify --quiet "$LAMA" >/dev/null; then
  N=$(git rev-list --count "$LAMA..HEAD")
  if [[ "$N" == 0 ]]; then
    hijau "     Prod sudah di commit ini ($KINI)."
  else
    echo "     $N commit akan naik:"
    git log --pretty='       %h %s' "$LAMA..HEAD"
    # Kalau yang berubah cuma templat/CSS/uji, restart tidak mengubah apa pun.
    if ! git diff --name-only "$LAMA..HEAD" -- . \
         | grep -qvE '^label-apps/(app/(templates|static)/|tests/|[A-Z]+\.md$)'; then
      kuning "     Semuanya templat/CSS/uji — itu sudah tayang tanpa restart."
    fi
  fi
else
  redup "     Belum ada catatan commit prod; ini pendataan pertama."
fi

# ---------------------------------------------------------------- 2. uji
if [[ "$UJI" == 1 ]]; then
  echo "2/5  Menjalankan pytest"
  if ! .venv/bin/python -m pytest -q 2>&1 | tail -5 | sed 's/^/       /'; then
    merah "     Uji GAGAL — prod tidak disentuh."
    exit 1
  fi
else
  kuning "2/5  pytest dilewati atas permintaan"
fi

# ---------------------------------------------------------------- 3. konfirmasi
echo "3/5  Bersiap mengganti proses prod"
ringkas | sed 's/^/     /'
if [[ "$TANYA" == 1 ]]; then
  kuning "     Restart memutus semua sesi yang sedang berjalan."
  read -rp "     Ketik 'ya' untuk lanjut: " j
  [[ "$j" == "ya" ]] || { echo "     Dibatalkan."; exit 1; }
fi

# ---------------------------------------------------------------- 4. ganti
echo "4/5  Mematikan yang lama, menyalakan yang baru"
P=$(pid_prod)
if [[ -n "$P" ]]; then
  kill -TERM $P 2>/dev/null || true
  for _ in $(seq 1 20); do [[ -z "$(pid_prod)" ]] && break; sleep 0.5; done
  if [[ -n "$(pid_prod)" ]]; then
    kuning "     Belum mau berhenti, dipaksa."
    kill -KILL $(pid_prod) 2>/dev/null || true
    sleep 1
  fi
fi
echo "     --- $(date '+%F %T') deploy $KINI ---" >> "$LOG"
setsid nohup ./start.sh prod >> "$LOG" 2>&1 < /dev/null &
disown || true

# ---------------------------------------------------------------- 5. buktikan
echo "5/5  Menunggu prod menjawab"
for i in $(seq 1 40); do
  KODE=$(curl -s -o /dev/null -m 2 -w '%{http_code}' "http://127.0.0.1:$PORT/login" || true)
  [[ "$KODE" == 200 ]] && break
  sleep 0.5
done
if [[ "${KODE:-}" != 200 ]]; then
  merah "     Prod TIDAK menjawab (HTTP '${KODE:-kosong}'). Sepuluh baris terakhir log:"
  tail -10 "$LOG" | sed 's/^/       /'
  merah "     Kembalikan dengan:  git checkout ${LAMA:-<commit lama>} && ./deploy.sh -y --lewati-uji"
  exit 1
fi
echo "$KINI" > "$CAP"
hijau "     Prod hidup di commit $KINI — http://$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -1):$PORT/"
redup "     Log: $LOG"
