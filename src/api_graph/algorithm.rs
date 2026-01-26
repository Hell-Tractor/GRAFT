use std::{collections::{HashSet, VecDeque}, sync::{Arc, RwLock}};

use tracing::{debug, info};

use crate::{api_graph::{API, APIGraph, edge::RefType, vertex::Vertex}, utils::config};

/// 生成方法：
/// 1. 通过拓扑排序，标记每个API的深度，深度 $depth[i] = \max\limits_{ p \in api[i].params}\{\min\limits_{api[j].return\_value = p}\{depth[j]\} + 1\}$
/// 2. 选择深度最深、使用次数最少的API，加入序列，开始反向生成
/// 3. 维护一个类型池，包含目前所有的类型、生成该类型的API ID
/// 4. 对于当前API的每个参数
///     若其在类型池中且该处为不可变引用：直接使用该类型（跳过该参数）
///     否则：选择可以产生此参数的，使用次数最少的API，加入序列，若为不可变引用则更新类型池，继续反向生成，直到生成完毕
/// 5. 返回生成结果。保留使用次数信息，供下一次生成使用。类型池不保留。
///
/// 这样可以生成树状API调用序列，同时尽可能覆盖更多的API，以提高Fuzzing覆盖率。
pub struct APISequeceGenerator<'a> {
    graph: &'a APIGraph,
    used_times: Vec<i32>,
    depth: Vec<i32>,
}

impl<'a> APISequeceGenerator<'a> {
    pub fn new(graph: &'a APIGraph) -> Self {
        let used_times = vec![0; graph.vertices.len()];
        let depth = APISequeceGenerator::calculate_depth(graph);
        Self { graph, used_times, depth }
    }

    fn calculate_depth(graph: &APIGraph) -> Vec<i32> {
        // init indegrees
        // TODO: 如果泛型T可以被基本类型替换，则也可以作为起点。并告知llm使用哪个基本类型替换哪个参数
        let mut indegrees = graph.vertices.iter().map(|vertex| {
            match vertex {
                Vertex::API(api) => {
                    api.read().unwrap().params.iter()
                        .filter(|param| {
                            !param.read().unwrap().is_fuzzer_generable()
                        })
                        .count() as i32
                },
                Vertex::APIParam(_) => -1,
                Vertex::Constant(_) => 0,
            }
        }).collect::<Vec<_>>();

        // initialize queue
        let mut depth = vec![-1; graph.vertices.len()];
        let mut q = VecDeque::new();
        for (idx, deg) in indegrees.iter().enumerate() {
            if *deg == 0 {
                q.push_back(idx);
                depth[idx] = 0;
            }
        }

        // Topo-sort
        while !q.is_empty() {
            let from_id = q.pop_front().unwrap(); // guaranteed to be exist
            debug!("Starting from api: {}, depth: {}", match &graph.vertices[from_id] {
                Vertex::API(api) => api.read().unwrap().signature.clone(),
                Vertex::Constant(constant) => constant.name.clone(),
                Vertex::APIParam(_) => unreachable!("Should not start from APIParam"),
            }, depth[from_id]);
            // edges link from api/constant to api_param
            for edge in &graph.edges[from_id] {
                debug!("  Edge to api_param: {}", match &graph.vertices[edge.to] {
                    Vertex::APIParam(param) => param.read().unwrap().name.clone(),
                    _ => unreachable!("Edge should point to APIParam"),
                });
                let to_id = edge.to;
                if depth[to_id] != -1 {
                    debug!("api_param already visited, skip");
                    // already visited api_param, skip
                    continue;
                }
                depth[to_id] = depth[from_id] + 1;
                let Vertex::APIParam(param) = &graph.vertices[to_id] else {
                    unreachable!("Edge should point to APIParam");
                };
                let api = param.read().unwrap().api.upgrade().unwrap();
                debug!("    api_param belongs to api: {}", api.read().unwrap().signature);
                let api_id = api.read().unwrap().id;
                indegrees[api_id] -= 1;
                if indegrees[api_id] == 0 {
                    depth[api_id] = depth[to_id];
                    q.push_back(api_id);
                    debug!("API: {}, depth: {}", api.read().unwrap().signature, depth[api_id]);
                }
            }
        }

        let debug_file = "debug/graph.txt";
        std::fs::create_dir_all("debug").unwrap();
        let mut f = std::fs::File::create(debug_file).unwrap();
        use std::io::Write;
        for (idx, d) in depth.iter().enumerate() {
            let signature = match &graph.vertices[idx] {
                Vertex::API(api) => api.read().unwrap().signature.clone(),
                Vertex::APIParam(_) => continue,
                Vertex::Constant(constant) => constant.name.clone(),
            };
            writeln!(f, "Vertex {}: Depth {}", signature, d).unwrap();
        }

        depth
    }

