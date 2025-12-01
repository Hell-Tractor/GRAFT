pub struct Edge {
    pub to: usize,
    pub ref_type: RefType,
}

pub enum RefType {
    Borrow,
    BorrowMut,
    Move,
}

impl Edge {
    pub fn new(to: usize, ref_type: RefType) -> Self {
        Self { to, ref_type }
    }
}