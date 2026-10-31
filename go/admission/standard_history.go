package admission

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
)

// StandardHistoryCounts reads the ladder's inputs from a standard_history/1
// card (AAC spec/standard-history-v0.md §4, §5.3; evidencebook-skills#87):
// the card's per-band counts as Card.Bands, and its received disputes as
// Card.Disputes. artifact is the card's bytes from an answer the caller has
// verified, card form included (AAC go/standardhistory); this reads only the
// bands and disputes members, and refuses them when they do not read.
//
// A band tally maps to one BandCount: Subjects are its decided deals (held
// plus did_not_hold) and Adverse those that did not hold; undecided deals
// are left out. A band whose decade is null (zero-amount deals) has no band
// and is skipped. The currency is the card's SR assetCode as written, with
// the decade in that asset's own unit, so a pack maps it through accept like
// any other currency (BTC: {band: sat, scale: 8}).
func StandardHistoryCounts(artifact []byte) ([]BandCount, *Disputes, error) {
	// Raw members throughout: each number is read from its own text, so a
	// quoted number or a float form is refused as the Rust reader refuses it.
	var card struct {
		Derivation string                       `json:"derivation"`
		Bands      []map[string]json.RawMessage `json:"bands"`
		Disputes   struct {
			Received map[string]json.RawMessage `json:"received"`
		} `json:"disputes"`
	}
	if err := json.NewDecoder(bytes.NewReader(artifact)).Decode(&card); err != nil {
		return nil, nil, fmt.Errorf("standard_history/1 card: %w", err)
	}
	if card.Derivation != "standard_history/1" {
		return nil, nil, fmt.Errorf("standard_history/1 card: derivation is %q", card.Derivation)
	}
	if card.Bands == nil {
		return nil, nil, errors.New("standard_history/1 card: bands is missing")
	}
	bands := []BandCount{}
	for i, b := range card.Bands {
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
	r := card.Disputes.Received
	if r == nil {
		return nil, nil, errors.New("standard_history/1 card: disputes.received is missing")
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

// cardInt reads a card integer: a plain JSON integer of at most 2^53 − 1 in
// magnitude (AAC standard-history-v0 §4, "Counts"). A fraction or exponent
// form, such as 75.0 or 1e2, is not one: JCS never writes it.
func cardInt(raw json.RawMessage) (int64, error) {
	s := string(raw)
	if s == "" || s == "null" {
		return 0, errors.New("must be an integer")
	}
	n, err := strconv.ParseInt(s, 10, 64)
	if err != nil || n > maxSafeInteger || n < -maxSafeInteger {
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
