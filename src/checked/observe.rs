//! Version-pinned, bounded observations; never observe pointer addresses as input influence.
use super::model::*;
use syn::parse_quote;

pub fn argument(plan:&SequencePlan,ty:&Ty,arg:&syn::Ident)->Option<(String,syn::Expr)> {
    let ty=match ty {Ty::Ref{inner,..}=>inner.as_ref(),t=>t};
    let Ty::Nominal{path,args,..}=ty else{return None};
    if matches!(path.as_str(),"core::option::Option"|"std::option::Option")
        && matches!(args.as_slice(),[Ty::Primitive{..}]) {
        return Some(("optional-scalar-value-v1".into(),parse_quote!(format!("{:?}", &#arg))));
    }
    if plan.crate_name=="csv" && plan.crate_version=="1.3.1" && path.ends_with("::Option")
        && matches!(args.as_slice(),[Ty::Nominal{path,..}] if path=="csv::Position") {
        return Some(("csv-optional-position-v1".into(),parse_quote!(format!("{:?}",
            #arg.as_ref().map(|p|(p.byte(),p.line(),p.record()))))));
    }
    match (plan.crate_name.as_str(),plan.crate_version.as_str(),path.as_str()) {
        ("csv","1.3.1","csv::StringRecord")=>Some(("csv-string-record-prefix64-v1".into(),
            parse_quote!(format!("{:?}",(#arg.len(),#arg.as_slice().len(),
                &#arg.as_slice().as_bytes()[..#arg.as_slice().len().min(64)]))))),
        ("csv","1.3.1","csv::ByteRecord")=>Some(("csv-byte-record-prefix64-v1".into(),
            parse_quote!(format!("{:?}",(#arg.len(),#arg.as_slice().len(),
                &#arg.as_slice()[..#arg.as_slice().len().min(64)]))))),
        ("csv","1.3.1","csv::Position")=>Some(("csv-position-v1".into(),
            parse_quote!(format!("{:?}",(#arg.byte(),#arg.line(),#arg.record()))))),
        ("bumpalo","3.14.0","bumpalo::Bump")=>Some(("bumpalo-allocated-value-v1".into(),
            parse_quote!(format!("{:?}",(#arg.allocated_bytes(),#arg.allocated_bytes_including_metadata()))))),
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
