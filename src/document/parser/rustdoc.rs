use core::panic;
use std::collections::{HashMap, HashSet};

use rustdoc_types as rd;
use serde::{Deserialize, Serialize};
use tracing::warn;
use trustfall_rustdoc_adapter::rust_type_name;

use crate::{document::{APIDocParser, api::{API, APIKind, Parameter, ReturnValue}, rust_type_names, r#type::{GenericParamBound, ImplementedTrait, StructField, TypeKind, TypeWithArgs, constant::Constant, r#enum::{Enum, EnumVariant}, primitive::Primitive, r#struct::Struct}, type_alias::TypeAlias}};
use crate::error::Result as CrateResult;

// const EXPECT_TYPE_EXISTS: &str = "Expected type to exist in the documentation";

#[derive(thiserror::Error, Debug, Clone)]
pub enum Error {
    #[error("Method found but 'Self' type could not be resolved")]
    SelfNotFound
}

type Result<T> = std::result::Result<T, Error>;

#[derive(Serialize, Deserialize)]
pub struct RustDocParser<const OFFSET: u32> {
    krate: rustdoc_types::Crate,
    #[serde(skip)]
    parent_map: HashMap<rustdoc_types::Id, rustdoc_types::Id>,
}

macro_rules! get_item_inner_as {
    ($krate: expr, $id: expr, $pat: pat => $ret: expr) => {
        {
            let item = $krate.index.get($id)
                .expect(&format!("Item ID {} not found in crate index, this isn't expected to happen.", $id.0));
            match &item.inner {
                $pat => $ret,
                other => {
                    panic!("Expected item of pattern {:?} for ID {}, found {:?}, this isn't expected to happen.", stringify!($pat), $id.0, other);
                }
            }
        }
    };
}

impl<const OFFSET: u32> RustDocParser<OFFSET> {
    pub fn new(raw_doc: &str) -> CrateResult<Self> {
        let krate = serde_json::from_str::<rustdoc_types::Crate>(raw_doc)?;
        let parent_map = Self::build_parent_map(&krate);
        Ok(RustDocParser { krate, parent_map })
    }

    fn build_parent_map(krate: &rustdoc_types::Crate) -> HashMap<rustdoc_types::Id, rustdoc_types::Id> {
        let mut parent_map = HashMap::new();

        fn visit(krate: &rustdoc_types::Crate, module_id: &rustdoc_types::Id, parent_map: &mut HashMap<rustdoc_types::Id, rustdoc_types::Id>) {
            let module = &krate.index[module_id];
            if let rd::ItemEnum::Module(m) = &module.inner {
                for child_id in &m.items {
                    parent_map.insert(child_id.clone(), module_id.clone());
                    visit(krate, child_id, parent_map);
                }
            }
        }

        visit(krate, &krate.root, &mut parent_map);
        parent_map
    }

    fn get_item_path(&self, item_id: &rustdoc_types::Id) -> String {
        let mut path = vec![];
        let mut current_id = item_id.clone();
        let item_name = self.krate.index.get(item_id)
            .expect(&format!("Item ID {} not found in crate index", item_id.0))
            .name.clone()
            .unwrap_or_default();
        path.push(item_name);

        while let Some(parent_id) = self.parent_map.get(&current_id) {
            let item = &self.krate.index.get(parent_id)
                .expect(&format!("Parent ID {} not found in crate index, this isn't expected to happen.", parent_id.0))
                .name;
            if let Some(name) = item {
                path.push(name.clone());
            }
            current_id = parent_id.clone();
        }

        path.reverse();
        path.join("::")
    }

    fn type_from_path_id(&self, path: &rd::Path) -> Option<rd::Type> {
        let id = &path.id;

        match &self.krate.index.get(id)?
            .inner
        {
            rd::ItemEnum::Struct(_) |
            rd::ItemEnum::Enum(_) |
            rd::ItemEnum::Union(_) |
            rd::ItemEnum::Trait(_) |
            rd::ItemEnum::TypeAlias(_) |
            rd::ItemEnum::Primitive(_) |
            rd::ItemEnum::TraitAlias(_) |
            rd::ItemEnum::ExternType => {
                // 对“名义类型”，ResolvedPath 是正确的
                Some(rd::Type::ResolvedPath(path.clone()))
            }

            other => {
                warn!(
                    "Item {:?} (id = {}) cannot be converted into a Type",
                    other, id.0
                );
                None
            }
        }
    }

    pub fn path_to_type_with_args(&self, path: &rd::Path) -> Option<TypeWithArgs> {
        let type_ = self.type_from_path_id(path)?;
        let args = path.args.as_ref().map(|args| extract_type_args(&*args)).unwrap_or(vec![]);

        Some(TypeWithArgs { type_, args })
    }

    fn impl_id_to_implemented_trait(&self, impl_id: &rustdoc_types::Id,) -> Option<ImplementedTrait> {
        let impl_ = get_item_inner_as!(&self.krate, impl_id, rd::ItemEnum::Impl(impl_) => impl_);

        // return none if inherent trait
        let trait_path = impl_.trait_.as_ref()?;
        let Some(args) = self.path_to_type_with_args(trait_path) else {
            warn!("Failed to convert trait path {:?} to TypeWithArgs", trait_path);
            return None;
        };

        let bounds = impl_.generics.params.iter()
            .filter_map(|param| {
                let rd::GenericParamDefKind::Type { bounds, .. } = &param.kind else {
                    return None;
                };

                let trait_bounds = bounds.iter()
                    .filter_map(|b| match b {
                        rd::GenericBound::TraitBound { trait_, .. } => self.path_to_type_with_args(trait_),
                        _ => None, // 忽略 Outlives / 其它
                    })
                    .collect::<Vec<_>>();

                Some(GenericParamBound {
                    name: param.name.clone(),
                    bounds: trait_bounds,
                })
            })
            .collect();

        Some(ImplementedTrait {
            id: Some(trait_path.id.0 + OFFSET),
            name: trait_path.path.clone(),
            trait_: args,
            bounds,
        })
    }

    fn id_to_struct_field(&self, field_id: &rustdoc_types::Id) -> StructField {
        let field = get_item_inner_as!(&self.krate, field_id, rd::ItemEnum::StructField(t) => t);
        StructField {
            name: rust_type_name(field),
            docs: String::new(),
            type_: field.clone(),
        }
    }

    fn generics_to_generic_param_bounds(&self, generics: &rd::Generics) -> Vec<GenericParamBound> {
        // name -> GenericParamBound
        let mut map: HashMap<String, GenericParamBound> = HashMap::new();

        // handle inline bounds
        for param in &generics.params {
            let rd::GenericParamDefKind::Type { bounds, .. } = &param.kind else {
                continue;   // only consider type parameters
            };

            let entry = map.entry(param.name.clone())
                .or_insert(GenericParamBound {
                    name: param.name.clone(),
                    bounds: Vec::new(),
                });

            for bound in bounds {
                if let rd::GenericBound::TraitBound { trait_, .. } = bound {
                    let ty = self.path_to_type_with_args(trait_);
                    match ty {
                        Some(t) => entry.bounds.push(t),
                        None => {
                            warn!("Failed to convert trait bound {:?} to TypeWithArgs", trait_);
                            continue;
                        },
                    }
                }
            }
        }

        // 2. handle where-clause bounds
        for pred in &generics.where_predicates {
            let rd::WherePredicate::BoundPredicate { type_, bounds, .. } = pred else {
                continue;
            };

            // only consider `T: Trait`
            //
            // following patterns are ignored:
            // - where <T as Iterator>::Item: Clone
            // - where Vec<T>: Send
            // - where (T, U): SomeTrait
            let rd::Type::Generic(name) = type_ else {
                continue;
            };

            let entry = map.entry(name.clone())
                .or_insert(GenericParamBound {
                    name: name.clone(),
                    bounds: Vec::new(),
                });

            for bound in bounds {
                if let rd::GenericBound::TraitBound { trait_, .. } = bound {
                    let ty = self.path_to_type_with_args(trait_);
                    match ty {
                        Some(t) => entry.bounds.push(t),
                        None => {
                            warn!("Failed to convert trait bound {:?} to TypeWithArgs", trait_);
                            continue;
                        },
                    }
                }
            }
        }

        map.into_values().collect()
    }

    fn parse_enum(&self, item: &rd::Item, e: &rd::Enum) -> Enum {
        let variants = e.variants.iter().map(|variant| {
            let variant = get_item_inner_as!(&self.krate, &variant, rd::ItemEnum::Variant(v) => v);
            let fields = match &variant.kind {
                rustdoc_types::VariantKind::Struct { fields, .. } => {
                    fields.iter().map(|field_id| self.id_to_struct_field(field_id)).collect()
                },
                rustdoc_types::VariantKind::Tuple(fields) => {
                    fields.iter().filter_map(|field_id| field_id.as_ref().map(|id| self.id_to_struct_field(&id))).collect()
                },
                rustdoc_types::VariantKind::Plain => vec![],
            };

            EnumVariant {
                name: variant.discriminant.as_ref().map(|d| d.expr.clone()).unwrap_or_default(),
                docs: String::new(),
                fields,
            }
        }).collect();

        let traits = e.impls.iter()
            .filter_map(|impl_id| self.impl_id_to_implemented_trait(impl_id))
            .collect();

        let bounds = self.generics_to_generic_param_bounds(&e.generics);

        Enum {
            id: item.id.0 + OFFSET,
            name: item.name.clone().unwrap_or_default(),
            docs: item.docs.clone().unwrap_or_default(),
            type_: TypeKind::Enum,
            path: self.get_item_path(&item.id),
            traits,
            bounds,
            variants,
        }
    }

    fn parse_struct(&self, item: &rd::Item, s: &rd::Struct) -> Struct {
        let fields = match &s.kind {
            rd::StructKind::Unit => vec![],
            rd::StructKind::Tuple(field_ids) => {
                field_ids.iter().filter_map(|field_id| field_id.as_ref().map(|id| self.id_to_struct_field(&id))).collect()
            },
            rd::StructKind::Plain { fields: field_ids, .. } => {
                field_ids.iter().map(|field_id| self.id_to_struct_field(field_id)).collect()
            },
        };

        let traits = s.impls.iter()
            .filter_map(|impl_id| self.impl_id_to_implemented_trait(impl_id))
            .collect();

        let bounds = self.generics_to_generic_param_bounds(&s.generics);

        Struct {
            id: item.id.0 + OFFSET,
            name: item.name.clone().unwrap_or_default(),
            docs: item.docs.clone().unwrap_or_default(),
            type_: TypeKind::Struct,
            path: self.get_item_path(&item.id),
            traits,
            bounds,
            fields,
        }
    }

    fn parse_function(&self, item: &rd::Item, f: &rd::Function, self_type: Option<&rd::Type>, additional_generics: Vec<GenericParamBound>) -> Result<API> {
        let name = item.name.clone().unwrap_or("UnknownFunction".to_string());
        let signature = rust_type_names::function_signature(f, &name);
        let path = self.get_item_path(&item.id);

        let mut generics = self.generics_to_generic_param_bounds(&f.generics);
        generics.extend(additional_generics);
        let generics = generics;

        let inputs = f.sig.inputs.iter()
            .map(|(name, type_)| {
                let type_: Result<rd::Type> = if let rd::Type::Generic(g) = type_ && g == "Self" {
                    let self_type = self_type.ok_or(Error::SelfNotFound);
                    self_type.map(|t| t.clone())
                } else {
                    Ok(type_.clone())
                };
                type_.map(|t| {
                    Parameter {
                        name: name.clone(),
                        type_: t,
                    }
                })
            }).collect::<Result<Vec<Parameter>>>()?;
        let output = f.sig.output.as_ref().map(|t| {
            if let rd::Type::Generic(g) = t && g == "Self" {
                let self_type = self_type.ok_or(Error::SelfNotFound);
                self_type.map(|ty| ty.clone())
            } else {
                Ok(t.clone())
            }
        });
        let output = match output {
            Some(ty) => Some(ty?),
            None => None,
        };
        let kind = if self_type.is_some() {
            APIKind::Method
        } else {
            APIKind::Function
        };

        Ok(API {
            id: item.id.0 + OFFSET,
            name,
            kind,
            signature,
            module_path: path,
            doc_comment: item.docs.clone().unwrap_or_default(),
            generics,
            inputs,
            output: ReturnValue { type_: output },
        })
    }

    fn parse_method(&self, impl_: &rd::Impl, mut additional_generics: Vec<GenericParamBound>) -> Vec<API> {
        let self_type = Some(&impl_.for_);
        let impl_generics = self.generics_to_generic_param_bounds(&impl_.generics);
        additional_generics.extend(impl_generics);
        impl_.items.iter().filter_map(|assoc_item_id| {
            let assoc_item = self.krate.index.get(assoc_item_id)
                .expect("Associated item ID not found in crate index");
            match &assoc_item.inner {
                rd::ItemEnum::Function(f) => {
                    let func = self.parse_function(assoc_item, f, self_type, additional_generics.clone());
                    match func {
                        Ok(api) => Some(api),
                        Err(e) => {
                            warn!("Failed to parse method {}: {:?}", assoc_item.name.clone().unwrap_or("UnknownMethod".to_owned()), e);
                            None
                        }
                    }
                },
                _ => None,
            }
        }).collect()
    }

    fn parse_type_alias(&self, ta: &rd::TypeAlias) -> TypeAlias {
        let generics = self.generics_to_generic_param_bounds(&ta.generics);
        TypeAlias {
            type_: ta.type_.clone(),
            generics,
        }
    }

    fn parse_primitives(&self, item: &rd::Item, p: &rd::Primitive) -> Primitive {
        let impls = p.impls.iter()
            .filter_map(|impl_id| self.impl_id_to_implemented_trait(impl_id))
            .collect();
        Primitive {
            id: item.id.0 + OFFSET,
            name: p.name.clone(),
            type_: TypeKind::Primitive,
            traits: impls,
        }
    }

}

fn is_item_valid(item: &rd::Item) -> bool {
    item.visibility == rd::Visibility::Public && item.deprecation == None
}

impl<const OFFSET: u32> APIDocParser<Vec<Constant>, &RustDocParser<OFFSET>> for Constant {
    fn parse(parser: &RustDocParser<OFFSET>) -> CrateResult<Vec<Constant>> {
        let result = parser.krate.index.values().filter_map(|item| {
            if !is_item_valid(item) {
                return None;
            }
            match &item.inner {
                rd::ItemEnum::Constant { type_, .. } => {
                    Some(Constant {
                        id: item.id.0 + OFFSET,
                        name: item.name.clone().unwrap_or_default(),
                        docs: item.docs.clone().unwrap_or_default(),
                        type_: type_.clone(),
                        path: parser.get_item_path(&item.id),
                    })
                },
                rd::ItemEnum::Static(s) => {
                    Some(Constant {
                        id: item.id.0 + OFFSET,
                        name: item.name.clone().unwrap_or_default(),
                        docs: item.docs.clone().unwrap_or_default(),
                        type_: s.type_.clone(),
                        path: parser.get_item_path(&item.id),
                    })
                },
                _ => None,
            }
        }).collect();

        Ok(result)
    }
}

fn extract_type_args(args: &rd::GenericArgs) -> Vec<rd::Type> {
    match args {
        rd::GenericArgs::AngleBracketed { args, .. } => {
            args.iter()
                .filter_map(|arg| match arg {
                    rd::GenericArg::Type(ty) => Some(ty.clone()),
                    _ => None, // 忽略 lifetime / const
                })
                .collect()
        }
        _ => Vec::new(), // Parenthesized 等直接忽略
    }
}

impl<const OFFSET: u32> APIDocParser<Vec<Enum>, &RustDocParser<OFFSET>> for Enum {
    fn parse(parser: &RustDocParser<OFFSET>) -> CrateResult<Vec<Enum>> {
        let result = parser.krate.index.values().filter_map(|item| {
            if !is_item_valid(item) {
                return None;
            }
            match &item.inner {
                rd::ItemEnum::Enum(e) => Some(parser.parse_enum(item, e)),
                _ => None,
            }
        }).collect();

        Ok(result)
    }
}

impl<const OFFSET: u32> APIDocParser<Vec<Struct>, &RustDocParser<OFFSET>> for Struct {
    fn parse(parser: &RustDocParser<OFFSET>) -> CrateResult<Vec<Struct>> {
        let result = parser.krate.index.values().filter_map(|item| {
            if !is_item_valid(item) {
                return None;
            }
            match &item.inner {
                rd::ItemEnum::Struct(s) => Some(parser.parse_struct(item, s)),
                _ => None,
            }
        }).collect();

        Ok(result)
    }
}

impl<const OFFSET: u32> APIDocParser<Vec<API>, &RustDocParser<OFFSET>> for API {
    fn parse(parser: &RustDocParser<OFFSET>) -> CrateResult<Vec<API>> {
        let mut set = HashSet::new();
        let mut methods: Vec<_> = parser.krate.index.values().filter_map(|item| {
            if !is_item_valid(item) {
                return None;
            }
            match &item.inner {
                rd::ItemEnum::Struct(s) => {
                    let struct_generics = parser.generics_to_generic_param_bounds(&s.generics);
                    let methods = s.impls.iter().flat_map(|impl_id| {
                        let impl_ = get_item_inner_as!(&parser.krate, impl_id, rd::ItemEnum::Impl(impl_) => impl_);
                        parser.parse_method(impl_, struct_generics.clone())
                    }).collect::<Vec<_>>();
                    for method in &methods {
                        set.insert(method.id);
                    }
                    Some(methods)
                },
                rd::ItemEnum::Enum(e) => {
                    let enum_generics = parser.generics_to_generic_param_bounds(&e.generics);
                    let methods = e.impls.iter().flat_map(|impl_id| {
                        let impl_ = get_item_inner_as!(&parser.krate, impl_id, rd::ItemEnum::Impl(impl_) => impl_);
                        parser.parse_method(impl_, enum_generics.clone())
                    }).collect::<Vec<_>>();
                    for method in &methods {
                        set.insert(method.id);
                    }
                    Some(methods)
                },
                // rd::ItemEnum::Union(u) => {
                //     let union_generics = parser.generics_to_generic_param_bounds(&u.generics);
                //     let methods = u.impls.iter().flat_map(|impl_id| {
                //         let impl_ = get_item_inner_as!(&parser.krate, impl_id, rd::ItemEnum::Impl(impl_) => impl_);
                //         parser.parse_method(impl_, union_generics.clone())
                //     }).collect::<Vec<_>>();
                //     for method in &methods {
                //         set.insert(method.id);
                //     }
                //     Some(methods)
                // },
                _ => None,
            }
        }).flatten().collect();
        let functions = parser.krate.index.values().filter_map(|item| {
            if !is_item_valid(item) {
                return None;
            }
            match &item.inner {
                rd::ItemEnum::Function(f) => {
                    if set.contains(&item.id.0) {
                        return None;   // already parsed as method
                    }
                    let func = parser.parse_function(item, f, None, vec![]);
                    match func {
                        Ok(api) => Some(api),
                        Err(e) => {
                            warn!("Failed to parse function {}: {:?}", item.name.clone().unwrap_or("UnknownFunction".to_owned()), e);
                            None
                        }
                    }
                },
                _ => None,
            }
        });
        methods.extend(functions);
        Ok(methods)
    }
}

impl<const OFFSET: u32> APIDocParser<Vec<TypeAlias>, &RustDocParser<OFFSET>> for TypeAlias {
    fn parse(parser: &RustDocParser<OFFSET>) -> CrateResult<Vec<TypeAlias>> {
        let result = parser.krate.index.values().filter_map(|item| {
            if !is_item_valid(item) {
                return None;
            }
            match &item.inner {
                rd::ItemEnum::TypeAlias(ta) => Some(parser.parse_type_alias(ta)),
                _ => None,
            }
        }).collect();

        Ok(result)
    }
}

impl<const OFFSET: u32> APIDocParser<Vec<Primitive>, &RustDocParser<OFFSET>> for Primitive {
    fn parse(parser: &RustDocParser<OFFSET>) -> CrateResult<Vec<Primitive>> {
        let result = parser.krate.index.values().filter_map(|item| {
            if !is_item_valid(item) {
                return None;
            }
            match &item.inner {
                rd::ItemEnum::Primitive(p) => Some(parser.parse_primitives(item, p)),
                _ => None,
            }
        }).collect();

        Ok(result)
    }
}