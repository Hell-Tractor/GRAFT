//! A deliberately conservative plan-retention gate: the generated core AST is immutable.
//! Wrappers may change, but their reachability must be measured separately.
use super::{emit, model::*, rustdoc::sha256};
use quote::ToTokens;
use serde::{Deserialize, Serialize};
use std::{path::Path, time::Instant};

pub fn retain_plan(plan: &SequencePlan, source: &str) -> Result<(), String> {
    let expected = syn::parse_file(&emit::emit(plan).map_err(|e| format!("{e:?}"))?)
        .map_err(|e| e.to_string())?;
    let candidate = syn::parse_file(source).map_err(|e| format!("invalid Rust AST: {e}"))?;
    if !candidate.attrs.is_empty() {
        return Err("crate attributes may disable/alter planned calls".into());
    }
    let core = expected
        .items
        .iter()
        .find(|i| matches!(i,syn::Item::Fn(f) if f.sig.ident == "graft_body"))
        .unwrap();
    let mut found = 0;
    let mut main = 0;
    for item in &candidate.items {
        match item {
            syn::Item::Fn(f) if f.sig.ident == "graft_body" => {
                found += 1;
                if item.to_token_stream().to_string() != core.to_token_stream().to_string() {
                    return Err("planned core AST changed: call/order/type/binding/branch/input decoder must be retained; request replanning instead".into());
                }
            }
            syn::Item::Fn(f) if f.sig.ident == "main" => {
                main += 1;
            }
            syn::Item::ExternCrate(e)
                if e.to_token_stream().to_string() == "extern crate alloc ;" => {}
            _ => {
                return Err(
                    "additional top-level item could shadow planned name resolution".into(),
                );
            }
        }
    }
    if found != 1 || main > 1 {
        return Err("expected one immutable graft_body and at most one wrapper main".into());
    }
    Ok(())
}
pub fn prompt(plan: &SequencePlan) -> Result<String, Failure> {
    Ok(format!(
        "Render the following pinned SequencePlan as Rust. The deterministic graft_body function below is immutable: preserve its AST, including actual calls, order, type arguments, value bindings, branches, and input decoding. You may format/comment it and add one main wrapper. The standalone main must parse each command-line argument as a u8, pass those bytes (or empty bytes if no arguments) to graft_body, and print the returned trace. It will be probed on empty, 0, and 1 inputs with a three-second timeout. No other top-level items or crate attributes. Unsupported operations require replanning, never target deletion, constant substitution, version changes, or unsafe workarounds. Return Rust source only. Compilation, plan retention, and runtime reachability are separate outcomes.\nPLAN:\n{}\nIMMUTABLE CORE:\n{}",
        serde_json::to_string_pretty(plan).unwrap(),
        emit::emit(plan)?
    ))
}
pub fn repair_prompt(original: &str, code: &str, diagnostics: &str, remaining: usize) -> String {
    format!(
        "{original}\nCURRENT SOURCE:\n{code}\nCOMPILER / PLAN DIAGNOSTICS:\n{diagnostics}\nRemaining repair calls: {remaining}. Return corrected Rust source only. Preserve the pinned dependency and immutable planned core."
    )
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Response {
    pub content: String,
    pub model: String,
    pub immutable_revision: Option<String>,
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub cost: Option<f64>,
}
#[async_trait::async_trait]
pub trait Backend {
    async fn respond(&mut self, prompt: &str) -> Result<Response, String>;
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Attempt {
    pub index: usize,
    pub is_repair: bool,
    pub response: Response,
    pub latency_ms: u128,
    pub source_sha256: String,
    pub plan_retained: bool,
    pub compiled: bool,
    pub diagnostics: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GenerationReport {
    pub schema: String,
    pub started_unix_ms: u128,
    pub max_repairs: usize,
    pub accepted: bool,
    pub attempts: Vec<Attempt>,
    pub backend_error: Option<String>,
    pub runtime_status: String,
}

/// max_repairs excludes the initial call. Each target has its own complete transcript.
/// Compiler callback must use the pinned, locked project; no dependency mutations are accepted here.
pub async fn generate<B: Backend>(
    plan: &SequencePlan,
    backend: &mut B,
    dir: &Path,
    max_repairs: usize,
    mut compile: impl FnMut(&str) -> Result<(), String>,
) -> Result<GenerationReport, String> {
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let original = prompt(plan).map_err(|e| format!("{e:?}"))?;
    let mut current_prompt = original.clone();
    let mut report = GenerationReport {
        schema: "graft-generation-v1".into(),
        started_unix_ms: std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis(),
        max_repairs,
        accepted: false,
        attempts: vec![],
        backend_error: None,
        runtime_status: "separate_probe_required".into(),
    };
    for index in 0..=max_repairs {
        std::fs::write(
            dir.join(format!("attempt-{index}.prompt.txt")),
            &current_prompt,
        )
        .map_err(|e| e.to_string())?;
        let start = Instant::now();
        let response = match backend.respond(&current_prompt).await {
            Ok(r) => r,
            Err(e) => {
                report.backend_error = Some(e);
                std::fs::write(
                    dir.join("generation.json"),
                    serde_json::to_vec_pretty(&report).unwrap(),
                )
                .map_err(|e| e.to_string())?;
                return Ok(report);
            }
        };
        let latency_ms = start.elapsed().as_millis();
        let code = response
            .content
            .trim()
            .strip_prefix("```rust")
            .unwrap_or(response.content.trim())
            .trim()
            .trim_end_matches("```")
            .trim()
            .to_string();
        let retained = retain_plan(plan, &code);
        let built = compile(&code);
        let diagnostics = [
            retained.as_ref().err().cloned(),
            built.as_ref().err().cloned(),
        ]
        .into_iter()
        .flatten()
        .collect::<Vec<_>>()
        .join("\n");
        let accepted = retained.is_ok() && built.is_ok();
        std::fs::write(dir.join(format!("attempt-{index}.rs")), &code)
            .map_err(|e| e.to_string())?;
        report.attempts.push(Attempt {
            index,
            is_repair: index > 0,
            response,
            latency_ms,
            source_sha256: sha256(code.as_bytes()),
            plan_retained: retained.is_ok(),
            compiled: built.is_ok(),
            diagnostics: diagnostics.clone(),
        });
        report.accepted = accepted;
        std::fs::write(
            dir.join("generation.json"),
            serde_json::to_vec_pretty(&report).unwrap(),
        )
        .map_err(|e| e.to_string())?;
        if accepted {
            std::fs::write(dir.join("accepted.rs"), code).map_err(|e| e.to_string())?;
            break;
        }
        current_prompt = repair_prompt(
            &original,
            &code,
            &diagnostics,
            max_repairs.saturating_sub(index + 1),
        );
    }
    Ok(report)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeSample {
    pub args: Vec<String>,
    pub exit_code: Option<i32>,
    pub timed_out: bool,
    pub target_reached: bool,
    pub scalar_arguments: Vec<String>,
    pub stdout: String,
    pub stderr: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeReport {
    pub schema: String,
    pub samples: Vec<RuntimeSample>,
    pub reached_inputs: usize,
    pub scalar_input_influence: String,
    pub bounded_probe_passed: bool,
    pub scope: String,
}
/// Exercises the actual wrapper. Markers originate inside the immutable core after calls.
/// Finite samples are diagnostic witnesses, never a universal reachability/taint proof.
pub async fn probe(
    plan: &SequencePlan,
    executable: &Path,
    output: &Path,
) -> Result<RuntimeReport, String> {
    let (instance, args) = plan
        .steps
        .iter()
        .rev()
        .find_map(|s| match s {
            Step::Call {
                instance,
                api,
                args,
                ..
            } if api == &plan.target => Some((*instance, args)),
            _ => None,
        })
        .ok_or("target absent from plan")?;
    let direct_scalar = args
        .iter()
        .any(|b| emit::input_supported(plan.steps[b.value].ty()));
    let marker = format!("GRAFT_CALL:{instance}:");
    let mut samples = vec![];
    for args in [vec![], vec!["0".to_string()], vec!["1".to_string()]] {
        let mut command = tokio::process::Command::new(executable);
        command
            .args(&args)
            .env("GRAFT_TRACE", "1")
            .kill_on_drop(true);
        let sample =
            match tokio::time::timeout(std::time::Duration::from_secs(3), command.output()).await {
                Err(_) => RuntimeSample {
                    args,
                    exit_code: None,
                    timed_out: true,
                    target_reached: false,
                    scalar_arguments: vec![],
                    stdout: String::new(),
                    stderr: "probe timed out".into(),
                },
                Ok(Err(e)) => return Err(e.to_string()),
                Ok(Ok(o)) => {
                    let stderr = String::from_utf8_lossy(&o.stderr).into_owned();
                    let arguments = stderr
                        .lines()
                        .filter_map(|l| l.strip_prefix(&marker).map(String::from))
                        .collect::<Vec<_>>();
                    RuntimeSample {
                        args,
                        exit_code: o.status.code(),
                        timed_out: false,
                        target_reached: !arguments.is_empty(),
                        scalar_arguments: arguments,
                        stdout: String::from_utf8_lossy(&o.stdout).into_owned(),
                        stderr,
                    }
                }
            };
        samples.push(sample);
    }
    let reached_inputs = samples.iter().filter(|s| s.target_reached).count();
    let observed: std::collections::BTreeSet<_> =
        samples.iter().flat_map(|s| &s.scalar_arguments).collect();
    let scalar_input_influence = if !direct_scalar {
        "unknown_no_direct_scalar"
    } else if observed.len() > 1 {
        "observed"
    } else {
        "not_observed"
    }
    .to_string();
    let bounded_probe_passed = reached_inputs > 0
        && samples
            .iter()
            .all(|s| s.exit_code == Some(0) && !s.timed_out)
        && scalar_input_influence != "not_observed";
    let report=RuntimeReport{schema:"graft-runtime-v1".into(),samples,reached_inputs,scalar_input_influence,bounded_probe_passed,scope:"three standalone-wrapper inputs only; marker observations are not complete taint or proof for all inputs; opaque target arguments remain unknown".into()};
    std::fs::write(output, serde_json::to_vec_pretty(&report).unwrap())
        .map_err(|e| e.to_string())?;
    Ok(report)
}

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct ExtendedSample {
    #[serde(flatten)]
    pub run:RuntimeSample,
    pub call_entered:bool,
    pub object_arguments:Vec<String>,
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct ExtendedReport {
    #[serde(flatten)]
    pub strict:RuntimeReport,
    pub extended_samples:Vec<ExtendedSample>,
    pub argument_input_influence:String,
    pub extended_probe_passed:bool,
    pub probe_budget_exhausted:bool,
    pub extended_scope:String,
}

/// Keep the strict gate, then require repeatable value changes where observable.
/// Eight fixed inputs are repeated once; no coverage-guided or plan-specific seeds.
pub async fn probe_extended(plan:&SequencePlan,executable:&Path,output:&Path)->Result<ExtendedReport,String> {
    let started=Instant::now();
    let strict=probe(plan,executable,output).await?;
    let (instance,args)=plan.steps.iter().rev().find_map(|s|match s {
        Step::Call{instance,api,args,..} if api==&plan.target=>Some((*instance,args)),_=>None,
    }).ok_or("target absent")?;
    let scalar=args.iter().any(|b|emit::input_supported(plan.steps[b.value].ty()));
    let object_marker=format!("GRAFT_OBJECT:{instance}:");
    let enter_marker=format!("GRAFT_ENTER:{instance}");
    let post_marker=format!("GRAFT_CALL:{instance}:");
    let convert=|run:RuntimeSample| {
        let object_arguments=run.stderr.lines().filter_map(|s|s.strip_prefix(&object_marker))
            .filter(|s|*s!="[]").map(String::from).collect();
        let call_entered=run.stderr.lines().any(|s|s==enter_marker);
        ExtendedSample{run,object_arguments,call_entered}
    };
    let inputs:Vec<Vec<String>>=[vec![],vec![0],vec![1],vec![255],vec![1,0,0,0],
        vec![2,0,0,0],vec![255,0,0,0],vec![0,1,2,3,4,5,6,7]]
        .into_iter().map(|v|v.into_iter().map(|b|b.to_string()).collect()).collect();
    let mut samples:Vec<_>=strict.samples.iter().cloned().map(&convert).collect();
    let mut exhausted=false;
    for input in inputs.iter().skip(3).chain(inputs.iter()) {
        let remaining=10.0-started.elapsed().as_secs_f64();
        if remaining<=0.0 {exhausted=true;break;}
        let mut command=tokio::process::Command::new(executable);
        command.args(input).env("GRAFT_TRACE","1").kill_on_drop(true);
        let run=match tokio::time::timeout(std::time::Duration::from_secs_f64(remaining.min(3.0)),command.output()).await {
            Ok(Ok(out))=>{
                let stderr=String::from_utf8_lossy(&out.stderr).into_owned();
                let arguments:Vec<_>=stderr.lines().filter_map(|s|s.strip_prefix(&post_marker).map(String::from)).collect();
                RuntimeSample{args:input.clone(),exit_code:out.status.code(),timed_out:false,
                    target_reached:!arguments.is_empty(),scalar_arguments:arguments,
                    stdout:String::from_utf8_lossy(&out.stdout).into_owned(),stderr}
            },
            Ok(Err(e))=>return Err(e.to_string()),
            Err(_)=>RuntimeSample{args:input.clone(),exit_code:None,timed_out:true,target_reached:false,
                scalar_arguments:vec![],stdout:String::new(),stderr:"extended probe timed out".into()},
        };
        samples.push(convert(run));
    }
    let object_supported=samples.iter().any(|s|!s.object_arguments.is_empty());
    let mut grouped:std::collections::BTreeMap<Vec<String>,Vec<String>>=Default::default();
    for sample in &samples {
        if sample.run.exit_code==Some(0) && sample.run.target_reached && !sample.run.timed_out {
            let observation=serde_json::to_string(&(
                if scalar {sample.run.scalar_arguments.clone()}else{vec![]},
                &sample.object_arguments)).map_err(|e|e.to_string())?;
            grouped.entry(sample.run.args.clone()).or_default().push(observation);
        }
    }
    let unstable=grouped.values().any(|v|v.len()==2 && v[0]!=v[1]);
    let stable_values:std::collections::BTreeSet<_>=grouped.values()
        .filter(|v|v.len()==2 && v[0]==v[1]).map(|v|v[0].clone()).collect();
    let influence=if args.is_empty() {"not_applicable_no_arguments"}
        else if !scalar && !object_supported {"unknown_opaque_arguments"}
        else if unstable {"nondeterministic_observations"}
        else if stable_values.len()>=2 {"observed_repeatable"}
        else {"not_observed"};
    exhausted=exhausted || samples.len()!=16 || started.elapsed().as_secs_f64()>10.0;
    let passed=strict.bounded_probe_passed && !exhausted &&
        !matches!(influence,"not_observed"|"nondeterministic_observations");
    let report=ExtendedReport{strict,extended_samples:samples,argument_input_influence:influence.into(),
        extended_probe_passed:passed,probe_budget_exhausted:exhausted,
        extended_scope:"unchanged strict three-input gate plus 16 fixed repeated-input observations within 10 seconds; supported object snapshots are bounded and are not a complete taint proof; extra inputs are not formal fuzz seeds".into()};
    std::fs::write(output,serde_json::to_vec_pretty(&report).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    Ok(report)
}
