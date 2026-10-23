# SPDX-License-Identifier: Apache-2.0
"""`capsulectl engine check --ruleset FILE` (capsule-engine#162): the
cli-plugin/v1 handshake, the engine-ruleset/v0 format and its
definition_digest, and the external-check-result/v0 it prints."""
from __future__ import annotations

import copy
import hashlib
import importlib
import io
import json
from pathlib import Path

import pytest
from agent_action_capsule.canonical import json_digest
from jsonschema import Draft202012Validator

from capsule_engine.cli import engine_plugin
from capsule_engine.rulesets import Refusal, check, load_ruleset

# The module, not the function the package exports under the same name.
check_module = importlib.import_module("capsule_engine.rulesets.check")

REPO = Path(__file__).resolve().parents[2]
VENDOR = REPO / "python" / "capsule_engine" / "schemas" / "vendor"
# One envelope a real capsulectl v0.1.0-rc14 handed its pinned rules checker
# (that directory's PROVENANCE.json): three earlier acts, all money.purchase.
REAL_INPUT = REPO / "go" / "guard" / "testdata" / "real-fyi-act" / "purchase-commit-repeat.input.json"

KIND = "agent_input.body.action_class"


def clause(cid: str, kinds: list[str], disposition: str = "deny") -> dict:
    return {
        "id": cid,
        "check": "record_type_coverage/1",
        "disposition": disposition,
        "params": {"kind_field": KIND, "registered_kinds": kinds},
    }


def ruleset_doc(*clauses: dict) -> dict:
    return {"schema": "engine-ruleset/v0", "ruleset_id": "test/coverage/1.0.0", "clauses": list(clauses)}


def write(tmp_path: Path, doc: dict | str, name: str = "rules.yaml") -> Path:
    path = tmp_path / name
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return path


def envelope(classes: list[str], complete: bool = True) -> dict:
    history = [
        {"capsule_id": f"c{i}", "agent_input": {"body": {"action_class": c, "amount_minor": 100}}}
        for i, c in enumerate(classes)
    ]
    return {
        "schema": "external-check-input/v0",
        "record": {"capsule_id": "now", "agent_input": {"body": {"action_class": "money.purchase"}}},
        "history": history,
        "history_scope": {"days": 31, "max_records": 1000, "complete": complete},
    }


def run(tmp_path: Path, doc: dict, env: dict) -> dict:
    return check(load_ruleset(write(tmp_path, doc)), json.dumps(env).encode())


def result_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads((VENDOR / "external-check-result-v0.json").read_text()))


# -- vendored schemas ---------------------------------------------------------


def test_vendored_schemas_are_the_pinned_bytes():
    # schemas/vendor/README.md: capsule-cli v0.1.0-rc22, byte for byte.
    pins = {
        "external-check-input-v0.json": "468d77cc2492b3888385c582aaaa3a4c44df3eaf01ad6ef612338e6ddb380a39",
        "external-check-result-v0.json": "61e5ffe59b2abc8390e69228c7d7e5ba86562c6550a59008c15fb3990a24dd4d",
    }
    for name, digest in pins.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, name


# -- the handshake and the command ---------------------------------------------


def test_handshake_is_cli_plugin_v1(capsys):
    assert engine_plugin.main(["cli-plugin-metadata"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    meta = json.loads(out)
    assert meta["name"] == "engine"
    assert meta["plugin_api"] == "cli-plugin/v1"
    assert meta["subcommands"] == ["check"]


def test_check_prints_one_result_on_a_real_capsulectl_input(tmp_path, monkeypatch, capsys):
    path = write(tmp_path, ruleset_doc(clause("purchases-seen", ["money.purchase"])))
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(REAL_INPUT.read_bytes())))
    assert engine_plugin.main(["check", "--ruleset", str(path)]) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert out.out.count("\n") == 1
    result = json.loads(out.out)
    result_validator().validate(result)
    assert result["tier"] == "recomputed"
    assert result["verdict"] == "allow"
    assert result["definition_digest"] == json_digest(ruleset_doc(clause("purchases-seen", ["money.purchase"])))
    assert result["findings"] == [
        {"id": "purchases-seen", "check": "record_type_coverage/1", "verdict": "pass", "disposition": "deny", "limit": 1, "value": 1}
    ]


