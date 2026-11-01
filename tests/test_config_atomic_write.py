"""Config replacement keeps original bytes on failure and retries transient Windows contention."""
import json
import pytest
from packages.core import config


def test_windows_replace_contention_retries_original_atomic_candidate(tmp_path, monkeypatch):
    path=tmp_path/'config.json'
    monkeypatch.setattr(config,'CONFIG_FILE',path)
    path.write_text('{"before":true}',encoding='utf8')
    replace=config.os.replace
    calls=[]
    def contended(source,target):
        calls.append(str(source))
        if len(calls)==1:
            assert json.loads(path.read_text())=={'before':True}
            error=PermissionError('owned Windows sharing contention');error.winerror=5
            raise error
        replace(source,target)
    monkeypatch.setattr(config.os,'replace',contended)
    config.save_config({'after':'原文'})
    assert json.loads(path.read_text(encoding='utf8'))=={'after':'原文'}
    assert len(calls)==2 and calls[0]==calls[1]


def test_persistent_replace_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    path=tmp_path/'config.json';path.write_text('{"before":true}',encoding='utf8')
    monkeypatch.setattr(config,'CONFIG_FILE',path)
    def denied(*args): raise OSError('owned disk error')
    monkeypatch.setattr(config.os,'replace',denied)
    with pytest.raises(OSError,match='owned disk error'): config.save_config({'after':False})
    assert json.loads(path.read_text())=={'before':True}


def test_persistent_windows_permission_failure_stops_at_bound(tmp_path, monkeypatch):
    path=tmp_path/'config.json';path.write_text('{"before":true}',encoding='utf8')
    monkeypatch.setattr(config,'CONFIG_FILE',path)
    calls=[]
    def denied(*args):
        calls.append(1)
        error=PermissionError('owned persistent Windows denial');error.winerror=5
        raise error
    monkeypatch.setattr(config.os,'replace',denied)
    monkeypatch.setattr(config.time,'sleep',lambda _:None)
    with pytest.raises(PermissionError,match='owned persistent Windows denial'): config.save_config({'after':False})
    assert len(calls)==8 and json.loads(path.read_text())=={'before':True}
