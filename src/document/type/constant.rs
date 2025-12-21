use serde::Serialize;
use tracing::info;

use crate::document::{TryFromApiDoc, query::QueryResult};
use crate::{error::*, utils};
use crate::document::utils as doc_utils;

#[derive(Serialize)]
pub struct Constant {
    pub id: u32,
    pub name: String,
    pub docs: String,
    #[serde(rename = "type")]
    pub type_: rustdoc_types::Type,
    pub path: String,
}

impl TryFromApiDoc<Vec<Constant>> for Constant {
    fn try_from_api_doc(querier: &crate::document::query::DocQuerier) -> crate::error::Result<Vec<Constant>> {
        info!("Parsing Constant documentation");

        info!("Querying constants from documentation");
        let constant_query = &utils::config().querys.constant;
        let constant_query = utils::read_file_to_string(constant_query)?;
        let constants = querier.query()
            .add_variable("public", true)
            .add_variable("deprecated", false)
            .execute(&constant_query, |results| -> Result<Vec<Constant>>{
                results.map(|result| Constant::try_from(&result)).collect()
            })??;
        info!("Found {} public constants in documentation", constants.len());

        Ok(constants)
    }
}

impl TryFrom<&QueryResult> for Constant {
    type Error = Error;

    fn try_from(value: &QueryResult) -> Result<Self> {
        Ok(Constant {
            id: doc_utils::get_key_as_str(value, "id")?.parse().expect("Id of constant is not a number."),
            name: doc_utils::get_key_as_str(value, "constant_name")?.to_string(),
            docs: doc_utils::get_key_as_str(value, "constant_docs").unwrap_or("").to_string(),
            type_: {
                let type_str = doc_utils::get_key_as_str(value, "raw_type")?;
                serde_json::from_str(type_str).map_err(|_| Error::DeserializeError(type_str.to_owned()))?
            },
            path: doc_utils::parse_path(value, "importable_path", "canonical_path")?,
        })
    }
}