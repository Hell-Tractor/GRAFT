use super::model::*;

pub fn rust_type(t: &Ty) -> Result<String, Failure> {
    Ok(match t {
        Ty::Primitive { name } => name.clone(),
        Ty::Nominal { path, args, .. } => {
            if args.is_empty() {
                path.clone()
            } else {
                format!(
                    "{}<{}>",
                    path,
                    args.iter()
                        .map(rust_type)
                        .collect::<Result<Vec<_>, _>>()?
                        .join(", ")
                )
            }
        }
        Ty::Ref { mutable, inner } => format!(
            "&{}{}",
            if *mutable { "mut " } else { "" },
            rust_type(inner)?
        ),
        Ty::Slice { inner } => format!("[{}]", rust_type(inner)?),
        Ty::Array { inner, len } => format!("[{}; {len}]", rust_type(inner)?),
        Ty::Tuple { items } => format!(
            "({})",
            items
                .iter()
                .map(|t| rust_type(t).map(|s| format!("{s},")))
                .collect::<Result<Vec<_>, _>>()?
                .join(" ")
        ),
        Ty::Unknown { reason } => return Err(Failure::unknown(reason)),
        Ty::Var { .. } => return Err(Failure::unknown("uninstantiated variable in emitter")),
    })
}
pub fn input_supported(t: &Ty) -> bool {
    matches!(t,Ty::Primitive { name } if matches!(name.as_str(),"u8"|"i8"|"u16"|"i16"|"u32"|"i32"|"u64"|"i64"|"u128"|"i128"|"usize"|"isize"|"bool"|"char"|"f32"|"f64"))
}
fn binding(b: &Binding) -> String {
    format!(
        "{}v{}",
        match b.adapter {
            Adapter::Move => "",
            Adapter::SharedBorrow => "&",
            Adapter::MutableBorrow => "&mut ",
            Adapter::SharedSlice => "&",
            Adapter::MutableSlice => "&mut ",
        },
        b.value
    )
}

/// Deterministic core; compiler validates the remaining Rust obligations.
pub fn emit(plan: &SequencePlan) -> Result<String, Failure> {
    if !plan.unresolved.is_empty() {
        return Err(Failure::unknown("plan has unresolved obligations"));
    }
    let mut s = String::from(
        "extern crate alloc;\n#[allow(unused_mut, unused_variables)]\npub fn graft_body(data: &[u8]) -> Vec<(usize, Vec<u8>)> {\nlet mut trace = Vec::new();\n",
    );
    let mut cursor = 0;
    for (position, step) in plan.steps.iter().enumerate() {
        if step.value() != position {
            return Err(Failure::incompatible("non-topological/duplicate value id"));
        }
        let v = step.value();
        let ty = rust_type(step.ty())?;
        match step {
            Step::Input { ty: t, .. } => {
                if !input_supported(t) {
                    return Err(Failure::unknown("input decoder unsupported"));
                }
                let expr = match ty.as_str() {
                    "bool" => format!(
                        "data.get({cursor} % data.len().max(1)).copied().unwrap_or(0) & 1 != 0"
                    ),
                    "char" => format!(
                        "data.get({cursor} % data.len().max(1)).copied().unwrap_or(0) as char"
                    ),
                    _ => format!(
                        "{ty}::from_le_bytes(core::array::from_fn(|i| data.get(({cursor} + i) % data.len().max(1)).copied().unwrap_or(0)))"
                    ),
                };
                s.push_str(&format!("let mut v{v}: {ty} = {expr};\n"));
                cursor += 16;
            }
            Step::Construct {
                constructor, args, ..
            } => {
                let es = args.iter().map(binding).collect::<Vec<_>>();
                let expr = match constructor.as_str() {
                    "tuple" => format!(
                        "({})",
                        es.iter()
                            .map(|e| format!("{e},"))
                            .collect::<Vec<_>>()
                            .join(" ")
                    ),
                    "array" => format!("[{}]", es.join(", ")),
                    "vec" => format!("alloc::vec![{}]", es.join(", ")),
                    "box" if es.len() == 1 => format!("alloc::boxed::Box::new({})", es[0]),
                    "some" if es.len() == 1 => format!("Some({})", es[0]),
                    _ => return Err(Failure::unknown("unsupported constructor")),
                };
                s.push_str(&format!("let mut v{v}: {ty} = {expr};\n"));
            }
            Step::Call {
                instance,
                path,
                type_args,
                args,
                ..
            } => {
                for b in args {
                    if b.value >= position {
                        return Err(Failure::incompatible(
                            "call dependency must precede consumer",
                        ));
                    }
                }
                let generics = if type_args.is_empty() {
                    String::new()
                } else {
                    format!(
                        "::<{}>",
                        type_args
                            .iter()
                            .map(rust_type)
                            .collect::<Result<Vec<_>, _>>()?
                            .join(", ")
                    )
                };
                // Observe direct scalar arguments before moves. This does not claim opaque-value taint.
                let observed = args
                    .iter()
                    .filter(|b| input_supported(plan.steps[b.value].ty()))
                    .map(|b| format!("v{},", b.value))
                    .collect::<Vec<_>>()
                    .join(" ");
                s.push_str(&format!(
                    "let observed_{instance} = format!(\"{{:?}}\", ({observed})).into_bytes();\n"
                ));
                s.push_str(&format!("let mut v{v}: {ty} = {path}{generics}({});\nstd::hint::black_box(&v{v});\nif std::env::var_os(\"GRAFT_TRACE\").is_some() {{ eprintln!(\"GRAFT_CALL:{instance}:{{}}\", String::from_utf8_lossy(&observed_{instance})); }}\ntrace.push(({instance}, observed_{instance}));\n",args.iter().map(binding).collect::<Vec<_>>().join(", ")));
            }
            Step::Extract {
                source, variant, ..
            } => {
                if *source >= position || !matches!(variant.as_str(), "Some" | "Ok") {
                    return Err(Failure::incompatible("invalid branch extraction"));
                }
                s.push_str(&format!(
                    "let {variant}(mut v{v}) = v{source} else {{ return trace; }};\n"
                ));
            }
        }
    }
    s.push_str("trace\n}\n");
    Ok(s)
}
pub fn standalone(plan: &SequencePlan) -> Result<String, Failure> {
    Ok(format!(
        "{}\nfn main() {{ let data: Vec<u8> = std::env::args().skip(1).map(|s| s.parse::<u8>().expect(\"byte argument\")).collect(); println!(\"{{:?}}\", graft_body(&data)); }}\n",
        emit(plan)?
    ))
}

