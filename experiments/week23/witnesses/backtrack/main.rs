extern crate alloc;
#[allow(unused_mut, unused_variables)]
pub fn graft_body(data: &[u8]) -> Vec<(usize, Vec<u8>)> {
let mut trace = Vec::new();
let mut v0: u8 = u8::from_le_bytes(core::array::from_fn(|i| data.get((0 + i) % data.len().max(1)).copied().unwrap_or(0)));
let observed_0 = format!("{:?}", (v0,)).into_bytes();
let mut v1: checked_fixture::Choice = checked_fixture::good_choice(v0);
if std::env::var_os("GRAFT_TRACE").is_some() { eprintln!("GRAFT_CALL:0:{}", String::from_utf8_lossy(&observed_0)); }
trace.push((0, observed_0));
let observed_1 = format!("{:?}", ()).into_bytes();
let mut v2: () = checked_fixture::choose(v1);
if std::env::var_os("GRAFT_TRACE").is_some() { eprintln!("GRAFT_CALL:1:{}", String::from_utf8_lossy(&observed_1)); }
trace.push((1, observed_1));
trace
}

fn main() { let data: Vec<u8> = std::env::args().skip(1).map(|s| s.parse::<u8>().expect("byte argument")).collect(); println!("{:?}", graft_body(&data)); }
