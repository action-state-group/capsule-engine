// SPDX-License-Identifier: Apache-2.0

//! The YAML subset the embedded pack, wicket and fold definitions are written
//! in, read as gopkg.in/yaml.v3 reads them for the Go port (go/pack): block
//! mappings and sequences, single-line flow sequences of scalars, plain and
//! quoted scalars, folded and literal block scalars, and comments. Anything
//! else (anchors, tags, flow mappings, nested flow collections, multi-line
//! plain scalars, a plain scalar YAML would read as a float, a timestamp or
//! an integer in any form but plain decimal, a plain scalar holding ": ") is
//! refused rather than guessed at. Every definition read here is then held
//! to the digest its pack cites, so a misread value fails the load. The
//! admission evaluator reads no YAML: it reads a compiled pack-definition.json.

use crate::canon;

/// A parsed YAML node.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum Node {
    Null,
    Bool(bool),
    Int(i64),
    Str(String),
    Seq(Vec<Node>),
    /// Members in document order; a key appears once.
    Map(Vec<(String, Node)>),
}

#[derive(Debug)]
struct Line<'a> {
    indent: usize,
    /// The line after its indentation, trailing whitespace removed.
    text: &'a str,
    /// The whole line, for block scalars.
    raw: &'a str,
    number: usize,
}

impl Line<'_> {
    fn is_blank_or_comment(&self) -> bool {
        self.text.is_empty() || self.text.starts_with('#')
    }

    fn is_seq_item(&self) -> bool {
        self.text == "-" || self.text.starts_with("- ")
    }
}

struct Parser<'a> {
    lines: Vec<Line<'a>>,
    pos: usize,
}

type Result<T> = std::result::Result<T, String>;

/// Parses one YAML document of the supported subset.
pub(crate) fn parse(doc: &str) -> Result<Node> {
    let mut lines = Vec::new();
    for (i, raw) in doc.split('\n').enumerate() {
        let raw = raw.strip_suffix('\r').unwrap_or(raw);
        let body = raw.trim_start_matches(' ');
        if body.starts_with('\t') {
            return Err(format!("line {}: a tab in indentation", i + 1));
        }
        lines.push(Line { indent: raw.len() - body.len(), text: body.trim_end(), raw, number: i + 1 });
    }
    let mut p = Parser { lines, pos: 0 };
    p.skip_blank();
    let Some(first) = p.current() else {
        return Ok(Node::Null);
    };
    if first.text == "---" || first.text.starts_with("%") {
        return Err(format!("line {}: document markers and directives are not supported", first.number));
    }
    let indent = first.indent;
    let node = p.block(indent)?;
    p.skip_blank();
    if let Some(l) = p.current() {
        return Err(format!("line {}: unexpected content after the document", l.number));
    }
    Ok(node)
}

