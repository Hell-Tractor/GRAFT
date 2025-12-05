pub struct Edge {
    pub to: usize,
    pub ref_type: RefType,
}

#[derive(Debug, PartialEq, Eq)]
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