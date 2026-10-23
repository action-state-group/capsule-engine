package guard

import (
	"encoding/json"
	"maps"
	"slices"
	"strconv"
)

// A thread copy's report, in either form capsulectl writes (capsule-engine
// #206, 0b9a293: report/sale_bundle.py _thread_report, _standalone_report
// and _last_step_key).
//
// Since capsule-cli #299 (e8a00b5), capsulectl seals a shelf deal's copy
// report off its log: the copy's x-deal-v0 section only points at it
// (sealed_report), and x-deal-report/v0 carries the report's record and
// agent_input, bound to the copy's checkpoint. Older copies seal the report
// in their own log. A copy that carries x-deal-report/v0 is read through it
// alone, never through an in-log report.
//
// A standalone report holds only when every check capsulectl's verify
// --bundle makes of it holds (DealReportEntry and standaloneReportEntry,
// capsule-cli internal/cli at 6210b93). The structural checks are made
// here. The two cryptographic ones, the record's signature under its key_id
// and the Capsule's disclosure check, are the host verifier's, as for every
// other record this package reads: a carried thread whose report fails them
// is not VALID (SaleVerification.Threads).

const (
	// standaloneReport is the extension that carries a copy's report off its log.
	standaloneReport = "x-deal-report/v0"
	// reportActionID is the action_id of a deal report's record.
	reportActionID = "capsulectl-deal-report"
	// sealedReportUnverified names a standalone report that does not hold.
	sealedReportUnverified = "sealed_report_unverified"
)

// stepRecordTypes are the evidence record header types of a deal's steps
// (capsulectl's DealLastStepKey).
var stepRecordTypes = []string{"deal_step", "step_completed", "step_failed"}

// engineCopyAudiences are the audiences of a copy's report the engine reads
// (report/sale_bundle.py _COPY_AUDIENCES). A caller may read fewer
// (SaleVerification.CopyAudiences); a copy for any other audience reads
// here as a report that does not hold, which fails closed.
var engineCopyAudiences = []string{"counterparty", "adjudicator", "keep"}

// threadReport is the report a thread's copy carries (_thread_report), and
// whether it is a standalone report that does not hold. Nil when there is
// no report to read.
func threadReport(threadBundle jsonObject, records []jsonObject, shown map[string]json.RawMessage, audiences []string) (jsonObject, bool) {
	extensions := objectOf(threadBundle["extensions"])
	if _, ok := extensions[standaloneReport]; ok {
		report := readStandaloneReport(threadBundle, extensions, records, audiences)
		return report, report == nil
	}
	return inLogReport(extensions, records, shown), false
}

// inLogReport is the report a thread's copy seals in its own log: the bound
// record its x-deal-v0 section names as sealed_report. Nil when there is
// none.
func inLogReport(extensions jsonObject, records []jsonObject, shown map[string]json.RawMessage) jsonObject {
	pointer, ok := textOf(objectOf(extensions[dealProfile])[sealedReport])
	if !ok {
		return nil
	}
	i := slices.IndexFunc(records, func(r jsonObject) bool { id, _ := textOf(r["capsule_id"]); return id == pointer })
	report, disclosed := shown[pointer]
	if i < 0 || !disclosed || !isBound(records[i], report) {
		return nil
	}
	return objectOf(objectOf(report)["report"])
}

