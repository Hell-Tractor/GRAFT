use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize)]
pub struct Constant {
    pub id: u32,
    pub name: String,
    pub docs: String,
    #[serde(rename = "type")]
    pub type_: rustdoc_types::Type,
    pub path: String,
}