use std::{collections::HashMap, io::Write};

use serde::Serialize;
use tracing::{info, warn};

use crate::{error::{Error, Result}, utils};
use super::{TryFromApiDoc, query};

#[derive(Serialize)]
pub struct API {
    pub name: String,
    pub kind: APIKind,
    pub signature: String,
    pub module_path: String,
    pub doc_comment: String,
    pub location: Location,
    pub generics: HashMap<String, String>, // generic parameter name -> type bound
    pub inputs: Vec<Parameter>,
    pub output: ReturnValue,
    // pub is_public: bool,
    // pub is_safe: bool,
    // pub has_unsafe: bool,
    // pub recommended_fuzz_type: FuzzType,
    // pub notes_for_fuzzer: String,
}

impl TryFromApiDoc<Vec<API>> for API {
    fn try_from_api_doc(querier: &query::DocQuerier) -> Result<Vec<Self>> {
        info!("Parsing API documentation");

        // query functions
        info!("Querying functions from API documentation");
        let function_query = utils::config().get_string("querys.function")?;
        let function_query = utils::read_file_to_string(function_query)?;
        let function = querier.query()
            .add_variable("public", true)
            .add_variable("deprecated", false)
            .execute(&function_query, |results| -> Result<Vec<API>> {
                let debugfile = std::fs::File::create("temp_func_query_result").unwrap();
                let mut debugwriter = std::io::BufWriter::new(debugfile);
                results.map(|result| {
                    writeln!(debugwriter, "{:#?}", result).unwrap();
                    API::try_from(&result)
                }).collect()
            })??;
        info!("Found {} public functions in API documentation", function.len());

        // query methods
        info!("Querying methods from API documentation");
        let method_query = utils::config().get_string("querys.method")?;
        let method_query = utils::read_file_to_string(method_query)?;
        let method = querier.query()
            .add_variable("public", true)
            .add_variable("deprecated", false)
            .execute(&method_query, |results| -> Result<Vec<API>> {
                let debugfile = std::fs::File::create("temp_method_query_result").unwrap();
                let mut debugwriter = std::io::BufWriter::new(debugfile);
                results.map(|result| {
                    writeln!(debugwriter, "{:#?}", result).unwrap();
                    let struct_name = result.get("struct_name").ok_or(Error::TrustfallQueryError("Missing key `struct_name` in result".to_string()))?.as_str().ok_or(Error::TrustfallQueryError("Key `struct_name` is not a string".to_string()))?.to_string();
                    API::try_from(&result).map(|mut api| {
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

impl TryFrom<&query::QueryResult> for API {
    type Error = Error;

    fn try_from(value: &query::QueryResult) -> std::result::Result<Self, Self::Error> {
        // parse generics
        let mut generics = HashMap::new();
        let generic_params = super::utils::get_key_as_slice(value, "generic_type_param_name")?;
        let type_bounds = super::utils::get_key_as_slice(value, "type_bound_name")?;
        if generic_params.len() != type_bounds.len() {
            warn!("Length of `generic_type_param_name` and `type_bound_name` do not match. This isn't expected to happen.");
            return Err(Error::TrustfallQueryResultLengthMissmatch("generic_type_param_name".into(), "type_bound_name".into()));
        }
        for i in 0..generic_params.len() {
            let param = generic_params[i].as_str()
                .ok_or(Error::TrustfallQueryResultTypeMismatch(format!("generic_type_param_name[{}]", i), "string".into()))?
                .to_string();
            let bound = type_bounds[i].as_slice()
                .ok_or(Error::TrustfallQueryResultTypeMismatch(format!("type_bound_name[{}]", i), "slice".into()))?
                .iter()
                .map(|s| s.as_str().ok_or(Error::TrustfallQueryResultTypeMismatch(format!("type_bound_name[{}][*]", i), "string".into()))
                .map(|s| s.to_string()))
                .collect::<Result<Vec<String>>>()?
                .join(" + ");
            generics.insert(param, bound);
        }

        // parse inputs
        let mut inputs = Vec::new();
        let param_names = super::utils::get_key_as_slice(value, "param_name")?;
        let param_types = super::utils::get_key_as_slice(value, "param_type")?;
        if param_names.len() != param_types.len() {
            warn!("Length of `param_name` and `param_type` do not match. This isn't expected to happen.");
            return Err(Error::TrustfallQueryResultLengthMissmatch("param_name".into(), "param_type".into()));
        }
        for i in 0..param_names.len() {
            let name = param_names[i].as_str()
                .ok_or(Error::TrustfallQueryResultTypeMismatch("param_name".into(), "string".into()))?.to_string();
            let type_ = param_types[i].as_str()
                .ok_or(Error::TrustfallQueryResultTypeMismatch("param_type".into(), "string".into()))?.to_string();
            inputs.push(Parameter { name, type_ });
        }

        let api = API {
            name: super::utils::get_key_as_str(value, "name")?.to_string(),
            kind: {
                let item_type = super::utils::get_key_as_str(value, "item_type")?;
                match item_type {
                    "Function" => Ok(APIKind::Function),
                    "Struct" => Ok(APIKind::Method),
                    _ => Err(Error::TrustfallQueryError(format!("Unknown item type `{}`", item_type))),
                }
            }?,
            signature: super::utils::get_key_as_str(value, "signature")?.to_string(),
            module_path: super::utils::parse_path(value, "module_path", "canonical_path")?,
            doc_comment: super::utils::get_key_as_str(value, "doc_comment")?.to_string(),
            location: Location {
                file: {
                    let raw_path = super::utils::get_key_as_str(value, "file")?.to_string();
                    // remove file path before "src/"
                    raw_path.split("src/").skip(2)
                        .map(|s| format!("src/{}", s))
                        .next()
                        .unwrap_or(raw_path)
                },
                line: super::utils::get_key_as_usize(value, "line")?,
            },
            generics,
            inputs,
            output: ReturnValue {
                type_: super::utils::get_key_as_str(value, "return_type")?.to_string(),
            },
        };
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

#[derive(Serialize)]
pub struct Location {
    pub file: String,
    pub line: usize,
}

#[derive(Serialize)]
pub struct Parameter {
    pub name: String,
    #[serde(rename = "type")]
    pub type_: String,
}

#[derive(Serialize)]
pub struct ReturnValue {
    #[serde(rename = "type")]
    pub type_: String,
}