#!/usr/bin/env bash
# Builds the real two-deal bundle with capsulectl v0.1.0-rc13, on a throwaway
# profile under this directory. Synthetic merchant, no real identity. Nothing
# is sent anywhere: no `deal tick`, no cadence, no remote checker.
set -euo pipefail
CAPSULECTL="$1"; MATERIALITY="$2"; R="$3"
cd "$R"
export HOME="$R/home" XDG_CONFIG_HOME="$R/config"
mkdir -p "$HOME" out
unset CAPSULE_DEAL_CHECK_URL
deal() { "$CAPSULECTL" --profile synthetic-two-deal deal "$@"; }

deal init --dir "$R/store" --materiality "$MATERIALITY" >/dev/null

# Deal 1: a purchase from Example Merchant, checked and paid.
d1=$(deal open --input in/open.json | jq -r .deal_id)
deal check --deal "$d1" --input in/check-pay.json >out/deal-1-check.json
deal note --deal "$d1" --kind act --input in/act-pay.json >/dev/null

# Deal 2: another purchase from the same merchant, the identical act.
d2=$(deal open --input in/open.json | jq -r .deal_id)
deal check --deal "$d2" --input in/check-pay.json >out/deal-2-check.json
deal note --deal "$d2" --kind act --input in/act-pay-2.json >/dev/null

# The counterparty's shared copy of each deal (sealed as a disclosure first).
"$CAPSULECTL" --profile synthetic-two-deal disclose --deal "$d1" --share counterparty --to counterparty --out out/deal-1.counterparty.bundle.json >/dev/null
"$CAPSULECTL" --profile synthetic-two-deal disclose --deal "$d2" --share counterparty --to counterparty --out out/deal-2.counterparty.bundle.json >/dev/null

# The user's own copy of each deal, last, so it holds every record.
"$CAPSULECTL" --profile synthetic-two-deal bundle --deal "$d1" --out out/deal-1.bundle.json >/dev/null
"$CAPSULECTL" --profile synthetic-two-deal bundle --deal "$d2" --out out/deal-2.bundle.json >/dev/null
printf '%s\n%s\n' "$d1" "$d2" >out/deal-ids.txt
