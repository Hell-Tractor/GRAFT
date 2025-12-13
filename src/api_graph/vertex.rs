use std::sync::{Arc, Mutex, Weak};

use rustdoc_types::Type;
use crate::{document::api::APIGeneric, generic_bound::GenericBoundChecker};

use super::edge::RefType;

// TODO: change mutex to rwlock
pub enum Vertex {
    APIParam(Arc<Mutex<APIParam>>),
    API(Arc<Mutex<API>>),
    Constant(Constant),
}

pub struct APIParam {
    pub name: String,
    pub type_: Type,
    pub api: Weak<Mutex<API>>,
    pub id: usize, // ID in the APIGraph
}

pub struct API {
    pub signature: String,
    pub params: Vec<Arc<Mutex<APIParam>>>,
    pub return_type: Option<Type>,
    pub id: usize, // ID in the APIGraph
    pub generics: Vec<APIGeneric>,
    pub impl_for: Option<Type>,
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
    pub fn new(name: String, type_: Type, api: &Arc<Mutex<API>>) -> Self {
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
    pub fn new(signature: String, return_type: Option<Type>, generics: Vec<APIGeneric>, impl_for: Option<Type>, module_path: String, doc_comment: String) -> Self {
        Self {
            signature,
            params: vec![],
            id: 0,
            return_type,
            generics,
            impl_for,
            module_path,
            doc_comment,
        }
    }

    pub fn add_param(&mut self, param: Arc<Mutex<APIParam>>) {
        self.params.push(param);
    }

    pub fn set_id(&mut self, id: usize) {
        self.id = id;
    }
}