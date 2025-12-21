use std::path::Path;

use serde::{Serialize};
use tracing::{info, instrument};

use crate::{document::r#type::constant::Constant, error::Result};

mod r#type;
mod query;
mod utils;
pub mod api;

trait TryFromApiDoc<T> {
    fn try_from_api_doc(querier: &query::DocQuerier) -> Result<T>;
}

#[derive(Serialize)]
pub struct APIDoc {
    pub apis: Vec<api::API>,
    pub types: Types,
    pub constants: Vec<Constant>,
}

#[derive(Serialize)]
pub struct Types {
    pub structs: Vec<r#type::r#struct::Struct>,
    pub enums: Vec<r#type::r#enum::Enum>,
    // enums, traits, etc can be added later
}

pub struct APIDocBuilder {
    doc: APIDoc,
}

impl APIDocBuilder {
    pub fn build(self) -> APIDoc {
        self.doc
    }
    pub fn filter_api(mut self) -> Self {
        const FILTER_API_NAMES: &[&str] = &[
            "into", "from", "try_into", "try_from",
            "as_ref", "as_mut", "deref", "deref_mut",
            "clone", "clone_from", "clone_into",
            "default", "type_id",
            "eq", "ne", "lt", "le", "gt", "ge",
            "add", "sub", "mul", "div", "rem",
            "bit_and", "bit_or", "bit_xor", "shl", "shr",
            "not", "neg",
            "borrow", "borrow_mut",
            "to_string",
            "drop", "to_owned",
            "fmt",
            "into_iter", "iter", "iter_mut",
        ];

        info!("Filtering out trivial APIs from documentation");
        let before_count = self.doc.apis.len();
        self.doc.apis.retain(|api| !FILTER_API_NAMES.contains(&api.name.as_str()));
        let after_count = self.doc.apis.len();
        info!("Filtered out {} trivial APIs (Origin: {}, After: {})", before_count - after_count, before_count, after_count);
        self
    }

    pub fn filter_types(mut self) -> Self {
        // filter out error enums
        info!("Filtering out error enums from documentation");
        let before_count = self.doc.types.enums.len();
        self.doc.types.enums.retain(|enm| !enm.name.ends_with("Error"));
        let after_count = self.doc.types.enums.len();
        info!("Filtered out {} error enums (Origin: {}, After: {})", before_count - after_count, before_count, after_count);

        // filter out trivial traits
        // const FILTER_TRAIT_NAMES: &[&str] = &[
        //     "Debug", "Display", "Clone", "Copy", "Default", "PartialEq", "Eq",
        //     "PartialOrd", "Ord", "Hash", "From", "Into", "AsRef", "AsMut",
        //     "Deref", "DerefMut", "Drop", "ToString", "TryFrom", "TryInto",
        //     "Sync", "Send",
        //     "Borrow", "BorrowMut", "ToOwned", "Any",
        //     "Freeze", "Unpin", "UnwindSafe", "RefUnwindSafe",
        // ];
        // info!("Filtering out trivial traits from documentation");
        // self.doc.types.structs.iter_mut().for_each(|struct_| {
        //     struct_.traits.retain(|t| !FILTER_TRAIT_NAMES.contains(&APIDocBuilder::get_raw_trait_name(t)));
        // });
        // self.doc.types.enums.iter_mut().for_each(|enm| {
        //     enm.traits.retain(|t| !FILTER_TRAIT_NAMES.contains(&APIDocBuilder::get_raw_trait_name(t)));
        // });
        // info!("Filtered out trivial traits from structs and enums");
        self
    }

    // fn get_raw_trait_name(t: &str) -> &str {
    //     t.split('<').next().unwrap_or(t)
    // }
}

impl APIDoc {
    // fn calculate_api_generic_allowed_types(&mut self) {
    //     for api in &mut self.apis {
    //         for generic in &mut api.generics {
    //             generic.calculate_allowed_types(&self.types);
    //         }
    //     }
    // }
    pub fn from_crate_name(crate_name: &str) -> Result<APIDocBuilder> {
        let raw_doc = get_raw_api_documentation(crate_name)?;
        let querier = query::DocQuerier::new(&raw_doc)?;
        let api_doc = APIDoc::try_from_api_doc(&querier)?;
        Ok(APIDocBuilder { doc: api_doc })
    }

    pub fn from_file(path: &Path) -> Result<APIDocBuilder> {
        let raw_doc = crate::utils::read_file_to_string(path)?;
        let querier = query::DocQuerier::new(&raw_doc)?;
        let api_doc = APIDoc::try_from_api_doc(&querier)?;
        Ok(APIDocBuilder { doc: api_doc })
    }
}

impl TryFromApiDoc<APIDoc> for APIDoc {
    fn try_from_api_doc(querier: &query::DocQuerier) -> Result<Self> {
        info!("Converting raw API documentation to structured APIDoc");
        let apis = api::API::try_from_api_doc(querier)?;
        let types = Types::try_from_api_doc(querier)?;
        let constants = Constant::try_from_api_doc(querier)?;
        Ok(APIDoc {
            apis,
            types,
            constants,
        })
    }
}

impl TryFromApiDoc<Types> for Types {
    fn try_from_api_doc(querier: &query::DocQuerier) -> Result<Self> {
        info!("Parsing Types documentation");
        let structs = r#type::r#struct::Struct::try_from_api_doc(querier)?;
        let enums = r#type::r#enum::Enum::try_from_api_doc(querier)?;
        Ok(Types {
            structs,
            enums,
        })
    }
}

impl ToString for APIDoc {
    fn to_string(&self) -> String {
        serde_json::to_string(self).unwrap_or("Serialization Failed".into())
    }
}

#[instrument]
fn get_raw_api_documentation(crate_name: &str) -> Result<String> {
    info!("Fetching raw API documentation for crate");
    let cargo = &crate::utils::config().commands.cargo;
    let temp_project_path = &crate::utils::config().doc.temp_project_path;
    let temp_project_path = std::path::Path::new(&temp_project_path);
    let temp_project_name = &crate::utils::config().doc.temp_project_name;

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
    // debug!("Cargo add output: {}", String::from_utf8_lossy(&output.stderr));

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
    let doc_content = crate::utils::read_file_to_string(doc_path)?;

    info!("Successfully fetched raw API documentation for crate");
    Ok(doc_content)
}

struct TempProject {
    project_path: Box<Path>,
}

impl TempProject {
    fn new(base_path: &Path, name: &str) -> Result<Self> {
        let cargo = &crate::utils::config().commands.cargo;
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