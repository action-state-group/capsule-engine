package guard

import (
	"encoding/json"
	"slices"
	"testing"
)

// bundleRecord is one record of a bundle: its capsule header and the
// agent_input it discloses.
type bundleRecord struct {
	Header     map[string]json.RawMessage
	AgentInput json.RawMessage
}

func realSellerRecords(t *testing.T, name string) []bundleRecord {
	t.Helper()
	var b struct {
		Records     []map[string]json.RawMessage `json:"records"`
		Disclosures map[string]struct {
			AgentInput json.RawMessage `json:"agent_input"`
		} `json:"disclosures"`
	}
	if err := json.Unmarshal(readFile(t, realSellerDir, name+".bundle.json"), &b); err != nil {
		t.Fatal(err)
	}
	var out []bundleRecord
	for _, h := range b.Records {
		var id string
		if err := json.Unmarshal(h["capsule_id"], &id); err != nil {
			t.Fatal(err)
		}
		if ai := b.Disclosures[id].AgentInput; ai != nil {
			out = append(out, bundleRecord{Header: h, AgentInput: ai})
		}
	}
	return out
}

// kind is a record's sealed type, and the action a proposed action states.
func (r bundleRecord) kind(t *testing.T) (string, string) {
	t.Helper()
	var in struct {
		Type  string `json:"type"`
		XDeal *struct {
			RecordType string `json:"record_type"`
		} `json:"x-deal-v0"`
		Body struct {
			Action string `json:"action"`
		} `json:"body"`
	}
	if err := json.Unmarshal(r.AgentInput, &in); err != nil {
		t.Fatal(err)
	}
	if in.XDeal != nil {
		return in.XDeal.RecordType, in.Body.Action
	}
	return in.Type, in.Body.Action
}

func (r bundleRecord) id(t *testing.T) string {
	t.Helper()
	var id string
	if err := json.Unmarshal(r.Header["capsule_id"], &id); err != nil {
		t.Fatal(err)
	}
	return id
}

// rebound is r disclosing agentInput, bound to it by digest and sealed at
// at, under a new capsule id: its signature no longer verifies, and the
// check reads only the binding (as the engine test's _rebound).
func rebound(t *testing.T, r bundleRecord, agentInput json.RawMessage, at string) bundleRecord {
	t.Helper()
	h := map[string]json.RawMessage{}
	for k, v := range r.Header {
		h[k] = v
	}
	d := agentInputDigest(agentInput)
	h["timestamp"] = json.RawMessage(`"` + at + `"`)
	h["capsule_id"] = json.RawMessage(`"` + agentInputDigest(json.RawMessage(`{"rebound":"`+d+`","at":"`+at+`"}`)) + `"`)
	h["model_attestation"] = json.RawMessage(`{"compute_attestation":{"agent_input_digest":"` + d + `"}}`)
	return bundleRecord{Header: h, AgentInput: agentInput}
}

