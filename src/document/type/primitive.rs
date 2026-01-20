use serde::{Deserialize, Serialize};

use crate::document::r#type::{ImplementedTrait, TypeKind};

#[derive(Serialize, Deserialize)]
pub struct Primitive {
    pub id: u32,
    pub name: String,
    #[serde(rename = "type")]
    pub type_: TypeKind,
    pub traits: Vec<ImplementedTrait>, // implemented traits of this primitive type
}