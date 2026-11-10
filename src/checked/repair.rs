//! Parameter repair contract: preserve actual planned calls, permit preparation.
//! Runtime probes are still required; AST use is not a complete taint proof.
use super::{emit, model::*, rustdoc::sha256};
use quote::{format_ident, quote, ToTokens};
use std::collections::{BTreeMap, BTreeSet};
use syn::visit::Visit;

fn local_name(local: &syn::Local) -> Option<String> {
    let pat = match &local.pat {
        syn::Pat::Type(p) => p.pat.as_ref(),
        p => p,
    };
    match pat {
        syn::Pat::Ident(p) if p.subpat.is_none() => Some(p.ident.to_string()),
        _ => None,
    }
}

fn function(file: &syn::File) -> Result<&syn::ItemFn, String> {
    file.items.iter().find_map(|item| match item {
        syn::Item::Fn(f) if f.sig.ident == "graft_body" => Some(f),
        _ => None,
    }).ok_or_else(|| "repair requires graft_body".into())
}

fn direct_producer(expr:&syn::Expr,name:&str)->bool {
    match expr {
        syn::Expr::Path(p)=>p.qself.is_none() && p.path.is_ident(name),
        syn::Expr::Reference(r)=>direct_producer(&r.expr,name),
        syn::Expr::Paren(p)=>direct_producer(&p.expr,name),
        syn::Expr::Group(g)=>direct_producer(&g.expr,name),
        syn::Expr::Unary(u) if matches!(u.op,syn::UnOp::Deref(_))=>direct_producer(&u.expr,name),
        syn::Expr::MethodCall(c) if c.method=="clone" && c.args.is_empty()=>direct_producer(&c.receiver,name),
        _=>false,
    }
}

/// Remove only trusted emitter instrumentation from the reference given to LLM.
pub fn seed(plan: &SequencePlan) -> Result<String, String> {
    let mut file = syn::parse_file(&emit::emit(plan).map_err(|e| format!("{e:?}"))?)
        .map_err(|e| e.to_string())?;
    for item in &mut file.items {
        if let syn::Item::Fn(f) = item {
            f.attrs.clear();
            f.block.stmts.retain(|stmt| {
                let text = stmt.to_token_stream().to_string();
                !text.contains("observed_") && !text.contains("black_box")
                    && !text.contains("GRAFT_TRACE") && !text.contains("trace . push")
            });
        }
    }
    Ok(file.to_token_stream().to_string())
}

