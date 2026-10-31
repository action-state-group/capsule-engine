// SPDX-License-Identifier: Apache-2.0

//! The pack loader (go/pack pack.go Load and loadFrom, fold.go): reads a
//! pack.yaml, resolves each wicket and fold citation against the definitions
//! shipped beside it at exactly the cited digest, checks its taxonomy pin
//! against the vendored table, and computes the definition digest, which a
//! pack naming a vendored pack_id must match. The files are the Go port's
//! embedded tree (go/packs), embedded here from the same paths so there is
//! one source of truth.

use std::collections::BTreeMap;

use super::taxonomy::Taxonomy;
use super::Error;
use crate::canon::{self, Value};
use crate::yaml::{self, Fields, Node};

/// One embedded pack version directory: its pack.yaml, the definitions it
/// ships, and its PROVENANCE.json sidecar (go/packs/<dir>).
pub(crate) struct Embedded {
    pub(crate) dir: &'static str,
    pub(crate) pack_yaml: &'static str,
    pub(crate) wickets: &'static [(&'static str, &'static str)],
    pub(crate) folds: &'static [(&'static str, &'static str)],
    pub(crate) provenance: &'static str,
}

/// go/packs/inference-0.1.0, asg/inference/0.1.0.
pub(crate) const INFERENCE_0_1_0: Embedded = Embedded {
    dir: "inference-0.1.0",
    pack_yaml: include_str!("../../../../go/packs/inference-0.1.0/pack.yaml"),
    wickets: &[
        ("counterparty_list.yaml", include_str!("../../../../go/packs/inference-0.1.0/wickets/counterparty_list.yaml")),
        ("dedupe.yaml", include_str!("../../../../go/packs/inference-0.1.0/wickets/dedupe.yaml")),
        ("rate_cap.yaml", include_str!("../../../../go/packs/inference-0.1.0/wickets/rate_cap.yaml")),
    ],
    folds: &[(
        "spend.weekly.v3.1.yaml",
        include_str!("../../../../go/packs/inference-0.1.0/folds/spend.weekly.v3.1.yaml"),
    )],
    provenance: include_str!("../../../../go/packs/inference-0.1.0/PROVENANCE.json"),
};

/// go/packs/engine/action_taxonomy.json, the vendored action taxonomy, and
/// its PROVENANCE.json.
pub(crate) const ACTION_TAXONOMY: &str = include_str!("../../../../go/packs/engine/action_taxonomy.json");
const TAXONOMY_PROVENANCE: &str = include_str!("../../../../go/packs/engine/PROVENANCE.json");

/// go/pack defaultDispositions.
const DEFAULT_DISPOSITIONS: [&str; 3] = ["DO", "ASK", "NEVER"];

/// One rule of a pack (go/pack Rule), for the obligations this port reads:
/// its id, the check it is measured by, and its default disposition.
#[derive(Clone, Debug)]
pub(crate) struct Rule {
    pub(crate) check: String,
    pub(crate) default_disposition: String,
}

/// One cited wicket definition (go/pack Wicket): its config, for the check
/// that implements it to decode.
#[derive(Clone, Debug)]
pub(crate) struct Wicket {
    pub(crate) id: String,
    pub(crate) check: String,
    pub(crate) digest: String,
    pub(crate) config: Node,
}

/// One cited fold definition: its id and digest. No check of the inference
/// pack reads a fold, so nothing else of it is kept.
#[derive(Clone, Debug)]
pub(crate) struct Fold {
    pub(crate) id: String,
    pub(crate) digest: String,
}

/// One action type a pack governs, and the normalized fields it requires
/// (go/pack ActionSemantic).
#[derive(Clone, Debug)]
pub(crate) struct ActionSemantic {
    pub(crate) action_type: String,
    pub(crate) required_fields: Vec<String>,
}

