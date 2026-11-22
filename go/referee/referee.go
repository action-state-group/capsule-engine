// SPDX-License-Identifier: Apache-2.0

// Package referee is a receiving party's check of a referee's sealed Result:
// the Go port of Python capsule_engine.referee.verify_referee_result, deciding
// the shared vectors in conformance-vectors/referee/v0 as Python does.
//
// VerifyResult establishes that the Result capsule verifies under the key it
// carries, is signed by the referee key both halves' terms name, names exactly
// the two halves given, and is the Result document it commits to, which itself
// verifies as an Evidence Result v0 document. BindOpenings is the check a
// receiver runs first, so that terms a forged file supplied cannot name their
// own referee: each half is authentic and each opening (action, terms)
// recomputes the action_digest its half committed to.
//
// What is not here: judging a deal, sealing a Result, and the judge-side checks
// of a half's terms and of how the two halves answer each other (Python's
// _check_half terms and operator checks, _check_pair and _check_shared). A
// receiver that must also hold that a Result is about a deal its own key sealed
// one half of does that with its own key; it is not in this package.
//
// The codes are wire tokens: a receiver may seal one into a refusal record.
// They may be added to, never renamed.
package referee

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"unicode/utf8"

	"github.com/action-state-group/agent-action-capsule/go/canonical"
	"github.com/action-state-group/agent-action-capsule/go/envelope"
	"github.com/action-state-group/agent-action-capsule/go/verify"
)

// The refusal codes, as in the Python module's RefereeError.
const (
	CodeHalfUnverified   = "half_unverified"
	CodeOpeningMismatch  = "opening_mismatch"
	CodeHalvesUnpaired   = "halves_unpaired"
	CodeResultUnverified = "result_unverified"
	CodeNotNamedReferee  = "not_named_referee"
	CodeNotAResult       = "not_a_result"
	CodeOtherDeal        = "other_deal"
	CodeResultMismatch   = "result_mismatch"
	CodeResultInvalid    = "result_invalid"
)

const (
	// ResultBlock is the compute_attestation member a sealed Result carries.
	ResultBlock = "referee_result"
	// ResultSchema is that block's schema tag.
	ResultSchema = "capsule-engine/referee-result/v1"
)

// Error is a refusal with a stable Code. Input that cannot be read as the
// documents it should be (a half with no capsule_id, a float in the Result,
// terms that are not an object) is CodeNotAResult, as the evidencebook node
// maps Python's non-RefereeError exceptions.
type Error struct {
	Code   string
	Detail string
}

func (e *Error) Error() string { return e.Code + ": " + e.Detail }

func refuse(code, format string, args ...any) *Error {
	return &Error{Code: code, Detail: fmt.Sprintf(format, args...)}
}

// Party is one party's signed half and the opening of its commitment, each as
// JSON bytes.
type Party struct {
	Half   []byte // the signed Capsule, with its signature and key_id
	Action string
	Terms  []byte // the terms the half's action_digest commits to
}

// Inputs are the two halves and their terms as the receiver holds them.
type Inputs struct {
	BuyerHalf, SellerHalf   []byte
	BuyerTerms, SellerTerms []byte
}

// ActionDigest is the commitment a half seals: SHA-256 of the JCS of
// {"action", "terms"}.
func ActionDigest(action string, terms any) (string, error) {
	return digest(map[string]any{"action": action, "terms": terms})
}

// ResultDigest is the digest a sealed Result names: SHA-256 of the Result's
// JCS bytes. doc is the Result's JSON.
func ResultDigest(doc []byte) (string, error) {
	v, tainted, err := decode(doc)
	if err != nil {
		return "", err
	}
	if tainted {
		return "", errors.New("the document holds an unpaired surrogate")
	}
	return digest(v)
}

func digest(v any) (string, error) {
	b, err := canonical.JCS(v)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:]), nil
}

// decode reads one JSON text as Python's json.loads does: numbers kept as
// json.Number, nothing but whitespace after the value, invalid UTF-8 refused.
// It has no depth bound of its own: verify.Verify grades a too-deep capsule as
// not authentic, as Python does. An unpaired surrogate escape is accepted by
// json.loads and refused only when the value is later canonicalized (the
// capsule's recomputed id, the Result digest, the action digest); the value is
// returned with tainted set, so each caller refuses where Python does, since
// encoding/json would otherwise read the escape as U+FFFD and a signature over
// that text could verify here and not there.
func decode(raw []byte) (v any, tainted bool, err error) {
	if !utf8.Valid(raw) {
		return nil, false, errors.New("not valid UTF-8")
	}
	tainted = canonical.CheckJSONText(raw) != nil
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	if err := d.Decode(&v); err != nil {
		return nil, false, err
	}
	if _, err := d.Token(); err != io.EOF {
		return nil, false, errors.New("trailing data after JSON value")
	}
	return v, tainted, nil
}

