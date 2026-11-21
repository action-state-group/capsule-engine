// Command capsule-engine runs capsule-engine's Go evaluators for callers that
// are not Go. Today it has one command:
//
//	capsule-engine admission evaluate --pack FILE [--pack-id ID --digest SHA256] [--input FILE] [--admit FILE]
//
// It loads an admission pack (pack-definition.json), reads Inputs as JSON
// (the "input" of a conformance vector: conformance-vectors/admission/v0)
// from --input or stdin, and prints the decision as one JSON object.
//
// With --admit, the output also holds "admit": the decision as the body of
// an x-deal-v1 admit record, for the caller to seal in its own journal
// (capsule-engine#303). The file holds what only the caller has, as JSON:
// {"asks": [hex], "answers": [hex], "card_digest": hex}, the SHA-256 digests
// of its own ask and answer deal records of this admission and of the JCS
// bytes of the card document it read. The command draws the body's nonce
// from crypto/rand. "admit" is null for a decision without a tier (a NO),
// which is not sealed as an admit. "admits_deal" says whether an x-deal-v1
// agreement may cite the admit: true for GO, and for LIMIT at a tier that
// holds the deal's band; false for ASK, NO and any other LIMIT.
//
// Exit status: 0 when a decision was made, whatever its verdict (the
// verdict is in the output); 2 when it could not be: a usage error, an
// unreadable or refused pack, a pin that does not match, or unusable input.
// Nothing is printed to stdout in that case; the reason is one line on stderr.
package main

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/action-state-group/capsule-engine/go/admission"
	"github.com/action-state-group/capsule-engine/go/canon"
)

const exitUnusable = 2

func main() {
	os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr))
}

