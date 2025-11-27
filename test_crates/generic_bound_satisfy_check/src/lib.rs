pub trait TraitA<T> {}

pub fn f1<U, T>(x: U)
where
    U: TraitA<T>,
    T: Copy
{
}

pub struct S1;

impl TraitA<i32> for S1 {}

pub struct S2;
pub struct S3;

impl TraitA<S2> for S3 {}

pub enum E1 {
    V1, V2
}

impl TraitA<i32> for E1 {}

pub fn f2<T, U>(x: T)
where
    T: TraitA<U>,
{}

pub fn f3(x: impl TraitA<i32>) {
}