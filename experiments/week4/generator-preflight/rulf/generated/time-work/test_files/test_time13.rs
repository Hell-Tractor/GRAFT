#[macro_use]
extern crate afl;
extern crate time;
fn _to_f64(data:&[u8], index: usize) -> f64 {
    let data_slice = &data[index..index+8];
    use std::convert::TryInto;
    let data_array:[u8;8] = data_slice.try_into().expect("slice with incorrect length");
    f64::from_le_bytes(data_array)
}

fn _to_f32(data:&[u8], index: usize) -> f32 {
    let data_slice = &data[index..index+4];
    use std::convert::TryInto;
    let data_array:[u8;4] = data_slice.try_into().expect("slice with incorrect length");
    f32::from_le_bytes(data_array)
}


fn test_function13(_param0 :f32 ,_param1 :f64) {
    let _local0 = time::Duration::seconds_f32(_param0);
    let _local1 = time::Duration::saturating_seconds_f64(_param1);
    let _ = time::Duration::saturating_sub(_local0 ,_local1);
}

fn main() {
    fuzz!(|data: &[u8]| {
        //actual body emit
        if data.len() != 12 {return;}
        let _param0 = _to_f32(data, 0);
        let _param1 = _to_f64(data, 4);
        test_function13(_param0 ,_param1);
    });
}
