"""Reproduce MA negative controls against the exact rejected helper body."""
def pytest_configure(config):
    import subprocess
    from packages.core import background_jobs as jobs
    source = subprocess.check_output(['git','show','2686c8d4:packages/core/background_jobs.py'],encoding='utf-8')
    start = source.index('def _terminate_job_processes(')
    end = source.index('\ndef register_completed_job_retention',start)
    namespace = dict(vars(jobs))
    exec(compile(source[start:end], '<2686c8d4 cleanup helper>', 'exec'), namespace)
    jobs._terminate_job_processes = namespace['_terminate_job_processes']
