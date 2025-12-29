use std::sync::{Arc, RwLock};

use rustdoc_types::Type;
use serde::{Deserialize, Serialize};
use tracing::{debug, info};
use vertex::APIParam;
use edge::Edge;

use crate::{api_graph::vertex::{Constant, Vertex}, document, generic_bound::GenericBoundChecker};

mod vertex;
mod edge;
pub mod algorithm;

pub use vertex::API;

pub struct APIGraph {
    vertices: Vec<Vertex>,
    edges: Vec<Vec<Edge>>,
    rev_edges: Vec<Vec<Edge>>,
    // apis: Vec<Arc<Mutex<API>>>,
}

impl APIGraph {
    pub fn to_dot_file(&self, writer: &mut dyn std::io::Write) -> std::io::Result<()> {
        writeln!(writer, "digraph APIGraph {{")?;
        // write vertices
        for vertex in &self.vertices {
            match vertex {
                Vertex::APIParam(param) => {
                    let param = param.read().unwrap();
                    let type_str = trustfall_rustdoc_adapter::rust_type_name(&param.type_);
                    writeln!(writer, "  {} [label=\"Param: {}\\nType: {}\"];", param.id, param.name, type_str)?;
                    // write edge from param to api
                    writeln!(writer, "  {} -> {};", param.id, param.api.upgrade().unwrap().read().unwrap().id)?;
                },
                Vertex::API(api) => {
                    let api = api.read().unwrap();
                    let type_str = if let Some(ret_type) = &api.return_type {
                        trustfall_rustdoc_adapter::rust_type_name(ret_type)
                    } else {
                        "()".to_owned()
                    };
                    writeln!(writer, "  {} [label=\"API: {}\\nReturn Type: {}\"];", api.id, api.signature, type_str)?;
                },
                Vertex::Constant(constant) => {
                    let type_str = trustfall_rustdoc_adapter::rust_type_name(&constant.type_);
                    writeln!(writer, "  {} [label=\"Constant: {}\\nType: {}\"];", constant.id, constant.name, type_str)?;
                }
            }
        }

        // write edges
        for (from_id, edges) in self.edges.iter().enumerate() {
            for edge in edges {
                writeln!(writer, "  {} -> {} [label=\"{:?}\"];", from_id, edge.to, edge.ref_type)?;
            }
        }
        writeln!(writer, "}}")?;
        Ok(())
    }
}

impl From<document::APIDoc> for APIGraph {
    fn from(doc: document::APIDoc) -> Self {
        let all_types = doc.types;

        let mut apis = vec![];
        let mut vertices = vec![];
        info!("Extracting APIs and API parameters from APIDoc");
        for api in doc.apis {
            let api_vertex = Arc::new(RwLock::new(API::new(api.signature, api.output.get_wrapped_type(), api.generics, api.impl_for, api.module_path, api.doc_comment)));
            for param in api.inputs {
                let api_param = APIParam::new(param.name, param.type_, &api_vertex);
                let api_param = Arc::new(RwLock::new(api_param));
                api_vertex.write().unwrap().add_param(api_param.clone());
                vertices.push(Vertex::APIParam(api_param.clone()));
                api_param.write().unwrap().set_id(vertices.len() - 1);
            }
            apis.push(api_vertex.clone());
            vertices.push(Vertex::API(api_vertex.clone()));
            api_vertex.write().unwrap().set_id(vertices.len() - 1);
        }
        info!("Total APIs extracted: {}", vertices.len());

        info!("Extracting Constants from APIDoc");
        for constant in doc.constants {
            let constant_vertex = Constant {
                name: constant.name,
                type_: constant.type_,
                id: vertices.len(),
                module_path: constant.path,
                doc_comment: constant.docs,
            };
            vertices.push(Vertex::Constant(constant_vertex));
        }

        info!("Building API graph edges based on type compatibility");
        info!("Processing edges from api to api");
        let mut edges = vec![];
        let mut rev_edges = vec![];
        edges.resize_with(vertices.len(), || vec![]);
        rev_edges.resize_with(vertices.len(), || vec![]);
        for (from_id, from_api) in apis.iter().enumerate() {
            let from_api = from_api.read().unwrap();
            debug!("Processing start API: {} (ID: {})", from_api.signature, from_api.id);
            let output_type = &from_api.return_type;
            let Some(output_type) = output_type else {
                continue;
            };
            if let Type::Primitive(_) = output_type {
                continue;
            }
            if let Type::ResolvedPath(path) = output_type {
                if path.path == "String" {
                    continue;
                }
            }
            for (to_id, to_api) in apis.iter().enumerate() {
                if from_id == to_id {
                    continue;
                }
                let to_api = to_api.read().unwrap();
                debug!("  Checking compatibility to target API: {} (ID: {})", to_api.signature, to_api.id);
                let checker = GenericBoundChecker::new(
                    &from_api.generics,
                    &to_api.generics,
                    &all_types,
                    from_api.impl_for.as_ref(),
                    to_api.impl_for.as_ref()
                );
                for input_type in &to_api.params {
                    let input_type = input_type.read().unwrap();
                    debug!("    Comparing with input param: {} (ID: {})", input_type.name, input_type.id);
                    if input_type.is_fuzzer_generable() {
                        continue;
                    }
                    if checker.type_satisfies(&output_type, &input_type.type_) {
                        debug!("      Compatible! Adding edge from API {} to API {}", from_api.id, to_api.id);
                        edges[from_api.id].push(Edge::new(input_type.id, input_type.get_ref_type()));
                        rev_edges[input_type.id].push(Edge::new(from_api.id, input_type.get_ref_type()));
                    } else {
                        debug!("      Not compatible.");
                    }
                }
            }
        }
        info!("Processing edges from constants to api");
        for vertex in &vertices {
            if let Vertex::Constant(constant) = vertex {
                for to_api in &apis {
                    let to_api = to_api.read().unwrap();
                    info!("  Checking compatibility from Constant: {} (ID: {}) to target API: {} (ID: {})", constant.name, constant.id, to_api.signature, to_api.id);
                    let empty_vec = vec![];
                    let checker = GenericBoundChecker::new(
                        &empty_vec,
                        &to_api.generics,
                        &all_types,
                        None,
                        to_api.impl_for.as_ref(),
                    );
                    for input_type in &to_api.params {
                        let input_type = input_type.read().unwrap();
                        info!("    Comparing with input param: {} (ID: {})", input_type.name, input_type.id);
                        if input_type.is_fuzzer_generable() {
                            continue;
                        }
                        if checker.type_satisfies(&constant.type_, &input_type.type_) {
                            info!("      Compatible! Adding edge from Constant {} to API {}", constant.id, to_api.id);
                            edges[constant.id].push(Edge::new(input_type.id, input_type.get_ref_type()));
                            rev_edges[input_type.id].push(Edge::new(constant.id, input_type.get_ref_type()));
                        } else {
                            info!("      Not compatible.");
                        }
                    }
                }
            }
        }

        info!("API graph construction completed.");

        APIGraph {
            vertices,
            edges,
            rev_edges,
        }
    }
}