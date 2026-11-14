pub fn scalar(x:u8)->u8{x.wrapping_add(1)}
pub struct Value(u8);
pub fn make(x:u8)->Value{Value(x)}
pub fn consume(v:Value)->u8{v.0}
