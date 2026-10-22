package guard

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"slices"
	"strings"
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// THE PAYEE KEYED PER PROFILE (AMENDMENT 3; capsule-engine #115 at 4ac9fb0).
// capsulectl passes record.counterparty_profile (and each history act's) beside
// the sealed capsule: {fp_alg: "hmac-sha256-profile-key", ids: {payee: <64
// lowercase hex>}}. In that shape the target is
// payee-fp:hmac-sha256-profile-key:<hex>, so one merchant is one target across
// the profile's deals; in any other it is ignored, named in the r06 evidence
// (ignored_inputs), and the per-deal target stands.
//
// Held against the engine side's vectors (testdata/parity/payee-fp-profile:
// capsulectl's own two-deal records, run through the engine by generate.py
// at 97a2aff): per check the target, the ignored inputs, and the dedupe
// result and evidence digest; per companion, that the replay gives it no
// decision (capsule-engine #121; before it, the companion's dedupe was n/a);
// per live entry the target and ignored inputs; and r06 + dedupe with and
// without the profile, and with a bad one.

const payeeFPDir = "payee-fp-profile"

type payeeFPVectors struct {
	CapsuleEngineCommit string                       `json:"capsule_engine_commit"`
	Replay              map[string]payeeFPReplay     `json:"replay"`
	LiveTarget          map[string]payeeFPLiveTarget `json:"live_target"`
	LiveSeenBefore      map[string]payeeFPLiveSeen   `json:"live_seen_before"`
	CapsFold            string                       `json:"caps_fold"`
}

type payeeFPReplay struct {
	Inputs []struct {
		Capsule map[string]json.RawMessage `json:"capsule"`
		Record  json.RawMessage            `json:"record"`
	} `json:"inputs"`
	Decisions []struct {
		ActionID          string          `json:"action_id"`
		RecordType        string          `json:"record_type"`
		StatesAct         bool            `json:"states_act"`
		Target            *string         `json:"target"`
		IgnoredInputs     []string        `json:"ignored_inputs"`
		Outcome           string          `json:"outcome"`
		DedupeResult      string          `json:"dedupe_result"`
		DedupeEvidence    json.RawMessage `json:"dedupe_evidence"`
		DecisionCapsuleID string          `json:"decision_capsule_id"`
	} `json:"decisions"`
	// Undecided are the action_ids of the records the replay gives no
	// decision, in input order.
	Undecided []string `json:"undecided"`
}

type payeeFPLiveTarget struct {
	Entry         json.RawMessage `json:"entry"`
	Target        string          `json:"target"`
	IgnoredInputs []string        `json:"ignored_inputs"`
}

type payeeFPLiveSeen struct {
	Entries            []json.RawMessage `json:"entries"`
	Deal1Decision      string            `json:"deal_1_decision"`
	SeenBeforeResult   string            `json:"seen_before_result"`
	SeenBeforeEvidence json.RawMessage   `json:"seen_before_evidence"`
	DedupeResult       string            `json:"dedupe_result"`
	DedupeEvidence     json.RawMessage   `json:"dedupe_evidence"`
	Outcome            string            `json:"outcome"`
}

func payeeFP(t *testing.T) payeeFPVectors {
	t.Helper()
	var v payeeFPVectors
	if err := json.Unmarshal(readFile(t, "testdata", "parity", payeeFPDir, "vectors-97a2aff.json"), &v); err != nil {
		t.Fatal(err)
	}
	// Pinned to the commit they were run at, not to the leg 3 run: at
	// dbdaca12 the engine reads these taxonomy 4 records under their own
	// taxonomy (9ac3722) and holds their class-keyed checks n/a, so a rerun
	// is another vector set, not the same one at a later commit.
	if v.CapsuleEngineCommit != "97a2aff71b8293f70dc335e6fb6a5fbe1ada31d0" {
		t.Fatalf("vectors at %s", v.CapsuleEngineCommit)
	}
	return v
}

// jcsDigest is the engine's json_digest of a JSON value.
func jcsDigest(t *testing.T, raw json.RawMessage) string {
	t.Helper()
	v, err := canon.FromJSON(raw)
	if err != nil {
		t.Fatal(err)
	}
	d, err := canon.Digest(v)
	if err != nil {
		t.Fatal(err)
	}
	return d
}

