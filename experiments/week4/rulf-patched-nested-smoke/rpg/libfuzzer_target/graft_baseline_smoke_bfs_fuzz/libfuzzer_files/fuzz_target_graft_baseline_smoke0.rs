#![no_main]
#[macro_use]
extern crate libfuzzer_sys;
extern crate graft_baseline_smoke;
fn _to_u8(data:&[u8], index:usize)->u8 {
    data[index]
}


fn test_function0(_param0: u8) {
    let _local0 = graft_baseline_smoke::nested::make(_param0);
    graft_baseline_smoke::nested::consume(_local0);
}

fuzz_target!(|data: &[u8]| {
    //actual body emit
    if data.len() != 1 {return;}
    let _param0 = _to_u8(data, 0);
    test_function0(_param0);
});
