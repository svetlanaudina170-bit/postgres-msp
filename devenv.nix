# =========================================================================
# VERSION: 1.2.0
# Path: devenv.nix
# Изменения в 1.2.0:
#  - Порт локального devenv-Postgres переведён с 5444 на 5432 — чтобы
#    совпадать с портом по умолчанию в DATABASE_URL (.env). Раньше это
#    было расхождение (см. VERSION 1.1.0 ниже) — теперь оба указывают
#    на один и тот же порт по умолчанию. Если на хосте уже что-то
#    слушает 5432 — либо остановите тот процесс, либо снова разведите
#    порты (здесь и в DATABASE_URL) на разные значения.
#  - Снята пометка про fastmcp из версии 1.1.0: пакет добавлять не
#    нужно — FastMCP входит в уже имеющуюся зависимость mcp[cli] как
#    mcp.server.fastmcp. Сам mcp_server.py переписан на этот API
#    (см. VERSION 2.0.0 файла src/postgres_mcp/autonomous/mcp_server.py).
# Изменения в 1.1.0:
#  - ВКЛЮЧЁН services.postgres (был закомментирован в исходном файле).
#    Даёт локальный Postgres для разработки с расширением
#    pg_stat_statements — необходим для функции "Top Queries"
#    (PostgresClient.get_top_queries() в pg_client.py).
# =========================================================================
{ pkgs, lib, config, inputs, ... }:
let
  pkgs-unstable = import inputs.nixpkgs-unstable { system = pkgs.stdenv.system; };
in
{
  # https://devenv.sh/basics/
  env.GREET = "devenv";

  # https://devenv.sh/packages/
  packages = with pkgs; [
    git
    postgresql_16
    pkgs-unstable.libgcc
  ];
  # env = {
  #   LD_LIBRARY_PATH = "${pkgs-unstable.icu}/lib:${pkgs-unstable.gcc.cc.lib}/lib64:${pkgs-unstable.gcc.cc.lib}/lib";
  #   NIX_GLIBC_PATH = "${pkgs-unstable.gcc.cc.lib}/lib64:${pkgs-unstable.gcc.cc.lib}/lib";
  # };

  # https://devenv.sh/languages/
  languages.javascript = {
    enable = true;
    package = pkgs-unstable.nodejs;
    corepack.enable = true;
  };

  languages.python = {
    enable = true;
    # version = "3.12";
    uv = {
      enable = true;
      sync = {
        enable = true;
        allExtras = true;
      };
    };
  };

  dotenv.enable = true;

  # https://devenv.sh/processes/
  # processes.cargo-watch.exec = "cargo-watch";

  # https://devenv.sh/services/
  # Локальный Postgres для разработки. ВКЛЮЧЕНО (было закомментировано) —
  # нужен для расширения pg_stat_statements (см. заголовок файла выше).
  # Порт 5432 — совпадает с портом по умолчанию в DATABASE_URL.
  # Чтобы отключить: enable = false, затем удалите .devenv/state/postgres.
  services.postgres = {
    enable = true;
    port = 5432;
    listen_addresses = "127.0.0.1";
    initialScript = ''
      CREATE USER postgres SUPERUSER;
      ALTER USER postgres WITH PASSWORD 'mysecretpassword';
      CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
    ''; # SELECT * FROM pg_stat_statements LIMIT 1;
    settings.shared_preload_libraries = "pg_stat_statements";
  };

  # https://devenv.sh/scripts/
  scripts.hello.exec = ''
    echo hello from $GREET
  '';

  enterShell = ''
    hello
    echo "Crystal DBA Agent Development Environment"
  '';

  # https://devenv.sh/tasks/
  # tasks = {
  #   "myproj:setup".exec = "mytool build";
  #   "devenv:enterShell".after = [ "myproj:setup" ];
  # };

  # https://devenv.sh/tests/
  enterTest = ''
    echo "Running tests"
    git --version | grep --color=auto "${pkgs.git.version}"
  '';

  # https://devenv.sh/git-hooks/
  # git-hooks.hooks.shellcheck.enable = true;

  # See full reference at https://devenv.sh/reference/options/
}