// withMember is the JSON object raw with member k set to v.
func withMember(t *testing.T, raw json.RawMessage, k string, v json.RawMessage) json.RawMessage {
	t.Helper()
	var m map[string]json.RawMessage
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatal(err)
	}
	m[k] = v
	out, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

// companionBlocks is what capsulectl resolves for each check, by the check's
// record digest, from the companions among records (the engine's
// _companion_profiles): a counterparty_profile record with exactly one ref,
// rel about, type deal-record, SHA-256. One companion gives its block (or {}
// when it has none); two give a list of them, which is no block.
func companionBlocks(t *testing.T, records []json.RawMessage) map[string]json.RawMessage {
	t.Helper()
	found := map[string][]json.RawMessage{}
	for _, raw := range records {
		var r struct {
			XDeal map[string]json.RawMessage `json:"x-deal-v0"`
		}
		if err := json.Unmarshal(raw, &r); err != nil {
			t.Fatal(err)
		}
		var recordType string
		if json.Unmarshal(r.XDeal["record_type"], &recordType) != nil || recordType != profileInput {
			continue
		}
		var refs []json.RawMessage
		if json.Unmarshal(r.XDeal["refs"], &refs) != nil || len(refs) != 1 {
			continue
		}
		var ref struct {
			Rel  string `json:"rel"`
			Type string `json:"type"`
		}
		digest, ok := typedRefDigest(refs[0])
		if json.Unmarshal(refs[0], &ref) != nil || ref.Rel != "about" || ref.Type != "deal-record" || !ok {
			continue
		}
		var d string
		_ = json.Unmarshal(digest, &d)
		block := r.XDeal[profileInput]
		if block == nil {
			block = json.RawMessage(`{}`)
		}
		found[d] = append(found[d], block)
	}
	out := map[string]json.RawMessage{}
	for d, blocks := range found {
		if len(blocks) == 1 {
			out[d] = blocks[0]
			continue
		}
		list, err := json.Marshal(blocks)
		if err != nil {
			t.Fatal(err)
		}
		out[d] = list
	}
	return out
}

