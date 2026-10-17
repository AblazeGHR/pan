"""Snapshot only owned center-click evidence; no toast send/remove."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

HERE = Path(__file__).resolve().parent
runtime = HERE / 'runtime-click-20261017-native1'
send = json.loads((HERE / 'visible-send.json').read_text(encoding='utf-8'))
accesses = [json.loads(line) for line in (runtime / 'access.jsonl').read_text(encoding='utf-8').splitlines()]
db = Path(os.environ['LOCALAPPDATA']) / 'Microsoft/Windows/Notifications/wpndatabase.db'
with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as connection:
    connection.row_factory = sqlite3.Row
    rows = [dict(row) for row in connection.execute(
        'SELECT n.Tag,n.[Group],n.ArrivalTime,n.ExpiryTime,CAST(n.Payload AS TEXT) AS Payload '
        'FROM Notification n JOIN NotificationHandler h ON n.HandlerId=h.RecordId '
        'WHERE h.PrimaryId=? AND n.Tag=? AND n.[Group]=?',
        ('Pan.Desktop.Notifications', 'owned-visible-20261017', 'Pan-owned-visible'))]
result = {'recordedAt': datetime.now(timezone.utc).isoformat(), 'lastSendReceipt': send,
          'userObservationViaMA': {'date': '2026-10-18', 'bannerClick': 'passed',
              'centerClick': 'failed', 'quote': '屏幕中直接弹出的可以跳转，但是通知中心的不行，补这一部分。',
              'centerBodyVersusExpandedButton': 'not_distinguished'},
          'ownedAccesses': accesses, 'ownedRowsAtSnapshot': rows,
          'timingCaution': 'The non-preflight Edge request at 15:58:07 precedes the retained last-send receipt at 15:58:37. Do not attribute it to that later send. Human feedback establishes banner success; retained files do not establish exact click/send pairing.'}
(HERE / 'center-before-fix.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'ownedRows': len(rows), 'accesses': len(accesses), 'nativeLikeAccesses':
                  [entry for entry in accesses if not entry['preflight']]}, indent=2))
