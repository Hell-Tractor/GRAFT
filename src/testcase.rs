use std::fs::File;
use std::io::Read;
use std::path::Path;
use tracing::{debug, info, span};
use tracing_subscriber::util;

use crate::document::get_api_documentation;
use crate::error::{Error, Result};

use crate::llm::LLMClient;
use crate::utils;

#[allow(unused)]
pub struct Testcase {
    path: Box<Path>,
    target_crate: String,
}

#[derive(serde::Deserialize)]
struct CodeAndDependencies {
    code: String,
    dependencies: String,
}

impl Testcase {
    pub async fn new(path: &Path, name: &str, target_crate: &str, mut llm: LLMClient) -> Result<Self> {
        let project_path = path.join(name);
        if path.exists() {
            std::fs::remove_dir_all(path)?;
        }
        std::fs::create_dir_all(path)?;

        // create project
        info!("Creating new cargo project `{}` at {}", name, project_path.display());
        let cargo = utils::config().get_string("commands.cargo")?;
        std::process::Command::new(&cargo)
            .arg("new").arg(name).arg("--vcs").arg("none")
            .current_dir(path)
            .output()?;
        info!("Project `{}` created successfully", name);
        
        // generate testcase
        info!("Generating testcase for project `{}`", name);
        let api_doc = get_api_documentation(target_crate)?;
        let main_path = project_path.join("src/main.rs");
        let cargo_path = project_path.join("Cargo.toml");
        Testcase::generate(name, &main_path, &cargo_path, &api_doc, &mut llm).await?;
        info!("Testcase for project `{}` generated successfully", name);

        Ok(Testcase { path: path.join(name).into(), target_crate: target_crate.to_string() })
    }

    async fn generate(name: &str, main_path: &Path, cargo_path: &Path, api_doc: &str, llm: &mut LLMClient) -> Result<()>{
        let prompt_path = utils::config().get_string("prompts.testcase_gen")?;
        // read prompt template
        let prompt = utils::read_file_to_string(prompt_path)?;

        // read fix prompt template
        let fix_prompt_path = utils::config().get_string("prompts.fix_testcase")?;
        let fix_prompt = utils::read_file_to_string(fix_prompt_path)?;

        let mut prompt = prompt.replace("{api_doc}", api_doc);
        let cargo = utils::config().get_string("commands.cargo")?;
        let retries = utils::config().get_int("LLM.fix_retries")? as u32;
        for i in 0..retries {
            let response = llm.generate_response(&prompt).await?;
            let code_and_deps = serde_json::from_str::<CodeAndDependencies>(&response.message.content);
            if let Err(e) = &code_and_deps {
                info!("LLM response of project `{}` is not valid JSON, retrying...({} time(s) left)", name, retries - i - 1);
                debug!("LLM response content: {}", response.message.content);
                debug!("JSON parsing error: {}", e);
                llm.clear();
                continue;
            };
            let code_and_deps = code_and_deps.unwrap();
            // write code and dependencies
            std::fs::write(main_path, code_and_deps.code)?;
            std::fs::write(cargo_path, code_and_deps.dependencies)?;

            // try to build the project
            let output = std::process::Command::new(&cargo)
                .arg("afl").arg("build")
                .current_dir(cargo_path.parent().unwrap())
                .output()?;
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

    #[allow(unused)]
    pub async fn merge_from(testcases: &[Testcase], mut llm: LLMClient) -> Result<Self> {
        info!("Merging {} testcases", testcases.len());
        info!("Successfully merged testcases");
        todo!()
    }

    pub async fn generate_inputs(&self, relative_dir: &Path, mut llm: LLMClient) -> Result<()> {
        // read prompt template
        let prompt_path = utils::config().get_string("prompts.input_gen")?;
        let prompt_template = utils::read_file_to_string(prompt_path)?;

        let input_count = utils::config().get_int("testcase.input_count")? as u32;

        // read code snippet from main.rs
        let path = self.path.join("src").join("main.rs");
        let code_snippet = utils::read_file_to_string(path)?;

        let prompt = prompt_template
            .replace("{crate_name}", &self.target_crate)
            .replace("{count}", &input_count.to_string())
            .replace("{code_snippet}", &code_snippet);

        let retries = utils::config().get_int("LLM.input_gen_retries")? as u32;
        for i in 0..retries {
            let response = llm.generate_response(&prompt).await?;
            let Ok(inputs) = serde_json::from_str::<Vec<String>>(&response.message.content) else {
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

    pub fn run(&self, relative_input_dir: &Path, relative_output_dir: &Path) -> Result<()> {
        let project_name = self.path.file_name().unwrap().to_string_lossy();
        let s = span!(tracing::Level::INFO, "Testcase", project = %project_name);
        let _ = s.enter();
        let timeout = utils::config().get_int("AFL.timeout")? as u32;
        let cargo = utils::config().get_string("commands.cargo")?;
        
        info!("Starting AFL fuzzing with timeout of {} seconds", timeout);
        let output = std::process::Command::new(&cargo)
            .arg("afl").arg("fuzz").arg("-i").arg(relative_input_dir)
            .arg("-o").arg(relative_output_dir).arg(format!("target/debug/{}", project_name))
            .env("AFL_TIMEOUT", timeout.to_string())
            .current_dir(&self.path)
            .output()?;
        if !output.status.success() {
            return Err(Error::CommandError(format!("AFL fuzzing failed: {}", String::from_utf8_lossy(&output.stderr))));
        }
        info!("AFL fuzzing completed");

        Ok(())
    }
}