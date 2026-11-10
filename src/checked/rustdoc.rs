//! Import from original rustdoc JSON, bypassing the legacy lossy bound parser.
use super::model::*;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::cell::RefCell;

pub fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
struct Import<'a> {
    krate: &'a Value,
    provenance: String,
    public_names: BTreeMap<String, String>,
    type_paths: RefCell<BTreeMap<String,TypePathEvidence>>,
}
impl Import<'_> {
    fn path(&self, p: &Value, scope: &str) -> Ty {
        let id = p["id"].to_string();
        let summary = &self.krate["paths"][&id];
        let path = summary["path"].as_array().map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join("::")
        });
        let Some(canonical) = path else {
            return Ty::Unknown {
                reason: format!("unresolved canonical path id {id}"),
            };
        };
        let path = if let Some(public) = self.public_names.get(&id) {
            public.clone()
        } else if summary["crate_id"] == 0 {
            return Ty::Unknown {
                reason: format!("no publicly named type path for {canonical}"),
            };
        } else {
            canonical.clone()
        };
        if summary["kind"] == "type_alias" {
            return Ty::Unknown {
                reason: format!("type alias expansion: {path}"),
            };
        }
        let crate_id = summary["crate_id"].as_u64().unwrap_or(u64::MAX);
        let identity = format!("{}:crate{crate_id}:item{id}:{canonical}", self.provenance);
        let external=&self.krate["external_crates"][crate_id.to_string()];
        self.type_paths.borrow_mut().insert(identity.clone(),TypePathEvidence {
            identity:identity.clone(),canonical_path:canonical.clone(),emitted_path:path.clone(),
            origin_crate_id:crate_id,
            origin_crate_name:external["name"].as_str().unwrap_or("").to_string(),
            origin_metadata_path:external["path"].as_str().map(String::from),
            public_reexport:crate_id!=0 && self.public_names.contains_key(&id),
        });
        let mut args = vec![];
        if !p["args"].is_null() {
            let Some(angle) = p["args"].get("angle_bracketed") else {
                return Ty::Unknown {
                    reason: "parenthesized/function trait arguments".into(),
                };
            };
            if angle["constraints"]
                .as_array()
                .is_some_and(|x| !x.is_empty())
            {
                return Ty::Unknown {
                    reason: "associated type binding/constraint".into(),
                };
            }
            for a in angle["args"].as_array().into_iter().flatten() {
                if let Some(t) = a.get("type") {
                    args.push(self.ty(t, scope));
                } else if a.get("lifetime").is_some() {
                    return Ty::Unknown {
                        reason: "nominal lifetime argument requires storage analysis".into(),
                    };
                } else {
                    return Ty::Unknown {
                        reason: "const/inferred generic argument".into(),
                    };
                }
            }
        }
        Ty::Nominal {
            identity,
            path,
            args,
        }
    }
    fn ty(&self, t: &Value, scope: &str) -> Ty {
        if let Some(p) = t.get("primitive").and_then(Value::as_str) {
            return Ty::primitive(p);
        }
        if let Some(g) = t.get("generic").and_then(Value::as_str) {
            return Ty::var(scope, g);
        }
        if let Some(p) = t.get("resolved_path") {
            return self.path(p, scope);
        }
        if let Some(r) = t.get("borrowed_ref") {
            if r["lifetime"] == "'static" {
                return Ty::Unknown {
                    reason: "static reference requires persistent storage".into(),
                };
            }
            return Ty::Ref {
                mutable: r["is_mutable"].as_bool().unwrap_or(false),
                inner: Box::new(self.ty(&r["type"], scope)),
            };
        }
        if let Some(s) = t.get("slice") {
            return Ty::Slice {
                inner: Box::new(self.ty(s, scope)),
            };
        }
        if let Some(a) = t.get("array") {
            if let Some(len) = a["len"].as_str().and_then(|s| s.parse::<usize>().ok()) {
                return Ty::Array {
                    inner: Box::new(self.ty(&a["type"], scope)),
                    len,
                };
            }
            return Ty::Unknown {
                reason: "symbolic const array length".into(),
            };
        }
        if let Some(ts) = t.get("tuple").and_then(Value::as_array) {
            return Ty::Tuple {
                items: ts.iter().map(|t| self.ty(t, scope)).collect(),
            };
        }
        Ty::Unknown {
            reason: format!(
                "unsupported rustdoc type: {}",
                t.as_object()
                    .and_then(|x| x.keys().next())
                    .map(String::as_str)
                    .unwrap_or("null")
            ),
        }
    }
    fn bounds(
        &self,
        ty: Ty,
        bounds: &Value,
        scope: &str,
        obligations: &mut Vec<Obligation>,
        unsupported: &mut Vec<String>,
    ) {
        if let Some(reason) = ty.unknown() {
            unsupported.push(reason);
        }
        for b in bounds.as_array().into_iter().flatten() {
            let Some(t) = b.get("trait_bound") else {
                unsupported.push("outlives/use bound".into());
                continue;
            };
            if t["generic_params"]
                .as_array()
                .is_some_and(|x| !x.is_empty())
            {
                unsupported.push("higher-ranked trait bound".into());
                continue;
            }
            if t["modifier"] != "none" {
                unsupported.push("modified/negative/?Sized trait bound".into());
                continue;
            }
            let trait_ty = self.path(&t["trait"], scope);
            if let Some(reason) = trait_ty.unknown() {
                unsupported.push(reason);
            }
            obligations.push(Obligation {
                ty: ty.clone(),
                trait_ty,
            });
        }
    }
    fn generics(&self, g: &Value, scope: &str) -> (Vec<Var>, Vec<Obligation>, Vec<String>) {
        let mut vars = vec![];
        let mut obs = vec![];
        let mut unsupported = vec![];
        for p in g["params"].as_array().into_iter().flatten() {
            let name = p["name"].as_str().unwrap_or("?");
            if let Some(k) = p["kind"].get("type") {
                vars.push(Var {
                    scope: scope.into(),
                    name: name.into(),
                });
                if k["is_synthetic"] == true {
                    unsupported.push("synthetic impl Trait parameter".into());
                }
                self.bounds(
                    Ty::var(scope, name),
                    &k["bounds"],
                    scope,
                    &mut obs,
                    &mut unsupported,
                );
            } else if let Some(k) = p["kind"].get("lifetime") {
                if k["outlives"].as_array().is_some_and(|x| !x.is_empty()) {
                    unsupported.push("lifetime outlives predicate".into());
                }
            } else {
                unsupported.push("const generic parameter".into());
            }
        }
        for w in g["where_predicates"].as_array().into_iter().flatten() {
            if let Some(p) = w.get("bound_predicate") {
                if p["generic_params"]
                    .as_array()
                    .is_some_and(|x| !x.is_empty())
                {
                    unsupported.push("higher-ranked where predicate".into());
                }
                self.bounds(
                    self.ty(&p["type"], scope),
                    &p["bounds"],
                    scope,
                    &mut obs,
                    &mut unsupported,
                );
            } else {
                unsupported.push("equality/lifetime where predicate".into());
            }
        }
        (vars, obs, unsupported)
    }
}