def test_a_refusal_exits_nonzero_with_nothing_on_stdout(tmp_path, monkeypatch, capsys):
    path = write(tmp_path, ruleset_doc(clause("a", ["money.purchase"])))
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"not json")))
    assert engine_plugin.main(["check", "--ruleset", str(path)]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.startswith("capsulectl engine check: the input is not one external-check-input/v0")


def test_check_needs_a_ruleset(capsys):
    with pytest.raises(SystemExit) as exc:
        engine_plugin.main(["check"])
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""


# -- the ruleset format and its digest ----------------------------------------


def test_the_digest_is_jcs_sha256_of_the_document_whatever_its_spelling(tmp_path):
    doc = ruleset_doc(clause("a", ["money.purchase", "money.refund"]))
    as_json = load_ruleset(write(tmp_path, doc, "r.json"))
    as_yaml = load_ruleset(
        write(
            tmp_path,
            "ruleset_id: test/coverage/1.0.0\n"
            "schema: engine-ruleset/v0\n"
            "clauses:\n"
            "  - params: {registered_kinds: [money.purchase, money.refund], kind_field: agent_input.body.action_class}\n"
            "    disposition: deny\n"
            "    check: record_type_coverage/1\n"
            "    id: a\n",
        )
    )
    expected = hashlib.sha256(
        b'{"clauses":[{"check":"record_type_coverage/1","disposition":"deny","id":"a","params":'
        b'{"kind_field":"agent_input.body.action_class","registered_kinds":["money.purchase","money.refund"]}}],'
        b'"ruleset_id":"test/coverage/1.0.0","schema":"engine-ruleset/v0"}'
    ).hexdigest()
    assert as_json.definition_digest == as_yaml.definition_digest == expected


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["clauses"][0]["params"]["registered_kinds"].append("money.refund"),
        lambda d: d["clauses"][0].update(disposition="ask"),
        lambda d: d["clauses"][0]["params"].update(kind_field="agent_input.body.action"),
        lambda d: d.update(ruleset_id="test/coverage/1.0.1"),
    ],
)
def test_any_change_is_a_new_digest(tmp_path, change):
    doc = ruleset_doc(clause("a", ["money.purchase"]))
    changed = copy.deepcopy(doc)
    change(changed)
    assert load_ruleset(write(tmp_path, doc, "a.json")).definition_digest != load_ruleset(
        write(tmp_path, changed, "b.json")
    ).definition_digest


@pytest.mark.parametrize(
    ("text", "says"),
    [
        (json.dumps({k: v for k, v in ruleset_doc(clause("a", ["x"])).items() if k != "schema"}), "is not engine-ruleset/v0"),
        (json.dumps({**ruleset_doc(clause("a", ["x"])), "schema": "engine-ruleset/v1"}), "is not engine-ruleset/v0"),
        (json.dumps({**ruleset_doc(clause("a", ["x"])), "extra": 1}), "$: additionalProperties"),
        (json.dumps(ruleset_doc({**clause("a", ["x"]), "weight": 1})), "$.clauses[0]: additionalProperties"),
        (json.dumps(ruleset_doc({**clause("a", ["x"]), "check": "caps/1"})), "$.clauses[0].check: enum"),
        (json.dumps(ruleset_doc({k: v for k, v in clause("a", ["x"]).items() if k != "disposition"})), "required"),
        (json.dumps(ruleset_doc()), "$.clauses: minItems"),
        (json.dumps(ruleset_doc(clause("a", ["x"]), clause("a", ["y"]))), "names a clause id more than once: a"),
        (json.dumps(ruleset_doc(clause("a", ["x", "x"]))), "uniqueItems"),
        (json.dumps(ruleset_doc(clause("a", [1.5]))), "registered_kinds[0]: type"),
        ("schema: engine-ruleset/v0\nruleset_id: 2026-10-23\nclauses: []\n", "$.ruleset_id: type"),
        ("schema: engine-ruleset/v0\nschema: engine-ruleset/v0\nruleset_id: x\nclauses: []\n", "appears twice"),
        ("[", "is not YAML or JSON"),
    ],
)
def test_a_ruleset_that_does_not_fit_is_refused(tmp_path, text, says):
    with pytest.raises(Refusal) as exc:
        load_ruleset(write(tmp_path, text))
    assert says in str(exc.value)


def test_a_missing_ruleset_is_refused(tmp_path):
    with pytest.raises(Refusal, match="cannot be read"):
        load_ruleset(tmp_path / "absent.yaml")


# -- findings and the verdict -------------------------------------------------


def test_an_exclusion_is_not_applicable_and_never_a_pass_or_a_verdict(tmp_path):
    result = run(tmp_path, ruleset_doc(clause("none", [])), envelope([]))
    assert result["findings"][0]["verdict"] == "not_applicable"
    assert result["verdict"] == "allow"


def test_a_kind_missing_from_a_complete_history_fails(tmp_path):
    result = run(tmp_path, ruleset_doc(clause("a", ["money.purchase", "money.refund"])), envelope(["money.purchase"]))
    (finding,) = result["findings"]
    assert finding["verdict"] == "fail"
    assert finding["reason"] == "not in the history: money.refund"
    assert (finding["limit"], finding["value"]) == (2, 1)
    assert result["verdict"] == "deny"


