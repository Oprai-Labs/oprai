//! Reading parameters that arrive with the wrong JSON type.
//!
//! Every action parameter reaches this service from one of two places, and
//! neither of them is careful about types. The action card holds its fields in
//! a `Record<string, string>`, so it sends `"30"`. The model writes JSON by
//! hand, so it sends `30` — or `"30"`, or `30.0`, depending on the turn.
//!
//! A field typed `u32` rejects the string, a field typed `String` rejects the
//! number, and serde fails the WHOLE parameter object over one of them: the
//! request answers 400 before it runs. That is not a hypothetical. It cost a
//! working expiry on every Magic Eden listing (`seller_expiry: -1` on chain no
//! matter what was chosen), the entire trending-collections read (`limit`),
//! and the sales history (`days`) — three separate outages, one cause,
//! diagnosed three separate times.
//!
//! So the type a value arrives as is not treated as information. What matters
//! is whether it parses.

use serde::de::{Deserializer, Error};
use serde::Deserialize;
use std::fmt::Display;
use std::str::FromStr;

/// Parse a required value from a string, a number or a bool.
pub fn lenient<'de, D, T>(d: D) -> Result<T, D::Error>
where
    D: Deserializer<'de>,
    T: FromStr,
    <T as FromStr>::Err: Display,
{
    let v = serde_json::Value::deserialize(d)?;
    from_json(&v)
        .ok_or_else(|| D::Error::custom(format!("cannot read {v} as a value")))?
        .parse::<T>()
        .map_err(D::Error::custom)
}

/// The same, for a field that may be absent. An empty string is an absent
/// value — it is what a cleared input sends — not a malformed one.
pub fn lenient_opt<'de, D, T>(d: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: FromStr,
    <T as FromStr>::Err: Display,
{
    let v = Option::<serde_json::Value>::deserialize(d)?;
    let raw = match v {
        None | Some(serde_json::Value::Null) => return Ok(None),
        Some(other) => match from_json(&other) {
            Some(s) => s,
            None => return Ok(None),
        },
    };
    if raw.is_empty() {
        return Ok(None);
    }
    raw.parse::<T>().map(Some).map_err(D::Error::custom)
}

/// The same, for a value we only display.
///
/// A parameter that will not parse is the user's intent misread, and must fail
/// loudly. A field in someone else's response is not: Relay's step data
/// carries `chainId: "solana"` where the EVM branch carries a number, and
/// failing there threw away an entire working quote over a field nothing was
/// going to read. Unreadable becomes absent.
pub fn soft_opt<'de, D, T>(d: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: FromStr,
    <T as FromStr>::Err: Display,
{
    let v = Option::<serde_json::Value>::deserialize(d)?;
    Ok(v.as_ref()
        .and_then(from_json)
        .and_then(|s| s.parse::<T>().ok()))
}

/// The text of a scalar, whatever shape it arrived in.
///
/// `30.0` is written back as `30` because an integer field cannot parse a
/// decimal point, and a model asked for a count will occasionally write one.
fn from_json(v: &serde_json::Value) -> Option<String> {
    Some(match v {
        serde_json::Value::String(s) => s.trim().to_string(),
        serde_json::Value::Bool(b) => b.to_string(),
        serde_json::Value::Number(n) => {
            let s = n.to_string();
            match s.strip_suffix(".0") {
                Some(whole) => whole.to_string(),
                None => s,
            }
        }
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Deserialize;

    #[derive(Deserialize)]
    struct T {
        #[serde(deserialize_with = "lenient")]
        count: u32,
        #[serde(default, deserialize_with = "lenient_opt")]
        ratio: Option<f64>,
        #[serde(default, deserialize_with = "lenient_opt")]
        on: Option<bool>,
    }

    fn read(s: &str) -> T {
        serde_json::from_str(s).expect(s)
    }

    #[test]
    fn a_count_reads_from_either_shape() {
        assert_eq!(read(r#"{"count": 30}"#).count, 30);
        assert_eq!(read(r#"{"count": "30"}"#).count, 30);
        // A model asked for a count sometimes writes a decimal.
        assert_eq!(read(r#"{"count": 30.0}"#).count, 30);
    }

    #[test]
    fn an_absent_option_stays_absent() {
        assert_eq!(read(r#"{"count": 1}"#).ratio, None);
        assert_eq!(read(r#"{"count": 1, "ratio": null}"#).ratio, None);
        // A cleared input sends "", which means unset, not broken.
        assert_eq!(read(r#"{"count": 1, "ratio": ""}"#).ratio, None);
    }

    #[test]
    fn a_flag_reads_from_either_shape() {
        assert_eq!(read(r#"{"count": 1, "on": true}"#).on, Some(true));
        assert_eq!(read(r#"{"count": 1, "on": "true"}"#).on, Some(true));
    }

    #[test]
    fn nonsense_is_still_rejected() {
        assert!(serde_json::from_str::<T>(r#"{"count": "abc"}"#).is_err());
    }
}
