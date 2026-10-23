package pack

import (
	"bytes"
	"fmt"
	"io/fs"
	"path"

	"gopkg.in/yaml.v3"

	"github.com/action-state-group/capsule-engine/go/canon"
)

// FoldRead is one declared read of a fold. HasDefault is true when the
// definition supplies a default for an absent field.
type FoldRead struct {
	Path, ErasureClass string
	HasDefault         bool
	// Default is the declared default when HasDefault.
	Default yaml.Node
}

// FoldFilter is one filter clause of a fold. Value is kept as YAML so the
// evaluator decides which shapes it supports.
type FoldFilter struct {
	Field, Op string
	Value     yaml.Node
}

// Fold is one fold definition (python/capsule_engine/folds/definition.py), with the
// digest capsule-engine computes over its canonical form.
type Fold struct {
	ID, Digest                 string
	Reads                      []FoldRead
	Filter                     []FoldFilter
	Key                        string
	WindowMode, WindowDuration string
	WindowStart, WindowEnd     string
	Reducer, ReduceField, Emit string
	// Reversal is the fold's reversal clause (spend.weekly/3.0.0), or nil.
	Reversal *FoldReversal
}

// FoldReversal is folds/definition.py Reversal: records whose class_field
// is in Classes take back a charge they cite instead of adding to the sum.
type FoldReversal struct {
	ClassField    string   `yaml:"class_field"`
	Classes       []string `yaml:"classes"`
	ParentField   string   `yaml:"parent_field"`
	RelationField string   `yaml:"relation_field"`
	Relation      string   `yaml:"relation"`
}

type rawFoldRead struct {
	Path         string `yaml:"path"`
	ErasureClass string `yaml:"erasure_class"`
	// Default is set (Kind != 0) when the read declares one. A *yaml.Node
	// field does not decode a scalar like `false` under yaml.v3.
	Default yaml.Node `yaml:"default"`
}

type rawFoldFilter struct {
	Field string    `yaml:"field"`
	Op    string    `yaml:"op"`
	Value yaml.Node `yaml:"value"`
}

type rawFoldWindow struct {
	Mode     string `yaml:"mode"`
	Duration string `yaml:"duration"`
	Start    string `yaml:"start"`
	End      string `yaml:"end"`
}

type rawFoldReduce struct {
	Reducer string `yaml:"reducer"`
	Field   string `yaml:"field"`
}

type rawFoldDef struct {
	FoldID string          `yaml:"fold_id"`
	Reads  []rawFoldRead   `yaml:"reads"`
	Filter []rawFoldFilter `yaml:"filter"`
	Key    *string         `yaml:"key"`
	Window *rawFoldWindow  `yaml:"window"`
	Reduce rawFoldReduce   `yaml:"reduce"`
	Emit   string          `yaml:"emit"`
	// Reversal is emitted into the canonical form only when set
	// (definition.py canonical_dict at c7fc3ac), so a fold without one keeps
	// its digest.
	Reversal *FoldReversal `yaml:"reversal"`
}

