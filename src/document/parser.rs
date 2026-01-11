use tracing::info;

use crate::{document::{APIDoc, APIDocParser, Types, r#type::{r#enum::Enum, r#struct::Struct}}, error::Result};

mod trustfall;

impl APIDocParser<Types> for Types {
    type Parser = crate::document::query::DocQuerier;

    fn parse(querier: &Self::Parser) -> Result<Types> {
        info!("Parsing Types documentation");
        let structs = Struct::parse(querier)?;
        let enums = Enum::parse(querier)?;
        Ok(Types {
            structs,
            enums,
        })
    }
}

impl APIDocParser<APIDoc> for APIDoc {
    type Parser = crate::document::query::DocQuerier;

    fn parse(querier: &Self::Parser) -> Result<APIDoc> {
        info!("Parsing API documentation");
        let apis = crate::document::api::API::parse(querier)?;
        let types = Types::parse(querier)?;
        let constants = crate::document::r#type::constant::Constant::parse(querier)?;
        Ok(APIDoc {
            apis,
            types,
            constants,
        })
    }
}