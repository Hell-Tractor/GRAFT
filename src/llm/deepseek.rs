use async_trait::async_trait;
use serde::Deserialize;
use tracing::{debug, enabled, info};

use crate::{error::{Error, Result}, llm::{LLM, LLMMessage}, utils::config};

pub struct LLMClient {
    model_name: String,
    api_url_chat: String,
    client: reqwest::Client,
    messages: Vec<LLMMessage>,
}

#[derive(Deserialize, Debug)]
#[allow(unused)]
struct LLMResponse {
    pub id: String,
    pub choices: Vec<LLMResponseChoice>,
}

#[derive(Deserialize, Debug)]
#[allow(unused)]
struct LLMResponseChoice {
    pub finish_reason: String,
    pub message: LLMMessage,
}

#[async_trait]
impl LLM for LLMClient {
    async fn generate_response(&mut self, prompt: &str) -> Result<LLMMessage> {
        if enabled!(tracing::Level::DEBUG) {
            debug!("Press Enter to request LLM...");
            std::io::stdin().read_line(&mut String::new()).unwrap();
        }

        self.messages.push(LLMMessage::user(prompt));
        let mut request_body = serde_json::json!({
                "model": self.model_name,
                "messages": self.messages,
                "stream": false,
                "temperature": 0.0,
            });
        if let Some(max_tokens) = config().llm.max_tokens {
            request_body["max_tokens"] = serde_json::json!(max_tokens);
        }
        info!("Requesting LLM with model `{}`, it may take some time...", self.model_name);
        let api_key = config().llm.api_key.as_ref()
            .expect("Missing API key for LLM service");
        let resp_str = self.client.post(&self.api_url_chat)
            .header("Authorization", format!("Bearer {}", api_key))
            .json(&request_body).send()
            .await?
            .text()
            .await?;
        let resp = serde_json::from_str::<LLMResponse>(&resp_str)
            .map_err(|_| Error::DeserializeError(resp_str))?;
        let message = resp.choices.into_iter()
            .next().ok_or(Error::LLMResponseNoContent)?.message;
        self.messages.push(message.clone_to_memory());
        Ok(message)
    }

    fn clear(&mut self) {
        debug!("Clearing LLM client messages");
        self.messages.clear();
    }
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