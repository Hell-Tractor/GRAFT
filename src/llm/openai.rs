use async_trait::async_trait;

use crate::error::Result;
use super::{LLM, LLMMessage};

pub struct LLMClient;

impl LLMClient {
    pub fn new(_model_name: &str, _api_url_gen: &str) -> Self {
        LLMClient
    }
}

#[async_trait]
impl LLM for LLMClient {
    async fn generate_response(&mut self, _prompt: &str) -> Result<LLMMessage> {
        unimplemented!()
    }
    fn clear(&mut self) {
        // no-op
    }
}