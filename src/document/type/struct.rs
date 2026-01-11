use serde::{Deserialize, Serialize};

use crate::document::r#type::{StructField, TypeKind};

#[derive(Serialize, Deserialize)]
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