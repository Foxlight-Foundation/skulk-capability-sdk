//! Translate the Python producer's character classes without changing literals.

use serde_json::Value;

use crate::schema::SchemaError;
use crate::schema_unicode::{Projection, literal};

const WORD: &str = r"\p{L}\p{N}_";
const SPACE: &str = "\\p{White_Space}\u{1c}-\u{1f}";

fn class(escape: char, ascii: bool) -> Option<String> {
    let body = match (escape.to_ascii_lowercase(), ascii) {
        ('w', false) => WORD,
        ('d', false) => r"\p{Nd}",
        ('s', false) => SPACE,
        ('w', true) => "a-zA-Z0-9_",
        ('d', true) => "0-9",
        ('s', true) => " \t\n\u{b}\u{c}\r",
        _ => return None,
    };
    Some(format!(
        "[{}{body}]",
        if escape.is_ascii_uppercase() { "^" } else { "" }
    ))
}

/// The producer uses Python `re.search`, including Unicode classes and `$`'s
/// final-newline position. Do not run this over const/enum/annotation contents.
pub(super) fn prepare(schema: &mut Value, projection: &Projection) -> Result<(), SchemaError> {
    let Some(object) = schema.as_object_mut() else {
        return Ok(());
    };
    if let Some(value) = object.get_mut("pattern") {
        let original = value.as_str().ok_or(SchemaError)?;
        *value = Value::String(pattern(original, projection)?);
    }
    for name in [
        "properties",
        "patternProperties",
        "$defs",
        "definitions",
        "dependentSchemas",
    ] {
        if let Some(children) = object.get_mut(name).and_then(Value::as_object_mut) {
            if name == "patternProperties" {
                let old = std::mem::take(children);
                for (key, mut child) in old {
                    prepare(&mut child, projection)?;
                    children.insert(pattern(&key, projection)?, child);
                }
            } else {
                for child in children.values_mut() {
                    prepare(child, projection)?;
                }
            }
        }
    }
    for name in [
        "additionalProperties",
        "unevaluatedProperties",
        "additionalItems",
        "unevaluatedItems",
        "contains",
        "propertyNames",
        "not",
        "if",
        "then",
        "else",
        "items",
    ] {
        if let Some(child) = object.get_mut(name) {
            if let Some(children) = child.as_array_mut() {
                for child in children {
                    prepare(child, projection)?;
                }
            } else {
                prepare(child, projection)?;
            }
        }
    }
    for name in ["allOf", "anyOf", "oneOf", "prefixItems"] {
        if let Some(children) = object.get_mut(name).and_then(Value::as_array_mut) {
            for child in children {
                prepare(child, projection)?;
            }
        }
    }
    if let Some(children) = object
        .get_mut("dependencies")
        .and_then(Value::as_object_mut)
    {
        for child in children.values_mut() {
            prepare(child, projection)?;
        }
    }
    Ok(())
}

