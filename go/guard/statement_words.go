package guard

import (
	"encoding/json"
	"strings"
	"unicode"
)

// The words a represent check is about to say, which promise_never/1.1.0
// judges for a claim attributed to the user (capsule-engine 0c93a58,
// guards/checks/promise_never.py and report/replay.py). capsulectl seals the
// words by commitment (representing.text_commitment) and passes their
// opening beside a live check (the check input record's
// representing_opening). A bundle carries the opening in its deal report's
// representations, unless the copy withholds it.

// statementWords are the words beside one check: live, the check input's
// representing_opening (nil when none was given); on a replay, the opening
// the bundle's deal report carries for the check's commitment, or none,
// which notInBundle marks (promise_never.py WORDS_NOT_IN_BUNDLE).
type statementWords struct {
	opening     json.RawMessage
	notInBundle bool
}

// replayedWords are the words a replay gives a check: the opening among
// openings that recomputes its sealed commitment, or notInBundle.
func replayedWords(a Action, openings map[string]json.RawMessage) statementWords {
	if a.representationTextCommitment != nil {
		if opening, ok := openings[*a.representationTextCommitment]; ok {
			return statementWords{opening: opening}
		}
	}
	return statementWords{notInBundle: true}
}

// Where capsulectl puts a deal's report in a bundle (capsule-cli
// deal_sealed_report.go): inline as the x-deal-v0 extension itself,
// standalone in the x-deal-report/v0 extension, or in the log as the record
// the extension's sealed_report names.
const (
	dealReportExtension = "x-deal-report/v0"
	sealedReportPointer = "sealed_report"
)

// representingOpenings are the openings of the agent's statements a
// bundle's deal reports carry (their representations, each {nonce, text}),
// by the commitment each recomputes (replay.py representing_openings). An
// opening proves itself: the words that recompute a represent check's
// sealed commitment are the words it sealed. An entry whose nonce is not 64
// lowercase hex or whose text is not a string with JCS bytes is skipped;
// the first opening of a commitment is kept.
func representingOpenings(bundle jsonObject) map[string]json.RawMessage {
	openings := map[string]json.RawMessage{}
	for _, report := range bundleReports(bundle) {
		var said []json.RawMessage
		_ = json.Unmarshal(report["representations"], &said)
		for _, entry := range said {
			e := objectOf(entry)
			nonce, ok := stringOf(e["nonce"])
			text, isText := stringOf(e["text"])
			if !ok || !hex64.MatchString(nonce) || !isText || loneSurrogate(e["text"]) {
				continue
			}
			commitment, err := textCommitment(nonce, text)
			if err != nil {
				continue
			}
			if _, seen := openings[commitment]; seen {
				continue
			}
			raw, err := json.Marshal(struct {
				Nonce string `json:"nonce"`
				Text  string `json:"text"`
			}{nonce, text})
			if err != nil {
				continue
			}
			openings[commitment] = raw
		}
	}
	return openings
}

// bundleReports are the deal reports bundle carries, in replay.py
// _bundle_reports' order: the inline x-deal-v0 extension, the report in the
// log record its sealed_report names, then the standalone report. Nothing
// about them is trusted here.
func bundleReports(bundle jsonObject) []jsonObject {
	extensions := objectOf(bundle["extensions"])
	var reports []jsonObject
	if deal := objectOf(extensions[dealProfile]); deal != nil {
		reports = append(reports, deal)
		if pointer, ok := stringOf(deal[sealedReportPointer]); ok {
			shown := objectOf(objectOf(bundle["disclosures"])[pointer])
			reports = append(reports, objectOf(objectOf(shown["agent_input"])["report"]))
		}
	}
	standalone := objectOf(extensions[dealReportExtension])
	reports = append(reports, objectOf(objectOf(standalone["agent_input"])["report"]))
	return reports
}

// attributedTemplate is promise_never/1.1.0's attributed_template: the
// prefix and suffix the user's claim is said between, and the marks that
// end a sentence or a line.
type attributedTemplate struct {
	Prefix        string   `yaml:"prefix"`
	Suffix        string   `yaml:"suffix"`
	SentenceMarks []string `yaml:"sentence_marks"`
}

// fits is fits_attributed_template: text starts with the prefix and ends
// with the suffix, and the claim between them is not blank and holds no
// sentence mark. Python compares code points; a valid UTF-8 prefix and
// suffix fit text at the same boundaries in bytes.
func (t attributedTemplate) fits(text string) bool {
	if len(text) < len(t.Prefix)+len(t.Suffix) || !strings.HasPrefix(text, t.Prefix) || !strings.HasSuffix(text, t.Suffix) {
		return false
	}
	claim := text[len(t.Prefix) : len(text)-len(t.Suffix)]
	if strings.TrimFunc(claim, pythonSpace) == "" {
		return false
	}
	for _, mark := range t.SentenceMarks {
		if strings.Contains(claim, mark) {
			return false
		}
	}
	return true
}

// pythonSpace is Python's str.isspace for one code point: Go's
// unicode.IsSpace and the four information separators U+001C to U+001F,
// which Python counts as whitespace and Go does not.
func pythonSpace(r rune) bool {
	return unicode.IsSpace(r) || (r >= 0x1C && r <= 0x1F)
}
