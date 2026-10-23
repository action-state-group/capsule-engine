package guard

import "encoding/json"

// Replay is what a replay of a capsule bundle gives a check read from it
// beside its record (replay.py replay and _decide, capsule-engine #205):
// the task-authority record the bundle discloses under the digest the
// check cites, and the words its deal report opens for the check's sealed
// commitment, or none, which promise_never/1.1.0 reads as not in the bundle.
type Replay struct {
	taskAuthority json.RawMessage
	words         statementWords
}

// BundleReplay is the Replay of the record raw holds for a: from a bundle
// (records a list, disclosures an object, read by their exact member names
// as the engine reads them), its disclosed task-authority/v0 record whose
// digest a cites and the opening of a's words its deal reports carry. A
// single capsule gives neither: a replay finds the words only in a bundle.
func BundleReplay(raw []byte, a Action) Replay {
	top := objectOf(raw)
	var records []json.RawMessage
	if json.Unmarshal(top["records"], &records) != nil || records == nil || objectOf(top["disclosures"]) == nil {
		return Replay{words: statementWords{notInBundle: true}}
	}
	return Replay{taskAuthority: disclosedTaskAuthority(disclosedOf(top), a.TaskAuthorityRef),
		words: replayedWords(a, representingOpenings(top))}
}
