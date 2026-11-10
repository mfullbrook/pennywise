import pytest

from pennywise import db


@pytest.fixture
def engine(tmp_path):
    engine = db.open_engine(tmp_path / "pennywise.db")
    db.migrate(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def conn(engine):
    with db.write(engine) as connection:
        yield connection
