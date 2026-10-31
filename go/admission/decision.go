package admission

import "github.com/action-state-group/capsule-engine/go/canon"

// Block is the decision as the admission_decision block a caller seals
// (Mesh-LLM/capsules-plugin's admission.rs BLOCK). It keeps that block's
// names, verdict, source, reads and rows with row and result, so loop step
// 04 reads what it reads today, and adds tier, band, asks and each row's
// count, denominator and effect. reads holds the policy entry only; the
// caller appends what it read (the card, by its own commitment) and names
// the counterparty by its own reference. The block is local-only: it is
// about one counterparty and is never exported for another party.
func (d Decision) Block() canon.Value {
	tier := canon.Null()
	if d.Tier != "" {
		tier = canon.Str(d.Tier)
	}
	band := canon.Null()
	if d.Band != nil {
		band = canon.Obj(canon.M("currency", canon.Str(d.Band.Currency)), canon.M("decade", canon.Int(int64(d.Band.Decade))))
	}
	rows := make([]canon.Value, len(d.Rows))
	for i, r := range d.Rows {
		ms := []canon.Member{canon.M("row", canon.Str(r.Row)), canon.M("result", canon.Str(r.Result)), canon.M("effect", canon.Str(r.Effect))}
		if r.Count != nil {
			ms = append(ms, canon.M("count", canon.Int(*r.Count)))
		}
		if r.Denominator != nil {
			ms = append(ms, canon.M("denominator", canon.Int(*r.Denominator)))
		}
		rows[i] = canon.Obj(ms...)
	}
	asks := make([]canon.Value, len(d.Asks))
	for i, a := range d.Asks {
		asks[i] = canon.Obj(canon.M("row", canon.Str(a.Row)), canon.M("derivation", canon.Str(a.Derivation)),
			canon.M("deadline_s", canon.Int(a.DeadlineS)))
	}
	policy := canon.Obj(
		canon.M("kind", canon.Str("policy")),
		canon.M("policy", canon.Str(d.Pack.ID)),
		canon.M("digest", canon.Obj(canon.M("alg", canon.Str("SHA-256")), canon.M("digest", canon.Str(d.Pack.Digest)))),
	)
	return canon.Obj(
		canon.M("verdict", canon.Str(d.Verdict)),
		canon.M("source", canon.Str(d.Source)),
		canon.M("tier", tier),
		canon.M("band", band),
		canon.M("reads", canon.Arr([]canon.Value{policy})),
		canon.M("rows", canon.Arr(rows)),
		canon.M("asks", canon.Arr(asks)),
	)
}
