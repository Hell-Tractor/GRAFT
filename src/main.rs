mod utils;
mod llm;
mod error;

#[tokio::main]
async fn main() {
    let llm_model = utils::config().get_string("LLM.model_name").unwrap();
    let llm_api_url = utils::config().get_string("LLM.api_url_gen").unwrap();
    let llm_client = llm::LLMClient::new(&llm_model, &llm_api_url);
    let prompt = "Explain the theory of relativity in simple terms.";
    println!("Sending prompt to LLM: {}", prompt);
    println!("LLM response:\n {:?}", llm_client.generate_response(prompt).await.map(|res| res.response));
}