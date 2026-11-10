//! Canonical byte/file wrapper for the controlled execution track.
use clap::Parser;
use quote::{quote, ToTokens};
use std::path::PathBuf;
use syn::visit::Visit;

#[derive(Parser)]
struct Args {
    source: PathBuf,
    output: PathBuf,
    report: PathBuf,
    #[arg(long)]
    plan: Option<PathBuf>,
    #[arg(long, conflicts_with_all=["plan", "baseline"])]
    repair_plan: Option<PathBuf>,
    #[arg(long, requires="repair_plan")]
    standalone_output: Option<PathBuf>,
    #[arg(long, requires="repair_plan")]
    observe_objects: bool,
    #[arg(long, default_value="fuzz_target")]
    entry: String,
    #[arg(long)]
    baseline: bool,
    #[arg(long)]
    required_names: Option<PathBuf>,
}
#[derive(Default)]
struct Review { unsafe_blocks: usize, pointer_casts: usize, forbidden: Vec<String>, slice_types: Vec<String>, data_uses: usize, calls: Vec<String> }
impl<'a> Visit<'a> for Review {
    fn visit_expr_unsafe(&mut self, node: &'a syn::ExprUnsafe) { self.unsafe_blocks+=1; syn::visit::visit_expr_unsafe(self,node); }
    fn visit_expr_cast(&mut self, node: &'a syn::ExprCast) { if matches!(*node.ty,syn::Type::Ptr(_)){self.pointer_casts+=1;} syn::visit::visit_expr_cast(self,node); }
    fn visit_expr_path(&mut self, node: &'a syn::ExprPath) {
        if node.path.is_ident("data") {self.data_uses+=1;}
        let s=node.path.to_token_stream().to_string();
        if ["Command", "include", "Tcp", "Udp"].iter().any(|x|s.contains(x)) {self.forbidden.push(s);}
        syn::visit::visit_expr_path(self,node);
    }
    fn visit_expr_call(&mut self,node:&'a syn::ExprCall){
        if let syn::Expr::Path(p)=node.func.as_ref(){
            if let Some(seg)=p.path.segments.last(){
                self.calls.push(seg.ident.to_string());
                if seg.ident=="_to_slice" {
                    if let syn::PathArguments::AngleBracketed(a)=&seg.arguments {
                        for t in &a.args {self.slice_types.push(t.to_token_stream().to_string());}
                    }
                }
            }
        }
        syn::visit::visit_expr_call(self,node);
    }
    fn visit_expr_method_call(&mut self,node:&'a syn::ExprMethodCall){
        syn::visit::visit_expr(&mut *self,&node.receiver);
        self.calls.push(node.method.to_string());
        for arg in &node.args {syn::visit::visit_expr(&mut *self,arg);}
    }
}
struct KeepCalls;
impl syn::visit_mut::VisitMut for KeepCalls {
    fn visit_expr_mut(&mut self,e:&mut syn::Expr){
        syn::visit_mut::visit_expr_mut(self,e);
        if matches!(e,syn::Expr::Call(_)|syn::Expr::MethodCall(_)) {
            let original=e.clone(); *e=syn::parse_quote!(std::hint::black_box(#original));
        }
    }
}
fn run()->Result<(),Box<dyn std::error::Error>>{
    let a=Args::parse();let source=std::fs::read_to_string(&a.source)?;
    let mut file=syn::parse_file(&source)?;
    let mut review=Review::default();review.visit_file(&file);
    let mut reasons=vec![];
    if review.pointer_casts>0 {reasons.push("raw pointer cast: storage/alignment/preconditions not established".to_string());}
    if !review.forbidden.is_empty(){reasons.push("unapproved process/network operation".into());}
    let entry: syn::Ident;
    let mut repair_evidence=None;
    if let Some(ref planpath)=a.repair_plan {
        let result:graft::checked::PlanResult=serde_json::from_slice(&std::fs::read(planpath)?)?;
        let graft::checked::PlanResult::Planned{plan}=result else{return Err("rejected repair plan".into());};
        let prepared=graft::checked::repair::prepare_observed(&plan,&source,a.observe_objects)?;
        if let Some(ref output)=a.standalone_output {std::fs::write(output,&prepared.standalone)?;}
        file=syn::parse_file(&prepared.core)?;
        repair_evidence=Some(prepared.evidence);
        entry=quote::format_ident!("graft_body");
    } else if let Some(ref planpath)=a.plan {
        let result:graft::checked::PlanResult=serde_json::from_slice(&std::fs::read(planpath)?)?;
        let graft::checked::PlanResult::Planned{plan}=result else{return Err("rejected plan".into());};
        graft::checked::validate::retain_plan(&plan,&source)?;
        file.items.retain(|i|!matches!(i,syn::Item::Fn(f) if f.sig.ident=="main"));
        entry=quote::format_ident!("graft_body");
    } else if a.baseline {
        let main=file.items.iter().find_map(|i|if let syn::Item::Fn(f)=i {if f.sig.ident=="main"{Some(f)}else{None}}else{None}).ok_or("missing main")?;
        let tokens=main.block.stmts.iter().find_map(|s|match s{
            syn::Stmt::Macro(m) if ["fuzz","fuzz_nohook"].iter().any(|s|m.mac.path.segments.last().unwrap().ident==s)=>Some(m.mac.tokens.clone()),
            syn::Stmt::Expr(syn::Expr::Macro(m),_) if ["fuzz","fuzz_nohook"].iter().any(|s|m.mac.path.segments.last().unwrap().ident==s)=>Some(m.mac.tokens.clone()),_=>None}).ok_or("baseline has no fuzz closure")?;
        let closure:syn::ExprClosure=syn::parse2(tokens)?;
        let body=closure.body;
        let mut body_review=Review::default();body_review.visit_expr(&body);
        review.unsafe_blocks+=body_review.unsafe_blocks;
        review.pointer_casts+=body_review.pointer_casts;
        review.forbidden.extend(body_review.forbidden);
        review.slice_types.extend(body_review.slice_types);
        review.data_uses+=body_review.data_uses;
        review.calls.extend(body_review.calls);
        file.items.retain(|i|!matches!(i,syn::Item::Fn(f) if f.sig.ident=="main"||f.sig.ident=="_read_data")
            && !matches!(i,syn::Item::ExternCrate(e) if e.ident=="afl"));
        file.items.push(syn::parse_quote!(pub fn graft_input(data:&[u8]) { #body }));
        entry=quote::format_ident!("graft_input");
    } else {
        file.items.retain(|i|!matches!(i,syn::Item::Fn(f) if f.sig.ident=="main"));
        entry=quote::format_ident!("{}",a.entry);
        if !file.items.iter().any(|i|matches!(i,syn::Item::Fn(f) if f.sig.ident==entry)){reasons.push("missing byte-input entry".into());}
        if review.data_uses==0 {reasons.push("no byte-input use in AST".into());}
    }
    if review.pointer_casts>0 && !reasons.iter().any(|r|r.starts_with("raw pointer cast")) {
        reasons.push("raw pointer cast in reviewed closure: storage/alignment/preconditions not established".to_string());
    }
    if !review.forbidden.is_empty() && !reasons.iter().any(|r|r.contains("process/network")) {
        reasons.push("unapproved process/network operation in reviewed closure".into());
    }
    if let Some(path)=a.required_names {
        let required:Vec<String>=serde_json::from_slice(&std::fs::read(path)?)?;
        let mut found=review.calls.iter();
        if !required.iter().all(|name|found.any(|actual|actual==name)) {
            reasons.push("required random API names are missing or reordered in AST traversal order; this checks names, not types or taint".into());
        }
    }
    let unsafe_calls=["set_len", "advance_mut", "toggle_unchecked", "set_unchecked", "copy_bit_unchecked", "contains_unchecked", "insert_unchecked", "remove_unchecked"];
    if review.unsafe_blocks>0 {
        let align_only=source.matches("unsafe").count()==source.matches("align_to::<T>()").count()
            && review.slice_types.iter().all(|t|t=="u8");
        let bump_raw=source.contains("bumpalo::Bump::iter_allocated_chunks_raw")
            && !source.contains("*mut") && !source.contains("*const")
            && !source.contains(".next(") && !source.contains("alloc(");
        if !align_only && !bump_raw {reasons.push("explicit unsafe block requires an unimplemented contract proof; admission rejected, not a library bug".into());}
        if review.calls.iter().any(|name|unsafe_calls.contains(&name.as_str())) {
            reasons.push("unchecked length/index operation without matching generated precondition guard".into());
        }
    }
    let safe_env_use=if a.plan.is_none(){!source.contains("std::env::") && !source.contains("std::fs::") && !source.contains("include!")}else{true};
    if !a.baseline && !safe_env_use {reasons.push("unapproved environment/filesystem access in model source".into());}
    let report=serde_json::json!({"accepted":reasons.is_empty(),"reasons":reasons,"unsafe_blocks":review.unsafe_blocks,"raw_pointer_casts":review.pointer_casts,"data_uses":review.data_uses,"called_names":review.calls,"repair_contract":repair_evidence,"policy":"same canonical wrapper; no unreviewed direct unsafe admission; byte-input AST use and requested-name order are not complete taint or API-resolution proofs"});
    std::fs::write(a.report,serde_json::to_vec_pretty(&report)?)?;
    if !report["accepted"].as_bool().unwrap(){std::process::exit(3);}
    if a.plan.is_none() && a.repair_plan.is_none(){syn::visit_mut::VisitMut::visit_file_mut(&mut KeepCalls,&mut file);}
    let items=file.items;
    let output=quote!{#(#items)* fn main(){
        use std::io::Read;
        let data:Vec<u8>=if std::env::var_os("GRAFT_PROBE_ARGS").is_some(){std::env::args().skip(1).map(|v|v.parse::<u8>().unwrap()).collect()}
        else if let Some(p)=std::env::args_os().nth(1){std::fs::read(p).unwrap()}
        else {let mut v=Vec::new();std::io::stdin().read_to_end(&mut v).unwrap();v};
        std::hint::black_box(#entry(&data));
    }};
    std::fs::write(a.output,output.to_string())?;Ok(())
}
fn main(){if let Err(e)=run(){eprintln!("{e}");std::process::exit(1);}}
