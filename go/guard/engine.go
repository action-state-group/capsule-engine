package guard

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"time"

	"gopkg.in/yaml.v3"

	"github.com/action-state-group/capsule-engine/go/canon"
	"github.com/action-state-group/capsule-engine/go/pack"
)

// portedDefinitions are the exact definitions this port implements, by id and
// digest (everyday fixtures gold_labels.yaml config_identity; the fold from
// the pack). A pack citing any other version of one of them is refused: a
// changed definition may mean changed semantics, and the port must be
// re-checked against it first.
//
// asg/everyday/0.3.4 (capsule-engine #121's merge, 97a2aff) cites
// caps/5.0.0 over spend.weekly/3.0.0, counterparty_seen_before/3.0.0 over
// counterparty.seen_before/3.0.0 (the only change from 0.3.3: the fold also
// counts a carried_out record), and channel_change, material_fields_changed,
// offer_fields_changed, refundability, task_authority and upfront_amount at
// 1.1.0: each is 1.0.0's config with marketplace.sale added to
// action_classes, so the same Go check reads it (sale_coverage_test.go holds
// them to the engine's decisions). counterparty.seen_before/2.0.0 is not
// cited; recipient_seen_before/1.0.0 still names it, by digest, in its
// config and reads it from the fold catalog (newSeenBefore).
//
// asg/everyday/0.3.5 (capsule-engine #135's merge, c878edf0) cites
// counterparty_seen_before/4.0.0 in place of 3.0.0: the same fold, with
// missing_target: unseen (newSeenBefore). Everything else is 0.3.4's, and
// 0.3.4 stays vendored, so 3.0.0 stays ported.
//
// asg/everyday/0.3.6 (capsule-engine #138's merge, a710636c) cites
// caps/5.1.0 over spend.weekly/3.1.0 in place of caps/5.0.0 over
// spend.weekly/3.0.0: the same limits and per-action reading, with a fold
// that also admits the engine's own record of an executed act. This port
// writes no such record; checkCaps counts the executed acts outside the fold
// under either, and under a fold that admits them (fold.countsExecuted) by
// the engine's rules for them (outsideTheFold), held to the engine by the
// G3 and executed-acts-036 vectors. 0.3.5 stays vendored, so 5.0.0 and 3.0.0
// stay ported.
var portedDefinitions = map[string]string{
	"caps/5.0.0":                         "2807e174dc7c817917621f90a53f3fa54992b76fe3ec28e8567f814b9e72a741",
	"caps/5.1.0":                         "7fd8def459eb42af32878108626748a76faea2bdf4f402530445f807ac5df2a8",
	"dedupe/1.0.0":                       "18ab5d489f1e5774d576b8f99897edd4f4b20f609b85683456a3e3b6b4912abb",
	"destination_rail/1.0.0":             "4d76251eba138b37e6be3e83390072315ebcea42cd8d790f7e9765ecf1f63429",
	"counterparty_identity_change/1.0.0": "0cfb1cb380b355abb88ea385334f3bb31f2e145269a2768022f85005665fc6fe",
	"credential_pattern/1.0.0":           "3617fdcaa39c03cf70a3254490328ccee5eaac0faba7cd52d9e3dc3fc50336c0",
	"recurring_charge/1.0.0":             "ea4ae204e5dc33f3a28e5e15471f404f20d4f8fca193d355a099a4a7e08b082e",
	"action_class_gate/1.0.0":            "fcc352838832e13f5e87662f0194ec4a7065bc989bbe624b43ac5d9d1b638ae2",
	"counterparty_seen_before/3.0.0":     "9cfab71d142ca7af8912218f4c4ea194f60fb02784d8536233cb7e18eddc24a8",
	"counterparty_seen_before/4.0.0":     "c7c20d9e57cb0a75184b9c6c4d037c9bea813a7b1105c581defd4481acf964b0",
	"recipient_seen_before/1.0.0":        "df8803e9c4bdab9deff48e772fcd21ff813d00157068dd250a8a5c6b45cd6c44",
	"counterparty.seen_before/3.0.0":     "73dbf8cd8a36fadca0ac823d2b17592fdc457c24081bc44667f032601e914033",
	"spend.weekly/3.0.0":                 "25907741320624a830c5854fc7f2d434bfa17528c8bfc17f4a8a4f84a39f1b18",
	"spend.weekly/3.1.0":                 "f379cd4b2b12284fa5d682845f23bcd7ab1ae8b8d1dd827cd9b5dd7069735997",
	"recipient_role/1.0.0":               "917ab5edea21867f318b8dfd54a6d7b458cbabcc5cebfac6ff0c4861e918d43b",
	"channel_change/1.1.0":               "f84770c7571a0932629667c4df38f447777f8dc5dfcdfdf29b37514c1eee1ad1",
	"material_fields_changed/1.1.0":      "e8cd02743203d17cd0de9d548f85ec5b6b6135e80aba63136ae507ac84660042",
	"offer_fields_changed/1.1.0":         "f62c034140827beaa4b8ee13da8b56e4583994a973bc789d4a11812f9727a953",
	"refundability/1.1.0":                "1a471c09254a3db872c34a5e59d9e05e6da47cb396695a6cb6bf2bc055f4d811",
	"upfront_amount/1.1.0":               "2311833db12bf9465dcb5ecdd9f7aca079dbf638a19ed6dd18d607aad1811c10",
	"task_authority/1.1.0":               "5d4e54d3a243be2cde9e64c99c1ce351b0dc04e46ed80aaadc2ba147fa9960ab",
	// asg/seller/0.1.0 (capsule-engine #122's merge, ba76a97) cites these
	// beside the everyday definitions it shares. seller.recipient_role
	// is recipient_role/1.0.0's check with the seller's closed set (buyer,
	// third_party, self): capsule-engine d8373fb changed only the check's
	// docstring. verify_before_dispatch is the check the engine always runs
	// (checkVerifyBeforeDispatch); the seller pack cites it for its rule s12.
	"seller.recipient_role/1.0.0":  "001d4d4198921ffec5f492f88286765a075eece1418d15b68576d4fceec7826b",
	"verify_before_dispatch/1.0.0": "a721624813f785de49f3dcef2090662e7045bc393e59db72defcdbf47269453c",
	// caps/1.0.0 over spend.weekly/1.0.0, the seller pack's spending limit:
	// the engine runs one caps check for every caps definition (guards/
	// checks/caps.py), so this is checkCaps over caps/1.0.0's config (a
	// money.transfer limit, no per-action limit) and the generic fold over
	// spend.weekly/1.0.0 (every accepted record with an amount, dry runs
	// included, keyed by developer). seller_s1_test.go holds it to the engine.
	"caps/1.0.0":         "906a75a0b908d38fa7b05823ba11f229c3d593516119ad757b541cee7083f54b",
	"spend.weekly/1.0.0": "3e7f6a3e8707de92b120bbc2aa0b5a78032df5d36e99f9955dd3ba948bba5e9c",
	// The seller pack's own checks, each held to the engine by
	// seller_s1_test.go: offer_expiry (checkOfferExpiry), price_floor
	// (checkPriceFloor; 2.0.1 is 2.0.0's re-pin at capsule-engine #118).
	"offer_expiry/1.0.1": "7bcd363b28475e3fac123fe2b8925f60f36bdaa3d22ebd5004ffe5290c4f2597",
	"price_floor/2.0.1":  "c39f858b52a9697a70450680d90d0bbd0cb4e4eb79925d18e67623d587404977",
	// and single_commitment (checkSingleCommitment).
	"seller.single_commitment/1.0.0": "91960c9a9d62b8f15778b6b626a0f0305fa795ab88dd11bd93101294fc56882e",
	// asg/seller/0.1.1 (capsule-engine 21975ce) adds release_on_acceptance
	// (checkReleaseOnAcceptance; release_test.go holds it to the engine).
	"seller.release_on_acceptance/1.0.0": "6a8487fb059f171765810bdc4a248edfdaf6489ddac409b978f03aaa0fec5583",
	// seller.destination_rail is destination_rail with an allow-list
	// (allowed_rails, read since d8373fb).
	"seller.destination_rail/1.0.0":   "fab02cc2b39b2f45a5ac2a260eff7544814a2fb82de5524d91b00fd0b8d19800",
	"promise_never/1.0.0":             "836ae58f704a9c8ab8027fee344ca47ddb3de76a7a1973a650f42a3595e649f4",
	"promise_requires_approval/1.0.0": "b29e29e0a31ef764b282fa5754992b49b5ffc4f3fc261fb821987ae6c2e1c9b2",
	"required_disclosure/1.0.0":       "c8f216415be1fac673d1efc6df2e7a9666b5d5c8e639fbb3d8379a24bb8f5ebf",
	"disclosure.class_seen/1.0.0":     "5b4d0280892db43c5209629137be8634be01c32d481ae79e005c4a1c6358c564",
	// asg/seller/0.1.3 (capsule-engine #139, 08cd96d): required_disclosure/
	// 1.1.0 also counts a statement in the action's deal, over
	// disclosure.statement_seen/1.0.0 (checkRequiredDisclosure).
	"required_disclosure/1.1.0":       "80f3af6e428f2331f6ab458997d996e172b9fc2e721b55fcf473f4e31d1492c0",
	"disclosure.statement_seen/1.0.0": "925e7ccd2e29b3f1984f966e968bdef47d0d22d2b85b8e0205a39d6a5a7ff0be",
	// asg/everyday/0.3.7 and asg/seller/0.1.4 (capsule-engine #173,
	// 7339028) add a platform recipient that may be given the user's email:
	// recipient_role/1.1.0 and seller.recipient_role/1.1.0 configure
	// role_classes (checkRecipientRole), action_class_gate/1.1.0's
	// personal_disclosure selector sets except_recipient_roles
	// (checkActionClassGate), and seller.release_on_acceptance/1.1.0 does
	// not apply to a disclosure to a platform (checkReleaseOnAcceptance).
	// platform_role_test.go holds them to the engine. 0.3.6 and 0.1.3 stay
	// vendored, so the 1.0.0 definitions stay ported.
	"recipient_role/1.1.0":               "57cb99bde24b5ae41ae214f4d2db0e8f20cbdf7ba31170b65da0561afdc499e1",
	"seller.recipient_role/1.1.0":        "8ea4cc45972a840a56af5f0fefbd6092dee3797696a8cd3303940a98b7b7b35d",
	"action_class_gate/1.1.0":            "817a3d537b024689e7fcc20d37e5253f77075274c5b7d863dcc25eac39bc8f2b",
	"seller.release_on_acceptance/1.1.0": "933d72ab36863cb67ef2f0333b424fb4d559fa77bfeb1f5b63a61dee6307e28b",
	// asg/seller/0.1.5 (capsule-engine #197, 9066490; vendored at 0c93a58):
	// seller.release_on_acceptance/1.2.0 reads the classes a share gives
	// and binds the sale's acceptance from the history (releaseFromHistory).
	// release_history_test.go holds it to the engine.
	"seller.release_on_acceptance/1.2.0": "854718b637f2f4a06c37d3ba49bccc8b44ca8832878b339a0ac988963256a832",
	// asg/seller/0.1.5 (capsule-engine #195, 2a873e6): seller.recipient_role/
	// 1.2.0 lets a platform be given pickup_area as well as email
	// (role_classes; checkRecipientRole is unchanged). recipient_role_12_test.go
	// holds it to the engine.
	"seller.recipient_role/1.2.0": "6216addfa4e53b746dd72ec6dd4986758e064d2885e007b0fa546e2d42d189d1",
	// asg/seller/0.1.5 (capsule-engine #201, 6c5d999): seller.destination_rail/
	// 1.1.0 checks the rail at the seller's commit too, counts venmo, zelle,
	// cash_app and paypal as p2p, passes cash only handed over in person, and
	// fails a commit that states no rail (checkDestinationRail).
	// destination_rail_11_test.go holds it to the engine.
	"seller.destination_rail/1.1.0": "a86844bd3b5132ae5fb04e9009c5c6907ea43c1609df713c45055dd04bcdbe0e",
	// asg/seller/0.1.5 (capsule-engine #204, d336c3a): required_disclosure/
	// 1.2.0 also counts a class the sale's listings in force state
	// (count_listing_classes, Action.ListingClasses; checkRequiredStatements).
	// required_disclosure_12_test.go holds it to the engine.
	"required_disclosure/1.2.0": "cae962303066286b3c391a6c1a2f5f5db6742865fb84e1932ad68515a423224f",
	// asg/everyday/0.3.8 (capsule-engine #203, b5d2c77): action_class_gate/
	// 1.2.0 leaves out a disclosure the producer reports as a repeat to the
	// same recipient (except_repeat_disclosures, except_recipient_told_before;
	// checkActionClassGate). action_class_gate_12_test.go holds it to the
	// engine.
	"action_class_gate/1.2.0": "39a7c5b94173a4c8496b338028b8680ba20ac65543802c67e419577acce097bf",
	// asg/seller/0.1.5 (capsule-engine #202, d3066f5): offer_expiry/1.1.0 holds
	// an offer that answers no proposal not applicable (no_proposal_classes,
	// Action.NoProposal), and a replayed typed offer is dated by the message
	// it answers (acceptedOfferAt). offer_expiry_11_test.go holds it to the
	// engine.
	"offer_expiry/1.1.0": "e55083b32ffe5e9415d3ef6455398b42187d9de76aba9f6cc735f3ea71f590f1",
	// asg/seller/0.1.5 (capsule-engine #205, 0c93a58; reworded by #209,
	// a489e49): promise_requires_
	// approval/1.1.0 reads the classes the user ordered at authorized_path
	// (body.ordered_representations), and promise_never/1.1.0 passes the
	// user's own claim, said as theirs and marked unverified, when its words
	// open the check's commitment and fit attributed_template
	// (decideAttributed). promise_11_test.go holds them to the engine.
	"promise_requires_approval/1.1.0": "52514fb625b208f2f04d900d115468b526c01ec34c5630fe3ae68ed86eefe386",
	"promise_never/1.1.0":             "ed9e02ed17cbd08a0f846023e7091e7636d504ba1f18c6b9173e0a2443d92b8b",
	// asg/inference/0.1.0 (capsule-engine#263): rate_cap/1.0.0 compares a
	// peer's advertised rates with two caps (checkRateCap), and
	// counterparty_list/1.0.0 is the user's list (checkCounterpartyList),
	// their values set by a profile (WithProfile). The inference conformance
	// vectors hold both to the engine.
	"rate_cap/1.0.0":          "a2628f677ddbeb6cdd6a62ac44403b51ac03f878b40e1c8d1627c95039c13d93",
	"counterparty_list/1.0.0": "d95162931a6c1f88c763f2fd221d63b238888c1bdd7c6d594123ee5671e18f65",
}

