use std::{path::PathBuf, sync::{Arc, RwLock}};

use clap::{ArgGroup, Args, Parser, Subcommand};
use tokio::task::JoinSet;
use tracing::{debug, enabled, error, info, warn};
use tracing_subscriber::{EnvFilter, fmt::writer::MakeWriterExt};

use crate::error::Result;

#[macro_use]
mod utils;
mod llm;
mod error;
mod testcase;
mod document;
mod generic_bound;
mod api_graph;

#[derive(Parser)]
#[command(version, about, long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Generate API documentation for a crate
    Doc(DocArgs),
    /// Generate API graph from API documentation
    ApiGraph {
        /// Input API documentation file in JSON format
        #[arg(short, long, value_name = "FILE")]
        input: PathBuf,
        /// Output API graph file in DOT format(based on extension)
        #[arg(short, long, value_name = "FILE")]
        output: PathBuf,
    },
    /// Generate API sequences from API graph
    ApiSequence {
        /// Input API documentation file in JSON format
        #[arg(short, long, value_name = "FILE")]
        input: PathBuf,
        /// Output file in JSON format for generated API sequences
        #[arg(short, long, value_name = "FILE")]
        output: PathBuf,
        /// Number of API sequences to generate.
        /// Suggested to be same with cpu cores for better performance.
        #[arg(short, long, default_value_t = 10)]
        count: usize,
    },
    /// Convert API sequences to executable testcases
    Testcase {
        /// Crate name to analyze, should be available on crates.io
        crate_name: String,
        /// Input API sequenecs file in JSON format
        #[arg(short, long, value_name = "FILE")]
        input: PathBuf,
        /// Output directory for generated testcases
        #[arg(short, long, value_name = "DIR")]
        output: PathBuf,
    },
    /// Generate seed inputs for all testcases
    SeedGen {
        /// Crate name to analyze, should be available on crates.io
        crate_name: String,
        /// Testcase directory
        dir: PathBuf,
        /// Number of testcases to generate seed inputs for
        #[arg(short, long)]
        count: usize,
        /// Number of seeds to generate for each testcase
        #[arg(short, long)]
        seed_count: usize,
    },
    /// Fuzz all testcases with AFL
    Fuzz {
        /// Crate name to analyze, should be available on crates.io
        crate_name: String,
        /// Testcase directory
        dir: PathBuf,
        /// Number of testcases to fuzz
        #[arg(short, long)]
        count: usize,
        /// Resume previous fuzzing session. Error if no previous session found.
        #[arg(short, long)]
        resume: bool,
        /// Fuzzing task execution time(in seconds).
        #[arg(short, long)]
        timeout: u64,
    },
}

#[derive(Args)]
#[command(group(
    ArgGroup::new("input")
        .required(true)
        .args(["name", "path"]),
))]
struct DocArgs {
    /// Crate name to analyze, should be available on crates.io
    #[arg(short, long)]
    name: Option<String>,
    /// Path to local Rust Doc JSON file
    #[arg(short, long)]
    path: Option<PathBuf>,
    /// Output file path for the generated API documentation in JSON format
    #[arg(short, long, value_name = "FILE")]
    output: PathBuf,
}

#[tokio::main]
async fn main() {
    let cli = Cli::parse();

    init_logger();

    let result = match &cli.command {
        Commands::Doc(args) => {
            if let Some(crate_name) = &args.name {
                generate_doc(crate_name, &args.output)
            } else if let Some(path) = &args.path {
                generate_doc_from_file(path, &args.output)
            } else {
                unreachable!("Either crate name or path must be provided");
            }
        },
        Commands::ApiGraph { input, output } => generate_api_graph(input, output),
        Commands::ApiSequence { input, output, count } => generate_api_sequence(input, output, *count),
        Commands::Testcase { crate_name, input, output } => generate_testcases(crate_name, input, output).await,
        Commands::SeedGen { crate_name, dir, count, seed_count } => generate_seed_inputs(crate_name, dir, *count, *seed_count).await,
        Commands::Fuzz { crate_name, dir, count, resume, timeout } => fuzz(crate_name, dir, *count, *resume, *timeout).await,
    };

    if let Err(e) = result {
        error!("Error occurred: {}", e);
    }
}

