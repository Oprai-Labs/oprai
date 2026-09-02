#!/usr/bin/env bash
# Guarded, FREQUENT derived-refresh (Faz A v1). The full derived rebuild
# (prices → positions → wallet_metrics → smart_wallets) takes ~26 min and was cron'd
# ONCE nightly, so a wallet that turned smart today wasn't flagged until 04:00 the
# next day. This runs it every 4h instead — but ONLY when the box can afford it, so
# making the smart-money set fresher never risks the live node/index/analysis:
#   • a single-instance lock (never overlap a still-running refresh),
#   • a load ceiling (skip while the box is busy — e.g. node still catching up),
#   • a disk floor (never run the box toward another /data-full incident).
# Cuts smart-set staleness 24h → ~4h with ZERO new correctness risk (reuses the
# fully-tested refresh_derived.sh verbatim). The true ~15-min incremental
# (a separate small `smart_recent` ReplacingMergeTree of current-form wallets,
# UNION'd into the signal engine's smart check) is the documented v2.
set -u
cd /data/rh-index || exit 1

LOCK=/tmp/rh_refresh.lock
LOAD_MAX=${REFRESH_LOAD_MAX:-12}       # 16-core box; skip if 1-min load exceeds this
MIN_FREE_GB=${REFRESH_MIN_FREE_GB:-80} # never run the box toward disk-full again

log() { echo "$(date -u +'%Y-%m-%d %H:%M:%S') [guard] $*"; }

exec 9>"$LOCK"
if ! flock -n 9; then
  log "skip: another refresh holds the lock"
  exit 0
fi

load1=$(awk '{print int($1)}' /proc/loadavg)
if [ "$load1" -gt "$LOAD_MAX" ]; then
  log "skip: load ${load1} > ${LOAD_MAX}"
  exit 0
fi

free_gb=$(df --output=avail -BG /data | tail -1 | tr -dc '0-9')
if [ -n "$free_gb" ] && [ "$free_gb" -lt "$MIN_FREE_GB" ]; then
  log "skip: /data free ${free_gb}G < ${MIN_FREE_GB}G floor"
  exit 0
fi

log "=== guarded refresh START (load=${load1} free=${free_gb}G) ==="
bash refresh_derived.sh
rc=$?
log "=== guarded refresh END (rc=${rc}) ==="
exit $rc
