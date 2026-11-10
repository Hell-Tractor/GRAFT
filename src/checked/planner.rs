use super::{
    model::*,
    solver::{prove, resolve, unify},
};
use std::collections::BTreeSet;

#[derive(Debug, Clone)]
pub struct Limits {
    pub nodes: usize,
    pub depth: usize,
    pub monomorphs: usize,
    pub type_pool: Vec<Ty>,
    /// Counterfactual only: use a separate concrete assignment for each argument.
    pub independent_arguments: bool,
}
impl Default for Limits {
    fn default() -> Self {
        Self {
            nodes: 2000,
            depth: 12,
            monomorphs: 128,
            type_pool: vec![Ty::primitive("u8"), Ty::primitive("i32")],
            independent_arguments: false,
        }
    }
}
#[derive(Clone, Default)]
struct State {
    steps: Vec<Step>,
    moved: BTreeSet<usize>,
    reserved: BTreeSet<usize>,
    calls: usize,
    obligations: Vec<Obligation>,
}
#[derive(Clone)]
struct Instance {
    api: Api,
    env: Substitution,
}
struct Search<'a> {
    catalog: &'a Catalog,
    limits: &'a Limits,
    nodes: usize,
    reasons: Vec<Failure>,
}
impl Search<'_> {
    fn tick(&mut self, depth: usize) -> Result<(), Failure> {
        if self.nodes >= self.limits.nodes || depth > self.limits.depth {
            Err(Failure::BudgetExhausted)
        } else {
            self.nodes += 1;
            Ok(())
        }
    }
    fn instances(&self, api: &Api) -> Result<Vec<Instance>, Failure> {
        if !api.unsupported.is_empty() {
            return Err(Failure::unknown(api.unsupported.join("; ")));
        }
        let mut envs = vec![Substitution::new()];
        for var in &api.generics {
            let mut next = vec![];
            for env in envs {
                for ty in &self.limits.type_pool {
                    let mut e = env.clone();
                    e.insert(var.clone(), ty.clone());
                    next.push(e);
                    if next.len() > self.limits.monomorphs {
                        return Err(Failure::unknown("finite monomorphization limit"));
                    }
                }
            }
            envs = next;
        }
        Ok(envs
            .into_iter()
            .map(|env| Instance {
                api: api.clone(),
                env,
            })
            .collect())
    }
    fn call(
        &mut self,
        i: &Instance,
        state: State,
        stack: &[Ty],
        depth: usize,
    ) -> Result<State, Failure> {
        self.tick(depth)?;
        let output = resolve(&i.api.output, &i.env);
        if output.contains_ref() {
            return Err(Failure::unknown(
                "borrowed output requires lifetime analysis",
            ));
        }
        if let Some(r) = output.unknown() {
            return Err(Failure::unknown(r));
        }
        let mut state = state;
        for o in &i.api.obligations {
            let o = Obligation {
                ty: resolve(&o.ty, &i.env),
                trait_ty: resolve(&o.trait_ty, &i.env),
            };
            prove(&o, &self.catalog.impls, &mut vec![], self.limits.depth)?;
            state.obligations.push(o);
        }
        let types: Vec<_> = i.api.inputs.iter().enumerate().map(|(n, x)| {
            let mut env = i.env.clone();
            if self.limits.independent_arguments && !self.limits.type_pool.is_empty() {
                for ty in env.values_mut() {
                    let base = self.limits.type_pool.iter().position(|t| t == ty).unwrap_or(0);
                    *ty = self.limits.type_pool[(base+n)%self.limits.type_pool.len()].clone();
                }
            }
            resolve(x, &env)
        }).collect();
        self.args(i, &types, 0, state, vec![], stack, depth)
    }
    fn args(
        &mut self,
        i: &Instance,
        types: &[Ty],
        n: usize,
        state: State,
        bindings: Vec<Binding>,
        stack: &[Ty],
        depth: usize,
    ) -> Result<State, Failure> {
        if n == types.len() {
            let mut state = state;
            for b in &bindings {
                state.reserved.remove(&b.value);
            }
            let instance = state.calls;
            state.calls += 1;
            let owner = i.api.owner.as_ref().map(|x| resolve(x, &i.env));
            let path = if let Some(t) = owner {
                format!("<{}>::{}", super::emit::rust_type(&t)?, i.api.path)
            } else {
                i.api.path.clone()
            };
            let substitution = i
                .env
                .iter()
                .map(|(v, t)| {
                    (
                        format!("{}::{}@call{}", v.scope, v.name, instance),
                        t.clone(),
                    )
                })
                .collect();
            state.steps.push(Step::Call {
                instance,
                api: i.api.id.clone(),
                path,
                type_args: i
                    .api
                    .function_generics
                    .iter()
                    .map(|v| resolve(&Ty::Var { var: v.clone() }, &i.env))
                    .collect(),
                substitution,
                args: bindings,
                value: state.steps.len(),
                ty: resolve(&i.api.output, &i.env),
            });
            return Ok(state);
        }
        let target = &types[n];
        let (mut ty, mut adapter) = match target {
            Ty::Ref { mutable, inner } => (
                inner.as_ref().clone(),
                if *mutable {
                    Adapter::MutableBorrow
                } else {
                    Adapter::SharedBorrow
                },
            ),
            _ => (target.clone(), Adapter::Move),
        };
        if let Ty::Slice { inner } = &ty {
            ty = Ty::Array {
                inner: inner.clone(),
                len: 4,
            };
            adapter = if adapter == Adapter::MutableBorrow {
                Adapter::MutableSlice
            } else {
                Adapter::SharedSlice
            };
        }
        let mut last = Failure::NoProducer { ty: ty.clone() };
        // Existing values first, then a fresh construction; recursive continuation allows backtracking.
        let mut candidates: Vec<(State, usize)> = state
            .steps
            .iter()
            .filter(|s| {
                !state.moved.contains(&s.value())
                    && (!state.reserved.contains(&s.value())
                        || bindings.iter().any(|b| b.value == s.value()))
                    && s.ty() == &ty
            })
            .map(|s| (state.clone(), s.value()))
            .collect();
        match self.produce(&ty, state.clone(), stack, depth + 1) {
            Ok(s) => {
                let v = s.steps.last().unwrap().value();
                candidates.push((s, v));
            }
            Err(e) => {
                last = e;
            }
        }
        for (mut s, value) in candidates {
            let conflict = bindings.iter().any(|b| {
                b.value == value
                    && !(b.adapter == Adapter::SharedBorrow && adapter == Adapter::SharedBorrow)
                    && !(b.adapter == Adapter::Move && adapter == Adapter::Move && ty.is_copy())
            });
            if conflict {
                last = Failure::incompatible("overlapping move/mutable/shared argument use");
                continue;
            }
            if adapter == Adapter::Move && !ty.is_copy() {
                s.moved.insert(value);
            }
            s.reserved.insert(value);
            let mut bs = bindings.clone();
            bs.push(Binding {
                value,
                adapter: adapter.clone(),
            });
            match self.args(i, types, n + 1, s, bs, stack, depth) {
                Ok(s) => return Ok(s),
                Err(e) => last = e,
            }
        }
        Err(last)
    }
    fn produce(
        &mut self,
        ty: &Ty,
        state: State,
        stack: &[Ty],
        depth: usize,
    ) -> Result<State, Failure> {
        self.tick(depth)?;
        if let Some(r) = ty.unknown() {
            return Err(Failure::unknown(r));
        }
        if stack.contains(ty) {
            return Err(Failure::Cycle { ty: ty.clone() });
        }
        let mut next_stack = stack.to_vec();
        next_stack.push(ty.clone());
        if super::emit::input_supported(ty) {
            let mut s = state;
            s.steps.push(Step::Input {
                value: s.steps.len(),
                ty: ty.clone(),
            });
            return Ok(s);
        }
        // Explicit constructors, never a wrapper-stripping equality rule.
        if let Some((constructor, elements)) = constructors(ty) {
            let mut s = state.clone();
            let mut args = vec![];
            let mut valid = true;
            for elem in elements {
                match self.produce(&elem, s.clone(), &next_stack, depth + 1) {
                    Ok(mut produced) => {
                        let v = produced.steps.last().unwrap().value();
                        if !elem.is_copy() {
                            produced.moved.insert(v);
                        }
                        args.push(Binding {
                            value: v,
                            adapter: Adapter::Move,
                        });
                        s = produced;
                    }
                    Err(_) => {
                        valid = false;
                        break;
                    }
                }
            }
            if valid {
                s.steps.push(Step::Construct {
                    value: s.steps.len(),
                    ty: ty.clone(),
                    constructor,
                    args,
                });
                return Ok(s);
            }
        }
        let mut last = Failure::NoProducer { ty: ty.clone() };
        for api in &self.catalog.apis {
            let instances = match self.instances(api) {
                Ok(i) => i,
                Err(e) => {
                    let (candidate, _) = unwrap_output(&api.output);
                    if unify(&candidate, ty, &mut Substitution::new()).is_ok() {
                        self.reasons.push(e.clone());
                        last = e;
                    }
                    continue;
                }
            };
            for i in instances {
                let output = resolve(&api.output, &i.env);
                let (candidate, variant) = unwrap_output(&output);
                if unify(&candidate, ty, &mut Substitution::new()).is_err() {
                    continue;
                }
                match self.call(&i, state.clone(), &next_stack, depth + 1) {
                    Ok(mut s) => {
                        if let Some(variant) = variant {
                            let source = s.steps.last().unwrap().value();
                            s.moved.insert(source);
                            s.steps.push(Step::Extract {
                                value: s.steps.len(),
                                source,
                                variant,
                                ty: candidate,
                            });
                        }
                        return Ok(s);
                    }
                    Err(e) => {
                        self.reasons.push(e.clone());
                        last = e;
                    }
                }
            }
        }
        Err(last)
    }
}
fn constructors(ty: &Ty) -> Option<(String, Vec<Ty>)> {
    match ty {
        Ty::Tuple { items } => Some(("tuple".into(), items.clone())),
        Ty::Array { inner, len } if *len <= 32 => {
            Some(("array".into(), vec![*inner.clone(); *len]))
        }
        Ty::Nominal { path, args, .. } if args.len() == 1 => match path.as_str() {
            "alloc::vec::Vec" => Some(("vec".into(), args.clone())),
            "alloc::boxed::Box" => Some(("box".into(), args.clone())),
            "core::option::Option" => Some(("some".into(), args.clone())),
            _ => None,
        },
        _ => None,
    }
}
fn unwrap_output(ty: &Ty) -> (Ty, Option<String>) {
    if let Ty::Nominal { path, args, .. } = ty {
        if path == "core::option::Option" && args.len() == 1 {
            return (args[0].clone(), Some("Some".into()));
        }
        if path == "core::result::Result" && args.len() == 2 {
            return (args[0].clone(), Some("Ok".into()));
        }
    }
    (ty.clone(), None)
}
pub fn plan(catalog: &Catalog, target: &str, limits: &Limits) -> PlanResult {
    let mut search = Search {
        catalog,
        limits,
        nodes: 0,
        reasons: vec![],
    };
    let attempt = (|| {
        let api = catalog
            .apis
            .iter()
            .find(|x| x.id == target)
            .ok_or_else(|| Failure::incompatible("unknown target API"))?;
        for i in search.instances(api)? {
            match search.call(&i, State::default(), &[], 0) {
                Ok(s) => return Ok(s),
                Err(e) => {
                    search.reasons.push(e);
                    if search.nodes >= limits.nodes {
                        break;
                    }
                }
            }
        }
        Err(Failure::incompatible(
            "no complete instance in finite type pool",
        ))
    })();
    match attempt {
        Ok(s) => {
            let mut identities=BTreeSet::new();
            for step in &s.steps {
                step.ty().nominal_identities(&mut identities);
                if let Step::Call{type_args,..}=step {for t in type_args {t.nominal_identities(&mut identities);}}
            }
            PlanResult::Planned {
            plan: SequencePlan {
                schema: if limits.independent_arguments { "graft-ablation-independent-arguments-v1" } else { "graft-plan-v1" }.into(),
                crate_name: catalog.crate_name.clone(),
                crate_version: catalog.crate_version.clone(),
                package_name: catalog.package_name.clone(),
                features: catalog.features.clone(),
                default_features: catalog.default_features,
                rustdoc_sha256: catalog.rustdoc_sha256.clone(),
                target: target.into(),
                steps: s.steps,
                obligations: s.obligations,
                unresolved: vec![],
                search_nodes: search.nodes,
                type_paths: catalog.type_paths.iter().filter(|p|identities.contains(&p.identity)).cloned().collect(),
                infer_call_results:false,
            },
        }},
        Err(e) => {
            search.reasons.push(e);
            PlanResult::Rejected {
                target: target.into(),
                reasons: search.reasons,
                search_nodes: search.nodes,
            }
        }
    }
}