type object = map[string]any

// authentic: the capsule recomputes (Class 1, as Python's verify_capsule) and
// its producer signature verifies under the key_id it carries.
func authentic(c any) bool {
	m, ok := c.(object)
	if !ok {
		return false
	}
	if res := verify.Verify(m, nil, nil); !res.OK {
		return false
	}
	verdict, _ := envelope.VerifyRecordSignature(m)
	return verdict == envelope.Authored
}

func obj(v any) object {
	m, _ := v.(object)
	return m
}

// compute is capsule.model_attestation.compute_attestation, or an empty object.
func compute(capsule object) object {
	return obj(obj(capsule["model_attestation"])["compute_attestation"])
}

// cited are the capsule ids a capsule points back at: its chain parent and its
// agent-action-capsule references.
func cited(capsule object) map[string]bool {
	out := map[string]bool{}
	if id, ok := obj(capsule["chain"])["parent_capsule_id"].(string); ok {
		out[id] = true
	}
	refs, _ := capsule["references"].([]any)
	for _, r := range refs {
		m := obj(r)
		if d, ok := m["digest"].(string); ok && m["type"] == "agent-action-capsule" {
			out[d] = true
		}
	}
	return out
}

// decodeObject reads a document that must be a JSON object; text that is not
// JSON, or not an object, cannot be read as one.
func decodeObject(raw []byte, what string) (object, *Error) {
	v, _, err := decode(raw)
	if err != nil {
		return nil, refuse(CodeNotAResult, "%s cannot be read: %v", what, err)
	}
	m, ok := v.(object)
	if !ok {
		return nil, refuse(CodeNotAResult, "%s is not a JSON object", what)
	}
	return m, nil
}

// BindOpenings checks each party's half and opening, then that the two halves
// are not signed by one key. It is the check a receiver makes before it reads
// the terms' referee key: verify_referee_result takes the openings as given.
func BindOpenings(buyer, seller Party) error {
	for _, side := range []struct {
		role string
		p    Party
	}{{"buyer", buyer}, {"seller", seller}} {
		value, tainted, err := decode(side.p.Half)
		if err != nil {
			return refuse(CodeNotAResult, "the %s's half cannot be read: %v", side.role, err)
		}
		if tainted || !authentic(value) {
			return refuse(CodeHalfUnverified, "the %s's half does not verify under the key it carries", side.role)
		}
		terms, tainted, err := decode(side.p.Terms)
		if err != nil || tainted {
			return refuse(CodeNotAResult, "the %s's terms cannot be read", side.role)
		}
		want, err := ActionDigest(side.p.Action, terms)
		if err != nil {
			return refuse(CodeNotAResult, "the %s's opening cannot be read: %v", side.role, err)
		}
		if got := compute(obj(value))["action_digest"]; got != want {
			return refuse(CodeOpeningMismatch, "the %s's opening does not recompute its half's action_digest", side.role)
		}
	}
	b, _ := decodeObject(buyer.Half, "")
	s, _ := decodeObject(seller.Half, "")
	if b["key_id"] == s["key_id"] {
		return refuse(CodeHalvesUnpaired, "both halves are signed by one key")
	}
	return nil
}

// refereeKey is terms.referee.key_id as Python's set of keys holds it: a
// comparable value, or false when it is unhashable there (a list or object).
func refereeKey(terms any) (key any, ok bool, err *Error) {
	t, isObj := terms.(object)
	if !isObj {
		return nil, false, refuse(CodeNotAResult, "terms are not an object")
	}
	ref := t["referee"]
	switch r := ref.(type) {
	case nil:
		return nil, true, nil
	case object:
		if len(r) == 0 {
			return nil, true, nil
		}
		return hashable(r["key_id"])
	case string:
		if r == "" {
			return nil, true, nil
		}
	case bool:
		if !r {
			return nil, true, nil
		}
	case json.Number:
		if f, err := r.Float64(); err == nil && f == 0 {
			return nil, true, nil
		}
	case []any:
		if len(r) == 0 {
			return nil, true, nil
		}
	}
	return nil, false, refuse(CodeNotAResult, "terms.referee is not an object")
}

func hashable(v any) (any, bool, *Error) {
	switch v.(type) {
	case object, []any:
		return nil, false, refuse(CodeNotAResult, "terms.referee.key_id is not a value")
	}
	return v, true, nil
}