impl<'a> Parser<'a> {
    fn current(&self) -> Option<&Line<'a>> {
        self.lines.get(self.pos)
    }

    fn skip_blank(&mut self) {
        while self.current().is_some_and(Line::is_blank_or_comment) {
            self.pos += 1;
        }
    }

    /// The block node whose first line is the current one, at `indent`.
    fn block(&mut self, indent: usize) -> Result<Node> {
        if self.current().is_some_and(Line::is_seq_item) {
            self.seq(indent)
        } else {
            self.map(indent)
        }
    }

    fn map(&mut self, indent: usize) -> Result<Node> {
        let mut members: Vec<(String, Node)> = Vec::new();
        loop {
            self.skip_blank();
            let Some(line) = self.current() else { break };
            if line.indent < indent || line.is_seq_item() {
                break;
            }
            let number = line.number;
            if line.indent > indent {
                return Err(format!("line {number}: unexpected indentation"));
            }
            let text = line.text;
            if let Some(e) = anchor_or_tag(text) {
                return Err(format!("line {number}: {e}"));
            }
            let (key, rest) = split_key(text).ok_or_else(|| format!("line {number}: expected a mapping key"))?;
            if members.iter().any(|(k, _)| *k == key) {
                return Err(format!("line {number}: mapping key {key:?} already defined"));
            }
            self.pos += 1;
            let value = self.value(rest, indent, true, number)?;
            members.push((key, value));
        }
        Ok(Node::Map(members))
    }

    fn seq(&mut self, indent: usize) -> Result<Node> {
        let mut items = Vec::new();
        loop {
            self.skip_blank();
            let Some(line) = self.current() else { break };
            if line.indent < indent || !line.is_seq_item() {
                break;
            }
            let number = line.number;
            if line.indent > indent {
                return Err(format!("line {number}: unexpected indentation"));
            }
            let rest = line.text[1..].trim_start_matches(' ');
            if rest.is_empty() {
                self.pos += 1;
                items.push(self.value("", indent, false, number)?);
                continue;
            }
            if split_key(rest).is_some() {
                // "- key: value" opens a mapping at the column of its key.
                let column = line.indent + (line.text.len() - rest.len());
                let raw = line.raw;
                self.lines[self.pos] = Line { indent: column, text: rest, raw, number };
                items.push(self.map(column)?);
                continue;
            }
            self.pos += 1;
            items.push(self.value(rest, indent, false, number)?);
        }
        Ok(Node::Seq(items))
    }

    /// The value after a key or a dash: `rest` inline, or the block below.
    /// In a mapping (`in_map`), a sequence may sit at the key's own indent.
    fn value(&mut self, rest: &'a str, indent: usize, in_map: bool, number: usize) -> Result<Node> {
        if rest.starts_with('>') || rest.starts_with('|') {
            return self.block_scalar(rest, indent, number);
        }
        // "key: # note" holds no value: the comment is not the value.
        if !rest.is_empty() && !rest.starts_with('#') {
            return inline(rest, number);
        }
        self.skip_blank();
        match self.current() {
            Some(next) if next.indent > indent => {
                let i = next.indent;
                self.block(i)
            }
            Some(next) if in_map && next.indent == indent && next.is_seq_item() => self.seq(indent),
            _ => Ok(Node::Null),
        }
    }

    /// A folded (>) or literal (|) block scalar, with clip or strip (-)
    /// chomping. A folded scalar with a more-indented line is refused.
    fn block_scalar(&mut self, header: &str, indent: usize, number: usize) -> Result<Node> {
        let header = header.split(" #").next().unwrap_or(header).trim_end();
        let (folded, strip) = match header {
            ">" => (true, false),
            ">-" => (true, true),
            "|" => (false, false),
            "|-" => (false, true),
            _ => return Err(format!("line {number}: block scalar header {header:?} is not supported")),
        };
        let mut content: Vec<&str> = Vec::new();
        let mut block_indent = None;
        while let Some(line) = self.current() {
            if line.text.is_empty() {
                content.push("");
                self.pos += 1;
                continue;
            }
            let bi = *block_indent.get_or_insert(line.indent);
            if bi <= indent || line.indent < bi {
                if bi <= indent {
                    block_indent = None;
                }
                break;
            }
            if folded && line.indent > bi {
                return Err(format!("line {}: a more-indented line in a folded scalar is not supported", line.number));
            }
            content.push(&line.raw[bi..]);
            self.pos += 1;
        }
        if block_indent.is_none() {
            // No content: blank lines only (or none).
            let trailing = content.len();
            self.pos -= trailing;
            return Ok(Node::Str(String::new()));
        }
        // Trailing blank lines belong to chomping, not to the next node.
        let trailing = content.iter().rev().take_while(|l| l.is_empty()).count();
        content.truncate(content.len() - trailing);
        let mut out = String::new();
        if folded {
            let mut blanks = 0;
            let mut started = false;
            for l in &content {
                if l.is_empty() {
                    blanks += 1;
                    continue;
                }
                if started {
                    if blanks == 0 {
                        out.push(' ');
                    } else {
                        out.push_str(&"\n".repeat(blanks));
                    }
                } else {
                    out.push_str(&"\n".repeat(blanks));
                }
                blanks = 0;
                started = true;
                out.push_str(l);
            }
        } else {
            out = content.join("\n");
        }
        if !strip && !out.is_empty() {
            out.push('\n');
        }
        Ok(Node::Str(out))
    }
}

