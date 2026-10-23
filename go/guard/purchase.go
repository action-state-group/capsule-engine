package guard

import (
	"errors"
	"fmt"

	"github.com/action-state-group/capsule-engine/go/pack"
)

// PurchaseClass is the taxonomy class of a purchase: the class whose limits
// PurchaseLimits reads.
const PurchaseClass = "money.purchase"

// PurchaseLimit is the pack's two spending limits for a purchase, read from
// the caps definition it cites: the per-action limit (per_action_minor) and
// the window limit (caps_minor), in minor units, and the window's duration
// from the fold that caps reads ("7d"). The pack names no currency; caps
// compares integers and converts nothing.
type PurchaseLimit struct {
	PerActionMinor int64
	WindowMinor    int64
	WindowDuration string
}

// PurchaseLimits reads PurchaseLimit from p's caps config, through the
// taxonomy as the engine resolves it, never from a constant. A pack with no
// caps check, or whose caps config sets no limit for a purchase, is refused:
// a closing message must not state a limit the pack does not set.
func PurchaseLimits(p pack.Pack) (PurchaseLimit, error) {
	w, ok := p.Wicket("caps")
	if !ok {
		return PurchaseLimit{}, fmt.Errorf("pack %s cites no caps check, so it sets no spending limit", p.ID)
	}
	caps, err := decodeConfig[capsConfig](w)
	if err != nil {
		return PurchaseLimit{}, err
	}
	tax, err := engineTaxonomy()
	if err != nil {
		return PurchaseLimit{}, err
	}
	window, err := tax.resolveLimits(caps.CapsMinor)
	if err != nil {
		return PurchaseLimit{}, fmt.Errorf("caps_minor: %w", err)
	}
	perAction, err := tax.resolveLimits(caps.PerActionMinor)
	if err != nil {
		return PurchaseLimit{}, fmt.Errorf("per_action_minor: %w", err)
	}
	wl, okW := window[PurchaseClass]
	pl, okP := perAction[PurchaseClass]
	if !okW || !okP {
		return PurchaseLimit{}, fmt.Errorf("caps %s sets no %s limit in both caps_minor and per_action_minor", w.ID, PurchaseClass)
	}
	fold, ok := p.Fold(caps.FoldID)
	if !ok || fold.Digest != caps.FoldDigest || fold.WindowDuration == "" {
		return PurchaseLimit{}, errors.New("caps cites no fold with a window the pack resolves")
	}
	return PurchaseLimit{PerActionMinor: pl, WindowMinor: wl, WindowDuration: fold.WindowDuration}, nil
}

// notCheckedOnPurchases are the checks a purchase record cannot make fail,
// each with the input it cannot do without that the deal-record reader
// (DecodeRecord, and so check --record and check --emit) never takes from a
// record: missing, the check is not_evaluable or not applicable, never a
// fail. The v0.0.10 MANIFEST's honest limit, "Recurring charges and a change
// to a payee's account are not yet checked on purchases." (ruled),
// and credential_pattern, whose outgoing content no deal record carries (ruled:
// never claim a NEVER no purchase check can produce).
// TestNotCheckedOnPurchasesInputsAreNeverRead holds each entry to the reader:
// once DecodeRecord takes the input, the entry must go.
var notCheckedOnPurchases = map[string]string{
	"recurring_charge":             "recurrence",
	"counterparty_identity_change": "counterparty_account_ref",
	"credential_pattern":           "outgoing_content",
}

// NotCheckedOnPurchases are the rules of p whose check is one a purchase
// record cannot make fail (notCheckedOnPurchases), by rule id.
func NotCheckedOnPurchases(p pack.Pack) map[string]bool {
	out := map[string]bool{}
	for _, r := range p.Rules {
		if _, ok := notCheckedOnPurchases[r.Check]; ok {
			out[r.ID] = true
		}
	}
	return out
}