pub(super) fn pattern(source: &str, projection: &Projection) -> Result<String, SchemaError> {
    let characters: Vec<char> = source.chars().collect();
    let mut output = String::new();
    let mut at = 0;
    let mut ascii = false;
    let mut verbose = false;
    let mut scopes = Vec::new();
    let mut global_prefix = true;
    while at < characters.len() {
        let ch = characters[at];
        at += 1;
        // Comments are tokens in Python's verbose grammar. Do not parse their
        // parentheses, brackets or trailing backslashes as pattern structure.
        if verbose && ch == '#' {
            while let Some(ch) = characters.get(at).copied() {
                at += 1;
                if ch == '\\' {
                    // Python tokenizes escapes even inside comments: an
                    // escaped newline does not terminate the comment token.
                    characters.get(at).ok_or(SchemaError)?;
                    at += 1;
                } else if ch == '\n' {
                    break;
                }
            }
            continue;
        }
        if verbose && matches!(ch, ' ' | '\t' | '\n' | '\r' | '\u{b}' | '\u{c}') {
            continue;
        }
        match ch {
            '\\' => {
                let escaped = *characters.get(at).ok_or(SchemaError)?;
                at += 1;
                if let Some(class) = class(escaped, ascii) {
                    output.push_str(&class);
                } else if escaped == 'Z' {
                    output.push_str(r"(?![\s\S])");
                } else if matches!(escaped, 'b' | 'B') {
                    let word = if ascii { "a-zA-Z0-9_" } else { WORD };
                    if escaped == 'b' {
                        output.push_str(&format!(
                            r"(?:(?<![{word}])(?=[{word}])|(?<=[{word}])(?![{word}]))"
                        ));
                    } else {
                        output.push_str(&format!(r"(?!\A(?![\s\S]))(?:(?<=[{word}])(?=[{word}])|(?<![{word}])(?![{word}]))"));
                    }
                } else if matches!(escaped, 'u' | 'U' | 'x') {
                    let point = hexadecimal(&characters, &mut at, escaped)?;
                    let point = u32::from(projection.point(point)?);
                    output.push_str(&format!("(?:{})", literal(point, false)?));
                } else if escaped == '0'
                    || (escaped.is_digit(8)
                        && characters
                            .get(at..at + 2)
                            .is_some_and(|v| v.iter().all(|c| c.is_digit(8))))
                {
                    let point = octal(&characters, &mut at, escaped)?;
                    output.push_str(&format!("(?:{})", literal(point, false)?));
                } else if escaped.is_ascii_digit() {
                    // Python consumes at most two decimal digits for a numeric
                    // backreference. Preserve that token when discarded trivia
                    // or a third literal digit follows; no captures are added.
                    let mut reference = escaped.to_string();
                    if let Some(next) = characters.get(at).filter(|ch| ch.is_ascii_digit()) {
                        reference.push(*next);
                        at += 1;
                    }
                    output.push_str(&format!(r"(?:\{reference})"));
                } else if matches!(escaped, 'p' | 'P' | 'k' | 'z') {
                    return Err(SchemaError);
                } else {
                    output.push('\\');
                    output.push(escaped);
                }
            }
            '$' => output.push_str(r"(?:$|(?=\n$(?!\n)))"),
            '[' => {
                let negate = characters.get(at) == Some(&'^');
                if negate {
                    at += 1;
                }
                let mut parts = Vec::new();
                let start = at;
                loop {
                    let ch = *characters.get(at).ok_or(SchemaError)?;
                    at += 1;
                    if ch == ']' && at > start + 1 {
                        break;
                    }
                    if ch == '\\' {
                        let escaped = *characters.get(at).ok_or(SchemaError)?;
                        at += 1;
                        if let Some(class) = class(escaped, ascii) {
                            if characters.get(at) == Some(&'-') {
                                return Err(SchemaError);
                            }
                            parts.push(class);
                            continue;
                        }
                    }
                    at -= if ch == '\\' { 2 } else { 1 };
                    let first = atom(&characters, &mut at, projection)?;
                    let last = if characters.get(at) == Some(&'-')
                        && characters.get(at + 1) != Some(&']')
                    {
                        at += 1;
                        atom(&characters, &mut at, projection)?
                    } else {
                        first
                    };
                    parts.push(projection.range(first, last)?);
                }
                if parts.is_empty() {
                    return Err(SchemaError);
                }
                let union = format!("(?:{})", parts.join("|"));
                if negate {
                    // The exclusion and consumed character are one atom;
                    // a following quantifier must repeat the exclusion too.
                    output.push_str(&format!("(?:(?!{union})(?s:.))"));
                } else {
                    output.push_str(&union);
                }
            }
            '(' if characters.get(at..at + 2) == Some(&['?', '#']) => {
                at += 2;
                loop {
                    let ch = *characters.get(at).ok_or(SchemaError)?;
                    at += 1;
                    if ch == ')' {
                        break;
                    }
                    if ch == '\\' {
                        // An escaped close-parenthesis is part of the comment.
                        characters.get(at).ok_or(SchemaError)?;
                        at += 1;
                    }
                }
                continue;
            }
            '(' if characters.get(at..at + 3) == Some(&['?', 'P', '=']) => {
                at += 3;
                let start = at;
                while characters.get(at).is_some_and(|ch| *ch != ')') {
                    at += 1;
                }
                if characters.get(at) != Some(&')') {
                    return Err(SchemaError);
                }
                output.push_str(r"\k<");
                output.extend(characters[start..at].iter());
                output.push('>');
                at += 1;
            }
            '(' => {
                let previous = (ascii, verbose);
                let flags_start = at + 1;
                let mut end = flags_start;
                if characters.get(at) == Some(&'?') {
                    while characters.get(end).is_some_and(|c| "aiLmsux-".contains(*c)) {
                        end += 1;
                    }
                }
                if end > flags_start && matches!(characters.get(end), Some(':' | ')')) {
                    let flags: String = characters[flags_start..end].iter().collect();
                    if flags.contains('a') && flags.contains('u') {
                        return Err(SchemaError);
                    }
                    if flags.contains('L') || flags.contains("-a") || flags.contains("-u") {
                        return Err(SchemaError);
                    }
                    if flags.contains('a') {
                        ascii = true;
                    }
                    if flags.contains('u') {
                        ascii = false;
                    }
                    let (enabled, disabled) = flags.split_once('-').unwrap_or((&flags, ""));
                    if enabled.contains('x') {
                        verbose = true;
                    }
                    if disabled.contains('x') {
                        verbose = false;
                    }
                    let emitted = flags.replace(['a', 'u'], "");
                    if characters[end] == ')' {
                        if !global_prefix || flags.contains('-') {
                            return Err(SchemaError);
                        }
                        if !emitted.is_empty() {
                            output.push_str(&format!("(?{emitted})"));
                        }
                        at = end + 1;
                        continue;
                    }
                    scopes.push(previous);
                    output.push_str(&format!("(?{emitted}:"));
                    at = end + 1;
                } else {
                    scopes.push(previous);
                    output.push('(');
                }
            }
            ')' => {
                (ascii, verbose) = scopes.pop().ok_or(SchemaError)?;
                output.push(')');
            }
            other if verbose && other.is_whitespace() => {
                // Python ignores only ASCII verbose whitespace. Quote other
                // Unicode spaces so the native engine cannot discard them.
                output.push_str(&literal(u32::from(other), false)?);
            }
            other => output.push(other),
        }
        global_prefix = false;
    }
    // Prevent ECMA prefix/exact-match optimizations from bypassing producer
    // anchor and Unicode behavior; the bounded fancy engine evaluates this.
    // An empty lookahead also prevents the ECMA translator from rejecting
    // Python named references before the bounded fancy parser sees them.
    // Distinct source keys must stay distinct even when comments, flags or
    // whitespace lower to the same regex. An empty-first lookahead is always
    // true and retains the original key's identity for pointer aliases. Keep
    // it in ECMA syntax: older draft meta-schemas reject inline comments.
    let identity = crate::codec::sha256(source.as_bytes());
    Ok(format!("(?=)(?:{output})(?=(?:|{identity}))"))
}

