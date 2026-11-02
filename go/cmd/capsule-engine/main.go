// Command capsule-engine runs capsule-engine's Go evaluators for callers that
// are not Go. Today it has one command:
//
//	capsule-engine admission evaluate --pack FILE [--pack-id ID --digest SHA256] [--input FILE]
//
// It loads an admission pack (pack-definition.json), reads Inputs as JSON
// (the "input" of a conformance vector: conformance-vectors/admission/v0)
// from --input or stdin, and prints the decision as one JSON object.
//
// Exit status: 0 when a decision was made, whatever its verdict (the
// verdict is in the output); 2 when it could not be: a usage error, an
// unreadable or refused pack, a pin that does not match, or unusable input.
// Nothing is printed to stdout in that case; the reason is one line on stderr.
package main

import (
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
		fmt.Fprintln(stderr, "usage: capsule-engine admission evaluate --pack FILE [--pack-id ID --digest SHA256] [--input FILE]")
		return exitUnusable
	}
	fs := flag.NewFlagSet("admission evaluate", flag.ContinueOnError)
	fs.SetOutput(stderr)
	packPath := fs.String("pack", "", "admission pack-definition.json")
	packID := fs.String("pack-id", "", "the pack_id the pack must have (with --digest)")
	digest := fs.String("digest", "", "the definition_digest the pack must have (with --pack-id)")
	inputPath := fs.String("input", "", "inputs as JSON; stdin when empty or -")
	if err := fs.Parse(args[2:]); err != nil {
		return exitUnusable
	}
	if err := evaluate(*packPath, *packID, *digest, *inputPath, stdin, stdout); err != nil {
		fmt.Fprintln(stderr, "capsule-engine:", err)
		return exitUnusable
	}
	return 0
}

func evaluate(packPath, packID, digest, inputPath string, stdin io.Reader, stdout io.Writer) error {
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
	out, err := encode(admission.Evaluate(p, in))
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

func encode(d admission.Decision) ([]byte, error) {
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
	b, err := json.Marshal(o)
	return append(b, '\n'), err
}
