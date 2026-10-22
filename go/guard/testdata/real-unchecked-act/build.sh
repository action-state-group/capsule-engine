#!/usr/bin/env bash
# Builds the real unchecked-act fixture with capsulectl (README.md names the commit), on a throwaway
# profile under <dir>. Synthetic merchant, no real identity. Nothing is sent anywhere: no `deal tick`,
# no cadence, no remote checker. The pinned rules checker is a local capture script: it saves each
# external-check-input/v0 it is given and answers not_evaluable, so every check pauses.
#   build.sh <capsulectl> <capsule-cli>/skills/deal/profile/materiality-predicate/neutral.json <dir>
# <dir> must hold inputs/ (copied to <dir>/in/).
set -euo pipefail
CAPSULECTL="$1"; MATERIALITY="$2"; R="$3"
cd "$R"
export HOME="$R/home" XDG_CONFIG_HOME="$R/config" CAPSULECTL_PLUGIN_ROOTS="$R/plugins"
mkdir -p "$HOME" out plugins captured
unset CAPSULE_DEAL_CHECK_URL
cat >plugins/capsulectl-capture-checker <<SH
#!/bin/bash
f=\$(mktemp "$R/captured/input-XXXXXX")
cat >"\$f"
echo '{"schema":"external-check-result/v0","ruleset_id":"synthetic-capture/1","definition_digest":"0000000000000000000000000000000000000000000000000000000000000000","verdict":"not_evaluable","findings":[{"id":"r05","verdict":"not_evaluable","reason":"synthetic"}]}'
SH
chmod +x plugins/capsulectl-capture-checker
printf '{"command": ["%s"]}\n' "$R/plugins/capsulectl-capture-checker" >in/pin.json
deal() { "$CAPSULECTL" --profile synthetic-unchecked deal "$@"; }
deal init --dir "$R/store" --materiality "$MATERIALITY" >/dev/null
"$CAPSULECTL" --profile synthetic-unchecked profile update --rules-checker in/pin.json >/dev/null

# keep is the envelope the checker was given at the last check: the newest non-empty capture.
keep() { f=$(ls -t captured/input-* | while read -r x; do [ -s "$x" ] && echo "$x" && break; done); cp "$f" "out/$1"; rm -f captured/input-*; }

# One deal per record set: the check pauses (not_evaluable), the agent pays anyway (sealed unchecked,
# no_sealed_approval), then the same purchase is checked again.
for set in x-deal-v0 typed; do
  d=$(deal open --records "$set" --input in/open.json | jq -r .deal_id)
  deal check --deal "$d" --input in/check-pay.json >"out/$set-check-1.json"; keep "$set-check-1.input.json"
  deal note --deal "$d" --kind act --input in/act-pay.json >"out/$set-act.json"
  deal check --deal "$d" --input in/check-pay.json >"out/$set-check-2.json"; keep "$set-check-2.input.json"
  "$CAPSULECTL" --profile synthetic-unchecked bundle --deal "$d" --out "out/$set.bundle.json" >/dev/null
done
