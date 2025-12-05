use std::{collections::{HashSet, VecDeque}, sync::{Arc, Mutex}};

use rustdoc_types::Type;

use crate::api_graph::{API, APIGraph, edge::RefType, vertex::Vertex};

/// 生成方法：
/// 1. 通过拓扑排序，标记每个API的深度，深度 $depth[i] = \max\limits_{ p \in api[i].params}\{\min\limits_{api[j].return\_value = p}\{depth[j]\} + 1\}$
/// 2. 选择深度最深、使用次数最少的API，加入序列，开始反向生成
/// 3. 维护一个类型池，包含目前所有的类型、生成该类型的API ID以及被引用的方式（不可变引用、可变引用、移动）
/// 4. 对于当前API的每个参数
///     若其在类型池中且可以被使用（没有被可变引用或移动）：直接使用该类型（跳过该参数）
///     否则：选择可以产生此参数的，使用次数最少的API，加入序列，更新类型池，继续反向生成，直到生成完毕
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
        let mut indegrees = graph.vertices.iter().map(|vertex| {
            match vertex {
                Vertex::API(api) => {
                    api.lock().unwrap().params.iter()
                        .filter(|param| !param.lock().unwrap().is_primitive())
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
            // edges link from api/constant to api_param
            for edge in &graph.edges[from_id] {
                let to_id = edge.to;
                if depth[to_id] != -1 {
                    // already visited api_param, skip
                    continue;
                }
                depth[to_id] = depth[from_id] + 1;
                let Vertex::APIParam(param) = &graph.vertices[to_id] else {
                    unreachable!("Edge should point to APIParam");
                };
                let api = param.lock().unwrap().api.upgrade().unwrap();
                let api_id = api.lock().unwrap().id;
                indegrees[api_id] -= 1;
                if indegrees[api_id] == 0 {
                    depth[api_id] = depth[to_id];
                    q.push_back(api_id);
                }
            }
        }

        depth
    }

    pub fn generate(&mut self) -> Vec<Arc<Mutex<API>>> {
        let target_api = self.graph.vertices.iter().filter_map(|vertex| {
            if let Vertex::API(api) = vertex {
                Some(api.clone())
            } else {
                None
            }
        }).max_by(|a, b| {
            let a = a.lock().unwrap();
            let b = b.lock().unwrap();
            // first by used_times asc, then by depth desc
            if self.used_times[a.id] != self.used_times[b.id] {
                self.used_times[a.id].cmp(&self.used_times[b.id])
            } else {
                self.depth[b.id].cmp(&self.depth[a.id])
            }
        }).expect("Should be at least one API in graph");
        self.generate_with_target(target_api)
    }

    pub fn generate_with_target(&mut self, target_api: Arc<Mutex<API>>) -> Vec<Arc<Mutex<API>>> {
        let mut sequence = vec![target_api.clone()];
        // 保存已生成类型对应的api id
        let mut type_pool: HashSet<usize> = HashSet::new();
        let mut q = VecDeque::new();
        q.push_back(target_api);

        while !q.is_empty() {
            let api = q.pop_front().unwrap();
            let api = api.lock().unwrap();
            for param in &api.params {
                let param = param.lock().unwrap();
                if param.is_primitive() {
                    continue;
                }
                // check if any return-type already exists in type pool
                if param.get_ref_type() == RefType::Borrow {
                    let exists = self.graph.rev_edges[param.id].iter().any(|edge| {
                        let Vertex::API(api) = &self.graph.vertices[edge.to] else {
                            unreachable!("Reverse edge should point to API");
                        };
                        type_pool.contains(&api.lock().unwrap().id)
                    });
                    if exists {
                        continue;
                    }
                }
                // need to generate this param
                let new_api = self.graph.rev_edges[param.id].iter()
                    .map(|edge| {
                        let Vertex::API(api) = &self.graph.vertices[edge.to] else {
                            unreachable!("Reverse edge should point to API");
                        };
                        api.clone()
                    }).min_by(|a, b| {
                        let a = a.lock().unwrap();
                        let b = b.lock().unwrap();
                        self.used_times[a.id].cmp(&self.used_times[b.id])
                    });
                if let Some(api_ptr) = new_api {
                    let new_api = api_ptr.lock().unwrap();
                    sequence.push(api_ptr.clone());
                    self.used_times[new_api.id] += 1;
                    if param.get_ref_type() == RefType::Borrow {
                        type_pool.insert(new_api.id);
                    }
                }
            }
        }

        sequence.reverse();
        sequence
    }
}