/// Bounded public module/re-export traversal. Missing paths stay Unknown.
fn public_names(krate: &Value, crate_name: &str) -> BTreeMap<String, String> {
    fn visit(
        k: &Value,
        id: &str,
        prefix: &str,
        stack: &mut Vec<String>,
        out: &mut BTreeMap<String, String>,
    ) {
        if stack.len() > 32 || stack.iter().any(|s| s == id) {
            return;
        }
        stack.push(id.into());
        for child in k["index"][id]["inner"]["module"]["items"]
            .as_array()
            .into_iter()
            .flatten()
        {
            let key = child.to_string();
            let item = &k["index"][&key];
            if item["visibility"] != "public" {
                continue;
            }
            if let Some(u) = item["inner"].get("use") {
                if u["id"].is_null() {
                    continue;
                }
                let target = u["id"].to_string();
                let path = if u["is_glob"] == true {
                    prefix.into()
                } else {
                    format!("{prefix}::{}", u["name"].as_str().unwrap_or("?"))
                };
                if k["index"][&target]["inner"].get("module").is_some() {
                    visit(k, &target, &path, stack, out);
                } else if u["is_glob"] != true {
                    out.entry(target)
                        .and_modify(|p| {
                            if path < *p {
                                *p = path.clone();
                            }
                        })
                        .or_insert(path);
                }
            } else {
                let Some(name) = item["name"].as_str() else {
                    continue;
                };
                let path = format!("{prefix}::{name}");
                out.entry(key.clone())
                    .and_modify(|p| {
                        if path < *p {
                            *p = path.clone();
                        }
                    })
                    .or_insert(path.clone());
                if item["inner"].get("module").is_some() {
                    visit(k, &key, &path, stack, out);
                }
            }
        }
        stack.pop();
    }
    let mut out = BTreeMap::new();
    visit(
        krate,
        &krate["root"].to_string(),
        crate_name,
        &mut vec![],
        &mut out,
    );
    out
}

