// Package admission is the admission evaluator (capsule-engine#265): a pinned
// admission pack and what was received about a counterparty give a Decision,
// GO, ASK, LIMIT or NO, with the tier and the rows that decided it. The
// evaluator does the math (the value band, the tier, the card's freshness,
// counts and denominators); a pack states thresholds only. The Rust crate
// (rust/capsule-engine, module admission) is the same evaluator, and both
// decide conformance-vectors/admission/v0 the same.
//
// Like the guard, the evaluator decides and its caller seals.
package admission

import (
	"fmt"
	"regexp"
	"strings"
	"time"
)

// Band is a deal's value band, (currency, decade) (Reference v3 §3A.14): the
// decade is the power of ten of the amount's leading digit, in the band
// currency's unit. 5 sat is decade 0, 1,000 sat decade 3, 0.25 USD decade -1.
type Band struct {
	Currency string
	Decade   int
}

// Unit is one row of a pack's accept table: the currency a band is counted
// in and the power of ten that turns an amount in the stated currency into
// that unit. msat counted as sat has Scale -3. There is no conversion across
// currencies: a pack maps only what it names, at a ratio it pins.
type Unit struct {
	Band  string
	Scale int
}

// amountPattern is a non-negative decimal with no exponent, sign or
// redundant leading zero: what an amount is read as, never a float.
var amountPattern = regexp.MustCompile(`^(0|[1-9][0-9]*)(\.[0-9]+)?$`)

// ValueBand is the band of amount in currency under accept. A zero amount
// has no band (ok false, err nil): it is free and sits in the lowest tier. A
// currency the table does not name, or an amount that is not a plain
// decimal, is an error, which the caller treats as a missing input.
func ValueBand(amount, currency string, accept map[string]Unit) (b Band, ok bool, err error) {
	q, err := quantity(amount, currency, accept)
	if err != nil {
		return Band{}, false, err
	}
	if q.digits == "" {
		return Band{}, false, nil
	}
	return Band{Currency: q.unit, Decade: len(q.digits) - 1 + q.exp}, true, nil
}

// amountQuantity is an amount in its band unit, exactly: digits × 10^exp,
// digits without leading zeros ("" for zero).
type amountQuantity struct {
	unit   string
	digits string
	exp    int
}

func quantity(amount, currency string, accept map[string]Unit) (amountQuantity, error) {
	unit, found := accept[currency]
	if !found {
		return amountQuantity{}, fmt.Errorf("currency %q is not in the pack's accept table", currency)
	}
	m := amountPattern.FindStringSubmatch(amount)
	if m == nil {
		return amountQuantity{}, fmt.Errorf("amount %q is not a plain decimal", amount)
	}
	frac := ""
	if m[2] != "" {
		frac = m[2][1:]
	}
	return amountQuantity{unit: unit.Band, digits: strings.TrimLeft(m[1]+frac, "0"), exp: unit.Scale - len(frac)}, nil
}

// atLeast reports whether q ≥ r; both are in the same unit. It compares
// leading decades, then the digits, padding only the shorter digit string,
// so no comparison allocates more than its inputs.
func (q amountQuantity) atLeast(r amountQuantity) bool {
	switch {
	case r.digits == "":
		return true
	case q.digits == "":
		return false
	}
	if dq, dr := len(q.digits)-1+q.exp, len(r.digits)-1+r.exp; dq != dr {
		return dq > dr
	}
	a, b := q.digits, r.digits
	if len(a) < len(b) {
		a += strings.Repeat("0", len(b)-len(a))
	} else {
		b += strings.Repeat("0", len(a)-len(b))
	}
	return a >= b
}

// Fresh reports whether a card whose latest checkpoint states at is within
// maxAge of now. Freshness is measured from the holder's checkpoint, never
// from when the card arrived (admission design §8). A checkpoint stated
// after now is not fresh: a clock that far ahead is not read as current.
func Fresh(at, now time.Time, maxAge time.Duration) bool {
	if at.After(now) {
		return false
	}
	return now.Sub(at) <= maxAge
}