// VerifyResult is a receiving party's check of a sealed Result: it verifies,
// it is signed by the referee both halves' terms name, its block and its
// references name exactly these halves, and doc is the Result it names. It
// returns nil or an *Error.
func VerifyResult(capsule, doc []byte, in Inputs) error {
	// The document is read first, as Python's callers read it before the check.
	resultDoc, docTainted, derr := decode(doc)
	if derr != nil {
		return refuse(CodeNotAResult, "the Result document cannot be read: %v", derr)
	}
	value, tainted, err := decode(capsule)
	if err != nil {
		return refuse(CodeNotAResult, "the Result capsule cannot be read: %v", err)
	}
	if tainted || !authentic(value) {
		return refuse(CodeResultUnverified, "the Result capsule does not verify under the key it carries")
	}
	c := obj(value)
	buyerTerms, _, err := decode(in.BuyerTerms)
	if err != nil {
		return refuse(CodeNotAResult, "the buyer's terms cannot be read: %v", err)
	}
	sellerTerms, _, err := decode(in.SellerTerms)
	if err != nil {
		return refuse(CodeNotAResult, "the seller's terms cannot be read: %v", err)
	}
	keys := []any{}
	for _, t := range []any{buyerTerms, sellerTerms} {
		k, _, kerr := refereeKey(t)
		if kerr != nil {
			return kerr
		}
		if !contains(keys, k) {
			keys = append(keys, k)
		}
	}
	if len(keys) != 1 || !contains(keys, c["key_id"]) {
		return refuse(CodeNotNamedReferee, "the Result is not signed by the referee the deal terms name")
	}
	block, isObj := compute(c)[ResultBlock].(object)
	if !isObj || block["schema"] != ResultSchema {
		return refuse(CodeNotAResult, "the capsule carries no referee Result")
	}
	buyerHalf, perr := decodeObject(in.BuyerHalf, "the buyer's half")
	if perr != nil {
		return perr
	}
	sellerHalf, perr := decodeObject(in.SellerHalf, "the seller's half")
	if perr != nil {
		return perr
	}
	bid, bok := buyerHalf["capsule_id"]
	sid, sok := sellerHalf["capsule_id"]
	if !bok || !sok {
		return refuse(CodeNotAResult, "a half carries no capsule_id")
	}
	// Python compares the pair of ids, then builds a set of them (which refuses
	// an unhashable id) and checks the capsule cites both.
	if !sameValue(block["buyer_half"], bid) || !sameValue(block["seller_half"], sid) {
		return refuse(CodeOtherDeal, "the Result names other halves")
	}
	bs, bstr := bid.(string)
	ss, sstr := sid.(string)
	if !hashableValue(bid) || !hashableValue(sid) {
		return refuse(CodeNotAResult, "a half's capsule_id is not a value")
	}
	if pointed := cited(c); !bstr || !sstr || !pointed[bs] || !pointed[ss] {
		return refuse(CodeOtherDeal, "the Result names other halves")
	}
	want, derr := digest(resultDoc)
	if derr != nil || docTainted {
		return refuse(CodeNotAResult, "the Result document cannot be read: %v", derr)
	}
	if block["result_digest"] != want {
		return refuse(CodeResultMismatch, "the document is not the Result the capsule names")
	}
	if err := checkResult(resultDoc); err != nil {
		var re *Error
		if errors.As(err, &re) {
			return re
		}
		return refuse(CodeResultInvalid, "%v", err)
	}
	return nil
}

// sameValue is Python's ==, for decoded JSON: lists and objects by content,
// numbers by value (2 == 2.0, and True == 1).
func sameValue(a, b any) bool {
	if x, ok := ratOf(a); ok {
		y, ok := ratOf(b)
		return ok && x.Cmp(y) == 0
	}
	switch x := a.(type) {
	case []any:
		y, ok := b.([]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for i := range x {
			if !sameValue(x[i], y[i]) {
				return false
			}
		}
		return true
	case object:
		y, ok := b.(object)
		if !ok || len(x) != len(y) {
			return false
		}
		for k, v := range x {
			w, ok := y[k]
			if !ok || !sameValue(v, w) {
				return false
			}
		}
		return true
	}
	return a == b
}

// ratOf is a number, or a boolean as Python counts it, as an exact rational.
func ratOf(v any) (*big.Rat, bool) {
	switch n := v.(type) {
	case json.Number:
		return new(big.Rat).SetString(string(n))
	case bool:
		if n {
			return big.NewRat(1, 1), true
		}
		return big.NewRat(0, 1), true
	}
	return nil, false
}

func hashableValue(v any) bool {
	switch v.(type) {
	case object, []any:
		return false
	}
	return true
}

func contains(set []any, v any) bool {
	for _, x := range set {
		if sameValue(x, v) {
			return true
		}
	}
	return false
}
