use std::{cell::RefCell, collections::{HashMap, HashSet}, sync::{Mutex, OnceLock}};

use rustdoc_types as rd;
use tracing::warn;

use crate::document::{self, r#type::{GenericParamBound, ImplementedTrait, TypeWithArgs}};

mod constants;
mod test;

const EXPECT_TYPE_EXISTS: &str = "Expected type to exist in documentation";

pub struct GenericBoundChecker<'a> {
    parser: &'a document::ProjectRustDocParser,
    std_parser: &'a document::ProjectRustDocParser,
    // struct_generics: &'a Vec<GenericParamBound>,
    source_func_generics: &'a Vec<GenericParamBound>,
    target_func_generics: &'a Vec<GenericParamBound>,
    all_types: &'a document::Types,
    // return_self: Option<&'a rustdoc_types::Type>,
    // param_self: Option<&'a rustdoc_types::Type>,
    std_types: &'a document::Types,
    // primitives: &'a Vec<document::Primitive>,
    #[allow(unused)]
    type_alias: &'a Vec<document::TypeAlias>,

    cache: RefCell<HashMap<(String, String), bool>>,
}

// static NOT_FOUND_GENERIC_BOUND: Vec<GenericBound> = vec![];

fn not_found_types() -> &'static Mutex<HashSet<String>> {
    static INSTANCE: OnceLock<Mutex<HashSet<String>>> = OnceLock::new();
    INSTANCE.get_or_init(|| Mutex::new(HashSet::new()))
}

impl<'a> GenericBoundChecker<'a> {
    pub fn new(
        parser: &'a document::ProjectRustDocParser,
        std_parser: &'a document::ProjectRustDocParser,
        // struct_generics: &'a Vec<GenericParamBound>, // already merged into func_generics
        source_func_generics: &'a Vec<GenericParamBound>,
        target_func_generics: &'a Vec<GenericParamBound>,
        all_types: &'a document::Types,
        // return_self: Option<&'a rustdoc_types::Type>,
        // param_self: Option<&'a rustdoc_types::Type>,
        std_types: &'a document::Types,
        // primitives: &'a Vec<document::Primitive>,
        type_alias: &'a Vec<document::TypeAlias>,
    ) -> Self {
        Self {
            parser,
            std_parser,
            // struct_generics,
            source_func_generics,
            target_func_generics,
            all_types,
            // return_self,
            // param_self,
            std_types,
            // primitives,
            type_alias,

            cache: RefCell::new(HashMap::new()),
        }
    }

    /// Check if the return type's can be used as the parameter type's
    pub fn check(&self, return_type: &rustdoc_types::Type, param_type: &rustdoc_types::Type) -> bool {
        let key = (
            document::rust_type_names::rust_type_name(return_type),
            document::rust_type_names::rust_type_name(param_type),
        );
        if let Some(&cached) = self.cache.borrow().get(&key) {
            return cached;
        }

        let result = self.check_inner(return_type, param_type);
        self.cache.borrow_mut().insert(key, result);
        result
    }

    fn check_inner(&self, return_ty: &rustdoc_types::Type, param_ty: &rustdoc_types::Type) -> bool {
        use rustdoc_types::Type;

        match param_ty {
            // unwrap directly:
            Type::Array { type_, .. } => self.check_inner(return_ty, type_),
            Type::BorrowedRef { type_, .. } => self.check_inner(return_ty, type_),
            Type::Pat { type_, .. } => self.check_inner(return_ty, type_),
            Type::RawPointer { type_, .. } => self.check_inner(return_ty, type_),
            Type::Slice(type_) => self.check_inner(return_ty, type_),

            // for primitive types, always true
            Type::Primitive(_) => true,

            // for DynTrait and Impl, check bounds
            Type::DynTrait(trait_) => {
                let required = self.dyn_trait_to_implemented_traits(trait_);
                self.type_satisfies_bounds(return_ty, &required)
            },
            Type::ImplTrait(impl_) => {
                let required = self.impl_trait_to_implemented_traits(impl_);
                self.type_satisfies_bounds(return_ty, &required)
            },
            Type::Generic(t) => {
                let required = self.target_generic_to_implemented_traits(t);
                self.type_satisfies_bounds(return_ty, &required)
            },

            // for ResolvedPath, FunctionPointer, check equality
            Type::ResolvedPath(_) |
            Type::FunctionPointer(_) |
            Type::QualifiedPath { .. } => {
                return_ty == param_ty
            },

            // for tuple:
            // if only one non-primitive element, check that element
            // else check equality
            Type::Tuple(t) => {
                let non_primitive_elements: Vec<&Type> = t.iter()
                    .filter(|elem| !Self::is_type_primitive(elem))
                    .collect();
                if non_primitive_elements.len() == 1 {
                    self.check_inner(return_ty, non_primitive_elements[0])
                } else {
                    return_ty == param_ty
                }
            }

            // fot infer, always false
            // not expected to appear in real code
            Type::Infer => false,
        }
    }