pub fn import(bytes: &[u8]) -> Result<Catalog, String> {
    let document: Value = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
    let krate = document.pointer("/parser/krate").unwrap_or(&document);
    if !krate["index"].is_object() {
        return Err("expected raw rustdoc or APIDoc with embedded parser.krate".into());
    }
    // Structural reading avoids deserializing attributes but requires the known schema.
    if krate["format_version"] != 57 {
        return Err(format!(
            "unsupported rustdoc format {}; expected 57",
            krate["format_version"]
        ));
    }
    let root = krate["root"].to_string();
    let crate_name = krate["index"][&root]["name"]
        .as_str()
        .ok_or("missing crate name")?
        .to_string();
    let crate_version = krate["crate_version"]
        .as_str()
        .ok_or("crate_version missing: build docs with a pinned package version")?
        .to_string();
    let raw = serde_json::to_vec(krate).map_err(|e| e.to_string())?;
    let provenance = sha256(&raw);
    let im = Import {
        krate,
        provenance: provenance.clone(),
        public_names: public_names(krate, &crate_name),
        type_paths:RefCell::new(BTreeMap::new()),
    };
    let index = krate["index"].as_object().unwrap();
    let mut owners = BTreeMap::new();
    let mut impls = vec![];
    let mut ids: Vec<_> = index.keys().collect();
    ids.sort();
    for id in &ids {
        let Some(imp) = index[*id]["inner"].get("impl") else {
            continue;
        };
        for child in imp["items"].as_array().into_iter().flatten() {
            owners.insert(child.to_string(), (id.to_string(), imp));
        }
        if imp["trait"].is_null() {
            continue;
        }
        let scope = format!("impl:{id}");
        let (_, obligations, mut unsupported) = im.generics(&imp["generics"], &scope);
        if imp["is_negative"] == true || imp["is_synthetic"] == true || imp["is_unsafe"] == true {
            unsupported.push("negative/synthetic/unsafe trait impl".into());
        }
        impls.push(TraitImpl {
            id: id.to_string(),
            for_ty: im.ty(&imp["for"], &scope),
            trait_ty: im.path(&imp["trait"], &scope),
            obligations,
            unsupported,
        });
    }
    let selected: Option<BTreeSet<String>> = document["apis"]
        .as_array()
        .map(|a| a.iter().map(|v| v["id"].to_string()).collect());
    let mut apis = vec![];
    for id in ids {
        let item = &index[id];
        let Some(fun) = item["inner"].get("function") else {
            continue;
        };
        if item["crate_id"] != 0 {
            continue;
        }
        if let Some(s) = &selected {
            if !s.contains(id) {
                continue;
            }
        } else if item["visibility"] != "public" {
            continue;
        }
        let scope = format!("api:{id}");
        let (mut generics, mut obligations, mut unsupported) =
            im.generics(&fun["generics"], &scope);
        let function_generics = generics.clone();
        let mut owner = None;
        let path;
        if let Some((_, imp)) = owners.get(id) {
            if !imp["trait"].is_null() {
                unsupported.push("trait method dispatch/impl specialization".into());
            }
            let (gv, obs, us) = im.generics(&imp["generics"], &scope);
            if gv.iter().any(|v| generics.contains(v)) {
                unsupported.push("shadowed impl/function generic identity".into());
            }
            generics.extend(gv);
            obligations.extend(obs);
            unsupported.extend(us);
            owner = Some(im.ty(&imp["for"], &scope));
            path = item["name"].as_str().unwrap_or("?").to_string();
        } else {
            path = im.public_names.get(id).cloned().unwrap_or_default();
            if path.is_empty() {
                unsupported.push("no canonical callable path".into());
            }
        }
        let replace_self = |t: Ty| {
            t.map(&|v| {
                if v.name == "Self" {
                    owner.clone().unwrap_or(Ty::Unknown {
                        reason: "unresolved Self".into(),
                    })
                } else {
                    Ty::Var { var: v.clone() }
                }
            })
        };
        let inputs = fun["sig"]["inputs"]
            .as_array()
            .ok_or("missing function inputs")?
            .iter()
            .map(|a| replace_self(im.ty(&a[1], &scope)))
            .collect::<Vec<_>>();
        let output = if fun["sig"]["output"].is_null() {
            Ty::unit()
        } else {
            replace_self(im.ty(&fun["sig"]["output"], &scope))
        };
        for o in &mut obligations {
            o.ty = replace_self(o.ty.clone());
            o.trait_ty = replace_self(o.trait_ty.clone());
        }
        if fun["header"]["is_unsafe"] == true {
            unsupported.push("direct unsafe call: preconditions require explicit contract".into());
        }
        if fun["header"]["is_async"] == true || fun["sig"]["is_c_variadic"] == true {
            unsupported.push("async/variadic function".into());
        }
        if fun["header"]["abi"] != "Rust" {
            unsupported.push("non-Rust ABI".into());
        }
        for t in inputs.iter().chain(std::iter::once(&output)) {
            if let Some(r) = t.unknown() {
                unsupported.push(r);
            }
        }
        if output.contains_ref() {
            unsupported.push("borrowed output requires lifetime analysis".into());
        }
        unsupported.sort();
        unsupported.dedup();
        apis.push(Api {
            id: id.to_string(),
            path,
            owner: owner.clone(),
            generics,
            function_generics,
            inputs,
            output,
            obligations,
            unsupported,
            signature: Some(TargetSignature {
                argument_count:fun["sig"]["inputs"].as_array().unwrap().len(),
                receiver_argument_index:fun["sig"]["inputs"].as_array().unwrap().iter().position(|a|a[0]=="self"),
                kind:if fun["sig"]["inputs"].as_array().unwrap().iter().any(|a|a[0]=="self") {"method"}
                    else if owner.is_some() {"associated"}else{"free"}.into(),
                evidence:im.provenance.clone(),
            }),
        });
    }
    Ok(Catalog {
        schema: "graft-catalog-v1".into(),
        package_name: crate_name.clone(),
        features: vec![],
        default_features: true,
        crate_name,
        crate_version,
        rustdoc_sha256: provenance,
        apis,
        impls,
        type_paths:im.type_paths.into_inner().into_values().collect(),
    })
}
