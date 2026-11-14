#[macro_use]
extern crate afl;
extern crate time;
fn _to_u8(data:&[u8], index:usize)->u8 {
    data[index]
}


fn test_function41(_param0 :u8) {
    let _local0 = time::OffsetDateTime::now_utc();
    let _local1 = time::OffsetDateTime::month(_local0);
    let _ = time::Month::nth_next(_local1 ,_param0);
}

fn main() {
    fuzz!(|data: &[u8]| {
        //actual body emit
        if data.len() != 1 {return;}
        let _param0 = _to_u8(data, 0);
        test_function41(_param0);
    });
}