struct Review<'a> {
    crate_name: &'a str,
    errors: Vec<String>,
    calls: Vec<String>,
    bindings: BTreeMap<String, usize>,
    data_uses: usize,
}
impl<'ast> Visit<'ast> for Review<'_> {
    fn visit_attribute(&mut self, _: &'ast syn::Attribute) {
        self.errors.push("attributes are not permitted in parameter repair".into());
    }
    fn visit_ident(&mut self, ident: &'ast syn::Ident) {
        if ident.to_string().starts_with("__graft_") {
            self.errors.push("reserved instrumentation identifier".into());
        }
    }
    fn visit_pat_ident(&mut self, pat: &'ast syn::PatIdent) {
        *self.bindings.entry(pat.ident.to_string()).or_default() += 1;
        syn::visit::visit_pat_ident(self, pat);
    }
    fn visit_expr_path(&mut self, path: &'ast syn::ExprPath) {
        if path.path.is_ident("data") { self.data_uses += 1; }
        let root = path.path.segments.first().map(|s| s.ident.to_string()).unwrap_or_default();
        if ["std", "core", "alloc"].contains(&root.as_str()) {
            let text = path.to_token_stream().to_string().replace(' ', "");
            let allowed = ["std::hint::black_box", "std::cmp::", "core::cmp::",
                "core::array::from_fn", "std::vec::Vec", "alloc::vec::Vec"];
            if !allowed.iter().any(|prefix| text.starts_with(prefix)) {
                self.errors.push(format!("unapproved helper path: {text}"));
            }
        }
        syn::visit::visit_expr_path(self, path);
    }
    fn visit_expr_call(&mut self, call: &'ast syn::ExprCall) {
        self.calls.push(call.func.to_token_stream().to_string());
        if let syn::Expr::Path(path) = call.func.as_ref() {
            let first = path.path.segments.first().unwrap().ident.to_string();
            let primitive = ["u8", "u16", "u32", "u64", "u128", "usize", "i8", "i16",
                "i32", "i64", "i128", "isize", "f32", "f64", "char", "bool"];
            if path.qself.is_none() && first != self.crate_name
                && !["std", "core", "alloc", "Vec", "Some", "Ok", "Err"].contains(&first.as_str())
                && !primitive.contains(&first.as_str()) {
                self.errors.push(format!("unapproved function path: {first}"));
            }
            if let Some(qself) = &path.qself {
                let owner = match qself.ty.as_ref() {
                    syn::Type::Path(ty) => ty.path.segments.first().map(|s| s.ident.to_string()),
                    _ => None,
                };
                if !owner.as_deref().is_some_and(|owner| owner == self.crate_name
                    || owner == "Vec" || primitive.contains(&owner)) {
                    self.errors.push("unapproved qualified helper owner".into());
                }
            }
        } else {
            self.errors.push("indirect/closure calls are not permitted".into());
        }
        syn::visit::visit_expr_call(self, call);
    }
    fn visit_expr_method_call(&mut self, call: &'ast syn::ExprMethodCall) {
        let name = call.method.to_string();
        if name.contains("unchecked") || ["set_len", "advance_mut", "catch_unwind"].contains(&name.as_str()) {
            self.errors.push(format!("unapproved helper method: {name}"));
        }
        syn::visit::visit_expr_method_call(self, call);
    }
    fn visit_macro(&mut self, _: &'ast syn::Macro) {
        self.errors.push("model macros/trace output are not permitted".into());
    }
    fn visit_expr_unsafe(&mut self, _: &'ast syn::ExprUnsafe) {
        self.errors.push("unsafe is not permitted in parameter repair".into());
    }
    fn visit_type_ptr(&mut self, _: &'ast syn::TypePtr) {
        self.errors.push("raw pointers are not permitted".into());
    }
    fn visit_expr_for_loop(&mut self, _: &'ast syn::ExprForLoop) {
        self.errors.push("explicit loops are not supported by this repair contract".into());
    }
    fn visit_expr_while(&mut self, _: &'ast syn::ExprWhile) {
        self.errors.push("explicit loops are not supported by this repair contract".into());
    }
    fn visit_expr_loop(&mut self, _: &'ast syn::ExprLoop) {
        self.errors.push("explicit loops are not supported by this repair contract".into());
    }
    fn visit_stmt(&mut self, stmt: &'ast syn::Stmt) {
        if matches!(stmt, syn::Stmt::Item(_)) {
            self.errors.push("nested items/imports may shadow planned APIs".into());
        }
        syn::visit::visit_stmt(self, stmt);
    }
}

pub struct Prepared {
    pub core: String,
    pub standalone: String,
    pub evidence: serde_json::Value,
}

