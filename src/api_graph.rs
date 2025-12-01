use std::sync::{Arc, Mutex};

use vertex::APIParam;
use edge::Edge;

use crate::{api_graph::vertex::API, document, generic_bound::{self, GenericBoundChecker}};

mod vertex;
mod edge;

pub struct APIGraph {
    vertices: Vec<Vertex>,
    edges: Vec<Vec<Edge>>,
    apis: Vec<Arc<Mutex<API>>>,
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
            apis: vec![],
        }
    }
}

impl From<document::APIDoc> for APIGraph {
    fn from(doc: document::APIDoc) -> Self {
        let all_types = doc.types;

        let mut apis = vec![];
        let mut vertices = vec![];
        for api in doc.apis {
            let api_vertex = Arc::new(Mutex::new(API::new(api.signature, api.output.type_, api.generics)));
            for param in api.inputs {
                let api_param = APIParam::new(param.name, param.type_, &api_vertex);
                let api_param = Arc::new(Mutex::new(api_param));
                api_vertex.lock().unwrap().add_param(api_param.clone());
                vertices.push(Vertex::APIParam(api_param.clone()));
                api_param.lock().unwrap().set_id(vertices.len() - 1);
            }
            apis.push(api_vertex.clone());
            api_vertex.lock().unwrap().set_id(vertices.len());
        }

        let mut edges: Vec<Vec<Edge>> = vec![];
        for from_api in &apis {
            let from_api = from_api.lock().unwrap();
            let output_type = &from_api.return_type;
            let Some(output_type) = output_type else {
                continue;
            };
            for to_api in &apis {
                let to_api = to_api.lock().unwrap();
                if from_api.id == to_api.id {
                    continue;
                }
                let checker = GenericBoundChecker::new(&to_api.generics, &all_types);
                for input_type in &to_api.params {
                    let input_type = input_type.lock().unwrap();
                    if checker.type_satisfies(&output_type, &input_type.type_) {
                        // add edge
                        edges[from_api.id].push(Edge::new(input_type.id, input_type.get_ref_type()));
                    }
                }
            }
        }

        APIGraph {
            vertices,
            edges,
            apis,
        }
    }
}