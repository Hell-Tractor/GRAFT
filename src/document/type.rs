use serde::Serialize;

pub mod r#struct;
pub mod r#enum;

#[derive(Serialize)]
pub enum TypeKind {
    Struct,
    Enum,
    Variant,
    Primitive,
}

#[derive(Serialize)]
pub struct StructField {
    pub name: String,
    pub docs: String,
    #[serde(rename = "type")]
    pub type_: String,
}