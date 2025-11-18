use trustfall::FieldValue;

use crate::error::*;
use super::query::QueryResult;

pub fn parse_path(map: &QueryResult, importable_path_key: &str, canonical_path_key: &str) -> Result<String> {
    let canonical_path = get_key_as_slice(map, canonical_path_key)?;
    let result = get_key_as_slice(map, importable_path_key)?
        .iter().next()
        .map_or(Ok(canonical_path), |path| path.as_slice()
            .ok_or(Error::TrustfallQueryResultTypeMismatch(format!("{}", importable_path_key), "slice".into())))?
        .into_iter()
        .map(|path| path.as_str()
            .ok_or(Error::TrustfallQueryResultTypeMismatch(format!("{}[*]", importable_path_key), "string".into()))
            .map(|s| s.to_string()))
        .collect::<Result<Vec<_>>>()?
        .join("::");
    Ok(result)
}

use paste::paste;

macro_rules! impl_field_value_try_into_T {
    () => {};

    ( $Name:ident, & $T:ty $(, $($rest:tt)*)? ) => {
        paste! {
            pub fn [<get_key_ $Name>]<'a>(
                map: &'a QueryResult,
                key: &str,
            ) -> Result<&'a $T> {
                let value = map.get(key)
                    .ok_or(Error::TrustfallQueryResultMissingKey(key.into()))?;
                value.$Name()
                    .ok_or(Error::TrustfallQueryResultTypeMismatch(
                        key.into(),
                        stringify!($T).into(),
                    ))
            }
        }
        $(impl_field_value_try_into_T!($($rest)*);)?
    };

    ( $Name:ident, $T:ty $(, $($rest:tt)*)? ) => {
        paste! {
            pub fn [<get_key_ $Name>](
                map: &QueryResult,
                key: &str,
            ) -> Result<$T> {
                let value = map.get(key)
                    .ok_or(Error::TrustfallQueryResultMissingKey(key.into()))?;
                value.$Name()
                    .ok_or(Error::TrustfallQueryResultTypeMismatch(
                        key.into(),
                        stringify!($T).into(),
                    ))
            }
        }
        $(impl_field_value_try_into_T!($($rest)*);)?
    };
}

impl_field_value_try_into_T!(
    as_slice, &[FieldValue],
    as_str, &str,
    as_usize, usize,
);