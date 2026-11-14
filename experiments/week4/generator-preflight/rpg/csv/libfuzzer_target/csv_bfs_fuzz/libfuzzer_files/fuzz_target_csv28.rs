#![no_main]
#[macro_use]
extern crate libfuzzer_sys;
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


fn test_function28(_param0: bool ,_param1: u8) {
    let mut _local0 = csv::WriterBuilder::new();
    csv::WriterBuilder::double_quote(&mut (_local0) ,_param0);
    csv::WriterBuilder::escape(&mut (_local0) ,_param1);
}

fuzz_target!(|data: &[u8]| {
    //actual body emit
    if data.len() != 2 {return;}
    let _param0 = _to_bool(data, 0);
    let _param1 = _to_u8(data, 1);
    test_function28(_param0 ,_param1);
});
