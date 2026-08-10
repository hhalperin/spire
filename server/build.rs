//! Tell cargo the binary depends on the bundled client.
//!
//! `include_str!("../assets/app.html")` does not always register a rebuild
//! dependency, which meant editing app/ and re-running the bundler produced a
//! server that still served the previous HTML — a genuinely confusing failure,
//! because the file on disk was correct and the thing being served was not.

fn main() {
    println!("cargo:rerun-if-changed=assets/app.html");
}
