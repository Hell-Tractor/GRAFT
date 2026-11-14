extern crate alloc;
#[allow(unused_mut, unused_variables)]
pub fn graft_body(data: &[u8]) -> Vec<(usize, Vec<u8>)> {
let mut trace = Vec::new();
let mut v0: u8 = u8::from_le_bytes(core::array::from_fn(|i| data.get((0 + i) % data.len().max(1)).copied().unwrap_or(0)));
let observed_0 = format!("{:?}", (v0,)).into_bytes();
let mut v1: checked_fixture::Token = checked_fixture::token(v0);
if std::env::var_os("GRAFT_TRACE").is_some() { eprintln!("GRAFT_CALL:0:{}", String::from_utf8_lossy(&observed_0)); }
trace.push((0, observed_0));
let observed_1 = format!("{:?}", (v0,)).into_bytes();
let mut v2: checked_fixture::Token = checked_fixture::token(v0);
if std::env::var_os("GRAFT_TRACE").is_some() { eprintln!("GRAFT_CALL:1:{}", String::from_utf8_lossy(&observed_1)); }
trace.push((1, observed_1));
let observed_2 = format!("{:?}", ()).into_bytes();
let mut v3: checked_fixture::Extra = checked_fixture::extra(v2);
if std::env::var_os("GRAFT_TRACE").is_some() { eprintln!("GRAFT_CALL:2:{}", String::from_utf8_lossy(&observed_2)); }
trace.push((2, observed_2));
let observed_3 = format!("{:?}", ()).into_bytes();
let mut v4: () = checked_fixture::hold_then_make(&v1, v3);
if std::env::var_os("GRAFT_TRACE").is_some() { eprintln!("GRAFT_CALL:3:{}", String::from_utf8_lossy(&observed_3)); }
trace.push((3, observed_3));
trace
}

fn main() { let data: Vec<u8> = std::env::args().skip(1).map(|s| s.parse::<u8>().expect("byte argument")).collect(); println!("{:?}", graft_body(&data)); }