// REPLAY: each step of the engine side's 13 bundles, the check given the block its
// companion carries as capsulectl gives it live.
func TestPayeeProfileReplayMatchesTheEngine(t *testing.T) {
	v := payeeFP(t)
	e, _ := everydayEngine(t)
	if len(v.Replay) != 13 {
		t.Fatalf("%d replay cases", len(v.Replay))
	}
	for name, c := range v.Replay {
		records := make([]json.RawMessage, len(c.Inputs))
		for i, in := range c.Inputs {
			records[i] = in.Record
		}
		blocks := companionBlocks(t, records)
		var ledger []Record
		var undecided []string
		next := 0
		for i, in := range c.Inputs {
			var actionID string
			if err := json.Unmarshal(in.Capsule["action_id"], &actionID); err != nil {
				t.Fatal(err)
			}
			capsule, err := json.Marshal(in.Capsule)
			if err != nil {
				t.Fatal(err)
			}
			entry := withMember(t, capsule, "agent_input", in.Record)
			var sealed struct {
				XDeal struct {
					RecordType string `json:"record_type"`
				} `json:"x-deal-v0"`
			}
			if err := json.Unmarshal(in.Record, &sealed); err != nil {
				t.Fatal(err)
			}
			if block, ok := blocks[jcsDigest(t, in.Record)]; ok {
				entry = withMember(t, entry, profileInput, block)
			} else if sealed.XDeal.RecordType != "check" {
				// A record that is not a check (the companion itself)
				// never reads a block: the engine's bridge does not run
				// on it, so even a good one changes nothing.
				entry = withMember(t, entry, profileInput, v.goodBlock(t))
			}
			a, err := DecodeCheckInputRecord(entry)
			if err != nil {
				t.Fatalf("%s step %d: %v", name, i, err)
			}
			if a.statesNoAct {
				// The replay gives it no decision (#121), so it never
				// reaches the ledger a later check reads.
				if a.Target != nil || len(a.ignoredInputs) != 0 {
					t.Errorf("%s step %d: a record that states no act reads target %v ignored %v", name, i, shown(a.Target), a.ignoredInputs)
				}
				undecided = append(undecided, actionID)
				continue
			}
			if next >= len(c.Decisions) || c.Decisions[next].ActionID != actionID {
				t.Fatalf("%s step %d: %s states an act, but is not the engine's next decision", name, i, actionID)
			}
			want := c.Decisions[next]
			next++
			if !eqPtr(a.Target, want.Target) || !slices.Equal(a.ignoredInputs, want.IgnoredInputs) ||
				a.statesNoAct == want.StatesAct {
				t.Errorf("%s step %d: target %v ignored %v act %v; engine %v %v %v", name, i, shown(a.Target), a.ignoredInputs, !a.statesNoAct,
					shown(want.Target), want.IgnoredInputs, want.StatesAct)
			}
			at, err := parseInstant(a.Timestamp)
			if err != nil {
				t.Fatal(err)
			}
			dd, err := e.checkDedupe(a, ledger, at)
			if err != nil {
				t.Fatal(err)
			}
			digest, _, err := dd.EvidenceDigest()
			if err != nil {
				t.Fatal(err)
			}
			if string(dd.Result) != want.DedupeResult || digest != jcsDigest(t, want.DedupeEvidence) {
				t.Errorf("%s step %d: dedupe %s %s; engine %s %s", name, i, dd.Result, digest, want.DedupeResult, want.DedupeEvidence)
			}
			r := RecordFor(a, Decision{Outcome: want.Outcome})
			r.CapsuleID, r.DryRun = want.DecisionCapsuleID, boolp(true)
			ledger = append(ledger, r)
		}
		if next != len(c.Decisions) || !slices.Equal(undecided, c.Undecided) {
			t.Errorf("%s: %d of %d decisions; undecided %v, engine %v", name, next, len(c.Decisions), undecided, c.Undecided)
		}
	}
	// The case the amendment exists for: deal 2's check repeats deal 1's
	// act at the same merchant, and only the profile key shows it.
	got := v.Replay["two-deals-with-companions"].Decisions[1]
	if got.DedupeResult != "fail" || got.Outcome != Escalate {
		t.Fatalf("two deals with companions: deal 2 dedupe %s, %s", got.DedupeResult, got.Outcome)
	}
	if got := v.Replay["two-deals-without-companions"].Decisions[1]; got.DedupeResult != "pass" {
		t.Fatalf("two deals without companions: deal 2 dedupe %s", got.DedupeResult)
	}
}

// goodBlock is the valid counterparty_profile block of the "good" live entry.
func (v payeeFPVectors) goodBlock(t *testing.T) json.RawMessage {
	t.Helper()
	var entry map[string]json.RawMessage
	if err := json.Unmarshal(v.LiveTarget["good"].Entry, &entry); err != nil || entry[profileInput] == nil {
		t.Fatalf("no good block: %v", err)
	}
	return entry[profileInput]
}

