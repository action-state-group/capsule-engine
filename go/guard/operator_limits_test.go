package guard

import (
	"strings"
	"testing"
	"time"

	"github.com/action-state-group/capsule-engine/go/canon"
	"github.com/action-state-group/capsule-engine/go/pack"
)

// fixedLimits is an OperatorLimits with one value per key, for every class
// the pack limits.
type fixedLimits map[string]LimitInForce

func (f fixedLimits) InForce(key, _ string, _ time.Time) (LimitInForce, bool) {
	l, ok := f[key]
	return l, ok
}

const fiveDigest = "ff664f8fa095e2684046544069bd63688b8cba601ee88032878f42c5dbf5e101"

func sixDollarPurchase(t *testing.T) Action {
	t.Helper()
	a, err := DecodeAction(strings.NewReader(`{"verb":"make_purchase","operator":"household-limits-fixture","developer":"household-assistant@v1",` +
		`"action_class":"money.purchase","amount_minor":600,"currency":"USD","target":"shop/six-dollars","rail":"card","recurrence":"one_time",` +
		`"action_id":"make_purchase/limits-six-dollars","timestamp":"2026-10-08T09:01:00Z"}`))
	if err != nil {
		t.Fatal(err)
	}
	return a
}

// capsEvidence is the caps evidence the engine would digest, as JCS.
func capsEvidence(t *testing.T, e *Engine, a Action) (Decision, Constraint, string) {
	t.Helper()
	d, err := e.Check(a, nil)
	if err != nil {
		t.Fatal(err)
	}
	c, ok := d.Constraint("caps")
	if !ok {
		t.Fatal("no caps")
	}
	j, err := canon.JCS(c.evidence)
	if err != nil {
		t.Fatal(err)
	}
	return d, c, j
}

// capsule-engine tests/test_operator_limits.py, the acceptance case: an
// operator's 5.00 per-action limit holds a 6.00 purchase for approval, the
// caps evidence names 500 as the per-action limit, tripped, with its source
// operator_profile and the profile's digest, and the window's source
// definition_default. The pack and its definition digest are unchanged, and
// evidence rewritten to claim the default does not digest to the sealed one.
func TestTheCapsEvidenceNamesTheOperatorLimit(t *testing.T) {
	p, err := pack.LoadEveryday037()
	if err != nil {
		t.Fatal(err)
	}
	digestBefore := p.DefinitionDigest
	plain, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	_, _, defaultEvidence := capsEvidence(t, plain, sixDollarPurchase(t))
	if !strings.Contains(defaultEvidence, `"limit_sources":{"per_action":{"limit_source":"definition_default"},"window":{"limit_source":"definition_default"}}`) {
		t.Fatalf("with no profile the evidence does not name the default: %s", defaultEvidence)
	}

	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	e.WithOperatorLimits(fixedLimits{
		CapsKeyPerAction: {ValueMinor: 500, Source: LimitSource{Source: LimitSourceProfile, ProfileDigest: fiveDigest}},
		CapsKeyWindow:    {ValueMinor: 10000, Source: LimitSource{Source: LimitSourceDefault}},
	})
	d, c, evidence := capsEvidence(t, e, sixDollarPurchase(t))
	if d.Outcome != Escalate || c.Result != Fail {
		t.Fatalf("outcome %s, caps %s; want escalate, fail (engine: ESCALATE)", d.Outcome, c.Result)
	}
	for _, want := range []string{
		`"per_action_cap_minor":500`,
		`"tripped":[{"limit":"per_action","observed_minor":600,"threshold_minor":500}]`,
		`"limit_sources":{"per_action":{"limit_source":"operator_profile","profile_digest":"` + fiveDigest + `"},"window":{"limit_source":"definition_default"}}`,
	} {
		if !strings.Contains(evidence, want) {
			t.Errorf("evidence lacks %s: %s", want, evidence)
		}
	}
	if p.DefinitionDigest != digestBefore {
		t.Fatal("the pack's definition digest moved")
	}
	sealed, _, err := c.EvidenceDigest()
	if err != nil {
		t.Fatal(err)
	}
	forged := strings.Replace(evidence, `"limit_source":"operator_profile","profile_digest":"`+fiveDigest+`"`, `"limit_source":"definition_default"`, 1)
	v, err := canon.FromJSON([]byte(forged))
	if err != nil {
		t.Fatal(err)
	}
	if fd, _ := canon.Digest(v); fd == sealed {
		t.Fatal("evidence claiming the default digests to the sealed evidence")
	}
}

// A pending raise is sealed beside the limit in force (caps.py
// PendingRaise).
func TestTheCapsEvidenceSealsAPendingRaise(t *testing.T) {
	p, err := pack.LoadEveryday037()
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	held := LimitSource{Source: LimitSourceProfile, ProfileDigest: fiveDigest,
		PendingRaise: &PendingRaise{ValueMinor: 4000, EffectiveAt: "2026-10-08T22:00:00Z", Source: LimitSourceProfile, ProfileDigest: strings.Repeat("b", 64)}}
	e.WithOperatorLimits(fixedLimits{CapsKeyPerAction: {ValueMinor: 500, Source: held}})
	_, _, evidence := capsEvidence(t, e, sixDollarPurchase(t))
	want := `"per_action":{"limit_source":"operator_profile","pending_raise":{"effective_at":"2026-10-08T22:00:00Z","limit_source":"operator_profile","profile_digest":"` +
		strings.Repeat("b", 64) + `","value_minor":4000},"profile_digest":"` + fiveDigest + `"}`
	if !strings.Contains(evidence, want) {
		t.Fatalf("evidence lacks the pending raise: %s", evidence)
	}
}