// unportedDefinitions are the definitions a vendored pack cites that this
// port does not run yet, pinned by digest all the same: a pack citing one is
// loaded only at exactly this digest. Each applies only to the action classes
// its config names (capsule-engine guards/checks at a5e9e30: an action of
// any other class is n/a, out of scope; unportedScope); within them its rule
// is not_evaluable, "not yet run by this plugin", never a pass.
//
// None now: every vendored pack, asg/everyday/0.3.4 to 0.3.8 and
// asg/seller/0.1.0 to 0.1.5, cites only definitions this port runs. A
// later pack's new check is pinned here until it is ported.
var unportedDefinitions = map[string]string{}

// unportedFolds are the folds a vendored pack cites that no ported check
// reads, pinned by digest. None now: every fold asg/seller/0.1.0 cites is
// read by a ported check.
var unportedFolds = map[string]string{}

// askGateSelectors is capsule-engine packs/install.py ask_gate_selectors
// (c29e738): the action_class_gate selectors bound to at least one rule,
// every one of which declares default_disposition ASK. A selector with a
// NEVER or DO rule, or a rule declaring none, is left out, so failing it
// still refuses.
func askGateSelectors(p pack.Pack) map[string]bool {
	declared := map[string]map[string]bool{}
	for _, r := range p.Rules {
		if r.Check == "action_class_gate" && r.Selector != "" {
			if declared[r.Selector] == nil {
				declared[r.Selector] = map[string]bool{}
			}
			declared[r.Selector][r.DefaultDisposition] = true
		}
	}
	out := map[string]bool{}
	for sid, dispositions := range declared {
		if len(dispositions) == 1 && dispositions["ASK"] {
			out[sid] = true
		}
	}
	return out
}

// AskGateSelectors are the engine's ASK gate selectors, sorted.
func (e *Engine) AskGateSelectors() []string {
	return slices.Sorted(maps.Keys(e.askGate))
}

// integrityChecks are capsule-engine guards/engine.py _INTEGRITY_CHECKS
// (8d7bff2): a failure is refused whatever a pack declares (a dedupe hit on
// the same act in another deal asks through asksApprover). ownAskRule are
// _OWN_ASK_RULE, the checks whose own config decides whether a failure asks.
// Together they are ASK_RULE_EXCLUDED_CHECKS: a declared ASK never makes
// one of them ask.
var (
	integrityChecks = []string{"dedupe", "verify_before_dispatch", "single_commitment", "promise_never", "release_on_acceptance"}
	ownAskRule      = []string{"action_class_gate", "counterparty_list"}
)

