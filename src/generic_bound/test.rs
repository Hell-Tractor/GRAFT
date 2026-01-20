#![allow(unused)]
#![cfg(test)]
use crate::document::{self, ProjectRustDocParser};
use super::*;

macro_rules! get_api_doc {
    ($path: expr) => {
        document::APIDoc::from_file(&std::path::Path::new($path), false)
            .unwrap().filter_api().filter_types()
            .build()
    };
}

fn get_api_by_name<'a>(api_doc: &'a document::APIDoc, name: &str) -> &'a document::api::API {
    api_doc.apis.iter().find(|api| api.name == name).expect(&format!("API {} not found", name))
}

fn get_type_bounds_by_name<'a>(api_doc: &'a document::APIDoc, name: &str) -> &'a Vec<ImplementedTrait> {
    let bounds = api_doc.types.structs.iter()
        .find(|s| s.name == name)
        .map(|s| &s.traits)
        .or(
            api_doc.types.enums.iter()
                .find(|e| e.name == name)
                .map(|e| &e.traits)
        ).expect(&format!("Type {} not found", name));
    bounds
}

// #[test]
// fn test_generic_bound_satisfy_check() {
//     let (api_doc, parser) = get_api_doc!("localdata/test_data/generic_bound_satisfy_check/rustdoc.json");
//     let (std_doc, std_parser) = get_api_doc!("localdata/std_pretty.json");
//     let empty_vec = vec![];
//     let empty_types = document::Types { structs: vec![], enums: vec![], primitives: vec![] };
//     let f1 = get_api_by_name(&api_doc, "f1");
//     let f1checker = GenericBoundChecker::new(&parser, &std_parser, &empty_vec, &f1.generics, &api_doc.types, &std_doc.types, &api_doc.type_alias);
//     let f2 = get_api_by_name(&api_doc, "f2");
//     let f2checker = GenericBoundChecker::new(&parser, &std_parser, &empty_vec, &f2.generics, &api_doc.types, &std_doc.types, &api_doc.type_alias);
//     let f3 = get_api_by_name(&api_doc, "f3");
//     let f3checker = GenericBoundChecker::new(&parser, &std_parser, &empty_vec, &f3.generics, &api_doc.types, &std_doc.types, &api_doc.type_alias);
//     let s1 = get_type_bounds_by_name(&api_doc, "S1");
//     assert!(f1checker.is_bounds_satisfy_bounds(s1, &f1.generics[0].type_bound));
//     assert!(f3checker.is_bounds_satisfy_bounds(s1, &f3.generics[0].type_bound));

//     let s2 = get_type_bounds_by_name(&api_doc, "S2");
//     assert!(!f1checker.is_bounds_satisfy_bounds(s2, &f1.generics[0].type_bound));

//     let s3 = get_type_bounds_by_name(&api_doc, "S3");
//     assert!(!f1checker.is_bounds_satisfy_bounds(s3, &f1.generics[0].type_bound));
//     assert!(f2checker.is_bounds_satisfy_bounds(s3, &f2.generics[0].type_bound));
//     assert!(!f3checker.is_bounds_satisfy_bounds(s3, &f3.generics[0].type_bound));

//     let e1 = get_type_bounds_by_name(&api_doc, "E1");
//     assert!(f1checker.is_bounds_satisfy_bounds(e1, &f1.generics[0].type_bound));
// }