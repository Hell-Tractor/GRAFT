use std::sync::{Arc, RwLock, Weak};

use rustdoc_types::Type;
use serde::{Deserialize, Serialize};
use crate::{document::r#type::GenericParamBound, generic_bound::GenericBoundChecker};

use super::edge::RefType;

pub enum Vertex {
    APIParam(Arc<RwLock<APIParam>>),
    API(Arc<RwLock<API>>),
    Constant(Constant),
}

#[derive(Serialize, Deserialize)]
pub struct APIParam {
    pub name: String,
    pub type_: Type,
    #[serde(skip)]
    pub api: Weak<RwLock<API>>,
    pub id: usize, // ID in the APIGraph
}

#[derive(Serialize, Deserialize)]
pub struct API {
    pub signature: String,
    pub params: Vec<Arc<RwLock<APIParam>>>,
    pub return_type: Option<Type>,
    pub id: usize, // ID in the APIGraph
    pub generics: Vec<GenericParamBound>,
    pub module_path: String,
    pub doc_comment: String,
}

pub struct Constant {
    pub name: String,
    pub type_: Type,
    pub id: usize, // ID in the APIGraph
    pub module_path: String,
    pub doc_comment: String,
}

impl APIParam {
    pub fn new(name: String, type_: Type, api: &Arc<RwLock<API>>) -> Self {
        Self {
            name,
            type_,
            api: Arc::downgrade(api),
            id: 0,
        }
    }

    pub fn get_ref_type(&self) -> RefType {
        match &self.type_ {
            Type::BorrowedRef { is_mutable, ..} => {
                if *is_mutable {
                    RefType::BorrowMut
                } else {
                    RefType::Borrow
                }
            },
            _ => RefType::Move,
        }
    }

    pub fn is_fuzzer_generable(&self) -> bool {
        GenericBoundChecker::is_type_primitive(&self.type_)
    }

    pub fn set_id(&mut self, id: usize) {
        self.id = id;
    }
}

impl API {
    pub fn new(signature: String, return_type: Option<Type>, generics: Vec<GenericParamBound>, module_path: String, doc_comment: String) -> Self {
        Self {
            signature,
            params: vec![],
            id: 0,
            return_type,
            generics,
            module_path,
            doc_comment,
        }
    }

    pub fn add_param(&mut self, param: Arc<RwLock<APIParam>>) {
        self.params.push(param);
    }

    pub fn set_id(&mut self, id: usize) {
        self.id = id;
    }
}