// askWickets is capsule-engine packs/install.py ask_wickets intersected with
// the configured checks, as engine_ask_sets builds it (8d7bff2): the checks
// among order bound to at least one rule, every one of which declares
// default_disposition ASK. A check with a NEVER or DO rule, or a rule
// declaring none, is left out, and so are the integrity checks and the two
// with their own ask rule. caps and counterparty_seen_before ask whatever
// is declared (escalatable).
func askWickets(p pack.Pack, order []string) map[string]bool {
	declared := map[string]map[string]bool{}
	for _, r := range p.Rules {
		if !slices.Contains(order, r.Check) || slices.Contains(integrityChecks, r.Check) || slices.Contains(ownAskRule, r.Check) {
			continue
		}
		if declared[r.Check] == nil {
			declared[r.Check] = map[string]bool{}
		}
		declared[r.Check][r.DefaultDisposition] = true
	}
	out := map[string]bool{}
	for check, dispositions := range declared {
		if len(dispositions) == 1 && dispositions["ASK"] {
			out[check] = true
		}
	}
	return out
}

// AskWickets are the engine's ASK checks (askWickets), sorted.
func (e *Engine) AskWickets() []string {
	return slices.Sorted(maps.Keys(e.askChecks))
}

// implementedChecks are the checks NewEngine configures and check runs.
// Every check asg/everyday/0.3.8 cites is one of them (unported_test.go), so
// no rule of the shipped pack is "not yet run by this plugin"; a later pack
// citing any other check reaches that fallback.
var implementedChecks = []string{"caps", "dedupe", "destination_rail", "counterparty_identity_change", "credential_pattern",
	"recurring_charge", "refundability", "upfront_amount", "recipient_role", "task_authority", "counterparty_seen_before",
	"channel_change", "material_fields_changed", "offer_fields_changed", "recipient_seen_before", "action_class_gate",
	"verify_before_dispatch", "offer_expiry", "price_floor", "single_commitment", "promise_never", "promise_requires_approval",
	"required_disclosure", "release_on_acceptance", "counterparty_list", "rate_cap"}

// NotYetRunRules are the rules of p whose check this port does not run, by
// rule id: their result is not_evaluable, NotYetRun, and a listing must say
// so rather than call them a rule check. Empty for asg/everyday/0.3.8. A
// check is not run when it is not implemented or when the definition p
// cites for it is pinned as unported.
func NotYetRunRules(p pack.Pack) map[string]bool {
	out := map[string]bool{}
	for _, r := range p.Rules {
		if r.Check == "" {
			continue
		}
		w, _ := p.Wicket(r.Check)
		if _, unported := unportedDefinitions[w.ID]; unported || !slices.Contains(implementedChecks, r.Check) {
			out[r.ID] = true
		}
	}
	return out
}

// NotYetRun is the reason a rule whose check this port does not run yet is
// not_evaluable; DeclaredNotMeasured is the reason for a rule the pack
// declares and no check measures (measurability declared_not_measured).
const (
	NotYetRun           = "not yet run by this plugin"
	DeclaredNotMeasured = "declared, not measured"
)

// escalatable are the failures that ask an approver rather than refuse
// (capsule-engine guards/engine.py _ESCALATABLE at 2521ee6): an over-limit
// spend and a first-time counterparty. The everyday pack cites no
// counterparty_seen_before, so here it is caps alone in practice.
var escalatable = map[string]bool{"caps": true, "counterparty_seen_before": true}

// Outcome values (python/capsule_engine/guards/capsule.py:75-77).
const (
	Allow    = "allow"
	Deny     = "deny"
	Escalate = "escalate"
)

// capsConfig is caps/3.0.0's config (guards/wickets/catalog_defs/caps.v3.yaml):
// the pooled window limit and the per-action limit, both per action class.
// caps/5.0.0 adds per_action_reads (caps.py PER_ACTION_READS): absent, the
// per-action limit reads the capture; spend_authorized_minor, it reads the
// authorised maximum (perActionBasis).
type capsConfig struct {
	PerActionReads string           `yaml:"per_action_reads"`
	FoldID         string           `yaml:"fold_id"`
	FoldDigest     string           `yaml:"fold_digest"`
	CapsMinor      map[string]int64 `yaml:"caps_minor"`
	PerActionMinor map[string]int64 `yaml:"per_action_minor"`
}

type dedupeConfig struct {
	Method     string `yaml:"method"`
	WindowDays int    `yaml:"window_days"`
}

// railConfig is destination_rail's config: destination_rail/1.0.0 watches
// rails; seller.destination_rail/1.0.0 allows only the rails it lists. nil
// AllowedRails is no allow-list (destination_rail.py allowed_rails None);
// an empty one allows no rail. InPersonRails, RailAliases and MissingRail
// are seller.destination_rail/1.1.0's (capsule-engine #201); with none of
// them set the check decides and records as 1.0.0 does.
type railConfig struct {
	ActionClasses []string          `yaml:"action_classes"`
	WatchedRails  []string          `yaml:"watched_rails"`
	AllowedRails  *[]string         `yaml:"allowed_rails"`
	InPersonRails []string          `yaml:"in_person_rails"`
	RailAliases   map[string]string `yaml:"rail_aliases"`
	MissingRail   map[string]string `yaml:"missing_rail"`
}

type counterpartyConfig struct {
	ActionClasses []string `yaml:"action_classes"`
	Method        string   `yaml:"method"`
}

type credentialConfig struct {
	Patterns []struct {
		ID    string `yaml:"id"`
		Regex string `yaml:"regex"`
	} `yaml:"patterns"`
}

// classConfig is the config of a check configured by its action classes
// alone (refundability).
type classConfig struct {
	ActionClasses []string `yaml:"action_classes"`
}

type upfrontConfig struct {
	ActionClasses   []string `yaml:"action_classes"`
	UpfrontMaxMinor int64    `yaml:"upfront_max_minor"`
	UpfrontMaxBps   int64    `yaml:"upfront_max_bps"`
}

// roleConfig is recipient_role's config. RoleClasses (recipient_role/1.1.0
// and seller.recipient_role/1.1.0) names roles that pass only for some
// classes of data; nil when the definition sets none (1.0.0).
type roleConfig struct {
	ActionClasses []string            `yaml:"action_classes"`
	Roles         []string            `yaml:"roles"`
	AllowedRoles  []string            `yaml:"allowed_roles"`
	RoleClasses   map[string][]string `yaml:"role_classes"`
}

// checkRoleClasses is recipient_role.py's role_classes check: every role it
// names is in roles and not in allowed_roles, each with at least one class.
// The engine raises when the check runs; here the engine is not built.
func checkRoleClasses(c roleConfig, id string) error {
	for _, role := range slices.Sorted(maps.Keys(c.RoleClasses)) {
		if !slices.Contains(c.Roles, role) || slices.Contains(c.AllowedRoles, role) || len(c.RoleClasses[role]) == 0 {
			return fmt.Errorf("%s role_classes must name roles in roles and not in allowed_roles, each with at least one class; got %v",
				id, slices.Sorted(maps.Keys(c.RoleClasses)))
		}
	}
	return nil
}

// neverConfig is promise_never's config: the closed set of statement
// classes, the classes never made, and the action classes it applies to.
type neverConfig struct {
	ActionClasses         []string `yaml:"action_classes"`
	RepresentationClasses []string `yaml:"representation_classes"`
	Never                 []string `yaml:"never"`
	// AttributedNever (1.1.0) are the never classes the user's own claim,
	// in AttributedTemplate, may state (decideAttributed); nil on 1.0.0,
	// which reads neither the attribution nor the words.
	AttributedNever    *[]string           `yaml:"attributed_never"`
	AttributedTemplate *attributedTemplate `yaml:"attributed_template"`
}

// checkAttributed is promise_never.py's config check for 1.1.0:
// attributed_never names never classes, and comes with a template.
func checkAttributed(c neverConfig, id string) error {
	if c.AttributedNever == nil {
		return nil
	}
	if c.AttributedTemplate == nil {
		return fmt.Errorf("%s attributed_never needs an attributed_template", id)
	}
	return requireKnownClasses(*c.AttributedNever, c.Never, id+" attributed_never")
}

// requireKnownClasses is required_disclosure.py require_known_classes: a
// configured list of statement classes must come from the closed set. The
// engine raises when the check runs; here the engine is not built.
func requireKnownClasses(classes, closed []string, what string) error {
	var unknown []string
	for _, c := range classes {
		if !slices.Contains(closed, c) {
			unknown = append(unknown, c)
		}
	}
	if len(unknown) > 0 {
		return fmt.Errorf("%s names %v, outside the closed set %v", what, unknown, closed)
	}
	return nil
}

