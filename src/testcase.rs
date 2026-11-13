use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};
use std::time::Duration;
use indicatif::{MultiProgress, ProgressDrawTarget, ProgressStyle};
use tokio::process::Command;
use tracing::{debug, enabled, info, warn};

use crate::api_graph;
use crate::error::{Error, Result};

use crate::llm::LLM;
use crate::utils;

/// New plan-bound path. Legacy Testcase::new accepts historical unbound API lists.
/// This produces a validation executable; AFL wrapping is a subsequent, separate step.
pub async fn generate_checked(input: &Path, output: &Path, max_repairs: usize, shared_project: Option<&Path>) -> Result<()> {
    use graft::checked::{emit, validate, PlanResult};
    let result: PlanResult = serde_json::from_slice(&std::fs::read(input)?)?;
    let PlanResult::Planned { plan } = result else {
        return Err(Error::CommandError("checked testcase requires a complete plan".into()));
    };
    if output.exists() {
        return Err(Error::CommandError("checked testcase output must be a new directory to preserve transcripts".into()));
    }
    std::fs::create_dir_all(output.join("src"))?;
    let canonical_output = std::fs::canonicalize(output)?;
    let output = canonical_output.as_path();
    let manifest = if let Some(shared) = shared_project {
        let manifest = emit::shared_manifest(&plan, &std::fs::read_to_string(shared.join("Cargo.toml"))?)
            .map_err(|e| Error::CommandError(format!("{e:?}")))?;
        std::fs::copy(shared.join("Cargo.lock"), output.join("Cargo.lock"))?;
        std::fs::create_dir_all(output.join(".cargo"))?;
        std::fs::copy(shared.join(".cargo/config.toml"), output.join(".cargo/config.toml"))?;
        manifest
    } else {
        emit::manifest(&plan).map_err(|e| Error::CommandError(format!("{e:?}")))?
    };
    std::fs::write(output.join("Cargo.toml"), &manifest)?;
    std::fs::write(output.join("plan.json"), serde_json::to_vec_pretty(&plan)?)?;
    std::fs::write(output.join("src/main.rs"), emit::standalone(&plan).map_err(|e| Error::CommandError(format!("{e:?}")))?)?;
    let cargo = utils::config().commands.cargo.clone();
    if shared_project.is_none() {
        let lock = std::process::Command::new(&cargo).args(["generate-lockfile", "--offline"]).current_dir(output).output()?;
        if !lock.status.success() {
            return Err(Error::CommandError(format!("pinned offline dependency resolution failed: {}", String::from_utf8_lossy(&lock.stderr))));
        }
    }
    let lock_bytes = std::fs::read(output.join("Cargo.lock"))?;
    struct PlanLLM { inner: Box<dyn LLM>, model: String }
    #[async_trait::async_trait]
    impl validate::Backend for PlanLLM {
        async fn respond(&mut self, prompt: &str) -> std::result::Result<validate::Response, String> {
            let response = self.inner.generate_response(prompt).await.map_err(|e| e.to_string())?;
            Ok(validate::Response { content: response.content, model: self.model.clone(), immutable_revision: None, input_tokens: None, output_tokens: None, cost: None })
        }
    }
    let cfg = &utils::config().llm;
    let mut backend = PlanLLM { inner: crate::llm::LLMFactory::create_llm(&cfg.type_, &cfg.model_name, &cfg.api_url_chat), model: cfg.model_name.clone() };
    let report = validate::generate(&plan, &mut backend, &output.join("transcript"), max_repairs, |code| {
        if std::fs::read_to_string(output.join("Cargo.toml")).map_err(|e| e.to_string())? != manifest || std::fs::read(output.join("Cargo.lock")).map_err(|e| e.to_string())? != lock_bytes {
            return Err("pinned manifest/lock changed".into());
        }
        std::fs::write(output.join("src/main.rs"), code).map_err(|e| e.to_string())?;
        let build = std::process::Command::new(&cargo).args(["build", "--offline", "--locked", "--bin", "graft_checked_target", "--target-dir"]).arg(output.join("build")).current_dir(output).output().map_err(|e| e.to_string())?;
        if build.status.success() { Ok(()) } else { Err(String::from_utf8_lossy(&build.stderr).into_owned()) }
    }).await.map_err(Error::CommandError)?;
    if !report.accepted { return Err(Error::CommandError("no compiled, plan-retaining target within repair budget; see transcript/generation.json".into())); }
    let runtime = validate::probe(&plan, &output.join("build/debug/graft_checked_target"), &output.join("runtime.json")).await.map_err(Error::CommandError)?;
    if !runtime.bounded_probe_passed {
        return Err(Error::CommandError("compiled core retained, but wrapper runtime probe failed; see runtime.json".into()));
    }
    info!("Checked core compiled and retained; bounded wrapper probe passed (not a proof for all inputs).");
    Ok(())
}

