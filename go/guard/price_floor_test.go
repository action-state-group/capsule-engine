package guard

import (
	"testing"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// pythonJSON digests a document as agent_action_capsule's json_digest
// digests json.loads of it. The digests and refusals below were computed by
// that code (agent-action-capsule 0.7.0, Python 3.13).
func TestPythonJSONDigestsAsJSONLoads(t *testing.T) {
	for raw, want := range map[string]string{
		`{"a":1,"a":2}`: "7e8059f495589fcd981232cc11d00b00da3802c01d688fa1cf1f6bed6e5bb33c",
		`{"b":{"x":1,"x":"y"},"a":[1,{"k":true,"k":null}]}`: "0cd21e267a923a30049c189bec8e78e19542f70fabbee58ead25db1a0d4f7bd3",
		`"😀"`:       "7a0c50b92434b015545fe93ab723db2d4b2cdd14a441405624a9ce8be29f1d5a",
		`"\\ud800"`: "eb5c7a825b6ee1b0ea16c7246ddd460a5a9dbd69e612726591d50d5395e6c854",
		`-0`:        "5feceb66ffc86f38d952786c6d696c79c2dbc239dd4e91b46729d73a27fb57e9",
	} {
		v, err := pythonJSON([]byte(raw))
		if err != nil {
			t.Errorf("%s: %v", raw, err)
			continue
		}
		if got, err := canon.Digest(v); err != nil || got != want {
			t.Errorf("%s: %s %v, want %s", raw, got, err, want)
		}
	}
	for _, raw := range []string{`1.5`, `1e2`, `"\ud800"`, `"\udc00\ud800"`, `9007199254740992`, `NaN`} {
		v, err := pythonJSON([]byte(raw))
		if err == nil {
			if _, err = canon.Digest(v); err == nil {
				t.Errorf("%s has a digest; json_digest refuses it", raw)
			}
		}
	}
}