/// A loaded pack (go/pack Pack).
#[derive(Clone, Debug)]
pub(crate) struct Pack {
    pub(crate) id: String,
    pub(crate) definition_digest: String,
    pub(crate) rules: Vec<Rule>,
    /// The cited wickets, in pack constraint order.
    pub(crate) wickets: Vec<Wicket>,
    pub(crate) folds: Vec<Fold>,
    pub(crate) action_semantics: Vec<ActionSemantic>,
    pub(crate) taxonomy: Taxonomy,
}

impl Pack {
    /// go/pack Pack.Wicket: the cited wicket configuring check.
    pub(crate) fn wicket(&self, check: &str) -> Option<&Wicket> {
        self.wickets.iter().find(|w| w.check == check)
    }
}

fn load_err(dir: &str, m: impl std::fmt::Display) -> Error {
    Error::Load(format!("pack in {dir}: {m}"))
}

fn digest_of(v: &Value, what: &str) -> Result<String, String> {
    canon::digest(v).map_err(|e| format!("{what}: {e}"))
}

/// go/pack loadWickets: every wicket definition and its digest, JSON-DIGEST
/// of {wicket_id, check, config}, plus semantics when the definition states
/// it.
fn load_wickets(files: &[(&str, &str)]) -> Result<BTreeMap<String, Wicket>, String> {
    let mut out = BTreeMap::new();
    for (name, data) in files {
        let node = yaml::parse(data).map_err(|e| format!("wicket {name}: {e}"))?;
        let mut f = Fields::of(&node, format!("wicket {name}"))?;
        let id = f.str("wicket_id")?;
        let check = f.str("check")?;
        let config = f.get("config").cloned().unwrap_or(Node::Null);
        let semantics = f.opt_str("semantics")?;
        f.done()?;
        let mut members = vec![("wicket_id".to_string(), canon::s(&id)), ("check".to_string(), canon::s(&check))];
        members.push(("config".to_string(), config.to_canon()));
        if let Some(sem) = semantics {
            if sem.trim().is_empty() {
                return Err(format!("wicket {name}: semantics must be a non-empty string"));
            }
            members.push(("semantics".to_string(), canon::s(sem)));
        }
        let digest = digest_of(&Value::Obj(members), &format!("wicket {name}"))?;
        if out.contains_key(&id) {
            return Err(format!("wicket_id {id:?} defined twice"));
        }
        out.insert(id.clone(), Wicket { id, check, digest, config });
    }
    Ok(out)
}

