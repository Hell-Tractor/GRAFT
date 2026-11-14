#[macro_use]
extern crate afl;
extern crate csv;
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


fn test_function26(_param0: u8 ,_param1: bool) {
    let mut _local0 = csv::ReaderBuilder::new();
    csv::ReaderBuilder::delimiter(&mut (_local0) ,_param0);
    csv::ReaderBuilder::has_headers(&mut (_local0) ,_param1);
}

fn main() {
    fuzz!(|data: &[u8]| {
        //actual body emit
        if data.len() != 2 {return;}
        let _param0 = _to_u8(data, 0);
        let _param1 = _to_bool(data, 1);
        test_function26(_param0 ,_param1);
    });
}
