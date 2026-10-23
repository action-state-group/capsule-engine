#!/usr/bin/env bash
# Builds the release-on-acceptance fixture with capsulectl (README.md names the commit), on a
# throwaway profile under <dir>. Synthetic buyers, no real identity. Nothing is sent anywhere: no
# `deal tick`, no cadence, no remote checker. A local stub rules checker is pinned only to record
# each external-check-input/v0 capsulectl hands it; it allows and names no rule.
#
# One sale, two buyer threads opened by a marketplace profile id. Buyer A is told the condition,
# offered 1900, accepts, and the seller commits and acts; then the seller checks a share of the
# phone and of the pickup area with A, and the same two with B. Steps are one second apart, as in
# ../sale-replay/build.sh. The sale bundle is written last.
set -euo pipefail
CAPSULECTL="$1"; DEAL_PLUGIN="$2"; MATERIALITY="$3"; R="$4"
cd "$R"
export HOME="$R/home" XDG_CONFIG_HOME="$R/config" CAPSULECTL_PLUGIN_ROOTS="$R/checker"
mkdir -p "$HOME" "$CAPSULECTL_PLUGIN_ROOTS" out out/check-inputs
chmod 0755 "$R" "$HOME" "$CAPSULECTL_PLUGIN_ROOTS"
install -m 0755 "$DEAL_PLUGIN" "$CAPSULECTL_PLUGIN_ROOTS/capsulectl-deal"
unset CAPSULE_DEAL_CHECK_URL
ctl() { sleep 1; "$CAPSULECTL" --profile synthetic-seller "$@"; }
deal() { ctl deal "$@"; }

deal init --dir "$R/store" --materiality "$MATERIALITY" >/dev/null

cat >"$CAPSULECTL_PLUGIN_ROOTS/record-inputs" <<STUB
#!/bin/sh
n=\$(ls "$R/out/check-inputs" | wc -l | tr -d ' ')
cat >"$R/out/check-inputs/\$n.json"
printf '%s' '{"schema":"external-check-result/v0","ruleset_id":"example-rules/1.0.0","definition_digest":"0000000000000000000000000000000000000000000000000000000000000000","verdict":"allow","findings":[]}'
STUB
chmod 700 "$CAPSULECTL_PLUGIN_ROOTS/record-inputs"
printf '{"command":["%s"]}' "$CAPSULECTL_PLUGIN_ROOTS/record-inputs" >pin.json
ctl profile update --rules-checker pin.json >/dev/null

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

sale=$(deal sale new --input in/sale.json | jq -r .sale_id)
a=$(deal open --sale "$sale" --input in/open-a.json | jq -r .deal_id)
b=$(deal open --sale "$sale" --input in/open-b.json | jq -r .deal_id)
deal note --deal "$a" --kind claim --input in/claim-condition.json >/dev/null
check "$a" check-offer-1900.json a-offer-1900
deal note --deal "$a" --kind act --input in/act-offer-1900.json >/dev/null
deal note --deal "$a" --kind acceptance --check "$(jq -r .check_id out/a-offer-1900.check.json)" \
  --words "Deal, that works for me" --channel marketplace >/dev/null
check "$a" check-commit-1900.json a-commit-1900
deal note --deal "$a" --kind act --input in/act-commit-1900.json >/dev/null
check "$a" share-phone.json a-share-phone
check "$a" share-pickup-area.json a-share-pickup-area
check "$b" share-phone.json b-share-phone
check "$b" share-pickup-area.json b-share-pickup-area

ctl deal sale bundle --sale "$sale" --out out/sale.bundle.json >out/sale.bundle.summary.json
printf '%s\n%s\n%s\n' "$sale" "$a" "$b" >out/deal-ids.txt