#[derive(serde::Deserialize, serde::Serialize)]
pub struct Testcase {
    path: PathBuf,
    target_crate: String,
    #[serde(skip)]
    pb: Option<ProgressBar>,
}

struct ProgressBar {
    multi: MultiProgress,
    // summary: indicatif::ProgressBar,
}

#[derive(serde::Serialize)]
struct API {
    signature: String,
    doc_comment: String,
    module_path: String,
}

impl From<&api_graph::API> for API {
    fn from(api: &api_graph::API) -> Self {
        API {
            signature: api.signature.clone(),
            doc_comment: api.doc_comment.clone(),
            module_path: api.module_path.clone(),
        }
    }
}

impl Testcase {
    pub fn from_existing(path: &Path, target_crate: &str) -> Self {
        Testcase {
            path: path.to_owned(),
            target_crate: target_crate.to_owned(),
            pb: None,
        }
    }

    pub fn with_pb(mut self, multi: MultiProgress/* , summary: indicatif::ProgressBar */) -> Self {
        self.pb = Some(ProgressBar { multi/* , summary */ });
        self
    }

    pub async fn new(path: &Path, name: &str, target_crate: &str, api_sequence: &Vec<Arc<RwLock<api_graph::API>>>, mut llm: Box<dyn LLM>) -> Result<Self> {
        let project_path = path.join(name);
        let cargo = &utils::config().commands.cargo;
        if project_path.exists() {
            info!("Cargo project `{}` already exists, trying to build it.", name);
            let output = Command::new(&cargo)
                .arg("+nightly")
                .arg("afl").arg("build")
                .env("RUSTFLAGS", "-Z sanitizer=address")
                .env("ASAN_OPTIONS", "detect_leaks=0:abort_on_error=1:symbolize=0")
                .current_dir(&project_path)
                .output().await;
            if output.is_ok() && output.unwrap().status.success() {
                info!("Existing project `{}` built successfully, skipping testcase generation.", name);
                return Ok(Testcase { path: path.join(name).into(), target_crate: target_crate.to_string(), pb: None })
            }
            info!("Failed to build project `{}`, Removing existing directory at {}", name, project_path.display());
            std::fs::remove_dir_all(&project_path)?;
        }
        if !project_path.exists() {
            std::fs::create_dir_all(&path)?;

            // create project
            info!("Creating new cargo project `{}` at {}", name, project_path.display());
            Command::new(cargo)
                .arg("new").arg(name).arg("--vcs").arg("none")
                .current_dir(path)
                .status().await?;
            info!("Project `{}` created successfully", name);
        }


        // add dependency
        info!("Adding dependency `{}` to project `{}`", target_crate, name);
        let output = Command::new(cargo)
            .arg("add").arg(target_crate)
            .current_dir(&project_path)
            .output().await?;
        if !output.status.success() {
            return Err(Error::CommandError(format!("Failed to add dependency: {}", String::from_utf8_lossy(&output.stderr))));
        }
        info!("Dependency `{}` added successfully to project `{}`", target_crate, name);

        let api_sequence = api_sequence.iter()
            .map(|api| {
                let api = api.read().unwrap();
                API::from(&*api)
            })
            .collect::<Vec<API>>();
        let api_doc = serde_json::to_string(&api_sequence)?;
        let main_path = project_path.join("src/main.rs");
        let cargo_path = project_path.join("Cargo.toml");
        Testcase::generate(name, target_crate, &main_path, &cargo_path, &api_doc, &mut llm).await?;
        info!("Testcase for project `{}` generated successfully", name);

        Ok(Testcase { path: path.join(name).into(), target_crate: target_crate.to_string(), pb: None })
    }

