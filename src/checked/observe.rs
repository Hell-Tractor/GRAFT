//! Version-pinned, bounded observations; never observe pointer addresses as input influence.
use super::model::*;
use syn::parse_quote;

pub fn argument(plan:&SequencePlan,ty:&Ty,arg:&syn::Ident)->Option<(String,syn::Expr)> {
    let ty=match ty {Ty::Ref{inner,..}=>inner.as_ref(),t=>t};
    let Ty::Nominal{path,..}=ty else{return None};
    match (plan.crate_name.as_str(),plan.crate_version.as_str(),path.as_str()) {
        ("bytes","1.11.1","bytes::Bytes"|"bytes::BytesMut")=>Some(("bytes-len-prefix64-v1".into(),
            parse_quote!(format!("{:?}",(#arg.len(), &#arg[..#arg.len().min(64)]))))),
        ("fixedbitset","0.5.7","fixedbitset::FixedBitSet")=>Some(("fixedbitset-len-prefix64-v1".into(),
            parse_quote!(format!("{:?}",(#arg.len(),(0..#arg.len().min(64)).map(|i|#arg.contains(i)).collect::<Vec<bool>>()))))),
        ("chrono","0.4.19","chrono::NaiveDate"|"chrono::NaiveDateTime"|"chrono::NaiveTime"|
            "chrono::Duration"|"time::duration::Duration")=>Some(("chrono-value-debug-v1".into(),parse_quote!(format!("{:?}", &#arg)))),
        ("time","0.3.9","time::Date"|"time::Time"|"time::Duration"|"time::UtcOffset"|
            "time::OffsetDateTime"|"time::PrimitiveDateTime"|"time::Month")=>Some(("time-value-debug-v1".into(),parse_quote!(format!("{:?}", &#arg)))),
        _=>None,
    }
}
