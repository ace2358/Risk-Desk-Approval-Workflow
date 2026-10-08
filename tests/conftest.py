import pytest

from workflow_engine.database import create_database, initialize_database, session_factory


@pytest.fixture
def sessions(tmp_path):
    database = create_database(f"sqlite:///{tmp_path / 'test.db'}")
    initialize_database(database)
    yield session_factory(database)
    database.dispose()