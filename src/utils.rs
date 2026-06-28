use std::sync::OnceLock;

use serde::Deserialize;

use crate::{document::APIDocParsers, llm::LLMType};

#[derive(Deserialize)]
pub struct Config {
    #[serde(rename = "LLM")]
    pub llm: LLMConfig,
    pub prompts: PromptConfig,
    pub commands: CommandConfig,
    pub testcase: TestcaseConfig,
    pub doc: DocConfig,
    pub strict_mode: bool,
    // pub querys: QueryConfig,
}

#[derive(Deserialize)]
pub struct LLMConfig {
    #[serde(rename = "type")]
    pub type_: LLMType,
    pub model_name: String,
    pub api_url_chat: String,
    pub fix_retries: u32,
    pub input_gen_retries: u32,
    #[serde(default)]
    pub api_key: Option<String>,
    pub concurrent_tasks: usize,
    #[serde(default)]
    pub max_tokens: Option<u32>,
    #[serde(default)]
    pub enable_thinking: Option<bool>,
    /// Maximum thinking tokens for Qwen3 models. Only used when enable_thinking is true.
    /// Prevents thinking loops without disabling reasoning entirely.
    #[serde(default)]
    pub thinking_budget: Option<u32>,
}

#[derive(Deserialize)]
pub struct PromptConfig {
    pub testcase_gen: String,
    pub fix_testcase: String,
    pub input_gen: String,
}

#[derive(Deserialize)]
pub struct CommandConfig {
    pub cargo: String,
}

#[derive(Deserialize)]
pub struct TestcaseConfig {
    // pub input_count: u32,
    pub each_api_max_usage: u32,
    pub api_least_coverage: u32,
}

#[derive(Deserialize)]
pub struct DocConfig {
    pub temp_project_path: String,
    pub temp_project_name: String,
    pub parser: APIDocParsers,
}

// #[derive(Deserialize)]
// pub struct QueryConfig {
//     pub function: String,
//     pub method: String,
//     #[serde(rename = "struct")]
//     pub struct_: String,
//     #[serde(rename = "enum")]
//     pub enum_: String,
//     pub constant: String,
// }

pub fn config() -> &'static Config {
    static CONFIG: OnceLock<Config> = OnceLock::new();
    CONFIG.get_or_init(|| {
        let mut config: Config = config::Config::builder()
            .add_source(config::File::with_name("config.yaml"))
            .build().unwrap()
            .try_deserialize().unwrap();
        let env_config = config::Config::builder()
            .add_source(config::Environment::with_prefix("PLF").separator("_"))
            .build().unwrap();
        config.llm.api_key = env_config.get_string("api.key").ok();

        config
    })
}

pub fn read_file_to_string<P>(path: P) -> std::io::Result<String>
    where P: AsRef<std::path::Path> {
    let mut file = std::fs::File::open(path)?;
    let mut contents = String::new();
    use std::io::Read;
    file.read_to_string(&mut contents)?;
    Ok(contents)
}

#[macro_export]
macro_rules! is_same_len {
    ($first: expr $(, $rest: expr)* $(,)?) => {
        {
            let len = $first.len();
            true $(&& len == $rest.len())*
        }
    };
}