package canon_test

import (
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// A JSON document digests as JCS over it: the expected value is sha256 over
// Python's json.dumps(sort_keys=True, separators=(",", ":"),
// ensure_ascii=False), which is JCS for strings, integers, booleans and null.
func TestFromJSONDigestsAsJCS(t *testing.T) {
	v, err := canon.FromJSON([]byte(`{"b": [1, true, null, "é"], "a": {"z": -3, "y": "x"}}`))
	if err != nil {
		t.Fatal(err)
	}
	got, err := canon.Digest(v)
	if err != nil {
		t.Fatal(err)
	}
	if want := "79ffbd09966fa56ba9bfb8808a72ba05b82db317304bd302f1f5c5a26d4a6240"; got != want {
		t.Fatalf("digest %s, want %s", got, want)
	}
}

// What JCS would format differently, or what is not one JSON object, is
// refused rather than digested.
func TestFromJSONRefuses(t *testing.T) {
	for _, doc := range []string{`{"a": 1.5}`, `{"a": 1e3}`, `{"a": 1, "a": 2}`, `{"a": 1} {}`, `{"a": 9223372036854775808}`} {
		if _, err := canon.FromJSON([]byte(doc)); err == nil {
			t.Errorf("accepted %s", doc)
		}
	}
}
