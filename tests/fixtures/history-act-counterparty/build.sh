#!/usr/bin/env bash
# Builds the history-act-counterparty fixture with capsulectl (README.md names the commit), on two
# throwaway profiles under <dir>: one deal record set each, x-deal-v0 and typed. Synthetic hotels,
# no real identity. Nothing is sent anywhere: no `deal tick`, no cadence, no remote checker. A local
# stub rules checker is pinned only to keep each external-check-input/v0 capsulectl hands it; it
# allows and names no rule.
set -euo pipefail
CAPSULECTL="$1"; MATERIALITY="$2"; R="$3"
cd "$R"
export HOME="$R/home" XDG_CONFIG_HOME="$R/config" CAPSULECTL_PLUGIN_ROOTS="$R/checker"
mkdir -p "$HOME" "$CAPSULECTL_PLUGIN_ROOTS" out/inputs
unset CAPSULE_DEAL_CHECK_URL

# The stub checker: keeps its input as out/inputs/last.json, then allows.
cat >"$CAPSULECTL_PLUGIN_ROOTS/record-inputs" <<STUB
#!/bin/sh
cat >"$R/out/inputs/last.json"
printf '%s' '{"schema":"external-check-result/v0","ruleset_id":"example-rules/1.0.0","definition_digest":"0000000000000000000000000000000000000000000000000000000000000000","verdict":"allow","findings":[]}'
STUB
chmod 700 "$CAPSULECTL_PLUGIN_ROOTS/record-inputs"
printf '{"command":["%s"]}' "$CAPSULECTL_PLUGIN_ROOTS/record-inputs" >pin.json

for set in x-deal-v0 typed; do
  ctl() { "$CAPSULECTL" --profile "synthetic-$set" "$@"; }
  deal() { ctl deal "$@"; }
  records=(); [ "$set" = typed ] && records=(--records typed)
  deal init --dir "$R/store-$set" --materiality "$MATERIALITY" >/dev/null
  ctl profile update --rules-checker pin.json >/dev/null
  mkdir -p "out/$set"

  # check <deal> <name>: checks the commit; its external-check-input/v0 is kept as
  # out/<set>/<name>.input.json, answered in the user's own words when it pauses.
  check() {
    deal check --deal "$1" --input in/check-commit.json >"out/$set/$2.check.json"
    mv out/inputs/last.json "out/$set/$2.input.json"
    if [ "$(jq -r .verdict "out/$set/$2.check.json")" != pass ]; then
      jq -r .card "out/$set/$2.check.json" >"out/$set/$2.card.txt"
      deal note --deal "$1" --kind approval --check "$(jq -r .check_id "out/$set/$2.check.json")" --choice proceed \
        --said "yes, book it" --shown-card "out/$set/$2.card.txt" >/dev/null
    fi
  }

  # Deal 1: a room at Example Hotel Shinjuku, checked and booked; then the same booking checked again.
  d1=$(deal open ${records[@]+"${records[@]}"} --input in/open-a.json | jq -r .deal_id)
  check "$d1" booking
  deal note --deal "$d1" --kind act --input in/act-commit.json >/dev/null
  check "$d1" repeat-in-one-deal
  # Deal 2: the same room, same amount, at another hotel, checked.
  d2=$(deal open ${records[@]+"${records[@]}"} --input in/open-b.json | jq -r .deal_id)
  check "$d2" other-merchant

  # Each deal's own copy, last, so it holds every record.
  ctl bundle --deal "$d1" --out "out/$set/deal-1.bundle.json" >/dev/null
  ctl bundle --deal "$d2" --out "out/$set/deal-2.bundle.json" >/dev/null
  printf '%s\n%s\n' "$d1" "$d2" >"out/$set/deal-ids.txt"
done
