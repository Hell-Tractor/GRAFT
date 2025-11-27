use rustdoc_types::{GenericArg, GenericArgs, GenericBound, Path};

use crate::document::{self, api::APIGeneric};

mod constants;
mod test;

pub struct GenericBoundChecker<'a> {
    all_required_generics: &'a Vec<APIGeneric>,
    all_types: &'a document::Types,
}

impl<'a> GenericBoundChecker<'a> {
    pub fn new(all_required_generics: &'a Vec<APIGeneric>, all_types: &'a document::Types) -> Self {
        Self {
            all_required_generics,
            all_types,
        }
    }

    pub fn is_bounds_satisfy_bounds(&self, implemented_bounds: &Vec<GenericBound>, required_bounds: &Vec<GenericBound>) -> bool {
        required_bounds.iter().all(|required_bound| {
            implemented_bounds.iter().any(|type_bound| {
                self.satisfies_trait_bound(type_bound, required_bound)
            })
        })
    }

    pub fn satisfies_trait_bound(&self, type_bound: &GenericBound, required_bound: &GenericBound) -> bool {
        match (type_bound, required_bound) {
            (
                GenericBound::TraitBound { trait_: actual, .. },
                GenericBound::TraitBound {
                    trait_: required, ..
                },
            ) => self.paths_satisfy(actual, required), // TODO: consider higher-ranked trait bounds

            // do not consider other kinds of bounds
            _ => true,
        }
    }

    fn paths_satisfy(&self, actual: &Path, required: &Path) -> bool {
        // check if the path IDs are the same
        // * 基本类型trait的id为手写，可能与运行时不符
        // * 这里先使用path进行判断
        if actual.path != required.path {
            return false;
        }
        // check args
        match (&actual.args, &required.args) {
            (Some(_), None) => true,
            (None, None) => true,
            (None, Some(_)) => false,
            (Some(actual), Some(required)) => {
                match (&**actual, &**required) {
                    (
                        GenericArgs::AngleBracketed {
                            args: actual_args, ..
                        },
                        GenericArgs::AngleBracketed {
                            args: required_args,
                            ..
                        },
                    ) => {
                        if actual_args.len() != required_args.len() {
                            return false;
                        }

                        for (actual, required) in actual_args.iter().zip(required_args.iter()) {
                            if !self.generic_arg_satisfies(actual, required) {
                                return false;
                            }
                        }

                        true
                    }

                    // TODO: support Fn(...) -> T
                    _ => false,
                }
            }
        }
    }

    fn generic_arg_satisfies(&self, actual: &GenericArg, required: &GenericArg) -> bool {
        match (actual, required) {
            (GenericArg::Type(a), GenericArg::Type(b)) => self.type_satisfies(a, b),

            // do not consider lifetime and constant generics for now
            _ => true,
        }
    }

    fn type_satisfies(&self, actual: &rustdoc_types::Type, required: &rustdoc_types::Type) -> bool {
        let actual_is_generic = GenericBoundChecker::get_generic_type(actual);
        let required_is_generic = GenericBoundChecker::get_generic_type(required);

        match (actual_is_generic, required_is_generic) {
            // both not generic, check equality
            (None, None) => actual == required,

            // check if actual satisfies the required generic
            (None, Some(t)) => {
                let required_bounds = self.get_generic_bounds(t);
                let implemented_bounds = self.get_implemented_traits_of_type(actual);
                self.is_bounds_satisfy_bounds(&implemented_bounds, required_bounds)
            },

            // actual is generic, required is not -> not satisfy
            (Some(_), None) => false,

            // both generic -> check generic constraints
            (Some(t), Some(rt)) => {
                let implemented_bounds = self.get_generic_bounds(t);
                let required_bounds = self.get_generic_bounds(rt);
                self.is_bounds_satisfy_bounds(&implemented_bounds, required_bounds)
            },
        }
    }

    fn get_generic_type(ty: &rustdoc_types::Type) -> Option<&String> {
        use rustdoc_types::Type;
        match ty {
            Type::Generic(t) => Some(t),
            _ => None,
        }
    }

    fn get_generic_bounds(&self, generic_name: &str) -> &Vec<GenericBound> {
        &self.all_required_generics.iter()
            .find(|g| &g.name == generic_name)
            .expect("Generic type not found in required generics")
            .type_bound
    }

    /// 获取某个类型实现的所有 trait 列表
    ///
    /// - 对于数组、切片：[T]，会递归获取 T 实现的 trait 列表
    /// - 对于DynTrait： dyn Trait1 + Trait2，会直接返回 Trait1、Trait2 列表
    /// - 对于函数： fn(...) -> T，返回空
    /// - 对于基本类型，返回预定义的 trait 列表
    /// - 对于泛型类型 T，查找 all_required_generics 获取其约束的 trait 列表
    /// - 对于 ImplTrait，返回其包含的 trait 列表
    /// - 对于裸指针，会递归获取其指向类型实现的 trait 列表
    /// - 对于元组，会递归获取每个元素类型实现的 trait 列表并合并返回
    /// - 其他类型暂不支持，返回空列表
    fn get_implemented_traits_of_type(&self, type_: &rustdoc_types::Type) -> Vec<GenericBound> {
        use rustdoc_types::Type;
        match type_ {
            Type::ResolvedPath(path) => {
                let structs = self.all_types.structs.iter()
                    .filter(|s| s.id == path.id.0)
                    .map(|s| &s.traits);
                let enums = self.all_types.enums.iter()
                    .filter(|e| e.id == path.id.0)
                    .map(|e| &e.traits);
                structs.chain(enums).next().expect("Type not found in documented types").clone()
            },
            Type::Array { type_, .. } => self.get_implemented_traits_of_type(type_),
            Type::BorrowedRef { type_ , .. } => self.get_implemented_traits_of_type(type_),
            Type::Slice(type_) => self.get_implemented_traits_of_type(type_),
            Type::DynTrait(dyn_trait) => dyn_trait.traits
                .iter().map(|pt| GenericBound::TraitBound {
                    trait_: pt.trait_.clone(),
                    generic_params: pt.generic_params.clone(),
                    modifier: rustdoc_types::TraitBoundModifier::None,
                }).collect::<Vec<GenericBound>>(),
            Type::FunctionPointer(_) => vec![],
            Type::Generic(t) => self.get_generic_bounds(t).clone(),
            Type::ImplTrait(bound) => bound.clone(),
            Type::Infer => panic!("Type inference should not appear here"),
            Type::Pat { type_, .. } => self.get_implemented_traits_of_type(type_),
            Type::Primitive(t) => constants::get_primitive_traits(t),
            Type::QualifiedPath {..} => vec![], // TODO: skip for now
            Type::RawPointer { type_ , .. } => self.get_implemented_traits_of_type(type_),
            Type::Tuple(type_) => {
                let mut traits = type_.iter()
                    .flat_map(|t| self.get_implemented_traits_of_type(t))
                    .collect::<Vec<_>>();
                traits.sort_by_key(|b| {
                    let GenericBound::TraitBound { trait_, .. } = b else {
                        panic!("Only TraitBound is expected here");
                    };
                    trait_.path.clone()
                });
                traits.dedup_by_key(|b| {
                    let GenericBound::TraitBound { trait_, .. } = b else {
                        panic!("Only TraitBound is expected here");
                    };
                    trait_.path.clone()
                });
                traits
            },
        }
    }
}
