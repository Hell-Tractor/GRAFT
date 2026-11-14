#[macro_use]
extern crate afl;
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

fn _to_u64(data:&[u8], index:usize)->u64 {
    let data0 = _to_u32(data, index) as u64;
    let data1 = _to_u32(data, index+4) as u64;
    data0 << 32 | data1
}

fn _to_usize(data:&[u8], index:usize)->usize {
    _to_u64(data, index) as usize
}

fn _to_u32(data:&[u8], index:usize)->u32 {
    let data0 = _to_u16(data, index) as u32;
    let data1 = _to_u16(data, index+2) as u32;
    data0 << 16 | data1
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

fn _to_u8(data:&[u8], index:usize)->u8 {
    data[index]
}

fn _to_u16(data:&[u8], index:usize)->u16 {
    let data0 = _to_u8(data, index) as u16;
    let data1 = _to_u8(data, index+1) as u16;
    data0 << 8 | data1
}


fn test_function20(_param0: &str ,_param1: usize ,_param2: usize) {
    let mut _local0 = autocfg::AutoCfg::new();
    let mut _local1_param0_helper1 = _unwrap_result(_local0);
    autocfg::AutoCfg::set_edition(&mut (_local1_param0_helper1) ,Some(_param0.to_string()));
    autocfg::AutoCfg::emit_rustc_version(&(_local1_param0_helper1) ,_param1 ,_param2);
}

fn main() {
    fuzz!(|data: &[u8]| {
        //actual body emit
        if data.len() < 17 {return;}
        let dynamic_length = (data.len() - 16) / 1;
        let _param0 = _to_str(data, 16 + 0 * dynamic_length, data.len());
        let _param1 = _to_usize(data, 0);
        let _param2 = _to_usize(data, 8);
        test_function20(_param0 ,_param1 ,_param2);
    });
}