// approvalConfig is promise_requires_approval's config: the closed set of
// statement classes, the classes that need the user's word, and the action
// classes it applies to.
type approvalConfig struct {
	ActionClasses         []string `yaml:"action_classes"`
	RepresentationClasses []string `yaml:"representation_classes"`
	RequiresApproval      []string `yaml:"requires_approval"`
	// AuthorizedPath (1.1.0) is where the bound task-authority record lists
	// the classes the user ordered; nil on 1.0.0, which reads authorizedPath.
	AuthorizedPath *[]string `yaml:"authorized_path"`
}

// authorizedPath is where the task authority lists the classes it allows.
func (c approvalConfig) authorizedPath() []string {
	if c.AuthorizedPath == nil {
		return authorizedPath
	}
	return *c.AuthorizedPath
}

// knownOrdered is the classes an ordered list may hold, or nil on 1.0.0,
// which counts a list whatever else it holds.
func (c approvalConfig) knownOrdered() []string {
	if c.AuthorizedPath == nil {
		return nil
	}
	return c.RepresentationClasses
}

type recurringConfig struct {
	ActionClasses []string `yaml:"action_classes"`
	OneTimeValues []string `yaml:"one_time_values"`
}

// Engine evaluates actions against one loaded pack.
type Engine struct {
	pack pack.Pack
	tax  taxonomy
	// noApprovalRecords is set (WithoutApprovalRecords) when the caller can
	// never supply the approval record an action's authorized_by names, as
	// on every path of the plugin: promise_requires_approval then reads such
	// an action as not evaluated (NoApprovalRecord), never as unapproved.
	noApprovalRecords bool
	// capsMinor and perActionMinor are keyed by canonical action class
	// (caps.py resolve_caps_minor).
	capsMinor      map[string]int64
	perActionMinor map[string]int64
	// perActionReads is the caps wicket's per_action_reads ("" reads the
	// capture).
	perActionReads string
	// requiredFields are the pack's action types and the fields each
	// requires (requiredFieldsOf): an action whose verb names one is refused
	// when it lacks one (checkRequiredFields).
	requiredFields map[string][]string
	// limitSources: caps evidence names where each limit came from
	// (limit_sources). An engine built from an installed pack reads its
	// limits from policy (capsule-engine 365eb92 engine.py _limits_for):
	// operatorLimits when set, else every limit is the wicket's own,
	// definition_default. Off only for a test mirroring a GuardEngine built
	// with bare limit tables, which writes none.
	limitSources bool
	// operatorLimits are the limits activated policy sets
	// (WithOperatorLimits), or nil.
	operatorLimits OperatorLimits
	// capsFold is the caps definition's fold, or nil when the pack cites no
	// caps definition (capsule-engine e0e664d): caps is then n/a, out of
	// scope, with no method, on every decision, and reads nothing.
	capsFold         *fold
	dedupeWindowDays int
	// configured checks, in pack constraint order
	order        []string
	rail         railConfig
	counterparty counterpartyConfig
	patterns     []compiledPattern
	recurring    recurringConfig
	refund       classConfig
	upfront      upfrontConfig
	role         roleConfig
	authority    classConfig
	list         listConfig
	rateCap      rateCapConfig
	gate         yaml.Node
	// askGate are the action_class_gate selectors whose obligations all
	// declare ASK (askGateSelectors): a gate failure on these alone asks.
	askGate map[string]bool
	// askChecks are the configured checks whose rules all declare ASK
	// (askWickets): a failure of one asks.
	askChecks  map[string]bool
	seenBefore seenBefore
	channel    channelConfig
	material   fieldCount
	offer      fieldCount
	// recipientSeenBefore is recipient_seen_before: the same check, for a
	// message.
	recipientSeenBefore seenBefore
	offerExpiry         offerExpiryConfig
	priceFloor          priceFloorConfig
	singleCommitment    singleCommitmentConfig
	release             releaseConfig
	never               neverConfig
	approval            approvalConfig
	disclosure          requiredDisclosure
	// unported are the checks cited at a pinned unportedDefinitions digest,
	// by check name: the wicket id and the action classes it applies to.
	unported map[string]unportedCheck
}

// unportedCheck is a cited check this port does not run yet.
type unportedCheck struct {
	wicketID      string
	actionClasses []string
}

// decodeConfig decodes a wicket's config into the check's config type,
// refusing keys the port does not implement.
func decodeConfig[T any](w pack.Wicket) (T, error) {
	var out T
	raw, err := yaml.Marshal(&w.Config)
	if err != nil {
		return out, err
	}
	dec := yaml.NewDecoder(bytes.NewReader(raw))
	dec.KnownFields(true)
	if err := dec.Decode(&out); err != nil {
		return out, fmt.Errorf("%s config (a key this port does not implement is refused): %w", w.ID, err)
	}
	return out, nil
}

func sortedKeys(m map[string]int64) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	return keys
}

// ClassScopes are the action classes each class-limited check's config
// covers, by check name, read from the pack's cited wicket config: today
// caps, whose caps_minor names one limit per class. A check not in the map
// is not limited by action class.
func ClassScopes(p pack.Pack) (map[string][]string, error) {
	out := map[string][]string{}
	if w, ok := p.Wicket("caps"); ok {
		caps, err := decodeConfig[capsConfig](w)
		if err != nil {
			return nil, err
		}
		tax, err := engineTaxonomy()
		if err != nil {
			return nil, err
		}
		limits, err := tax.resolveLimits(caps.CapsMinor)
		if err != nil {
			return nil, err
		}
		out["caps"] = sortedKeys(limits)
	}
	return out, nil
}

// NewEngine builds an engine from a loaded pack, refusing one whose cited
// wicket or fold definitions are not exactly the ported ones.
// WithoutApprovalRecords says the caller supplies no approval record with
// any check (GuardEngine.check authorization_record), and returns e. The
// plugin's paths read only what capsulectl seals, and capsulectl seals no
// approval for a class of statement, so an action whose authorized_by
// cites one is not evaluated by promise_requires_approval: it never passes,
// and it is not called unapproved for a record this plugin cannot be given.
// The engine's own result (fail, "no approval record was supplied") stays
// the default, for the vectors that supply one or none on purpose.
func (e *Engine) WithoutApprovalRecords() *Engine {
	e.noApprovalRecords = true
	return e
}

// ErrNotPorted is NewEngine's refusal of a pack that cites a definition this
// port neither implements nor pins at that digest: a pack vendored ahead of
// its port.
var ErrNotPorted = errors.New("not a definition this port implements or pins")

