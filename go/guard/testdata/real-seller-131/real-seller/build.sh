#!/usr/bin/env bash
# Builds the real seller fixture with capsulectl (README.md names the commit), on a throwaway
# profile under <dir>. Synthetic buyers, no real identity. Nothing is sent anywhere: no
# `deal tick`, no cadence, no remote checker. A local stub rules checker is pinned only to
# record each external-check-input/v0 capsulectl hands it; it allows and names no rule.
set -euo pipefail
CAPSULECTL="$1"; MATERIALITY="$2"; R="$3"
cd "$R"
export HOME="$R/home" XDG_CONFIG_HOME="$R/config" CAPSULECTL_PLUGIN_ROOTS="$R/checker"
mkdir -p "$HOME" "$CAPSULECTL_PLUGIN_ROOTS" out out/check-inputs
unset CAPSULE_DEAL_CHECK_URL
ctl() { "$CAPSULECTL" --profile synthetic-seller "$@"; }
deal() { ctl deal "$@"; }

deal init --dir "$R/store" --materiality "$MATERIALITY" >/dev/null

# The stub checker: keeps its input as the next numbered file, then allows.
cat >"$CAPSULECTL_PLUGIN_ROOTS/record-inputs" <<STUB
#!/bin/sh
n=\$(ls "$R/out/check-inputs" | wc -l | tr -d ' ')
cat >"$R/out/check-inputs/\$n.json"
printf '%s' '{"schema":"external-check-result/v0","ruleset_id":"example-rules/1.0.0","definition_digest":"0000000000000000000000000000000000000000000000000000000000000000","verdict":"allow","findings":[]}'
STUB
chmod 700 "$CAPSULECTL_PLUGIN_ROOTS/record-inputs"
printf '{"command":["%s"]}' "$CAPSULECTL_PLUGIN_ROOTS/record-inputs" >pin.json
ctl profile update --rules-checker pin.json >/dev/null

# check <deal> <input> <name>: a check whose external-check-input/v0 is kept as
# out/check-inputs/<name>.json; answered with the user's own words when it pauses.
check() {
  local before; before=$(ls out/check-inputs | wc -l | tr -d ' ')
  deal check --deal "$1" --input "in/$2" >"out/$3.check.json"
  mv "out/check-inputs/$before.json" "out/check-inputs/$3.json"
  if [ "$(jq -r .verdict "out/$3.check.json")" != pass ] && [ "${4:-}" = answer ]; then
    jq -r .card "out/$3.check.json" >"out/$3.card.txt"
    deal note --deal "$1" --kind approval --check "$(jq -r .check_id "out/$3.check.json")" --choice proceed \
      --said "yes, go ahead" --shown-card "out/$3.card.txt" >/dev/null
  fi
}

# Sale 1: ask 1900, not under 1700. Two buyers.
sale1=$(deal sale new --input in/sale.json | jq -r .sale_id)
a=$(deal open --sale "$sale1" --input in/open-a.json | jq -r .deal_id)
b=$(deal open --sale "$sale1" --input in/open-b.json | jq -r .deal_id)
# Buyer A: told the condition, offered 1900, accepted, committed.
deal note --deal "$a" --kind claim --input in/claim-condition.json >/dev/null
check "$a" check-offer-1900.json a-offer-1900 answer
deal note --deal "$a" --kind act --input in/act-offer-1900.json >/dev/null
deal note --deal "$a" --kind acceptance --check "$(jq -r .check_id out/a-offer-1900.check.json)" \
  --words "Deal, that works for me" --channel app_chat >/dev/null
check "$a" check-commit-1900.json a-commit-1900 answer
deal note --deal "$a" --kind act --input in/act-commit-1900.json >/dev/null
# Buyer B: told the condition, then an offer under the floor, never answered or made.
deal note --deal "$b" --kind claim --input in/claim-condition.json >/dev/null
check "$b" check-offer-1500.json b-offer-1500

# Sale 2: the same item and floor, one buyer, who accepts an offer under the floor.
sale2=$(deal sale new --input in/sale.json | jq -r .sale_id)
c=$(deal open --sale "$sale2" --input in/open-c.json | jq -r .deal_id)
deal note --deal "$c" --kind claim --input in/claim-condition.json >/dev/null
check "$c" check-offer-1600.json c-offer-1600 answer
deal note --deal "$c" --kind act --input in/act-offer-1600.json >/dev/null
deal note --deal "$c" --kind acceptance --check "$(jq -r .check_id out/c-offer-1600.check.json)" \
  --words "Deal, that works for me" --channel app_chat >/dev/null
check "$c" check-commit-1600.json c-commit-1600 answer

# Each thread's own copy, last, so it holds every record.
ctl bundle --deal "$a" --out out/buyer-a.bundle.json >/dev/null
ctl bundle --deal "$b" --out out/buyer-b.bundle.json >/dev/null
ctl bundle --deal "$c" --out out/buyer-c.bundle.json >/dev/null
printf '%s\n%s\n%s\n' "$a" "$b" "$c" >out/deal-ids.txt
