package guard

import (
	"encoding/json"
	"strings"
	"testing"
)

// HEX DIGESTS MATCH IN FULL (capsule-engine #118, 12ee139): the engine's hex
// digest checks are fullmatch, so a digest with a final newline is not a
// digest. Each hex validator of this port is fed "<64 hex>\n" and refuses
// it, and takes the same 64 hex without the newline: typedRefDigest (a
// typed record reference, the bridge's _typed_ref_digest: task_authority_ref,
// reverses_ref) and profileTarget (the profile payee, _profile_target).
func TestHexDigestsMatchInFull(t *testing.T) {
	hex := strings.Repeat("ab", 32)
	for _, tc := range []struct {
		digest string
		ok     bool
	}{{hex, true}, {hex + "\n", false}, {"\n" + hex, false}, {hex + " ", false}, {strings.ToUpper(hex), false}} {
		ref, err := json.Marshal(map[string]string{"type": "record", "digest_alg": "SHA-256", "digest": tc.digest})
		if err != nil {
			t.Fatal(err)
		}
		if _, ok := typedRefDigest(ref); ok != tc.ok {
			t.Errorf("typedRefDigest(%q): %v, want %v", tc.digest, ok, tc.ok)
		}
		block, err := json.Marshal(map[string]any{"fp_alg": profileFPAlg, "ids": map[string]string{"payee": tc.digest}})
		if err != nil {
			t.Fatal(err)
		}
		if _, ok := profileTarget(block); ok != tc.ok {
			t.Errorf("profileTarget(%q): %v, want %v", tc.digest, ok, tc.ok)
		}
	}
}
