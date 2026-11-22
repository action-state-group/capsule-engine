// SPDX-License-Identifier: Apache-2.0

package referee

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"fmt"
	"sync"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

// The schema is the one capsule_engine.report.result validates against
// (python/capsule_engine/schemas/vendor/evidence-result-v0.json), copied
// because go:embed cannot reach outside the module; a test holds the copy
// byte-equal. The Go gate agent-action-capsule/go/evidenceresult is a later
// revision of that schema and decides some documents differently, so it is not
// used: a receiver must decide a Result as the frozen Python check does.
//
//go:embed evidence-result-v0.json
var schemaBytes []byte

const schemaID = "https://agentactioncapsule.org/schemas/evidence-result-v0.json"

var compiled struct {
	sync.Once
	schema *jsonschema.Schema
	err    error
}

func compiledSchema() (*jsonschema.Schema, error) {
	compiled.Do(func() {
		doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(schemaBytes))
		if err != nil {
			compiled.err = err
			return
		}
		c := jsonschema.NewCompiler()
		if err = c.AddResource(schemaID, doc); err != nil {
			compiled.err = err
			return
		}
		compiled.schema, compiled.err = c.Compile(schemaID)
	})
	return compiled.schema, compiled.err
}

// checkResult is validate_against_schema then verify_result.
func checkResult(doc any) error {
	schema, err := compiledSchema()
	if err != nil {
		return fmt.Errorf("compiling the Result schema: %w", err)
	}
	if err := schema.Validate(doc); err != nil {
		return err
	}
	return verifyResult(obj(doc))
}

func asFloat(v any) (float64, bool) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, false
	}
	f, err := n.Float64()
	return f, err == nil
}

func list(v any) []any {
	l, _ := v.([]any)
	return l
}

// verifyResult is Python verify_result: claim ids are unique; every bucket entry
// names a claim with the matching verdict and every claim is in a bucket; the
// two coverage counts recompute from the claims; a coverage_report verifies.
func verifyResult(doc object) error {
	claims := map[string]object{}
	order := []string{}
	for _, c := range list(doc["claims"]) {
		claim := obj(c)
		id, _ := claim["id"].(string)
		if _, dup := claims[id]; dup {
			return fmt.Errorf("claim id %q appears more than once", id)
		}
		claims[id] = claim
		order = append(order, id)
	}
	buckets := obj(obj(doc["aggregate"])["buckets"])
	bucketed := map[string]bool{}
	for _, verdict := range []string{"met", "not_met", "not_evaluable"} {
		for _, v := range list(buckets[verdict]) {
			id, _ := v.(string)
			claim, ok := claims[id]
			if !ok {
				return fmt.Errorf("bucket %q names claim id %q, which has no claim", verdict, id)
			}
			if claim["verdict"] != verdict {
				return fmt.Errorf("claim %q is in bucket %q but its own verdict is %v", id, verdict, claim["verdict"])
			}
		}
	}
	for _, ids := range buckets {
		for _, v := range list(ids) {
			id, _ := v.(string)
			bucketed[id] = true
		}
	}
	for _, id := range order {
		if !bucketed[id] {
			return fmt.Errorf("claim %q (verdict %v) is in no bucket", id, claims[id]["verdict"])
		}
	}
	if coverage, ok := obj(doc["aggregate"])["coverage"].(object); ok {
		unknown := 0
		for _, id := range order {
			if claims[id]["sufficiency"] == "UNKNOWN" {
				unknown++
			}
		}
		for _, c := range []struct {
			name  string
			count int
		}{{"evaluated_population", len(claims)}, {"unknown_count", unknown}} {
			if f, ok := asFloat(coverage[c.name]); !ok || f != float64(c.count) {
				return fmt.Errorf("coverage %s %v disagrees with the %d counted from claims", c.name, coverage[c.name], c.count)
			}
		}
	}
	if report, ok := doc["coverage_report"]; ok {
		return verifyCoverageReport(obj(report), claims)
	}
	return nil
}

var statusToSufficiency = map[string]string{
	"SATISFIED": "SATISFIED", "NOT_FOUND": "GAP", "INSUFFICIENT": "INSUFFICIENT", "UNKNOWN": "UNKNOWN",
}

