'''CBC-native rewind support used by the backend rewind workflow.'''

from .driver import (
    AnchorOutOfRangeError,
    AnchorSpec,
    ForkResult,
    REWIND_SCOPE_LABELS,
    RewindDriver,
    RewindResult,
    RewindStage,
    coerce_rewind_scope,
    fork_session,
    rewind_in_pty,
    screen_contains,
)
from .filetools import compute_add_dirs, extract_mutated_files
from .hybrid import HybridRewindResult, ensure_rewind_supported, run_hybrid_rewind
from .storage import RewindRecordStore
from .transcript import TranscriptAnchor, TruncationResult, truncate_transcript

__all__ = [
    'AnchorOutOfRangeError',
    'AnchorSpec',
    'ForkResult',
    'HybridRewindResult',
    'REWIND_SCOPE_LABELS',
    'RewindDriver',
    'RewindResult',
    'RewindStage',
    'RewindRecordStore',
    'TranscriptAnchor',
    'TruncationResult',
    'compute_add_dirs',
    'coerce_rewind_scope',
    'ensure_rewind_supported',
    'extract_mutated_files',
    'fork_session',
    'rewind_in_pty',
    'run_hybrid_rewind',
    'screen_contains',
    'truncate_transcript',
]
