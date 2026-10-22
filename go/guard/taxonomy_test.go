package guard

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"testing"
	"time"

	"github.com/action-state-group/capsule-engine/go/canon"
	"github.com/action-state-group/capsule-engine/go/packs"
)

const syntheticTable = `{"taxonomy_version": "t", "actions": [
 {"name": "money.transfer", "trigger_class": "COMMIT", "consequential": true, "fail_open_allowed": false, "approver_role": "treasury-approver", "legacy_aliases": ["payments.wire"]},
 {"name": "booking.create", "trigger_class": "COMMIT", "consequential": true, "fail_open_allowed": false, "approver_role": null, "legacy_aliases": []},
 {"name": "info.query", "trigger_class": null, "consequential": false, "fail_open_allowed": true, "approver_role": null, "legacy_aliases": []}]}`

// The pack's definition digest does not cover the action taxonomy, so the
// vendored table is pinned on its own: capsule-engine's
// guards/action_taxonomy.json at 7ae6b95 (PR #117's merge), taxonomy_version
// 6. Any other bytes fail here, even bytes with the same JCS form.
func TestVendoredTaxonomyIsPinned(t *testing.T) {
	const fileSHA256 = "7563fcaeb6c192a27877fae3131bca1021526046c8976475c46eebf4fb374272"
	const jcsDigest = "fc12eb90bc2b3c85bb49c4d3d7be8f384c6e0b8e189bc3b68a753a4f1af5cd94"
	sum := sha256.Sum256(packs.ActionTaxonomy)
	if got := hex.EncodeToString(sum[:]); got != fileSHA256 {
		t.Fatalf("vendored action_taxonomy.json sha256 %s, pinned %s", got, fileSHA256)
	}
	v, err := canon.FromJSON(packs.ActionTaxonomy)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := canon.Digest(v); err != nil || got != jcsDigest {
		t.Fatalf("vendored action_taxonomy.json JCS digest %s (%v), pinned %s", got, err, jcsDigest)
	}
	tax, err := engineTaxonomy()
	if err != nil || tax.version != "6" {
		t.Fatalf("taxonomy_version %q (%v), want 6", tax.version, err)
	}
}

// The vendored table: money.transfer has an approver (a sole caps failure
// escalates); from taxonomy 3 money.purchase, money.subscription,
// booking.create and booking.modify have account_holder, and from taxonomy 4
// so do booking.cancel, data.delete, communication.publish and
// disclosure.personal, from taxonomy 5 communication.send (and
// comms.external, its legacy name), and from taxonomy 6 marketplace.offer,
// marketplace.sale and agreement.accept; money.refund has none (it denies);
// and a legacy name resolves to its canonical row (classes.py resolve).
func TestVendoredTaxonomy(t *testing.T) {
	tax, err := engineTaxonomy()
	if err != nil {
		t.Fatal(err)
	}
	if role, ok := tax.approver("money.transfer"); !ok || role != "treasury-approver" {
		t.Errorf("money.transfer approver %q %v", role, ok)
	}
	for _, class := range []string{"money.purchase", "money.subscription", "booking.create", "booking.modify",
		"booking.cancel", "data.delete", "communication.publish", "disclosure.personal", "communication.send", "comms.external",
		"marketplace.offer", "marketplace.sale", "agreement.accept"} {
		if role, ok := tax.approver(class); !ok || role != "account_holder" {
			t.Errorf("%s approver %q %v, want account_holder", class, role, ok)
		}
	}
	for _, class := range []string{"money.refund", "disclosure.secret", "external_commitment.other", "no-such-class", ""} {
		if _, ok := tax.approver(class); ok {
			t.Errorf("%q has an approver", class)
		}
	}
	if got := tax.canonical("comms.external"); got != "communication.send" {
		t.Errorf("comms.external resolves to %q", got)
	}
}

// A cap keyed by a legacy name and the action's canonical name are one cap,
// and an action sent under the legacy name meets the canonical class's
// limits (caps.py cap_for).
func TestCapsResolvesLegacyNames(t *testing.T) {
	tax, err := loadTaxonomy([]byte(syntheticTable))
	if err != nil {
		t.Fatal(err)
	}
	limits, err := tax.resolveLimits(map[string]int64{"payments.wire": 100})
	if err != nil || limits["money.transfer"] != 100 || len(limits) != 1 {
		t.Fatalf("resolved %v, %v", limits, err)
	}
	if _, err := tax.resolveLimits(map[string]int64{"payments.wire": 100, "money.transfer": 200}); err == nil {
		t.Error("two limits for one class under a legacy and a canonical name were accepted")
	}
	e, _ := everydayEngine(t)
	e.tax = tax
	e.capsMinor = map[string]int64{"money.transfer": 100}
	e.perActionMinor = map[string]int64{"money.transfer": 50}
	amount := int64(60)
	for _, class := range []string{"money.transfer", "payments.wire"} {
		a := Action{Verb: "pay", Operator: "o", Developer: "d", ActionClass: &class, AmountMinor: &amount}
		if c := e.checkCaps(a, nil, time.Now(), checkInputs{}); c.Result != Fail {
			t.Errorf("%s: caps %s, want fail on the per-action limit", class, c.Result)
		}
	}
}

func TestTaxonomyRefusesUnderDeclaredRows(t *testing.T) {
	for name, table := range map[string]string{
		"missing key":     strings.Replace(syntheticTable, `"fail_open_allowed": false, "approver_role": null, "legacy_aliases": []},`, `"approver_role": null, "legacy_aliases": []},`, 1),
		"duplicate name":  strings.Replace(syntheticTable, `"name": "booking.create"`, `"name": "money.transfer"`, 1),
		"alias collision": strings.Replace(syntheticTable, `"legacy_aliases": ["payments.wire"]`, `"legacy_aliases": ["booking.create"]`, 1),
	} {
		if _, err := loadTaxonomy([]byte(table)); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}
