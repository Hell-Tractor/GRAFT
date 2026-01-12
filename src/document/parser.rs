use tracing::info;

use crate::{document::{APIDoc, APIDocParser, Types, api::API, parser::rustdoc::RustDocParser, r#type::{constant::Constant, r#enum::Enum, r#struct::Struct}}, error::Result};

// mod trustfall;
mod rustdoc;

// write a marco to auto impl APIDocParser for APIDoc and Types using different parsers
macro_rules! impl_api_doc_parser {
    ($parser_type: ty) => {
        impl APIDocParser<APIDoc, $parser_type> for APIDoc {
            fn parse(querier: & $parser_type) -> Result<APIDoc> {
                info!("Parsing API documentation with {}", stringify!($parser_type));
                let apis = API::parse(querier)?;
                let types = Types::parse(querier)?;
                let constants = Constant::parse(querier)?;
                Ok(APIDoc {
                    apis,
                    types,
                    constants,
                })
            }
        }

        impl APIDocParser<Types, $parser_type> for Types {
            fn parse(querier: & $parser_type) -> Result<Types> {
                info!("Parsing Types documentation with {}", stringify!($parser_type));
                let structs = Struct::parse(querier)?;
                let enums = Enum::parse(querier)?;
                Ok(Types {
                    structs,
                    enums,
                })
            }
        }
    };
}

// impl_api_doc_parser!(DocQuerier);
impl_api_doc_parser!(RustDocParser);