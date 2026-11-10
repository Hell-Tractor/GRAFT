//! Reproducible bounded planner; legacy graft api-sequence is not a checked plan.
use clap::{Parser, Subcommand};
use graft::checked::{
    planner::{Limits, plan},
    *,
};
use std::path::PathBuf;

#[derive(Parser)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    /// Read raw format-57 rustdoc or historical APIDoc; retain unsupported predicates.
    Import {
        input: PathBuf,
        output: PathBuf,
        #[arg(long)]
        package: Option<String>,
        #[arg(long)]
        features: Vec<String>,
        #[arg(long)]
        no_default_features: bool,
    },
    /// Plan one target API id; every dependency and branch is explicit.
    Plan {
        catalog: PathBuf,
        target: String,
        output: PathBuf,
        #[arg(long, default_value_t = 2000)]
        nodes: usize,
        #[arg(long, default_value_t = 12)]
        depth: usize,
        #[arg(long)]
        independent_arguments: bool,
        /// Syntactic control: no generic solving or concrete generic enumeration.
        #[arg(long)]
        syntactic: bool,
        /// Let the pinned compiler infer call result annotations, not API identity.
        #[arg(long)]
        infer_call_results: bool,
    },
    /// Emit a standalone validation program and an exact-version Cargo manifest.
    Emit { plan: PathBuf, output: PathBuf },
    /// Save the exact initial model prompt for inspection without a model call.
    Prompt { plan: PathBuf, output: PathBuf },
    /// Reference core without tracing; parameter repair must preserve call anchors.
    RepairSeed { plan: PathBuf, output: PathBuf },
    /// Bounded alternate producers for exactly the original target and catalog.
    Alternatives {
        plan:PathBuf,catalog:PathBuf,output:PathBuf,
        #[arg(long,default_value_t=2)] max_candidates:usize,
        #[arg(long,default_value_t=2000)] nodes:usize,
        #[arg(long)] syntactic:bool,
    },
    /// Check immutable core AST retention; compilation and runtime are separate checks.
    Validate { plan: PathBuf, source: PathBuf },
    /// Run the actual standalone wrapper on three inputs; save separate runtime evidence.
    Probe {
        plan: PathBuf,
        executable: PathBuf,
        output: PathBuf,
        #[arg(long)]
        extended:bool,
    },
    /// Count supported/unknown APIs without claiming reachability or edge validity.
    Inventory {
        catalog: PathBuf,
        output: PathBuf,
        #[arg(long)]
        edges: bool,
    },
}
fn read<T: serde::de::DeserializeOwned>(p: &PathBuf) -> Result<T, Box<dyn std::error::Error>> {
    Ok(serde_json::from_slice(&std::fs::read(p)?)?)
}
fn write(p: &PathBuf, v: &impl serde::Serialize) -> Result<(), Box<dyn std::error::Error>> {
    std::fs::write(p, serde_json::to_vec_pretty(v)?)?;
    Ok(())
}
fn get_plan(p: &PathBuf) -> Result<SequencePlan, Box<dyn std::error::Error>> {
    let r: PlanResult = read(p)?;
    match r {
        PlanResult::Planned { plan } => Ok(plan),
        _ => Err("cannot emit rejected plan".into()),
    }
}
fn run() -> Result<(), Box<dyn std::error::Error>> {
    match Cli::parse().command {
        Command::Alternatives{plan:original,catalog,output,max_candidates,nodes,syntactic}=>{
            let p=get_plan(&original)?;let mut c:Catalog=read(&catalog)?;
            if syntactic {for a in &mut c.apis {if !a.generics.is_empty(){a.unsupported.push("syntactic control disables generic solving/instantiation".into());}}}
            write(&output,&planner::alternatives(&c,&p,&Limits{nodes,..Limits::default()},max_candidates)
                .map_err(|e|format!("{e:?}"))?)?;
        },
        Command::RepairSeed { plan, output } => {
            std::fs::write(output, repair::seed(&get_plan(&plan)?)?)?;
        }
        Command::Probe {
            plan,
            executable,
            output,
            extended,
        } => {
            let p = get_plan(&plan)?;
            if extended {
                let r=tokio::runtime::Runtime::new()?.block_on(validate::probe_extended(&p,&executable,&output))?;
                if !r.extended_probe_passed {std::process::exit(3);}
                return Ok(());
            }
            let r = tokio::runtime::Runtime::new()?.block_on(validate::probe(
                &p,
                &executable,
                &output,
            ))?;
            if !r.bounded_probe_passed {
                std::process::exit(3);
            }
        }
        Command::Import {
            input,
            output,
            package,
            features,
            no_default_features,
        } => {
            let mut c = rustdoc::import(&std::fs::read(input)?)?;
            if let Some(p) = package {
                c.package_name = p;
            }
            c.features = features;
            c.default_features = !no_default_features;
            write(&output, &c)?;
        }
        Command::Plan {
            catalog,
            target,
            output,
            nodes,
            depth,
            independent_arguments,
            syntactic,
            infer_call_results,
        } => {
            let mut c: Catalog = read(&catalog)?;
            if syntactic {
                for api in &mut c.apis {
                    if !api.generics.is_empty() {
                        api.unsupported.push("syntactic control disables generic solving/instantiation".into());
                    }
                }
            }
            let mut r = plan(
                &c,
                &target,
                &Limits {
                    nodes,
                    depth,
                    independent_arguments,
                    ..Limits::default()
                },
            );
            if let PlanResult::Planned{plan}= &mut r {plan.infer_call_results=infer_call_results;}
            write(&output, &r)?;
            if matches!(r, PlanResult::Rejected { .. }) {
                std::process::exit(2);
            }
        }
        Command::Emit { plan, output } => {
            let p = get_plan(&plan)?;
            std::fs::create_dir_all(output.join("src"))?;
            std::fs::write(
                output.join("src/main.rs"),
                emit::standalone(&p).map_err(|e| format!("{e:?}"))?,
            )?;
            std::fs::write(
                output.join("Cargo.toml"),
                emit::manifest(&p).map_err(|e| format!("{e:?}"))?,
            )?;
            write(&output.join("plan.json"), &p)?;
        }
        Command::Prompt { plan, output } => {
            let p = get_plan(&plan)?;
            std::fs::write(output, validate::prompt(&p).map_err(|e| format!("{e:?}"))?)?;
        }
        Command::Validate { plan, source } => {
            validate::retain_plan(&get_plan(&plan)?, &std::fs::read_to_string(source)?)?;
            println!("plan retained; compilation and wrapper reachability not established");
        }
        Command::Inventory {
            catalog,
            output,
            edges,
        } => {
            let c: Catalog = read(&catalog)?;
            write(&output, &audit::inventory(&c, edges))?;
        }
    }
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
