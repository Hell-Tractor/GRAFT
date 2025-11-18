use serde::Serialize;

use crate::document::query::QueryResult;
use crate::document::{TryFromApiDoc, query::DocQuerier, r#type::TypeKind};
use crate::error::*;

#[derive(Serialize)]
pub struct Struct {
    pub name: String,
    #[serde(rename = "type")]
    pub type_: TypeKind,
    pub path: String,
    pub traits: Vec<String>
}

impl TryFromApiDoc<Vec<Struct>> for Struct {
    fn try_from_api_doc(querier: &DocQuerier) -> Result<Vec<Struct>> {
        todo!()
    }
}

impl TryFrom<&QueryResult> for Struct {
    type Error = Error;

    fn try_from(value: &QueryResult) -> Result<Self> {
        todo!()
    }
}