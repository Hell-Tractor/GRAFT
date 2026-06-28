use std::sync::OnceLock;

use indicatif::ProgressStyle;
use tracing::info;
use tracing_appender::non_blocking::WorkerGuard;
use tracing_indicatif::IndicatifLayer;
use tracing_subscriber::{EnvFilter, layer::SubscriberExt, util::SubscriberInitExt};

pub struct Logger;

impl Logger {
    pub fn init() -> WorkerGuard {
        let file_appender = tracing_appender::rolling::daily("logs", "latest.log");
        let (file_writer, guard) = tracing_appender::non_blocking(file_appender);

        let indicatif_layer = IndicatifLayer::new();

        let file_layer = tracing_subscriber::fmt::layer()
            .with_writer(file_writer)
            .with_ansi(false)
            .with_target(true);

        let terminal_layer = tracing_subscriber::fmt::layer()
            .with_writer(indicatif_layer.get_stderr_writer())
            .with_target(true);

        tracing_subscriber::Registry::default()
            .with(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("graft=info")))
            .with(file_layer)
            .with(terminal_layer)
            .with(indicatif_layer)
            .init();

        info!("Logger initialized");
        guard
    }
}

pub fn pb_style() -> &'static ProgressStyle {
    static STYLE: OnceLock<ProgressStyle> = OnceLock::new();
    STYLE.get_or_init(|| {
        ProgressStyle::with_template("{spinner:.green} [{elapsed_precise}] [{bar:40.cyan/blue}] {pos:>7}/{len:7} ({percent}%) - {msg}")
            .unwrap()
    })
}