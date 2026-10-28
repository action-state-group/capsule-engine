package guard

import (
	"cmp"
	"encoding/json"
	"fmt"
	"maps"
	"slices"
	"time"
)

// The history a check of a sale bundle's thread is decided on
// (capsule-engine #146: report/sale_bundle.py sale_history).
//
// A check of a carried thread is given the history a live check of it is
// given (external-check-input/v0): each act with an amount of every carried
// thread sealed before the check, oldest first, with the sale's key beside
// it as item_ref and, from the check the act rests on, the counterparty
// that check sealed and its counterparty_profile companion. Each entry is
// read by the reader a live check's history is read by (InputHistory.AddEntry).
// The history is complete only when the sale is complete and the check's
// whole second falls before CertifiedUntil. Records are ordered by the
// whole second they name, and two threads are two logs, so records of two
// threads are ordered only when their seconds differ: the history is not
// complete when another thread's act is sealed in the check's own second,
// or two acts of different threads before it share a second. A record a
// thread carries without disclosing it, sealed before the check, may be an
// act, so it is written unread, as the engine's history ledger writes an
// entry whose act cannot be read.

// SaleCheck is the check capsuleID of a carried thread as a replay of the
// sale decides it: its action, read from its thread's bundle as DecodeRecord
// reads a bundle's record, with its companion's counterparty_profile
// (replay.py _companion_profiles) and the sale's key as item_ref; and the
// sale's history for it, which single_commitment reads whole and
// release_on_acceptance 1.2.0 reads cut to the live window
// (InputHistory.releaseWindow; CheckSale), with the task-authority record the
// sale discloses under the digest the check cites (TaskAuthorityRecord).
// It is an error when no carried thread holds the record.
func (s SaleBundle) Check(capsuleID string) (Action, InputHistory, error) {
	for t, thread := range s.Threads {
		for i, r := range thread.records {
			if id, _ := textOf(r["capsule_id"]); id != capsuleID {
				continue
			}
			index := threadIndexOf(thread)
			var profile json.RawMessage
			if shown, ok := thread.disclosed[capsuleID]; ok {
				if d, ok := rawDigest(shown); ok {
					profile = index.profiles[d]
				}
			}
			a, err := decodeRecord(thread.bundle, capsuleID, profile)
			if err != nil {
				return Action{}, InputHistory{}, err
			}
			a.ItemRef = nil
			if s.Key != nil {
				key := *s.Key
				a.ItemRef = &key
			}
			h, err := s.history(t, i, a, 0)
			if err != nil {
				return Action{}, InputHistory{}, err
			}
			w, err := s.history(t, i, a, rulesHistoryDays)
			if err != nil {
				return Action{}, InputHistory{}, err
			}
			h.releaseWindow = &w
			h.TaskAuthorityRecord = disclosedTaskAuthority(s.disclosed(), a.TaskAuthorityRef)
			h.RepresentingOpening, h.replayed = replayedWords(a, s.openings()).opening, true
			return a, h, nil
		}
	}
	return Action{}, InputHistory{}, fmt.Errorf("record: no thread the sale bundle carries holds %s", capsuleID)
}

// disclosed is every carried thread's disclosed agent_input, by capsule id
// (sale_bundle.py SaleBundle.disclosed).
func (s SaleBundle) disclosed() map[string]json.RawMessage {
	out := map[string]json.RawMessage{}
	for _, thread := range s.Threads {
		maps.Copy(out, thread.disclosed)
	}
	return out
}

// openings are the openings of represent checks' words every carried
// thread's deal reports carry, by commitment (sale_bundle.py
// SaleThread.openings, replay_sale).
func (s SaleBundle) openings() map[string]json.RawMessage {
	out := map[string]json.RawMessage{}
	for _, thread := range s.Threads {
		for commitment, opening := range representingOpenings(objectOf(thread.bundle)) {
			if _, ok := out[commitment]; !ok {
				out[commitment] = opening
			}
		}
	}
	return out
}

// rulesHistoryDays is how far back a live check's history reaches:
// capsulectl sends the acts sealed in the last 31 days (history_state.py
// RULES_HISTORY_DAYS; capsule-cli RulesHistoryDays).
const rulesHistoryDays = 31