func NewEngine(p pack.Pack) (*Engine, error) {
	for _, w := range p.Wickets {
		want, ok := portedDefinitions[w.ID]
		if !ok {
			want, ok = unportedDefinitions[w.ID]
		}
		if !ok || want != w.Digest {
			return nil, fmt.Errorf("wicket %s at digest %s is %w", w.ID, w.Digest, ErrNotPorted)
		}
	}
	for _, f := range p.Folds {
		want, ok := portedDefinitions[f.ID]
		if !ok {
			want, ok = unportedFolds[f.ID]
		}
		if !ok || want != f.Digest {
			return nil, fmt.Errorf("fold %s at digest %s is %w", f.ID, f.Digest, ErrNotPorted)
		}
	}
	tax, err := engineTaxonomy()
	if err != nil {
		return nil, err
	}
	e := &Engine{pack: p, tax: tax, limitSources: true, askGate: askGateSelectors(p)}
	if e.requiredFields, err = requiredFieldsOf(p); err != nil {
		return nil, err
	}
	// A pack may cite no caps definition (capsule-engine e0e664d,
	// GuardEngine caps_fold=None); this port reads no limit from anywhere
	// else, so there is no limit to refuse without one.
	if capsW, ok := p.Wicket("caps"); ok {
		if err := e.configureCaps(capsW); err != nil {
			return nil, err
		}
	}

	dedupeW, ok := p.Wicket("dedupe")
	if !ok {
		return nil, errors.New("pack cites no dedupe wicket")
	}
	dd, err := decodeConfig[dedupeConfig](dedupeW)
	if err != nil {
		return nil, err
	}
	if dd.Method != "exact_match_index_v0" || dd.WindowDays <= 0 {
		return nil, fmt.Errorf("dedupe method %q window %d is not what this port implements", dd.Method, dd.WindowDays)
	}
	e.dedupeWindowDays = dd.WindowDays

	for _, w := range p.Wickets {
		var err error
		if _, pinned := unportedDefinitions[w.ID]; pinned {
			// A pinned unported definition is not run, whether or not its
			// check is implemented at another definition.
			var u unportedCheck
			if u, err = unportedScope(w); err != nil {
				return nil, err
			}
			if e.unported == nil {
				e.unported = map[string]unportedCheck{}
			}
			e.unported[w.Check] = u
			e.order = append(e.order, w.Check)
			continue
		}
		switch w.Check {
		case "caps", "dedupe", "verify_before_dispatch":
			// Run by check in the engine's own order, whether a pack cites
			// them or not.
			continue
		case "destination_rail":
			e.rail, err = decodeConfig[railConfig](w)
		case "counterparty_identity_change":
			e.counterparty, err = decodeConfig[counterpartyConfig](w)
			if err == nil && e.counterparty.Method != "prior_recorded_value_v0" {
				err = fmt.Errorf("counterparty_identity_change method %q is not ported", e.counterparty.Method)
			}
		case "credential_pattern":
			var cc credentialConfig
			if cc, err = decodeConfig[credentialConfig](w); err == nil {
				for _, pt := range cc.Patterns {
					re, cerr := regexp.Compile(pt.Regex)
					if cerr != nil {
						err = fmt.Errorf("credential_pattern %s: %w", pt.ID, cerr)
						break
					}
					e.patterns = append(e.patterns, compiledPattern{id: pt.ID, re: re})
				}
			}
		case "recurring_charge":
			e.recurring, err = decodeConfig[recurringConfig](w)
		case "refundability":
			e.refund, err = decodeConfig[classConfig](w)
		case "upfront_amount":
			e.upfront, err = decodeConfig[upfrontConfig](w)
		case "recipient_role":
			if e.role, err = decodeConfig[roleConfig](w); err == nil {
				err = checkRoleClasses(e.role, w.ID)
			}
		case "task_authority":
			e.authority, err = decodeConfig[classConfig](w)
		case "counterparty_list":
			if e.list, err = decodeConfig[listConfig](w); err == nil {
				err = e.list.check()
			}
		case "rate_cap":
			e.rateCap, err = decodeConfig[rateCapConfig](w)
		case "counterparty_seen_before":
			e.seenBefore, err = newSeenBefore(w, p)
		case "channel_change":
			e.channel, err = decodeConfig[channelConfig](w)
		case "material_fields_changed":
			e.material, err = newFieldCount(w)
		case "offer_fields_changed":
			e.offer, err = newFieldCount(w)
		case "recipient_seen_before":
			e.recipientSeenBefore, err = newSeenBefore(w, p)
		case "offer_expiry":
			e.offerExpiry, err = decodeConfig[offerExpiryConfig](w)
		case "price_floor":
			e.priceFloor, err = decodeConfig[priceFloorConfig](w)
		case "single_commitment":
			e.singleCommitment, err = decodeConfig[singleCommitmentConfig](w)
		case "release_on_acceptance":
			if e.release, err = decodeConfig[releaseConfig](w); err == nil {
				err = checkReleaseBinding(e.release, w.ID)
			}
		case "promise_never":
			if e.never, err = decodeConfig[neverConfig](w); err == nil {
				err = errors.Join(requireKnownClasses(e.never.Never, e.never.RepresentationClasses, w.ID+" never"),
					checkAttributed(e.never, w.ID))
			}
		case "promise_requires_approval":
			if e.approval, err = decodeConfig[approvalConfig](w); err == nil {
				err = requireKnownClasses(e.approval.RequiresApproval, e.approval.RepresentationClasses, w.ID+" requires_approval")
			}
		case "required_disclosure":
			e.disclosure, err = newRequiredDisclosure(w, p)
		case "action_class_gate":
			var gc gateConfig
			gc, err = decodeConfig[gateConfig](w)
			e.gate = gc.Selectors
		default:
			err = fmt.Errorf("wicket %s configures check %q, which this port does not implement", w.ID, w.Check)
		}
		if err != nil {
			return nil, err
		}
		e.order = append(e.order, w.Check)
	}
	e.askChecks = askWickets(p, e.order)
	return e, nil
}

// configureCaps reads a ported caps definition: its fold, and its limits
// resolved through the taxonomy.
func (e *Engine) configureCaps(w pack.Wicket) error {
	caps, err := decodeConfig[capsConfig](w)
	if err != nil {
		return err
	}
	fold, ok := e.pack.Fold(caps.FoldID)
	if !ok || fold.Digest != caps.FoldDigest {
		return fmt.Errorf("caps cites fold %s at %s, which the pack does not resolve to that digest", caps.FoldID, caps.FoldDigest)
	}
	sf, err := newFold(fold)
	if err != nil {
		return err
	}
	if e.capsMinor, err = e.tax.resolveLimits(caps.CapsMinor); err != nil {
		return fmt.Errorf("caps_minor: %w", err)
	}
	if e.perActionMinor, err = e.tax.resolveLimits(caps.PerActionMinor); err != nil {
		return fmt.Errorf("per_action_minor: %w", err)
	}
	if err := requirePerActionReads(caps.PerActionReads); err != nil {
		return err
	}
	e.perActionReads = caps.PerActionReads
	e.capsFold = &sf
	return nil
}

// unportedScope is the action classes an unported definition applies to,
// from its config (only the scope is read: the digest pins the rest):
// action_classes, and for a commitment check (single_commitment) its
// commit_classes and acceptance_classes. A definition naming none is refused
// rather than read as applying to nothing.
func unportedScope(w pack.Wicket) (unportedCheck, error) {
	var scope struct {
		ActionClasses     []string `yaml:"action_classes"`
		CommitClasses     []string `yaml:"commit_classes"`
		AcceptanceClasses []string `yaml:"acceptance_classes"`
	}
	if err := w.Config.Decode(&scope); err != nil {
		return unportedCheck{}, fmt.Errorf("%s scope: %w", w.ID, err)
	}
	classes := slices.Concat(scope.ActionClasses, scope.CommitClasses, scope.AcceptanceClasses)
	if len(classes) == 0 {
		return unportedCheck{}, fmt.Errorf("%s names no action class it applies to", w.ID)
	}
	slices.Sort(classes)
	return unportedCheck{wicketID: w.ID, actionClasses: slices.Compact(classes)}, nil
}

// ErrCitedMandate refuses a check of an action that cites a mandate capsule:
// verify_before_dispatch must re-verify it, which needs the capsule verifier
// this port does not have. asg/seller/0.1.0 cites one on every commitment
// after an acceptance and on a disclosure under it.
var ErrCitedMandate = errors.New("the action cites a mandate capsule; verify_before_dispatch needs the capsule verifier, which this port does not have")

// Decision is the outcome of one check run: allow, deny or escalate, and the
// constraints in the engine's order (dedupe, caps, verify_before_dispatch,
// then the configured checks in pack order; engine.py check()).
type Decision struct {
	Outcome     string
	Constraints []Constraint
	// refusedForTaxonomy marks a would-allow refused only because its
	// record is sealed at another action taxonomy (checkWith): the engine
	// seals it reject, and labels its verdict not_evaluable (Report).
	refusedForTaxonomy bool
	// notEvaluated are the checks that could not be evaluated and fail
	// closed (GuardDecision.not_evaluated_checks, capsule-engine #137): the
	// action is refused (Deny), and with no constraint failed the report's
	// verdict is not_evaluable.
	notEvaluated []string
	// askedUnevaluated are the checks that could not be evaluated and ask
	// (GuardDecision.asked_unevaluated_checks: caps over a window the
	// history cannot decide): the action is never allowed (Escalate, or Deny
	// with no approver), and with no constraint failed the report labels
	// it as the engine's GuardDecision.verdict does: escalate when it asks,
	// not_evaluable with Refused when it is denied for want of an approver,
	// and not_evaluable when the record is in no deal.
	askedUnevaluated []string
}

// anyFailed is whether any constraint failed.
func (d Decision) anyFailed() bool {
	return slices.ContainsFunc(d.Constraints, func(c Constraint) bool { return c.Result == Fail })
}

// Constraint returns the named constraint.
func (d Decision) Constraint(id string) (Constraint, bool) {
	for _, c := range d.Constraints {
		if c.ID == id {
			return c, true
		}
	}
	return Constraint{}, false
}

// Check evaluates one action against the history of earlier records; a nil
// history holds none.
func (e *Engine) Check(a Action, h History) (Decision, error) {
	return e.check(a, h, "", nil)
}

// CheckWithTaskAuthority is Check with the sealed task-authority record the
// action's task_authority_ref names supplied beside it (GuardEngine.check
// task_authority_record): the whole record as JSON, read by task_authority
// only when its digest equals the reference.
func (e *Engine) CheckWithTaskAuthority(a Action, h History, record json.RawMessage) (Decision, error) {
	return e.check(a, h, "", record)
}

