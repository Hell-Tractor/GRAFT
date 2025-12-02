use tracing::{error, info, span, warn};
use tracing_subscriber::{EnvFilter, fmt::writer::MakeWriterExt};

use crate::{error::Result, llm::LLMType};

#[macro_use]
mod utils;
mod llm;
mod error;
mod testcase;
mod document;
mod generic_bound;
mod api_graph;

#[tokio::main]
async fn main() {
    init_logger();

    const TARGET_CRATE: &str = "base64";
    if let Err(e) = test_crate(TARGET_CRATE).await {
        error!("Error occurred: {}", e);
    }
}

#[allow(unused)]
async fn test() {
    let json = document::APIDoc::from_file(&std::path::Path::new("localdata/test_data/generic_bound_satisfy_check/rustdoc.json"))
        .unwrap().filter_api().filter_types()
        .build().to_string();
    std::fs::write("temp_apis.json", &json).unwrap();
}

#[allow(unused)]
async fn test_crate(crate_name: &str) -> Result<()> {
    info!("Preparing to generate testcases for crate {}", crate_name);
    let count = utils::config().get_int("testcase.count")? as u32;
    let base_path = utils::config().get_string("testcase.base_path")?;
    let base_path = std::path::Path::new(&base_path);
    let llm_model = utils::config().get_string("LLM.model_name")?;
    let llm_api_url = utils::config().get_string("LLM.api_url_chat")?;
    let llm_type: LLMType =  utils::config().get_string("LLM.type")?.as_str().try_into()?;
    info!("Preparation completed.");

    info!("Generating api graph...");
    let api_doc = document::APIDoc::from_crate_name(crate_name)?
        .filter_api().filter_types()
        .build();
    // write api_doc to temp_apis.json for debug
    let json = api_doc.to_string();
    std::fs::write("temp_apis.json", &json)?;
    let api_graph = api_graph::APIGraph::from(api_doc);
    info!("API graph generation completed.");

    let dot_file = "temp.dot";
    let mut dot_file = std::io::BufWriter::new(std::fs::File::create(dot_file)?);
    api_graph.to_dot_file(&mut dot_file);

    // info!("Generating inputs for all testcases");
    // let input_dir = std::path::Path::new("inputs");
    // let output_dir = std::path::Path::new("outputs");
    // for testcase in &testcases {
    //     let llm_client = llm::LLMFactory::create_llm(&llm_type, &llm_model, &llm_api_url);
    //     testcase.generate_inputs(input_dir, llm_client).await?;
    // }
    // info!("Input generation completed for all testcases");

    // info!("Running all testcases with AFL");
    // let mut success_count = 0;
    // for testcase in &testcases {
    //     if let Err(e) = testcase.run(input_dir, output_dir) {
    //         warn!("Error occurred while running testcase: {}, skipping...", e);
    //     } else {
    //         success_count += 1;
    //     }
    // }
    // info!("{}/{} testcases ran successfully with AFL", success_count, testcases.len());

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