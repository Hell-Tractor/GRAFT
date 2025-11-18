use std::{collections::BTreeMap, sync::Arc};

use trustfall_rustdoc_adapter::{Crate, PackageIndex, RustdocAdapter};

use crate::error::{Error, Result};

pub struct DocQuerier {
    krate: Crate,
}

pub struct QueryBuilder<'a, K, V>
{
    krate: &'a Crate,
    variables: BTreeMap<K, V>,
}

pub type QueryResult = BTreeMap<Arc<str>, trustfall::FieldValue>;

impl DocQuerier {
    pub fn new(doc: &str) -> Result<Self> {
        let krate: Crate = serde_json::from_str(doc)?;
        Ok(DocQuerier { krate })
    }
    pub fn query<'a, K, V>(&'a self) -> QueryBuilder<'a, K, V> {
        QueryBuilder {
            krate: &self.krate,
            variables: BTreeMap::new(),
        }
    }
}

impl<'a, K, V> QueryBuilder<'a, K, V>
where
    K: Into<Arc<str>> + Ord,
    V: Into<trustfall::FieldValue>,
{
    pub fn add_variable(mut self, key: K, value: V) -> Self {
        self.variables.insert(key, value);
        self
    }
    pub fn execute<F, R>(self, gql: &str, f: F) -> Result<R>
    where
        F: for<'m> FnOnce(Box<dyn Iterator<Item = QueryResult> + 'm>) -> R {
        let index = PackageIndex::from_crate(self.krate);
        let adapter = Arc::new(RustdocAdapter::new(&index, None)); // 这一步很慢，最好想办法缓存
        let result = trustfall::execute_query(
            RustdocAdapter::schema(),
            (&*adapter).into(),
            gql,
            self.variables
        ).map_err(|e| Error::TrustfallQueryError(e.to_string()))?;
        let ret = f(result);
        Ok(ret)
    }
}