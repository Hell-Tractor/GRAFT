use std::path::Path;

use tracing::{info, instrument};

use crate::{error::Result, utils};

mod query;
mod api;

/// Fetch the API documentation of a crate in JSON format
/// # Arguments
/// * `crate_name` - The name of the crate to fetch documentation for
/// # Returns
/// A Result containing the JSON documentation as a String
/// or an Error if the operation fails
#[instrument]
pub fn get_api_documentation(crate_name: &str) -> Result<String> {
    info!("Fetching API documentation for crate");
    let cargo = utils::config().get_string("commands.cargo")?;
    let temp_project_path = utils::config().get_string("doc.temp_project_path")?;
    let temp_project_path = std::path::Path::new(&temp_project_path);
    let temp_project_name = utils::config().get_string("doc.temp_project_name")?;
    
    // create temporary project
    let _temp_project = TempProject::new(temp_project_path, &temp_project_name)?;
    // add the target crate as dependency
    let project_path = temp_project_path.join(&temp_project_name);
    let output = std::process::Command::new(&cargo)
        .arg("add").arg(crate_name)
        .current_dir(&project_path)
        .output()?;
    if !output.status.success() {
        return Err(crate::error::Error::CommandError(format!("Failed to add dependency: {}", String::from_utf8_lossy(&output.stderr))));
    }

    // generate documentation
    let output = std::process::Command::new(&cargo)
        .arg("+nightly").arg("rustdoc").arg("-p").arg(crate_name).arg("--")
        .arg("--output-format").arg("json").arg("-Z").arg("unstable-options")
        .current_dir(&project_path)
        .output()?;
    if !output.status.success() {
        return Err(crate::error::Error::CommandError(format!("Failed to generate documentation: {}", String::from_utf8_lossy(&output.stderr))));
    }

    // read the generated JSON file
    let doc_path = project_path.join("target/doc").join(format!("{}.json", crate_name.replace("-", "_")));
    let doc_content = utils::read_file_to_string(doc_path)?;
    
    info!("Successfully fetched API documentation for crate");
    Ok(doc_content)
}

struct TempProject {
    project_path: Box<Path>,
}

impl TempProject {
    fn new(base_path: &Path, name: &str) -> Result<Self> {
        let cargo = utils::config().get_string("commands.cargo")?;
        // create a temporary rust project
        let output = std::process::Command::new(&cargo)
            .arg("new").arg(&name).arg("--vcs").arg("none")
            .current_dir(base_path)
            .output()?;
        if !output.status.success() {
            return Err(crate::error::Error::CommandError(format!("Failed to create temporary project: {}", String::from_utf8_lossy(&output.stderr))));
        }
        Ok(TempProject { project_path: base_path.join(name).into() })
    }
}

impl Drop for TempProject {
    fn drop(&mut self) {
        // clean up the temporary project
        if self.project_path.exists() {
            let _ = std::fs::remove_dir_all(&self.project_path);
        }
    }
}