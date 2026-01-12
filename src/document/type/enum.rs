use serde::{Deserialize, Serialize};

use crate::document::r#type::{GenericParamBound, ImplementedTrait, StructField, TypeKind};

#[derive(Serialize,Deserialize)]
pub struct Enum {
    pub id: u32,
    pub name: String,
    pub docs: String,
    #[serde(rename = "type")]
    pub type_: TypeKind,
    pub path: String,
    // pub traits: Vec<rustdoc_types::GenericBound>,
    pub traits: Vec<ImplementedTrait>, // implemented traits of this enum
    pub bounds: Vec<GenericParamBound>, // generic parameter bounds of this enum
    pub variants: Vec<EnumVariant>,
}

#[derive(Serialize, Deserialize)]
pub struct EnumVariant {
    pub name: String,
    pub docs: String,
    pub fields: Vec<StructField>,
}