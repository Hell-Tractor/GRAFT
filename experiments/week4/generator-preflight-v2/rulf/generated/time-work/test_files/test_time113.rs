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

fn _to_i8(data:&[u8], index:usize)->i8 {    
    data[index] as i8
}


fn test_function113(_param0 :i8 ,_param1 :i8 ,_param2 :i8) {
    let _local0 = time::UtcOffset::from_hms(_param0 ,_param1 ,_param2);
    let _local1_param0_helper1 = _unwrap_result(_local0);
    let _ = time::UtcOffset::minutes_past_hour(_local1_param0_helper1);
}

fn main() {
    fuzz!(|data: &[u8]| {
        //actual body emit
        if data.len() != 3 {return;}
        let _param0 = _to_i8(data, 0);
        let _param1 = _to_i8(data, 1);
        let _param2 = _to_i8(data, 2);
        test_function113(_param0 ,_param1 ,_param2);
    });
}
