package guard

import (
	"encoding/json"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// Opening a salted commitment a deal record seals (commit_alg
// sha256-jcs-nonce256), ported from capsule-engine 0c93a58
// guards/checks/commitment.py: SHA-256 over the JCS bytes of
// {"nonce", "text"}, with a 256-bit nonce in lowercase hex. capsulectl seals
// every *_commitment this way. price_floor opens the task authority's
// bounds_commitment with the commercial-bounds document as the text;
// promise_never/1.1.0 opens a represent check's representing.text_commitment
// with the words it is about to say.

// textCommitment is the commitment to text under nonce (text_commitment).
func textCommitment(nonce, text string) (string, error) {
	return canon.Digest(canon.Obj(canon.M("nonce", canon.Str(nonce)), canon.M("text", canon.Str(text))))
}

// opensCommitment is opens_commitment over decoded JSON from a checker
// input: nonce a string of 64 lowercase hex, text a string, sealed 64
// lowercase hex, and the commitment they recompute equal to sealed. A text
// holding a lone surrogate escape has no JCS bytes, so it opens nothing
// (Go's decoder would replace it with U+FFFD and digest that).
func opensCommitment(nonce, text json.RawMessage, sealed string) bool {
	n, ok := stringOf(nonce)
	if !ok || !hex64.MatchString(n) || !hex64.MatchString(sealed) {
		return false
	}
	t, ok := stringOf(text)
	if !ok || loneSurrogate(text) {
		return false
	}
	got, err := textCommitment(n, t)
	return err == nil && got == sealed
}
