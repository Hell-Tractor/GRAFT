use std::{collections::{BTreeMap, HashMap}, io::Write, sync::Arc};

use serde::Serialize;
use tracing::{info, warn};

use crate::{document::query::DocQuerier, error::{Error, Result}, utils};

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
            .add_variable("public", true)
            .add_variable("deprecated", false)
            .execute(&function_query, |results| -> Result<Vec<API>> {
                let debugfile = std::fs::File::create("temp_func_query_result").unwrap();
                let mut debugwriter = std::io::BufWriter::new(debugfile);
                results.map(|result| {
                    writeln!(debugwriter, "{:#?}", result).unwrap();
                    API::try_from(result)
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
        // parse generics
        let mut generics = HashMap::new();
        let generic_params = value.get("generic_type_param_name").ok_or(Error::TrustfallQueryError("Missing key `generic_type_param_name` in result".to_string()))?.as_slice().ok_or(Error::TrustfallQueryError("Key `generic_type_param_name` is not a slice".to_string()))?;
        let type_bounds = value.get("type_bound_name").ok_or(Error::TrustfallQueryError("Missing key `type_bound_name` in result".to_string()))?.as_slice().ok_or(Error::TrustfallQueryError("Key `type_bound_name` is not a slice".to_string()))?;
        if generic_params.len() != type_bounds.len() {
            warn!("Length of `generic_type_param_name` and `type_bound_name` do not match. This isn't expected to happen.");
            return Err(Error::TrustfallQueryError("Length of `generic_type_param_name` and `type_bound_name` do not match".to_string()));
        }
        for i in 0..generic_params.len() {
            let param = generic_params[i].as_str().ok_or(Error::TrustfallQueryError("Element in `generic_type_param_name` is not a string".to_string()))?.to_string();
            let bound = type_bounds[i].as_slice().ok_or(Error::TrustfallQueryError("Element in `type_bound_name` is not a slice".to_string()))?
                .iter()
                .map(|s| s.as_str().ok_or(Error::TrustfallQueryError("Element in `type_bound_name` slice is not a string".to_string())).map(|s| s.to_string()))
                .collect::<Result<Vec<String>>>()?
                .join(" + ");
            generics.insert(param, bound);
        }

        // parse inputs
        let mut inputs = Vec::new();
        let param_names = value.get("param_name").ok_or(Error::TrustfallQueryError("Missing key `param_name` in result".to_string()))?.as_slice().ok_or(Error::TrustfallQueryError("Key `param_name` is not a slice".to_string()))?;
        let param_types = value.get("param_type").ok_or(Error::TrustfallQueryError("Missing key `param_type` in result".to_string()))?.as_slice().ok_or(Error::TrustfallQueryError("Key `param_type` is not a slice".to_string()))?;
        if param_names.len() != param_types.len() {
            warn!("Length of `param_name` and `param_type` do not match. This isn't expected to happen.");
            return Err(Error::TrustfallQueryError("Length of `param_name` and `param_type` do not match".to_string()));
        }
        for i in 0..param_names.len() {
            let name = param_names[i].as_str().ok_or(Error::TrustfallQueryError("Element in `param_name` is not a string".to_string()))?.to_string();
            let type_ = param_types[i].as_str().ok_or(Error::TrustfallQueryError("Element in `param_type` is not a string".to_string()))?.to_string();
            inputs.push(Parameter { name, type_ });
        }

        // parse module path
        let canonical_path = value.get("canonical_path").ok_or(Error::TrustfallQueryError("Missing key `canonical_path` in result".to_string()))?.as_slice().ok_or(Error::TrustfallQueryError("Key `canonical_path` is not a slice".to_string()))?;
        let module_path = value.get("module_path").ok_or(Error::TrustfallQueryError("Missing key `module_path` in result".to_string()))?.as_slice().ok_or(Error::TrustfallQueryError("Key `module_path` is not a slice".to_string()))?.iter()
            .next().map_or(Ok(canonical_path), |path| path.as_slice().ok_or(Error::TrustfallQueryError("Element in `module_path` is not a slice".to_string())))?
            .iter()
            .map(|path| path.as_str().ok_or(Error::TrustfallQueryError("Element in `module_path` slice is not a string".to_string())).map(|s| s.to_string()))
            .collect::<Result<Vec<String>>>()?
            .join("::");

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
            module_path,
            doc_comment: value.get("doc_comment").ok_or(Error::TrustfallQueryError("Missing key `doc_comment` in result".to_string()))?.as_str().unwrap_or("").to_string(),
            location: Location {
                file: value.get("file").ok_or(Error::TrustfallQueryError("Missing key `file` in result".to_string()))?.as_str().ok_or(Error::TrustfallQueryError("Key `file` is not a string".to_string()))?.to_string(),
                line: value.get("line").ok_or(Error::TrustfallQueryError("Missing key `line` in result".to_string()))?.as_u64().ok_or(Error::TrustfallQueryError("Key `line` is not a u64".to_string()))? as usize,
            },
            generics,
            inputs,
            output: ReturnValue {
                type_: value.get("return_type").ok_or(Error::TrustfallQueryError("Missing key `return_type` in result".to_string()))?.as_str().ok_or(Error::TrustfallQueryError("Key `return_type` is not a string".to_string()))?.to_string(),
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