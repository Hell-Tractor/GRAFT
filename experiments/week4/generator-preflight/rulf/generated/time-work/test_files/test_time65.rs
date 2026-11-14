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

fn _to_i8(data:&[u8], index:usize)->i8 {    
    data[index] as i8
}

fn _to_i128(data:&[u8], index:usize)->i128 {
    let data0 = _to_i64(data, index) as i128;
    let data1 = _to_i64(data, index+8) as i128;
    data0 << 64 | data1
}

fn _to_i64(data:&[u8], index:usize)->i64 {
    let data0 = _to_i32(data, index) as i64;
    let data1 = _to_i32(data, index+4) as i64;
    data0 << 32 | data1
}

fn _to_i32(data:&[u8], index:usize)->i32 {
    let data0 = _to_i16(data, index) as i32;
    let data1 = _to_i16(data, index+2) as i32;
    data0 << 16 | data1
}

fn _to_i16(data:&[u8], index:usize)->i16 {
    let data0 = _to_i8(data, index) as i16;
    let data1 = _to_i8(data, index+1) as i16;
    data0 << 8 | data1
}


fn test_function65(_param0 :i128 ,_param1 :u8) {
    let _local0 = time::OffsetDateTime::from_unix_timestamp_nanos(_param0);
    let _local1_param0_helper1 = _unwrap_result(_local0);
    let _local1 = time::OffsetDateTime::replace_minute(_local1_param0_helper1 ,_param1);
    let _local2_param0_helper1 = _unwrap_result(_local1);
    let _ = time::OffsetDateTime::millisecond(_local2_param0_helper1);
}

fn main() {
    fuzz!(|data: &[u8]| {
        //actual body emit
        if data.len() != 17 {return;}
        let _param0 = _to_i128(data, 0);
        let _param1 = _to_u8(data, 16);
        test_function65(_param0 ,_param1);
    });
}
