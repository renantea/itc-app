import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from itc import create_app
from itc.config import TestConfig
from itc.db import init_db


@pytest.fixture()
def app():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(db_path)

    class Cfg(TestConfig):
        DATABASE = db_path

    application = create_app(Cfg)
    yield application
    os.unlink(db_path)


@pytest.fixture()
def client(app):
    return app.test_client()