/// A mapping line's key and the rest after its colon, or None when the line
/// is not "key:" or "key: value".
fn split_key(text: &str) -> Option<(String, &str)> {
    if text.starts_with('"') || text.starts_with('\'') {
        let (key, used) = quoted(text).ok()?;
        let after = &text[used..];
        let rest = after.strip_prefix(':')?;
        if !rest.is_empty() && !rest.starts_with(' ') {
            return None;
        }
        return Some((key, rest.trim_start_matches(' ')));
    }
    if text.starts_with(['[', '{', '&', '*', '!', '|', '>', '#']) {
        return None;
    }
    let (key, rest) = match text.find(": ") {
        Some(i) => (&text[..i], text[i + 2..].trim_start_matches(' ')),
        None => (text.strip_suffix(':')?, ""),
    };
    if key.is_empty() || key.contains(" #") {
        return None;
    }
    Some((key.trim_end().to_string(), rest))
}

/// The items of a single-line flow collection opening `rest` with `open`,
/// split at the commas outside quotes, and checked to close on the line
/// with nothing after it but a comment. A nested collection is refused.
fn flow_items(rest: &str, open: char, close: char) -> Result<Vec<&str>> {
    let body = &rest[open.len_utf8()..];
    let mut items = Vec::new();
    let mut start = 0;
    let mut quote: Option<char> = None;
    let mut chars = body.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        match quote {
            Some('"') if c == '\\' => {
                chars.next();
            }
            Some(q) if c == q => {
                if q == '\'' && chars.peek().map(|&(_, n)| n) == Some('\'') {
                    chars.next();
                } else {
                    quote = None;
                }
            }
            Some(_) => {}
            None if (c == '"' || c == '\'') && body[start..i].trim().is_empty() => quote = Some(c),
            None if c == ',' => {
                items.push(body[start..i].trim());
                start = i + 1;
            }
            None if c == close => {
                let tail = &body[i + 1..];
                if !(tail.trim().is_empty() || (tail.starts_with([' ', '\t']) && tail.trim_start().starts_with('#'))) {
                    return Err(format!("content after a flow collection: {tail:?}"));
                }
                let last = body[start..i].trim();
                if !(last.is_empty() && items.is_empty()) {
                    items.push(last);
                }
                if items.iter().any(|it| it.is_empty()) {
                    return Err("an empty flow collection item".into());
                }
                return Ok(items);
            }
            None if matches!(c, '[' | ']' | '{' | '}') => {
                return Err("a nested flow collection is not supported".into());
            }
            None => {}
        }
    }
    Err("a flow collection must close on its line".into())
}

/// One scalar in a flow sequence: quoted, or plain.
fn flow_scalar(item: &str) -> Result<Node> {
    if item.starts_with('"') || item.starts_with('\'') {
        let (v, used) = quoted(item)?;
        if used != item.len() {
            return Err(format!("flow item {item:?} is not supported"));
        }
        return Ok(Node::Str(v));
    }
    plain(item)
}

/// An inline value: a quoted scalar, a flow sequence of scalars, or a plain
/// scalar.
fn inline(rest: &str, number: usize) -> Result<Node> {
    let err = |e: String| format!("line {number}: {e}");
    if rest.starts_with('"') || rest.starts_with('\'') {
        let (v, used) = quoted(rest).map_err(err)?;
        let tail = &rest[used..];
        if !(tail.trim().is_empty() || (tail.starts_with([' ', '\t']) && tail.trim_start().starts_with('#'))) {
            return Err(format!("line {number}: content after a quoted scalar"));
        }
        return Ok(Node::Str(v));
    }
    if rest.starts_with('[') {
        let items = flow_items(rest, '[', ']').map_err(err)?;
        return items.into_iter().map(|i| flow_scalar(i).map_err(err)).collect::<Result<_>>().map(Node::Seq);
    }
    if rest.starts_with('{') {
        return Err(err("a flow mapping is not supported".into()));
    }
    let v = match rest.find(" #") {
        Some(i) => rest[..i].trim_end(),
        None => rest,
    };
    plain(v).map_err(err)
}

