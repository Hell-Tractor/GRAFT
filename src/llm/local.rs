use async_trait::async_trait;
use tracing::{debug, enabled, info};

use crate::{error::Result, llm::LLM};
use super::LLMMessage;

pub struct LLMClient {
    model_name: String,
    api_url_chat: String,
    client: reqwest::Client,
    messages: Vec<LLMMessage>,
}

#[derive(serde::Deserialize, Debug)]
#[allow(unused)]
struct LLMResponse {
    pub model: String,
    pub created_at: String,
    pub message: LLMMessage,
    pub done: bool,
    pub done_reason: String,
    pub total_duration: u64,
    pub load_duration: u64,
    pub prompt_eval_count: u32,
    pub prompt_eval_duration: u64,
    pub eval_count: u32,
    pub eval_duration: u64,
}

impl LLMClient {
    pub fn new(model_name: &str, api_url_gen: &str) -> Self {
        LLMClient {
            model_name: model_name.to_string(),
            api_url_chat: api_url_gen.to_string(),
            client: reqwest::Client::new(),
            messages: Vec::new(),
        }
    }
}

#[async_trait]
impl LLM for LLMClient {
    async fn generate_response(&mut self, prompt: &str) -> Result<LLMMessage> {
        // debug!("Generating LLM response for prompt:\n{}", prompt);
        if enabled!(tracing::Level::DEBUG) {
            debug!("Press Enter to request LLM...");
            std::io::stdin().read_line(&mut String::new()).unwrap();
        }
        self.messages.push(LLMMessage::user(prompt));
        let request_body = serde_json::json!({
                "model": self.model_name,
                "messages": self.messages,
                "stream": false,
                // "think": true,
                "format": "json",
            });
        info!("Requesting LLM with model `{}`, it may take some time...", self.model_name);
        let resp: LLMResponse = self.client.post(&self.api_url_chat)
            .json(&request_body).send()
            .await?
            .json()
            .await?;
        // debug!("LLM response received: {}", resp);
        self.messages.push(resp.message.clone());
        debug!("messages updated, total {} messages", self.messages.len());
        Ok(resp.message)
    }

    fn clear(&mut self) {
        debug!("Clearing LLM client messages");
        self.messages.clear();
    }
}