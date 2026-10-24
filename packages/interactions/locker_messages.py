"""Internal, source-Session-only admission of dedicated Locker messages.

No Chat/Job/QQ forwarding. The local stdio producer bridge and internal callers must
already possess a trusted source Session identity; never take it from a user
request. Storage is independent of Session history and queue consumption.
"""
from copy import deepcopy
from datetime import datetime
import re
from urllib.parse import parse_qs, unquote

from .contracts import InteractionError, validate


def validate_message(message):
    validate('LockerMessage', message)
    try:
        stamp = datetime.fromisoformat(message['ts'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('Timezone required')
    except ValueError as exc:
        raise InteractionError('invalid', 'Locker message ts requires an ISO timestamp with timezone') from exc


def attachment_ids(message):
    return {part['attachmentId'] for part in message.get('parts', [])
            if part.get('type') == 'attachment'}


def accept_locker_message(source_session_id, recipient_id, message):
    """Shared admission boundary for internal and trusted local stdio producers."""
    from packages.core import session
    from packages.web import server
    from .api import get_store
    validate('Id', source_session_id)
    validate('Id', recipient_id)
    validate_message(message)
    source = session.get(source_session_id, load_history=False)
    if source is None:
        raise InteractionError('not_found', 'Source Session is unavailable')
    # Keep the original document, including future fields. Paths/hrefs supplied
    # in metadata never grant access; only the existing opaque registry does.
    value = deepcopy(message)
    ids = attachment_ids(value)
    for part in value.get('parts', []):
        if part.get('type') == 'text':
            text = part['text']
        else:
            text = ''
        _validate_links(server, source_session_id, text)
    _validate_links(server, source_session_id, value['content'])
    for attachment_id in ids:
        error = server._attachment_id_error(source_session_id, attachment_id)
        if error:
            raise InteractionError('forbidden', error['code'])
    return get_store().accept_message(source_session_id, recipient_id, value,
        source_name=source.name, workspace_ids=session.effective_workspace_ids(source))


def _validate_links(server, source_id, text):
    # Existing legacy/file validation plus strict ownership for opaque links.
    error = server._validate_message_attachment_references(source_id, text)
    if error:
        raise InteractionError('forbidden', error['code'])
    for aid, query in re.findall(r'/api/attachments/(?:ref|editor)/([^?\s)]+)\?([^\s)]+)', text):
        if parse_qs(query).get('session_id') != [source_id]:
            raise InteractionError('forbidden', 'attachment_session_mismatch')
        error = server._attachment_id_error(source_id, unquote(aid))
        if error:
            raise InteractionError('forbidden', error['code'])
