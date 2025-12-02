use std::sync::{Arc, Mutex};

use rustdoc_types::Type;
use tracing::info;
use vertex::APIParam;
use edge::Edge;

use crate::{api_graph::vertex::API, document::{self, api::APIGeneric}, generic_bound::GenericBoundChecker};

mod vertex;
mod edge;

pub struct APIGraph {
    vertices: Vec<Vertex>,
    edges: Vec<Vec<Edge>>,
    // apis: Vec<Arc<Mutex<API>>>,
}

pub enum Vertex {
    APIParam(Arc<Mutex<APIParam>>),
    API(Arc<Mutex<API>>),
}

impl APIGraph {
    pub fn new() -> Self {
        Self {
            vertices: vec![],
            edges: vec![],
            // apis: vec![],
        }
    }

    pub fn to_dot_file(&self, writer: &mut dyn std::io::Write) -> std::io::Result<()> {
        writeln!(writer, "digraph APIGraph {{")?;
        // write vertices
        for vertex in &self.vertices {
            match vertex {
                Vertex::APIParam(param) => {
                    let param = param.lock().unwrap();
                    let type_str = trustfall_rustdoc_adapter::rust_type_name(&param.type_);
                    writeln!(writer, "  {} [label=\"Param: {}\\nType: {}\"];", param.id, param.name, type_str)?;
                    // write edge from param to api
                    writeln!(writer, "  {} -> {};", param.id, param.api.upgrade().unwrap().lock().unwrap().id)?;
                }
                Vertex::API(api) => {
                    let api = api.lock().unwrap();
                    let type_str = if let Some(ret_type) = &api.return_type {
                        trustfall_rustdoc_adapter::rust_type_name(ret_type)
                    } else {
                        "()".to_owned()
                    };
                    writeln!(writer, "  {} [label=\"API: {}\\nReturn Type: {}\"];", api.id, api.signature, type_str)?;
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
            let api_vertex = Arc::new(Mutex::new(API::new(api.signature, api.output.get_wrapped_type(), api.generics, api.impl_for)));
            for param in api.inputs {
                let api_param = APIParam::new(param.name, param.type_, &api_vertex);
                let api_param = Arc::new(Mutex::new(api_param));
                api_vertex.lock().unwrap().add_param(api_param.clone());
                vertices.push(Vertex::APIParam(api_param.clone()));
                api_param.lock().unwrap().set_id(vertices.len() - 1);
            }
            apis.push(api_vertex.clone());
            vertices.push(Vertex::API(api_vertex.clone()));
            api_vertex.lock().unwrap().set_id(vertices.len() - 1);
        }
        info!("Total APIs extracted: {}", vertices.len());

        info!("Building API graph edges based on type compatibility");
        let mut edges: Vec<Vec<Edge>> = vec![];
        edges.resize_with(vertices.len(), || vec![]);
        for (from_id, from_api) in apis.iter().enumerate() {
            let from_api = from_api.lock().unwrap();
            info!("Processing start API: {} (ID: {})", from_api.signature, from_api.id);
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
                let to_api = to_api.lock().unwrap();
                info!("  Checking compatibility to target API: {} (ID: {})", to_api.signature, to_api.id);
                let checker = GenericBoundChecker::new(
                    &from_api.generics,
                    &to_api.generics,
                    &all_types,
                    from_api.impl_for.as_ref(),
                    to_api.impl_for.as_ref()
                );
                for input_type in &to_api.params {
                    let input_type = input_type.lock().unwrap();
                    info!("    Comparing with input param: {} (ID: {})", input_type.name, input_type.id);
                    if input_type.is_primitive() {
                        continue;
                    }
                    if checker.type_satisfies(&output_type, &input_type.type_) {
                        info!("      Compatible! Adding edge from API {} to API {}", from_api.id, to_api.id);
                        edges[from_api.id].push(Edge::new(input_type.id, input_type.get_ref_type()));
                    } else {
                        info!("      Not compatible.");
                    }
                }
            }
        }

        APIGraph {
            vertices,
            edges,
        }
    }
}