#![no_main]
#[macro_use]
extern crate libfuzzer_sys;
extern crate bytes;
fn _to_slice<T>(data:&[u8], start_index: usize, end_index: usize)->&[T] {
    let data_slice = &data[start_index..end_index];
    let (_, shorts, _) = unsafe {data_slice.align_to::<T>()};
    shorts
}

use bytes::buf::BufMut;

fn test_function13(_param0: &[u8]) {
    let mut _local0 = bytes::BytesMut::new();
    bytes::BytesMut::clear(&mut (_local0));
    bytes::buf::BufMut::put_slice(&mut (_local0) ,_param0);
}

fuzz_target!(|data: &[u8]| {
    //actual body emit
    if data.len() < 1 {return;}
    let dynamic_length = (data.len() - 0) / 1;
    let _param0 = _to_slice::<u8>(data, 0 + 0 * dynamic_length, data.len());
    test_function13(_param0);
});
