#[macro_use]
extern crate afl;
extern crate unicode_segmentation;
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

fn _to_u16(data:&[u8], index:usize)->u16 {
    let data0 = _to_u8(data, index) as u16;
    let data1 = _to_u8(data, index+1) as u16;
    data0 << 8 | data1
}


fn test_function2(_param0 :usize ,_param1 :usize ,_param2 :bool ,_param3 :&str ,_param4 :usize ,_param5 :&str ,_param6 :usize) {
    let mut _local0 = unicode_segmentation::GraphemeCursor::new(_param0 ,_param1 ,_param2);
    let _local1 = unicode_segmentation::GraphemeCursor::next_boundary(&mut (_local0) ,_param3 ,_param4);
    let _ = unicode_segmentation::GraphemeCursor::prev_boundary(&mut (_local0) ,_param5 ,_param6);
}

fn main() {
    fuzz!(|data: &[u8]| {
        //actual body emit
        if data.len() < 35 {return;}
        let dynamic_length = (data.len() - 33) / 2;
        let _param0 = _to_usize(data, 0);
        let _param1 = _to_usize(data, 8);
        let _param2 = _to_bool(data, 16);
        let _param3 = _to_str(data, 33 + 0 * dynamic_length, 33 + 1 * dynamic_length);
        let _param4 = _to_usize(data, 17);
        let _param5 = _to_str(data, 33 + 1 * dynamic_length, data.len());
        let _param6 = _to_usize(data, 25);
        test_function2(_param0 ,_param1 ,_param2 ,_param3 ,_param4 ,_param5 ,_param6);
    });
}
