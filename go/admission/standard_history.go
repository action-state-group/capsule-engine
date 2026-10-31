package admission

import (
	"encoding/json"
	"errors"
	"fmt"
	"strconv"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// StandardHistoryCounts reads the ladder's inputs from a standard_history/1
// card (AAC spec/standard-history-v0.md §4, §5.3; evidencebook-skills#87):
// the card's per-band counts as Card.Bands, and its received disputes as
// Card.Disputes. artifact is the card's bytes from an answer the caller has
// verified, card form included (AAC go/standardhistory); this reads only the
// bands and disputes members, and refuses them when they do not read.
//
// The artifact must be what AAC standard-history-v0 §3 says it is, the JCS
// of the card: bytes that are exactly the JCS encoding of their own parse, as
// a pack-definition.json must be (Load). That one rule refuses trailing data,
// duplicate keys, other whitespace or key order, float and -0 forms, and
// strings whose bytes do not round-trip, so Go and Rust read the same bytes
// the same way. Members are then matched by exact key.
//
// A band tally maps to one BandCount: Subjects are its decided deals (held
// plus did_not_hold) and Adverse those that did not hold; undecided deals
// are left out. A band whose decade is null (zero-amount deals) has no band
// and is skipped. The currency is the card's SR assetCode as written, with
// the decade in that asset's own unit, so a pack maps it through accept like
// any other currency (BTC: {band: sat, scale: 8}).
func StandardHistoryCounts(artifact []byte) ([]BandCount, *Disputes, error) {
	if err := isJCS(artifact); err != nil {
		return nil, nil, fmt.Errorf("standard_history/1 card: the artifact is not JCS bytes: %w", err)
	}
	// Raw members throughout, decoded into maps: keys match exactly (a struct
	// would match them case-insensitively), and each number is read from its
	// own text.
	var card map[string]json.RawMessage
	if json.Unmarshal(artifact, &card) != nil || card == nil {
		return nil, nil, errors.New("standard_history/1 card: not an object")
	}
	var derivation string
	if raw, ok := card["derivation"]; ok && json.Unmarshal(raw, &derivation) != nil {
		return nil, nil, errors.New("standard_history/1 card: derivation must be a string")
	}
	if derivation != "standard_history/1" {
		return nil, nil, fmt.Errorf("standard_history/1 card: derivation is %q", derivation)
	}
	if raw, ok := card["bands"]; !ok || string(raw) == "null" {
		return nil, nil, errors.New("standard_history/1 card: bands is missing")
	}
	var tallies []json.RawMessage
	if json.Unmarshal(card["bands"], &tallies) != nil {
		return nil, nil, errors.New("standard_history/1 card: bands must be an array")
	}
	bands := []BandCount{}
	for i, raw := range tallies {
		var b map[string]json.RawMessage
		if json.Unmarshal(raw, &b) != nil || b == nil {
			return nil, nil, fmt.Errorf("standard_history/1 card: bands[%d] must be an object", i)
		}
		where := fmt.Sprintf("standard_history/1 card: bands[%d]", i)
		var currency string
		if err := json.Unmarshal(b["currency"], &currency); err != nil || currency == "" {
			return nil, nil, fmt.Errorf("%s: currency must be a non-empty string", where)
		}
		var counts [3]int64
		for j, k := range []string{"held", "did_not_hold", "undecided"} {
			n, err := cardCount(b[k])
			if err != nil {
				return nil, nil, fmt.Errorf("%s.%s: %w", where, k, err)
			}
			counts[j] = n
		}
		if string(b["decade"]) == "null" {
			continue
		}
		decade, err := cardInt(b["decade"])
		if err != nil {
			return nil, nil, fmt.Errorf("%s.decade: %w", where, err)
		}
		bands = append(bands, BandCount{Currency: currency, Decade: int(decade),
			Subjects: counts[0] + counts[1], Adverse: counts[1]})
	}
	var disputes, r map[string]json.RawMessage
	_ = json.Unmarshal(card["disputes"], &disputes)
	if json.Unmarshal(disputes["received"], &r) != nil || r == nil {
		return nil, nil, errors.New("standard_history/1 card: disputes.received must be an object")
	}
	of, err := cardCount(r["of"])
	if err != nil {
		return nil, nil, fmt.Errorf("standard_history/1 card: disputes.received.of: %w", err)
	}
	var byOutcome map[string]json.RawMessage
	if json.Unmarshal(r["by_outcome"], &byOutcome) != nil || byOutcome == nil {
		return nil, nil, errors.New("standard_history/1 card: disputes.received.by_outcome must be an object")
	}
	open, err := cardCount(byOutcome["open"])
	if err != nil {
		return nil, nil, fmt.Errorf("standard_history/1 card: disputes.received.by_outcome.open: %w", err)
	}
	return bands, &Disputes{Open: open, Of: of}, nil
}

// isJCS reports why src is not exactly the JCS encoding of its own parse.
func isJCS(src []byte) error {
	v, err := canon.FromJSON(src)
	if err != nil {
		return err
	}
	if jcs, err := canon.JCS(v); err != nil || jcs != string(src) {
		return errors.New("not the JCS encoding of their own parse")
	}
	return nil
}

// cardInt reads a card integer: a plain JSON integer of at most 2^53 − 1 in
// magnitude (AAC standard-history-v0 §4, "Counts"). The artifact is JCS, so
// an integer is in its one canonical form; a quoted number is not one.
func cardInt(raw json.RawMessage) (int64, error) {
	n, err := strconv.ParseInt(string(raw), 10, 64)
	if err != nil {
		return 0, errors.New("must be an integer")
	}
	if n > maxSafeInteger || n < -maxSafeInteger {
		return 0, errors.New("must be an integer of at most 2^53 - 1 in magnitude")
	}
	return n, nil
}

// cardCount is cardInt for a count, which is 0 or more.
func cardCount(raw json.RawMessage) (int64, error) {
	n, err := cardInt(raw)
	if err == nil && n < 0 {
		err = errors.New("must be 0 or more")
	}
	return n, err
}
