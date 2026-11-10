#![allow(dead_code)]
pub struct Left<T>(T);
pub struct Right<T>(T);
pub struct Token(u8);
pub struct Cycle;
pub fn left_u8(x: u8) -> Left<u8> {
    Left(x)
}
pub fn right_i32(x: i32) -> Right<i32> {
    Right(x)
}
pub fn right_u8(x: u8) -> Right<u8> {
    Right(x)
}
pub fn same<T>(_: Left<T>, _: Right<T>) {}
pub fn make<T>(x: T) -> Left<T> {
    Left(x)
}
pub fn independent(_: Left<u8>, _: Left<i32>) {}
pub fn nested<T>(_: Vec<Box<(T, T)>>) {}
pub fn token(x: u8) -> Token {
    Token(x)
}
pub fn moved(_: Token, _: Token) {}
pub fn mutable(_: &mut Token, _: &mut Token) {}
pub fn shared(_: &Token, _: &Token) {}
pub fn slice(_: &[u8]) {}
pub fn array(_: [u8; 4]) {}
pub fn result(x: u8) -> Result<Token, ()> {
    if x == 0 { Err(()) } else { Ok(Token(x)) }
}
pub fn option(x: u8) -> Option<Token> {
    if x == 0 { None } else { Some(Token(x)) }
}
pub fn use_token(_: Token) {}
pub fn cycle(_: Cycle) -> Cycle {
    Cycle
}
pub fn use_cycle(_: Cycle) {}
pub trait Tag {}
impl Tag for u8 {}
pub struct Wrap<T>(T);
impl<T: Tag> Tag for Wrap<T> {}
pub fn wrap<T>(x: T) -> Wrap<T> {
    Wrap(x)
}
pub fn bounded<T: Tag>(_: Wrap<T>) {}
pub fn needs_tag<T: Tag>(_: T) {}
pub mod other {
    pub trait Tag {}
}
pub fn other_tag<T: other::Tag>(_: T) {}
pub trait Associated {
    type Item;
}
pub fn projection<T: Associated>(_: T::Item) {}
pub fn associated_binding<T: Associated<Item = u8>>(_: T) {}
pub fn const_array<const N: usize>(_: [u8; N]) {}
pub fn higher<T: for<'a> Fn(&'a u8)>(_: T) {}
pub fn raw(_: *const u8) {}
/// # Safety
/// p must point to a readable u8.
pub unsafe fn unsafe_api(p: *const u8) -> u8 {
    unsafe { *p }
}
pub fn borrowed(x: &u8) -> &u8 {
    x
}
pub fn scalar(x: u8) -> u8 {
    x.wrapping_add(1)
}
pub fn scalar_mut(x: &mut u8) -> u8 {
    *x = x.wrapping_add(1);
    *x
}
pub use core::time::Duration as PublicDuration;
pub fn public_duration(x:u64)->PublicDuration { PublicDuration::from_secs(x) }
pub fn scalar_u32(x:u32)->u32 {x.wrapping_add(1)}
pub fn process_nonce()->u32 {std::process::id()}
pub struct Buffer<T>(T);
pub fn buffer_i32(x: i32) -> Buffer<i32> {
    Buffer(x)
}
pub fn combine<T>(_: Vec<T>, _: Buffer<T>) {}
pub struct Extra;
pub fn extra(_: Token) -> Extra {
    Extra
}
pub fn hold_then_make(_: &Token, _: Extra) {}
pub struct Choice;
pub fn bad_choice(_: Cycle) -> Choice {
    Choice
}
pub fn good_choice(_: u8) -> Choice {
    Choice
}
pub fn other_choice(_:u8)->Choice {Choice}
pub fn choose(_: Choice) {}
pub struct Holder<'a>(&'a u8);
pub fn held_ref(x: &u8) -> Holder<'_> {
    Holder(x)
}
pub struct Method<T>(T);
impl<T> Method<T> {
    pub fn new(x: T) -> Self {
        Self(x)
    }
    pub fn visit(&self) -> u8 {
        1
    }
}

pub struct Bytes([u8;1]);
static OBSERVER_CALLS:std::sync::atomic::AtomicUsize=std::sync::atomic::AtomicUsize::new(0);
impl Bytes {
    pub fn len(&self)->usize {OBSERVER_CALLS.fetch_add(1,std::sync::atomic::Ordering::SeqCst);1}
}
impl std::ops::Index<std::ops::RangeTo<usize>> for Bytes {
    type Output=[u8];
    fn index(&self,r:std::ops::RangeTo<usize>)->&[u8] {&self.0[r]}
}
pub fn make_bytes()->Bytes {Bytes([0])}
pub fn read_bytes(_: &Bytes)->u8 {0}
pub fn observer_count()->usize {OBSERVER_CALLS.load(std::sync::atomic::Ordering::SeqCst)}
pub fn optional_scalar(x:Option<u8>)->u8 {x.unwrap_or(0)}
static ARGUMENT_CALLS:std::sync::atomic::AtomicUsize=std::sync::atomic::AtomicUsize::new(0);
pub fn counted_input()->u8 {ARGUMENT_CALLS.fetch_add(1,std::sync::atomic::Ordering::SeqCst);1}
pub fn argument_count()->usize {ARGUMENT_CALLS.load(std::sync::atomic::Ordering::SeqCst)}
