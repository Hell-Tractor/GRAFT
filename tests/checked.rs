use graft::checked::{
    planner::{Limits, plan},
    solver::*,
    *,
};
use std::{collections::BTreeMap, path::PathBuf, process::Command, sync::OnceLock};

fn nominal(path: &str, args: Vec<Ty>) -> Ty {
    Ty::Nominal {
        identity: path.into(),
        path: path.into(),
        args,
    }
}
fn catalog() -> Catalog {
    static C: OnceLock<Catalog> = OnceLock::new();
    C.get_or_init(|| {
        let dir = PathBuf::from(format!("/tmp/graft-checked-tests-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let fixture =
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/checked_fixture.rs");
        let out = Command::new("rustdoc")
            .args([
                "+nightly",
                "--crate-name",
                "checked_fixture",
                "--crate-version",
                "0.1.0",
                "--edition=2024",
                "-Z",
                "unstable-options",
                "--output-format",
                "json",
                "-o",
            ])
            .arg(&dir)
            .arg(&fixture)
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        rustdoc::import(&std::fs::read(dir.join("checked_fixture.json")).unwrap()).unwrap()
    })
    .clone()
}
fn target(c: &Catalog, name: &str) -> String {
    c.apis
        .iter()
        .find(|a| a.path == format!("checked_fixture::{name}"))
        .unwrap()
        .id
        .clone()
}
fn planned(c: &Catalog, name: &str) -> SequencePlan {
    match plan(c, &target(c, name), &Limits::default()) {
        PlanResult::Planned { plan } => plan,
        x => panic!("{name}: {x:?}"),
    }
}
fn compile(source: &str, name: &str, success: bool) -> PathBuf {
    let dir = PathBuf::from(format!("/tmp/graft-checked-tests-{}", std::process::id())).join(name);
    std::fs::create_dir_all(&dir).unwrap();
    let fixture =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/checked_fixture.rs");
    let lib = dir.join("libchecked_fixture.rlib");
    assert!(
        Command::new("rustc")
            .args([
                "--crate-type",
                "lib",
                "--crate-name",
                "checked_fixture",
                "--edition=2024"
            ])
            .arg(fixture)
            .arg("-o")
            .arg(&lib)
            .status()
            .unwrap()
            .success()
    );
    let src = dir.join("main.rs");
    std::fs::write(&src, source).unwrap();
    let bin = dir.join("target");
    let out = Command::new("rustc")
        .arg("--edition=2024")
        .arg(src)
        .arg("--extern")
        .arg(format!("checked_fixture={}", lib.display()))
        .arg("-o")
        .arg(&bin)
        .output()
        .unwrap();
    std::fs::write(dir.join("compiler.stderr"), &out.stderr).unwrap();
    std::fs::write(dir.join("compiler.json"),serde_json::to_vec_pretty(&serde_json::json!({"expected_compile":success,"actual_compile":out.status.success(),"exit_code":out.status.code(),"source_sha256":rustdoc::sha256(source.as_bytes())})).unwrap()).unwrap();
    assert_eq!(
        out.status.success(),
        success,
        "{name}: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    bin
}
fn run(bin: &PathBuf, byte: &str) -> std::process::Output {
    let o = Command::new(bin).arg(byte).output().unwrap();
    std::fs::write(bin.parent().unwrap().join(format!("runtime-{byte}.json")),serde_json::to_vec_pretty(&serde_json::json!({"input_byte":byte,"exit_code":o.status.code(),"stdout":String::from_utf8_lossy(&o.stdout),"stderr":String::from_utf8_lossy(&o.stderr)})).unwrap()).unwrap();
    o
}
#[test]
fn scoped_unification_and_transaction() {
    let mut env = BTreeMap::new();
    let t = Ty::var("combine@1", "T");
    assert!(matches!(
        match_type(
            &nominal("Vec", vec![Ty::primitive("u8")]),
            &nominal("Vec", vec![t.clone()]),
            &mut env,
            &[]
        ),
        Match::Compatible { .. }
    ));
    let before = env.clone();
    assert!(
        unify(
            &nominal("Buffer", vec![Ty::primitive("i32")]),
            &nominal("Buffer", vec![t]),
            &mut env
        )
        .is_err()
    );
    assert_eq!(env, before);
    assert!(unify(&Ty::var("make@2", "T"), &Ty::primitive("i32"), &mut env).is_ok());
    assert!(unify(&Ty::var("make@3", "T"), &Ty::primitive("u8"), &mut env).is_ok());
}

#[test]
fn common_manifest_preserves_identity_and_rejects_pin_feature_changes() {
    let mut p = planned(&catalog(), "combine");
    p.crate_name = "unicode_segmentation".into();
    p.package_name = "unicode-segmentation".into();
    p.crate_version = "1.10.1".into();
    p.features = vec![];
    p.default_features = true;
    let shared = "[package]\nname=\"shared_graph\"\nversion=\"0.0.0\"\n[dependencies]\nunicode-segmentation=\"=1.10.1\"\n";
    let rendered = emit::shared_manifest(&p, shared).unwrap();
    assert!(rendered.starts_with(shared));
    assert!(rendered.contains("name = \"graft_checked_target\""));
    assert!(emit::shared_manifest(&p, &shared.replace("=1.10.1", "=1.12.0")).is_err());
    assert!(emit::shared_manifest(&p, &shared.replace("=1.10.1", "1.10.1")).is_err());
    p.default_features = false;
    assert!(emit::shared_manifest(&p, shared).is_err());
    p.default_features = true;
    p.features = vec!["extra".into()];
    assert!(emit::shared_manifest(&p, shared).is_err());
}

#[test]
fn independent_argument_ablation_produces_a_real_compiler_rejection() {
    let c=catalog();
    let result=plan(&c,&target(&c,"combine"),&Limits{independent_arguments:true,..Limits::default()});
    let PlanResult::Planned{plan:p}=result else {panic!("ablation must expose candidate, not hide conflict");};
    assert_eq!(p.schema,"graft-ablation-independent-arguments-v1");
    compile(&emit::standalone(&p).unwrap(),"independent_argument_ablation",false);
}
#[test]
fn occurs_check() {
    let t = Ty::var("x", "T");
    assert!(unify(&t, &nominal("Vec", vec![t.clone()]), &mut BTreeMap::new()).is_err());
}
#[test]
fn primitives_are_not_wildcards() {
    assert!(
        unify(
            &Ty::primitive("u8"),
            &Ty::primitive("i32"),
            &mut BTreeMap::new()
        )
        .is_err()
    );
}
#[test]
fn wrappers_are_not_equal() {
    assert!(
        unify(
            &Ty::primitive("u8"),
            &Ty::Array {
                inner: Box::new(Ty::primitive("u8")),
                len: 4
            },
            &mut BTreeMap::new()
        )
        .is_err()
    );
}
#[test]
fn unknown_is_explicit() {
    assert!(matches!(
        match_type(
            &Ty::Unknown {
                reason: "projection".into()
            },
            &Ty::primitive("u8"),
            &mut BTreeMap::new(),
            &[]
        ),
        Match::Unknown { .. }
    ));
}
#[test]
fn conflicting_generics_rejected() {
    let mut c = catalog();
    c.apis
        .retain(|a| !a.path.ends_with("::make") && !a.path.ends_with("::right_u8"));
    assert!(matches!(
        plan(&c, &target(&c, "same"), &Limits::default()),
        PlanResult::Rejected { .. }
    ));
    compile(
        "fn main(){checked_fixture::same(checked_fixture::left_u8(1),checked_fixture::right_i32(2));}",
        "conflicting",
        false,
    );
}
#[test]
fn consistent_generics_compile() {
    let c = catalog();
    let p = planned(&c, "same");
    compile(&emit::standalone(&p).unwrap(), "consistent", true);
}
#[test]
fn review_combine_counterexample_replans() {
    let p = planned(&catalog(), "combine");
    let Step::Call { type_args, .. } = p.steps.last().unwrap() else {
        panic!()
    };
    assert_eq!(type_args, &vec![Ty::primitive("i32")]);
    compile(&emit::standalone(&p).unwrap(), "combine-replanned", true);
    compile(
        "fn main(){checked_fixture::combine(vec![1u8],checked_fixture::buffer_i32(2));}",
        "combine-conflict",
        false,
    );
}
#[test]
fn independent_instances_compile() {
    let mut c = catalog();
    c.apis.retain(|a| !a.path.ends_with("::left_u8"));
    let p = planned(&c, "independent");
    let calls: Vec<_> = p
        .steps
        .iter()
        .filter_map(|s| {
            if let Step::Call {
                api,
                instance,
                type_args,
                ..
            } = s
            {
                if api == &target(&c, "make") {
                    Some((instance, type_args))
                } else {
                    None
                }
            } else {
                None
            }
        })
        .collect();
    assert_eq!(calls.len(), 2);
    assert_ne!(calls[0].0, calls[1].0);
    assert_ne!(calls[0].1, calls[1].1);
    compile(&emit::standalone(&p).unwrap(), "independent", true);
}
#[test]
fn nested_containers_compile() {
    compile(
        &emit::standalone(&planned(&catalog(), "nested")).unwrap(),
        "nested",
        true,
    );
}
#[test]
fn conditional_impl_proven() {
    let c = catalog();
    let p = planned(&c, "bounded");
    assert!(!p.obligations.is_empty());
    compile(&emit::standalone(&p).unwrap(), "conditional", true);
}
#[test]
fn conditional_impl_wrong_type_not_proven() {
    let c = catalog();
    let a = c
        .apis
        .iter()
        .find(|a| a.id == target(&c, "bounded"))
        .unwrap();
    let mut env = Substitution::new();
    for v in &a.generics {
        env.insert(v.clone(), Ty::primitive("i32"));
    }
    let o = &a.obligations[0];
    assert!(
        prove(
            &Obligation {
                ty: resolve(&o.ty, &env),
                trait_ty: resolve(&o.trait_ty, &env)
            },
            &c.impls,
            &mut vec![],
            10
        )
        .is_err()
    );
    compile(
        "fn main(){checked_fixture::needs_tag(1i32);}",
        "bad-bound",
        false,
    );
}
#[test]
fn distinct_trait_identity() {
    let c = catalog();
    assert!(matches!(
        plan(&c, &target(&c, "other_tag"), &Limits::default()),
        PlanResult::Rejected { .. }
    ));
}
#[test]
fn unsupported_predicates_never_drop() {
    let c = catalog();
    for name in [
        "projection",
        "associated_binding",
        "const_array",
        "higher",
        "raw",
        "unsafe_api",
        "borrowed",
        "held_ref",
    ] {
        let id = target(&c, name);
        let a = c.apis.iter().find(|a| a.id == id).unwrap();
        assert!(!a.unsupported.is_empty(), "{name}");
        assert!(
            matches!(plan(&c,&id,&Limits::default()),PlanResult::Rejected{reasons,..}if reasons.iter().any(|r|matches!(r,Failure::Unknown{..}))),
            "{name}"
        );
    }
}
#[test]
fn moved_values_are_not_reused() {
    let p = planned(&catalog(), "moved");
    let Step::Call { args, .. } = p.steps.last().unwrap() else {
        panic!()
    };
    assert_ne!(args[0].value, args[1].value);
    compile(&emit::standalone(&p).unwrap(), "moved", true);
    compile(
        "fn main(){let t=checked_fixture::token(1);checked_fixture::moved(t,t);}",
        "double-move",
        false,
    );
}
#[test]
fn borrow_conflicts_get_fresh_values() {
    let p = planned(&catalog(), "mutable");
    let Step::Call { args, .. } = p.steps.last().unwrap() else {
        panic!()
    };
    assert_ne!(args[0].value, args[1].value);
    compile(&emit::standalone(&p).unwrap(), "mutable", true);
    compile(
        "fn main(){let mut t=checked_fixture::token(1);checked_fixture::mutable(&mut t,&mut t);}",
        "alias",
        false,
    );
}
#[test]
fn shared_borrows_reuse_values() {
    let p = planned(&catalog(), "shared");
    let Step::Call { args, .. } = p.steps.last().unwrap() else {
        panic!()
    };
    assert_eq!(args[0].value, args[1].value);
    compile(&emit::standalone(&p).unwrap(), "shared", true);
}
#[test]
fn slices_and_arrays_compile() {
    for name in ["slice", "array"] {
        let p = planned(&catalog(), name);
        compile(&emit::standalone(&p).unwrap(), name, true);
    }
}
#[test]
fn success_branches_and_failure_exit() {
    for variant in ["result", "option"] {
        let mut c = catalog();
        let keep = target(&c, variant);
        let consumer = target(&c, "use_token");
        c.apis.retain(|a| a.id == keep || a.id == consumer);
        let p = planned(&c, "use_token");
        assert!(p.steps.iter().any(|s| matches!(s, Step::Extract { .. })));
        let bin = compile(&emit::standalone(&p).unwrap(), variant, true);
        let a = run(&bin, "0");
        let b = run(&bin, "1");
        assert!(a.status.success() && b.status.success());
        assert_ne!(a.stdout, b.stdout);
    }
}
#[test]
fn rootless_cycle_and_budget() {
    let c = catalog();
    assert!(
        matches!(plan(&c,&target(&c,"use_cycle"),&Limits::default()),PlanResult::Rejected{reasons,..}if reasons.iter().any(|r|matches!(r,Failure::Cycle{..})))
    );
    assert!(
        matches!(plan(&c,&target(&c,"scalar"),&Limits{nodes:0,..Limits::default()}),PlanResult::Rejected{reasons,..}if reasons.contains(&Failure::BudgetExhausted))
    );
}
#[test]
fn ast_gate_rejects_compiling_deletion_and_constant_input() {
    let p = planned(&catalog(), "scalar");
    let code = emit::standalone(&p).unwrap();
    assert!(validate::retain_plan(&p, &code).is_ok());
    assert!(
        validate::retain_plan(&p, &code.replace("checked_fixture::scalar(v0)", "0u8")).is_err()
    );
    assert!(validate::retain_plan(&p, &code.replace("data.get(", "&[0u8].get(")).is_err());
    compile(
        &code.replace("checked_fixture::scalar(v0)", "0u8"),
        "deleted-but-compiles",
        true,
    );
}
#[test]
fn runtime_reaches_target_and_scalar_input_influences_argument() {
    let p = planned(&catalog(), "scalar");
    let bin = compile(&emit::standalone(&p).unwrap(), "reaches", true);
    let a = run(&bin, "1");
    let b = run(&bin, "2");
    assert!(a.status.success() && b.status.success());
    assert_ne!(a.stdout, b.stdout);
    assert!(String::from_utf8(a.stdout).unwrap().starts_with("[(0,"));
}
#[test]
fn pinned_manifest() {
    let p = planned(&catalog(), "scalar");
    assert!(emit::manifest(&p).unwrap().contains("=0.1.0"));
}
#[test]
fn reserves_borrow_during_later_dependency_construction() {
    let p = planned(&catalog(), "hold_then_make");
    compile(&emit::standalone(&p).unwrap(), "reserved-loan", true);
}
#[test]
fn backtracks_cyclic_producer() {
    let mut c = catalog();
    c.apis.sort_by_key(|a| {
        if a.path.ends_with("::bad_choice") {
            0
        } else {
            1
        }
    });
    let p = planned(&c, "choose");
    assert!(
        p.steps
            .iter()
            .any(|s| matches!(s,Step::Call{api,..}if api==&target(&c,"good_choice")))
    );
    compile(&emit::standalone(&p).unwrap(), "backtrack", true);
}
struct Mock {
    responses: std::collections::VecDeque<String>,
    prompts: Vec<String>,
}
#[async_trait::async_trait]
impl validate::Backend for Mock {
    async fn respond(&mut self, prompt: &str) -> Result<validate::Response, String> {
        self.prompts.push(prompt.into());
        Ok(validate::Response {
            content: self.responses.pop_front().ok_or("no mock response")?,
            model: "offline-mock".into(),
            immutable_revision: Some("fixture-v1".into()),
            input_tokens: None,
            output_tokens: None,
            cost: Some(0.),
        })
    }
}
#[tokio::test]
async fn repair_includes_code_constraints_and_budget() {
    let p = planned(&catalog(), "scalar");
    let good = emit::standalone(&p).unwrap();
    let bad = good.replace("checked_fixture::scalar(v0)", "0u8");
    let mut mock = Mock {
        responses: [bad.clone(), good].into(),
        prompts: vec![],
    };
    let dir = PathBuf::from(format!(
        "/tmp/graft-checked-tests-{}/repair",
        std::process::id()
    ));
    let r = validate::generate(&p, &mut mock, &dir, 1, |code| {
        compile(code, "repair-build", true);
        Ok(())
    })
    .await
    .unwrap();
    assert!(r.accepted);
    assert_eq!(r.attempts.len(), 2);
    assert!(r.attempts[0].compiled && !r.attempts[0].plan_retained);
    assert!(mock.prompts[1].contains(&bad));
    assert!(mock.prompts[1].contains("Remaining repair calls: 0"));
    assert!(dir.join("attempt-0.prompt.txt").exists());
}
#[tokio::test]
async fn zero_repairs_means_one_initial_call() {
    let p = planned(&catalog(), "scalar");
    let mut mock = Mock {
        responses: ["fn main(){}".into()].into(),
        prompts: vec![],
    };
    let dir = PathBuf::from(format!(
        "/tmp/graft-checked-tests-{}/zero-repair",
        std::process::id()
    ));
    let r = validate::generate(&p, &mut mock, &dir, 0, |_| Ok(()))
        .await
        .unwrap();
    assert!(!r.accepted);
    assert_eq!(r.attempts.len(), 1);
}
#[tokio::test]
async fn backend_failure_is_recorded() {
    let p = planned(&catalog(), "scalar");
    let mut mock = Mock {
        responses: [].into(),
        prompts: vec![],
    };
    let dir = PathBuf::from(format!(
        "/tmp/graft-checked-tests-{}/backend-failure",
        std::process::id()
    ));
    let r = validate::generate(&p, &mut mock, &dir, 1, |_| Ok(()))
        .await
        .unwrap();
    assert!(!r.accepted && r.backend_error.is_some());
    assert!(dir.join("generation.json").exists());
}

#[test]
fn generic_inherent_methods_resolve_self_and_owner() {
    let c = catalog();
    let id = c
        .apis
        .iter()
        .find(|a| a.owner.is_some() && a.path == "visit")
        .unwrap()
        .id
        .clone();
    let PlanResult::Planned { plan } = plan(&c, &id, &Limits::default()) else {
        panic!("method plan rejected")
    };
    assert!(
        plan.steps
            .iter()
            .any(|s| matches!(s,Step::Call{path,..}if path.contains("Method<u8>")))
    );
    compile(&emit::standalone(&plan).unwrap(), "inherent-method", true);
}

#[tokio::test]
async fn runtime_probe_rejects_unused_core_and_constant_wrapper_input() {
    let p = planned(&catalog(), "scalar");
    let core = emit::emit(&p).unwrap();
    for (name, wrapper) in [
        ("unused-wrapper", "fn main() {}"),
        ("constant-wrapper", "fn main() {let _ = graft_body(&[1]);}"),
    ] {
        let source = format!("{core}\n{wrapper}\n");
        assert!(validate::retain_plan(&p, &source).is_ok());
        let bin = compile(&source, name, true);
        let r = validate::probe(&p, &bin, &bin.parent().unwrap().join("runtime.json"))
            .await
            .unwrap();
        assert!(!r.bounded_probe_passed);
    }
}
#[tokio::test]
async fn runtime_probe_accepts_actual_byte_wrapper() {
    let p = planned(&catalog(), "scalar");
    let bin = compile(&emit::standalone(&p).unwrap(), "valid-wrapper", true);
    let r = validate::probe(&p, &bin, &bin.parent().unwrap().join("runtime.json"))
        .await
        .unwrap();
    assert!(r.bounded_probe_passed);
    assert_eq!(r.reached_inputs, 3);
    assert_eq!(r.scalar_input_influence, "observed");
}
