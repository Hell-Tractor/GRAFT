use std::{path::PathBuf, sync::{Arc, RwLock}};

use clap::Parser;
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use tokio::{sync::Semaphore, task::JoinSet};
use tracing::{enabled, error, info, warn};
use tracing_indicatif::span_ext::IndicatifSpanExt;

use crate::error::Result;

#[macro_use]
mod utils;
mod llm;
mod error;
mod testcase;
mod document;
mod generic_bound;
mod api_graph;
mod cli;
mod log;

#[tokio::main]
async fn main() {
    let cli = cli::Cli::parse();

    let _guard = log::Logger::init();

    use cli::Commands;
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
        Commands::Measure { crate_name } => measure_crate(crate_name),
        Commands::ApiSequence { input, output, count } => generate_api_sequence(input, output, count),
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

fn measure_crate(crate_name: &str) -> Result<()> {
    info!("Measuring crate `{}` for possible max API coverage", crate_name);
    let api_doc = document::APIDoc::from_crate_name(crate_name)?
        .filter_api().filter_types()
        .build();
    let api_graph = api_graph::APIGraph::from(api_doc);
    let generator = api_graph::algorithm::APISequeceGenerator::new(&api_graph);
    let (max_covered, total) = generator.max_api_coverage();
    info!("Max API coverage for crate `{}`: {}/{} ({:.2}%)", crate_name, max_covered, total, max_covered as f64 / total as f64 * 100.0);
    Ok(())
}

fn generate_api_sequence_once(id: usize, generator: &mut api_graph::algorithm::APISequeceGenerator) -> Vec<Arc<RwLock<api_graph::API>>> {
    let seq = generator.generate();
    if enabled!(tracing::Level::DEBUG) {
        let debug_file = format!("debug/api_sequence_{}.txt", id);
        let mut debug_file = std::io::BufWriter::new(std::fs::File::create(debug_file).expect("Failed to create debug file"));
        for api in &seq {
            let api = api.read().unwrap();
            use std::io::Write;
            writeln!(debug_file, "{}", api.signature).unwrap();
        }
    }
    seq
}

fn generate_api_sequence(input: &PathBuf, output: &PathBuf, count: &Option<usize>) -> Result<()> {
    info!("Initializing API sequence generator...");
    let api_graph = {
        let reader = std::io::BufReader::new(std::fs::File::open(input)?);
        let api_doc: document::APIDoc = serde_json::from_reader(reader)?;
        api_graph::APIGraph::from(api_doc)
    };
    let mut generator = api_graph::algorithm::APISequeceGenerator::new(&api_graph);
    info!("API sequence generator initialized.");
    let (max_covered, total) = generator.max_api_coverage();
    info!("Max API coverage: {}/{} ({:.2}%)", max_covered, total, max_covered as f64 / total as f64 * 100.0);
    info!("Generating api sequences...");
    let mut api_sequences = vec![];
    if let Some(count) = count {
        for i in 0..*count {
            let seq = generate_api_sequence_once(i, &mut generator);
            api_sequences.push(seq);
        }
    } else {
        while generator.suggest_generate() {
            let seq = generate_api_sequence_once(api_sequences.len(), &mut generator);
            api_sequences.push(seq);
        }
    }
    info!("API sequence(count = {}) generation completed. Serializing to `{}`...", api_sequences.len(), output.display());
    println!("{}", api_sequences.len());
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

    let span = tracing::info_span!("Generate Testcases", crate_name, api_sequence_count);
    span.pb_set_length(api_sequence_count as u64);
    span.pb_set_style(&log::pb_style());
    let _enter = span.enter();

    let mut task_set = JoinSet::new();
    for (i, api_sequence) in api_sequences.into_iter().enumerate() {
        // info!("Converting API sequence {} / {} to testcase...", i + 1, api_sequence_count);
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
        // info!("API sequence {} / {} converted to testcase.", i + 1, api_sequence_count);
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
        span.pb_inc(1);
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

    let span = tracing::info_span!("Seed Inputs", crate_name, count, seed_count);
    span.pb_set_length(count as u64);
    span.pb_set_style(&log::pb_style());
    let _enter = span.enter();

    let mut task_set = JoinSet::new();
    for (i, testcase) in testcases.into_iter().enumerate() {
        // info!("Generating inputs for testcase {} / {}...", i + 1, count);
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
        span.pb_inc(1);
    }
    info!("Input generation completed for all testcases: {}/{} succeeded.", successed_count, count);
    Ok(())
}

async fn fuzz(crate_name: &str, dir: &PathBuf, count: usize, resume: bool, timeout: u64) -> Result<()> {
    info!("Running all testcases with AFL");
    let multi_pb = MultiProgress::new();
    let summary_pb = multi_pb.add(ProgressBar::new(count as u64));
    summary_pb.set_style(
        ProgressStyle::with_template(
            " {prefix:.bold}: [{bar:40.green/white}] {pos}/{len} ({percent}%) | {msg}"
        )
        .unwrap()
        .progress_chars("#>-")
    );
    summary_pb.set_prefix("Global Progress");
    summary_pb.set_message("Starting afl fuzz...");

    let max_concurrent = std::thread::available_parallelism()?.get();
    let semaphore = Arc::new(Semaphore::new(max_concurrent));
    info!("Max concurrent fuzzing tasks set to {}", max_concurrent);

    let testcases = (0..count).map(|i| {
        let testcase_dir = dir.join(format!("{}_test{}", crate_name, i));
        testcase::Testcase::from_existing(&testcase_dir, crate_name)
            .with_pb(multi_pb.clone()/* , summary_pb.clone() */)
    }).collect::<Vec<testcase::Testcase>>();

    let input_dir = std::path::Path::new("inputs");
    let output_dir = std::path::Path::new("outputs");
    let mut fuzz_task_set = JoinSet::new();
    for (i, testcase) in testcases.into_iter().enumerate() {
        // info!("Starting testcase {} / {}...", i + 1, count);
        let sem = semaphore.clone();
        fuzz_task_set.spawn(async move {
            let _permit = sem.acquire().await.expect("Failed to acquire semaphore permit");
            let res = testcase.run(input_dir, output_dir, resume, timeout).await;
            (res, i)
        });
    }
    summary_pb.set_message("Fuzzing in progress...");
    let mut success_count = 0;
    while let Some(res) = fuzz_task_set.join_next().await {
        let (res, id) = res.expect("JoinError occurred in JoinSet");
        if let Err(e) = res {
            warn!("Error occurred while running testcase(id = {}): {}, skipping...", id, e);
        } else {
            info!("Testcase(id = {}) completed successfully.", id);
            success_count += 1;
        }
        summary_pb.inc(1);
    }
    summary_pb.finish_with_message("Fuzzing completed.");
    info!("{}/{} testcases ran successfully with AFL", success_count, count);
    Ok(())
}