# Dev tool, not shipped: regenerates python-totals.json, capsule-engine's own
# weekly_spend_minor for each case of corpus.json (the spend.weekly/3.0.0
# differential test). Run it from capsule-engine checked out read-only at
# the commit named in the output, with the vendored fold definition:
#
#   cd <capsule-engine@SHA> && <venv>/bin/python <this file> <SHA> <corpus.json> <spend.weekly.v3.yaml> > python-totals.json
#   (and <corpus-3.1.0.json> <spend.weekly.v3.1.yaml> > python-totals-3.1.0.json, at a710636c)
#
# The fold is loaded from the vendored file, not the engine's catalog, and
# its digest is printed, so the totals are for exactly the definition this
# repository carries.
import json
import sys
from pathlib import Path

from capsule_engine.folds.engine import evaluate_one
from capsule_engine.folds.loader import load_definition_file


def main(sha: str, corpus_path: str, fold_path: str) -> None:
    corpus = json.loads(Path(corpus_path).read_text())
    definition = load_definition_file(Path(fold_path))
    assert definition.fold_id == corpus["fold"], definition.fold_id
    out = {"capsule_engine_commit": sha, "fold": definition.fold_id, "fold_digest": definition.definition_digest(), "totals": {}}
    for case in corpus["cases"]:
        trace = evaluate_one(definition, case["records"], key_value=corpus["key_value"], as_of=corpus["as_of"])
        out["totals"][case["name"]] = {"weekly_spend_minor": trace.result or 0, "reversals": trace.reversals}
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(*sys.argv[1:])
