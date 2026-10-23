#!/usr/bin/env bash
# Builds the sale-replay fixture with capsulectl (README.md names the commit), on a throwaway
# profile under <dir>. Synthetic buyers, no real identity. Nothing is sent anywhere: no
# `deal tick`, no cadence, no remote checker. A local stub rules checker is pinned only to
# record each external-check-input/v0 capsulectl hands it; it allows and names no rule.
#
# The steps are those of tests/fixtures/live-history/build.sh, one second apart: a record's
# time is whole seconds and two threads are two logs, so a replay can order records of two
# threads only when their seconds differ. The sale bundle is written last, a second after the
# last check; it seals a sale_cut, so its checkpoint certifies every check.
set -euo pipefail
CAPSULECTL="$1"; MATERIALITY="$2"; R="$3"
cd "$R"
export HOME="$R/home" XDG_CONFIG_HOME="$R/config" CAPSULECTL_PLUGIN_ROOTS="$R/checker"
mkdir -p "$HOME" "$CAPSULECTL_PLUGIN_ROOTS" out out/check-inputs
unset CAPSULE_DEAL_CHECK_URL
ctl() { sleep 1; "$CAPSULECTL" --profile synthetic-seller "$@"; }
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
  if [ "$(jq -r .verdict "out/$3.check.json")" != pass ]; then
    jq -r .card "out/$3.check.json" >"out/$3.card.txt"
    deal note --deal "$1" --kind approval --check "$(jq -r .check_id "out/$3.check.json")" --choice proceed \
      --said "yes, go ahead" --shown-card "out/$3.card.txt" >/dev/null
  fi
}

# One sale: ask 1900, not under 1700. Two buyer threads, each registered on the sale's log.
sale=$(deal sale new --input in/sale.json | jq -r .sale_id)
a=$(deal open --sale "$sale" --input in/open-a.json | jq -r .deal_id)
b=$(deal open --sale "$sale" --input in/open-b.json | jq -r .deal_id)
# Buyer A: told the condition, offered 1900, accepts, and the seller commits and acts.
deal note --deal "$a" --kind claim --input in/claim-condition.json >/dev/null
check "$a" check-offer-1900.json a-offer-1900
deal note --deal "$a" --kind act --input in/act-offer-1900.json >/dev/null
deal note --deal "$a" --kind acceptance --check "$(jq -r .check_id out/a-offer-1900.check.json)" \
  --words "Deal, that works for me" --channel marketplace >/dev/null
check "$a" check-commit-1900.json a-commit-1900
deal note --deal "$a" --kind act --input in/act-commit-1900.json >/dev/null
# Buyer B, after A's commit: told the condition, offered 1850, accepts; the seller's commit is checked.
deal note --deal "$b" --kind claim --input in/claim-condition.json >/dev/null
check "$b" check-offer-1850.json b-offer-1850
deal note --deal "$b" --kind act --input in/act-offer-1850.json >/dev/null
deal note --deal "$b" --kind acceptance --check "$(jq -r .check_id out/b-offer-1850.check.json)" \
  --words "OK at 1850" --channel marketplace >/dev/null
check "$b" check-commit-1850.json b-commit-1850
# Buyer A again: the seller's commit to A is checked a second time, after B's.
check "$a" check-commit-1900.json a-commit-again-1900

# The user's own copy of the sale, with both threads carried in x-deal-sale/v0. capsulectl seals
# each thread's report, then a sale_cut naming each thread's last record, then cuts the sale's
# checkpoint, so the copy's checkpoint is after every check.
ctl deal sale bundle --sale "$sale" --out out/sale.bundle.json >out/sale.bundle.summary.json
printf '%s\n%s\n%s\n' "$sale" "$a" "$b" >out/deal-ids.txt
