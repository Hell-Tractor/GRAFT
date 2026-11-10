//! Transactional first-order unification and bounded positive trait resolution.
use super::model::*;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum Match {
    Compatible {
        substitution: Vec<(Var, Ty)>,
        adapters: Vec<Adapter>,
        obligations: Vec<Obligation>,
    },
    Incompatible {
        reason: String,
    },
    Unknown {
        reason: String,
    },
}
pub fn resolve(ty: &Ty, env: &Substitution) -> Ty {
    ty.map(&|v| {
        env.get(v)
            .map(|t| resolve(t, env))
            .unwrap_or(Ty::Var { var: v.clone() })
    })
}
fn occurs(v: &Var, ty: &Ty) -> bool {
    match ty {
        Ty::Var { var } => var == v,
        Ty::Nominal { args, .. } | Ty::Tuple { items: args } => args.iter().any(|x| occurs(v, x)),
        Ty::Ref { inner, .. } | Ty::Slice { inner } | Ty::Array { inner, .. } => occurs(v, inner),
        _ => false,
    }
}
fn unify_inner(a: &Ty, b: &Ty, env: &mut Substitution) -> Result<(), Failure> {
    let a = resolve(a, env);
    let b = resolve(b, env);
    if let Some(r) = a.unknown().or_else(|| b.unknown()) {
        return Err(Failure::unknown(r));
    }
    if a == b {
        return Ok(());
    }
    match (&a, &b) {
        (Ty::Var { var }, ty) | (ty, Ty::Var { var }) => {
            if occurs(var, ty) {
                return Err(Failure::incompatible("occurs check"));
            }
            env.insert(var.clone(), ty.clone());
            Ok(())
        }
        (
            Ty::Nominal {
                identity: i,
                args: x,
                ..
            },
            Ty::Nominal {
                identity: j,
                args: y,
                ..
            },
        ) if i == j && x.len() == y.len() => unify_list(x, y, env),
        (Ty::Tuple { items: x }, Ty::Tuple { items: y }) if x.len() == y.len() => {
            unify_list(x, y, env)
        }
        (
            Ty::Ref {
                mutable: x,
                inner: a,
            },
            Ty::Ref {
                mutable: y,
                inner: b,
            },
        ) if x == y => unify_inner(a, b, env),
        (Ty::Slice { inner: a }, Ty::Slice { inner: b }) => unify_inner(a, b, env),
        (Ty::Array { inner: a, len: n }, Ty::Array { inner: b, len: m }) if n == m => {
            unify_inner(a, b, env)
        }
        _ => Err(Failure::incompatible(format!(
            "type conflict: {a:?} vs {b:?}"
        ))),
    }
}
fn unify_list(a: &[Ty], b: &[Ty], env: &mut Substitution) -> Result<(), Failure> {
    for (x, y) in a.iter().zip(b) {
        unify_inner(x, y, env)?;
    }
    Ok(())
}
/// Failure never leaves a partially committed substitution.
pub fn unify(a: &Ty, b: &Ty, env: &mut Substitution) -> Result<(), Failure> {
    let mut trial = env.clone();
    unify_inner(a, b, &mut trial)?;
    *env = trial;
    Ok(())
}
pub fn match_type(
    source: &Ty,
    target: &Ty,
    env: &mut Substitution,
    obligations: &[Obligation],
) -> Match {
    let (source, target, adapters) = match target {
        Ty::Ref { mutable, inner } if !matches!(source, Ty::Ref { .. }) => (
            source,
            inner.as_ref(),
            vec![if *mutable {
                Adapter::MutableBorrow
            } else {
                Adapter::SharedBorrow
            }],
        ),
        _ => (source, target, vec![Adapter::Move]),
    };
    match unify(source, target, env) {
        Ok(()) => Match::Compatible {
            substitution: env
                .iter()
                .map(|(v, t)| (v.clone(), resolve(t, env)))
                .collect(),
            adapters,
            obligations: obligations.to_vec(),
        },
        Err(Failure::Unknown { reason }) => Match::Unknown { reason },
        Err(e) => Match::Incompatible {
            reason: format!("{e:?}"),
        },
    }
}

pub fn prove(
    obligation: &Obligation,
    impls: &[TraitImpl],
    stack: &mut Vec<String>,
    budget: usize,
) -> Result<(), Failure> {
    if budget == 0 {
        return Err(Failure::BudgetExhausted);
    }
    if let Some(r) = obligation
        .ty
        .unknown()
        .or_else(|| obligation.trait_ty.unknown())
    {
        return Err(Failure::unknown(r));
    }
    let key = format!("{:?}:{:?}", obligation.ty, obligation.trait_ty);
    if stack.contains(&key) {
        return Err(Failure::unknown("recursive trait obligation"));
    }
    stack.push(key);
    let result = (|| {
        let mut unknown = None;
        for imp in impls {
            let mut env = Substitution::new();
            if unify(&imp.for_ty, &obligation.ty, &mut env).is_err()
                || unify(&imp.trait_ty, &obligation.trait_ty, &mut env).is_err()
            {
                continue;
            }
            if !imp.unsupported.is_empty() {
                unknown = Some(Failure::unknown(imp.unsupported.join("; ")));
                continue;
            }
            let mut valid = true;
            for nested in &imp.obligations {
                let nested = Obligation {
                    ty: resolve(&nested.ty, &env),
                    trait_ty: resolve(&nested.trait_ty, &env),
                };
                if let Err(e) = prove(&nested, impls, stack, budget - 1) {
                    if !matches!(e, Failure::Incompatible { .. }) {
                        unknown = Some(e);
                    }
                    valid = false;
                    break;
                }
            }
            if valid {
                return Ok(());
            }
        }
        // Catalog is not a closed world (external / blanket impls may be absent).
        Err(unknown.unwrap_or_else(|| {
            Failure::unknown(format!(
                "no proven impl for {obligation:?}; external impl universe is incomplete"
            ))
        }))
    })();
    stack.pop();
    result
}
