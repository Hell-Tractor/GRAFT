use crate::error::Result;
use async_trait::async_trait;

pub mod local;
pub mod openai;

#[async_trait]
pub trait LLM {
    async fn generate_response(&mut self, prompt: &str) -> Result<LLMMessage>;
    fn clear(&mut self);
}

#[derive(Clone, Debug)]
pub enum LLMType {
    Local,
    OpenAI,
}

impl TryFrom<&str> for LLMType {
    type Error = crate::error::Error;

    fn try_from(value: &str) -> std::result::Result<Self, Self::Error> {
        match value.to_lowercase().as_str() {
            "local" => Ok(LLMType::Local),
            "openai" => Ok(LLMType::OpenAI),
            _ => Err(crate::error::Error::UnsupportedLLMType(value.into())),
        }
    }
}

pub struct LLMFactory;

impl LLMFactory {
    pub fn create_llm(llm_type: &LLMType, model_name: &str, api_url_gen: &str) -> Box<dyn LLM> {
        match llm_type {
            LLMType::Local => Box::new(local::LLMClient::new(model_name, api_url_gen)),
            LLMType::OpenAI => Box::new(openai::LLMClient::new(model_name, api_url_gen)),
        }
    }
}

#[derive(serde::Deserialize, serde::Serialize, Clone, Debug)]
pub struct LLMMessage {
    pub role: String,
    pub content: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub thinking: Option<String>,
}

impl LLMMessage {
    pub fn user(content: &str) -> Self {
        LLMMessage {
            role: "user".to_string(),
            content: content.to_string(),
            thinking: None,
        }
    }
}