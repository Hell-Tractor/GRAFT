use serde::Serialize;
use tracing::info;

use crate::{document::{TryFromApiDoc, query::{DocQuerier, QueryResult}, r#type::{StructField, TypeKind}}, utils};
use crate::error::*;
use super::super::utils as doc_utils;

#[derive(Serialize)]
pub struct Enum {
    pub name: String,
    pub docs: String,
    #[serde(rename = "type")]
    pub type_: TypeKind,
    pub path: String,
    pub traits: Vec<rustdoc_types::GenericBound>,
    pub variants: Vec<EnumVariant>,
}

impl TryFromApiDoc<Vec<Enum>> for Enum {
    fn try_from_api_doc(querier: &DocQuerier) -> Result<Vec<Enum>> {
        info!("Parsing Enum documentation");

        info!("Querying enums from documentation");
        let enum_query = utils::config().get_string("querys.enum")?;
        let enum_query = utils::read_file_to_string(enum_query)?;
        let enums = querier.query()
            .add_variable("public", true)
            .add_variable("deprecated", false)
            .execute(&enum_query, |results| -> Result<Vec<Enum>>{
                results.map(|result| Enum::try_from(&result)).collect()
            })??;
        info!("Found {} public enums in documentation", enums.len());

        Ok(enums)
    }
}

impl TryFrom<&QueryResult> for Enum {
    type Error = Error;

    fn try_from(value: &QueryResult) -> Result<Self> {
        Ok(Enum {
            name: doc_utils::get_key_as_str(value, "enum_name")?.to_string(),
            docs: doc_utils::get_key_as_str(value, "enum_docs").unwrap_or("").to_string(),
            type_: TypeKind::Enum,
            path: doc_utils::parse_path(value, "importable_path", "canonical_path")?,
            traits: {
                doc_utils::get_key_as_slice(value, "trait_name")?
                    .iter()
                    .map(|v| {
                        let s = doc_utils::parse_field_value_as_str(v, "trait_name")?;
                        let trait_bound = serde_json::from_str(s)?;
                        Ok(trait_bound)
                    })
                    .collect::<Result<Vec<rustdoc_types::GenericBound>>>()?
            },
            variants: {
                let variant_names = doc_utils::get_key_as_vec_with(value, "variant_name", |fv| {
                    doc_utils::parse_field_value_as_str(fv, "variant_name[*]").map(str::to_string)
                })?;
                let variant_docs = doc_utils::get_key_as_vec_with(value, "variant_docs", |fv| {
                    Ok(doc_utils::parse_field_value_as_str(fv, "variant_docs[*]").unwrap_or("").to_string())
                })?;
                let variant_field_names = doc_utils::get_key_as_vec_with(value, "field_name", |fv| {
                    doc_utils::parse_field_value_as_vec_with(fv, "field_name[*]", |fv| {
                        doc_utils::parse_field_value_as_str(fv, "field_name[*][*]").map(str::to_string)
                    })
                })?;
                let variant_field_docs = doc_utils::get_key_as_vec_with(value, "field_docs", |fv| {
                    doc_utils::parse_field_value_as_vec_with(fv, "field_docs[*]", |fv| {
                        Ok(doc_utils::parse_field_value_as_str(fv, "field_docs[*][*]").unwrap_or("").to_string())
                    })
                })?;
                let variant_field_type_names = doc_utils::get_key_as_vec_with(value, "field_type_name", |fv| {
                    doc_utils::parse_field_value_as_vec_with(fv, "field_type_name[*]", |fv| {
                        doc_utils::parse_field_value_as_str(fv, "field_type_name[*][*]").map(str::to_string)
                    })
                })?;
                if !is_same_len!(variant_names, variant_docs, variant_field_names, variant_field_docs, variant_field_type_names) {
                    return Err(Error::TrustfallQueryError("Mismatched lengths of enum variant attributes".to_string()));
                }
                variant_names.into_iter()
                    .zip(variant_docs.into_iter())
                    .zip(variant_field_names.into_iter())
                    .zip(variant_field_docs.into_iter())
                    .zip(variant_field_type_names.into_iter())
                    .map(|((((name, docs), field_names), field_docs), field_type_names)| {
                        if !is_same_len!(field_names, field_docs, field_type_names) {
                            return Err(Error::TrustfallQueryError("Mismatched lengths of enum variant field attributes".to_string()));
                        }
                        let fields = field_names.into_iter()
                            .zip(field_docs.into_iter())
                            .zip(field_type_names.into_iter())
                            .map(|((field_name, field_docs), field_type_name)| {
                                StructField {
                                    name: field_name,
                                    docs: field_docs,
                                    type_: field_type_name,
                                }
                            })
                            .collect();
                        Ok(EnumVariant {
                            name,
                            docs,
                            fields,
                        })
                    }).collect::<Result<Vec<EnumVariant>>>()?
            }
        })
    }
}

#[derive(Serialize)]
pub struct EnumVariant {
    pub name: String,
    pub docs: String,
    pub fields: Vec<StructField>,
}