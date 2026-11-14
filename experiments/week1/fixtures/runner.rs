extern crate coverage_fixture;

fn main() {
    use coverage_fixture::*;
    assert_eq!(entered(3), 4);
    assert_eq!(generic(5_u8), 5);
    assert_eq!(generic(6_u32), 6);
    assert_eq!(Container::new(9_u8).read(), 9);
    assert_eq!(alias(), 7);
    assert_eq!(same_line_left(), 1);
    assert_eq!(macro_api(), 3);
}
