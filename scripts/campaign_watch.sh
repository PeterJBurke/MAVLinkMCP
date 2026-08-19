#!/bin/bash
# Watch the running N=5 campaign and push a phone notification when something
# needs a human - not on every trial, only on things worth acting on.
#
#   campaign_watch.sh <campaign-log-path>
#
# Fires on:
#   * each model whose key is refused / abandoned (exit 3)  -> high, per model
#   * each model stopped at a budget ceiling                -> high, per model
#   * the campaign finishing                                -> default, w/ tally
#   * the campaign dying with no completion line            -> high
#
# Dedup is PER MODEL, not once-ever. The old version used a single boolean, so
# the first refusal (anthropic) silenced the alert and a later refusal
# (gemini-3.1-pro, HTTP 429) fired nothing. Now every distinct (type,model)
# event alerts exactly once. On startup we seed the set with whatever is
# ALREADY in the log, so restarting the watcher re-spams nothing.
#
# Still deliberately silent on normal trials. A notifier that cries every trial
# gets muted, and a muted notifier is worse than none.
set -u
LOG="${1:?usage: campaign_watch.sh <log>}"
NOTIFY=/root/droneserver/scripts/notify.sh
declare -A ALERTED

# Emit "type<TAB>model" for every refusal/budget event currently in the log,
# deduped. Walks the log tracking the current model from the section headers.
events() {
  python3 - "$LOG" <<'PY'
import sys, re
cur = "unknown"; out = []; seen = set()
for line in open(sys.argv[1], errors="ignore"):
    h = re.match(r"#+\s+(\S+)\s+\(20.*cap", line)      # section header
    if h: cur = h.group(1); continue
    e = re.match(r"#+ end (\S+) \(exit 3", line)        # key refused / abandoned
    if e:
        k = ("refused", e.group(1))
        if k not in seen: seen.add(k); out.append(k)
        continue
    if re.search(r"BUDGET stop|BudgetExceeded", line):  # spend ceiling
        k = ("budget", cur)
        if k not in seen: seen.add(k); out.append(k)
for t, m in out:
    print(f"{t}\t{m}")
PY
}

alert() {  # type  model
  case "$1" in
    refused) bash "$NOTIFY" "API key refused: $2" \
      "$2 was refused/abandoned (out of credit, or rate-limited). The campaign continues with other models; this arm needs attention to finish." \
      "high" "money_with_wings" ;;
    budget)  bash "$NOTIFY" "Budget ceiling: $2" \
      "$2 stopped at its spend cap. It stopped cleanly and can resume; the cap needs raising to finish that arm." \
      "high" "chart_with_upwards_trend" ;;
  esac
}

# Seed: everything already in the log is treated as known, so a restart is quiet
# about events that already happened. A one-line heads-up records the seed.
SEED=""
while IFS=$'\t' read -r t m; do
  [ -z "$t" ] && continue
  ALERTED["$t:$m"]=1
  SEED="$SEED $t:$m"
done < <(events)
bash "$NOTIFY" "Watcher armed (per-model alerts)" \
  "Now alerting on every future refusal/budget stop, not just the first.${SEED:+ Already known:$SEED}" \
  "low" "eyes"

while true; do
  if ! pgrep -f 'run_n5_campaign(_px4)?\.sh|run_local_arm\.sh' >/dev/null 2>&1; then
    sleep 20   # let the final writes land
    if grep -qE "campaign finished|arm finished" "$LOG" 2>/dev/null; then
      done_n=$(grep -c "^############ end" "$LOG" 2>/dev/null || echo 0)
      pass=$(grep -c "PASS T" "$LOG" 2>/dev/null || echo 0)
      fail=$(grep -c "FAIL T" "$LOG" 2>/dev/null || echo 0)
      void=$(grep -c "VOID" "$LOG" 2>/dev/null || echo 0)
      bash "$NOTIFY" "Campaign FINISHED" \
        "$done_n models done. PASS $pass / FAIL $fail / VOID $void. Ready for analysis." \
        "default" "checkered_flag"
    else
      bash "$NOTIFY" "Campaign STOPPED unexpectedly" \
        "The run is no longer active and the log has no completion line. Needs a look." \
        "high" "rotating_light"
    fi
    exit 0
  fi

  # Alert once per newly-seen (type,model) event.
  while IFS=$'\t' read -r t m; do
    [ -z "$t" ] && continue
    key="$t:$m"
    if [ -z "${ALERTED[$key]:-}" ]; then
      ALERTED["$key"]=1
      alert "$t" "$m"
    fi
  done < <(events)

  sleep 120
done
