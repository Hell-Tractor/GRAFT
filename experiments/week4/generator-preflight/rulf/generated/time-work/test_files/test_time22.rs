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


fn test_function22(_param0 :u8) {
    let _local0 = time::OffsetDateTime::now_utc();
    let _local1 = time::OffsetDateTime::replace_second(_local0 ,_param0);
    let _local2_param0_helper1 = _unwrap_result(_local1);
    let _ = time::OffsetDateTime::ordinal(_local2_param0_helper1);
}

fn main() {
    fuzz!(|data: &[u8]| {
        //actual body emit
        if data.len() != 1 {return;}
        let _param0 = _to_u8(data, 0);
        test_function22(_param0);
    });
}