def test_an_incomplete_history_cannot_fail_a_kind_but_can_show_one(tmp_path):
    doc = ruleset_doc(clause("seen", ["money.purchase"]), clause("unseen", ["money.refund"]))
    result = run(tmp_path, doc, envelope(["money.purchase"], complete=False))
    assert [f["verdict"] for f in result["findings"]] == ["pass", "not_evaluable"]
    assert result["verdict"] == "not_evaluable"


def test_only_a_string_is_a_kind(tmp_path):
    env = envelope(["money.purchase"])
    env["history"].append({"capsule_id": "odd", "agent_input": {"body": {"action_class": {"nested": True}}}})
    env["history"].append({"capsule_id": "none", "agent_input": {}})
    result = run(tmp_path, ruleset_doc(clause("a", ["money.purchase"])), env)
    assert result["findings"][0]["verdict"] == "pass"


@pytest.mark.parametrize(
    ("clauses", "verdict"),
    [
        ([clause("a", ["money.refund"], "ask"), clause("b", ["money.refund"], "deny")], "deny"),
        ([clause("a", ["money.refund"], "ask"), clause("b", ["money.purchase"])], "escalate"),
        ([clause("a", ["money.purchase"]), clause("b", [])], "allow"),
    ],
)
def test_the_verdict_is_the_strictest_finding(tmp_path, clauses, verdict):
    assert run(tmp_path, ruleset_doc(*clauses), envelope(["money.purchase"]))["verdict"] == verdict


def test_a_failing_ask_clause_outranks_a_not_evaluable_one(tmp_path):
    doc = ruleset_doc(clause("a", ["money.refund"], "ask"), clause("b", ["money.refund"]))
    env = envelope(["money.purchase"], complete=True)
    assert run(tmp_path, doc, env)["verdict"] == "deny"
    env["history_scope"]["complete"] = False
    assert run(tmp_path, ruleset_doc(clause("a", ["money.refund"], "ask")), env)["verdict"] == "not_evaluable"


# -- the input ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "says"),
    [
        (b"[]", "is not external-check-input/v0"),
        (b'{"schema": "external-check-input/v1"}', "is not external-check-input/v0"),
        (b'{"schema": "external-check-input/v0", "history_scope": NaN}', "is not one external-check-input/v0"),
        (b"\xff", "is not one external-check-input/v0"),
    ],
)
def test_an_input_that_is_not_v0_is_refused(tmp_path, raw, says):
    with pytest.raises(Refusal, match=says):
        check(load_ruleset(write(tmp_path, ruleset_doc(clause("a", ["x"])))), raw)


def test_a_refusal_names_where_the_input_failed_never_a_value(tmp_path):
    env = envelope([])
    env["item_ref"] = "PRIVATE-VALUE-not-hex"
    env["history_scope"]["days"] = 0
    with pytest.raises(Refusal) as exc:
        check(load_ruleset(write(tmp_path, ruleset_doc(clause("a", ["x"])))), json.dumps(env).encode())
    message = str(exc.value)
    assert "$.item_ref: pattern" in message
    assert "$.history_scope.days: minimum" in message
    assert "PRIVATE" not in message


# -- capsule-cli#257's members ------------------------------------------------


def _with_limit_source(params, env):
    return check_module.Finding(verdict="pass", limit=1, value=1, limit_source="profile", profile_digest="ab" * 32)


def test_257_members_are_printed_only_once_the_vendored_schema_lists_them(tmp_path, monkeypatch):
    monkeypatch.setitem(check_module._CHECKS, "record_type_coverage/1", _with_limit_source)
    doc = ruleset_doc(clause("a", ["money.purchase"]))

    # The rc22 schema: capsulectl refuses either member, so neither is printed.
    finding = run(tmp_path, doc, envelope(["money.purchase"]))["findings"][0]
    assert "limit_source" not in finding and "profile_digest" not in finding

    # A schema that lists them (as capsule-cli#257 will): both are printed.
    schema = json.loads((VENDOR / "external-check-result-v0.json").read_text())
    props = schema["properties"]["findings"]["items"]["properties"]
    props["limit_source"] = {"type": "string"}
    props["profile_digest"] = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    real = check_module._validator

    def patched(name):
        return Draft202012Validator(schema) if name == "vendor/external-check-result-v0.json" else real(name)

    monkeypatch.setattr(check_module, "_validator", patched)
    finding = run(tmp_path, doc, envelope(["money.purchase"]))["findings"][0]
    assert finding["limit_source"] == "profile"
    assert finding["profile_digest"] == "ab" * 32


def test_the_example_ruleset_loads_and_checks_the_real_input():
    ruleset = load_ruleset(REPO / "examples" / "rulesets" / "everyday-history-coverage.yaml")
    result = check(ruleset, REAL_INPUT.read_bytes())
    assert [f["verdict"] for f in result["findings"]] == ["pass"]
    assert result["verdict"] == "allow"