    async fn generate(name: &str, crate_name: &str, main_path: &Path, cargo_path: &Path, api_doc: &str, llm: &mut Box<dyn LLM>) -> Result<()> {
        info!("Generating testcase for project `{}` using LLM...", name);
        // read prompt template
        let prompt_path = &utils::config().prompts.testcase_gen;
        let prompt = utils::read_file_to_string(prompt_path)?;

        // read fix prompt template
        let fix_prompt_path = &utils::config().prompts.fix_testcase;
        let fix_prompt = utils::read_file_to_string(fix_prompt_path)?;

        let mut prompt = prompt
            .replace("{crate_name}", crate_name)
            .replace("{api_sequence}", api_doc);
        // save prompt to temp_prompt.txt for debug
        if enabled!(tracing::Level::DEBUG) {
            std::fs::write("temp_prompt.txt", &prompt)?;
        }
        let cargo = &utils::config().commands.cargo;
        let retries = utils::config().llm.fix_retries;
        for i in 0..retries {
            let mut attempt = async || -> Result<()> {
                let response = llm.generate_response(&prompt).await?;
                let code = response.content
                    .trim_start_matches("```rust")
                    .trim_start_matches("```")
                    .trim_end_matches("```");
                std::fs::write(main_path, code)?;

                // try to build the project
                let output = Command::new(&cargo)
                    .arg("+nightly")
                    .arg("afl").arg("build")
                    .env("RUSTFLAGS", "-Z sanitizer=address")
                    .env("ASAN_OPTIONS", "detect_leaks=0:abort_on_error=1:symbolize=0")
                    .current_dir(cargo_path.parent().unwrap())
                    .output().await?;
                if output.status.success() {
                    Ok(())
                } else {
                    let error_message = String::from_utf8_lossy(&output.stderr);
                    debug!("Build error: {}", error_message);
                    prompt = fix_prompt.replace("{errors}", &error_message);
                    Err(Error::CommandError("cargo build failed".to_string()))
                }
            };

            if let Err(e) = attempt().await {
                warn!("Failed to generate testcase `{}`", name);
                warn!("Error: {}", e);
                warn!("Retrying...({} time(s) left)", retries - i - 1);
            } else {
                // info!("Successfully generated testcase for project `{}`", name);
                return Ok(());
            }
        }

        Err(Error::CodeGenerationError(name.to_string(), retries))
    }

    pub async fn generate_inputs(&self, relative_dir: &Path, mut llm: Box<dyn LLM>, seed_count: usize) -> Result<()> {
        let input_dir = self.path.join(relative_dir);
        if input_dir.exists() {
            let count = std::fs::read_dir(&input_dir)?.count();
            if count >= seed_count {
                info!("Input directory already exists with {} inputs, skipping input generation.", count);
                return Ok(());
            }
            info!("Input directory already exists with {} inputs, but less than required {}, regenerating inputs.", count, seed_count);
        }
        // read prompt template
        let prompt_path = &utils::config().prompts.input_gen;
        let prompt_template = utils::read_file_to_string(prompt_path)?;

        let input_count = seed_count;

        // read code snippet from main.rs
        let path = self.path.join("src").join("main.rs");
        let code_snippet = utils::read_file_to_string(path)?;

        let prompt = prompt_template
            .replace("{crate_name}", &self.target_crate)
            .replace("{count}", &input_count.to_string())
            .replace("{code_snippet}", &code_snippet);

        let retries = utils::config().llm.input_gen_retries;
        for i in 0..retries {
            let mut attempt = async || -> Result<()> {
                let response = llm.generate_response(&prompt).await?;
                let content = response.content
                    .trim_start_matches("```json")
                    .trim_start_matches("```")
                    .trim_end_matches("```");
                let Ok(inputs) = serde_json::from_str::<Vec<String>>(content) else {
                    return Err(Error::LLMResponseInvalidFormat);
                    // info!("LLM response for input generation is not valid JSON, retrying...({} time(s) left)", retries - i - 1);
                    // continue;
                };

                if !input_dir.exists() {
                    std::fs::create_dir_all(&input_dir)?;
                }
                for (index, input) in inputs.iter().enumerate() {
                    let input_path = input_dir.join(format!("input_{}", index));
                    std::fs::write(input_path, input)?;
                }
                Ok(())
            };

            if let Err(e) = attempt().await {
                warn!("Failed to generate inputs for project `{}`", self.path.iter().last().and_then(|name| name.to_str()).unwrap_or("unknown"));
                warn!("Error: {}", e);
                warn!("Retrying...({} time(s) left)", retries - i - 1);
            } else {
                // info!("Successfully generated inputs for project `{}`", self.path.display());
                return Ok(());
            }
        }
        Ok(())
    }