// history is the sale's history for the record at index in thread own. With
// days above 0, an act sealed more than that many days before the check is
// left out, as capsulectl leaves it out of a live check's history; it is
// still complete when nothing in that window is missing.
func (s SaleBundle) history(own, index int, a Action, days int) (InputHistory, error) {
	at := secondOf(s.Threads[own].records[index]["timestamp"])
	complete := s.Complete() && at != nil && s.CertifiedUntil != nil && !at.Add(time.Second).After(*s.CertifiedUntil)
	type dated struct {
		at            time.Time
		thread, index int
		entry         saleEntry
	}
	var before []dated
	for t, thread := range s.Threads {
		ix := threadIndexOf(thread)
		for i, r := range thread.records {
			entry, ok := saleHistoryEntry(r, thread, ix, s.Key)
			if !ok {
				continue
			}
			sealed := secondOf(r["timestamp"])
			switch {
			case sealed == nil || at == nil:
				complete = false
			case days > 0 && sealed.Before(at.Add(-time.Duration(days)*24*time.Hour)):
				// Outside the live window: not in a live check's history.
			case t != own && sealed.Equal(*at):
				// Another thread's act in the check's second: before or after it is not sealed.
				complete = false
			case t == own && i < index || t != own && sealed.Before(*at):
				before = append(before, dated{*sealed, t, i, entry})
			}
		}
	}
	threadsBySecond := map[time.Time]map[int]bool{}
	for _, b := range before {
		if threadsBySecond[b.at] == nil {
			threadsBySecond[b.at] = map[int]bool{}
		}
		threadsBySecond[b.at][b.thread] = true
		if len(threadsBySecond[b.at]) > 1 {
			// Two threads' acts in one second: which came first is not sealed.
			complete = false
		}
	}
	slices.SortStableFunc(before, func(x, y dated) int {
		return cmp.Or(x.at.Compare(y.at), cmp.Compare(x.thread, y.thread), cmp.Compare(x.index, y.index))
	})
	h := InputHistory{Complete: complete}
	for _, b := range before {
		if err := h.addSaleEntry(b.entry, a); err != nil {
			return InputHistory{}, err
		}
	}
	return h, nil
}

// saleEntry is one history entry of a sale: the capsule with what is beside
// it, and whether its agent_input is disclosed and bound.
type saleEntry struct {
	raw   json.RawMessage
	shown bool
}

// addSaleEntry reads one entry as a live check's history entry is read
// (AddEntry). An entry with no bound agent_input, or one whose act cannot
// be read, is written unread with its item_ref, as the engine's history
// ledger writes an entry action_for_history_entry cannot read.
func (h *InputHistory) addSaleEntry(e saleEntry, a Action) error {
	if e.shown {
		if _, err := DecodeHistoryAct(e.raw); err == nil {
			return h.AddEntry(e.raw, a)
		}
	}
	var header struct {
		CapsuleID string `json:"capsule_id"`
		Operator  string `json:"operator"`
		Timestamp string `json:"timestamp"`
		ItemRef   string `json:"item_ref"`
	}
	if err := json.Unmarshal(e.raw, &header); err != nil {
		return fmt.Errorf("history entry: %w", err)
	}
	r := Record{CapsuleID: header.CapsuleID, Operator: header.Operator, Timestamp: header.Timestamp, live: liveUnread}
	if hex64.MatchString(header.ItemRef) {
		r.ItemRef = &header.ItemRef
	}
	h.Acts = append(h.Acts, r)
	return nil
}

// threadIndex is a carried thread's disclosed records by digest, and each
// check's counterparty_profile block by the check's digest.
type threadIndex struct {
	byDigest map[string]jsonObject
	profiles map[string]json.RawMessage
}

func threadIndexOf(thread SaleThread) threadIndex {
	ix := threadIndex{byDigest: map[string]jsonObject{}, profiles: companionProfiles(thread)}
	for _, shown := range thread.disclosed {
		if d, ok := rawDigest(shown); ok {
			ix.byDigest[d] = objectOf(shown)
		}
	}
	return ix
}

