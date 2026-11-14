//! A small, known API universe for independent coverage mapping verification.

pub fn entered(x: u8) -> u8 {
    x.wrapping_add(1)
}

pub fn uncalled() -> u8 {
    42
}

pub fn generic<T: Copy>(x: T) -> T {
    x
}

pub struct Container<T>(T);

impl<T: Copy> Container<T> {
    pub fn new(value: T) -> Self {
        Self(value)
    }

    pub fn read(&self) -> T {
        self.0
    }
}

pub mod inner {
    pub fn reexported() -> u8 {
        7
    }
}
pub use inner::reexported as alias;

pub fn same_line_left() -> u8 { 1 } pub fn same_line_right() -> u8 { 2 }

macro_rules! define_api {
    ($name:ident) => { pub fn $name() -> u8 { 3 } };
}
define_api!(macro_api);