// CheckReplay is Check for a record read from a capsule bundle or a single
// capsule (check --record), decided as the engine's replay decides it: with
// what the bundle gives it beside the record (BundleReplay).
func (e *Engine) CheckReplay(a Action, h History, r Replay) (Decision, error) {
	return e.checkWith(a, h, checkInputs{taskAuthority: r.taskAuthority, words: r.words})
}

// CheckWithRecords is CheckWithTaskAuthority with the sealed approval record
// the action's authorized_by names supplied too (GuardEngine.check
// authorization_record): the whole record as JSON, read by
// promise_requires_approval only when its digest equals the reference.
func (e *Engine) CheckWithRecords(a Action, h History, taskAuthority, authorization json.RawMessage) (Decision, error) {
	return e.checkWith(a, h, checkInputs{taskAuthority: taskAuthority, authorization: authorization})
}

// InputHistory says what a history of earlier acts covers (external-check-input/v0
// history_scope): how many days back it reaches, whether it holds every act
// sealed in them, and the acts themselves. TaskAuthorityRecord is the
// envelope's task_authority_record, supplied beside them (see
// CheckWithTaskAuthority), or, for a sale bundle's check, the
// task-authority/v0 record the sale discloses under the digest the check
// cites (SaleBundle.Check), or nil; CommercialBoundsOpening is its
// commercial_bounds_opening, read only by price_floor, or nil.
type InputHistory struct {
	Days                    int
	Complete                bool
	Acts                    []Record
	TaskAuthorityRecord     json.RawMessage
	CommercialBoundsOpening json.RawMessage
	// RepresentingOpening is the record entry's representing_opening, or,
	// for a sale bundle's check (SaleBundle.Check, where replayed is set),
	// the opening the sale's deal reports carry for the check's words; nil
	// when there is none.
	RepresentingOpening json.RawMessage
	replayed            bool
	// statements are the claims AddEntry and AddDealClaims wrote a
	// statement for, by the claim's capsule id, so a claim given twice is
	// written once.
	statements map[string]bool
	// claimed are the statements the claims beside the checked record make
	// (AddDealClaims), written after the history's records, as the engine's
	// history ledger writes them.
	claimed []Record
	// itemRefUnread is set when an entry passes an item_ref not in its
	// agreed shape (AddEntry): which sale its act is in cannot be read.
	itemRefUnread bool
	// releaseWindow is, for a sale bundle's check (SaleBundle.Check), the
	// same history cut to the acts sealed in the last rulesHistoryDays days
	// before the check, which release_on_acceptance 1.2.0 reads (CheckSale).
	releaseWindow *InputHistory
}

// saleUnknown is whether h may be missing an act of a sale, as
// live_history.py history_ledger writes its incomplete record: its scope
// is not declared complete (an absent history included), or an entry's
// item_ref cannot be read. Only single_commitment and release_on_acceptance
// read it; the caps window reads Complete alone, as before (the engine's
// caps never reads the incomplete record).
func (h InputHistory) saleUnknown() bool { return !h.Complete || h.itemRefUnread }

// words are the words beside the check: on a replay, none is
// notInBundle (promise_never.py WORDS_NOT_IN_BUNDLE); live, none was given.
func (h InputHistory) words() statementWords {
	if isJSONNull(h.RepresentingOpening) {
		return statementWords{notInBundle: h.replayed}
	}
	return statementWords{opening: h.RepresentingOpening}
}

// records is what the checks read of h: its acts and history statements, then
// the statements the claims beside the checked record make.
func (h InputHistory) records() []Record {
	return append(slices.Clone(h.Acts), h.claimed...)
}

// CheckWithHistory is Check with the caps window evaluated over h. Authority
// comes from the scope, not from whether h holds any acts: complete and
// reaching back at least the window, h is the profile's spending in that
// period, even when empty. The window limit is not evaluable when h is
// incomplete, reaches back fewer days than the window, or holds an act the
// window cannot count (no recorded decision and no spend it can read, or a
// taxonomy version whose class names are not the engine's; see
// sameClassesAs). An executed act with no recorded decision is counted at
// its sealed spend (the G3 ruling; ExecutedInWindow), and an unchecked
// act as spend (UncheckedInWindow). An act with no recorded decision whose
// record states the money moved in is never spend, so it does not leave the
// window unevaluated; the caps facts name how many were left out
// (ExcludedFromWindow). The per-purchase limit is evaluated either way.
func (e *Engine) CheckWithHistory(a Action, h InputHistory) (Decision, error) {
	coverage, moneyIn := e.windowCoverage(a, h)
	return e.checkWith(a, MemoryHistory(h.records()), checkInputs{coverage: coverage, moneyInExcluded: moneyIn, history: true,
		historyIncomplete: h.saleUnknown(), taskAuthority: h.TaskAuthorityRecord, opening: h.CommercialBoundsOpening, words: h.words()})
}

// CheckSale is Check for a check of a sale bundle's thread (SaleBundle.Check;
// capsule-engine #146, GuardEngine.check sale_history): single_commitment
// reads the sale's acceptance from sale, the sale's history read as a live
// check's history is read, never from h; every other check reads h, as
// Check does. A release_on_acceptance that binds the acceptance
// from the history (1.2.0) reads sale's history cut to the live window
// (InputHistory.releaseWindow, which SaleBundle.Check gives); the cited 1.0.0
// and 1.1.0 read h, as the engine's replay does, where no acceptance
// is sealed, so they release nothing. A check citing a task authority is
// decided with the record the sale discloses under that digest
// (sale.TaskAuthorityRecord, which SaleBundle.Check gives), and a represent
// check with the opening of its words the sale's deal reports carry, or
// none (InputHistory.words).
func (e *Engine) CheckSale(a Action, h History, sale InputHistory) (Decision, error) {
	return e.checkWith(a, h, checkInputs{sale: &sale, taskAuthority: sale.TaskAuthorityRecord, words: sale.words()})
}

// moneyIn is the direction a deal body states when the money moved in (a
// refund, a partial cancel): the engine's deal bridge carries such an act's
// spend as 0, whatever its spend_minor says (capsule-engine #130,
// report/replay.py MONEY_IN).
const moneyIn = "in"

// windowCoverage is why h cannot carry the caps window, or "", and how many
// acts with no recorded decision it did not count because each states the
// money moved in. Such an act can never add to the window, so it cannot
// evade the limit, and ruling (b) (an undecided act could be uncounted
// spend) does not reach it (the ruling on F1, capsule-engine #130). It is told
// by its stated direction, as the bridge tells it, never by its spend. Any
// other act with no recorded decision is counted at its sealed spend
// (Record.executedSpend; the G3 ruling), a spend of 0 included; only one
// whose spend cannot be read (none, a negative one, or one in the wrong
// shape) leaves the window unevaluated, and only when it is in the window
// for a's fold key: one outside cannot change the total (rule 5,
// window-only).
func (e *Engine) windowCoverage(a Action, h InputHistory) (string, int) {
	if e.capsFold == nil {
		// No caps definition: there is no window to cover.
		return "", 0
	}
	window := e.capsFold.windowSpec
	switch {
	case !h.Complete:
		return "the history supplied is incomplete (more acts than it holds, or some could not be read), so the " + window + " total cannot be computed", 0
	case time.Duration(h.Days)*24*time.Hour < e.capsFold.window:
		return fmt.Sprintf("the history supplied reaches back %d day(s); the %s total needs %s", h.Days, window, window), 0
	}
	// An act whose spend cannot be read holds the window only when the
	// window would count it: under the check's fold key, in its window
	// (the G3 ruling, rule 5 window-only). A check whose time cannot be
	// read is refused by caps itself; here every such act holds.
	keyValue := a.Developer
	if e.capsFold.key == "operator" {
		keyValue = a.Operator
	}
	anchor, anchorErr := parseInstant(a.Timestamp)
	undecided, otherTaxonomy, excluded := 0, 0, 0
	for _, r := range h.Acts {
		switch {
		case r.Stated != nil:
			// A statement is not an act (live_history.py: state statement,
			// never no_disposition), so it leaves no gap in the window.
			continue
		case r.Decision != nil:
		case r.Direction != nil && *r.Direction == moneyIn:
			excluded++
		case r.Unchecked:
			// Counted as spend (outsideTheFold), so it leaves no gap.
		case e.capsFold.countsExecuted():
			// The engine's own rule decides it (outsideTheFold): an act
			// whose spend cannot be read leaves the window not evaluable
			// there, with its reason (WindowUnreadable).
		default:
			// An executed act is counted at its sealed spend (outsideTheFold;
			// the G3 ruling), so it leaves no gap; one whose spend cannot
			// be read does.
			if _, ok := r.executedSpend(); !ok && (anchorErr != nil || e.capsFold.inWindowFor(r, keyValue, anchor)) {
				undecided++
			}
		}
		if r.TaxonomyVersion != nil && !e.tax.sameClasses(*r.TaxonomyVersion) {
			otherTaxonomy++
		}
	}
	switch {
	case undecided > 0:
		return fmt.Sprintf("%d earlier act(s) carry no recorded decision; the %s total as the engine computes it counts accepted acts only, so it cannot be computed", undecided, window), excluded
	case otherTaxonomy > 0:
		return fmt.Sprintf("%d earlier act(s) were classed under another taxonomy version than the engine's (%s), so the %s total cannot be computed", otherTaxonomy, e.tax.version, window), excluded
	}
	return "", excluded
}