fn generate_doc(crate_name: &str, output: &PathBuf) -> Result<()> {
    info!("Generating API documentation for crate `{}`", crate_name);
    let api_doc = document::APIDoc::from_crate_name(crate_name)?
        .filter_api().filter_types()
        .build();
    info!("Generation completed. Serializing to `{}`...", output.display());
    let json = api_doc.to_string();
    std::fs::write(output, &json)?;
    info!("Done.");
    Ok(())
}

fn generate_doc_from_file(path: &PathBuf, output: &PathBuf) -> Result<()> {
    info!("Generating API documentation from file `{}`", path.display());
    let api_doc = document::APIDoc::from_file(path)?
        .filter_api().filter_types()
        .build();
    info!("Generation completed. Serializing to `{}`...", output.display());
    let json = api_doc.to_string();
    std::fs::write(output, &json)?;
    info!("Done.");
    Ok(())
}

fn generate_api_graph(input: &PathBuf, output: &PathBuf) -> Result<()> {
    info!("Generating API graph from file `{}`", input.display());
    let reader = std::io::BufReader::new(std::fs::File::open(input)?);
    let api_doc: document::APIDoc = serde_json::from_reader(reader)?;
    let graph = api_graph::APIGraph::from(api_doc);
    info!("API graph generation completed. Serializing to `{}`...", output.display());
    let mut writer = std::io::BufWriter::new(std::fs::File::create(output)?);
    graph.to_dot_file(&mut writer)?;
    info!("Done.");
    Ok(())
}

fn generate_api_sequence(input: &PathBuf, output: &PathBuf, count: usize) -> Result<()> {
    info!("Initializing API sequence generator...");
    let api_graph = {
        let reader = std::io::BufReader::new(std::fs::File::open(input)?);
        let api_doc: document::APIDoc = serde_json::from_reader(reader)?;
        api_graph::APIGraph::from(api_doc)
    };
    let mut generator = api_graph::algorithm::APISequeceGenerator::new(&api_graph);
    info!("API sequence generator initialized.");
    info!("Generating api sequences...");
    let mut api_sequences = vec![];
    for i in 0..count {
        let seq = generator.generate();
        if enabled!(tracing::Level::DEBUG) {
            let debug_file = format!("debug/api_sequence_{}.txt", i);
            let mut debug_file = std::io::BufWriter::new(std::fs::File::create(debug_file)?);
            for api in &seq {
                let api = api.read().unwrap();
                use std::io::Write;
                writeln!(debug_file, "{}", api.signature).unwrap();
            }
        }
        api_sequences.push(seq);
    }
    info!("API sequence generation completed. Serializing to `{}`...", output.display());
    let json = serde_json::to_string(&api_sequences)?;
    std::fs::write(output, &json)?;
    info!("Done.");
    Ok(())
}

async fn generate_testcases(crate_name: &str, input: &PathBuf, output: &PathBuf) -> Result<()> {
    info!("Converting API sequences to testcases...");
    let api_sequences: Vec<Vec<Arc<RwLock<api_graph::API>>>> = {
        let reader = std::io::BufReader::new(std::fs::File::open(input)?);
        serde_json::from_reader(reader)?
    };

    let llm_type = &utils::config().llm.type_;
    let llm_model = &utils::config().llm.model_name;
    let llm_api_url = &utils::config().llm.api_url_chat;
    let mut testcases = Vec::new();
    let api_sequence_count = api_sequences.len();

    let mut task_set = JoinSet::new();
    for (i, api_sequence) in api_sequences.into_iter().enumerate() {
        info!("Converting API sequence {} / {} to testcase...", i + 1, api_sequence_count);
        let name = format!("{}_test{}", crate_name, i);
        let output = output.clone();
        let crate_name = crate_name.to_string();
        task_set.spawn(async move {
            let llm = llm::LLMFactory::create_llm(&llm_type, &llm_model, &llm_api_url);
            let tc = testcase::Testcase::new(
                &output,
                &name,
                &crate_name,
                &api_sequence,
                llm,
            ).await;
            (tc, i)
        });
        info!("API sequence {} / {} converted to testcase.", i + 1, api_sequence_count);
    }
    let mut successed_count = 0;
    while let Some(res) = task_set.join_next().await {
        let (tc, i) = res.expect("JoinError occurred in JoinSet");
        match tc {
            Ok(tc) => {
                testcases.push(tc);
                successed_count += 1;
                info!("Testcase for API sequence(id = {}) generated successfully.", i);
            },
            Err(e) => {
                warn!("Error occurred while generating testcase for API sequence {}: {}, skipping...", i, e);
            }
        }
    }
    info!("{} / {} API sequences converted to testcases.", successed_count, api_sequence_count);
    info!("Done.");

    Ok(())
}

