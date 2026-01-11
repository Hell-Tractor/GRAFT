use tracing::{info, warn};
use std::io::Write;

use crate::{document::{APIDocParser, api::{API, APIGeneric, APIKind, Location, Parameter, ReturnValue}, query::{DocQuerier, QueryResult}, r#type::{StructField, TypeKind, constant::Constant, r#enum::{Enum, EnumVariant}, r#struct::Struct}}, error::*, utils};
use crate::document::utils as doc_utils;

impl APIDocParser<Vec<Enum>> for Enum {
    type Parser = DocQuerier;

    fn parse(querier: &Self::Parser) -> Result<Vec<Enum>> {
        info!("Parsing Enum documentation");

        info!("Querying enums from documentation");
        let enum_query = &utils::config().querys.enum_;
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
            id: doc_utils::get_key_as_str(value, "id")?.parse().expect("Id of enum is not a number."),
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

impl APIDocParser<Vec<Constant>> for Constant {
    type Parser = DocQuerier;

    fn parse(querier: &Self::Parser) -> Result<Vec<Constant>> {
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

impl APIDocParser<Vec<Struct>> for Struct {
    type Parser = crate::document::query::DocQuerier;

    fn parse(querier: &Self::Parser) -> Result<Vec<Struct>> {
        info!("Parsing Struct documentation");

        info!("Querying structs from documentation");
        let struct_query = &utils::config().querys.struct_;
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
            fields: doc_utils::parse_struct_fields(value, "field_name", "field_docs", "field_type_name")?,
        })
    }
}

impl APIDocParser<Vec<API>> for API {
    type Parser = crate::document::query::DocQuerier;

    fn parse(querier: &Self::Parser) -> Result<Vec<API>> {
        info!("Parsing API documentation");

        // query functions
        info!("Querying functions from API documentation");
        let function_query = &utils::config().querys.function;
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
        let method_query = &utils::config().querys.method;
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

impl TryFrom<&QueryResult> for API {
    type Error = Error;

    fn try_from(value: &QueryResult) -> std::result::Result<Self, Self::Error> {
        // parse generics
        let generic_names = doc_utils::get_key_as_vec_with(value, "generic_type_param_name", |fv| {
            doc_utils::parse_field_value_as_str(fv, "generic_type_param_name[*]").map(str::to_string)
        })?;
        let generic_type_bounds = doc_utils::get_key_as_vec_with(value, "type_bound_name", |fv| {
            doc_utils::parse_field_value_as_vec_with(fv, "type_bound_name[*]", |fv| {
                let bound = doc_utils::parse_field_value_as_str(fv, "type_bound_name[*][*]").map(str::to_string)?;
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
            let struct_generic_names = doc_utils::get_key_as_vec_with(value, "struct_generic_type_param_name", |fv| {
                doc_utils::parse_field_value_as_str(fv, "struct_generic_type_param_name[*]").map(str::to_string)
            })?;
            let struct_generic_type_bounds = doc_utils::get_key_as_vec_with(value, "struct_type_bound_name", |fv| {
                doc_utils::parse_field_value_as_vec_with(fv, "struct_type_bound_name[*]", |fv| {
                    let bound = doc_utils::parse_field_value_as_str(fv, "struct_type_bound_name[*][*]").map(str::to_string)?;
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
        let param_names = doc_utils::get_key_as_slice(value, "param_name")?;
        let param_types = doc_utils::get_key_as_slice(value, "param_type")?;
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
            name: doc_utils::get_key_as_str(value, "name")?.to_string(),
            kind: {
                let item_type = doc_utils::get_key_as_str(value, "item_type")?;
                match item_type {
                    "Function" => Ok(APIKind::Function),
                    "Struct" => Ok(APIKind::Method),
                    _ => Err(Error::TrustfallQueryError(format!("Unknown item type `{}`", item_type))),
                }
            }?,
            signature: doc_utils::get_key_as_str(value, "signature")?.to_string(),
            module_path: doc_utils::parse_path(value, "module_path", "canonical_path")?,
            doc_comment: doc_utils::get_key_as_str(value, "doc_comment").unwrap_or("").to_string(),
            location: Location {
                file: {
                    let raw_path = doc_utils::get_key_as_str(value, "file")?.to_string();
                    // remove file path before "src/"
                    raw_path.split("src/").skip(2)
                        .map(|s| format!("src/{}", s))
                        .next()
                        .unwrap_or(raw_path)
                },
                line: doc_utils::get_key_as_usize(value, "line")?,
            },
            generics,
            inputs,
            output: ReturnValue {
                type_: {
                    let type_ = doc_utils::get_key_as_str(value, "return_type")?;
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