// check is Check; coverage, when set, is why the caps window cannot be
// evaluated over the history; taskAuthority is the supplied task-authority
// record, or nil.
func (e *Engine) check(a Action, h History, coverage string, taskAuthority json.RawMessage) (Decision, error) {
	return e.checkWith(a, h, checkInputs{coverage: coverage, taskAuthority: taskAuthority})
}

// checkInputs is what a check reads beside the action and the history:
// coverage, when set, is why the caps window cannot be evaluated over the
// history; moneyInExcluded is how many money-in acts with no recorded
// decision the window did not count (windowCoverage); taskAuthority is the supplied task-authority record, or nil;
// opening is the checker input's commercial_bounds_opening, or nil;
// authorization is the supplied approval record (GuardEngine.check
// authorization_record), or nil.
type checkInputs struct {
	coverage        string
	moneyInExcluded int
	// history is whether the history is a check input's, every entry an
	// executed act (CheckWithHistory), not a log of decisions;
	// historyIncomplete, whether that history may be missing acts
	// (InputHistory.saleUnknown), which single_commitment and release_on_acceptance
	// 1.2.0 read.
	history           bool
	historyIncomplete bool
	taskAuthority     json.RawMessage
	opening           json.RawMessage
	authorization     json.RawMessage
	// words are the words beside a represent check, read by
	// promise_never/1.1.0.
	words statementWords
	// sale is the history of a sale bundle's check (CheckSale), which
	// single_commitment reads whole and release_on_acceptance 1.2.0 cut to
	// the live window (InputHistory.releaseWindow), or nil.
	sale *InputHistory
}

// checkWith is check with its inputs: dedupe, caps and verify_before_dispatch
// in the engine's order, then the configured checks in pack order.
func (e *Engine) checkWith(a Action, h History, in checkInputs) (Decision, error) {
	if a.CitedMandateCapsuleID != nil {
		return Decision{}, ErrCitedMandate
	}
	var cs []Constraint
	for _, check := range append([]string{"dedupe", "caps", "verify_before_dispatch"}, e.order...) {
		c, err := e.constraintFor(check, a, h, in)
		if err != nil {
			return Decision{}, err
		}
		cs = append(cs, c)
	}
	if c, ok := checkRequiredFields(a, e.requiredFields); ok {
		cs = append(cs, c)
	}
	cs = e.compareTaxonomy(a, cs)
	_, approver := e.tax.approver(a.class())
	asks := e.askChecks
	if e.list.Disposition == "ask" {
		// engine.py: a counterparty_list configured disposition ask joins the
		// failures that ask an approver.
		asks = maps.Clone(asks)
		if asks == nil {
			asks = map[string]bool{}
		}
		asks["counterparty_list"] = true
	}
	outcome := decide(cs, approver, e.gateFailuresAsk(cs), asks)
	// A would-allow held only by its record's taxonomy is refused, as the
	// engine seals it (a reject;
	// capsule-engine 9ac3722): the class-keyed checks were never run, so
	// nothing allowed it. A failure elsewhere decides as before.
	refused := outcome == Allow && a.TaxonomyVersion != nil && *a.TaxonomyVersion != e.tax.version
	if refused {
		outcome = Deny
	}
	// engine.py check (#137): a check that fails closed and could not be
	// evaluated refuses the action, whatever else would allow or ask.
	var notEvaluated []string
	for _, c := range cs {
		if c.failsClosed && c.Result == NotApplicable {
			notEvaluated = append(notEvaluated, c.ID)
		}
	}
	if len(notEvaluated) > 0 {
		outcome = Deny
	}
	// engine.py check (78098ff): a caps result the window could not decide
	// (CheckOutcome.asks_when_unevaluated: the history is not known to hold
	// every act, or an act in the window has no readable spend) is never
	// allowed on. It asks when the class has an approver and is refused
	// otherwise. A class held only by its taxonomy was refused above.
	var askedUnevaluated []string
	if outcome == Allow && capsHeld(cs) {
		askedUnevaluated = []string{"caps"}
		outcome = Deny
		if approver {
			outcome = Escalate
		}
	}
	return Decision{Outcome: outcome, Constraints: cs, refusedForTaxonomy: refused, notEvaluated: notEvaluated,
		askedUnevaluated: askedUnevaluated}, nil
}

// capsHeld is whether caps is an n/a its window could not decide: the caps
// check ran (it carries its facts) and left a limit pending.
func capsHeld(cs []Constraint) bool {
	for _, c := range cs {
		if c.ID == "caps" && c.Result == NotApplicable && c.caps != nil && c.pending != "" {
			return true
		}
	}
	return false
}

// constraintFor is one check's result for a, before the taxonomy comparison
// (compareTaxonomy) and the decision. A check cited at a definition pinned as
// unported is not yet run, even when the check is implemented at another
// definition.
func (e *Engine) constraintFor(check string, a Action, h History, in checkInputs) (Constraint, error) {
	r := &historyReader{h: orEmpty(h)}
	c, err := e.runCheck(check, a, r, in)
	if err == nil {
		err = r.err
	}
	return c, err
}

// runCheck is constraintFor's check, reading the history through r.
func (e *Engine) runCheck(check string, a Action, r *historyReader, in checkInputs) (Constraint, error) {
	if u, ok := e.unported[check]; ok {
		return checkUnported(a, check, u), nil
	}
	switch check {
	case "dedupe", "caps":
		at, err := parseInstant(a.Timestamp)
		if err != nil {
			return Constraint{}, fmt.Errorf("timestamp: %w", err)
		}
		if check == "dedupe" {
			return e.checkDedupe(a, r, at)
		}
		return e.checkCaps(a, r, at, in), nil
	case "verify_before_dispatch":
		return checkVerifyBeforeDispatch(), nil
	case "destination_rail":
		return checkDestinationRail(a, e.rail), nil
	case "counterparty_identity_change":
		return checkCounterpartyIdentityChange(a, r, e.counterparty), nil
	case "credential_pattern":
		return checkCredentialPattern(a, e.patterns), nil
	case "recurring_charge":
		return checkRecurringCharge(a, e.recurring), nil
	case "refundability":
		return checkRefundability(a, e.refund), nil
	case "upfront_amount":
		return checkUpfrontAmount(a, e.upfront), nil
	case "recipient_role":
		return checkRecipientRole(a, e.role), nil
	case "task_authority":
		return checkTaskAuthority(a, in.taskAuthority, e.authority), nil
	case "counterparty_seen_before":
		return checkSeenBefore(a, r, e.seenBefore), nil
	case "channel_change":
		return checkChannelChange(a, e.channel), nil
	case "material_fields_changed":
		return checkFieldCount(check, "material_fields_changed", "material_fields_basis", a.MaterialFieldsChanged, a.MaterialFieldsBasis, e.material, a), nil
	case "offer_fields_changed":
		return checkFieldCount(check, "offer_fields_changed", "offer_fields_basis", a.OfferFieldsChanged, a.OfferFieldsBasis, e.offer, a), nil
	case "recipient_seen_before":
		return checkSeenBefore(a, r, e.recipientSeenBefore), nil
	case "action_class_gate":
		return checkActionClassGate(a, e.gate, e.tax)
	case "offer_expiry":
		return checkOfferExpiry(a, e.offerExpiry), nil
	case "price_floor":
		return checkPriceFloor(a, in.taskAuthority, in.opening, e.priceFloor), nil
	case "single_commitment":
		if in.sale != nil {
			r.h = MemoryHistory(in.sale.records())
			return checkSingleCommitment(a, r, checkInputs{history: true, historyIncomplete: in.sale.saleUnknown()},
				e.singleCommitment), nil
		}
		return checkSingleCommitment(a, r, in, e.singleCommitment), nil
	case "release_on_acceptance":
		if e.release.Binding != "" && in.sale != nil && in.sale.releaseWindow != nil {
			// A replayed sale's 1.2.0 reads the sale's history cut to the
			// live window (GuardEngine.check release_history).
			w := in.sale.releaseWindow
			r.h = MemoryHistory(w.records())
			return checkReleaseOnAcceptance(a, r, checkInputs{history: true, historyIncomplete: w.saleUnknown()}, e.release, e.tax), nil
		}
		return checkReleaseOnAcceptance(a, r, in, e.release, e.tax), nil
	case "promise_never":
		return checkPromiseNever(a, in.words, e.never), nil
	case "promise_requires_approval":
		return checkPromiseRequiresApproval(a, in.taskAuthority, in.authorization, e.approval, e.noApprovalRecords), nil
	case "required_disclosure":
		return checkRequiredDisclosure(a, r, e.disclosure), nil
	case "counterparty_list":
		return checkCounterpartyList(a, e.list), nil
	case "rate_cap":
		return checkRateCap(a, e.rateCap), nil
	}
	return Constraint{}, fmt.Errorf("check %q is configured and neither run nor pinned as unported", check)
}