// companionProfiles is replay.py _companion_profiles: each check's
// counterparty_profile block, by the check's record digest, from the bound
// companions among a thread's records (a counterparty_profile record with
// exactly one ref, rel about, type deal-record). A check two companions
// name gets their blocks as a list, which is no block, so it is ignored; a
// companion without one gets {}, ignored the same way.
func companionProfiles(thread SaleThread) map[string]json.RawMessage {
	found := map[string][]json.RawMessage{}
	for _, r := range boundRecords(thread.records, thread.disclosed) {
		block := objectOf(r[dealProfile])
		if recordType, _ := textOf(block["record_type"]); recordType != profileInput {
			continue
		}
		var refs []json.RawMessage
		if json.Unmarshal(block["refs"], &refs) != nil || len(refs) != 1 {
			continue
		}
		ref := objectOf(refs[0])
		rel, _ := textOf(ref["rel"])
		kind, _ := textOf(ref["type"])
		digest, ok := refDigest(refs[0])
		if ref == nil || rel != "about" || kind != "deal-record" || !ok {
			continue
		}
		profile := block[profileInput]
		if isJSONNull(profile) {
			profile = json.RawMessage("{}")
		}
		found[digest] = append(found[digest], profile)
	}
	out := map[string]json.RawMessage{}
	for digest, blocks := range found {
		out[digest] = blocks[0]
		if len(blocks) > 1 {
			list, _ := json.Marshal(blocks)
			out[digest] = list
		}
	}
	return out
}

// saleHistoryEntry is replay.py _history_entry: the entry capsulectl gives for
// a record that is an act with an amount (its capsule, its agent_input, the
// sale's key as item_ref, and, from the check it rests on, the counterparty
// that check sealed and the check's counterparty_profile companion). A
// record the thread carries without disclosing it is given with no
// agent_input: it may be an act. False for any other record.
func saleHistoryEntry(record jsonObject, thread SaleThread, ix threadIndex, key *string) (saleEntry, bool) {
	entry := jsonObject{}
	for k, v := range record {
		entry[k] = v
	}
	if key != nil {
		entry[itemRefInput], _ = json.Marshal(*key)
	}
	id, _ := textOf(record["capsule_id"])
	shown, disclosed := thread.disclosed[id]
	if !disclosed || !isBound(record, shown) {
		raw, err := json.Marshal(entry)
		return saleEntry{raw: raw}, err == nil
	}
	act := objectOf(shown)
	isAct := typeOf(act) == typedActRecord || recordTypeOf(act) == actionStep
	if !isAct || !isInteger(objectOf(act["body"])["amount_minor"]) {
		return saleEntry{}, false
	}
	entry["agent_input"] = shown
	if check := actCheck(act, ix.byDigest); check != nil {
		if counterparty := objectOf(check["body"])[counterpartyInput]; objectOf(counterparty) != nil {
			entry[counterpartyInput] = counterparty
		}
		if d, ok := digestOf(check); ok {
			if profile, ok := ix.profiles[d]; ok {
				entry[profileInput] = profile
			}
		}
	}
	raw, err := json.Marshal(entry)
	return saleEntry{raw: raw, shown: true}, err == nil
}

// actCheck is replay.py _act_check: the check a typed act rests on, as
// capsulectl finds it for the act's counterparty. The act's one
// authorized_by ref names an evaluation, whose one checks ref names the
// check, or an approval, whose proposed_action_ref names it. Nil when a
// link is missing or for an act that is not typed.
func actCheck(act jsonObject, byDigest map[string]jsonObject) jsonObject {
	verdict := byDigest[oneRef(act, "authorized_by")]
	var check jsonObject
	switch typeOf(verdict) {
	case typedEvaluation:
		check = byDigest[oneRef(verdict, "checks")]
	case typedApproval:
		d, _ := refDigest(objectOf(verdict["body"])["proposed_action_ref"])
		check = byDigest[d]
	default:
		return nil
	}
	if typeOf(check) != proposedActionType || objectOf(check["body"]) == nil {
		return nil
	}
	return check
}

// oneRef is the digest of a typed record's one rel ref, or "".
func oneRef(s jsonObject, rel string) string {
	refs := refsNamed(s, rel)
	if len(refs) != 1 {
		return ""
	}
	raw, err := json.Marshal(refs[0])
	if err != nil {
		return ""
	}
	d, _ := refDigest(raw)
	return d
}

// secondOf is a capsule's timestamp in UTC, cut to its whole second, or
// nil: records are ordered by the second they name, whatever fraction or
// zone a producer writes.
func secondOf(raw json.RawMessage) *time.Time {
	s, ok := textOf(raw)
	if !ok {
		return nil
	}
	at, err := parseInstant(s)
	if err != nil {
		return nil
	}
	at = at.UTC().Truncate(time.Second)
	return &at
}
