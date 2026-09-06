import pytest
from gustsim import config, db

@pytest.fixture(autouse=True)
def isolated_data(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DATA',tmp_path/'data')
    db.initialize()