/// At most two alternate producer exclusions after the original plan. No target
/// replacement, version change, coverage ranking or unbounded candidate search.
pub fn alternatives(catalog:&Catalog,original:&SequencePlan,limits:&Limits,max_candidates:usize)
    ->Result<serde_json::Value,Failure> {
    if max_candidates==0 || max_candidates>2 || limits.nodes==0 || limits.nodes>2000 || catalog.crate_name!=original.crate_name
        || catalog.crate_version!=original.crate_version || catalog.package_name!=original.package_name
        || catalog.features!=original.features || catalog.default_features!=original.default_features
        || catalog.rustdoc_sha256!=original.rustdoc_sha256 {
        return Err(Failure::incompatible("alternate plans require the original pinned catalog and at most two alternatives"));
    }
    let mut excluded=vec![];
    for step in original.steps.iter().rev() {
        if let Step::Call{api,..}=step {
            if api!=&original.target && !excluded.contains(api) {excluded.push(api.clone());}
        }
    }
    let mut spent_nodes=0;let mut rows=vec![];
    for producer in excluded.into_iter().take(max_candidates) {
        if spent_nodes>=limits.nodes {break;}
        let mut candidate=catalog.clone();candidate.apis.retain(|a|a.id!=producer);
        let mut bounds=limits.clone();bounds.nodes=limits.nodes-spent_nodes;
        bounds.independent_arguments=original.schema=="graft-ablation-independent-arguments-v1";
        let mut result=plan(&candidate,&original.target,&bounds);
        let nodes=match &mut result {
            PlanResult::Planned{plan}=>{plan.infer_call_results=original.infer_call_results;plan.search_nodes},
            PlanResult::Rejected{search_nodes,..}=>*search_nodes,
        };
        spent_nodes+=nodes;
        rows.push(serde_json::json!({"excluded_producer":producer,"result":result}));
    }
    Ok(serde_json::json!({"schema":"graft-bounded-alternatives-v1","target":original.target,
        "search_nodes":spent_nodes,"max_candidates":max_candidates,"candidates":rows}))
}
