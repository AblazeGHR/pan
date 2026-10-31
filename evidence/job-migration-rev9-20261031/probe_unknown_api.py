import tempfile,sys,json,hashlib
from pathlib import Path
sys.path[:0]=[str(Path.cwd()),str(Path.cwd()/'tests')]
from tests.test_job_migration_qq_runtime import source_for
from tests.test_job_migration_runtime_contract import runtime_context
from scripts.job_final_migration.inventory import build_inventory
from scripts.job_final_migration.mapping import Mapper
with tempfile.TemporaryDirectory(prefix='pan-rev9-unknown-') as owned:
    root=Path(owned)/'source'; path,source=source_for(root)
    source['action']['api']='unknown-ordinal9-action'; path.write_text(json.dumps(source),encoding='utf-8'); before=path.read_bytes()
    item,=build_inventory(root,runtime_delivery=True)['instances']; mapper=Mapper(runtime_context(root))
    try:
        mapper.map(item,source)
        raise AssertionError('unknown API accepted')
    except ValueError as error:
        rejection=str(error)
    assert not mapper.objects and not item['targetIds'] and path.read_bytes()==before
    assert any(repr(source['action']['api']) in reason for reason in item['rejectionReasons'])
    report={'decision':'job-unknown-api-reject-approved-20261031','field':'action.api','value':source['action']['api'],'classification':item['classification'],'reasonCodes':item['reasonCodes'],'rejectionReasons':item['rejectionReasons'],'mapperRejection':rejection,'generatedCanonicalObjects':len(mapper.objects),'targetIds':item['targetIds'],'sourceUnchanged':True,'sourceSha256':hashlib.sha256(before).hexdigest()}
report['ownedRootRemoved']=not Path(owned).exists()
Path('evidence/job-migration-rev9-20261031/unknown-api.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