    pub fn generate(&mut self) -> Vec<Arc<RwLock<API>>> {
        let target_api = self.graph.vertices.iter().filter_map(|vertex| {
            if let Vertex::API(api) = vertex {
                Some(api.clone())
            } else {
                None
            }
        }).min_by(|a, b| {
            let a = a.read().unwrap();
            let b = b.read().unwrap();
            // first by used_times asc, then by depth desc
            if self.used_times[a.id] != self.used_times[b.id] {
                self.used_times[a.id].cmp(&self.used_times[b.id])
            } else {
                self.depth[b.id].cmp(&self.depth[a.id])
            }
        }).expect("Should be at least one API in graph");
        {
            let api = target_api.read().unwrap();
            info!("Selected target API(used = {}): {}", self.used_times[api.id], api.signature);
        }
        self.generate_with_target(target_api)
    }

    pub fn generate_with_target(&mut self, target_api: Arc<RwLock<API>>) -> Vec<Arc<RwLock<API>>> {
        let mut sequence = vec![target_api.clone()];
        let mut count = vec![0; self.graph.vertices.len()];
        count[target_api.read().unwrap().id] += 1;
        self.used_times[target_api.read().unwrap().id] += 1;
        let api_max_usage = config().testcase.each_api_max_usage;
        // 保存已生成类型对应的api id
        let mut type_pool: HashSet<usize> = HashSet::new();
        let mut q = VecDeque::new();
        q.push_back(target_api);

        while !q.is_empty() {
            let api = q.pop_front().unwrap();
            let api = api.read().unwrap();
            debug!("Generating for API: {}", api.signature);
            for param in &api.params {
                let param = param.read().unwrap();
                if param.is_fuzzer_generable() {
                    continue;
                }
                // check if any return-type already exists in type pool
                if param.get_ref_type() == RefType::Borrow {
                    let exists = self.graph.rev_edges[param.id].iter().any(|edge| {
                        if let Vertex::Constant(constant) = &self.graph.vertices[edge.to] {
                            return type_pool.contains(&constant.id);
                        }
                        let Vertex::API(api) = &self.graph.vertices[edge.to] else {
                            unreachable!("Reverse edge should point to API");
                        };
                        type_pool.contains(&api.read().unwrap().id)
                    });
                    if exists {
                        continue;
                    }
                }
                // need to generate this param
                let new_api = self.graph.rev_edges[param.id].iter()
                    .map(|edge| {
                        if let Vertex::Constant(constant) = &self.graph.vertices[edge.to] {
                            return Arc::new(RwLock::new(API { signature: format!("const {}: {}", constant.name, trustfall_rustdoc_adapter::rust_type_name(&constant.type_)), params: vec![], return_type: Some(constant.type_.clone()), id: constant.id, generics: vec![], module_path: constant.module_path.clone(), doc_comment: constant.doc_comment.clone() }));
                        }
                        let Vertex::API(api) = &self.graph.vertices[edge.to] else {
                            unreachable!("Reverse edge should point to API");
                        };
                        api.clone()
                    }).filter(|api_ptr| {
                        let api = api_ptr.read().unwrap();
                        count[api.id] < api_max_usage
                    }).min_by(|a, b| {
                        let a = a.read().unwrap();
                        let b = b.read().unwrap();
                        self.used_times[a.id].cmp(&self.used_times[b.id])
                    });
                if let Some(api_ptr) = new_api {
                    let new_api = api_ptr.read().unwrap();
                    sequence.push(api_ptr.clone());
                    q.push_back(api_ptr.clone());
                    self.used_times[new_api.id] += 1;
                    count[new_api.id] += 1;
                    if param.get_ref_type() == RefType::Borrow {
                        type_pool.insert(new_api.id);
                    }
                }
            }
        }

        sequence.reverse();
        sequence
    }

    /// Suggest whether to generate a new API sequence based on usage times and depth
    ///
    /// If all reachable APIs have been used, return false
    /// else return true
    pub fn suggest_generate(&self) -> bool {
        self.graph.vertices.iter().any(|vertex| {
            if let Vertex::API(api) = vertex {
                let api = api.read().unwrap();
                self.depth[api.id] >= 0 && self.used_times[api.id] == 0
            } else {
                false
            }
        })
    }

    pub fn max_api_coverage(&self) -> (usize, usize) {
        let total = self.graph.vertices.iter().filter(|vertex| {
            if let Vertex::API(_) = vertex {
                true
            } else {
                false
            }
        }).count();
        let used = self.graph.vertices.iter().filter(|vertex| {
            if let Vertex::API(api) = vertex {
                let api = api.read().unwrap();
                self.depth[api.id] >= 0
            } else {
                false
            }
        }).count();
        (used, total)
    }
}