fn hexadecimal(characters: &[char], at: &mut usize, escape: char) -> Result<u32, SchemaError> {
    let length = match escape {
        'u' => 4,
        'U' => 8,
        'x' => 2,
        _ => return Err(SchemaError),
    };
    let digits: String = characters
        .get(*at..*at + length)
        .ok_or(SchemaError)?
        .iter()
        .collect();
    *at += length;
    let point = u32::from_str_radix(&digits, 16).map_err(|_| SchemaError)?;
    if point > 0x10ffff {
        return Err(SchemaError);
    }
    Ok(point)
}

fn atom(characters: &[char], at: &mut usize, projection: &Projection) -> Result<u32, SchemaError> {
    let point = *characters.get(*at).ok_or(SchemaError)?;
    *at += 1;
    if point != '\\' {
        return Ok(projection.original(point));
    }
    let escaped = *characters.get(*at).ok_or(SchemaError)?;
    *at += 1;
    if matches!(escaped, 'u' | 'U' | 'x') {
        return hexadecimal(characters, at, escaped);
    }
    if escaped.is_digit(8) {
        return octal(characters, at, escaped);
    }
    Ok(match escaped {
        'a' => 7,
        'b' => 8,
        't' => 9,
        'n' => 10,
        'v' => 11,
        'f' => 12,
        'r' => 13,
        ch if !ch.is_ascii_alphabetic() => u32::from(ch),
        _ => return Err(SchemaError),
    })
}

fn octal(characters: &[char], at: &mut usize, first: char) -> Result<u32, SchemaError> {
    let mut digits = first.to_string();
    while digits.len() < 3 && characters.get(*at).is_some_and(|c| c.is_digit(8)) {
        digits.push(characters[*at]);
        *at += 1;
    }
    let point = u32::from_str_radix(&digits, 8).map_err(|_| SchemaError)?;
    if point > 255 {
        return Err(SchemaError);
    }
    Ok(point)
}

#[cfg(test)]
mod tests {
    use crate::schema::Schema;
    use serde_json::json;

