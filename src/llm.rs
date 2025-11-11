use crate::error::Result;

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

pub struct LLMClient {
    model_name: String,
    api_url_chat: String,
    client: reqwest::Client,
    messages: Vec<LLMMessage>,
}

#[derive(serde::Deserialize, Debug)]
#[allow(dead_code)]
pub struct LLMResponse {
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

    pub async fn generate_response(&mut self, prompt: &str) -> Result<LLMResponse> {
        self.messages.push(LLMMessage::user(prompt));
        let request_body = serde_json::json!({
                "model": self.model_name,
                "messages": self.messages,
                "stream": false,
                "think": true,
                "format": "json",
            });
        let resp: LLMResponse = self.client.post(&self.api_url_chat)
            .json(&request_body).send()
            .await?
            .json()
            .await?;
        self.messages.push(resp.message.clone());
        Ok(resp)
    }
}