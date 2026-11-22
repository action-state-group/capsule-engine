// SPDX-License-Identifier: Apache-2.0

package referee

import (
	"github.com/action-state-group/agent-action-capsule/go/evidenceresult"
)

// checkResult is AAC's gate for an Evidence Result v0 document,
// agent-action-capsule/go/evidenceresult.Check: the schema, then the
// cross-element rules (unique claim ids, buckets, coverage counts, a
// coverage_report's rows). AAC owns that schema; there is no copy of it here
// (capsule-engine#318). A receiver holds no sidecar of cited records, so a close
// or calibration claim, which Check recomputes from the records it cites, is
// refused: the referee's judge emits requirement claims only.
func checkResult(doc any) error {
	d, ok := doc.(object)
	if !ok {
		return refuse(CodeResultInvalid, "the Result is not an object")
	}
	_, err := evidenceresult.Check(d, nil)
	return err
}
