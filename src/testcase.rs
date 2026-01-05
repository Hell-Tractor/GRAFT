use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};
use tokio::process::Command;
use tracing::{debug, enabled, info, span};

use crate::api_graph;
use crate::error::{Error, Result};

use crate::llm::LLM;
use crate::utils;

#[derive(serde::Deserialize, serde::Serialize)]
pub struct Testcase {
    path: PathBuf,
    target_crate: String,
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
        }
    }

    pub async fn new(path: &Path, name: &str, target_crate: &str, api_sequence: &Vec<Arc<RwLock<api_graph::API>>>, mut llm: Box<dyn LLM>) -> Result<Self> {
        let project_path = path.join(name);
        if project_path.exists() {
            debug!("Removing existing directory at {}", project_path.display());
            std::fs::remove_dir_all(&project_path)?;
        }
        if !path.exists() {
            std::fs::create_dir_all(&path)?;
        }

        // create project
        info!("Creating new cargo project `{}` at {}", name, project_path.display());
        let cargo = &utils::config().commands.cargo;
        Command::new(cargo)
            .arg("new").arg(name).arg("--vcs").arg("none")
            .current_dir(path)
            .status().await?;
        info!("Project `{}` created successfully", name);

        // add dependency
        info!("Adding dependency `{}` to project `{}`", target_crate, name);
        let output = Command::new(&cargo)
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

        Ok(Testcase { path: path.join(name).into(), target_crate: target_crate.to_string() })
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
            let response = llm.generate_response(&prompt).await?;
            // let code_and_deps = serde_json::from_str::<CodeAndDependencies>(&response.content);
            // if let Err(e) = &code_and_deps {
            //     info!("LLM response of project `{}` is not valid JSON, retrying...({} time(s) left)", name, retries - i - 1);
            //     debug!("LLM response content: {}", response.content);
            //     debug!("JSON parsing error: {}", e);
            //     llm.clear();
            //     continue;
            // };
            // let code_and_deps = code_and_deps.unwrap();
            // write code
            // std::fs::write(main_path, code_and_deps.code)?;
            let code = response.content
                .trim_start_matches("```rust")
                .trim_start_matches("```")
                .trim_end_matches("```");
            std::fs::write(main_path, code)?;

            // load origin cargo.toml
            // let origin_cargo_toml = utils::read_file_to_string(cargo_path)?;
            // let new_cargo_toml = origin_cargo_toml.split("[dependencies]").next().unwrap().to_string()
            //     + &code_and_deps.dependencies;
            // std::fs::write(cargo_path, new_cargo_toml)?;

            // try to build the project
            let output = Command::new(&cargo)
                .arg("afl").arg("build")
                .current_dir(cargo_path.parent().unwrap())
                .output().await?;
            if output.status.success() {
                info!("Successfully generated testcase for project `{}`", name);
                return Ok(());
            } else {
                info!("Failed to build project `{}`, retrying...({} time(s) left)", name, retries - i - 1);
                let error_message = String::from_utf8_lossy(&output.stderr);
                debug!("Build error: {}", error_message);
                prompt = fix_prompt.replace("{errors}", &error_message);
            }
        }

        Err(Error::CodeGenerationError(name.to_string(), retries))
    }

    pub async fn generate_inputs(&self, relative_dir: &Path, mut llm: Box<dyn LLM>, seed_count: usize) -> Result<()> {
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
            let response = llm.generate_response(&prompt).await?;
            let content = response.content
                .trim_start_matches("```json")
                .trim_start_matches("```")
                .trim_end_matches("```");
            let Ok(inputs) = serde_json::from_str::<Vec<String>>(content) else {
                info!("LLM response for input generation is not valid JSON, retrying...({} time(s) left)", retries - i - 1);
                continue;
            };

            let input_dir = self.path.join(relative_dir);
            if !input_dir.exists() {
                std::fs::create_dir_all(&input_dir)?;
            }
            for (index, input) in inputs.iter().enumerate() {
                let input_path = input_dir.join(format!("input_{}", index));
                std::fs::write(input_path, input)?;
            }
            break;
        }
        Ok(())
    }

    pub async fn run(&self, relative_input_dir: &Path, relative_output_dir: &Path, resume: bool, timeout: u64) -> Result<()> {
        let project_name = self.path.file_name().unwrap().to_string_lossy();
        let s = span!(tracing::Level::INFO, "Testcase", project = %project_name);
        let _ = s.enter();
        let cargo = &utils::config().commands.cargo;
        if !resume {
            info!("Building project...");
            let output = Command::new(&cargo)
                .arg("afl").arg("build")
                .current_dir(&self.path)
                .output().await?;
            if !output.status.success() {
                return Err(Error::CommandError(format!("AFL build failed: {}", String::from_utf8_lossy(&output.stderr))));
            }
            info!("Build completed successfully");
            info!("Starting AFL fuzzing for {} seconds", timeout);
        } else {
            info!("Resuming AFL fuzzing for {} seconds", timeout);
        }

        // let mut fuzz_process = Command::new(&cargo)
        let mut fuzz_process = Command::new(&cargo);
        fuzz_process.arg("afl").arg("fuzz")
            .arg("-V").arg(timeout.to_string());
        if resume {
            fuzz_process.arg("-i").arg("-");
        } else {
            fuzz_process.arg("-i").arg(relative_input_dir);
        }
        let fuzz_process = fuzz_process.arg("-o").arg(relative_output_dir).arg(format!("target/debug/{}", project_name))
            .current_dir(&self.path)
            .stdout(std::process::Stdio::inherit())
            .stderr(std::process::Stdio::inherit())
            .spawn()?
            .wait_with_output().await?;
        let status = &fuzz_process.status;
        if !status.success() {
            return Err(Error::CommandError(format!("AFL fuzzing failed with status: {}", status)));
        }
        info!("AFL fuzzing completed");

        Ok(())
    }
}