/// go/pack canonicalFold (capsule_engine/folds/definition.py
/// FoldDefinition.canonical_dict) and loadFolds: every fold definition and
/// its digest.
fn load_folds(files: &[(&str, &str)]) -> Result<BTreeMap<String, Fold>, String> {
    let mut out = BTreeMap::new();
    for (name, data) in files {
        let what = format!("fold {name}");
        let node = yaml::parse(data).map_err(|e| format!("{what}: {e}"))?;
        let mut f = Fields::of(&node, what.clone())?;
        let id = f.str("fold_id")?;
        let mut reads = Vec::new();
        for r in f.seq("reads")? {
            let mut rf = Fields::of(r, format!("{what} reads[]"))?;
            let mut members = vec![("path".to_string(), canon::s(rf.str("path")?))];
            members.push(("erasure_class".to_string(), canon::s(rf.str("erasure_class")?)));
            if let Some(d) = rf.get("default") {
                members.push(("default".to_string(), d.to_canon()));
            }
            rf.done()?;
            reads.push(Value::Obj(members));
        }
        let mut filters = Vec::new();
        for fl in f.seq("filter")? {
            let mut ff = Fields::of(fl, format!("{what} filter[]"))?;
            let field = ff.str("field")?;
            let op = ff.str("op")?;
            let value = ff.get("value").map_or(Value::Null, Node::to_canon);
            ff.done()?;
            filters.push(canon::obj([("field", canon::s(field)), ("op", canon::s(op)), ("value", value)]));
        }
        let key = f.opt_str("key")?;
        let window = match f.get("window") {
            None | Some(Node::Null) => None,
            Some(w) => {
                let mut wf = Fields::of(w, format!("{what} window"))?;
                let mut members = vec![("mode".to_string(), canon::s(wf.str("mode")?))];
                for k in ["duration", "start", "end"] {
                    let v = wf.str(k)?;
                    if !v.is_empty() {
                        members.push((k.to_string(), canon::s(v)));
                    }
                }
                wf.done()?;
                Some(Value::Obj(members))
            }
        };
        let reduce = {
            let node = f.get("reduce").cloned().unwrap_or(Node::Map(Vec::new()));
            let mut rf = Fields::of(&node, format!("{what} reduce"))?;
            let mut members = vec![("reducer".to_string(), canon::s(rf.str("reducer")?))];
            let field = rf.str("field")?;
            if !field.is_empty() {
                members.push(("field".to_string(), canon::s(field)));
            }
            rf.done()?;
            Value::Obj(members)
        };
        let emit = f.str("emit")?;
        let reversal = match f.get("reversal") {
            None | Some(Node::Null) => None,
            Some(r) => {
                let mut rf = Fields::of(r, format!("{what} reversal"))?;
                let v = canon::obj([
                    ("class_field", canon::s(rf.str("class_field")?)),
                    ("classes", canon::strs(&rf.opt_strs("classes")?.unwrap_or_default())),
                    ("parent_field", canon::s(rf.str("parent_field")?)),
                    ("relation_field", canon::s(rf.str("relation_field")?)),
                    ("relation", canon::s(rf.str("relation")?)),
                ]);
                rf.done()?;
                Some(v)
            }
        };
        f.done()?;
        let mut members = vec![
            ("fold_id".to_string(), canon::s(&id)),
            ("reads".to_string(), Value::Arr(reads)),
            ("reduce".to_string(), reduce),
            ("emit".to_string(), canon::s(emit)),
        ];
        if !filters.is_empty() {
            members.push(("filter".to_string(), Value::Arr(filters)));
        }
        if let Some(k) = key {
            members.push(("key".to_string(), canon::s(k)));
        }
        if let Some(w) = window {
            members.push(("window".to_string(), w));
        }
        if let Some(r) = reversal {
            members.push(("reversal".to_string(), r));
        }
        let digest = digest_of(&Value::Obj(members), &what)?;
        if out.contains_key(&id) {
            return Err(format!("fold_id {id:?} defined twice"));
        }
        out.insert(id.clone(), Fold { id, digest });
    }
    Ok(out)
}

/// The fields of PROVENANCE.json this loader holds the files to.
struct Provenance {
    pack_id: String,
    engine_definition_digest: String,
    wicket_digests: BTreeMap<String, String>,
    fold_digests: BTreeMap<String, String>,
    file_sha256: BTreeMap<String, String>,
}

fn read_provenance(raw: &str) -> Result<Provenance, String> {
    let doc: serde_json::Value = serde_json::from_str(raw).map_err(|e| format!("PROVENANCE.json: {e}"))?;
    let text = |v: &serde_json::Value, k: &str| v.get(k).and_then(|x| x.as_str()).map(str::to_string);
    let digests = |k: &str| -> BTreeMap<String, String> {
        doc.get(k)
            .and_then(|m| m.as_object())
            .map(|m| m.iter().filter_map(|(id, d)| Some((id.clone(), d.as_str()?.to_string()))).collect())
            .unwrap_or_default()
    };
    let file_sha256 = doc
        .get("files")
        .and_then(|m| m.as_object())
        .map(|m| m.iter().filter_map(|(path, f)| Some((path.clone(), text(f, "sha256")?))).collect())
        .unwrap_or_default();
    Ok(Provenance {
        pack_id: text(&doc, "pack_id").ok_or("PROVENANCE.json names no pack_id")?,
        engine_definition_digest: text(&doc, "engine_definition_digest")
            .ok_or("PROVENANCE.json names no engine_definition_digest")?,
        wicket_digests: digests("wicket_digests"),
        fold_digests: digests("fold_digests"),
        file_sha256,
    })
}

