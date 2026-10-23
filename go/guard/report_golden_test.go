package guard

import (
	"bytes"
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"sort"
	"testing"
)

// updateReportGoldens rewrites testdata/report-goldens.json from this port:
// go test ./guard -run TestReportGoldens -update-report-goldens.
var updateReportGoldens = flag.Bool("update-report-goldens", false, "rewrite testdata/report-goldens.json")

// The per-rule report's wording is this port's own (the engine keeps none
// for most rules), so no shared vector holds it. Every case of the shared
// vectors is reported here and held to its golden, one line per case (a
// refused one holds its refusal): a check-input case's report, and an
// action-ledger or sale-bundle case's reports, one per step it decides, each
// step decided as the conformance runner decides it. A change in a verdict,
// a reason, a value or a refusal shows in review as a change to this file.
func TestReportGoldens(t *testing.T) {
	engines := conformanceEngines(t)
	// golden is one case's report, with Refused, which the report's JSON
	// leaves out and a caller reads; a multi-step case holds one per step.
	// A case or step this port refuses has no report; its golden holds the
	// refusal, so one that starts or stops being refused shows in review.
	type golden struct {
		Error   string       `json:"error,omitempty"`
		Report  *CheckReport `json:"report,omitempty"`
		Refused bool         `json:"refused,omitempty"`
		// Steps is set for every multi-step case, [] when it decides no step.
		Steps *[]golden `json:"steps,omitempty"`
	}
	reportOf := func(e *Engine, a Action, d Decision) golden {
		r := e.Report(a, d)
		return golden{Report: &r, Refused: r.Refused}
	}
	stepsOf := func(e *Engine, steps []replayStep) golden {
		out := []golden{}
		for _, step := range steps {
			if step.decision.Error != "" {
				out = append(out, golden{Error: step.decision.Error})
				continue
			}
			out = append(out, reportOf(e, step.action, step.raw))
		}
		return golden{Steps: &out}
	}
	reports := map[string]golden{}
	for name, s := range readConformanceSets(t) {
		for _, c := range s.Cases {
			id := name + ":" + c.Case
			e, raw := caseInput(t, engines, c)
			switch c.Kind {
			case "check-input":
				a, h, err := DecodeCheckInput(raw)
				if err != nil {
					reports[id] = golden{Error: "decode: " + err.Error()}
					continue
				}
				d, err := e.CheckWithHistory(a, h)
				if err != nil {
					reports[id] = golden{Error: "check: " + err.Error()}
					continue
				}
				reports[id] = reportOf(e, a, d)
			case "action-ledger":
				steps, refused := ledgerSteps(t, e, c, raw)
				if refused != "" {
					reports[id] = golden{Error: refused}
					continue
				}
				reports[id] = stepsOf(e, steps)
			case "sale-bundle":
				sale, err := caseSale(t, c, raw)
				if err != nil {
					reports[id] = golden{Error: "decode: " + err.Error()}
					continue
				}
				reports[id] = stepsOf(e, replaySteps(t, e, sale, sealedIDs(c)))
			default:
				t.Fatalf("%s: unknown kind %q", id, c.Kind)
			}
		}
	}
	keys := make([]string, 0, len(reports))
	for k := range reports {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var buf bytes.Buffer
	buf.WriteString("{\n")
	for i, k := range keys {
		kj, _ := json.Marshal(k)
		vj, err := json.Marshal(reports[k])
		if err != nil {
			t.Fatal(err)
		}
		buf.Write(kj)
		buf.WriteString(": ")
		buf.Write(vj)
		if i < len(keys)-1 {
			buf.WriteString(",")
		}
		buf.WriteString("\n")
	}
	buf.WriteString("}\n")
	path := filepath.Join("testdata", "report-goldens.json")
	if *updateReportGoldens {
		if err := os.WriteFile(path, buf.Bytes(), 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	want, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(want, buf.Bytes()) {
		gotLines, wantLines := bytes.Split(buf.Bytes(), []byte("\n")), bytes.Split(want, []byte("\n"))
		for i := 0; i < len(gotLines) && i < len(wantLines); i++ {
			if !bytes.Equal(gotLines[i], wantLines[i]) {
				t.Fatalf("the reports differ from testdata/report-goldens.json at line %d:\n got  %.400s\n want %.400s\nrun with -update-report-goldens and review the change", i+1, gotLines[i], wantLines[i])
			}
		}
		t.Fatalf("the reports differ from testdata/report-goldens.json in length (%d lines, want %d)", len(gotLines), len(wantLines))
	}
}
