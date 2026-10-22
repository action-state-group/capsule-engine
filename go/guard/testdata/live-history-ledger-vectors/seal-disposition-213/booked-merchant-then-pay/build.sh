#!/usr/bin/env bash
# Deal 1 books Example Hotel Shinjuku (commit, checked, act). Deal 2, a purchase at the same hotel:
# a pay check. Synthetic, local stub checker, nothing sent.
set -euo pipefail
CAPSULECTL="$1"; MATERIALITY="$2"; R="$3"; FX="$4"
rm -rf "$R"; mkdir -p "$R/in"; cp "$FX"/inputs/* "$R/in/"; cd "$R"
export HOME="$R/home" XDG_CONFIG_HOME="$R/config" CAPSULECTL_PLUGIN_ROOTS="$R/checker"
mkdir -p "$HOME" "$CAPSULECTL_PLUGIN_ROOTS" out/inputs
unset CAPSULE_DEAL_CHECK_URL
cat >"$CAPSULECTL_PLUGIN_ROOTS/record-inputs" <<STUB
#!/bin/sh
cat >"$R/out/inputs/last.json"
printf '%s' '{"schema":"external-check-result/v0","ruleset_id":"example-rules/1.0.0","definition_digest":"0000000000000000000000000000000000000000000000000000000000000000","verdict":"allow","findings":[]}'
STUB
chmod 700 "$CAPSULECTL_PLUGIN_ROOTS/record-inputs"
printf '{"command":["%s"]}' "$CAPSULECTL_PLUGIN_ROOTS/record-inputs" >pin.json
cat > in/open-pay.json <<'J'
{"type":"purchase","channel":"web",
 "intent":{"verbatim":"pay Example Hotel Shinjuku for the minibar","max_total_minor":50000,"allowed":["pay"]},
 "who":{"name":"Example Hotel Shinjuku","domain":"hotel.example"},
 "terms":{"item":"minibar","price_minor":4000,"currency":"USD"},
 "recourse":{"rail":"card","refundable":false}}
J
printf '%s' '{"action":"pay","amount_minor":4000,"authorized_max_minor":4000,"terms":{"price_minor":4000}}' > in/check-pay.json
for set in x-deal-v0 typed; do
  ctl() { "$CAPSULECTL" --profile "synthetic-$set" "$@"; }
  deal() { ctl deal "$@"; }
  records=(); [ "$set" = typed ] && records=(--records typed)
  deal init --dir "$R/store-$set" --materiality "$MATERIALITY" >/dev/null
  ctl profile update --rules-checker pin.json >/dev/null
  mkdir -p "out/$set"
  check() {
    deal check --deal "$1" --input "in/$3" >"out/$set/$2.check.json"
    mv out/inputs/last.json "out/$set/$2.input.json"
    if [ "$(jq -r .verdict "out/$set/$2.check.json")" != pass ]; then
      jq -r .card "out/$set/$2.check.json" >"out/$set/$2.card.txt"
      deal note --deal "$1" --kind approval --check "$(jq -r .check_id "out/$set/$2.check.json")" --choice proceed \
        --said "yes, go ahead" --shown-card "out/$set/$2.card.txt" >/dev/null
    fi
  }
  d1=$(deal open ${records[@]+"${records[@]}"} --input in/open-a.json | jq -r .deal_id)
  check "$d1" booking check-commit.json
  deal note --deal "$d1" --kind act --input in/act-commit.json >/dev/null
  d2=$(deal open ${records[@]+"${records[@]}"} --input in/open-pay.json | jq -r .deal_id)
  check "$d2" pay-booked-merchant check-pay.json
done
