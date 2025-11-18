#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error(transparent)]
    ConfigError(#[from] config::ConfigError),
    #[error(transparent)]
    ReqwestError(#[from] reqwest::Error),
    #[error(transparent)]
    IoError(#[from] std::io::Error),
    #[error("LLM generation failed for project `{0}` after {1} retries")]
    CodeGenerationError(String, u32),
    #[error("Command execution failed: {0}")]
    CommandError(String),
    #[error(transparent)]
    SerdeError(#[from] serde_json::Error),
    #[error("Trustfall query error: {0}")]
    TrustfallQueryError(String),
    #[error("Length of slice `{0}` and `{1}` do not match in Trustfall query result.")]
    TrustfallQueryResultLengthMissmatch(String, String),
    #[error("Missing key `{0}` in Trustfall query result.")]
    TrustfallQueryResultMissingKey(String),
    #[error("Element `{0}` in Trustfall query result is not a {1}.")]
    TrustfallQueryResultTypeMismatch(String, String),
    #[error("Unsupported LLM type: {0}")]
    UnsupportedLLMType(String),
}

pub type Result<T> = std::result::Result<T, Error>;