/// The vendored taxonomy, held to its PROVENANCE.json: the file's SHA-256
/// and the JCS digest a pack's pin must name (go/pack VendoredTaxonomy).
pub(crate) fn vendored_taxonomy() -> Result<Taxonomy, Error> {
    let tax = Taxonomy::load(ACTION_TAXONOMY)?;
    let doc: serde_json::Value =
        serde_json::from_str(TAXONOMY_PROVENANCE).map_err(|e| Error::Load(format!("engine PROVENANCE.json: {e}")))?;
    let sha = doc.pointer("/files/action_taxonomy.json/sha256").and_then(|v| v.as_str());
    if sha != Some(canon::sha256_hex(ACTION_TAXONOMY.as_bytes()).as_str()) {
        return Err(Error::Load("action_taxonomy.json is not the file engine/PROVENANCE.json names".into()));
    }
    let pinned = (doc.get("taxonomy_version").and_then(|v| v.as_str()), doc.get("jcs_digest").and_then(|v| v.as_str()));
    if pinned != (Some(tax.version.as_str()), Some(tax.digest.as_str())) {
        return Err(Error::Load("action_taxonomy.json does not digest as engine/PROVENANCE.json records".into()));
    }
    Ok(tax)
}

/// go/pack Load and loadFrom over one embedded version directory.
pub(crate) fn load(e: &Embedded) -> Result<Pack, Error> {
    let fail = |m: String| load_err(e.dir, m);
    let prov = read_provenance(e.provenance).map_err(fail)?;
    // Each file is the one PROVENANCE.json names, byte for byte.
    let mut files: Vec<(String, &str)> = vec![];
    files.extend(e.wickets.iter().map(|(n, d)| (format!("wickets/{n}"), *d)));
    files.extend(e.folds.iter().map(|(n, d)| (format!("folds/{n}"), *d)));
    for (path, data) in &files {
        if prov.file_sha256.get(path) != Some(&canon::sha256_hex(data.as_bytes())) {
            return Err(fail(format!("{path} is not the file PROVENANCE.json names")));
        }
    }
    if prov.file_sha256.len() != files.len() {
        return Err(fail("PROVENANCE.json names a file this port does not embed".into()));
    }
    let taxonomy = vendored_taxonomy()?;
    let wickets = load_wickets(e.wickets).map_err(fail)?;
    let folds = load_folds(e.folds).map_err(fail)?;
    for (id, w) in &wickets {
        if prov.wicket_digests.get(id) != Some(&w.digest) {
            return Err(fail(format!("wicket {id} digests to {}, not what PROVENANCE.json records", w.digest)));
        }
    }
    for (id, f) in &folds {
        if prov.fold_digests.get(id) != Some(&f.digest) {
            return Err(fail(format!("fold {id} digests to {}, not what PROVENANCE.json records", f.digest)));
        }
    }

    let node = yaml::parse(e.pack_yaml).map_err(|m| fail(format!("pack.yaml: {m}")))?;
    let mut top = Fields::of(&node, "pack.yaml").map_err(fail)?;
    let pack_id = top.str("pack_id").map_err(fail)?;
    if pack_id.trim().is_empty() {
        return Err(fail("pack_id is required".into()));
    }

    let mut cited = Vec::new();
    let mut constraints = Vec::new();
    for (i, c) in top.seq("constraints").map_err(fail)?.iter().enumerate() {
        let mut cf = Fields::of(c, format!("constraints[{i}]")).map_err(fail)?;
        let wicket_ref = cf.str("wicket_ref").map_err(fail)?;
        let digest = cf.str("digest").map_err(fail)?;
        let scope = cf.opt_strs("scope").map_err(fail)?;
        cf.done().map_err(fail)?;
        if wicket_ref.is_empty() {
            return Err(fail(format!("constraints[{i}]: only wicket_ref citations are supported by this port")));
        }
        let w = wickets.get(&wicket_ref).ok_or_else(|| {
            fail(format!("constraints[{i}]: wicket_ref {wicket_ref:?} is not in the vendored catalog"))
        })?;
        if w.digest != digest {
            return Err(fail(format!(
                "constraints[{i}] cites {wicket_ref:?} at digest {digest}, but the definition digests to {}",
                w.digest
            )));
        }
        if w.check == "caps" && scope.is_none() {
            return Err(fail(format!("constraints[{i}]: a caps constraint must declare scope")));
        }
        let mut members = vec![
            ("wicket_id".to_string(), canon::s(&w.id)),
            ("check".to_string(), canon::s(&w.check)),
            ("digest".to_string(), canon::s(&w.digest)),
        ];
        if let Some(scope) = scope {
            members.push(("scope".to_string(), canon::strs(&scope)));
        }
        constraints.push(Value::Obj(members));
        cited.push(w.clone());
    }

    let mut rules: Vec<Rule> = Vec::new();
    let mut rule_ids: Vec<String> = Vec::new();
    let mut obligations = Vec::new();
    for (i, o) in top.seq("obligations").map_err(fail)?.iter().enumerate() {
        let mut of = Fields::of(o, format!("obligations[{i}]")).map_err(fail)?;
        let id = of.str("id").map_err(fail)?;
        let statement = of.str("statement").map_err(fail)?;
        let check = of.str("check").map_err(fail)?;
        let grade = of.str("re_derivability_grade").map_err(fail)?;
        let disposition = of.str("default_disposition").map_err(fail)?;
        let measurability = of.str("measurability").map_err(fail)?;
        // selector and evidence_instrument are not read, so done() refuses
        // them: no rule of this pack is declared_not_measured or bound to a
        // selector.
        of.done().map_err(fail)?;
        if id.trim().is_empty() {
            return Err(fail(format!("obligations[{i}].id is required")));
        }
        if statement.trim().is_empty() {
            return Err(fail(format!("obligations[{id:?}].statement is required")));
        }
        if !DEFAULT_DISPOSITIONS.contains(&disposition.as_str()) {
            return Err(fail(format!(
                "obligations[{id:?}].default_disposition={disposition:?} must be one of {DEFAULT_DISPOSITIONS:?}"
            )));
        }
        if !(measurability.is_empty() || measurability == "measured") {
            return Err(fail(format!(
                "obligations[{id:?}].measurability={measurability:?} is not one this port reads"
            )));
        }
        if !cited.iter().any(|w| w.check == check) {
            return Err(fail(format!("obligations[{id:?}].check={check:?} has no matching constraint")));
        }
        if cited.iter().any(|w| w.check == check && config_has(&w.config, "selectors")) {
            return Err(fail(format!(
                "obligations[{id:?}] cites check {check:?}, which has selectors this port does not read"
            )));
        }
        let mut members = vec![
            ("id".to_string(), canon::s(&id)),
            ("statement".to_string(), canon::s(&statement)),
            ("check".to_string(), canon::s(&check)),
        ];
        if !grade.is_empty() {
            members.push(("re_derivability_grade".to_string(), canon::s(&grade)));
        }
        members.push(("default_disposition".to_string(), canon::s(&disposition)));
        obligations.push(Value::Obj(members));
        if rule_ids.contains(&id) {
            return Err(fail(format!("rule id {id:?} declared more than once")));
        }
        rule_ids.push(id);
        rules.push(Rule { check, default_disposition: disposition });
    }

    let mut semantics = Vec::new();
    let mut action_semantics = Vec::new();
    for (i, s) in top.seq("action_semantics").map_err(fail)?.iter().enumerate() {
        let mut sf = Fields::of(s, format!("action_semantics[{i}]")).map_err(fail)?;
        let action_type = sf.str("action_type").map_err(fail)?;
        let action_class = sf.str("action_class").map_err(fail)?;
        let required = sf.opt_strs("required_fields").map_err(fail)?.unwrap_or_default();
        let optional = sf.opt_strs("optional_fields").map_err(fail)?.unwrap_or_default();
        // field_aliases is not read, so done() refuses it.
        sf.done().map_err(fail)?;
        let mut members = vec![
            ("action_type".to_string(), canon::s(&action_type)),
            ("action_class".to_string(), canon::s(&action_class)),
            ("required_fields".to_string(), canon::strs(&required)),
        ];
        if !optional.is_empty() {
            members.push(("optional_fields".to_string(), canon::strs(&optional)));
        }
        semantics.push(Value::Obj(members));
        action_semantics.push(ActionSemantic { action_type, required_fields: required });
    }

    let mut cited_folds = Vec::new();
    let mut fold_values = Vec::new();
    for (i, f) in top.seq("folds").map_err(fail)?.iter().enumerate() {
        let mut ff = Fields::of(f, format!("folds[{i}]")).map_err(fail)?;
        let fold_ref = ff.str("fold_ref").map_err(fail)?;
        let digest = ff.str("digest").map_err(fail)?;
        ff.done().map_err(fail)?;
        if fold_ref.is_empty() || digest.is_empty() {
            return Err(fail(format!("folds[{i}]: only fold_ref citations with a digest are supported by this port")));
        }
        let def = folds
            .get(&fold_ref)
            .ok_or_else(|| fail(format!("folds[{i}]: fold_ref {fold_ref:?} is not in the vendored catalog")))?;
        if def.digest != digest {
            return Err(fail(format!(
                "folds[{i}] cites {fold_ref:?} at digest {digest}, but the definition digests to {}",
                def.digest
            )));
        }
        fold_values.push(canon::obj([("fold_id", canon::s(&def.id)), ("digest", canon::s(&def.digest))]));
        cited_folds.push(def.clone());
    }

    let holds = top.opt_str("holds_integration").map_err(fail)?.unwrap_or_else(|| "none".into());
    let pin = match top.get("taxonomy") {
        None | Some(Node::Null) => None,
        Some(t) => {
            let mut tf = Fields::of(t, "taxonomy").map_err(fail)?;
            let version = tf.str("taxonomy_version").map_err(fail)?;
            let digest = tf.str("digest").map_err(fail)?;
            tf.done().map_err(fail)?;
            if version.trim().is_empty() || digest.trim().is_empty() {
                return Err(fail("taxonomy.taxonomy_version and taxonomy.digest are required".into()));
            }
            if version != taxonomy.version || digest != taxonomy.digest {
                return Err(fail(format!(
                    "taxonomy pins version {version:?} digest {digest:?}; this port vendors version {:?} digest {:?}",
                    taxonomy.version, taxonomy.digest
                )));
            }
            Some(canon::obj([("taxonomy_version", canon::s(version)), ("digest", canon::s(digest))]))
        }
    };
    // outcomes, fixtures and bootstrap are not read, so done() refuses them.
    top.done().map_err(fail)?;
    if rules.is_empty() {
        return Err(fail("pack declares no rules".into()));
    }

    let mut members = vec![
        ("pack_id".to_string(), canon::s(&pack_id)),
        ("obligations".to_string(), Value::Arr(obligations)),
        ("action_semantics".to_string(), Value::Arr(semantics)),
        ("constraints".to_string(), Value::Arr(constraints)),
        ("folds".to_string(), Value::Arr(fold_values)),
        ("holds_integration".to_string(), canon::s(holds)),
    ];
    if let Some(pin) = pin {
        members.push(("taxonomy".to_string(), pin));
    }
    let definition_digest = digest_of(&Value::Obj(members), "pack").map_err(fail)?;
    // go/pack loadFrom: a pack naming a vendored pack_id must digest to that
    // pack's pinned digest.
    if prov.pack_id != pack_id || prov.engine_definition_digest != definition_digest {
        return Err(fail(format!(
            "pack {pack_id} digests to {definition_digest}, but {} is pinned at {}",
            prov.pack_id, prov.engine_definition_digest
        )));
    }
    Ok(Pack { id: pack_id, definition_digest, rules, wickets: cited, folds: cited_folds, action_semantics, taxonomy })
}

/// Whether a wicket config is a mapping with `key`.
fn config_has(config: &Node, key: &str) -> bool {
    matches!(config, Node::Map(m) if m.iter().any(|(k, _)| k == key))
}
