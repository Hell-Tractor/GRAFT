#[macro_use]
extern crate afl;
extern crate time;
fn _unwrap_result<T, E>(_res: Result<T, E>) -> T {
    match _res {
        Ok(_t) => _t,
        Err(_) => {
            use std::process;
            process::exit(0);
        },
    }
}

fn _to_u8(data:&[u8], index:usize)->u8 {
    data[index]
}


fn test_function88(_param0 :u8 ,_param1 :u8 ,_param2 :u8 ,_param3 :u8) {
    let _local0 = time::Time::from_hms(_param0 ,_param1 ,_param2);
    let _local1_param0_helper1 = _unwrap_result(_local0);
    let _local1 = time::Time::replace_minute(_local1_param0_helper1 ,_param3);
    let _local2_param0_helper1 = _unwrap_result(_local1);
    let _ = time::Time::millisecond(_local2_param0_helper1);
}

fn main() {
    fuzz!(|data: &[u8]| {
        //actual body emit
        if data.len() != 4 {return;}
        let _param0 = _to_u8(data, 0);
        let _param1 = _to_u8(data, 1);
        let _param2 = _to_u8(data, 2);
        let _param3 = _to_u8(data, 3);
        test_function88(_param0 ,_param1 ,_param2 ,_param3);
    });
}