    pub fn is_type_primitive(type_: &rustdoc_types::Type) -> bool {
        use rustdoc_types::Type;
        match type_ {
            Type::Primitive(_) => true,

            Type::Array { type_, .. } => Self::is_type_primitive(type_),
            Type::BorrowedRef { type_, .. } => Self::is_type_primitive(type_),
            Type::Pat { type_, .. } => Self::is_type_primitive(type_),
            Type::RawPointer { type_, .. } => Self::is_type_primitive(type_),
            Type::Slice(type_) => Self::is_type_primitive(type_),

            _ => false,
        }
    }

    fn dyn_trait_to_implemented_traits(&self, dyn_trait: &rustdoc_types::DynTrait) -> Vec<ImplementedTrait> {
        dyn_trait.traits.iter().map(|trait_| {
            let type_with_args = self.parser.path_to_type_with_args(&trait_.trait_)
                .expect(EXPECT_TYPE_EXISTS);
            ImplementedTrait {
                id: None,
                name: trait_.trait_.path.clone(),
                trait_: type_with_args,
                bounds: vec![],
            }
        }).collect()
    }

    fn impl_trait_to_implemented_traits(&self, impl_trait: &Vec<rustdoc_types::GenericBound>) -> Vec<ImplementedTrait> {
        impl_trait.iter().filter_map(|bound| {
            match bound {
                rd::GenericBound::TraitBound { trait_, .. } => {
                    let type_with_args = self.parser.path_to_type_with_args(&trait_)
                        .expect(EXPECT_TYPE_EXISTS);
                    Some(ImplementedTrait {
                        id: None,
                        name: trait_.path.clone(),
                        trait_: type_with_args,
                        bounds: vec![],
                    })
                },
                _ => None,
            }
        }).collect()
    }

    fn source_generic_to_implemented_traits(&self, name: &str) -> Vec<ImplementedTrait> {
        self.find_generic_in_source_func(name)
            .map(|bounds| {
                bounds.bounds.iter().map(|type_with_args| {
                    ImplementedTrait {
                        id: None,
                        name: bounds.name.clone(),
                        trait_: type_with_args.clone(),
                        bounds: vec![],
                    }
                }).collect()
            }).unwrap_or_else(|| {
                if not_found_types().lock().unwrap().insert(name.to_string()) {
                    warn!("Generic {} not found in source function", name);
                }
                vec![]
            })
    }

    fn find_generic_in_target_func(&self, name: &str) -> Option<&GenericParamBound> {
        self.target_func_generics.iter()
            .find(|g| g.name == name)
    }

    fn target_generic_to_implemented_traits(&self, name: &str) -> Vec<ImplementedTrait> {
        self.find_generic_in_target_func(name)
            .map(|bounds| {
                bounds.bounds.iter().map(|type_with_args| {
                    ImplementedTrait {
                        id: None,
                        name: bounds.name.clone(),
                        trait_: type_with_args.clone(),
                        bounds: vec![],
                    }
                }).collect()
            }).unwrap_or_else(|| {
                if not_found_types().lock().unwrap().insert(name.to_string()) {
                    warn!("Generic {} not found in target function", name);
                }
                vec![]
            })
    }

