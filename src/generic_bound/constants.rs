use std::sync::OnceLock;

use rustdoc_types::{GenericBound, Id};
use tracing::warn;

const VERY_BIG_ID: u32 = 1000000;
const INT_TRAITS: &[&str] = &[
    "Copy", "Clone",
    "Default",
    "PartialEq", "Eq", "PartialOrd", "Ord",
    "Hash",
    "Debug", "Display", "Binary", "Octal", "LowerHex", "UpperHex",
    "Add", "Sub", "Mul", "Div", "Rem",
    "Neg",
    "BitAnd", "BitOr", "BitXor", "Shl", "Shr",
    "Send", "Sync",
];
const UINT_TRAITS: &[&str] = &[
    "Copy", "Clone",
    "Default",
    "PartialEq", "Eq", "PartialOrd", "Ord",
    "Hash",
    "Debug", "Display", "Binary", "Octal", "LowerHex", "UpperHex",
    "Add", "Sub", "Mul", "Div", "Rem",
    "BitAnd", "BitOr", "BitXor", "Shl", "Shr",
    "Send", "Sync",
];
const FLOAT_TRAITS: &[&str] = &[
    "Copy", "Clone",
    "Default",
    "PartialEq", "PartialOrd",
    "Debug", "Display", "LowerExp", "UpperExp",
    "Add", "Sub", "Mul", "Div", "Rem",
    "Neg",
    "Send", "Sync",
];
const BOOL_TRAITS: &[&str] = &[
    "Copy", "Clone",
    "Default",
    "PartialEq", "Eq", "PartialOrd", "Ord",
    "Hash",
    "Debug", "Display",
    "Not",
    "Send", "Sync",
];
const CHAR_TRAITS: &[&str] = &[
    "Copy",  "Clone",
    "Default",
    "PartialEq",  "Eq",  "PartialOrd",  "Ord",
    "Hash",
    "Debug", "Display",
    "Send", "Sync",
];
const UNIT_TRAITS: &[&str] = &[
    "Copy", "Clone",
    "Default",
    "PartialEq", "Eq", "PartialOrd", "Ord",
    "Hash",
    "Debug", "Display",
    "Send", "Sync",
];
const STR_TRAITS: &[&str] = &[
    "Debug", "Display", "PartialEq", "Eq", "PartialOrd", "Ord", "Hash", "Send", "Sync",
];

macro_rules! trait_bounds {
    ( $StartId: expr, $( $TraitName: expr ),+ $(,)? ) => {
        {
            let mut bounds = Vec::new();
            let mut id_counter = $StartId as u32;
            $(
                bounds.push(GenericBound::TraitBound{
                    trait_: rustdoc_types::Path {
                        path: $TraitName.to_owned(),
                        id: Id(id_counter),
                        args: None,
                    },
                    generic_params: vec![],
                    modifier: rustdoc_types::TraitBoundModifier::None,
                });
                #[allow(unused_assignments)] // suppress warnings for id_counter
                {
                    id_counter += 1;
                }
            )+
            bounds
        }
    }
}

pub fn get_primitive_traits(t: &str) -> Vec<GenericBound> {
    static ALL_TRAITS: OnceLock<Vec<GenericBound>> = OnceLock::new();
    let all_traits = ALL_TRAITS.get_or_init(|| {
        trait_bounds!(
            VERY_BIG_ID,
            "Clone", "Copy",
            "Default",
            "PartialEq", "Eq", "PartialOrd", "Ord",
            "Hash",
            "Debug", "Display", "Binary", "Octal", "LowerHex", "UpperHex", "LowerExp", "UpperExp",
            "Add", "Sub", "Mul", "Div", "Rem",
            "Neg",
            "Not",
            "BitAnd", "BitOr", "BitXor", "Shl", "Shr",
            "Send", "Sync",
        )
    });
    all_traits.clone()
        .into_iter()
        .filter(|b| {
            let GenericBound::TraitBound { trait_, .. } = b else {
                panic!("Only TraitBound is expected here");
            };
            match t {
                "i8" | "i16" | "i32" | "i64" | "i128" | "isize" => INT_TRAITS.contains(&trait_.path.as_str()),
                "u8" | "u16" | "u32" | "u64" | "u128" | "usize" => UINT_TRAITS.contains(&trait_.path.as_str()),
                "f32" | "f64" => FLOAT_TRAITS.contains(&trait_.path.as_str()),
                "bool" => BOOL_TRAITS.contains(&trait_.path.as_str()),
                "char" => CHAR_TRAITS.contains(&trait_.path.as_str()),
                "()" => UNIT_TRAITS.contains(&trait_.path.as_str()),
                "str" => STR_TRAITS.contains(&trait_.path.as_str()),
                _ => panic!("Only primitive types are expected here, found `{}`", t),
            }
        })
        .collect()
}

