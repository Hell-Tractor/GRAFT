use serde::Serialize;

#[derive(Serialize)]
pub enum Type {
}

#[derive(Serialize)]
pub struct Struct {
    pub name: String,
    #[serde(rename = "type")]
    pub type_: TypeKind,
    pub path: String,
    pub traits: Vec<String>
}

#[derive(Serialize)]
pub enum TypeKind {
    Struct,
    Enum,
    Variant,
    Primitive,
}