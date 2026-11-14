#[macro_use]
extern crate afl;
extern crate idna;
fn _to_slice<T>(data:&[u8], start_index: usize, end_index: usize)->&[T] {
    let data_slice = &data[start_index..end_index];
    let (_, shorts, _) = unsafe {data_slice.align_to::<T>()};
    shorts
}


fn test_function3(_param0: &[char]) {
    idna::punycode::encode(_param0);
}

fn main() {
    fuzz!(|data: &[u8]| {
        //actual body emit
        if data.len() < 4 {return;}
        let dynamic_length = (data.len() - 0) / 1;
        let _param0 = _to_slice::<char>(data, 0 + 0 * dynamic_length, data.len());
        test_function3(_param0);
    });
}
