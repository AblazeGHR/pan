"""Original scheduled resume selection and per-fire Wake identity.

No dispatcher or persistence lives here. The canonical host must freeze the
selection in its Run before invoking the existing Worker Wake boundary.
"""
from packages.jobs import final_contracts as fc

OPERATION = "resume-legal-running.v1"


def select():
    from packages.core import session, worker, legal_offline
    # Extracted from background_jobs._execute_job_action before retirement.
    # is_illegal is the authority; old legal-state projections are not.
    return sorted(s.id for s in session.list_all(load_history=False)
                  if legal_offline.is_illegal(s) and isinstance(getattr(s, "id", None), str)
                  and worker.find_alive_worker_by_session(s.id) is None)


def client_message_id(effect_key):
    # The canonical effect includes the original Run/fire and selected target.
    return "job-resume:" + fc.digest(dict(effect_key))


def time_template(action_ref, creator_definition):
    """Passive native composition; publication/activation use existing APIs."""
    import copy
    return {"name": "Resume Illegal / pending recovery Sessions",
            "jobDefinition": {"name": "Resume Illegal / pending recovery Sessions",
                "steps": [{"stepId": "resume", "actionRef": copy.deepcopy(action_ref), "arguments": {},
                           "completion": {"contract": "durable-queue-accepted"}}],
                "retention": {"policyRef": "default"}},
            "composition": {"creators": [{"mode": "create", "key": "clock", "definition": copy.deepcopy(creator_definition)}],
                "triggers": [{"key": "tick", "targetJobKey": "job", "creatorKeys": ["clock"],
                    "definition": {"name": "Resume tick", "targetJobId": "job",
                        "creatorSelector": {"mode": "ids", "ids": ["clock"]}, "eventTypes": ["scheduler.time.v2"]}}]}}
