package guard

import (
	"encoding/json"
	"errors"
	"fmt"
	"math/bits"
	"regexp"
	"slices"
	"time"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// A sale bundle, and the history a check of the sale is decided on
// (capsule-engine #146, c49a9258, #147, 7b2d0339, #148, a6405ea2, and
// #149, fd43d432: report/sale_bundle.py; AMENDMENTS 10 and 11).
//
// capsulectl's `deal sale bundle` writes the user's own copy of a sale: the
// Evidence Bundle of the sale's own log (its sale root, its one
// task-authority/v0, and one x-deal-v0 thread record per thread registered
// on it, body.thread_ref_commitment committing to the thread's deal id),
// each registered thread's own copy carried in the private x-deal-sale/v0
// extension ({"threads": {<thread deal id>: <evidence-bundle/v2>}}), and,
// in the x-deal-v0 section, sale_threads: one entry per registration,
// {registration, nonce, thread_id, member}, member present, missing or
// never_opened. The sale's log also seals one thread_opened per opened
// thread (its registration ref and a task_authority_commitment), and the
// bundle step seals each thread's report, then a sale_cut (a
// head_commitment per opened thread, by registration), then the sale's
// checkpoint. A present entry carries opened {nonce} and head {nonce,
// record_digest} (AMENDMENT 11).
//
// ReadSaleBundle reads it as read_sale_bundle does. Each link that does not
// hold is named in Findings and leaves the sale incomplete; no thread is
// skipped:
//
//   - the sale's bundle, and each carried thread's, verifies VALID as an
//     Evidence Bundle (SaleVerification: the host's verifier, which the
//     plugin runs as capsulectl verify --bundle);
//   - each is the whole of its own log, deal/<its id>, at its checkpoint:
//     its completeness certificate runs from the first record to the last
//     the checkpoint holds, and it carries that many records;
//   - every record of the sale's log is read: disclosed, bound, and
//     verified (its identity recomputes and its signature is not invalid,
//     SaleVerification.Records), so no registration is hidden and an
//     edited record re-bound to its capsule is never read; it holds its
//     sale record and exactly one task-authority/v0 of the sale;
//   - sale_threads is there, and the sale does not say its threads predate
//     registration;
//   - sale_threads has one entry per thread record, in seal order, each
//     naming that record by digest; a present or never_opened entry's nonce
//     and thread_id open that record's thread_ref_commitment, and no two
//     entries open one thread; a missing entry is a thread not shown;
//   - the threads x-deal-sale/v0 carries are exactly the present ones;
//   - each carried thread is the registered one: its checkpoint is signed
//     with the sale's checkpoint key and its records with keys the sale's
//     records name; its one task authority of deal thread_id names that
//     registration in its one registration ref; its sealed report's
//     sale_authority_opening opens that task authority's
//     sale_authority_commitment to the sale's key, the digest of the sale's
//     own task authority, and names that task authority by digest;
//   - the sale's log shows each carried thread opened and whole: exactly
//     one thread_opened names its registration, and opened.nonce opens it
//     to the digest of the thread's task authority; the latest sale_cut
//     (the bundle is its whole log at its checkpoint, so the latest at or
//     before it; none when a record it does not read comes after it) has a
//     head for it, and head opens it to record_digest, the
//     agent_input_digest of the capsule at the thread's last seq. A carried
//     copy cut short is not whole;
//   - a never_opened entry is named by no thread_opened and no head of that
//     cut. Every record of the sale's log is disclosed, so none is hidden.
//
// The sale is certified through the earliest of the signed checkpoints, the
// sale's and each carried thread's (CertifiedUntil).
//
// What this does not establish: every checkpoint is the producer's own and
// no witness receipt is read, so a producer holding the profile's keys can
// sign a log that leaves an act out; never_opened holds against the sale's
// log as its producer signed it, so a producer that never sealed a thread's
// thread_opened is that case; the absence of threads_predate_registration
// is the producer's word, but such a copy has no thread_opened or sale_cut
// for its present threads and is not complete; and the history holds the
// sale's registered threads only, where a live check is given every act of
// every deal on the profile from its last 31 days.

// SaleExtension is the private extension that carries a sale's threads.
const SaleExtension = "x-deal-sale/v0"

const (
	threadPresent     = "present"
	threadMissing     = "missing"
	threadNeverOpened = "never_opened"
	taskAuthorityType = "task-authority/v0"
	typedEvaluation   = "action-evaluation/v0"
	typedApproval     = "action-approval/v0"
	sealedReport      = "sealed_report"
	dealProfile       = "x-deal-v0"
)

var (
	dealIDPattern = regexp.MustCompile(`^deal-[0-9a-f]{16}$`)
	saleIDPattern = regexp.MustCompile(`^sale-[0-9a-f]{16}$`)
)

// SaleVerification is what the host's Evidence Bundle verifier established
// of a sale bundle, as check_evidence_file does for each bundle the engine
// reads: whether the sale's own bundle is VALID (every record's identity,
// signature and membership under its signed checkpoint, the bundle's JSON
// copy of the checkpoint equal to what was signed), which of the sale's
// own records verify, by capsule id (its identity recomputes and its
// signature is not invalid: check_evidence_file's identity_ok and
// signature), and which carried threads are VALID as their own Evidence
// Bundles. ReadSaleBundle reads each checkpoint's log, size, key and time
// from the bundle's JSON copy, which a VALID verdict holds equal to the
// signed one.
//
// CopyAudiences are the audiences of a carried thread's standalone report
// the caller reads; nil reads the engine's (engineCopyAudiences). A caller
// that offers fewer copy audiences passes only those, so a copy for any
// other reads as a report that does not hold.
type SaleVerification struct {
	Sale          bool
	Records       map[string]bool
	Threads       map[string]bool
	CopyAudiences []string
}

func (v SaleVerification) copyAudiences() []string {
	if v.CopyAudiences == nil {
		return engineCopyAudiences
	}
	return v.CopyAudiences
}

// SaleThread is a thread the sale bundle carries: its deal id, its bundle,
// its records in its bundle's order, and each record's disclosed
// agent_input by capsule id.
type SaleThread struct {
	ID        string
	bundle    json.RawMessage
	records   []jsonObject
	disclosed map[string]json.RawMessage
}

// SaleBundle is what ReadSaleBundle established. Key is the digest of the
// sale's own task authority, nil when there is not exactly one; Threads the
// carried threads, in registration order; CertifiedUntil the earliest
// signed checkpoint time; Findings each link that does not hold, never a
// value.
type SaleBundle struct {
	Key            *string
	Threads        []SaleThread
	CertifiedUntil *time.Time
	Findings       []string
}

// Complete is whether the replay holds every thread of the sale, each
// verified and bound to it.
func (s SaleBundle) Complete() bool { return len(s.Findings) == 0 }

// jsonObject is one decoded JSON object, its members undecoded.
type jsonObject map[string]json.RawMessage

// objectOf is raw as a JSON object, or nil when it is not one.
func objectOf(raw json.RawMessage) jsonObject {
	var o jsonObject
	if json.Unmarshal(raw, &o) != nil {
		return nil
	}
	return o
}

// textOf is raw when it is a non-empty JSON string (the bridge's _text).
func textOf(raw json.RawMessage) (string, bool) {
	var s string
	if json.Unmarshal(raw, &s) != nil || s == "" {
		return "", false
	}
	return s, true
}

// rawDigest is the lowercase hex SHA-256 of raw's JCS form (json_digest).
func rawDigest(raw json.RawMessage) (string, bool) {
	v, err := canon.FromJSON(raw)
	if err != nil {
		return "", false
	}
	d, err := canon.Digest(v)
	return d, err == nil
}

// openingDigest is the commitment a nonce and text open
// (sha256-jcs-nonce256: the digest of {"nonce", "text"}).
func openingDigest(nonce, text string) string {
	d, _ := canon.Digest(canon.Obj(canon.M("nonce", canon.Str(nonce)), canon.M("text", canon.Str(text))))
	return d
}

// IsSaleBundle is whether raw is a bundle that carries a sale's threads
// (its extensions name x-deal-sale/v0).
func IsSaleBundle(raw []byte) bool {
	_, ok := objectOf(objectOf(raw)["extensions"])[SaleExtension]
	return ok
}

// ReadSaleBundle reads raw as a sale bundle (the package comment above),
// with v what the host's verifier established of it. It returns an error
// only when raw is not a JSON object.
func ReadSaleBundle(raw []byte, v SaleVerification) (SaleBundle, error) {
	bundle := objectOf(raw)
	if bundle == nil {
		return SaleBundle{}, errors.New("record: the sale bundle is not a JSON object")
	}
	var findings []string
	if !v.Sale {
		findings = append(findings, "sale_bundle_not_valid")
	}
	disclosed := disclosedOf(bundle)
	records := recordsOf(bundle["records"])
	// A record is read only when its disclosure binds it and it verifies
	// (#149, as capsulectl's verifier reads it): an edited record re-bound
	// to its capsule is never read.
	var bound []jsonObject
	read := map[string]bool{}
	for _, r := range records {
		id, _ := textOf(r["capsule_id"])
		if ai, ok := disclosed[id]; ok && v.Records[id] && isBound(r, ai) {
			bound = append(bound, objectOf(ai))
			read[id] = true
		}
	}
	if len(bound) != len(records) {
		// A record of the sale's log it does not disclose may be a registration.
		findings = append(findings, "sale_record_not_disclosed")
	}
	var authorities []jsonObject
	isSale := false
	for _, s := range bound {
		if chain, _ := textOf(s["chain_id"]); typeOf(s) == taskAuthorityType && saleIDPattern.MatchString(chain) {
			authorities = append(authorities, s)
		}
		isSale = isSale || recordTypeOf(s) == "sale"
	}
	var key *string
	saleID := ""
	if len(authorities) == 1 {
		if d, ok := digestOf(authorities[0]); ok {
			key = &d
		}
		saleID, _ = textOf(authorities[0]["chain_id"])
	}
	if !isSale || key == nil {
		findings = append(findings, "not_a_sale_bundle")
	}
	checkpoint := objectOf(bundle["checkpoint"])
	if saleID == "" || !coversItsLog(bundle, checkpoint, saleID) {
		findings = append(findings, "sale_bundle_does_not_cover_its_log")
	}
	signers := recordKeys(records)
	var registrations []jsonObject
	for _, s := range bound {
		if recordTypeOf(s) == "thread" {
			registrations = append(registrations, s)
		}
	}
	slices.SortStableFunc(registrations, func(x, y jsonObject) int { return seqOf(x) - seqOf(y) })
	extensions := objectOf(bundle["extensions"])
	section := objectOf(extensions[dealProfile])
	carried := map[string]json.RawMessage{}
	_ = json.Unmarshal(objectOf(extensions[SaleExtension])["threads"], &carried)
	if string(section["threads_predate_registration"]) == "true" {
		findings = append(findings, "threads_predate_registration")
	}
	var listed []json.RawMessage
	if json.Unmarshal(section["sale_threads"], &listed) != nil || listed == nil {
		findings = append(findings, "no_sale_threads")
		listed = nil
	}
	log := saleLogOpenings(bound, lastUnreadSeq(bundle, records, read), &findings)
	present := presentThreads(listed, registrations, log, &findings)
	if !sameThreads(carried, present) {
		findings = append(findings, "carried_threads_are_not_the_present_ones")
	}
	var threads []SaleThread
	times := []*time.Time{checkpointTime(checkpoint)}
	for _, p := range present {
		threadBundle := objectOf(carried[p.thread])
		if threadBundle == nil {
			continue
		}
		if !v.Threads[p.thread] {
			findings = append(findings, "thread_not_valid")
		}
		threadCheckpoint := objectOf(threadBundle["checkpoint"])
		if !coversItsLog(threadBundle, threadCheckpoint, p.thread) {
			findings = append(findings, "thread_does_not_cover_its_log")
		}
		saleKeyID, ok := textOf(checkpoint["key_id"])
		threadKeyID, _ := textOf(threadCheckpoint["key_id"])
		held := recordsOf(threadBundle["records"])
		if !ok || threadKeyID != saleKeyID || !subset(recordKeys(held), signers) {
			findings = append(findings, "thread_not_signed_as_the_sale")
		}
		times = append(times, checkpointTime(threadCheckpoint))
		shown := disclosedOf(threadBundle)
		report, unverified := threadReport(threadBundle, held, shown, v.copyAudiences())
		if unverified {
			findings = append(findings, sealedReportUnverified)
		}
		if why, authority := threadBinding(held, shown, report, p.thread, p.registration, key); why != "" {
			findings = append(findings, why)
		} else {
			findings = append(findings, openedAndWhole(p.entry, p.registration, authority, threadBundle, log)...)
		}
		threads = append(threads, SaleThread{ID: p.thread, bundle: carried[p.thread], records: held, disclosed: shown})
	}
	var certified *time.Time
	for _, at := range times {
		if at == nil {
			certified = nil
			findings = append(findings, "checkpoint_time_not_read")
			break
		}
		if certified == nil || at.Before(*certified) {
			certified = at
		}
	}
	return SaleBundle{Key: key, Threads: threads, CertifiedUntil: certified, Findings: uniqueInOrder(findings)}, nil
}

// registered is one present thread: its deal id, the digest of its
// registration, and its sale_threads entry.
type registered struct {
	thread, registration string
	entry                jsonObject
}

// presentThreads are the present threads sale_threads lists, in
// registration order, each entry checked against the thread record in its
// place, and a never_opened one against the sale's log (_present), each
// failure appended to findings.
func presentThreads(listed []json.RawMessage, registrations []jsonObject, log saleLog, findings *[]string) []registered {
	if len(listed) != len(registrations) {
		*findings = append(*findings, "sale_threads_do_not_match_the_registrations")
	}
	var present []registered
	for i := range min(len(listed), len(registrations)) {
		entry := objectOf(listed[i])
		digest, _ := digestOf(registrations[i])
		if ref, ok := refDigest(entry["registration"]); !ok || ref != digest {
			*findings = append(*findings, "sale_threads_do_not_match_the_registrations")
			continue
		}
		member, _ := textOf(entry["member"])
		switch member {
		case threadMissing:
			*findings = append(*findings, "thread_not_shown")
			continue
		case threadPresent, threadNeverOpened:
		default:
			*findings = append(*findings, "unknown_thread_state")
			continue
		}
		nonce, okNonce := textOf(entry["nonce"])
		thread, okThread := textOf(entry["thread_id"])
		commitment, _ := textOf(objectOf(registrations[i]["body"])["thread_ref_commitment"])
		if !okNonce || !okThread || !dealIDPattern.MatchString(thread) ||
			slices.ContainsFunc(present, func(p registered) bool { return p.thread == thread }) ||
			openingDigest(nonce, thread) != commitment {
			*findings = append(*findings, "registration_opening_does_not_match")
			continue
		}
		switch _, inCut := log.cut[digest]; {
		case member == threadPresent:
			present = append(present, registered{thread: thread, registration: digest, entry: entry})
		case len(log.opened[digest]) > 0:
			*findings = append(*findings, "never_opened_but_opened")
		case log.hasCut && inCut:
			*findings = append(*findings, "never_opened_but_in_the_cut")
		}
	}
	return present
}

// sameThreads is whether the carried threads are exactly the present ones.
func sameThreads(carried map[string]json.RawMessage, present []registered) bool {
	if len(carried) != len(present) {
		return false
	}
	for _, p := range present {
		if _, ok := carried[p.thread]; !ok {
			return false
		}
	}
	return true
}

// threadBinding is why a carried thread is not the registered thread of
// this sale (_thread_binding), or "" when it is, with the digest of its
// task authority when it is. report is the thread's report (threadReport).
func threadBinding(records []jsonObject, shown map[string]json.RawMessage, report jsonObject, thread, registration string, key *string) (string, string) {
	var authorities []jsonObject
	for _, s := range boundRecords(records, shown) {
		if chain, _ := textOf(s["chain_id"]); typeOf(s) == taskAuthorityType && chain == thread {
			authorities = append(authorities, s)
		}
	}
	if len(authorities) != 1 {
		return "thread_has_no_task_authority_of_this_thread", ""
	}
	authority := authorities[0]
	if named := refsNamed(authority, "registration"); len(named) != 1 || !refIs(named[0], registration) {
		return "thread_does_not_name_its_registration", ""
	}
	opening := objectOf(report["sale_authority_opening"])
	nonce, okNonce := textOf(opening["nonce"])
	text, okText := textOf(opening["text"])
	recordDigest, _ := textOf(opening["record_digest"])
	authorityDigest, _ := digestOf(authority)
	commitment, _ := textOf(objectOf(authority["body"])["sale_authority_commitment"])
	if key == nil || !okNonce || !okText || text != *key || recordDigest != authorityDigest ||
		openingDigest(nonce, text) != commitment {
		return "thread_is_not_under_this_sale", ""
	}
	return "", authorityDigest
}

// saleLog is what the sale's log says of its threads (AMENDMENT 11): each
// thread_opened record's task_authority_commitment, by the digest of the
// registration it names, and whether the log holds a sale_cut it can read,
// with the head_commitment of each thread its latest cut names, by
// registration.
type saleLog struct {
	opened map[string][]string
	hasCut bool
	cut    map[string]string
}

// saleLogOpenings reads the sale's bound records as _sale_log_openings
// does. The bundle is the whole log at its certified checkpoint, so its
// latest cut is the latest one at or before it. A thread_opened that
// cannot be read is appended to findings: it may be the one that opens a
// thread. A latest cut that cannot be read (two at its seq, or a malformed
// or repeated head) is no cut, so each present thread names no_sale_cut
// (#148, one name with capsulectl's verifier). Only the latest cut counts:
// when a record the bundle does not read comes after it on the log
// (lastUnread, its seq there), that record may be a later cut, so there is
// no cut (#149).
func saleLogOpenings(bound []jsonObject, lastUnread *int, findings *[]string) saleLog {
	log := saleLog{opened: map[string][]string{}}
	var cuts []jsonObject
	for _, s := range bound {
		switch recordTypeOf(s) {
		case "thread_opened":
			var refs, named []json.RawMessage
			_ = json.Unmarshal(objectOf(s[dealProfile])["refs"], &refs)
			for _, raw := range refs {
				if objectOf(raw) != nil {
					named = append(named, raw)
				}
			}
			registration, okRegistration := "", false
			if len(named) == 1 {
				if rel, _ := textOf(objectOf(named[0])["rel"]); rel == "registration" {
					registration, okRegistration = refDigest(named[0])
				}
			}
			commitment, okCommitment := textOf(objectOf(s["body"])["task_authority_commitment"])
			if !okRegistration || !okCommitment {
				*findings = append(*findings, "thread_opened_not_read")
				continue
			}
			log.opened[registration] = append(log.opened[registration], commitment)
		case "sale_cut":
			cuts = append(cuts, s)
		}
	}
	if len(cuts) == 0 {
		return log
	}
	latest, ties := slices.MaxFunc(cuts, func(x, y jsonObject) int { return seqOf(x) - seqOf(y) }), 0
	if lastUnread != nil && *lastUnread > seqOf(latest) {
		return log
	}
	for _, c := range cuts {
		if seqOf(c) == seqOf(latest) {
			ties++
		}
	}
	if ties != 1 {
		return log
	}
	var heads []json.RawMessage
	if json.Unmarshal(objectOf(latest["body"])["thread_heads"], &heads) != nil || heads == nil {
		heads = []json.RawMessage{nil}
	}
	cut := map[string]string{}
	for _, raw := range heads {
		head := objectOf(raw)
		registration, okRegistration := refDigest(head["registration"])
		commitment, okCommitment := textOf(head["head_commitment"])
		if _, twice := cut[registration]; !okRegistration || !okCommitment || twice {
			return log
		}
		cut[registration] = commitment
	}
	log.hasCut, log.cut = true, cut
	return log
}

// lastUnreadSeq is the latest seq on the sale's log (its completeness
// certificate's membership log_coordinates.seq, an integer) of a record of
// the bundle that is not read, or nil when there is none.
func lastUnreadSeq(bundle jsonObject, records []jsonObject, read map[string]bool) *int {
	memberships := objectOf(objectOf(bundle["completeness_certificate"])["memberships"])
	var last *int
	for _, r := range records {
		id, _ := stringOf(r["capsule_id"])
		raw := objectOf(objectOf(memberships[id])["log_coordinates"])["seq"]
		var seq int
		if read[id] || !isInteger(raw) || json.Unmarshal(raw, &seq) != nil {
			continue
		}
		if last == nil || seq > *last {
			last = &seq
		}
	}
	return last
}

// openedAndWhole is why the sale's log does not show a present thread
// opened and whole (_opened_and_whole), or nil: exactly one thread_opened
// names its registration, and the entry's opened.nonce opens it to the
// digest of the thread's task authority; the latest sale_cut has a head for
// it, and the entry's head opens it to the digest of the carried thread's
// last record.
func openedAndWhole(entry jsonObject, registration, authority string, threadBundle jsonObject, log saleLog) []string {
	var why []string
	commitments := log.opened[registration]
	if len(commitments) != 1 {
		why = append(why, "opened_not_evidenced")
	} else if !opens(objectOf(entry["opened"])["nonce"], authority, commitments[0]) {
		why = append(why, "opening_does_not_match_the_thread")
	}
	headCommitment, inCut := log.cut[registration]
	switch {
	case !log.hasCut:
		why = append(why, "no_sale_cut")
	case !inCut:
		why = append(why, "thread_not_in_the_cut")
	default:
		head := objectOf(entry["head"])
		digest, ok := textOf(head["record_digest"])
		last, okLast := lastRecordDigest(threadBundle)
		if !ok || !okLast || digest != last || !opens(head["nonce"], digest, headCommitment) {
			why = append(why, "thread_not_whole_at_the_cut")
		}
	}
	return why
}

// opens is whether nonce, a JSON string, with text recomputes to
// commitment (_opens).
func opens(nonce json.RawMessage, text, commitment string) bool {
	s, ok := stringOf(nonce)
	return ok && openingDigest(s, text) == commitment
}

// stringOf is raw when it is a JSON string, empty or not.
func stringOf(raw json.RawMessage) (string, bool) {
	var s string
	if len(raw) == 0 || raw[0] != '"' || json.Unmarshal(raw, &s) != nil {
		return "", false
	}
	return s, true
}

// lastRecordDigest is the agent_input_digest the capsule at a thread
// copy's completeness certificate last_seq seals: the digest of the
// thread's last record, disclosed or not (_last_record_digest). False when
// no one capsule is there. A membership's seq and last_seq compare as JSON
// values (JCS text; an absent one is null).
func lastRecordDigest(threadBundle jsonObject) (string, bool) {
	certificate := objectOf(threadBundle["completeness_certificate"])
	last := jcsText(certificate["last_seq"])
	var at []string
	for id, m := range objectOf(certificate["memberships"]) {
		if coordinates := objectOf(objectOf(m)["log_coordinates"]); coordinates != nil && jcsText(coordinates["seq"]) == last {
			at = append(at, id)
		}
	}
	var held []jsonObject
	for _, r := range recordsOf(threadBundle["records"]) {
		if id, ok := stringOf(r["capsule_id"]); ok && slices.Contains(at, id) {
			held = append(held, r)
		}
	}
	if len(at) != 1 || len(held) != 1 {
		return "", false
	}
	return textOf(objectOf(objectOf(held[0]["model_attestation"])["compute_attestation"])["agent_input_digest"])
}

// jcsText is raw's JCS text, "null" when raw is absent, or raw itself when
// it is not JSON.
func jcsText(raw json.RawMessage) string {
	if raw == nil {
		return "null"
	}
	v, err := canon.FromJSON(raw)
	if err != nil {
		return string(raw)
	}
	text, err := canon.JCS(v)
	if err != nil {
		return string(raw)
	}
	return text
}

// coversItsLog is whether bundle is the whole log of deal at its
// checkpoint: the checkpoint's log and its completeness certificate's are
// deal/<deal>, the certificate runs from the first record to the last the
// checkpoint holds (its mmr_size read as a leaf count), and the bundle
// carries that many records.
func coversItsLog(bundle, checkpoint jsonObject, deal string) bool {
	log := "deal/" + deal
	certificate := objectOf(bundle["completeness_certificate"])
	var records []json.RawMessage
	if certificate == nil || json.Unmarshal(bundle["records"], &records) != nil || records == nil {
		return false
	}
	checkpointLog, _ := textOf(checkpoint["log_id"])
	certificateLog, _ := textOf(certificate["log_id"])
	leaves, ok := leavesOf(checkpoint["mmr_size"])
	return ok && checkpointLog == log && certificateLog == log && string(certificate["first_seq"]) == "1" &&
		isInteger(certificate["last_seq"]) && string(certificate["last_seq"]) == fmt.Sprint(leaves) && len(records) == leaves
}

// leavesOf is the number of leaves of a Merkle mountain range of raw nodes
// (2n - popcount(n) nodes hold n leaves), or false when raw is not a
// positive integer or no leaf count gives that size.
func leavesOf(raw json.RawMessage) (int, bool) {
	var size int
	if !isInteger(raw) || json.Unmarshal(raw, &size) != nil || size < 1 {
		return 0, false
	}
	for n := size / 2; n <= size; n++ {
		if 2*n-bits.OnesCount(uint(n)) == size {
			return n, true
		}
	}
	return 0, false
}

// checkpointTime is the time a checkpoint states, or nil.
func checkpointTime(checkpoint jsonObject) *time.Time {
	s, ok := textOf(checkpoint["timestamp"])
	if !ok {
		return nil
	}
	at, err := parseInstant(s)
	if err != nil {
		return nil
	}
	return &at
}

// recordKeys are the key_id every record names, as JCS text (a record
// naming none names null).
func recordKeys(records []jsonObject) map[string]bool {
	out := map[string]bool{}
	for _, r := range records {
		k := json.RawMessage("null")
		if raw, ok := r["key_id"]; ok {
			k = raw
		}
		v, err := canon.FromJSON(k)
		if err != nil {
			out[string(k)] = true
			continue
		}
		text, _ := canon.JCS(v)
		out[text] = true
	}
	return out
}

func subset(xs, of map[string]bool) bool {
	for x := range xs {
		if !of[x] {
			return false
		}
	}
	return true
}

// recordsOf are the JSON objects among a bundle's records, in order.
func recordsOf(raw json.RawMessage) []jsonObject {
	var items []json.RawMessage
	_ = json.Unmarshal(raw, &items)
	var out []jsonObject
	for _, item := range items {
		if o := objectOf(item); o != nil {
			out = append(out, o)
		}
	}
	return out
}

// disclosedOf is a bundle's disclosed agent_input objects, by capsule id.
func disclosedOf(bundle jsonObject) map[string]json.RawMessage {
	members := map[string]json.RawMessage{}
	_ = json.Unmarshal(bundle["disclosures"], &members)
	out := map[string]json.RawMessage{}
	for id, member := range members {
		if ai, ok := objectOf(member)["agent_input"]; ok && objectOf(ai) != nil {
			out[id] = ai
		}
	}
	return out
}

// boundRecords are the disclosed agent_input objects the records bind, in
// the records' order.
func boundRecords(records []jsonObject, disclosed map[string]json.RawMessage) []jsonObject {
	var out []jsonObject
	for _, r := range records {
		id, _ := textOf(r["capsule_id"])
		if ai, ok := disclosed[id]; ok && isBound(r, ai) {
			out = append(out, objectOf(ai))
		}
	}
	return out
}

// isBound is whether agentInput is what record's capsule sealed (_bound).
func isBound(record jsonObject, agentInput json.RawMessage) bool {
	return checkAgentInputDigest(record, agentInput) == nil
}

func typeOf(s jsonObject) string {
	t, _ := textOf(s["type"])
	return t
}

func recordTypeOf(s jsonObject) string {
	t, _ := textOf(objectOf(s[dealProfile])["record_type"])
	return t
}

// seqOf is a record's x-deal-v0.seq, or -1.
func seqOf(s jsonObject) int {
	raw := objectOf(s[dealProfile])["seq"]
	var n int
	if !isInteger(raw) || json.Unmarshal(raw, &n) != nil {
		return -1
	}
	return n
}

// digestOf is the JCS digest of a decoded object.
func digestOf(o jsonObject) (string, bool) {
	raw, err := json.Marshal(o)
	if err != nil {
		return "", false
	}
	return rawDigest(raw)
}

// refsNamed are a typed record's refs whose rel is rel.
func refsNamed(s jsonObject, rel string) []jsonObject {
	var refs []json.RawMessage
	_ = json.Unmarshal(s["refs"], &refs)
	var out []jsonObject
	for _, raw := range refs {
		if r := objectOf(raw); r != nil {
			if got, _ := textOf(r["rel"]); got == rel {
				out = append(out, r)
			}
		}
	}
	return out
}

// refDigest is the digest a typed reference names, when it is a SHA-256
// one (_typed_ref_digest).
func refDigest(raw json.RawMessage) (string, bool) {
	d, ok := typedRefDigest(raw)
	if !ok {
		return "", false
	}
	var s string
	return s, json.Unmarshal(d, &s) == nil
}

// refIs is whether a typed reference names digest.
func refIs(ref jsonObject, digest string) bool {
	raw, err := json.Marshal(ref)
	if err != nil {
		return false
	}
	got, ok := refDigest(raw)
	return ok && got == digest
}

func uniqueInOrder(xs []string) []string {
	var out []string
	for _, x := range xs {
		if !slices.Contains(out, x) {
			out = append(out, x)
		}
	}
	return out
}
