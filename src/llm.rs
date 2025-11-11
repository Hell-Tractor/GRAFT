use crate::error::Result;

pub struct LLMClient {
    model_name: String,
    api_url_gen: String,
    client: reqwest::Client,
}

#[derive(serde::Deserialize)]
#[allow(dead_code)]
pub struct LLMResponse {
    pub model: String,
    pub created_at: String,
    pub response: String,
    pub thinking: String,
    pub done: bool,
    pub done_reason: String,
    pub context: Vec<u32>,
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
            api_url_gen: api_url_gen.to_string(),
            client: reqwest::Client::new(),
        }
    }

    pub async fn generate_response(&self, prompt: &str) -> Result<LLMResponse> {
        let resp = self.client.post(&self.api_url_gen)
            .json(&serde_json::json!({
                "model": self.model_name,
                "prompt": prompt,
                "stream": false
            })).send()
            .await?
            .json()
            .await?;
        Ok(resp)
    }
}