/// A quoted scalar at the start of `text`, and the bytes it used.
fn quoted(text: &str) -> Result<(String, usize)> {
    let q = text.chars().next().ok_or("empty")?;
    let mut out = String::new();
    let mut chars = text.char_indices().skip(1).peekable();
    while let Some((i, c)) = chars.next() {
        if c == q {
            if q == '\'' && chars.peek().map(|&(_, n)| n) == Some('\'') {
                chars.next();
                out.push('\'');
                continue;
            }
            return Ok((out, i + 1));
        }
        if q == '"' && c == '\\' {
            let (_, e) = chars.next().ok_or("unterminated escape")?;
            match e {
                '"' => out.push('"'),
                '\\' => out.push('\\'),
                '/' => out.push('/'),
                'n' => out.push('\n'),
                't' => out.push('\t'),
                'r' => out.push('\r'),
                'u' => {
                    let hex: String = (0..4).filter_map(|_| chars.next().map(|(_, h)| h)).collect();
                    let code = u32::from_str_radix(&hex, 16).map_err(|_| format!("bad escape \\u{hex}"))?;
                    out.push(char::from_u32(code).ok_or_else(|| format!("bad escape \\u{hex}"))?);
                }
                other => return Err(format!("escape \\{other} is not supported")),
            }
            continue;
        }
        out.push(c);
    }
    Err("unterminated quoted scalar".into())
}

/// The refusal of an anchor, an alias or a tag at the start of `text`.
fn anchor_or_tag(text: &str) -> Option<&'static str> {
    if text.starts_with(['&', '*']) {
        Some("anchors and aliases are not read")
    } else if text.starts_with('!') {
        Some("tags are not read")
    } else {
        None
    }
}

/// A plain scalar, resolved as yaml.v3's core schema resolves it, for the
/// forms go/canon FromYAML accepts: null, a boolean, a decimal integer, or a
/// string. A float, a timestamp or an integer in another form is refused.
fn plain(v: &str) -> Result<Node> {
    if let Some(e) = anchor_or_tag(v) {
        return Err(e.into());
    }
    if v.starts_with(['%', '@', '`', '{', '}', '[', ']', '|', '>', '"', '\'', '#', ','])
        || v.starts_with("- ")
        || v.starts_with("? ")
        || v.starts_with(": ")
        || v.contains(": ")
        || v.ends_with(':')
    {
        // yaml.v3 reads these as another node, or refuses them ("mapping
        // values are not allowed in this context").
        return Err(format!("plain scalar {v:?} is not supported"));
    }
    match v {
        "" | "~" | "null" | "Null" | "NULL" => return Ok(Node::Null),
        "true" | "True" | "TRUE" => return Ok(Node::Bool(true)),
        "false" | "False" | "FALSE" => return Ok(Node::Bool(false)),
        _ => {}
    }
    // Plain decimal is the one integer form read; "-0" is refused, as its
    // canonical form would not be the text written.
    let digits = v.strip_prefix('-').unwrap_or(v);
    let decimal = !digits.is_empty()
        && digits.bytes().all(|b| b.is_ascii_digit())
        && !(digits.len() > 1 && digits.starts_with('0'))
        && v != "-0";
    if decimal {
        return v.parse::<i64>().map(Node::Int).map_err(|_| format!("integer {v:?} is out of range"));
    }
    match numeric_tag(v) {
        Some("!!int") => Err(format!("write the integer {v:?} in plain decimal")),
        Some(tag) => Err(format!("{v:?} reads as {tag}, which no definition holds (quote a string)")),
        None => Ok(Node::Str(v.to_string())),
    }
}