// readStandaloneReport is a copy's standalone report, or nil when it does
// not hold. Each numbered step is standaloneReportEntry's.
func readStandaloneReport(threadBundle, extensions jsonObject, records []jsonObject, audiences []string) jsonObject {
	section := objectOf(extensions[dealProfile])
	carried := objectOf(extensions[standaloneReport])
	pointer, ok := stringOf(section[sealedReport])
	if section == nil || len(section) != 1 || !ok || carried == nil {
		return nil
	}
	// 1. Its record is the pointer's deal report.
	record, payload := objectOf(carried["record"]), objectOf(carried["agent_input"])
	id, okID := stringOf(record["capsule_id"])
	action, _ := stringOf(record["action_id"])
	if len(carried) != 2 || record == nil || payload == nil || !okID || id != pointer || action != reportActionID {
		return nil
	}
	// 2. It is signed with the key of the deal's last step (the signature
	// itself is the host verifier's).
	keyID, ok := textOf(record["key_id"])
	if last, found := lastStepKey(threadBundle); !ok || !found || keyID != last {
		return nil
	}
	// 3. Its agent_input is what the Capsule commits to (the Capsule's own
	// check is the host verifier's).
	if !isBound(record, carried["agent_input"]) {
		return nil
	}
	// 4. It is a deal_report of the bundle's deal, for a copy audience.
	logID, _ := stringOf(objectOf(threadBundle["completeness_certificate"])["log_id"])
	deal, isDeal := cutPrefix(logID, "deal/")
	kind, _ := stringOf(payload["type"])
	profile, _ := stringOf(payload["profile"])
	dealID, okDeal := stringOf(payload["deal_id"])
	audience, okAudience := stringOf(payload["audience"])
	if kind != "deal_report" || profile != dealProfile || !isDeal || !okDeal || dealID != deal ||
		!okAudience || !slices.Contains(audiences, audience) {
		return nil
	}
	// 5. Its deal_checkpoint is the bundle's checkpoint: log id, MMR size and root.
	checkpoint := objectOf(threadBundle["checkpoint"])
	bound := objectOf(payload["deal_checkpoint"])
	boundLog, okLog := stringOf(bound["log_id"])
	size, okSize := naturalOf(bound["mmr_size"])
	signedSize, okSigned := naturalOf(checkpoint["mmr_size"])
	if bound == nil || !slices.Equal(slices.Sorted(maps.Keys(bound)), []string{"log_id", "mmr_size", "root"}) ||
		!okLog || boundLog != logID || !okSize || !okSigned || size != signedSize ||
		jcsText(bound["root"]) != jcsText(checkpoint["root"]) {
		return nil
	}
	// 6. The bundle holds no deal report record.
	if slices.ContainsFunc(records, func(r jsonObject) bool { a, _ := stringOf(r["action_id"]); return a == reportActionID }) {
		return nil
	}
	// 7. Its lifecycle's as_of is the checkpoint's time, as RFC3339Nano
	// instants (capsulectl's comparison).
	report := objectOf(payload["report"])
	asOf, okAsOf := stringOf(objectOf(report["lifecycle"])["as_of"])
	signed, okTime := stringOf(checkpoint["timestamp"])
	if !okAsOf || !okTime {
		return nil
	}
	at, errAt := parseInstant(asOf)
	signedAt, errSigned := parseInstant(signed)
	if errAt != nil || errSigned != nil || !at.Equal(signedAt) {
		return nil
	}
	return report
}

// lastStepKey is the key_id of a deal's last step in its copy (capsulectl's
// DealLastStepKey): the record at the highest log seq, by its completeness
// certificate's membership (a seq of at least 1), whose evidence record
// header is a step's, whatever the records' order. False when there is
// none or it names no key (capsulectl's verifiedProducerKey refuses an
// empty one).
func lastStepKey(threadBundle jsonObject) (string, bool) {
	memberships := objectOf(objectOf(threadBundle["completeness_certificate"])["memberships"])
	var last jsonObject
	var lastSeq int64
	for _, r := range recordsOf(threadBundle["records"]) {
		header, _ := stringOf(objectOf(r["evidence_record_header"])["record_type"])
		id, ok := stringOf(r["capsule_id"])
		if !ok || !slices.Contains(stepRecordTypes, header) {
			continue
		}
		seq, ok := naturalOf(objectOf(objectOf(memberships[id])["log_coordinates"])["seq"])
		if ok && seq > lastSeq {
			last, lastSeq = r, seq
		}
	}
	if last == nil {
		return "", false
	}
	return textOf(last["key_id"])
}

// naturalOf is raw as a non-negative JSON integer.
func naturalOf(raw json.RawMessage) (int64, bool) {
	n, err := strconv.ParseInt(string(raw), 10, 64)
	return n, err == nil && n >= 0
}

// cutPrefix is s without prefix, and whether there was a non-empty rest.
func cutPrefix(s, prefix string) (string, bool) {
	if len(s) <= len(prefix) || s[:len(prefix)] != prefix {
		return "", false
	}
	return s[len(prefix):], true
}
