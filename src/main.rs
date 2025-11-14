use tracing::{error, info, span, warn};
use tracing_subscriber::{EnvFilter, fmt::writer::MakeWriterExt};

use crate::{document::{api, get_api_documentation}, error::Result};

mod utils;
mod llm;
mod error;
mod testcase;
mod document;

#[tokio::main]
async fn main() {
    init_logger();
    // test().await;

    const TARGET_CRATE: &str = "base64";
    if let Err(e) = test_crate(TARGET_CRATE).await {
        error!("Error occurred: {}", e);
    }
}

#[allow(unused)]
async fn test() {
    let result = get_api_documentation("base64");
    if let Err(e) = result {
        println!("Error: {}", e);
    } else {
        println!("Documentation fetched successfully");
        let mut result = result.unwrap();
        // std::fs::write("temp.json", &result).unwrap();
        let apis = api::API::from_api_doc(&result).unwrap();
        let json = serde_json::to_string_pretty(&apis).unwrap();
        std::fs::write("temp_apis.json", &json).unwrap();
    }
}

#[allow(unused)]
async fn test_crate(crate_name: &str) -> Result<()> {
    info!("Preparing to generate testcases for crate {}", crate_name);
    let count = utils::config().get_int("testcase.count")? as u32;
    let base_path = utils::config().get_string("testcase.base_path")?;
    let base_path = std::path::Path::new(&base_path);
    let llm_model = utils::config().get_string("LLM.model_name")?;
    let llm_api_url = utils::config().get_string("LLM.api_url_chat")?;

    info!("Preparation complete, starting testcase generation for crate {}", crate_name);
    let mut testcases = vec![];
    for i in 0..count {
        info!("Generating testcase {}/{} for crate {}", i + 1, count, crate_name);
        let span = span!(tracing::Level::INFO, "Testcase", testcase_index = i + 1, total_testcases = count, crate = crate_name);
        let _ = span.enter();
        let llm_client = llm::LLMClient::new(&llm_model, &llm_api_url);        
        let testcase = testcase::Testcase::new(base_path, &format!("{}_{}", crate_name, i), crate_name, llm_client).await;
        if let Err(e) = &testcase {
            warn!("Error occurred while generating testcase: {}, skipping...", e);
            continue;
        }
        testcases.push(testcase.unwrap());
    }
    info!("{} testcases generated successfully for crate {}",testcases.len(), crate_name);
    let merged = testcase::Testcase::merge_from(&testcases, llm::LLMClient::new(&llm_model, &llm_api_url)).await?;
    testcases.push(merged);

    info!("Generating inputs for all testcases");
    let input_dir = std::path::Path::new("inputs");
    let output_dir = std::path::Path::new("outputs");
    for testcase in &testcases {
        testcase.generate_inputs(input_dir, llm::LLMClient::new(&llm_model, &llm_api_url)).await?;
    }
    info!("Input generation completed for all testcases");

    info!("Running all testcases with AFL");
    let mut success_count = 0;
    for testcase in &testcases {
        if let Err(e) = testcase.run(input_dir, output_dir) {
            warn!("Error occurred while running testcase: {}, skipping...", e);
        } else {
            success_count += 1;
        }
    }
    info!("{}/{} testcases ran successfully with AFL", success_count, testcases.len());

    Ok(())
}

fn init_logger() {
    let file_appender = tracing_appender::rolling::daily("logs", "latest.log");
    let (file_writer, guard) = tracing_appender::non_blocking(file_appender);
    std::mem::forget(guard);
    let subscriber = tracing_subscriber::fmt()
        .with_writer(file_writer.and(std::io::stderr))
        .with_max_level(tracing::Level::DEBUG)
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("PLFuzz=debug")))
        .finish();
    tracing::subscriber::set_global_default(subscriber).expect("Failed to set global subscriber");
    info!("Logger initialized");
}