    #[test]
    fn discarded_trivia_cannot_join_reference_and_literal_tokens() {
        for pattern in [
            r"(?x)^(a)\1 2$",
            r"^(a)\1(?# gap)2$",
            "(?x)^(a)\\1 # ignored\n 2$",
        ] {
            let schema = Schema::installed(&json!({"pattern": pattern}).to_string()).unwrap();
            assert!(schema.matches(r#""aa2""#));
            assert!(!schema.matches(r#""aa12""#));
        }
        let pattern = r"^(a)\1(?# gap)+2$";
        let schema = Schema::installed(&json!({"pattern": pattern}).to_string()).unwrap();
        assert!(schema.matches(r#""aa2""#));
        assert!(schema.matches(r#""aaa2""#));
        assert!(!schema.matches(r#""a2""#));
    }

    #[test]
    fn numeric_reference_width_and_octal_tokens_remain_distinct() {
        let groups = "(a)(b)(c)(d)(e)(f)(g)(h)(i)(j)(k)(l)";
        for (suffix, accepted, rejected) in [
            (r"\1 2", "abcdefghijkla2", "abcdefghijkll"),
            (r"\12 3", "abcdefghijkll3", "abcdefghijkla23"),
            (r"\118", "abcdefghijklk8", "abcdefghijklS"),
            (r"\123", "abcdefghijklS", "abcdefghijkll3"),
        ] {
            let pattern = format!("(?x)^{groups}{suffix}$");
            let schema = Schema::installed(&json!({"pattern": pattern}).to_string()).unwrap();
            assert!(schema.matches(&json!(accepted).to_string()));
            assert!(!schema.matches(&json!(rejected).to_string()));
        }
    }

    #[test]
    fn private_pattern_identity_remains_valid_in_older_drafts() {
        for draft in ["04", "06", "07"] {
            let definition = json!({
                "$schema": format!("http://json-schema.org/draft-{draft}/schema#"),
                "pattern": "^a*$"
            });
            let schema = Schema::new(&definition.to_string()).unwrap();
            assert!(schema.matches(r#""aaa""#));
            assert!(schema.matches("true"));
            assert!(!schema.matches(r#""x""#));
        }
    }

    #[test]
    fn equivalent_source_patterns_keep_both_property_constraints() {
        let definition = json!({"patternProperties": {
            "(?x)^a b$": {"type": "integer"},
            "(?x)^ab$": {"type": "string"}
        }});
        let schema = Schema::installed(&definition.to_string()).unwrap();
        assert!(schema.matches(r#"{}"#));
        assert!(schema.matches(r#"{"other": 1}"#));
        assert!(!schema.matches(r#"{"ab": "x"}"#));
        assert!(!schema.matches(r#"{"ab": 1}"#));
    }

    #[test]
    fn equivalent_source_patterns_keep_independent_pointer_targets() {
        for (pointer, accepted, rejected) in [
            ("%28%3Fx%29%5Ea%20b%24", "1", r#""x""#),
            ("%28%3Fx%29%5Eab%24", r#""x""#, "1"),
        ] {
            let definition = json!({
                "patternProperties": {
                    "(?x)^a b$": {"type": "integer"},
                    "(?x)^ab$": {"type": "string"}
                },
                "$ref": format!("#/patternProperties/{pointer}")
            });
            let schema = Schema::installed(&definition.to_string()).unwrap();
            assert!(schema.matches(accepted));
            assert!(!schema.matches(rejected));
        }
    }

    #[test]
    fn comments_never_supply_group_or_class_structure() {
        for pattern in [
            "(?x)^a # ignored ( [\n+b$",
            r"^(?# ignored ([ # )ab$",
            r"^(?# ignored \) then )ab$",
        ] {
            let schema = Schema::installed(&json!({"pattern": pattern}).to_string()).unwrap();
            assert!(schema.matches(r#""ab""#), "{pattern}");
            assert!(!schema.matches(r#""ac""#), "{pattern}");
        }
    }

    #[test]
    fn an_escaped_newline_continues_the_verbose_comment() {
        let pattern = "(?x)^a # ignored ( [ \\\n+b$";
        let schema = Schema::installed(&json!({"pattern": pattern}).to_string()).unwrap();
        assert!(schema.matches(r#""ac""#));
        assert!(schema.matches(r#""a#b""#));
        assert!(!schema.matches(r#""b""#));
    }

    #[test]
    fn repeated_negative_classes_check_every_consumed_character() {
        let pattern = "(?x)^[^ #]+$";
        let schema = Schema::installed(&json!({"pattern": pattern}).to_string()).unwrap();
        assert!(schema.matches(r#""abc""#));
        assert!(!schema.matches(r#""a#b""#));
        assert!(!schema.matches(r#""a b""#));
    }

    #[test]
    fn verbose_classes_and_unicode_escapes_keep_literal_hash_and_space() {
        for (pattern, accepted) in [
            (r"(?x)^[ #]+$", " # "),
            (r"(?x)^a\u0020b$", "a b"),
            (r"(?x)^a\u0023b$", "a#b"),
            ("(?x)^a\u{a0}b$", "a\u{a0}b"),
        ] {
            let schema = Schema::installed(&json!({"pattern": pattern}).to_string()).unwrap();
            assert!(schema.matches(&json!(accepted).to_string()), "{pattern}");
            assert!(!schema.matches(r#""ab""#), "{pattern}");
        }
    }

    #[test]
    fn verbose_scope_and_global_prefix_survive_comments() {
        for (pattern, accepted) in [
            (r"(?x)^a(?-x: # )b$", "a # b"),
            ("^(?x:a # ignored [\n b) c$", "ab c"),
            ("(?x) # ignored\n (?i)^ab$", "AB"),
        ] {
            let schema = Schema::installed(&json!({"pattern": pattern}).to_string()).unwrap();
            assert!(schema.matches(&json!(accepted).to_string()), "{pattern}");
            assert!(!schema.matches(r#""ac""#), "{pattern}");
        }
    }
}