/// The tag yaml.v3 could resolve plain `v` to, other than a string, when it
/// is not a plain decimal integer: !!float, !!timestamp, or !!int for an
/// integer in another form (yaml.v3 resolve.go: a float
/// matching yamlStyleFloat, .inf or .nan, any strconv.ParseInt base-0 form
/// once underscores are removed, or four digits and a dash that may open a
/// timestamp).
fn numeric_tag(v: &str) -> Option<&'static str> {
    let body = v.strip_prefix(['-', '+']).unwrap_or(v);
    if matches!(body, ".inf" | ".Inf" | ".INF" | ".nan" | ".NaN" | ".NAN") {
        return Some("!!float");
    }
    let b = body.as_bytes();
    // yamlStyleFloat: ^[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?$
    let float = {
        let mut i = 0;
        let int_digits = b.iter().take_while(|c| c.is_ascii_digit()).count();
        i += int_digits;
        let mut ok = true;
        if i < b.len() && b[i] == b'.' {
            i += 1;
            let frac = b[i..].iter().take_while(|c| c.is_ascii_digit()).count();
            i += frac;
            ok = int_digits > 0 || frac > 0;
        } else {
            ok &= int_digits > 0;
        }
        if ok && i < b.len() && (b[i] == b'e' || b[i] == b'E') {
            i += 1;
            if i < b.len() && (b[i] == b'+' || b[i] == b'-') {
                i += 1;
            }
            let exp = b[i..].iter().take_while(|c| c.is_ascii_digit()).count();
            ok = exp > 0;
            i += exp;
        }
        ok && i == b.len()
    };
    // strconv.ParseInt base 0 needs a leading digit, so "add_bed" is a string.
    let underscored = body.contains('_')
        && body.as_bytes().first().is_some_and(u8::is_ascii_digit)
        && body.bytes().all(|c| c.is_ascii_hexdigit() || b"_xXoObB".contains(&c));
    let prefixed = b.len() > 2 && b[0] == b'0' && b"xXoObB".contains(&b[1]);
    // A signed, zero-led or "-0" integer is an !!int in another form.
    let other_int = !body.is_empty() && body.bytes().all(|c| c.is_ascii_digit());
    let timestamp = b.len() > 4 && b[..4].iter().all(u8::is_ascii_digit) && b[4] == b'-';
    if underscored || prefixed || other_int {
        Some("!!int")
    } else if float {
        Some("!!float")
    } else if timestamp {
        Some("!!timestamp")
    } else {
        None
    }
}

impl Node {
    /// go/canon FromYAML: the node as a canonical JSON value.
    pub(crate) fn to_canon(&self) -> canon::Value {
        match self {
            Node::Null => canon::Value::Null,
            Node::Bool(b) => canon::Value::Bool(*b),
            Node::Int(i) => canon::Value::Int(*i),
            Node::Str(s) => canon::Value::Str(s.clone()),
            Node::Seq(items) => canon::Value::Arr(items.iter().map(Node::to_canon).collect()),
            Node::Map(members) => canon::Value::Obj(members.iter().map(|(k, v)| (k.clone(), v.to_canon())).collect()),
        }
    }
}

/// A mapping read field by field, refusing a key the reader did not take
/// (yaml.v3 KnownFields, as go/pack and decodeConfig decode).
pub(crate) struct Fields<'a> {
    what: String,
    members: &'a [(String, Node)],
    taken: Vec<&'a str>,
}

impl<'a> Fields<'a> {
    pub(crate) fn of(node: &'a Node, what: impl Into<String>) -> Result<Self> {
        let what = what.into();
        match node {
            Node::Map(members) => Ok(Fields { what, members, taken: Vec::new() }),
            _ => Err(format!("{what} must be a mapping")),
        }
    }

