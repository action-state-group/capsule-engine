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
// for most rules), so no shared vector holds it. Every check-input case of
// the shared vectors is reported here and held to its golden: a change in a
// verdict, a reason or a value shows in review as a change to this file.
func TestReportGoldens(t *testing.T) {
	engines := conformanceEngines(t)
	// golden is one case's report, with Refused, which the report's JSON
	// leaves out and a caller reads.
	type golden struct {
		Report  CheckReport `json:"report"`
		Refused bool        `json:"refused"`
	}
	reports := map[string]golden{}
	for name, s := range readConformanceSets(t) {
		for _, c := range s.Cases {
			if c.Kind != "check-input" {
				continue
			}
			raw, err := os.ReadFile(filepath.Join(conformanceRoot, filepath.FromSlash(c.Input)))
			if err != nil {
				t.Fatal(err)
			}
			a, h, err := DecodeCheckInput(raw)
			if err != nil {
				continue // refused at decode: no report (KNOWN-DIFFERENCES group 5)
			}
			e := engines[c.Pack]
			d, err := e.CheckWithHistory(a, h)
			if err != nil {
				continue
			}
			r := e.Report(a, d)
			reports[name+":"+c.Case] = golden{Report: r, Refused: r.Refused}
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
