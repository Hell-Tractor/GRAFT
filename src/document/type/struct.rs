use serde::Serialize;
use tracing::info;

use crate::document::query::QueryResult;
use crate::document::r#type::StructField;
use crate::document::utils::parse_struct_fields;
use crate::document::{TryFromApiDoc, query::DocQuerier, r#type::TypeKind};
use crate::{error::*, utils};
use super::super::utils as doc_utils;

#[derive(Serialize)]
pub struct Struct {
    pub id: u32,
    pub name: String,
    pub docs: String,
    #[serde(rename = "type")]
    pub type_: TypeKind,
    pub path: String,
    pub traits: Vec<rustdoc_types::GenericBound>,
    pub fields: Vec<StructField>,
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
            id: doc_utils::get_key_as_str(value, "id")?.parse().expect("Id of struct is not a number."),
            name: doc_utils::get_key_as_str(value, "struct_name")?.to_string(),
            docs: doc_utils::get_key_as_str(value, "struct_docs").unwrap_or("").to_string(),
            type_: TypeKind::Struct,
            path: doc_utils::parse_path(value, "importable_path", "canonical_path")?,
            traits: {
                doc_utils::get_key_as_vec_with(value, "trait_name", |fv| -> Result<rustdoc_types::GenericBound>{
                    let bound = doc_utils::parse_field_value_as_str(fv, "trait_name[*]")?;
                    let bound = serde_json::from_str::<rustdoc_types::GenericBound>(bound)?;
                    Ok(bound)
                })?
            },
            fields: parse_struct_fields(value, "field_name", "field_docs", "field_type_name")?,
        })
    }
}