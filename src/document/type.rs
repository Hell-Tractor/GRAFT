use serde::Serialize;

pub mod r#struct;

#[derive(Serialize)]
pub enum TypeKind {
    Struct,
    Enum,
    Variant,
    Primitive,
}