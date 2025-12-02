use std::io::Write;

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
    pub generics: Vec<APIGeneric>,
    pub inputs: Vec<Parameter>,
    pub output: ReturnValue,
    pub impl_for: Option<rustdoc_types::Type>,
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
                        // api.inputs.iter_mut().for_each(|param| {
                        //     param.type_ = param.type_.replace("Self", &struct_name);
                        // });
                        // api.output.type_ = api.output.type_.replace("Self", &struct_name);
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
        let generic_names = super::utils::get_key_as_vec_with(value, "generic_type_param_name", |fv| {
            super::utils::parse_field_value_as_str(fv, "generic_type_param_name[*]").map(str::to_string)
        })?;
        let generic_type_bounds = super::utils::get_key_as_vec_with(value, "type_bound_name", |fv| {
            super::utils::parse_field_value_as_vec_with(fv, "type_bound_name[*]", |fv| {
                let bound = super::utils::parse_field_value_as_str(fv, "type_bound_name[*][*]").map(str::to_string)?;
                // deserialize to GenericBound
                serde_json::from_str(&bound)
                    .map_err(|e| Error::DeserializeError(format!("API Generic bound deserialization error: {}\nOrigin strnig: {}", e, bound)))
            })
        })?;
        if !is_same_len!(generic_names, generic_type_bounds) {
            warn!("Length of `generic_type_param_name` and `type_bound_name` do not match. This isn't expected to happen.");
            return Err(Error::TrustfallQueryResultLengthMissmatch("generic_type_param_name".into(), "type_bound_name".into()));
        }
        let mut generics = generic_names.into_iter().zip(generic_type_bounds.into_iter())
            .map(|(name, type_bound)| APIGeneric {
                name,
                type_bound,
                // allowed_types: Vec::new(), // calculated later
            })
            .collect::<Vec<_>>();
        // parse struct generics
        if value.contains_key("struct_generic_type_param_name") {
        // stop doing this for now.
        // if false {
            let struct_generic_names = super::utils::get_key_as_vec_with(value, "struct_generic_type_param_name", |fv| {
                super::utils::parse_field_value_as_str(fv, "struct_generic_type_param_name[*]").map(str::to_string)
            })?;
            let struct_generic_type_bounds = super::utils::get_key_as_vec_with(value, "struct_type_bound_name", |fv| {
                super::utils::parse_field_value_as_vec_with(fv, "struct_type_bound_name[*]", |fv| {
                    let bound = super::utils::parse_field_value_as_str(fv, "struct_type_bound_name[*][*]").map(str::to_string)?;
                    // deserialize to GenericBound
                    serde_json::from_str(&bound)
                        .map_err(|e| Error::DeserializeError(format!("API Struct Generic bound deserialization error: {}\nOrigin string: {}", e, bound)))
                })
            })?;
            if !is_same_len!(struct_generic_names, struct_generic_type_bounds) {
                warn!("Length of `struct_generic_type_param_name` and `struct_type_bound_name` do not match. This isn't expected to happen.");
                return Err(Error::TrustfallQueryResultLengthMissmatch("struct_generic_type_param_name".into(), "struct_type_bound_name".into()));
            }
            let struct_generics = struct_generic_names.into_iter().zip(struct_generic_type_bounds.into_iter())
                .map(|(name, type_bound)| APIGeneric {
                    name,
                    type_bound,
                    // allowed_types: Vec::new(), // calculated later
                })
                .collect::<Vec<_>>();
            generics.extend(struct_generics);
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
                .ok_or(Error::TrustfallQueryResultTypeMismatch("param_type".into(), "string".into()))?;
            let type_ = serde_json::from_str::<rustdoc_types::Type>(type_)
                .map_err(|e| Error::TrustfallQueryError(format!("Param `{}` deserialization error: {}", name, e)))?;
            inputs.push(Parameter { name, type_ });
        }

        // parse impl_for
        let impl_for = value.get("impl_for")
            .map(|fv| fv.as_str().expect("impl_for should be a string"))
            .map(|type_| {
                serde_json::from_str::<rustdoc_types::Type>(type_)
                    .expect("Impl for type deserialization error")
            });

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
            doc_comment: super::utils::get_key_as_str(value, "doc_comment").unwrap_or("").to_string(),
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
                type_: {
                    let type_ = super::utils::get_key_as_str(value, "return_type")?;
                    let type_ = serde_json::from_str(type_)
                        .map_err(|e| Error::TrustfallQueryError(format!("Return value deserialization error: {}", e)))?;
                    type_
                },
            },
            impl_for,
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
    pub type_: rustdoc_types::Type,
}

#[derive(Serialize)]
pub struct ReturnValue {
    #[serde(rename = "type")]
    pub type_: Option<rustdoc_types::Type>,
}

impl ReturnValue {
    /// 获取被包装的类型，例如 Option<T> 返回 T，Result<T, E> 返回 T
    pub fn get_wrapped_type(&self) -> Option<rustdoc_types::Type> {
        match &self.type_ {
            Some(rustdoc_types::Type::ResolvedPath(path)) => {
                let type_name = path.path.split("::").last().unwrap_or(&path.path);
                if type_name == "Option" || type_name == "Result" {
                    if let Some(args) = &path.args {
                        if let rustdoc_types::GenericArgs::AngleBracketed { args, .. } = &**args {
                            if let rustdoc_types::GenericArg::Type(ty) = &args[0] {
                                return Some(ty.clone());
                            }
                            panic!("Unexpected GenericArg in Option/Result");
                        }
                        panic!("Unexpected GenericArgs in Option/Result");
                    } else {
                        self.type_.clone()
                    }
                } else {
                    self.type_.clone()
                }
            }
            _ => self.type_.clone(),
        }
    }
}

#[derive(Serialize)]
pub struct APIGeneric {
    pub name: String,
    pub type_bound: Vec<rustdoc_types::GenericBound>,
    // pub allowed_types: Vec<String>,
}

// impl APIGeneric {
//     pub fn calculate_allowed_types(&mut self, types: &Types) {
//         self.allowed_types = types.structs.iter()
//             .filter(|type_| self.type_bound.iter().all(|bound| type_.traits.contains(bound)))
//             .map(|type_| type_.name.clone())
//             .collect();
//     }
// }