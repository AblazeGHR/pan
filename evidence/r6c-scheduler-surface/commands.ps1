# Run from repository root. Tests use owned temporary data; no protected ports or production build.
python scripts/run_tests_isolated.py -- python -m pytest tests/test_scheduler_api.py tests/test_scheduler_mcp.py tests/test_job_final_retirement.py -q -o addopts= --tb=short --junitxml=evidence/r6c-scheduler-surface/baseline.xml
python scripts/audit_scheduler_surface.py evidence/r6c-scheduler-surface/references-after.json
python scripts/run_tests_isolated.py -- python scripts/audit_scheduler_surface.py evidence/r6c-scheduler-surface/surface-after.json --snapshot
python scripts/run_tests_isolated.py -- python -m pytest tests/test_scheduler_api.py tests/test_scheduler_mcp.py tests/test_scheduler_surface_retirement.py tests/test_job_final_retirement.py tests/test_scheduler_canonical_migration.py tests/test_scheduler_time_events.py tests/test_authorization_mcp.py tests/test_job_final_transports.py tests/test_job_final_creators.py tests/test_job_final_http_service.py tests/test_job_path_flip.py::test_production_route_snapshot_and_same_handler_aliases tests/test_handoff_job_retarget.py tests/test_jobs_completed_retention.py tests/test_jobs_api.py tests/test_pan_cli.py tests/test_scheduler_integration.py tests/test_scheduler_store.py tests/test_scheduler_cron.py -q -o addopts= --tb=short --junitxml=evidence/r6c-scheduler-surface/final.xml
python scripts/run_tests_isolated.py -- python evidence/r6c-scheduler-surface/replay_baseline.py
# In packages/web:
# pnpm install --frozen-lockfile --ignore-scripts
# pnpm exec vitest run src/views/AutomationWorkspace.test.tsx src/views/CanonicalJobsView.test.tsx src/stores/automationSession.test.tsx
# git diff --check
# Historical initial baseline command above was run before source edits, not against current tests.

# Final rerun after isolating the inherited R5 TestClient lifecycle:
python scripts/run_tests_isolated.py -- python -m pytest tests/test_scheduler_api.py tests/test_scheduler_mcp.py tests/test_scheduler_surface_retirement.py tests/test_job_final_retirement.py tests/test_scheduler_canonical_migration.py tests/test_scheduler_time_events.py tests/test_authorization_mcp.py tests/test_job_final_transports.py tests/test_job_final_creators.py tests/test_job_final_http_service.py tests/test_job_path_flip.py::test_production_route_snapshot_and_same_handler_aliases tests/test_handoff_job_retarget.py tests/test_jobs_completed_retention.py tests/test_jobs_api.py tests/test_pan_cli.py tests/test_scheduler_integration.py tests/test_scheduler_store.py tests/test_scheduler_cron.py -q -o addopts= --tb=short --junitxml=evidence/r6c-scheduler-surface/final-no-lifespan.xml
