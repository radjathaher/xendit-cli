{
  description = "Xendit CLI";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        packages.default = pkgs.rustPlatform.buildRustPackage {
          pname = "xendit";
          version = "0.1.4";
          src = self;
          cargoLock = {
            lockFile = ./Cargo.lock;
          };
          meta = {
            mainProgram = "xendit";
            description = "Xendit CLI";
            homepage = "https://github.com/radjathaher/xendit-cli";
            license = pkgs.lib.licenses.mit;
          };
        };
      }
    );
}
