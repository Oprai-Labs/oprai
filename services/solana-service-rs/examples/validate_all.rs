fn main() {
    use std::str::FromStr;
    let checks = [
        // Correct Marinade from SDK
        (
            "MARINADE_PROG_CORRECT",
            "MarBmsSgKXdrN1egZf5sqe1TMai9K1rChYNDJgjq7aD",
        ),
        (
            "MARINADE_PROG_WRONG",
            "MarBmsSgKXdrN1egZf5sqe1TMhq9GVWy3zGSUymDA2tT",
        ),
        (
            "MARINADE_STATE",
            "8szGkuLTAux9XMgZ2vtY39jVSowEcpBfFfD8hXSEqdGC",
        ),
        (
            "MARINADE_REF_PROG",
            "MR2LqxoSbw831bNy68utpu5n4YqBH3AzDmddkgk9LQv",
        ),
    ];
    for (name, addr) in &checks {
        match solana_sdk::pubkey::Pubkey::from_str(addr) {
            Ok(_) => println!("OK  {name:30} {addr}"),
            Err(e) => println!("ERR {name:30} {addr} [{e}]"),
        }
    }
}
