use tracing::{error, info, span, warn};
use tracing_subscriber::{EnvFilter, fmt::writer::MakeWriterExt};

use crate::{document::get_api_documentation, error::Result};

mod utils;
mod llm;
mod error;
mod testcase;
mod document;

#[tokio::main]
async fn main() {
    let _ = init_logger();
    test().await;

    // const TARGET_CRATE: &str = "base64";
    // if let Err(e) = test_crate(TARGET_CRATE).await {
    //     error!("Error occurred: {}", e);
    // }
}

#[allow(unused)]
async fn test() {
    let result = get_api_documentation("base64");
    if let Err(e) = result {
        println!("Error: {}", e);
    } else {
        println!("Documentation fetched successfully");
        let mut result = result.unwrap();
        // save to temp.html
        std::fs::write("temp.html", &result).unwrap();
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
        span!(tracing::Level::INFO, "Testcase", testcase_index = i + 1, total_testcases = count, crate = crate_name);
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
    for testcase in &testcases {
        testcase.generate_inputs(std::path::Path::new("inputs"), llm::LLMClient::new(&llm_model, &llm_api_url)).await?;
    }
    info!("Input generation completed for all testcases");
    Ok(())
}

fn init_logger() -> tracing_appender::non_blocking::WorkerGuard {
    let file_appender = tracing_appender::rolling::daily("logs", "latest.log");
    let (file_writer, guard) = tracing_appender::non_blocking(file_appender);
    let subscriber = tracing_subscriber::fmt()
        .with_writer(file_writer.and(std::io::stderr))
        .with_max_level(tracing::Level::DEBUG)
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("PLFuzz=debug")))
        .finish();
    tracing::subscriber::set_global_default(subscriber).expect("Failed to set global subscriber");
    info!("Logger initialized");
    guard
}