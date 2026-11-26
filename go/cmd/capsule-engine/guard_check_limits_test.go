package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/action-state-group/capsule-engine/go/guard"
	"github.com/action-state-group/capsule-engine/go/pack"
)

func writeLimits(t *testing.T, body string) string {
	t.Helper()
	f := filepath.Join(t.TempDir(), "limits.json")
	if err := os.WriteFile(f, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return f
}

const personProfile = `{"format":"policy-profile/v0","packs":[` +
	`{"pack":"asg/seller","parameters":{"caps":{"caps_minor":{"money.purchase":1}}}},` +
	`{"pack":"asg/everyday","parameters":{"caps":{"caps_minor":{"money.purchase":3000},"per_action_minor":{"money.purchase":1500}}}}]}`

// TestGuardCheckLimits: a person's limits tighten the pack's, the findings of
// a failing limit say " you set", and the result is the one a host gets that
// applies the same profile to the Go guard itself.
func TestGuardCheckLimits(t *testing.T) {
	in, err := os.ReadFile(filepath.Join(emitDir, "pay-second-in-week.json"))
	if err != nil {
		t.Fatal(err)
	}
	file := writeLimits(t, personProfile)
	code, out, stderr := runGuardCheck(t, in, "--limits", file)
	if code != 0 || stderr != "" {
		t.Fatalf("exit %d, stderr %q", code, stderr)
	}
	var r externalResult
	if err := json.Unmarshal([]byte(out), &r); err != nil {
		t.Fatal(err)
	}
	byID := map[string]externalFinding{}
	for _, f := range r.Findings {
		byID[f.ID] = f
	}
	per, win := byID["r05-spending-limits#per_purchase"], byID["r05-spending-limits#7d"]
	if per.Verdict != "fail" || *per.Limit != 1500 || !strings.HasSuffix(per.Reason, " you set") {
		t.Fatalf("per-purchase finding %+v", per)
	}
	if win.Verdict != "fail" || *win.Limit != 3000 || !strings.HasSuffix(win.Reason, " you set") {
		t.Fatalf("window finding %+v", win)
	}
	if r.Verdict == "allow" {
		t.Fatalf("verdict %s", r.Verdict)
	}

	// The same result from a host that applies the profile itself.
	a, h, err := guard.DecodeCheckInput(in)
	if err != nil {
		t.Fatal(err)
	}
	p, err := pack.LoadEveryday0310()
	if err != nil {
		t.Fatal(err)
	}
	e, err := guard.NewEngine(p)
	if err != nil {
		t.Fatal(err)
	}
	prof, err := guard.DecodeProfile([]byte(personProfile))
	if err != nil {
		t.Fatal(err)
	}
	e.WithOperatorLimits(hostLimits{prof.Packs["asg/everyday"].Caps, guard.LimitSource{Source: guard.LimitSourceProfile, ProfileDigest: prof.Digest}})
	e.WithoutApprovalRecords()
	d, err := e.CheckWithHistory(a, h)
	if err != nil {
		t.Fatal(err)
	}
	want, err := json.Marshal(externalResultOf(e.Report(a, d)))
	if err != nil {
		t.Fatal(err)
	}
	if out != string(want)+"\n" {
		t.Fatalf("differs from a host applying the profile:\n got  %s\n want %s", out, want)
	}
}

type hostLimits struct {
	caps   map[string]map[string]int64
	source guard.LimitSource
}

func (l hostLimits) InForce(key, class string, _ time.Time) (guard.LimitInForce, bool) {
	v, ok := l.caps[key][class]
	return guard.LimitInForce{ValueMinor: v, Source: l.source}, ok
}

// A profile with no entry for the pack changes nothing, and a run without
// --limits carries no " you set".
func TestGuardCheckLimitsOtherPackAndNone(t *testing.T) {
	in, err := os.ReadFile(filepath.Join(emitDir, "pay-second-in-week.json"))
	if err != nil {
		t.Fatal(err)
	}
	_, plain, _ := runGuardCheck(t, in)
	_, other, _ := runGuardCheck(t, in, "--limits", writeLimits(t, `{"format":"policy-profile/v0","packs":[{"pack":"asg/seller","parameters":{"caps":{"caps_minor":{"money.purchase":1}}}}]}`))
	if plain != other || strings.Contains(plain, "you set") {
		t.Fatalf("plain and other-pack results differ or say you set")
	}
}

func TestGuardCheckLimitsRefusals(t *testing.T) {
	in, err := os.ReadFile(filepath.Join(emitDir, "pay-no-history.json"))
	if err != nil {
		t.Fatal(err)
	}
	for name, c := range map[string]struct{ file, stderr string }{
		"missing":                            {filepath.Join(t.TempDir(), "none.json"), "cannot be read"},
		"not a profile":                      {writeLimits(t, `{"secret-7777":1}`), "not a valid policy-profile"},
		"class with no pack default":         {writeLimits(t, `{"format":"policy-profile/v0","packs":[{"pack":"asg/everyday","parameters":{"caps":{"per_action_minor":{"made.up.class":1}}}}]}`), "does not take"},
		"legacy name of an undeclared class": {writeLimits(t, `{"format":"policy-profile/v0","packs":[{"pack":"asg/everyday","parameters":{"caps":{"caps_minor":{"comms.external":1}}}}]}`), "does not take"},
		"unknown rate cap":                   {writeLimits(t, `{"format":"policy-profile/v0","packs":[{"pack":"asg/everyday","parameters":{"rate_cap":{"max_input_msat_per_million":1}}}]}`), "does not take"},
	} {
		t.Run(name, func(t *testing.T) {
			code, out, stderr := runGuardCheck(t, in, "--limits", c.file)
			if code != 2 || out != "" || !strings.Contains(stderr, c.stderr) || strings.Contains(stderr, "7777") {
				t.Fatalf("exit %d, stdout %q, stderr %q", code, out, stderr)
			}
		})
	}
}

// A failed person-set limit asks for the person's approval (escalate); it
// does not deny (operator-limits ruling u325a).
func TestGuardCheckLimitsEscalate(t *testing.T) {
	in, err := os.ReadFile(filepath.Join(emitDir, "pay-no-history.json"))
	if err != nil {
		t.Fatal(err)
	}
	file := writeLimits(t, `{"format":"policy-profile/v0","packs":[{"pack":"asg/everyday","parameters":{"caps":{"per_action_minor":{"money.purchase":100}}}}]}`)
	code, out, stderr := runGuardCheck(t, in, "--limits", file)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, stderr)
	}
	var r externalResult
	if err := json.Unmarshal([]byte(out), &r); err != nil {
		t.Fatal(err)
	}
	if r.Verdict != "escalate" || !strings.Contains(out, "per-purchase limit you set") {
		t.Fatalf("verdict %s: %s", r.Verdict, out)
	}
}
