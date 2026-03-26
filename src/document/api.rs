use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::document::r#type::GenericParamBound;

#[derive(Serialize, Deserialize)]
pub struct API {
    pub id: u32,
    pub name: String,
    pub kind: APIKind,
    pub signature: String,
    pub module_path: String,
    pub doc_comment: String,
    // pub location: Location,
    pub generics: Vec<GenericParamBound>,
    pub inputs: Vec<Parameter>,
    pub output: ReturnValue,
    #[serde(skip)]
    pub file: PathBuf,
    #[serde(skip)]
    pub start_line: usize,
    #[serde(skip)]
    pub end_line: usize,
    // pub impl_for: Option<u32>,
    // pub is_public: bool,
    // pub is_safe: bool,
    // pub has_unsafe: bool,
    // pub recommended_fuzz_type: FuzzType,
    // pub notes_for_fuzzer: String,
}

#[derive(Serialize, Deserialize)]
pub enum APIKind {
    Function,
    Method,
}

// #[derive(Serialize, Deserialize)]
// pub enum FuzzType {
//     String,
//     Custom,
//     Bytes,
//     Integer,
// }

// #[derive(Serialize, Deserialize)]
// pub struct Location {
//     pub file: String,
//     pub line: usize,
// }

#[derive(Serialize, Deserialize)]
pub struct Parameter {
    pub name: String,
    #[serde(rename = "type")]
    pub type_: rustdoc_types::Type,
}

#[derive(Serialize, Deserialize)]
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

// #[derive(Serialize, Deserialize)]
// pub struct APIGeneric {
//     pub name: String,
//     pub type_bound: Vec<rustdoc_types::GenericBound>,
    // pub allowed_types: Vec<String>,
// }

// impl APIGeneric {
//     pub fn calculate_allowed_types(&mut self, types: &Types) {
//         self.allowed_types = types.structs.iter()
//             .filter(|type_| self.type_bound.iter().all(|bound| type_.traits.contains(bound)))
//             .map(|type_| type_.name.clone())
//             .collect();
//     }
// }