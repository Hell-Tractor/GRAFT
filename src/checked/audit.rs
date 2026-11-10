use super::{
    model::*,
    solver::{Match, match_type},
};
use serde_json::{Value, json};
use std::collections::BTreeMap;
pub fn inventory(c: &Catalog, edges: bool) -> Value {
    let mut reasons = BTreeMap::<String, usize>::new();
    for a in &c.apis {
        for r in &a.unsupported {
            *reasons.entry(r.clone()).or_default() += 1;
        }
    }
    let mut result = json!({"crate":c.crate_name,"version":c.crate_version,"rustdoc_sha256":c.rustdoc_sha256,"api_count":c.apis.len(),"no_import_blocker":c.apis.iter().filter(|a|a.unsupported.is_empty()).count(),"apis_with_obligations":c.apis.iter().filter(|a|!a.obligations.is_empty()).count(),"unsupported_api_reasons":reasons,"scope":"import support only, not proven constructibility; trait obligations may still be unknown"});
    if edges {
        let mut counts = BTreeMap::<String, usize>::new();
        for source in &c.apis {
            let mut out = source.output.clone();
            if let Ty::Nominal { path, args, .. } = &out {
                if (path == "core::option::Option" || path == "core::result::Result")
                    && !args.is_empty()
                {
                    out = args[0].clone();
                }
            }
            if out == Ty::unit() || matches!(out, Ty::Primitive { .. }) {
                continue;
            }
            let out = out.map(&|v| Ty::var(&format!("{}/edge-source", v.scope), &v.name));
            for target in &c.apis {
                for input in &target.inputs {
                    let input =
                        input.map(&|v| Ty::var(&format!("{}/edge-target", v.scope), &v.name));
                    let status = match match_type(&out, &input, &mut Substitution::new(), &[]) {
                        Match::Incompatible { .. } => "incompatible",
                        Match::Unknown { .. } => "unknown_type_form",
                        Match::Compatible { .. }
                            if !source.unsupported.is_empty() || !target.unsupported.is_empty() =>
                        {
                            "unknown_api_feature"
                        }
                        Match::Compatible { .. }
                            if !source.obligations.is_empty() || !target.obligations.is_empty() =>
                        {
                            "unified_pending_trait_obligations"
                        }
                        Match::Compatible { .. } => "unified_candidate_not_compiler_verified",
                    };
                    *counts.entry(status.into()).or_default() += 1;
                }
            }
        }
        result["candidate_parameter_pairs"] = json!(counts);
    }
    result
}