// canonicalFold is FoldDefinition.canonical_dict at capsule-engine
// 559288d0f61a (capsule_engine/folds/definition.py:149-180).
func canonicalFold(f rawFoldDef) (canon.Value, error) {
	reads := make([]canon.Value, 0, len(f.Reads))
	for _, r := range f.Reads {
		members := []canon.Member{canon.M("path", canon.Str(r.Path)), canon.M("erasure_class", canon.Str(r.ErasureClass))}
		if r.Default.Kind != 0 {
			d, err := canon.FromYAML(&r.Default)
			if err != nil {
				return canon.Value{}, err
			}
			members = append(members, canon.M("default", d))
		}
		reads = append(reads, canon.Obj(members...))
	}
	reduce := []canon.Member{canon.M("reducer", canon.Str(f.Reduce.Reducer))}
	if f.Reduce.Field != "" {
		reduce = append(reduce, canon.M("field", canon.Str(f.Reduce.Field)))
	}
	members := []canon.Member{
		canon.M("fold_id", canon.Str(f.FoldID)),
		canon.M("reads", canon.Arr(reads)),
		canon.M("reduce", canon.Obj(reduce...)),
		canon.M("emit", canon.Str(f.Emit)),
	}
	if len(f.Filter) > 0 {
		filters := make([]canon.Value, 0, len(f.Filter))
		for _, fl := range f.Filter {
			v, err := canon.FromYAML(&fl.Value)
			if err != nil {
				return canon.Value{}, err
			}
			filters = append(filters, canon.Obj(canon.M("field", canon.Str(fl.Field)), canon.M("op", canon.Str(fl.Op)), canon.M("value", v)))
		}
		members = append(members, canon.M("filter", canon.Arr(filters)))
	}
	if f.Key != nil {
		members = append(members, canon.M("key", canon.Str(*f.Key)))
	}
	if f.Window != nil {
		w := []canon.Member{canon.M("mode", canon.Str(f.Window.Mode))}
		for _, kv := range [][2]string{{"duration", f.Window.Duration}, {"start", f.Window.Start}, {"end", f.Window.End}} {
			if kv[1] != "" {
				w = append(w, canon.M(kv[0], canon.Str(kv[1])))
			}
		}
		members = append(members, canon.M("window", canon.Obj(w...)))
	}
	if r := f.Reversal; r != nil {
		members = append(members, canon.M("reversal", canon.Obj(
			canon.M("class_field", canon.Str(r.ClassField)), canon.M("classes", canon.Strs(r.Classes)),
			canon.M("parent_field", canon.Str(r.ParentField)), canon.M("relation_field", canon.Str(r.RelationField)),
			canon.M("relation", canon.Str(r.Relation)))))
	}
	return canon.Obj(members...), nil
}

// loadFolds reads every *.yaml fold definition in dir of fsys and computes its
// digest. A missing folds directory is an empty catalog.
func loadFolds(fsys fs.FS, dir string) (map[string]Fold, error) {
	entries, err := fs.ReadDir(fsys, dir)
	if err != nil {
		return map[string]Fold{}, nil
	}
	out := make(map[string]Fold, len(entries))
	for _, e := range entries {
		if e.IsDir() || path.Ext(e.Name()) != ".yaml" {
			continue
		}
		data, err := fs.ReadFile(fsys, path.Join(dir, e.Name()))
		if err != nil {
			return nil, err
		}
		var raw rawFoldDef
		dec := yaml.NewDecoder(bytes.NewReader(data))
		dec.KnownFields(true)
		if err := dec.Decode(&raw); err != nil {
			return nil, fmt.Errorf("fold %s: %w", e.Name(), err)
		}
		cv, err := canonicalFold(raw)
		if err != nil {
			return nil, fmt.Errorf("fold %s: %w", e.Name(), err)
		}
		d, err := canon.Digest(cv)
		if err != nil {
			return nil, fmt.Errorf("fold %s: %w", e.Name(), err)
		}
		if _, dup := out[raw.FoldID]; dup {
			return nil, fmt.Errorf("fold_id %q defined twice", raw.FoldID)
		}
		f := Fold{ID: raw.FoldID, Digest: d, Reducer: raw.Reduce.Reducer, ReduceField: raw.Reduce.Field, Emit: raw.Emit, Reversal: raw.Reversal}
		for _, r := range raw.Reads {
			read := FoldRead{Path: r.Path, ErasureClass: r.ErasureClass, HasDefault: r.Default.Kind != 0, Default: r.Default}
			f.Reads = append(f.Reads, read)
		}
		for _, fl := range raw.Filter {
			f.Filter = append(f.Filter, FoldFilter{Field: fl.Field, Op: fl.Op, Value: fl.Value})
		}
		if raw.Key != nil {
			f.Key = *raw.Key
		}
		if raw.Window != nil {
			f.WindowMode, f.WindowDuration, f.WindowStart, f.WindowEnd = raw.Window.Mode, raw.Window.Duration, raw.Window.Start, raw.Window.End
		}
		out[raw.FoldID] = f
	}
	return out, nil
}
