"""Single-owner bridge from legacy scheduler records to Event/Trigger/Job."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from datetime import datetime

from packages.core import background_jobs
from packages.jobs import cron
from packages.jobs.action_templates import Conflict, digest
from packages.jobs.action_runtime import is_action_job
from packages.jobs import system_actions
from packages.automation.events import now
from . import store
from .time_events import EVENT_TYPE


class LegacySchedulerBridge:
    """Own one legacy scheduled-task record after an explicit switch.

    The legacy ``taskId`` and ``jobId`` remain the public identity.  The old
    schedule is retained on the record for compatibility reads, while the
    executable config is a manual Action Job admitted by a managed Trigger.
    """

    def __init__(self, service):
        self.service = service
        self.lock = asyncio.Lock()

    @property
    def jobs(self):
        return self.service.job_runtime

    @property
    def ledger(self):
        return self.service.ledger

    @property
    def sources(self):
        return self.service.time_sources

    def _job(self, task_id):
        return store._job_for_task(task_id)

    @staticmethod
    def _action_args(job):
        action = copy.deepcopy(job.get('action') or {'api': 'assign'})
        if action.get('api') not in {'assign', 'send_session', 'send_qq', 'shell',
                                     background_jobs.RESUME_LEGAL_RUNNING_ACTION}:
            raise ValueError('Unknown scheduled Action shape; legacy owner retained')
        if action.get('api') == 'shell':
            args = action.get('args')
            if not isinstance(args, dict) or set(args) - {'command', 'cwd', 'hideWindow'}:
                raise ValueError('Unsupported shell Action fields; legacy owner retained')
        if action.get('api') == 'send_qq':
            from packages.jobs.actions import normalize_scheduled_qq_action
            action, error = normalize_scheduled_qq_action(action)
            if error: raise ValueError(error)
        params = {'sessionId': job.get('targetSessionId'), 'text': job.get('text', ''), 'action': action}
        # Validate the exact system Action input before changing ownership.
        import jsonschema
        jsonschema.Draft202012Validator(system_actions.definition('scheduled-task')['input_schema']).validate(params)
        return params

    @staticmethod
    def _entry(job):
        entries = job.get('schedule') or []
        if len(entries) != 1 or not isinstance(entries[0], dict):
            raise ValueError('Multiple or missing schedule entries require explicit legacy review')
        entry = copy.deepcopy(entries[0])
        allowed = {'id', 'kind', 'at', 'intervalSec', 'interval_sec', 'anchor', 'cron',
                   'timezone', 'enabled', 'nextFireAt', 'lastFireAt', 'misfirePolicy', 'graceSec'}
        if set(entry) - allowed:
            raise ValueError('Unknown schedule fields require explicit legacy review')
        return entry

    def _source_config(self, job, entry, enabled=False):
        schedule = {key: copy.deepcopy(value) for key, value in entry.items()
                    if key in {'kind', 'at', 'intervalSec', 'interval_sec', 'anchor', 'cron', 'timezone'}}
        return {'name': f"{job.get('name') or job['taskId']} · Scheduler",
                'schedule': schedule, 'enabled': enabled,
                'misfirePolicy': entry.get('misfirePolicy') or job.get('misfirePolicy') or 'fire_now',
                'graceSec': entry.get('graceSec', 300)}

    @staticmethod
    def _trigger_id(job_id, task_id):
        return 'trg_scheduler_' + hashlib.sha256(f'{job_id}:{task_id}'.encode()).hexdigest()[:24]

    def _config(self, job, args, enabled=False):
        return {'name': job.get('name') or job['taskId'],
                'description': job.get('description') or '',
                'actionId': 'system_scheduled_task', 'arguments': args, 'schedule': [],
                'trigger': {'kind': 'manual'}, 'enabled': enabled,
                'maxRuns': job.get('maxRuns'), 'misfirePolicy': job.get('misfirePolicy', 'fire_now'),
                'recovery': {'enabled': False, 'monitoring': False, 'maxAttempts': 3,
                             'backoffSec': [5, 15, 60], 'stableResetSec': 300},
                'overlap': {'mode': 'forbid', 'parallelLimit': 1},
                'notification': {'on': ['completed', 'failed', 'cancelled', 'unknown', 'uncertain'], 'target': 'job'},
                'completion': {'kind': 'ordinary'}}

    def _write_job(self, job, config, *, enabled, source_id, trigger_id, state):
        root = store.data_root()
        with background_jobs._lock, background_jobs._job_lock(job['jobId'], root):
            current = background_jobs._load_path(background_jobs._job_path(job['jobId'], root))
            if not current: raise KeyError(job['jobId'])
            config_copy = copy.deepcopy(config)
            config_copy['enabled'] = enabled
            from packages.jobs.action_templates import reference_generation
            approved = reference_generation(self.jobs.actions.read(), config_copy)
            current.update(config=config_copy,
                          action={'api': 'action_reference', 'actionId': 'system_scheduled_task', 'arguments': copy.deepcopy(config_copy['arguments'])},
                          legacySchedulerCompat=True, schedulerOwner='event',
                          legacySourceId=source_id, legacyTriggerId=trigger_id,
                          schedulerMigration=state,
                          referenceGeneration=approved,
                          configRevision=max(1, int(current.get('configRevision') or 0) + 1),
                          creationFingerprint=current.get('creationFingerprint') or digest({'jobId': job['jobId'], 'taskId': job.get('taskId')}),
                          enabled=enabled, status='scheduled' if enabled else 'pending',
                          runStartedAt=None, updatedAt=time.time())
            # Keep the old top-level schedule/nextFireAt/taskId fields intact.
            background_jobs._write_job(background_jobs._job_path(job['jobId'], root), current)
            return current

    async def switch(self, task_id):
        async with self.lock:
            return await asyncio.to_thread(self._switch, task_id)

    def _switch(self, task_id):
        job = self._job(task_id)
        if not job: raise KeyError(task_id)
        if job.get('legacySchedulerCompat') and job.get('schedulerMigration') == 'complete':
            return job
        if is_action_job(job) and not job.get('legacySchedulerCompat'):
            raise Conflict('Job is owned by another Action path')
        entry = self._entry(job)
        args = self._action_args(job)
        desired = bool(job.get('enabled')) and not bool(job.get('paused'))
        task_id = job.get('taskId') or task_id
        source_key = f'legacy-scheduler:{task_id}'
        source = self.sources.put(self._source_config(job, entry, enabled=False), 0,
                                  creation_key=source_key)
        source_id = source['id']
        trigger_id = self._trigger_id(job['jobId'], task_id)
        config = self._config(job, args, enabled=False)
        self._write_job(job, config, enabled=False, source_id=source_id,
                        trigger_id=trigger_id, state='pending')
        current_trigger = self.ledger.read()['triggers'].get(trigger_id)
        raw_trigger = dict(id=trigger_id, name=f"{job.get('name') or task_id} · legacy Scheduler",
                           description='Managed compatibility Trigger', eventTypes=[EVENT_TYPE],
                           eventFilter={'payload.scheduleId': source_id}, actionId='system_scheduled_task',
                           arguments={}, targetJobId=job['jobId'], ownerJobId=job['jobId'],
                           ownerLinkId=task_id, enabled=False, executionMode='serial', maxConcurrency=1)
        self.ledger.put(raw_trigger, current_trigger['revision'] if current_trigger else 0,
                        True, authority={'type': 'user'}, managed=True)
        self.sources.bind_link(source_id, trigger_id, job['jobId'])
        # Preserve the exact legacy next-fire value; source calculation is still
        # used for future occurrences, but the switch itself never re-times it.
        if entry.get('nextFireAt') is not None:
            self.sources.set_next_fire(source_id, entry.get('nextFireAt'))
        if desired:
            self._write_job(self._job(job['jobId']), config, enabled=True,
                            source_id=source_id, trigger_id=trigger_id, state='complete')
            self.sources.composition_enabled(source_id, True)
            trig = self.ledger.read()['triggers'][trigger_id]
            fields = {k: v for k, v in trig.items() if k not in {'revision','createdBy','createdAt','updatedAt','authorization'}}
            fields['enabled'] = True
            self.ledger.put(fields, trig['revision'], True, authority={'type': 'user'}, managed=True)
        else:
            self._write_job(self._job(job['jobId']), config, enabled=False,
                            source_id=source_id, trigger_id=trigger_id, state='complete')
        return background_jobs.get(job['jobId'], registry_root=store.data_root())

    def _task_view(self, job):
        return store._task_from_job(job)

    async def update(self, task_id, patch):
        async with self.lock:
            return await asyncio.to_thread(self._update, task_id, patch)

    def _update(self, task_id, patch):
        job = self._job(task_id)
        if not job or not job.get('legacySchedulerCompat'):
            raise KeyError(task_id)
        task = self._task_view(job)
        merged = dict(task)
        merged.update(copy.deepcopy(patch))
        # Reuse legacy validation/calculation in a temporary pure projection.
        if 'schedule' in patch:
            merged['schedule'] = store._normalize_schedule(patch['schedule'])
        if 'enabled' in patch: merged['enabled'] = bool(patch['enabled'])
        if 'paused' in patch: merged['paused'] = bool(patch['paused'])
        if 'misfire_policy' in patch and patch['misfire_policy'] not in store.MISFIRE_POLICIES:
            raise ValueError('misfire_policy must be fire_now / skip')
        entry = self._entry({**job, 'schedule': [merged['schedule']] if isinstance(merged.get('schedule'), dict) else job['schedule']})
        args = self._action_args({**job, 'targetSessionId': merged.get('target_session_id', job.get('targetSessionId')),
                                  'text': merged.get('text', job.get('text')), 'action': merged.get('action', job.get('action'))})
        config = self._config({**job, 'name': merged.get('name', job.get('name')), 'description': merged.get('description', job.get('description')),
                               'maxRuns': merged.get('max_runs', job.get('maxRuns')), 'misfirePolicy': merged.get('misfire_policy', job.get('misfirePolicy'))}, args, enabled=False)
        current = self._write_job(job, config, enabled=False, source_id=job['legacySourceId'], trigger_id=job['legacyTriggerId'], state='complete')
        # Replace the old top-level compatibility fields under the same lock.
        changes = {'name': merged.get('name', job.get('name')), 'description': merged.get('description', job.get('description')),
                   'text': merged.get('text', job.get('text')), 'targetSessionId': merged.get('target_session_id', job.get('targetSessionId')),
                   'action': copy.deepcopy(merged.get('action', job.get('action'))), 'schedule': [entry],
                   'nextFireAt': merged.get('next_fire_at', entry.get('nextFireAt', job.get('nextFireAt'))),
                   'enabled': bool(merged.get('enabled', job.get('enabled'))) and not bool(merged.get('paused', job.get('paused'))),
                   'paused': bool(merged.get('paused', job.get('paused'))), 'maxRuns': merged.get('max_runs', job.get('maxRuns')),
                   'misfirePolicy': merged.get('misfire_policy', job.get('misfirePolicy')), 'legacySchedulerCompat': True}
        updated = background_jobs._update(job['jobId'], changes, registry_root=store.data_root())
        source_current = self.sources.get(updated['legacySourceId'])
        self.sources.put(self._source_config(updated, entry, enabled=changes['enabled']),
                         source_current['revision'], sid=updated['legacySourceId'])
        if updated.get('nextFireAt') is not None:
            self.sources.set_next_fire(updated['legacySourceId'], updated['nextFireAt'])
        return self._task_view(updated)

    async def run_now(self, task_id):
        async with self.lock:
            job = self._job(task_id)
            if not job or not job.get('legacySchedulerCompat'): raise KeyError(task_id)
            view = await self.jobs.launch(job['jobId'], expected=job['configRevision'], authority={'type': 'user'})
            dispatch = view.get('execution', {}).get('id') or 'manual'
            record = {'run_id': dispatch, 'task_id': task_id, 'fire_at': store.iso(datetime.now().replace(microsecond=0)),
                      'actual_at': store.iso(datetime.now().replace(microsecond=0)), 'dispatch_key': dispatch,
                      'status': 'dispatched', 'session_id': job.get('targetSessionId'), 'error': None, 'result': {'status': 'dispatched'}}
            store.append_run(record)
            return {'ok': True, 'taskId': task_id, 'dispatchKey': dispatch, 'status': 'dispatched', 'run': record}

    async def delete(self, task_id):
        async with self.lock:
            job = self._job(task_id)
            if not job or not job.get('legacySchedulerCompat'):
                raise KeyError(task_id)
            tid = job['legacyTriggerId']; sid = job['legacySourceId']
            trigger = self.ledger.read()['triggers'].get(tid)
            if trigger:
                self.ledger.put({**{k: v for k, v in trigger.items() if k not in {'revision','createdBy','createdAt','updatedAt','authorization'}},
                                 'enabled': False}, trigger['revision'], True, authority={'type':'user'}, managed=True)
            source = self.sources.get(sid)
            if source and source.get('enabled'):
                self.sources.composition_enabled(sid, False)
            self.sources.detach_links(job['jobId'])
            try:
                if trigger:
                    self.ledger.delete(tid, self.ledger.read()['triggers'][tid]['revision'])
            except Exception:
                pass
            self.sources.delete(sid, self.sources.get(sid)['revision'])
            return await self.jobs.delete(job['jobId'], job['configRevision'])
