package admission

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"time"
)

// wireInputs is Inputs as JSON writes it: a conformance vector's input, and
// what `capsule-engine admission evaluate` reads.
type wireInputs struct {
	Now      string `json:"now"`
	Amount   string `json:"amount"`
	Currency string `json:"currency"`
	Platform struct {
		ReleaseAttestation string `json:"release_attestation"`
		OwnerSigned        *bool  `json:"owner_signed"`
	} `json:"platform"`
	Card struct {
		State              string `json:"state"`
		FirstCheckpointAt  string `json:"first_checkpoint_at"`
		LatestCheckpointAt string `json:"latest_checkpoint_at"`
		Witness            string `json:"witness"`
		Controller         string `json:"controller"`
		Claims             *struct {
			Held  int64 `json:"held"`
			Total int64 `json:"total"`
		} `json:"claims"`
		Disputes *struct {
			Open int64 `json:"open"`
			Of   int64 `json:"of"`
		} `json:"disputes"`
		Bands *[]struct {
			Currency string `json:"currency"`
			Decade   int    `json:"decade"`
			Subjects int64  `json:"subjects"`
			Adverse  int64  `json:"adverse"`
		} `json:"bands"`
	} `json:"card"`
	Selection struct {
		State              string   `json:"state"`
		UnansweredDisputes []string `json:"unanswered_disputes"`
	} `json:"selection"`
	Own *struct {
		Exchanges   int64 `json:"exchanges"`
		Settled     int64 `json:"settled"`
		HeldHalves  int64 `json:"held_halves"`
		Conflicting int64 `json:"conflicting"`
		Verdicts    []struct {
			At      string `json:"at"`
			Adverse bool   `json:"adverse"`
		} `json:"verdicts"`
	} `json:"own"`
	Operator struct {
		DenyListed  bool  `json:"deny_listed"`
		AllowListed *bool `json:"allow_listed"`
	} `json:"operator"`
	Vouches []struct {
		Controller string `json:"controller"`
	} `json:"vouches"`
	Stake *struct {
		Amount   string `json:"amount"`
		Currency string `json:"currency"`
		Rail     struct {
			ID     string `json:"id"`
			Digest string `json:"digest"`
		} `json:"rail"`
	} `json:"stake"`
	Subject string `json:"subject"`
	Judged  map[string]struct {
		Met   bool   `json:"met"`
		Judge string `json:"judge"`
	} `json:"judged"`
	Asks     map[string]string `json:"asks"`
	AsksMade int               `json:"asks_made"`
}

// DecodeInputs reads Inputs from their JSON form. Unknown members and
// unparsable times are refused, so a misspelt input is never read as absent.
func DecodeInputs(raw []byte) (Inputs, error) {
	if t := bytes.TrimSpace(raw); len(t) == 0 || t[0] != '{' {
		return Inputs{}, errors.New("admission input: not a JSON object")
	}
	var v wireInputs
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&v); err != nil {
		return Inputs{}, fmt.Errorf("admission input: %w", err)
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		return Inputs{}, errors.New("admission input: trailing data after the JSON object")
	}
	var perr error
	at := func(s string) time.Time {
		if s == "" {
			return time.Time{}
		}
		t, err := time.Parse(time.RFC3339, s)
		if err != nil && perr == nil {
			perr = fmt.Errorf("admission input: time %q is not RFC 3339", s)
		}
		return t
	}
	in := Inputs{
		Now: at(v.Now), Amount: v.Amount, Currency: v.Currency,
		Platform: Platform{ReleaseAttestation: v.Platform.ReleaseAttestation, OwnerSigned: v.Platform.OwnerSigned},
		Card: Card{State: v.Card.State, FirstCheckpointAt: at(v.Card.FirstCheckpointAt),
			LatestCheckpointAt: at(v.Card.LatestCheckpointAt), Witness: v.Card.Witness, Controller: v.Card.Controller},
		Selection: Selection{State: v.Selection.State},
		Operator:  Operator{DenyListed: v.Operator.DenyListed, AllowListed: v.Operator.AllowListed},
		Subject:   v.Subject, Asks: v.Asks, AsksMade: v.AsksMade,
	}
	if v.Card.Claims != nil {
		in.Card.Claims = &Claims{Held: v.Card.Claims.Held, Total: v.Card.Claims.Total}
	}
	if v.Card.Disputes != nil {
		in.Card.Disputes = &Disputes{Open: v.Card.Disputes.Open, Of: v.Card.Disputes.Of}
	}
	if v.Card.Bands != nil {
		in.Card.Bands = []BandCount{}
		for _, b := range *v.Card.Bands {
			in.Card.Bands = append(in.Card.Bands, BandCount{Currency: b.Currency, Decade: b.Decade, Subjects: b.Subjects, Adverse: b.Adverse})
		}
	}
	for _, s := range v.Selection.UnansweredDisputes {
		in.Selection.UnansweredDisputes = append(in.Selection.UnansweredDisputes, at(s))
	}
	if v.Judged != nil {
		in.Judged = map[string]Judgement{}
		for row, j := range v.Judged {
			in.Judged[row] = Judgement{Met: j.Met, Judge: j.Judge}
		}
	}
	if v.Own != nil {
		in.Own = &OwnHistory{Exchanges: v.Own.Exchanges, Settled: v.Own.Settled, HeldHalves: v.Own.HeldHalves, Conflicting: v.Own.Conflicting}
		for _, vd := range v.Own.Verdicts {
			in.Own.Verdicts = append(in.Own.Verdicts, Verdict{At: at(vd.At), Adverse: vd.Adverse})
		}
	}
	if v.Stake != nil {
		in.Stake = &Stake{Amount: v.Stake.Amount, Currency: v.Stake.Currency,
			Rail: RailPin{ID: v.Stake.Rail.ID, Digest: v.Stake.Rail.Digest}}
	}
	for _, vc := range v.Vouches {
		in.Vouches = append(in.Vouches, Vouch{Controller: vc.Controller})
	}
	if perr != nil {
		return Inputs{}, perr
	}
	return in, nil
}