async fn generate_seed_inputs(crate_name: &str, dir: &PathBuf, count: usize, seed_count: usize) -> Result<()> {
    info!("Generating inputs for all testcases");
    let testcases = (0..count).map(|i| {
        let testcase_dir = dir.join(format!("{}_test{}", crate_name, i));
        testcase::Testcase::from_existing(&testcase_dir, crate_name)
    }).collect::<Vec<testcase::Testcase>>();

    let input_dir = std::path::Path::new("inputs");
    let llm_type = &utils::config().llm.type_;
    let llm_model = &utils::config().llm.model_name;
    let llm_api_url = &utils::config().llm.api_url_chat;
    let mut task_set = JoinSet::new();
    for (i, testcase) in testcases.into_iter().enumerate() {
        info!("Generating inputs for testcase {} / {}...", i + 1, count);
        task_set.spawn(async move {
            let llm_client = llm::LLMFactory::create_llm(&llm_type, &llm_model, &llm_api_url);
            let res = testcase.generate_inputs(input_dir, llm_client, seed_count).await;
            (res, i)
        });
    }
    let mut successed_count = 0;
    while let Some(res) = task_set.join_next().await {
        let (res, id) = res.expect("JoinError occurred in JoinSet");
        if let Err(e) = res {
            warn!("Error occurred while generating inputs for testcase(id = {}): {}, skipping...", id, e);
        } else {
            successed_count += 1;
            info!("Input generation for testcase(id = {}) completed successfully.", id);
        }
    }
    info!("Input generation completed for all testcases: {}/{} succeeded.", successed_count, count);
    Ok(())
}

async fn fuzz(crate_name: &str, dir: &PathBuf, count: usize, resume: bool, timeout: u64) -> Result<()> {
    info!("Running all testcases with AFL");
    let testcases = (0..count).map(|i| {
        let testcase_dir = dir.join(format!("{}_test{}", crate_name, i));
        testcase::Testcase::from_existing(&testcase_dir, crate_name)
    }).collect::<Vec<testcase::Testcase>>();

    let input_dir = std::path::Path::new("inputs");
    let output_dir = std::path::Path::new("outputs");
    let mut fuzz_task_set = JoinSet::new();
    for (i, testcase) in testcases.into_iter().enumerate() {
        info!("Starting testcase {} / {}...", i + 1, count);
        fuzz_task_set.spawn(async move { (testcase.run(input_dir, output_dir, resume, timeout).await, i) });
    }
    let mut success_count = 0;
    while let Some(res) = fuzz_task_set.join_next().await {
        let (res, id) = res.expect("JoinError occurred in JoinSet");
        if let Err(e) = res {
            warn!("Error occurred while running testcase(id = {}): {}, skipping...", id, e);
        } else {
            info!("Testcase(id = {}) completed successfully.", id);
            success_count += 1;
        }
    }
    info!("{}/{} testcases ran successfully with AFL", success_count, count);
    Ok(())
}

fn init_logger() {
    let file_appender = tracing_appender::rolling::daily("logs", "latest.log");
    let (file_writer, guard) = tracing_appender::non_blocking(file_appender);
    std::mem::forget(guard);
    let subscriber = tracing_subscriber::fmt()
        .with_writer(file_writer.and(std::io::stderr))
        .with_max_level(tracing::Level::INFO)
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("PLFuzz=info")))
        .finish();
    tracing::subscriber::set_global_default(subscriber).expect("Failed to set global subscriber");
    debug!("Logger initialized");
}