// LIVE: the record entry of an external-check-input/v0 envelope, with no
// block, the good one, and six bad shapes. A null block is no block (the
// engine's "is not None").
func TestPayeeProfileLiveTargetMatchesTheEngine(t *testing.T) {
	v := payeeFP(t)
	if len(v.LiveTarget) != 8 {
		t.Fatalf("%d live cases", len(v.LiveTarget))
	}
	null, err := DecodeCheckInputRecord(withMember(t, v.LiveTarget["absent"].Entry, profileInput, json.RawMessage(`null`)))
	if err != nil || null.Target == nil || *null.Target != v.LiveTarget["absent"].Target || len(null.ignoredInputs) != 0 {
		t.Errorf("null block: target %s ignored %v, %v", shown(null.Target), null.ignoredInputs, err)
	}
	// The block is read only where the engine's bridge reads a check: an
	// x-deal-v0 record_type of "check" whose body states action_class and
	// taxonomy_version. Elsewhere a good block changes nothing and is not
	// marked, the per-deal target standing.
	good := v.LiveTarget["good"].Entry
	for name, edit := range map[string][]string{
		"another record_type": {"x-deal-v0", "record_type", `"action"`},
		"no taxonomy_version": {"body", "taxonomy_version", `""`},
		"no action_class":     {"body", "action_class", `""`},
	} {
		var entry map[string]json.RawMessage
		if err := json.Unmarshal(good, &entry); err != nil {
			t.Fatal(err)
		}
		var input map[string]json.RawMessage
		if err := json.Unmarshal(entry["agent_input"], &input); err != nil {
			t.Fatal(err)
		}
		input[edit[0]] = withMember(t, input[edit[0]], edit[1], json.RawMessage(edit[2]))
		ai, err := json.Marshal(input)
		if err != nil {
			t.Fatal(err)
		}
		raw := withMember(t, good, "agent_input", ai)
		raw = withMember(t, raw, "model_attestation",
			json.RawMessage(`{"compute_attestation":{"agent_input_digest":"`+jcsDigest(t, ai)+`"}}`))
		a, err := DecodeCheckInputRecord(raw)
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if a.Target == nil || *a.Target != v.LiveTarget["absent"].Target || len(a.ignoredInputs) != 0 {
			t.Errorf("%s: target %s ignored %v; want the per-deal target, unmarked", name, shown(a.Target), a.ignoredInputs)
		}
	}
	for name, c := range v.LiveTarget {
		a, err := DecodeCheckInputRecord(c.Entry)
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if a.Target == nil || *a.Target != c.Target || !slices.Equal(a.ignoredInputs, c.IgnoredInputs) {
			t.Errorf("%s: target %s ignored %v; engine %s %v", name, shown(a.Target), a.ignoredInputs, c.Target, c.IgnoredInputs)
		}
		// --record reads one capsule as the engine's action_for_record
		// does: the block beside it is not its input.
		plain, err := DecodeRecord(c.Entry, "")
		if err != nil {
			t.Fatal(err)
		}
		if want := v.LiveTarget["absent"].Target; plain.Target == nil || *plain.Target != want || len(plain.ignoredInputs) != 0 {
			t.Errorf("%s via DecodeRecord: target %s ignored %v", name, shown(plain.Target), plain.ignoredInputs)
		}
	}
}

// LIVE r06 + dedupe: deal 1 accepted, then deal 2 checked; with the profile
// r06 knows the merchant and dedupe finds the act in another deal; without
// it, or with a bad block (named in the r06 evidence), neither does.
func TestPayeeProfileSeenBeforeMatchesTheEngine(t *testing.T) {
	v := payeeFP(t)
	e, _ := everydayEngine(t)
	for _, name := range []string{"with-profile", "without-profile", "bad-profile"} {
		c, ok := v.LiveSeenBefore[name]
		if !ok || len(c.Entries) != 2 || c.Deal1Decision != "accept" {
			t.Fatalf("%s: missing or not two entries", name)
		}
		first, err := DecodeCheckInputRecord(c.Entries[0])
		if err != nil {
			t.Fatal(err)
		}
		r := RecordFor(first, Decision{Outcome: Allow})
		// The engine's deal-1 decision capsule, which a dedupe match names.
		var ev struct {
			MatchedCapsuleID string `json:"matched_capsule_id"`
		}
		_ = json.Unmarshal(c.DedupeEvidence, &ev)
		r.CapsuleID = ev.MatchedCapsuleID
		a, err := DecodeCheckInputRecord(c.Entries[1])
		if err != nil {
			t.Fatal(err)
		}
		if e.seenBefore.fold.digest != "73dbf8cd8a36fadca0ac823d2b17592fdc457c24081bc44667f032601e914033" {
			t.Fatalf("r06 reads fold %s", e.seenBefore.fold.digest)
		}
		sb := checkSeenBefore(a, []Record{r}, e.seenBefore)
		sbDigest, _, err := sb.EvidenceDigest()
		if err != nil {
			t.Fatal(err)
		}
		at, err := parseInstant(a.Timestamp)
		if err != nil {
			t.Fatal(err)
		}
		dd, err := e.checkDedupe(a, []Record{r}, at)
		if err != nil {
			t.Fatal(err)
		}
		ddDigest, _, err := dd.EvidenceDigest()
		if err != nil {
			t.Fatal(err)
		}
		if string(sb.Result) != c.SeenBeforeResult || sbDigest != jcsDigest(t, c.SeenBeforeEvidence) ||
			string(dd.Result) != c.DedupeResult || ddDigest != jcsDigest(t, c.DedupeEvidence) {
			t.Errorf("%s: r06 %s %s dedupe %s %s; engine r06 %s %s dedupe %s %s", name, sb.Result, sbDigest, dd.Result, ddDigest,
				c.SeenBeforeResult, c.SeenBeforeEvidence, c.DedupeResult, c.DedupeEvidence)
		}
	}
	var bad struct {
		IgnoredInputs []string `json:"ignored_inputs"`
	}
	_ = json.Unmarshal(v.LiveSeenBefore["bad-profile"].SeenBeforeEvidence, &bad)
	if v.LiveSeenBefore["with-profile"].SeenBeforeResult != "pass" || v.LiveSeenBefore["bad-profile"].SeenBeforeResult != "fail" ||
		!slices.Equal(bad.IgnoredInputs, []string{profileInput}) {
		t.Fatal("the vectors lost the cases they exist for")
	}
}