func run(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	if len(args) < 2 || args[0] != "admission" || args[1] != "evaluate" {
		fmt.Fprintln(stderr, "usage: capsule-engine admission evaluate --pack FILE [--pack-id ID --digest SHA256] [--input FILE] [--admit FILE]")
		return exitUnusable
	}
	fs := flag.NewFlagSet("admission evaluate", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	packPath := fs.String("pack", "", "admission pack-definition.json")
	packID := fs.String("pack-id", "", "the pack_id the pack must have (with --digest)")
	digest := fs.String("digest", "", "the definition_digest the pack must have (with --pack-id)")
	inputPath := fs.String("input", "", "inputs as JSON; stdin when empty or -")
	admitPath := fs.String("admit", "", "the caller's x-deal-v1 admit refs as JSON; prints the admit body")
	if err := fs.Parse(args[2:]); err != nil {
		fmt.Fprintln(stderr, "capsule-engine:", err)
		return exitUnusable
	}
	if fs.NArg() > 0 {
		fmt.Fprintln(stderr, "capsule-engine: unexpected argument", fs.Arg(0))
		return exitUnusable
	}
	if err := evaluate(*packPath, *packID, *digest, *inputPath, *admitPath, stdin, stdout); err != nil {
		fmt.Fprintln(stderr, "capsule-engine:", err)
		return exitUnusable
	}
	return 0
}

func evaluate(packPath, packID, digest, inputPath, admitPath string, stdin io.Reader, stdout io.Writer) error {
	if packPath == "" {
		return errors.New("--pack is required")
	}
	if (packID == "") != (digest == "") {
		return errors.New("--pack-id and --digest go together")
	}
	src, err := os.ReadFile(packPath)
	if err != nil {
		return err
	}
	var p *admission.Pack
	if packID != "" {
		p, err = admission.LoadPinned(src, packID, digest)
	} else {
		p, err = admission.Load(src)
	}
	if err != nil {
		return err
	}
	var raw []byte
	if inputPath == "" || inputPath == "-" {
		raw, err = io.ReadAll(stdin)
	} else {
		raw, err = os.ReadFile(inputPath)
	}
	if err != nil {
		return err
	}
	in, err := admission.DecodeInputs(raw)
	if err != nil {
		return err
	}
	var refs *admission.AdmitRefs
	if admitPath != "" {
		if refs, err = readAdmitRefs(admitPath); err != nil {
			return err
		}
	}
	out, err := encode(admission.Evaluate(p, in), refs)
	if err != nil {
		return err
	}
	_, err = stdout.Write(out)
	return err
}

// output is the decision as printed: the decision's own members, the pack it
// cites, and the admission_decision block (as JCS text) with its SHA-256,
// which is what the shared vectors compare.
type output struct {
	Verdict     string  `json:"verdict"`
	Source      string  `json:"source"`
	Tier        *string `json:"tier"`
	Band        *band   `json:"band"`
	Rows        []row   `json:"rows"`
	Asks        []ask   `json:"asks"`
	Pack        packRef `json:"pack"`
	Block       string  `json:"block"`
	BlockSHA256 string  `json:"block_sha256"`
	// Admit is the x-deal-v1 admit body, present only with --admit: null
	// for a decision without a tier. AdmitsDeal, also only with --admit,
	// says whether an agreement may cite it: GO, or LIMIT at a tier that
	// holds the deal.
	Admit      json.RawMessage `json:"admit,omitempty"`
	AdmitsDeal *bool           `json:"admits_deal,omitempty"`
}

type band struct {
	Currency string `json:"currency"`
	Decade   int    `json:"decade"`
}

type row struct {
	Row         string `json:"row"`
	Result      string `json:"result"`
	Effect      string `json:"effect"`
	Count       *int64 `json:"count,omitempty"`
	Denominator *int64 `json:"denominator,omitempty"`
}

type ask struct {
	Row        string `json:"row"`
	Derivation string `json:"derivation"`
	DeadlineS  int64  `json:"deadline_s"`
}

type packRef struct {
	ID     string `json:"id"`
	Digest string `json:"digest"`
}

func encode(d admission.Decision, refs *admission.AdmitRefs) ([]byte, error) {
	jcs, err := canon.JCS(d.Block())
	if err != nil {
		return nil, err
	}
	sum := sha256.Sum256([]byte(jcs))
	o := output{Verdict: d.Verdict, Source: d.Source, Rows: []row{}, Asks: []ask{},
		Pack: packRef{ID: d.Pack.ID, Digest: d.Pack.Digest}, Block: jcs, BlockSHA256: hex.EncodeToString(sum[:])}
	if d.Tier != "" {
		o.Tier = &d.Tier
	}
	if d.Band != nil {
		o.Band = &band{Currency: d.Band.Currency, Decade: d.Band.Decade}
	}
	for _, r := range d.Rows {
		o.Rows = append(o.Rows, row{Row: r.Row, Result: r.Result, Effect: r.Effect, Count: r.Count, Denominator: r.Denominator})
	}
	for _, a := range d.Asks {
		o.Asks = append(o.Asks, ask{Row: a.Row, Derivation: a.Derivation, DeadlineS: a.DeadlineS})
	}
	if refs != nil {
		o.AdmitsDeal = &d.AdmitsDeal
		body, err := d.Admit(*refs)
		switch {
		case errors.Is(err, admission.ErrNoTier):
			o.Admit = json.RawMessage("null")
		case err != nil:
			return nil, err
		default:
			s, err := canon.JCS(body)
			if err != nil {
				return nil, err
			}
			o.Admit = json.RawMessage(s)
		}
	}
	b, err := json.Marshal(o)
	return append(b, '\n'), err
}

// admitFile is the --admit file: the members of the admit body only the
// caller holds.
type admitFile struct {
	Asks       []string `json:"asks"`
	Answers    []string `json:"answers"`
	CardDigest string   `json:"card_digest"`
}

// readAdmitRefs reads the --admit file, refusing unknown members and a
// missing one, and draws a fresh nonce for the body.
func readAdmitRefs(path string) (*admission.AdmitRefs, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	var f admitFile
	if err := dec.Decode(&f); err != nil {
		return nil, fmt.Errorf("--admit: %w", err)
	}
	if dec.More() {
		return nil, errors.New("--admit: more than one JSON value")
	}
	if f.Asks == nil || f.Answers == nil || f.CardDigest == "" {
		return nil, errors.New("--admit: asks, answers and card_digest are required")
	}
	var n [32]byte
	if _, err := rand.Read(n[:]); err != nil {
		return nil, err
	}
	return &admission.AdmitRefs{Asks: f.Asks, Answers: f.Answers, CardDigest: f.CardDigest, Nonce: hex.EncodeToString(n[:])}, nil
}
