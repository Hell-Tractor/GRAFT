#![no_main]
#[macro_use]
extern crate libfuzzer_sys;
extern crate regex;
fn _to_str(data:&[u8], start_index: usize, end_index: usize)->&str {
    let data_slice = &data[start_index..end_index];
    use std::str;
    match str::from_utf8(data_slice) {
        Ok(s)=>s,
        Err(_)=>{
            use std::process;
            process::exit(0);
        }
    }
}


fn test_function58(_param0: &str) {
    let _local0 = regex::RegexSet::empty();
    let _local1 = regex::RegexSet::matches(&(_local0) ,_param0);
    regex::SetMatches::iter(&(_local1));
}

fuzz_target!(|data: &[u8]| {
    //actual body emit
    if data.len() < 1 {return;}
    let dynamic_length = (data.len() - 0) / 1;
    let _param0 = _to_str(data, 0 + 0 * dynamic_length, data.len());
    test_function58(_param0);
});