    // 当目标类型是泛型(Generics, ImplTrait, DynTrait)时，检查返回类型是否满足其约束
    fn type_satisfies_bounds(&self, type_: &rustdoc_types::Type, required: &Vec<ImplementedTrait>) -> bool {
        use rustdoc_types::Type;

        match type_ {
            // unwrap directly:
            Type::Array { type_, .. } => self.type_satisfies_bounds(type_, required),
            Type::BorrowedRef { type_, .. } => self.type_satisfies_bounds(type_, required),
            Type::Pat { type_, .. } => self.type_satisfies_bounds(type_, required),
            Type::RawPointer { type_, .. } => self.type_satisfies_bounds(type_, required),
            Type::Slice(type_) => self.type_satisfies_bounds(type_, required),

            // for primitive types, search in std types
            Type::Primitive(name) => {
                let implemented = self.std_types.primitives.iter()
                    .find(|p| &p.name == name)
                    .map(|p| &p.traits);
                match implemented {
                    Some(traits) => self.traits_satisfies_traits(traits, required),
                    None => {
                        warn!("Primitive type {} not found in std types", name);
                        false
                    },
                }
            },

            // for DynTrait and Impl, return their traits directly
            Type::DynTrait(d) => {
                let implemented = self.dyn_trait_to_implemented_traits(d);
                self.traits_satisfies_traits(&implemented, required)
            },
            Type::ImplTrait(i) => {
                let implemented = self.impl_trait_to_implemented_traits(i);
                self.traits_satisfies_traits(&implemented, required)
            }

            // for generics, search in function generics or struct/enum generics
            Type::Generic(g) => {
                let implemented = self.source_generic_to_implemented_traits(g);
                self.traits_satisfies_traits(&implemented, required)
            },

            // for ResolvedPath, search in docs
            Type::ResolvedPath(path) => {
                let type_ = self.parser.path_to_type_with_args(path)
                    .or_else(|| self.std_parser.path_to_type_with_args(path));
                match type_ {
                    Some(t) => {
                        let implemented = self.find_implemented_traits(&t);
                        match implemented {
                            None => false,
                            Some(traits) => self.traits_satisfies_traits(traits, required),
                        }
                    },
                    None => {
                        let mut not_found = not_found_types().lock().unwrap();
                        if not_found.insert(path.path.clone()) {
                            warn!("Type {} not found in documentation", path.path);
                        }
                        false
                    }
                }
            },

            // for tuple, check each element
            Type::Tuple(elements) => {
                elements.iter().any(|elem| self.type_satisfies_bounds(elem, required))
            }

            // for QualifiedPath, unsupported yet
            Type::QualifiedPath { .. } => false,

            // for other types, return false
            Type::FunctionPointer(_) => false,
            Type::Infer => false,
        }
    }

    fn find_generic_in_source_func(&self, name: &str) -> Option<&GenericParamBound> {
        self.source_func_generics.iter()
            .find(|g| g.name == name)
    }

    // fn find_generic_in_struct(&self, name: &str) -> Option<&GenericParamBound> {
    //     self.struct_generics.iter()
    //         .find(|g| g.name == name)
    // }

    fn find_implemented_traits(&self, type_: &TypeWithArgs) -> Option<&Vec<ImplementedTrait>> {
        let id = match &type_.type_ {
            rustdoc_types::Type::ResolvedPath(p) => p.id.0,
            _ => {
                warn!("Type in TypeWithArgs is not ResolvedPath");
                return None;
            },
        };
        let structs = self.all_types.structs.iter()
            .find(|s| s.id == id)
            .map(|s| {
                // TODO: check if type_ satisfies bound of impl block
                // s.traits.clone().into_iter()
                //     .filter(|t| {
                //
                //     }).collect()
                &s.traits
            });
        let enums = self.all_types.enums.iter()
            .find(|e| e.id == id)
            .map(|e| &e.traits);
        let std_structs = self.std_types.structs.iter()
            .find(|s| s.id == id)
            .map(|s| &s.traits);
        let std_enums = self.std_types.enums.iter()
            .find(|e| e.id == id)
            .map(|e| &e.traits);
        structs.or(enums).or(std_structs).or(std_enums)
    }

    fn traits_satisfies_traits(&self, implemented: &Vec<ImplementedTrait>, required: &Vec<ImplementedTrait>) -> bool {
        required.iter().all(|req_trait| {
            implemented.iter().any(|imp_trait| {
                self.trait_satisfies_trait(imp_trait, req_trait)
            })
        })
    }

    fn trait_satisfies_trait(&self, implemented: &ImplementedTrait, required: &ImplementedTrait) -> bool {
        if implemented.trait_.type_ != required.trait_.type_ {
            return false;
        }
        // check args
        if implemented.trait_.args.len() != required.trait_.args.len() {
            return false;
        }
        for (imp_arg, req_arg) in implemented.trait_.args.iter().zip(required.trait_.args.iter()) {
            if !self.check_inner(imp_arg, req_arg) {
                return false;
            }
        }
        true
    }

}
