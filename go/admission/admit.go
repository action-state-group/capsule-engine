package admission

import (
	"errors"
	"fmt"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// AdmitRefs are the members of an x-deal-v1 admit body that only the caller
// holds (evidencebook dealprofile/x-deal-v1/PROFILE.md, "admit";
// capsule-engine#303). Each is 64 lowercase hex.
type AdmitRefs struct {
	// Asks and Answers are the SHA-256 digests of the caller's own ask and
	// answer deal records of this admission, in the order it sealed them.
	Asks, Answers []string
	// CardDigest is the SHA-256 of the JCS bytes of the counterparty's card
	// document the decision read.
	CardDigest string
	// Nonce is a fresh random 256-bit value, drawn for this record only.
	// The agreement cites the admit by digest, and the nonce keeps that
	// digest from confirming a guess at the decision (u528).
	Nonce string
}

// ErrNoTier is Admit's answer for a decision without a tier (a NO, or a
// missing input decided without one): an admit body requires a tier, and no
// agreement cites such a decision, so it is not sealed as an admit. The
// admission_decision block still records it.
var ErrNoTier = errors.New("admission: a decision without a tier is not sealed as an x-deal-v1 admit")

// Admit is the decision as the body of an x-deal-v1 admit record, for the
// caller to seal in its own journal: outcome is the verdict, tier the tier
// admitted at, rows the ids of every row that applied to the deal's tier,
// whatever its result (as the pack names them), pack_digest the pack's definition_digest, and asks, answers,
// card_digest and nonce the caller's (refs). Like the block, it is
// local-only; only its digest leaves the holder, in an agreement.
func (d Decision) Admit(refs AdmitRefs) (canon.Value, error) {
	// The refs are checked first, so a caller's malformed refs are refused
	// whatever the verdict.
	asks, err := digestRefs("asks", refs.Asks)
	if err != nil {
		return canon.Value{}, err
	}
	answers, err := digestRefs("answers", refs.Answers)
	if err != nil {
		return canon.Value{}, err
	}
	if !digestPattern.MatchString(refs.CardDigest) {
		return canon.Value{}, fmt.Errorf("admission: admit card_digest %q is not 64 lowercase hex", refs.CardDigest)
	}
	if !digestPattern.MatchString(refs.Nonce) {
		return canon.Value{}, errors.New("admission: admit nonce is not 64 lowercase hex")
	}
	if d.Tier == "" {
		return canon.Value{}, ErrNoTier
	}
	rows := make([]string, len(d.Rows))
	for i, r := range d.Rows {
		rows[i] = r.Row
	}
	return canon.Obj(
		canon.M("outcome", canon.Str(d.Verdict)),
		canon.M("tier", canon.Str(d.Tier)),
		canon.M("rows", canon.Strs(rows)),
		canon.M("asks", asks),
		canon.M("pack_digest", canon.Str(d.Pack.Digest)),
		canon.M("card_digest", canon.Str(refs.CardDigest)),
		canon.M("answers", answers),
		canon.M("nonce", canon.Str(refs.Nonce)),
	), nil
}

// digestRefs writes x-deal-v0's digest_ref for each digest, which x-deal-v1
// uses for a record cited by digest.
func digestRefs(what string, digests []string) (canon.Value, error) {
	out := make([]canon.Value, len(digests))
	for i, h := range digests {
		if !digestPattern.MatchString(h) {
			return canon.Value{}, fmt.Errorf("admission: admit %s[%d] %q is not 64 lowercase hex", what, i, h)
		}
		out[i] = canon.Obj(canon.M("type", canon.Str("deal-record")), canon.M("digest_alg", canon.Str("SHA-256")),
			canon.M("digest", canon.Str(h)))
	}
	return canon.Arr(out), nil
}
