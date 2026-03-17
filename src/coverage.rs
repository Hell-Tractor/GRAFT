use std::{io::BufRead, path::{Path, PathBuf}};

use tracing::{debug, info, warn};

use crate::Result;

#[derive(Debug, Clone)]
struct API {
    pub file: PathBuf,
    pub start_line: usize,
    pub end_line: usize,
    pub is_covered: bool,
    #[allow(unused)]
    signature: String,
}

fn get_public_apis(krate: &rustdoc_types::Crate) -> Vec<API> {
    krate.index.values().filter_map(|item| {
        // Suppose all api in documentation are public, as private items are not included in rustdoc output.
        // if item.visibility != rustdoc_types::Visibility::Public {
        //     return None;
        // }
        // if !matches!(item.visibility, rustdoc_types::Visibility::Public) {
        //     return None;
        // }
        if let Some(_) = item.deprecation {
            return None;
        };
        match &item.inner {
            rustdoc_types::ItemEnum::Function(func) => {
                if !func.has_body {
                    return None;
                }
                let span = item.span.as_ref()?;
                let name = item.name.clone().unwrap_or("UNKNOWN_FUNCTION_NAME".into());
                let signature = crate::document::rust_type_names::function_signature(func, &name);
                Some(API {
                    file: span.filename.clone(),
                    start_line: span.begin.0,
                    end_line: span.end.0,
                    is_covered: false,
                    signature,
                })
            }
            _ => None,
        }
    }).collect()
}

pub struct Coverage {
    pub covered: usize,
    pub total: usize,
}

pub fn calculate_coverage(krate: &rustdoc_types::Crate, coverage_report_path: &Path) -> Result<Coverage> {
    let mut apis = get_public_apis(krate);
    info!("Total public APIs found: {}", apis.len());
    debug!("Head APIs: {:#?}", apis.iter().take(10).collect::<Vec<_>>());
    let reader = std::io::BufReader::new(std::fs::File::open(coverage_report_path)?);
    let mut covered = 0;
    let mut current_file = String::new();
    for line in reader.lines() {
        let line = line?;
        let line = line.trim().trim_start_matches("|").trim();
        if line.starts_with("^") {
            continue;
        } else if line.starts_with("/") && line.ends_with(":") {
            current_file = line.trim_end_matches(":").to_string();
        } else {
            if line.trim().is_empty() {
                continue;
            }
            let mut line_split = line.split("|");
            if line_split.clone().count() < 3 {
                // warn!("Invalid coverage report line: {}", line);
                continue;
            }
            let line_num = line_split.next().unwrap().trim().parse::<usize>().expect(&format!("Invalid line number at line: {}", line));
            let count = line_split.next().unwrap().trim().parse::<usize>().unwrap_or(0);
            if count == 0 {
                continue;
            }
            let candidate_apis = apis.iter_mut().filter(|api| {
                let Some(api_file) = api.file.to_str() else {
                    warn!("Non-UTF8 file path: {:?}", api.file);
                    return false;
                };
                if api_file != current_file {
                    return false;
                }
                line_num >= api.start_line && line_num <= api.end_line
            }).collect::<Vec<_>>();
            // if candidate_apis.is_empty() {
            //     warn!("No API found for coverage line: {}", line);
            // }
            if candidate_apis.len() > 1 {
                warn!("Multiple APIs found for coverage line: {}. Marking all as covered.", line);
            }
            for api in candidate_apis {
                if !api.is_covered {
                    api.is_covered = true;
                    covered += 1;
                }
            }
        }
    }
    Ok(Coverage {
        covered,
        total: apis.len(),
    })
}