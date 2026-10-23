package guard

import (
	"encoding/json"
	"strings"
	"testing"
)

// payeeTarget reads a counterparty block's members by their exact names,
// as the engine reads a dict (report/replay.py _sealed_counterparty and
// _payee_target): encoding/json would match a struct field to FP_ALG too
// (the Go port's exact-name fix). The engine's own decisions on such blocks are the
// record-*-case-* rows of target-parity.json.
func TestPayeeTargetReadsMembersByExactName(t *testing.T) {
	payee := strings.Repeat("9", 64)
	want := "payee-fp:hmac-sha256-deal-key:" + payee
	for name, c := range map[string]struct {
		block, target string
	}{
		"as sealed":             {`{"fp_alg":"hmac-sha256-deal-key","ids":{"payee":"` + payee + `"}}`, want},
		"upper-case members":    {`{"FP_ALG":"hmac-sha256-deal-key","IDS":{"payee":"` + payee + `"}}`, ""},
		"mixed-case members":    {`{"Fp_Alg":"hmac-sha256-deal-key","Ids":{"payee":"` + payee + `"}}`, ""},
		"upper-case fp_alg":     {`{"FP_ALG":"hmac-sha256-deal-key","ids":{"payee":"` + payee + `"}}`, ""},
		"upper-case ids":        {`{"fp_alg":"hmac-sha256-deal-key","IDS":{"payee":"` + payee + `"}}`, ""},
		"upper-case payee kind": {`{"fp_alg":"hmac-sha256-deal-key","ids":{"PAYEE":"` + payee + `"}}`, ""},
		"both spellings":        {`{"FP_ALG":"other","fp_alg":"hmac-sha256-deal-key","IDS":{},"ids":{"payee":"` + payee + `"}}`, want},
		"empty fp_alg":          {`{"fp_alg":"","ids":{"payee":"` + payee + `"}}`, ""},
		"fp_alg not a string":   {`{"fp_alg":7,"ids":{"payee":"` + payee + `"}}`, ""},
		"empty payee":           {`{"fp_alg":"hmac-sha256-deal-key","ids":{"payee":""}}`, ""},
		"payee not a string":    {`{"fp_alg":"hmac-sha256-deal-key","ids":{"payee":7}}`, ""},
		"not an object":         {`["hmac-sha256-deal-key"]`, ""},
		"null":                  {`null`, ""},
	} {
		got, ok := payeeTarget(json.RawMessage(c.block))
		if got != c.target || ok != (c.target != "") {
			t.Errorf("%s: target %q %v, want %q", name, got, ok, c.target)
		}
	}
}