func num(v any, def float64) float64 {
	if f, ok := asFloat(v); ok {
		return f
	}
	return def
}

// verifyCoverageReport is Python coverage.verify_coverage_report.
func verifyCoverageReport(doc object, claims map[string]object) error {
	bad := func(format string, args ...any) error { return fmt.Errorf("invalid_coverage_report: "+format, args...) }
	contractRef := doc["contract_ref"]
	var gapsTotal, noRemedy, satisfied, withGaps int
	rows := list(doc["requirements"])
	for _, r := range rows {
		row := obj(r)
		ref, status := row["requirement_ref"], row["status"]
		wantSuff, known := statusToSufficiency[fmt.Sprint(status)]
		if _, isStr := status.(string); !isStr || !known {
			return bad("requirement %v status %v is not a coverage status", ref, status)
		}
		if row["sufficiency"] != wantSuff {
			return bad("requirement %v status %v requires sufficiency %q, got %v", ref, status, wantSuff, row["sufficiency"])
		}
		for _, cid := range list(row["claim_ids"]) {
			id, _ := cid.(string)
			claim, ok := claims[id]
			if !ok {
				return bad("requirement %v names claim %q, which has no claim", ref, id)
			}
			if claim["requirement_ref"] != ref || claim["contract_ref"] != contractRef {
				return bad("claim %q is for %v/%v, not %v/%v", id, claim["contract_ref"], claim["requirement_ref"], contractRef, ref)
			}
		}
		distinct := map[any]bool{}
		for _, s := range list(row["sources"]) {
			src := obj(s)
			count := num(src["record_count"], -1)
			if num(src["contemporaneous_count"], 0)+num(src["backfilled_count"], 0) != count {
				return bad("requirement %v source %v: contemporaneous_count + backfilled_count != record_count", ref, src["source"])
			}
			evidence := list(src["evidence"])
			if float64(len(evidence)) != count {
				return bad("requirement %v source %v: evidence digests != record_count", ref, src["source"])
			}
			if (src["status"] == "NOT_FOUND") != (count == 0) {
				return bad("requirement %v source %v: NOT_FOUND exactly when record_count is 0", ref, src["source"])
			}
			for _, e := range evidence {
				distinct[obj(e)["digest"]] = true
			}
		}
		ind := obj(row["independence"])
		independent := num(ind["independent_producers"], 0)
		if independent+num(ind["correlated_records"], 0)+num(ind["unattributed_records"], 0) != float64(len(distinct)) {
			return bad("requirement %v: independent_producers + correlated_records + unattributed_records != %d distinct records", ref, len(distinct))
		}
		basis, _ := ind["producer_basis"].(string)
		if (basis != "key" && basis != "asserted" && basis != "none") || ((basis == "none") != (independent == 0)) {
			return bad("requirement %v: producer_basis %v disagrees with independent_producers", ref, ind["producer_basis"])
		}
		met := independent >= num(ind["required_producers"], 1)
		if ind["met"] != met {
			return bad("requirement %v: independence.met disagrees with its producer counts", ref)
		}
		gaps := list(row["gaps"])
		if status == "SATISFIED" && (len(gaps) > 0 || ind["met"] != true) {
			return bad("requirement %v is SATISFIED but has gaps or unmet independence", ref)
		}
		gapsTotal += len(gaps)
		for _, g := range gaps {
			if obj(g)["remedy"] == nil {
				noRemedy++
			}
		}
		if status == "SATISFIED" {
			satisfied++
		}
		if len(gaps) > 0 {
			withGaps++
		}
	}
	want := map[string]int{
		"requirements": len(rows), "satisfied": satisfied, "with_gaps": withGaps,
		"gaps": gapsTotal, "gaps_without_remedy": noRemedy,
	}
	summary := obj(doc["summary"])
	if len(summary) != len(want) {
		return bad("summary %v does not recompute", doc["summary"])
	}
	for k, v := range want {
		if f, ok := asFloat(summary[k]); !ok || f != float64(v) {
			return bad("summary %v does not recompute", doc["summary"])
		}
	}
	return nil
}
