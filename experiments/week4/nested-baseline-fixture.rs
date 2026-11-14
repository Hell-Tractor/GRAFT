pub mod nested {
    pub struct Value(u8);
    pub fn make(x: u8) -> Value { Value(x) }
    pub fn consume(v: Value) -> u8 { v.0 }
}
