import pytest

from iip.db.engine import parse_database_url


@pytest.mark.parametrize(
    "raw",
    [
        "postgres://u:p@db:5432/iip",
        "postgresql://u:p@db:5432/iip",
        "postgresql+asyncpg://u:p@db:5432/iip",
    ],
)
def test_normalizes_scheme_to_asyncpg(raw: str) -> None:
    config = parse_database_url(raw)

    assert config.url.drivername == "postgresql+asyncpg"
    assert config.url.host == "db"
    assert config.url.database == "iip"
    assert config.url.password == "p"


def test_translates_sslmode_and_drops_libpq_only_options() -> None:
    # The shape Neon hands out.
    config = parse_database_url(
        "postgresql://u:p@ep-x-123.us-east-1.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )

    assert dict(config.url.query) == {}
    assert config.connect_args["ssl"] == "require"


def test_keeps_unrelated_query_options() -> None:
    config = parse_database_url("postgresql://u:p@db/iip?application_name=iip&sslmode=disable")

    assert dict(config.url.query) == {"application_name": "iip"}
    assert "ssl" not in config.connect_args


def test_pooler_host_disables_statement_caches() -> None:
    config = parse_database_url(
        "postgresql://u:p@ep-x-123-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require"
    )

    assert config.uses_pooler is True
    assert config.connect_args["statement_cache_size"] == 0
    assert config.connect_args["prepared_statement_cache_size"] == 0
    name_func = config.connect_args["prepared_statement_name_func"]
    assert name_func() != name_func()  # unique per statement


def test_direct_host_keeps_statement_caches() -> None:
    config = parse_database_url("postgresql://u:p@ep-x-123.us-east-1.aws.neon.tech/neondb")

    assert config.uses_pooler is False
    assert "statement_cache_size" not in config.connect_args


def test_rejects_non_postgres_urls() -> None:
    with pytest.raises(ValueError, match="unsupported database scheme"):
        parse_database_url("mysql://u:p@db/iip")