func bundleOf(t *testing.T, records []bundleRecord) []byte {
	t.Helper()
	headers := make([]map[string]json.RawMessage, 0, len(records))
	disclosures := map[string]map[string]json.RawMessage{}
	for _, r := range records {
		headers = append(headers, r.Header)
		disclosures[r.id(t)] = map[string]json.RawMessage{"agent_input": r.AgentInput}
	}
	b, err := json.Marshal(map[string]any{"records": headers, "disclosures": disclosures})
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// commitProposal is the proposal_at check --record reads for the bundle's
// commit (the last one in it).
func commitProposal(t *testing.T, records []bundleRecord) *string {
	t.Helper()
	commit := ""
	for _, r := range records {
		if k, act := r.kind(t); k == proposedActionType && act == commitVerb {
			commit = r.id(t)
		}
	}
	a, err := DecodeRecord(bundleOf(t, records), commit)
	if err != nil {
		t.Fatal(err)
	}
	return a.ProposalAt
}

// edited is rec's agent_input with one body member set.
func edited(t *testing.T, r bundleRecord, path []string, v json.RawMessage) json.RawMessage {
	t.Helper()
	var set func(raw json.RawMessage, path []string) json.RawMessage
	set = func(raw json.RawMessage, path []string) json.RawMessage {
		var m map[string]json.RawMessage
		if err := json.Unmarshal(raw, &m); err != nil {
			t.Fatal(err)
		}
		if len(path) == 1 {
			m[path[0]] = v
		} else {
			m[path[0]] = set(m[path[0]], path[1:])
		}
		b, err := json.Marshal(m)
		if err != nil {
			t.Fatal(err)
		}
		return b
	}
	return set(r.AgentInput, path)
}

// The variants of capsule-engine #131's own test (tests/
// test_real_seller_bundle.py at f256abc), on buyer A's and C's real records:
// a typed commit is dated by the deal's latest offer an acceptance names,
// with no change of details after it, read within its own deal. Each
// variant that leaves no such offer leaves the commit undated, which
// offer_expiry fails as unverified, never passes.
func TestACommitIsDatedByTheOfferItsBuyerAccepted(t *testing.T) {
	a := realSellerRecords(t, "buyer-a")
	var offer, approval, claim bundleRecord
	for _, r := range a {
		switch k, act := r.kind(t); {
		case k == proposedActionType && act == offerVerb:
			offer = r
		case k == actionApprovalType:
			approval = r
		case k == "claim":
			claim = r
		}
	}
	// The offer's age is the at its record seals (capsule-engine #132); one
	// clock read in capsulectl, so its capsule's timestamp is the same instant.
	offerAt := sealedAt(t, offer)
	if string(offer.Header["timestamp"]) != `"`+offerAt+`"` {
		t.Fatalf("the offer's capsule timestamp %s is not its record's at %s", offer.Header["timestamp"], offerAt)
	}
	without := func(kind string) []bundleRecord {
		return slices.DeleteFunc(slices.Clone(a), func(r bundleRecord) bool { k, _ := r.kind(t); return k == kind })
	}
	insertBefore := func(records []bundleRecord, extra ...bundleRecord) []bundleRecord {
		i := slices.IndexFunc(records, func(r bundleRecord) bool { k, act := r.kind(t); return k == proposedActionType && act == commitVerb })
		return slices.Insert(slices.Clone(records), i, extra...)
	}
	insertAfterAcceptance := func(extra ...bundleRecord) []bundleRecord {
		i := slices.IndexFunc(a, func(r bundleRecord) bool { k, _ := r.kind(t); return k == actionApprovalType })
		return slices.Insert(slices.Clone(a), i+1, extra...)
	}
	later := rebound(t, offer, edited(t, offer, []string{"body", "amount_minor"}, json.RawMessage(`185000`)), "2026-10-09T16:54:27Z")
	change := rebound(t, claim, edited(t, claim, []string{"x-deal-v0", "record_type"}, json.RawMessage(`"change"`)), "2026-10-09T16:54:27Z")

	if got := commitProposal(t, a); got == nil || *got != offerAt {
		t.Fatalf("the real thread: %v, want the accepted offer's %s", got, offerAt)
	}
	for name, records := range map[string][]bundleRecord{
		"no acceptance":                        without(actionApprovalType),
		"a later offer after the acceptance":   insertAfterAcceptance(later),
		"an acceptance of a superseded offer":  insertBefore(without(actionApprovalType), later, approval),
		"a change of details after acceptance": insertAfterAcceptance(change),
	} {
		if got := commitProposal(t, records); got != nil {
			t.Errorf("%s: dated %s, want no proposal", name, *got)
		}
	}
	// An acceptance whose disclosed body is not the one its capsule seals is
	// never read (the engine's _bound): edited without resealing, it dates
	// nothing.
	unbound := bundleRecord{Header: approval.Header, AgentInput: edited(t, approval, []string{"body", "note"}, json.RawMessage(`"edited"`))}
	unsealed := slices.Clone(a)
	unsealed[slices.IndexFunc(unsealed, func(r bundleRecord) bool { k, _ := r.kind(t); return k == actionApprovalType })] = unbound
	if got := commitProposal(t, unsealed); got != nil {
		t.Errorf("an acceptance its capsule does not seal dated the commit: %s", *got)
	}
	if got := commitProposal(t, insertBefore(without(actionApprovalType), approval)); got == nil || *got != offerAt {
		t.Errorf("the acceptance moved to just before the commit: %v, want %s", got, offerAt)
	}
	// Another deal's accepted offer never dates this commit: buyer C's
	// whole thread (sale 2), then A's records without its offer or
	// acceptance.
	mixed := realSellerRecords(t, "buyer-c")
	for _, r := range a {
		if k, act := r.kind(t); k == actionApprovalType || (k == proposedActionType && act == offerVerb) {
			continue
		}
		mixed = append(mixed, r)
	}
	if got := commitProposal(t, mixed); got != nil {
		t.Errorf("another deal's acceptance dated A's commit: %s", *got)
	}
	// Read alone, with no bundle around it, a commit has no proposal_at.
	var commit bundleRecord
	for _, r := range a {
		if k, act := r.kind(t); k == proposedActionType && act == commitVerb {
			commit = r
		}
	}
	single := map[string]json.RawMessage{}
	for k, v := range commit.Header {
		single[k] = v
	}
	single["agent_input"] = commit.AgentInput
	raw, err := json.Marshal(single)
	if err != nil {
		t.Fatal(err)
	}
	if alone, err := DecodeRecord(raw, ""); err != nil || alone.ProposalAt != nil {
		t.Errorf("a commit read alone: %v %v", alone.ProposalAt, err)
	}
}

// sealedAt is the at a typed record's agent_input seals.
func sealedAt(t *testing.T, r bundleRecord) string {
	t.Helper()
	var in struct {
		At string `json:"at"`
	}
	if err := json.Unmarshal(r.AgentInput, &in); err != nil || in.At == "" {
		t.Fatalf("the record seals no at: %v", err)
	}
	return in.At
}

// capsule-engine #132 (test_the_offer_age_is_read_from_the_record_never_its_
// capsule_timestamp): the offer's age is the at its record seals, the value
// capsulectl sends live as record.proposal_at. A capsule stamped at another
// instant does not move it.
func TestTheOfferAgeIsReadFromTheRecordNeverItsCapsuleTimestamp(t *testing.T) {
	for _, buyer := range []string{"buyer-a", "buyer-c"} {
		records := realSellerRecords(t, buyer)
		i := slices.IndexFunc(records, func(r bundleRecord) bool { k, act := r.kind(t); return k == proposedActionType && act == offerVerb })
		offer := records[i]
		want := sealedAt(t, offer)
		h := map[string]json.RawMessage{}
		for k, v := range offer.Header {
			h[k] = v
		}
		h["timestamp"] = json.RawMessage(`"2026-01-01T00:00:00Z"`)
		records[i] = bundleRecord{Header: h, AgentInput: offer.AgentInput}
		if got := commitProposal(t, records); got == nil || *got != want {
			dated := "nothing"
			if got != nil {
				dated = *got
			}
			t.Errorf("%s: the commit is dated %s, want its offer record's at %s", buyer, dated, want)
		}
	}
}

// capsule-engine #131: a typed record states no act when its type is one of
// the six that do not; a proposed action, and any type not named, still
// does. An x-deal-v0 record is read from its record_type, as before.
func TestTypedRecordsThatStateNoAct(t *testing.T) {
	a := realSellerRecords(t, "buyer-a")
	seen := map[string]bool{}
	for _, r := range a {
		k, _ := r.kind(t)
		if k == "deal_report" {
			// No deal record: check --record refuses it, and the engine
			// gives a report no decision.
			continue
		}
		got, err := DecodeRecord(bundleOf(t, a), r.id(t))
		if err != nil {
			t.Fatal(err)
		}
		// Only a proposed action states an act in this thread: the
		// others are typed records that state none, or x-deal-v0 records
		// other than a check.
		if want := k != proposedActionType; got.statesNoAct != want {
			t.Errorf("%s: states no act %v, want %v", k, got.statesNoAct, want)
		}
		seen[k] = true
	}
	for _, k := range []string{"task-authority/v0", "action-evaluation/v0", "action-approval/v0", "action-record/v0", proposedActionType} {
		if !seen[k] {
			t.Errorf("the fixture has no %s", k)
		}
	}
	var authority bundleRecord
	for _, r := range a {
		if k, _ := r.kind(t); k == "task-authority/v0" {
			authority = r
		}
	}
	for _, typ := range []string{"made-up-record/v0", "action-outcome/v0", "action-report/v0"} {
		r := rebound(t, authority, edited(t, authority, []string{"type"}, json.RawMessage(`"`+typ+`"`)), "2026-10-09T16:54:20Z")
		got, err := DecodeRecord(bundleOf(t, []bundleRecord{r}), r.id(t))
		if err != nil {
			t.Fatal(err)
		}
		if want := typ != "made-up-record/v0"; got.statesNoAct != want {
			t.Errorf("%s: states no act %v, want %v", typ, got.statesNoAct, want)
		}
	}
}
