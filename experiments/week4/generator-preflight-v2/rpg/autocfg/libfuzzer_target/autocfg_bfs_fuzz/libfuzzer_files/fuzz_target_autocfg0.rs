#![no_main]
#[macro_use]
extern crate libfuzzer_sys;
extern crate autocfg;
fn _unwrap_result<T, E>(_res: Result<T, E>) -> T {
    match _res {
        Ok(_t) => _t,
        Err(_) => {
            use std::process;
            process::exit(0);
        },
    }
}

fn _to_bool(data:&[u8], index: usize)->bool {
    let bool_value = _to_u8(data, index);
    if bool_value %2 == 0 {
        true
    } else {
        false
    }
}

fn _to_u8(data:&[u8], index:usize)->u8 {
    data[index]
}

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


fn test_function0(_param0: bool ,_param1: &str) {
    let mut _local0 = autocfg::AutoCfg::new();
    let mut _local1_param0_helper1 = _unwrap_result(_local0);
    autocfg::AutoCfg::set_no_std(&mut (_local1_param0_helper1) ,_param0);
    autocfg::AutoCfg::set_edition(&mut (_local1_param0_helper1) ,Some(_param1.to_string()));
}

fuzz_target!(|data: &[u8]| {
    //actual body emit
    if data.len() < 2 {return;}
    let dynamic_length = (data.len() - 1) / 1;
    let _param0 = _to_bool(data, 0);
    let _param1 = _to_str(data, 1 + 0 * dynamic_length, data.len());
    test_function0(_param0 ,_param1);
});