pub fn prepare(plan: &SequencePlan, source: &str) -> Result<Prepared, String> {
    let expected = syn::parse_file(&emit::emit(plan).map_err(|e| format!("{e:?}"))?)
        .map_err(|e| e.to_string())?;
    let reference = function(&expected)?;
    let candidate = syn::parse_file(source).map_err(|e| format!("invalid repair Rust: {e}"))?;
    if !candidate.attrs.is_empty() { return Err("crate attributes are forbidden".into()); }
    let mut functions = 0;
    for item in &candidate.items {
        match item {
            syn::Item::Fn(f) if f.sig.ident == "graft_body" => functions += 1,
            syn::Item::ExternCrate(e) if e.to_token_stream().to_string() == "extern crate alloc ;" => {},
            _ => return Err("repair may only define graft_body and extern crate alloc".into()),
        }
    }
    if functions != 1 { return Err("repair requires exactly one graft_body".into()); }
    let mut repaired = function(&candidate)?.clone();
    if repaired.sig.to_token_stream().to_string() != reference.sig.to_token_stream().to_string() {
        return Err("repair changed byte-input entry signature".into());
    }
    let mut review = Review { crate_name: &plan.crate_name, errors: vec![], calls: vec![],
        bindings: BTreeMap::new(), data_uses: 0 };
    review.visit_item_fn(&repaired);
    if !review.errors.is_empty() { return Err(review.errors.join("; ")); }
    if review.data_uses == 0 { return Err("repair has no byte-input use".into()); }

    let mut calls = vec![];
    for step in &plan.steps {
        if let Step::Call { instance, api, value, args, .. } = step {
            let name = format!("v{value}");
            let local = reference.block.stmts.iter().find_map(|s| match s {
                syn::Stmt::Local(l) if local_name(l).as_deref() == Some(&name) => Some(l),
                _ => None,
            }).ok_or("emitted call binding absent")?;
            let syn::Expr::Call(expr) = local.init.as_ref().ok_or("missing initializer")?.expr.as_ref()
                else { return Err("emitted call not direct".into()); };
            calls.push((*instance, api.clone(), name, expr.func.to_token_stream().to_string(), args));
        }
    }
    let paths: BTreeSet<_> = calls.iter().map(|c| c.3.clone()).collect();
    let seen: Vec<_> = review.calls.iter().filter(|c| paths.contains(*c)).cloned().collect();
    if seen != calls.iter().map(|c| c.3.clone()).collect::<Vec<_>>() {
        return Err("planned API calls were deleted, duplicated, or reordered".into());
    }
    let mut position = 0;
    let mut rows = vec![];
    let mut producer_names: BTreeMap<usize,String> = BTreeMap::new();
    for stmt in &mut repaired.block.stmts {
        let syn::Stmt::Local(local) = stmt else { continue };
        let Some(name) = local_name(local) else { continue };
        let Some(init) = local.init.as_mut() else { continue };
        let syn::Expr::Call(call) = init.expr.as_ref() else { continue };
        if !paths.contains(&call.func.to_token_stream().to_string()) { continue; }
        let (instance, api, expected_name, func, args) = calls.get(position)
            .ok_or("too many planned bindings")?;
        if review.bindings.get(&name) != Some(&1) {
            return Err(format!("planned result binding {name} is shadowed"));
        }
        if init.diverge.is_some() { return Err("planned call initializer may not diverge".into()); }
        if call.func.to_token_stream().to_string() != *func || call.args.len() != args.len() {
            return Err(format!("planned API/type arguments changed for {name}"));
        }
        for (binding,actual) in args.iter().zip(&call.args) {
            if !emit::input_supported(plan.steps[binding.value].ty()) {
                if let Some(producer) = producer_names.get(&binding.value) {
                    if !direct_producer(actual,producer) {
                        return Err(format!("planned producer dependency {producer} was bypassed"));
                    }
                }
            }
        }
        let function = &call.func;
        let identifiers: Vec<_> = (0..call.args.len()).map(|i| format_ident!("__graft_arg_{i}")).collect();
        let actual_args: Vec<_> = call.args.iter().collect();
        let scalar: Vec<_> = args.iter().enumerate().filter_map(|(i, binding)| {
            emit::input_supported(plan.steps[binding.value].ty()).then(|| identifiers[i].clone())
        }).collect();
        let marker = format!("GRAFT_CALL:{instance}:{{}}");
        let replacement: syn::Expr = syn::parse_quote!({
            #(let #identifiers = #actual_args;)*
            let __graft_observed = format!("{:?}", (#(&#scalar,)*));
            let __graft_result = #function(#(#identifiers),*);
            std::hint::black_box(&__graft_result);
            if std::env::var_os("GRAFT_TRACE").is_some() {
                eprintln!(#marker, __graft_observed);
            }
            __graft_result
        });
        init.expr = Box::new(replacement);
        let original_value:usize=expected_name.trim_start_matches('v').parse()
            .map_err(|_|"invalid original producer binding")?;
        producer_names.insert(original_value,name.clone());
        rows.push(serde_json::json!({"instance": instance, "api": api, "binding": name,
            "original_binding":expected_name,
            "callee_tokens": func, "observed_scalar_positions": args.iter().enumerate()
                .filter_map(|(i,b)| emit::input_supported(plan.steps[b.value].ty()).then_some(i)).collect::<Vec<_>>()}));
        position += 1;
    }
    if position != calls.len() { return Err("planned calls must remain top-level local initializers".into()); }
    let core = quote!(extern crate alloc; #repaired).to_string();
    let standalone = format!("{core}\nfn main() {{ let data: Vec<u8> = std::env::args().skip(1).map(|v| v.parse::<u8>().unwrap()).collect(); std::hint::black_box(graft_body(&data)); }}");
    let evidence = serde_json::json!({"schema":"graft-parameter-repair-contract-v2",
        "target":plan.target,"crate":plan.crate_name,"version":plan.crate_version,
        "source_sha256":sha256(source.as_bytes()),"instrumented_sha256":sha256(core.as_bytes()),
        "calls":rows,"byte_input_ast_uses":review.data_uses,
        "scope":"preserved direct planned calls and tool-inserted post-call markers; runtime target arrival and scalar influence remain mandatory; opaque argument taint and universal preconditions are not proved"});
    Ok(Prepared { core, standalone, evidence })
}
