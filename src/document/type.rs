use serde::{Deserialize, Serialize};

pub mod r#struct;
pub mod r#enum;
pub mod constant;

#[derive(Serialize, Deserialize)]
pub enum TypeKind {
    Struct,
    Enum,
    // Union,
    // Constant,
}

#[derive(Serialize, Deserialize)]
pub struct StructField {
    pub name: String,
    pub docs: String,
    #[serde(rename = "type")]
    pub type_: String,
}