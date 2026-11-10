use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Identity includes definition and call/impl instance; names alone are never keys.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Var {
    pub scope: String,
    pub name: String,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Ty {
    Primitive {
        name: String,
    },
    Var {
        var: Var,
    },
    Nominal {
        identity: String,
        path: String,
        args: Vec<Ty>,
    },
    Ref {
        mutable: bool,
        inner: Box<Ty>,
    },
    Slice {
        inner: Box<Ty>,
    },
    Array {
        inner: Box<Ty>,
        len: usize,
    },
    Tuple {
        items: Vec<Ty>,
    },
    Unknown {
        reason: String,
    },
}
impl Ty {
    pub fn primitive(name: &str) -> Self {
        Self::Primitive { name: name.into() }
    }
    pub fn var(scope: &str, name: &str) -> Self {
        Self::Var {
            var: Var {
                scope: scope.into(),
                name: name.into(),
            },
        }
    }
    pub fn unit() -> Self {
        Self::Tuple { items: vec![] }
    }
    pub fn map(&self, f: &impl Fn(&Var) -> Ty) -> Ty {
        match self {
            Self::Var { var } => f(var),
            Self::Nominal {
                identity,
                path,
                args,
            } => Self::Nominal {
                identity: identity.clone(),
                path: path.clone(),
                args: args.iter().map(|x| x.map(f)).collect(),
            },
            Self::Ref { mutable, inner } => Self::Ref {
                mutable: *mutable,
                inner: Box::new(inner.map(f)),
            },
            Self::Slice { inner } => Self::Slice {
                inner: Box::new(inner.map(f)),
            },
            Self::Array { inner, len } => Self::Array {
                inner: Box::new(inner.map(f)),
                len: *len,
            },
            Self::Tuple { items } => Self::Tuple {
                items: items.iter().map(|x| x.map(f)).collect(),
            },
            _ => self.clone(),
        }
    }
    pub fn contains_ref(&self) -> bool {
        match self {
            Self::Ref { .. } => true,
            Self::Nominal { args, .. } | Self::Tuple { items: args } => {
                args.iter().any(Self::contains_ref)
            }
            Self::Array { inner, .. } | Self::Slice { inner } => inner.contains_ref(),
            _ => false,
        }
    }
    pub fn unknown(&self) -> Option<String> {
        match self {
            Self::Unknown { reason } => Some(reason.clone()),
            Self::Nominal { args, .. } | Self::Tuple { items: args } => {
                args.iter().find_map(Self::unknown)
            }
            Self::Ref { inner, .. } | Self::Slice { inner } | Self::Array { inner, .. } => {
                inner.unknown()
            }
            _ => None,
        }
    }
    pub fn is_copy(&self) -> bool {
        match self {
            Self::Primitive { .. } => true,
            Self::Ref { mutable: false, .. } => true,
            Self::Array { inner, .. } => inner.is_copy(),
            Self::Tuple { items } => items.iter().all(Self::is_copy),
            _ => false,
        }
    }
}
pub type Substitution = BTreeMap<Var, Ty>;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Obligation {
    pub ty: Ty,
    pub trait_ty: Ty,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraitImpl {
    pub id: String,
    pub for_ty: Ty,
    pub trait_ty: Ty,
    pub obligations: Vec<Obligation>,
    pub unsupported: Vec<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Api {
    pub id: String,
    pub path: String,
    pub owner: Option<Ty>,
    pub generics: Vec<Var>,
    pub function_generics: Vec<Var>,
    pub inputs: Vec<Ty>,
    pub output: Ty,
    pub obligations: Vec<Obligation>,
    pub unsupported: Vec<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Catalog {
    pub schema: String,
    pub crate_name: String,
    pub crate_version: String,
    pub package_name: String,
    pub features: Vec<String>,
    pub default_features: bool,
    pub rustdoc_sha256: String,
    pub apis: Vec<Api>,
    pub impls: Vec<TraitImpl>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum Failure {
    Incompatible { reason: String },
    Unknown { reason: String },
    NoProducer { ty: Ty },
    Cycle { ty: Ty },
    BudgetExhausted,
}
impl Failure {
    pub fn incompatible(reason: impl Into<String>) -> Self {
        Self::Incompatible {
            reason: reason.into(),
        }
    }
    pub fn unknown(reason: impl Into<String>) -> Self {
        Self::Unknown {
            reason: reason.into(),
        }
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Adapter {
    Move,
    SharedBorrow,
    MutableBorrow,
    SharedSlice,
    MutableSlice,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Binding {
    pub value: usize,
    pub adapter: Adapter,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Step {
    Input {
        value: usize,
        ty: Ty,
    },
    Construct {
        value: usize,
        ty: Ty,
        constructor: String,
        args: Vec<Binding>,
    },
    Call {
        instance: usize,
        api: String,
        path: String,
        type_args: Vec<Ty>,
        substitution: BTreeMap<String, Ty>,
        args: Vec<Binding>,
        value: usize,
        ty: Ty,
    },
    Extract {
        value: usize,
        source: usize,
        variant: String,
        ty: Ty,
    },
}
impl Step {
    pub fn value(&self) -> usize {
        match self {
            Self::Input { value, .. }
            | Self::Construct { value, .. }
            | Self::Call { value, .. }
            | Self::Extract { value, .. } => *value,
        }
    }
    pub fn ty(&self) -> &Ty {
        match self {
            Self::Input { ty, .. }
            | Self::Construct { ty, .. }
            | Self::Call { ty, .. }
            | Self::Extract { ty, .. } => ty,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SequencePlan {
    pub schema: String,
    pub crate_name: String,
    pub crate_version: String,
    pub package_name: String,
    pub features: Vec<String>,
    pub default_features: bool,
    pub rustdoc_sha256: String,
    pub target: String,
    pub steps: Vec<Step>,
    pub obligations: Vec<Obligation>,
    pub unresolved: Vec<Failure>,
    pub search_nodes: usize,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum PlanResult {
    Planned {
        plan: SequencePlan,
    },
    Rejected {
        target: String,
        reasons: Vec<Failure>,
        search_nodes: usize,
    },
}
