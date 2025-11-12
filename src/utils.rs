use std::sync::OnceLock;

use config::Config;

pub fn config() -> &'static Config {
    static CONFIG: OnceLock<Config> = OnceLock::new();
    CONFIG.get_or_init(|| {
        Config::builder()
            .add_source(config::File::with_name("config.yaml"))
            .add_source(config::Environment::with_prefix("PLF").separator("_"))
            .build().unwrap()
    })
}

pub fn read_file_to_string<P>(path: P) -> std::io::Result<String>
    where P: AsRef<std::path::Path> {
    let mut file = std::fs::File::open(path)?;
    let mut contents = String::new();
    use std::io::Read;
    file.read_to_string(&mut contents)?;
    Ok(contents)
}