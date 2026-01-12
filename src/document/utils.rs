use trustfall::FieldValue;

use crate::{document::r#type::StructField, error::*};
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

pub fn get_key_as_vec_with<T, U>(map: &QueryResult, key: &str, f: T) -> Result<Vec<U>>
where T: Fn(&FieldValue) -> Result<U>
{
    let vec = get_key_as_slice(map, key)?
        .iter()
        .map(f)
        .collect::<Result<Vec<U>>>()?;
    Ok(vec)
}

pub fn parse_field_value_as_vec_with<T, U>(value: &FieldValue, value_name: &str, f: T) -> Result<Vec<U>>
where T: Fn(&FieldValue) -> Result<U>
{
    let vec = parse_field_value_as_slice(value, value_name)?
        .into_iter()
        .map(f)
        .collect::<Result<Vec<U>>>()?;
    Ok(vec)
}

pub fn parse_struct_fields(value: &QueryResult, field_name_key: &str, field_docs_key: &str, field_type_name_key: &str) -> Result<Vec<StructField>> {
    let field_names = get_key_as_vec_with(value, field_name_key, |fv| {
        parse_field_value_as_str(fv, &format!("{}[*]", field_name_key)).map(str::to_string)
    })?;
    let field_docs = get_key_as_vec_with(value, field_docs_key, |fv| {
        Ok(parse_field_value_as_str(fv, &format!("{}[*]", field_docs_key)).unwrap_or("").to_string())
    })?;
    let field_type_names = get_key_as_vec_with(value, field_type_name_key, |fv| {
        parse_field_value_as_str(fv, &format!("{}[*]", field_type_name_key)).map(str::to_string)
    })?;
    if field_names.len() != field_docs.len() || field_names.len() != field_type_names.len() {
        return Err(Error::TrustfallQueryError("Mismatched lengths of struct field attributes".to_string()));
    }

    let fields = field_names.into_iter().zip(field_docs.into_iter()).zip(field_type_names.into_iter())
        .map(|((name, docs), type_name)| -> Result<StructField> {
            let type_ = serde_json::from_str::<rustdoc_types::Type>(&type_name)
                .map_err(|e| Error::TrustfallQueryError(format!("Struct field type deserialization error: {}", e)))?;
            Ok(StructField {
                name: name,
                docs: docs,
                type_,
            })
        })
        .collect::<Result<Vec<StructField>>>()?;
    Ok(fields)
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

macro_rules! impl_field_value_try_into_T {
    () => {};

    ( $Name:ident, & $T:ty $(, $($rest:tt)*)? ) => {
        paste! {
            pub fn [<parse_field_value_ $Name>]<'a>(
                value: &'a FieldValue,
                value_name: &str,
            ) -> Result<&'a $T> {
                value.$Name().ok_or(Error::TrustfallQueryResultTypeMismatch(
                    value_name.into(),
                    stringify!($T).into(),
                ))
            }
        }
        $(impl_field_value_try_into_T!($($rest)*);)?
    };

    ( $Name:ident, $T:ty $(, $($rest:tt)*)? ) => {
        paste! {
            pub fn [<parse_field_value_ $Name>](
                value: &FieldValue,
                value_name: &str,
            ) -> Result<$T> {
                value.$Name().ok_or(Error::TrustfallQueryResultTypeMismatch(
                    value_name.into(),
                    stringify!($T).into(),
                ))
            }
        }
        $(impl_field_value_try_into_T!($($rest)*);)?
    };
}

impl_field_value_try_into_T!(
    as_str, &str,
    as_slice, &[FieldValue],
);