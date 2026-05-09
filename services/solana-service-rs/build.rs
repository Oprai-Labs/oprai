fn main() -> Result<(), Box<dyn std::error::Error>> {
    let proto_root = "../../proto";

    // Compile common protos
    tonic_build::configure()
        .build_server(true)
        .build_client(false)
        // Suppress dead-code warnings on prost-generated structs that are not
        // all wired up yet (Pagination, PaginatedResponse, DateRange, Error, Empty).
        .type_attribute(".", "#[allow(dead_code)]")
        .compile_protos(
            &[
                &format!("{proto_root}/common/health.proto"),
                &format!("{proto_root}/common/types.proto"),
            ],
            &[proto_root],
        )?;

    // If a solana-specific proto exists in the future, compile it here:
    // tonic_build::configure()
    //     .build_server(true)
    //     .build_client(false)
    //     .compile_protos(
    //         &[&format!("{proto_root}/solana/solana.proto")],
    //         &[proto_root],
    //     )?;

    println!("cargo:rerun-if-changed={proto_root}");
    Ok(())
}