// requirePerActionReads is caps.py require_per_action_reads: absent, or
// spend_authorized_minor.
func requirePerActionReads(s string) error {
	if s != "" && s != "spend_authorized_minor" {
		return fmt.Errorf("caps per_action_reads %q is not one of [spend_authorized_minor]", s)
	}
	return nil
}

// neverHeld is engine.py _NEVER_HELD_CHECKS: the checks a taxonomy
// mismatch never holds. credential_pattern and plan_containment read no
// action_class, and an integrity check refuses whatever the taxonomy says
// (dedupe, single_commitment and promise_never compare the class name as
// sealed). Every other check is keyed on the class, so a mismatch leaves it
// unevaluated (compareTaxonomy), including any check added later.
var neverHeld = []string{"dedupe", "verify_before_dispatch", "single_commitment", "promise_never", "release_on_acceptance",
	"credential_pattern", "plan_containment", "required_fields"}

// compareTaxonomy holds the action's taxonomy_version against the vendored
// action taxonomy's. A different version leaves every check that reads
// action_class not evaluable, naming both versions: a class drawn from one
// table is not evaluated against another, and never coerced. A deal record
// with an action_class and no taxonomy_version is not evaluable the same
// way. (A normalized --action with no taxonomy_version is read as today.)
// Where the versions agree, the class-keyed checks have read
// taxonomy_version too.
func (e *Engine) compareTaxonomy(a Action, cs []Constraint) []Constraint {
	var reason, shown string
	switch {
	case a.TaxonomyVersion != nil && *a.TaxonomyVersion != e.tax.version:
		reason = fmt.Sprintf("taxonomy_version %s on the action; the engine's action taxonomy is version %s: action_class is not evaluated against a different table", *a.TaxonomyVersion, e.tax.version)
		shown = fmt.Sprintf("%s (engine action taxonomy: %s)", *a.TaxonomyVersion, e.tax.version)
	case a.TaxonomyVersion == nil && a.fromRecord && a.ActionClass != nil:
		reason = fmt.Sprintf("the deal body has an action_class and no taxonomy_version; the engine's action taxonomy is version %s", e.tax.version)
		shown = fmt.Sprintf("absent (engine action taxonomy: %s)", e.tax.version)
	}
	out := make([]Constraint, len(cs))
	for i, c := range cs {
		out[i] = c
		// With no caps definition, caps reads nothing keyed on the class, so
		// a taxonomy mismatch does not hold it (engine.py check, e0e664d).
		if slices.Contains(neverHeld, c.ID) || c.ID == "caps" && e.capsFold == nil {
			continue
		}
		if reason != "" {
			n := notEvaluable(c.ID, c.Method, reason)
			if a.TaxonomyVersion != nil {
				// engine.py _taxonomy_held: TaxonomyHeldEvidence, the n/a
				// evidence with the versions it was held on. The engine's deal
				// bridge reads no action from a body with no taxonomy_version
				// (report/replay.py), so that hold has no engine evidence to
				// match and keeps the plain n/a.
				n.evidence = canon.Obj(canon.M("constraint_id", canon.Str(c.ID)), canon.M("in_scope", canon.Bool(true)),
					canon.M("missing_field", canon.Null()), canon.M("taxonomy_mismatch", canon.Obj(
						canon.M("record_taxonomy_version", canon.Str(*a.TaxonomyVersion)),
						canon.M("engine_taxonomy_version", canon.Str(e.tax.version)))))
			}
			n.read = &Read{Field: "taxonomy_version", Value: shown}
			n.reads = []string{"action_class", "taxonomy_version"}
			out[i] = n
		} else if a.TaxonomyVersion != nil {
			out[i].reads = append(slices.Clone(c.reads), "taxonomy_version")
		}
	}
	return out
}

// decide is engine.py _decide (8d7bff2): a clean run allows; when every
// failure is escalatable and the action's class has an approver_role in the
// action-class table, it escalates; any other failure denies. A failure is
// escalatable when its check is in escalatable or askChecks (the engine's
// escalatable |= ask_wickets, #113), an action_class_gate failure when
// gateAsks (gateFailuresAsk), and a failure the check marked asksApprover (a
// dedupe hit on the same act in another deal, capsule-engine #111).
func decide(cs []Constraint, hasApprover, gateAsks bool, askChecks map[string]bool) string {
	fails := 0
	allEscalatable := true
	for _, c := range cs {
		if c.Result == Fail {
			fails++
			allEscalatable = allEscalatable && (escalatable[c.ID] || askChecks[c.ID] || c.asksApprover || c.ID == "action_class_gate" && gateAsks)
		}
	}
	switch {
	case fails == 0:
		return Allow
	case allEscalatable && hasApprover:
		return Escalate
	}
	return Deny
}

// gateFailuresAsk is capsule-engine guards/engine.py _gate_failures_ask
// (c29e738): some action_class_gate outcome failed, and every failed one
// failed only on selectors in the ASK set (askGateSelectors). Its failing
// selectors are those it matched whose on_match is fail; a gate that failed
// closed (no class, or a class with no taxonomy row) names none and never
// asks.
func (e *Engine) gateFailuresAsk(cs []Constraint) bool {
	failed := false
	for _, c := range cs {
		if c.ID != "action_class_gate" || c.Result != Fail {
			continue
		}
		failed = true
		if c.gate == nil {
			return false
		}
		n := 0
		for _, sid := range c.gate.matched {
			if c.gate.onMatch[sid] != "fail" {
				continue
			}
			n++
			if !e.askGate[sid] {
				return false
			}
		}
		if n == 0 {
			return false
		}
	}
	return failed
}

// RecordFor is the ledger record a decision leaves, as the checks read it
// later (disposition per capsule.py:81-85).
func RecordFor(a Action, d Decision) Record {
	decision := map[string]string{Allow: "accept", Deny: "reject", Escalate: "needs_input"}[d.Outcome]
	// The engine's capsule records its action's amount: on a deal record,
	// the spend its bridge took (engineAmount).
	amount, _ := a.engineAmount()
	return Record{ActionID: a.ActionID, ActionType: a.ActionType, Operator: a.Operator, Developer: a.Developer,
		Timestamp: a.Timestamp, Decision: &decision, Target: a.Target, AmountMinor: amount,
		CounterpartyAccountRef: a.CounterpartyAccountRef, ActionClass: a.ActionClass, TaxonomyVersion: a.TaxonomyVersion,
		ReturnedMinor: a.ReturnedMinor, ReversesRef: a.ReversesRef, DealID: a.DealID, Currency: a.Currency,
		TaskAuthorityRef: a.TaskAuthorityRef, ItemRef: a.ItemRef, RepresentationClass: a.RepresentationClass,
		EquivalenceKeyDigest: sealedKeyOf(a)}
}

// sealedKeyOf is the equivalence key digest the engine's decision on a seals
// (capsule.py _payload_extension, #134), or nil when a carries no key.
func sealedKeyOf(a Action) *string {
	if a.EquivalenceKey == nil {
		return nil
	}
	// A JSON string always digests: canon refuses only non-finite numbers.
	d, _ := sealedEquivalenceKey(*a.EquivalenceKey)
	return &d
}

// checkUnported is a cited check this port does not run yet: n/a out of
// scope for an action class it does not apply to (as the engine's check
// is), and otherwise not_evaluable, never a pass.
func checkUnported(a Action, check string, u unportedCheck) (c Constraint) {
	defer func() { c.reads = []string{"action_class"} }()
	if !slices.Contains(u.actionClasses, a.class()) {
		return outOfScope(check, u.wicketID)
	}
	return notEvaluable(check, u.wicketID, NotYetRun)
}
