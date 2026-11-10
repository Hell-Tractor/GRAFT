//! Independent visibility cross-check; never resolves generic impl obligations.
use std::{collections::BTreeMap, error::Error, fs, path::Path};

use rustdoc_types::{Crate, Id, ItemEnum, Type, Visibility};
use serde_json::{Value, json};
use trustfall_rustdoc_adapter::{IndexedCrate, rust_type_name};

fn named(id: &Id, krate: &Crate, indexed: &IndexedCrate<'_>) -> Option<bool> {
    if let Some(item) = krate.index.get(id) {
        if item.crate_id == krate.index[&krate.root].crate_id {
            return Some(!indexed.publicly_importable_names(&item.id).is_empty());
        }
        return Some(true);
    }
    krate
        .paths
        .get(id)
        .map(|path| path.crate_id != krate.index[&krate.root].crate_id)
}

fn receiver_named(ty: &Type, krate: &Crate, indexed: &IndexedCrate<'_>) -> Option<bool> {
    match ty {
        Type::ResolvedPath(path) => named(&path.id, krate, indexed),
        Type::BorrowedRef { type_, .. }
        | Type::RawPointer { type_, .. }
        | Type::Slice(type_)
        | Type::Array { type_, .. } => receiver_named(type_, krate, indexed),
        Type::Primitive(_) => Some(true),
        _ => None,
    }
}

fn audit(krate: &Crate) -> Value {
    let indexed = IndexedCrate::new(krate);
    let mut owners: BTreeMap<u32, Vec<Value>> = BTreeMap::new();
    for item in krate.index.values() {
        match &item.inner {
            ItemEnum::Impl(implementation) => {
                let owner = json!({
                    "kind": "impl", "id": item.id.0,
                    "receiver": rust_type_name(&implementation.for_),
                    "receiver_publicly_named": receiver_named(&implementation.for_, krate, &indexed),
                    "trait_publicly_named": implementation.trait_.as_ref().and_then(|path| named(&path.id, krate, &indexed)),
                    "trait": implementation.trait_.as_ref().map(|path| path.path.as_str()),
                    "generics": implementation.generics,
                    "is_negative": implementation.is_negative,
                    "is_synthetic": implementation.is_synthetic,
                    "blanket_impl": implementation.blanket_impl,
                });
                for id in &implementation.items {
                    owners.entry(id.0).or_default().push(owner.clone());
                }
            }
            ItemEnum::Trait(trait_) => {
                let owner = json!({"kind": "trait_declaration", "id": item.id.0,
                    "trait_publicly_named": named(&item.id, krate, &indexed)});
                for id in &trait_.items {
                    owners.entry(id.0).or_default().push(owner.clone());
                }
            }
            _ => {}
        }
    }
    let root = &krate.index[&krate.root];
    let mut functions = Vec::new();
    for item in krate.index.values() {
        let ItemEnum::Function(function) = &item.inner else {
            continue;
        };
        let mut own = owners.get(&item.id.0).cloned().unwrap_or_default();
        own.sort_by_key(|owner| owner["id"].as_u64().unwrap_or_default());
        let mut paths = indexed.publicly_importable_names(&item.id);
        paths.sort();
        let status = if item.crate_id != root.crate_id {
            "external_definition"
        } else if own.iter().any(|owner| {
            owner["kind"] == "trait_declaration" && owner["trait_publicly_named"] == true
        }) {
            "public_trait_declaration"
        } else if !paths.is_empty() {
            "publicly_named_function"
        } else if own.iter().any(|owner| {
            owner["kind"] == "impl"
                && owner["trait"].is_null()
                && owner["receiver_publicly_named"] == true
        }) && item.visibility == Visibility::Public
        {
            "method_on_publicly_named_receiver"
        } else if own.iter().any(|owner| {
            owner["kind"] == "impl"
                && owner["trait_publicly_named"] == true
                && owner["receiver_publicly_named"] == true
        }) {
            "trait_method_on_publicly_named_receiver"
        } else if own.iter().any(|owner| {
            owner["kind"] == "impl"
                && !owner["trait"].is_null()
                && owner["trait_publicly_named"] == true
        }) {
            "trait_impl_requires_applicability_or_opaque_receiver_review"
        } else if own
            .iter()
            .any(|owner| owner["kind"] == "impl" && owner["trait"].is_null())
            && item.visibility != Visibility::Public
        {
            "private_inherent_method"
        } else if own.is_empty() {
            "not_publicly_named_function"
        } else {
            "owner_visibility_or_applicability_unresolved"
        };
        functions.push(
            json!({"rustdoc_id": item.id.0.to_string(), "name": item.name,
            "status": status, "has_body": function.has_body, "span": item.span,
            "importable_path_count": paths.len(), "importable_paths_debug": format!("{paths:?}"),
            "owners": own}),
        );
    }
    functions.sort_by_key(|entry| entry["rustdoc_id"].as_str().unwrap().to_owned());
    json!({"schema": "graft-public-surface-crosscheck-v1", "crate": root.name,
        "version": krate.crate_version, "rustdoc_format": krate.format_version,
        "policy": "publicly importable paths from existing rustdoc adapter; trait impl obligations remain unresolved",
        "functions": functions})
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 2 {
        return Err("usage: audit_public_surface INPUT_RUSTDOC_OR_APIDOC_JSON OUTPUT_JSON".into());
    }
    let document: Value = serde_json::from_slice(&fs::read(&args[0])?)?;
    let raw = document
        .get("parser")
        .and_then(|value| value.get("krate"))
        .unwrap_or(&document);
    let krate: Crate = serde_json::from_value(raw.clone())?;
    let mut report = audit(&krate);
    report["input_path"] = json!(Path::new(&args[0]).display().to_string());
    fs::write(&args[1], serde_json::to_vec_pretty(&report)?)?;
    Ok(())
}