    /// The value at `key`, if present.
    pub(crate) fn get(&mut self, key: &'a str) -> Option<&'a Node> {
        self.taken.push(key);
        self.members.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }

    /// A string at `key`, or None when absent or null.
    pub(crate) fn opt_str(&mut self, key: &'a str) -> Result<Option<String>> {
        match self.get(key) {
            None | Some(Node::Null) => Ok(None),
            Some(Node::Str(s)) => Ok(Some(s.clone())),
            Some(_) => Err(format!("{}.{key} must be a string", self.what)),
        }
    }

    /// A string at `key`, "" when absent (a Go string field's zero value).
    pub(crate) fn str(&mut self, key: &'a str) -> Result<String> {
        Ok(self.opt_str(key)?.unwrap_or_default())
    }

    /// A list of strings at `key`, or None when absent or null.
    pub(crate) fn opt_strs(&mut self, key: &'a str) -> Result<Option<Vec<String>>> {
        match self.get(key) {
            None | Some(Node::Null) => Ok(None),
            Some(Node::Seq(items)) => items
                .iter()
                .map(|i| match i {
                    Node::Str(s) => Ok(s.clone()),
                    _ => Err(format!("{}.{key} must be a list of strings", self.what)),
                })
                .collect::<Result<Vec<_>>>()
                .map(Some),
            Some(_) => Err(format!("{}.{key} must be a list of strings", self.what)),
        }
    }

    /// A list at `key`, empty when absent or null.
    pub(crate) fn seq(&mut self, key: &'a str) -> Result<&'a [Node]> {
        match self.get(key) {
            None | Some(Node::Null) => Ok(&[]),
            Some(Node::Seq(items)) => Ok(items),
            Some(_) => Err(format!("{}.{key} must be a list", self.what)),
        }
    }

    /// An integer at `key`, 0 when absent (a Go int field's zero value).
    pub(crate) fn int(&mut self, key: &'a str) -> Result<i64> {
        match self.get(key) {
            None | Some(Node::Null) => Ok(0),
            Some(Node::Int(i)) => Ok(*i),
            Some(_) => Err(format!("{}.{key} must be an integer", self.what)),
        }
    }

    /// Refuses any key not taken.
    pub(crate) fn done(self) -> Result<()> {
        for (k, _) in self.members {
            if !self.taken.contains(&k.as_str()) {
                return Err(format!("{}: key {k:?} is not one this port reads", self.what));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_the_subset() {
        let doc = "# comment\nid: a/1.0.0\nlist: [x, \"y\", 3]\nempty: []\nn: 9007199254740991\nq: \"6\"\nb: false\nz: null\nnested:\n  - path: p\n    default: false\n  - k: v\nseq:\n- one\n- two\ntext: >-\n  folded\n  lines\n\n  para\nend: x # trailing\n";
        let n = parse(doc).unwrap();
        let Node::Map(m) = &n else { panic!("not a map") };
        let get = |k: &str| m.iter().find(|(key, _)| key == k).map(|(_, v)| v.clone()).unwrap();
        assert_eq!(get("id"), Node::Str("a/1.0.0".into()));
        assert_eq!(get("list"), Node::Seq(vec![Node::Str("x".into()), Node::Str("y".into()), Node::Int(3)]));
        assert_eq!(get("empty"), Node::Seq(vec![]));
        assert_eq!(get("n"), Node::Int(9007199254740991));
        assert_eq!(get("q"), Node::Str("6".into()));
        assert_eq!(get("b"), Node::Bool(false));
        assert_eq!(get("z"), Node::Null);
        assert_eq!(
            get("nested"),
            Node::Seq(vec![
                Node::Map(vec![("path".into(), Node::Str("p".into())), ("default".into(), Node::Bool(false))]),
                Node::Map(vec![("k".into(), Node::Str("v".into()))]),
            ])
        );
        assert_eq!(get("seq"), Node::Seq(vec![Node::Str("one".into()), Node::Str("two".into())]));
        assert_eq!(get("text"), Node::Str("folded lines\npara".into()));
        assert_eq!(get("end"), Node::Str("x".into()));
    }

    #[test]
    fn refuses_what_it_does_not_read() {
        for doc in [
            "a: 1.5\n",
            "a: 1e3\n",
            "a: 0x10\n",
            "a: 010\n",
            "a: 2026-10-29\n",
            "a: &x 1\n",
            "a: {b: c}\n",
            "a: 1\na: 2\n",
            "a: b\n  c\n",
            "a: >-\n  x\n    y\n",
            "a: .inf\n",
        ] {
            assert!(parse(doc).is_err(), "accepted {doc:?}");
        }
    }

    #[test]
    fn keeps_strings_that_start_like_numbers() {
        assert_eq!(parse("a: add_bed\n").unwrap(), Node::Map(vec![("a".into(), Node::Str("add_bed".into()))]));
        let n = parse("d: 18ab5d489f1e\nw: 7d\nv: 1.0.0x\n").unwrap();
        assert_eq!(
            n,
            Node::Map(vec![
                ("d".into(), Node::Str("18ab5d489f1e".into())),
                ("w".into(), Node::Str("7d".into())),
                ("v".into(), Node::Str("1.0.0x".into())),
            ])
        );
    }
}
