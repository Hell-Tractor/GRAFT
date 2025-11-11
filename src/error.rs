#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error(transparent)]
    ConfigError(#[from] config::ConfigError),
    #[error(transparent)]
    ReqwestError(#[from] reqwest::Error),
    #[error(transparent)]
    IoError(#[from] std::io::Error),
    #[error("LLM generation failed for project `{0}` after {1} retries")]
    CodeGenerationError(String, u32)
}

pub type Result<T> = std::result::Result<T, Error>;