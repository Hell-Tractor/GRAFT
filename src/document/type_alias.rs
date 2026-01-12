use serde::{Deserialize, Serialize};

use crate::document::r#type::GenericParamBound;

#[derive(Serialize, Deserialize)]
pub struct TypeAlias {
    pub type_: rustdoc_types::Type,
    pub generics: Vec<GenericParamBound>,
}