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

fn _to_i16(data:&[u8], index:usize)->i16 {
    let data0 = _to_i8(data, index) as i16;
    let data1 = _to_i8(data, index+1) as i16;
    data0 << 8 | data1
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

fn _to_u16(data:&[u8], index:usize)->u16 {
    let data0 = _to_u8(data, index) as u16;
    let data1 = _to_u8(data, index+1) as u16;
    data0 << 8 | data1
}


fn test_function110(_param0 :i64 ,_param1 :i32 ,_param2 :u16) {
    let _local0 = time::OffsetDateTime::from_unix_timestamp(_param0);
    let _local1 = time::Date::from_ordinal_date(_param1 ,_param2);
    let _local2_param0_helper1 = _unwrap_result(_local1);
    let _local2 = time::Date::midnight(_local2_param0_helper1);
    let _local3_param0_helper1 = _unwrap_result(_local0);
    let _ = time::OffsetDateTime::replace_date_time(_local3_param0_helper1 ,_local2);
}

fn main() {
    fuzz!(|data: &[u8]| {
        //actual body emit
        if data.len() != 14 {return;}
        let _param0 = _to_i64(data, 0);
        let _param1 = _to_i32(data, 8);
        let _param2 = _to_u16(data, 12);
        test_function110(_param0 ,_param1 ,_param2);
    });
}
