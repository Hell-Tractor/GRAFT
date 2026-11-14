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

fn _to_u16(data:&[u8], index:usize)->u16 {
    let data0 = _to_u8(data, index) as u16;
    let data1 = _to_u8(data, index+1) as u16;
    data0 << 8 | data1
}


fn test_function6(_param0 :u8 ,_param1 :u8 ,_param2 :u8 ,_param3 :u16 ,_param4 :u8) {
    let _local0 = time::Time::from_hms_milli(_param0 ,_param1 ,_param2 ,_param3);
    let _local1_param0_helper1 = _unwrap_result(_local0);
    let _local1 = time::Time::replace_minute(_local1_param0_helper1 ,_param4);
    let _local2_param0_helper1 = _unwrap_result(_local1);
    let _ = time::Time::as_hms_milli(_local2_param0_helper1);
}

fn main() {
    fuzz!(|data: &[u8]| {
        //actual body emit
        if data.len() != 6 {return;}
        let _param0 = _to_u8(data, 0);
        let _param1 = _to_u8(data, 1);
        let _param2 = _to_u8(data, 2);
        let _param3 = _to_u16(data, 3);
        let _param4 = _to_u8(data, 5);
        test_function6(_param0 ,_param1 ,_param2 ,_param3 ,_param4);
    });
}