// HISTORY (plugin only: the engine's history is its own ledger):
// history[i].counterparty_profile keys an earlier act on the profile payee,
// as the engine's replay keys an earlier check on its companion. Bad or
// absent, the per-deal target stands; it is never part of the agent_input
// digest, so adding it does not unbind the act.
func TestPayeeProfileHistoryAct(t *testing.T) {
	v := payeeFP(t)
	good := v.LiveTarget["good"]
	var entry map[string]json.RawMessage
	if err := json.Unmarshal(good.Entry, &entry); err != nil {
		t.Fatal(err)
	}
	perDeal := v.LiveTarget["absent"].Target
	act := withMember(t, v.LiveTarget["absent"].Entry, "disposition", json.RawMessage(`{"decision":"accept"}`))
	for name, c := range map[string]struct {
		block json.RawMessage
		want  string
	}{
		"absent": {nil, perDeal},
		"null":   {json.RawMessage(`null`), perDeal},
		"good":   {entry[profileInput], good.Target},
	} {
		raw := act
		if c.block != nil {
			raw = withMember(t, act, profileInput, c.block)
		}
		r, err := DecodeHistoryAct(raw)
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if r.Target == nil || *r.Target != c.want {
			t.Errorf("history %s: target %s, want %s", name, shown(r.Target), c.want)
		}
	}
	for name, lt := range v.LiveTarget {
		if !strings.HasPrefix(name, "bad-") {
			continue
		}
		var bad map[string]json.RawMessage
		if err := json.Unmarshal(lt.Entry, &bad); err != nil {
			t.Fatal(err)
		}
		r, err := DecodeHistoryAct(withMember(t, act, profileInput, bad[profileInput]))
		if err != nil {
			t.Fatalf("history %s: %v", name, err)
		}
		if r.Target == nil || *r.Target != perDeal {
			t.Errorf("history %s: target %s, want the per-deal %s", name, shown(r.Target), perDeal)
		}
	}
}

// The profile target is never written into a reason: plan containment
// names "another payee" for it, and the per-deal target as before.
func TestPlanContainmentReasonNeverNamesAProfilePayee(t *testing.T) {
	p := plan{outcomeID: "o1", allowedActions: []string{"pay"}, binding: map[string]json.RawMessage{"subject": json.RawMessage(`"payee-fp:x:y"`)}}
	sum := sha256.Sum256([]byte("merchant"))
	hexPayee := hex.EncodeToString(sum[:])
	profile := ProfileTargetPrefix + hexPayee
	_, reason := p.contains(Action{Verb: "pay", Target: &profile})
	if strings.Contains(reason, hexPayee) || !strings.HasSuffix(reason, "action target is another payee") {
		t.Errorf("profile target: %q", reason)
	}
	perDeal := "payee-fp:hmac-sha256-deal-key:" + hexPayee
	if _, reason := p.contains(Action{Verb: "pay", Target: &perDeal}); !strings.HasSuffix(reason, "action target is "+perDeal) {
		t.Errorf("per-deal target: %q", reason)
	}
}
