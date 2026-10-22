import subprocess
import pytest
from pathlib import Path
SOURCE = subprocess.check_output(['git','show','36b3a9bec4d905839cbabe8010efde6fab8dcb7f:packages/actions/store.py'],encoding='utf-8')
BASE = {'__name__':'packages.actions.baseline_store','__file__':str(Path('packages/actions/store.py').resolve())}
exec(compile(SOURCE,BASE['__file__'],'exec'),BASE)

def test_fixed_template_action_kind_available():
    a={'id':'instantiate','name':'Instantiate fixed template','enabled':False,'effect':'write_local','input_schema':{'type':'object','properties':{},'additionalProperties':False},'execution':{'kind':'job_template','templateId':'jtpl_fixed','templateRevision':1}}
    assert BASE['validate_action'](a)['execution']['kind']=='job_template'

def test_ephemeral_transactions_do_not_change_source_revision(tmp_path):
    store=BASE['Store'](tmp_path)
    store.mutate(lambda s:s.update(jobExecutions={}),versioned=False)
    assert store.read()['revision']==0
