"""Save exact owned acceptance facts before cleanup and graceful shutdown."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
runtime = HERE / 'runtime-click-20261017-native1'
receipt = json.loads((HERE / 'visible-send.json').read_text(encoding='utf-8'))
accesses = [json.loads(line) for line in (runtime / 'access.jsonl').read_text(encoding='utf-8').splitlines()]
sent = datetime.fromisoformat(receipt['at'])
subsequent = [entry for entry in accesses if not entry['preflight']
              and datetime.fromisoformat(entry['at']) >= sent]
expected = 'session=ses_054cea78b612e558'
assert receipt['launchUrl'] == 'http://127.0.0.1:52389/react/?' + expected
assert subsequent and subsequent[-1]['query'] == expected
elapsed = (datetime.fromisoformat(subsequent[-1]['at']) - sent).total_seconds()
result = {'recordedAt': datetime.now(timezone.utc).isoformat(), 'sendReceipt': receipt,
          'humanAcceptance': {'source': 'MA ses_8110c6ece332c2b7 current user relay on 2026-10-18',
              'userQuote': '成功了', 'instructionContext': 'Wait 60s, then Win+N and click notification body',
              'centerDelayedBodyNavigation': 'passed', 'bannerButtonNavigation': 'previously_passed',
              'centerExpandedButtonNavigation': 'not_separately_retested',
              'browserFullyClosedColdStart': 'not_verified'},
          'nonPreflightAccessesAfterSend': subsequent,
          'sendReceiptToAccessSeconds': elapsed,
          'timingLimit': 'Measured interval is sender receipt timestamp to HTTP arrival, not a direct measurement of mouse-click time or user waiting behavior.',
          'noHelperLifetimeDependency': 'Sender returns after PowerShell process exits; no callback/message loop',
          'ownedRuntimeIdentity': json.loads((runtime / 'identity.json').read_text(encoding='utf-8'))}
shutil.copyfile(runtime / 'access.jsonl', HERE / 'final-owned-access.jsonl')
(HERE / 'final-acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'sendAt': receipt['at'], 'accessAt': subsequent[-1]['at'],
                  'receiptToAccessSeconds': elapsed, 'humanResult': 'passed'}, indent=2))