/// Explicit exact dependency, never a floating cargo-add lookup.
pub fn manifest(plan: &SequencePlan) -> Result<String, Failure> {
    if plan.crate_version.is_empty() {
        return Err(Failure::unknown("missing pinned crate version"));
    }
    // JSON strings are valid TOML basic string literals for these package identifiers.
    let q = |s: &str| serde_json::to_string(s).unwrap();
    Ok(format!(
        "[package]\nname = \"graft_checked_target\"\nversion = \"0.0.0\"\nedition = \"2024\"\n\n[dependencies]\n{} = {{ package = {}, version = {}, default-features = {}, features = [{}] }}\n",
        q(&plan.crate_name),
        q(&plan.package_name),
        q(&format!("={}", plan.crate_version)),
        plan.default_features,
        plan.features
            .iter()
            .map(|s| q(s))
            .collect::<Vec<_>>()
            .join(", ")
    ))
}

/// Keep the common project identity/dependency graph so Cargo uses its exact lock.
/// Reject a mismatched direct pin before making a paid generation request.
pub fn shared_manifest(plan: &SequencePlan, source: &str) -> Result<String, Failure> {
    let config = config::Config::builder()
        .add_source(config::File::from_str(source, config::FileFormat::Toml))
        .build().map_err(|e| Failure::unknown(format!("invalid shared manifest: {e}")))?;
    let value: serde_json::Value = config.try_deserialize()
        .map_err(|e| Failure::unknown(format!("invalid shared manifest: {e}")))?;
    let deps = value.get("dependencies")
        .ok_or_else(|| Failure::unknown("shared manifest missing target dependency"))?;
    let key = if deps.get(&plan.crate_name).is_some() { &plan.crate_name } else { &plan.package_name };
    let dep = deps.get(key).ok_or_else(|| Failure::unknown("shared manifest missing target dependency"))?;
    if key.replace('-', "_") != plan.crate_name.replace('-', "_") {
        return Err(Failure::unknown("shared dependency alias differs from plan crate name"));
    }
    let version = dep.as_str().or_else(|| dep.get("version").and_then(|v| v.as_str()));
    let package = dep.get("package").and_then(|v| v.as_str()).unwrap_or(key);
    let defaults = dep.get("default-features").and_then(|v| v.as_bool()).unwrap_or(true);
    let mut features: Vec<String> = dep.get("features").and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect()).unwrap_or_default();
    features.sort();
    let mut expected = plan.features.clone();
    expected.sort();
    if version != Some(format!("={}", plan.crate_version).as_str())
        || package != plan.package_name || defaults != plan.default_features || features != expected {
        return Err(Failure::unknown("shared manifest differs from plan's exact version/package/features"));
    }
    if value.get("bin").is_some() {
        return Err(Failure::unknown("shared manifest already declares binaries"));
    }
    Ok(format!("{source}\n[[bin]]\nname = \"graft_checked_target\"\npath = \"src/main.rs\"\n"))
}
