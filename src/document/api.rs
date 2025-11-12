use serde::Serialize;


#[derive(Serialize)]
pub struct API {
    pub name: String,
    pub kind: APIKind,
    pub signature: String,
    pub module_path: String,
    pub doc_comment: String,
    pub location: Location,
    pub inputs: Vec<Parameter>,
    pub output: ReturnValue,
    pub is_public: bool,
    pub is_safe: bool,
    pub has_unsafe: bool,
    pub recommended_fuzz_type: FuzzType,
    pub notes_for_fuzzer: String,
}

#[derive(Serialize)]
pub enum APIKind {
    Function,
    Method,
}

#[derive(Serialize)]
pub enum FuzzType {
    String,
    Custom,
    Bytes,
    Integer,
}

#[derive(Serialize)]
pub struct Location {
    pub file: String,
    pub line: usize,
}

#[derive(Serialize)]
pub struct Parameter {
    pub name: String,
    #[serde(rename = "type")]
    pub type_name: String,
    pub description: String,
    pub fuzz_strategy_suggestion: String,
}

#[derive(Serialize)]
pub struct ReturnValue {
    #[serde(rename = "type")]
    pub type_name: String,
    pub description: String,
}