pub const AUTO_UNWRAP_TYPES: &[&str] = &[
    "Option",
    "Result",
    "Vec",
    "Box", "Arc", "Rc", "Mutex", "RwLock", "Cell", "RefCell", "Cow", "Pin",
];

pub const STD_STRUCT_LIST: &[&str] = &[
    // "String", "alloc::string::String",
    "Vec",
    "Option",
    "Result",
    // "Box", "Arc", "Rc", "Mutex", "RwLock", "Cell", "RefCell", "Cow", "Pin",
];

pub const AUTO_UNWRAP_REF_TYPES: &[&str] = &[
    "AsRef", "Deref",
    "Arc", "Rc", "Box", "Cell", "RefCell", "Mutex", "RwLock", "Cow", "Pin"
];

pub const PRIMITIVE_STRUCTS: &[&str] = &[
    "String",
];

pub fn get_std_struct_traits(name: &str) -> Vec<GenericBound> {
    match name {
        "String" | "alloc::string::String" => trait_bounds!(
            VERY_BIG_ID + 100,
            "Clone", "Debug", "Default", "PartialEq", "Eq", "PartialOrd", "Ord", "Hash", "Send", "Sync",
        ),
        "Vec" => trait_bounds!(
            VERY_BIG_ID + 200,
            "Clone", "Debug", "Default", "PartialEq", "Eq", "PartialOrd", "Ord", "Hash", "Send", "Sync",
        ),
        "Option" => trait_bounds!(
            VERY_BIG_ID + 300,
            "Clone", "Debug", "Default", "PartialEq", "Eq", "PartialOrd", "Ord", "Hash", "Send", "Sync",
        ),
        "Result" => trait_bounds!(
            VERY_BIG_ID + 400,
            "Clone", "Debug", "PartialEq", "Eq", "PartialOrd", "Ord", "Hash", "Send", "Sync",
        ),
        "Box" => trait_bounds!(
            VERY_BIG_ID + 500,
            "Clone", "Debug", "Default", "PartialEq", "Eq", "PartialOrd", "Ord", "Hash", "Send", "Sync",
        ),
        "Arc" => trait_bounds!(
            VERY_BIG_ID + 600,
            "Clone", "Debug", "Default", "PartialEq", "Eq", "PartialOrd", "Ord", "Hash", "Send", "Sync",
        ),
        "Rc" => trait_bounds!(
            VERY_BIG_ID + 700,
            "Clone", "Debug", "Default", "PartialEq", "Eq", "PartialOrd", "Ord", "Hash",
        ),
        "Mutex" => trait_bounds!(
            VERY_BIG_ID + 800,
            "Send", "Sync",
        ),
        "RwLock" => trait_bounds!(
            VERY_BIG_ID + 900,
            "Send", "Sync",
        ),
        "Cell" => trait_bounds!(
            VERY_BIG_ID + 1000,
            "Send", "Sync",
        ),
        "RefCell" => trait_bounds!(
            VERY_BIG_ID + 1100,
            "Send", "Sync",
        ),
        "Cow" => trait_bounds!(
            VERY_BIG_ID + 1200,
            "Clone", "Debug", "Default", "PartialEq", "Eq", "PartialOrd", "Ord", "Hash", "Send", "Sync",
        ),
        "Pin" => trait_bounds!(
            VERY_BIG_ID + 1300,
            "Send", "Sync",
        ),
        _ => {
            warn!("No predefined traits for std struct `{}`", name);
            vec![]
        },
    }
}

#[cfg(test)]
mod test {
    use super::*;
    #[test]
    fn test_get_primitive_traits() {
        let int_traits = get_primitive_traits("i32");
        assert_eq!(int_traits.len(), INT_TRAITS.len());
    }
}