use serde::Serialize;
use tracing::info;

use crate::document::query::QueryResult;
use crate::document::{TryFromApiDoc, query::DocQuerier, r#type::TypeKind};
use crate::{error::*, utils};
use super::super::utils as doc_utils;

#[derive(Serialize)]
pub struct Struct {
    pub name: String,
    pub docs: String,
    #[serde(rename = "type")]
    pub type_: TypeKind,
    pub path: String,
    pub traits: Vec<String>,
    pub fields: Vec<StructField>,
}

#[derive(Serialize)]
pub struct StructField {
    pub name: String,
    pub docs: String,
    pub type_: String,
}

impl TryFromApiDoc<Vec<Struct>> for Struct {
    fn try_from_api_doc(querier: &DocQuerier) -> Result<Vec<Struct>> {
        info!("Parsing Struct documentation");

        info!("Querying structs from documentation");
        let struct_query = utils::config().get_string("querys.struct")?;
        let struct_query = utils::read_file_to_string(struct_query)?;
        let structs = querier.query()
            .add_variable("public", true)
            .add_variable("deprecated", false)
            .execute(&struct_query, |results| -> Result<Vec<Struct>>{
                results.map(|result| Struct::try_from(&result)).collect()
            })??;
        info!("Found {} public structs in documentation", structs.len());

        Ok(structs)
    }
}

impl TryFrom<&QueryResult> for Struct {
    type Error = Error;

    fn try_from(value: &QueryResult) -> Result<Self> {
        Ok(Struct {
            name: doc_utils::get_key_as_str(value, "struct_name")?.to_string(),
            docs: doc_utils::get_key_as_str(value, "struct_docs")?.to_string(),
            type_: TypeKind::Struct,
            path: doc_utils::parse_path(value, "importable_path", "canonical_path")?,
            traits: {
                let trait_names = doc_utils::get_key_as_slice(value, "trait_name")?;
                doc_utils::parse_slice_elem_as_str(trait_names, Some("trait_name"))?
                    .into_iter().map(|s| s.to_string())
                    .collect()
            },
            fields: parse_struct_fields(value)?,
        })
    }
}

fn parse_struct_fields(value: &QueryResult) -> Result<Vec<StructField>> {
    let field_names = doc_utils::get_key_as_slice(value, "field_name")?;
    let field_names = doc_utils::parse_slice_elem_as_str(field_names, Some("field_name"))?;
    let field_docs = doc_utils::get_key_as_slice(value, "field_docs")?;
    let field_docs = doc_utils::parse_slice_elem_as_str(field_docs, Some("field_docs"))?;
    let field_type_names = doc_utils::get_key_as_slice(value, "field_type_name")?;
    let field_type_names = doc_utils::parse_slice_elem_as_str(field_type_names, Some("field_type_name"))?;
    if field_names.len() != field_docs.len() || field_names.len() != field_type_names.len() {
        return Err(Error::TrustfallQueryError("Mismatched lengths of struct field attributes".to_string()));
    }

    let fields = field_names.into_iter().zip(field_docs.into_iter()).zip(field_type_names.into_iter())
        .map(|((name, docs), type_name)| StructField {
            name: name.to_string(),
            docs: docs.to_string(),
            type_: type_name.to_string(),
        })
        .collect();
    Ok(fields)
}