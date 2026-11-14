mod hidden {
    pub fn exposed_by_alias() -> u8 { 1 }
    pub fn not_exported() -> u8 { 2 }
    pub struct Hidden;
    impl Hidden {
        pub fn hidden_method(&self) -> u8 { 3 }
    }
}
pub use hidden::exposed_by_alias as public_alias;

pub struct Public;
impl Public {
    pub fn public_method(&self) -> u8 { 4 }
    fn private_method(&self) -> u8 { 5 }
}

pub trait PublicTrait {
    fn default_method(&self) -> u8 { 6 }
}
impl PublicTrait for Public {
    fn default_method(&self) -> u8 { 7 }
}