    pub async fn run(&self, relative_input_dir: &Path, relative_output_dir: &Path, resume: bool, timeout: u64) -> Result<()> {
        let project_name = self.path.file_name().unwrap().to_string_lossy();
        let cargo = &utils::config().commands.cargo;

        // Auto-detect resume: if AFL queue exists, always resume regardless of the flag.
        // This allows batch-rotation fuzzing without manually tracking per-testcase state.
        let queue_dir = self.path.join(relative_output_dir).join("default/queue");
        let has_queue = queue_dir.exists()
            && std::fs::read_dir(&queue_dir).map(|mut d| d.next().is_some()).unwrap_or(false);
        let do_resume = resume || has_queue;

        if !do_resume {
            // Skip build if the AFL-instrumented binary already exists (built during testcase generation).
            // Running 32 concurrent cargo-afl builds causes proc-macro crate races (e.g. zerofrom_derive).
            let binary_path = self.path.join("target/debug").join(project_name.as_ref());
            if !binary_path.exists() {
                info!("Building project...");
                let output = Command::new(&cargo)
                    .arg("+nightly")
                    .arg("afl").arg("build")
                    .env("RUSTFLAGS", "-Z sanitizer=address")
                    .env("ASAN_OPTIONS", "detect_leaks=0:abort_on_error=1:symbolize=0")
                    .current_dir(&self.path)
                    .output().await?;
                if !output.status.success() {
                    return Err(Error::CommandError(format!("AFL build failed: {}", String::from_utf8_lossy(&output.stderr))));
                }
                info!("Build completed successfully");
            } else {
                info!("Binary already exists, skipping build");
            }
            info!("Starting AFL fuzzing for {} seconds", timeout);
        } else {
            info!("Resuming AFL fuzzing for {} seconds", timeout);
        }

        // pb init
        let pb = self.pb.as_ref().map(|_| {
            let pb = indicatif::ProgressBar::with_draw_target(Some(timeout), ProgressDrawTarget::hidden());
            pb.set_style(
                ProgressStyle::with_template("{prefix:.bold} {bar:30.cyan} | {msg}")
                    .unwrap()
            );
            pb.set_prefix(project_name.to_string());
            pb
        });

        let mut fuzz_process = Command::new(&cargo);
        fuzz_process.arg("+nightly").arg("afl").arg("fuzz")
            .env("RUSTFLAGS", "-Z sanitizer=address")
            .env("ASAN_OPTIONS", "detect_leaks=0:abort_on_error=1:symbolize=0")
            .env("AFL_NO_UI", "1")
            .arg("-V").arg(timeout.to_string());
        if do_resume {
            fuzz_process.arg("-i").arg("-");
        } else {
            fuzz_process.arg("-i").arg(relative_input_dir);
        }
        fuzz_process.arg("-o")
            .arg(relative_output_dir)
            // .arg("target/debug/fuzz")
            .arg("--")
            .arg(format!("target/debug/{}", project_name))
            .current_dir(&self.path);
        if let Some(_) = self.pb {
            fuzz_process
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null());
        } else {
            fuzz_process
                .stdout(std::process::Stdio::inherit())
                .stderr(std::process::Stdio::inherit());
        }
        let mut fuzz_process = fuzz_process.spawn()?;

        // if no pb, inherit stdout/stderr and wait
        if let Some(pb) = pb {
            let start = std::time::Instant::now();
            let mut is_visible = false;
            let stats_file = self.path.join(relative_output_dir).join("fuzzer_stats");
            loop {
                let elapsed = start.elapsed().as_secs();
                pb.set_position(elapsed);

                let found_crash = get_crash_count(&stats_file).await;
                if found_crash > 0 {
                    if !is_visible {
                        let pb = self.pb.as_ref().unwrap().multi.add(pb.clone());
                        pb.set_draw_target(ProgressDrawTarget::stderr());
                        is_visible = true;
                        warn!("Crash found(project = `{}`)! ", project_name);
                    }
                    pb.set_message(format!("Found {} crash(es)!", found_crash));
                } else {
                    pb.set_message("Fuzzing...");
                }

                if let Ok(Some(_)) = fuzz_process.try_wait() {
                    break;
                }
                if elapsed >= timeout {
                    break;
                }

                tokio::time::sleep(Duration::from_millis(500)).await;
            }

            if is_visible {
                pb.finish_and_clear();
            } else {
                pb.finish();
            }
            // self.pb.as_ref().unwrap().summary.inc(1);
        }

        let status = &fuzz_process.wait_with_output().await?.status;
        if !status.success() {
            return Err(Error::CommandError(format!("AFL fuzzing failed with status: {}", status)));
        }

        info!("AFL fuzzing completed");

        Ok(())
    }
}

async fn get_crash_count(stats_path: &Path) -> u64 {
    let content = match tokio::fs::read_to_string(stats_path).await {
        Ok(c) => c,
        Err(_) => return 0,
    };

    for line in content.lines() {
        if line.starts_with("saved_crashes") {
            let parts: Vec<&str> = line.split(':').map(|s| s.trim()).collect();
            if parts.len() == 2 {
                if let Ok(crashes) = parts[1].parse::<u64>() {
                    return crashes;
                }
            }
        }
    }

    0
}
