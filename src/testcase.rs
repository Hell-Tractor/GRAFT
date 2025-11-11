use std::fs::File;
use std::io::Read;
use std::path::Path;
use tracing::{debug, info};

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
        // create path if not exists
        if !path.exists() {
            std::fs::create_dir_all(path)?;
        }

        // create project
        let cargo = utils::config().get_string("commands.cargo")?;
        std::process::Command::new(&cargo)
            .arg("new").arg(name).arg("--vcs").arg("none")
            .current_dir(path)
            .output()?;
        
        // generate testcase
        let api_doc = get_api_documentation(target_crate)?;
        let main_path = path.join(name).join("src/main.rs");
        let cargo_path = path.join(name).join("Cargo.toml");
        Testcase::generate(name, &main_path, &cargo_path, &api_doc, &mut llm).await?;

        Ok(Testcase { path: path.join(name).into(), target_crate: target_crate.to_string() })
    }

    async fn generate(name: &str, main_path: &Path, cargo_path: &Path, api_doc: &str, llm: &mut LLMClient) -> Result<()>{
        let prompt_path = utils::config().get_string("prompts.testcase_gen")?;
        // read prompt template
        let mut prompt = String::new();
        let mut file = File::open(prompt_path)?;
        file.read_to_string(&mut prompt)?;

        // read fix prompt template
        let fix_prompt_path = utils::config().get_string("prompts.fix_testcase")?;
        let mut fix_prompt = String::new();
        let mut fix_file = File::open(fix_prompt_path)?;
        fix_file.read_to_string(&mut fix_prompt)?;
        let fix_prompt = fix_prompt;

        prompt = prompt.replace("{api_doc}", api_doc);
        let cargo = utils::config().get_string("commands.cargo")?;
        let retries = utils::config().get_int("LLM.fix_retries")? as u32;
        for i in 0..retries {
            let response = llm.generate_response(&prompt).await?;
            let Ok(code_and_deps) = serde_json::from_str::<CodeAndDependencies>(&response.message.content) else {
                info!("LLM response of project `{}` is not valid JSON, retrying...({} time(s) left)", name, retries - i - 1);
                continue;
            };
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
        todo!()
    }
}