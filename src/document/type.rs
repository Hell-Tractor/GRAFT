use serde::{Deserialize, Serialize};

pub mod r#struct;
pub mod r#enum;
pub mod constant;
pub mod primitive;

#[derive(Serialize, Deserialize)]
pub enum TypeKind {
    Struct,
    Enum,
    Primitive,
    // Union,
    // Constant,
}

#[derive(Serialize, Deserialize)]
pub struct StructField {
    /// Name of the generic parameter, same as in original code.
    /// Allow use as primiary key
    pub name: String,
    pub docs: String,
    #[serde(rename = "type")]
    pub type_: rustdoc_types::Type,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct ImplementedTrait {
    pub id: Option<u32>,
    /// Name of the trait, converted from doc file.
    /// ! Not allow use as primary key
    /// * Use `id` instead
    pub name: String,
    pub trait_: TypeWithArgs,
    pub bounds: Vec<GenericParamBound>,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct TypeWithArgs {
    pub type_: rustdoc_types::Type,
    pub args: Vec<rustdoc_types::Type>, // empty for non-generic types
}

impl TypeWithArgs {
    // pub fn new(type_: &rustdoc_types::Type) -> Self {
    //     Self {
    //         type_: type_.clone(),
    //         args: vec![],
    //     }
    // }
}

#[derive(Serialize, Deserialize, Clone)]
pub struct GenericParamBound {
    /// Name of the generic parameter, same as in original code.
    /// Allow use as primiary key
    pub name: String,
    pub bounds: Vec<TypeWithArgs>,
}

// pub struct Struct<T: Sync> { field: T }
// trait SomeTrait<T: Send> {}

// impl<T: Clone + Sync> Struct<T> {

// }

// impl<T: Clone + Send + Sync> SomeTrait<T> for Struct<T> {

// }