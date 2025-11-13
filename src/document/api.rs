use std::{collections::BTreeMap, sync::Arc};

use serde::Serialize;
use tracing::info;

use crate::{document::query::DocQuerier, error::{Error, Result}, utils};

#[derive(Serialize)]
pub struct API {
    pub name: String,
    pub kind: APIKind,
    pub signature: String,
    pub module_path: String,
    pub doc_comment: String,
    pub location: Location,
    // pub inputs: Vec<Parameter>,
    // pub output: ReturnValue,
    // pub is_public: bool,
    // pub is_safe: bool,
    // pub has_unsafe: bool,
    // pub recommended_fuzz_type: FuzzType,
    // pub notes_for_fuzzer: String,
}

impl API {
    pub fn from_api_doc(doc: &str) -> Result<Vec<Self>> {
        info!("Parsing API documentation");
        // init querier
        let querier = DocQuerier::new(doc)?;

        // query functions
        info!("Querying functions from API documentation");
        let function_query = utils::config().get_string("querys.function")?;
        let function_query = utils::read_file_to_string(function_query)?;
        let function = querier.query()
            .add_variable("v", true)
            .execute(&function_query, |results| -> Result<Vec<API>> {
                results.map(API::try_from).collect()
            })??;
        info!("Found {} public functions in API documentation", function.len());

        // query methods
        info!("Querying methods from API documentation");
        let method_query = utils::config().get_string("querys.method")?;
        let method_query = utils::read_file_to_string(method_query)?;
        let method = querier.query()
            .add_variable("v", true)
            .execute(&method_query, |results| -> Result<Vec<API>> {
                results.map(|result| {
                    let struct_name = result.get("struct_name").ok_or(Error::TrustfallQueryError("Missing key `struct_name` in result".to_string()))?.as_str().ok_or(Error::TrustfallQueryError("Key `struct_name` is not a string".to_string()))?.to_string();
                    API::try_from(result).map(|mut api| {
                        api.signature = api.signature.replace("Self", &struct_name);
                        api
                    })
                }).collect()
            })??;
        info!("Found {} public methods in API documentation", method.len());

        // merge results
        info!("Merging function and method results");
        let mut apis = function;
        apis.extend(method);
        info!("Total {} public APIs found", apis.len());
        Ok(apis)
    }
}

impl TryFrom<BTreeMap<Arc<str>, trustfall::FieldValue>> for API {
    type Error = Error;
    
    fn try_from(value: BTreeMap<Arc<str>, trustfall::FieldValue>) -> std::result::Result<Self, Self::Error> {
        let mut api = API {
            name: value.get("name").ok_or(Error::TrustfallQueryError("Missing key `name` in result".to_string()))?.as_str().ok_or(Error::TrustfallQueryError("Key `name` is not a string".to_string()))?.to_string(),
            kind: {
                let item_type = value.get("item_type").ok_or(Error::TrustfallQueryError("Missing key `item_type` in result".to_string()))?.as_str().ok_or(Error::TrustfallQueryError("Key `item_type` is not a string".to_string()))?;
                match item_type {
                    "Function" => Ok(APIKind::Function),
                    "Struct" => Ok(APIKind::Method),
                    _ => Err(Error::TrustfallQueryError(format!("Unknown item type `{}`", item_type))),
                }
            }?,
            signature: value.get("signature").ok_or(Error::TrustfallQueryError("Missing key `signature` in result".to_string()))?.as_str().ok_or(Error::TrustfallQueryError("Key `signature` is not a string".to_string()))?.to_string(),
            module_path: value.get("module_path").ok_or(Error::TrustfallQueryError("Missing key `module_path` in result".to_string()))?.as_slice().ok_or(Error::TrustfallQueryError("Key `module_path` is not a slice".to_string()))?.iter()
                .map(|s| s.as_str().ok_or(Error::TrustfallQueryError("Element in `module_path` is not a string".to_string())).map(|s| s.to_string()))
                .collect::<Result<Vec<String>>>()?
                .join("::"),
            doc_comment: value.get("doc_comment").ok_or(Error::TrustfallQueryError("Missing key `doc_comment` in result".to_string()))?.as_str().unwrap_or("").to_string(),
            location: Location {
                file: value.get("file").ok_or(Error::TrustfallQueryError("Missing key `file` in result".to_string()))?.as_str().ok_or(Error::TrustfallQueryError("Key `file` is not a string".to_string()))?.to_string(),
                line: value.get("line").ok_or(Error::TrustfallQueryError("Missing key `line` in result".to_string()))?.as_u64().ok_or(Error::TrustfallQueryError("Key `line` is not a u64".to_string()))? as usize,
            },
        };
        // remove file path before "src/"
        api.location.file = api.location.file.split("src/").skip(2).map(|s| format!("src/{}", s)).next().unwrap_or(api.location.file);
        Ok(api)
    }
}

#[derive(Serialize)]
pub enum APIKind {
    Function,
    Method,
}

#[allow(unused)]
#[derive(Serialize)]
pub enum FuzzType {
    String,
    Custom,
    Bytes,
    Integer,
}

#[allow(unused)]
#[derive(Serialize)]
pub struct Location {
    pub file: String,
    pub line: usize,
}

#[allow(unused)]
#[derive(Serialize)]
pub struct Parameter {
    pub name: String,
    #[serde(rename = "type")]
    pub type_name: String,
    pub description: String,
    pub fuzz_strategy_suggestion: String,
}

#[allow(unused)]
#[derive(Serialize)]
pub struct ReturnValue {
    #[serde(rename = "type")]
    pub type_name: String,
    pub description: String,
}