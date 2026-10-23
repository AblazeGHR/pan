"""Read-only cc-switch provider-domain sampling. Never an account registry."""
import hashlib
import json
import math
import time

from . import quota_identity as identity

DOMAIN='cc-switch-provider:codex'
MAX_OFFICIAL=32
TIERS={'five_hour':'fiveHour','seven_day':'week','5 小时':'fiveHour','7 天':'week'}


def catalog(value):
    if not isinstance(value,dict) or not isinstance(value.get('providers'),list) or len(value['providers'])>128:
        raise ValueError('provider_catalog_invalid')
    rows=value['providers'];ids=[p.get('id') if isinstance(p,dict) else None for p in rows]
    if not all(identity.identifier(i) and not i.startswith('-') for i in ids) or len(set(ids))!=len(ids):
        raise ValueError('provider_ids_invalid')
    if value.get('currentProviderId') not in ids:raise ValueError('current_provider_unknown')
    official=sorted(p['id'] for p in rows if p.get('category')=='official')
    if len(official)>MAX_OFFICIAL:raise ValueError('official_provider_capacity')
    return value['currentProviderId'],official


def window_sample(value,provider_id,now):
    from .quota_host import timestamp
    if not isinstance(value,dict) or value.get('id')!=provider_id or value.get('app')!='codex':
        raise ValueError('provider_quota_identity')
    q=value.get('quota')
    if not isinstance(q,dict) or q.get('status')!='ok' or q.get('available') is not True or q.get('message'):
        raise ValueError('provider_quota_unavailable')
    # CLI queriedAt is explicitly Unix milliseconds; no magnitude guessing.
    at=q.get('queriedAt')
    if type(at) not in (int,float):raise ValueError('provider_time_invalid')
    at=at/1000
    if not 0<=now-at<=300:raise ValueError('provider_time_stale')
    rows=q.get('tiers')
    if not isinstance(rows,list) or not 1<=len(rows)<=32:raise ValueError('provider_windows_invalid')
    result={}
    for row in rows:
        if not isinstance(row,dict):raise ValueError('provider_windows_invalid')
        kind=TIERS.get(row.get('name')) if isinstance(row.get('name'),str) else None
        if kind:
            if kind in result:raise ValueError('provider_window_ambiguous')
            used=row.get('usedPercent');reset=timestamp(row.get('resetsAt'))
            if (type(used) not in (int,float) or not math.isfinite(used) or not 0<=used<=100
                    or reset is None or reset<=now):raise ValueError('provider_window_invalid')
            result[kind]={'usedPercent':used,'resetsAt':reset,'tier':row['name']}
    if set(result)!={'fiveHour','week'}:raise ValueError('provider_window_missing')
    return {'id':provider_id,'status':'known','queriedAt':at,'queriedAtMillis':q['queriedAt'],
            'receivedAt':now,'source':'cc-switch-provider-quota','providerUpdatedAt':None,**result}


def sample(bridge,disabled,clock,deadline):
    """One bounded sample, reads outside all policy/business locks. No retry."""
    now=clock();unknown={'status':'unknown','identityDomain':DOMAIN}
    result={'identityDomain':DOMAIN,'status':'unknown','currentProviderId':None,'providers':[],
            'observedAt':now,'reason':'provider_catalog_unavailable','stable':False}
    try:
        before=catalog(bridge.read_only('list',{'app':'codex'},deadline=deadline))
        current,official=before;result['currentProviderId']=current
        for pid in official:
            if pid in disabled:
                result['providers'].append({'id':pid,'status':'disabled'});continue
            row={'id':pid,'status':'unknown','reason':'provider_quota_unavailable'}
            if time.monotonic()<deadline:
                try:row=window_sample(bridge.read_only('quota',{'app':'codex','id':pid},deadline=deadline),pid,clock())
                except Exception:pass  # fixed diagnostics only; no raw errors/credentials
            result['providers'].append(row)
        after=catalog(bridge.read_only('list',{'app':'codex'},deadline=deadline))
        if before!=after or time.monotonic()>=deadline:raise ValueError('provider_catalog_changed_or_expired')
        result['stable']=True
        result['catalogDigest']=hashlib.sha256(json.dumps(before,separators=(',',':')).encode()).hexdigest()
        now=clock();result['observedAt']=now
        selected=[r for r in result['providers'] if r['status']!='disabled']
        if (current not in official or current in disabled or not selected
                or any(r['status']!='known' or not 0<=now-r['queriedAt']<=300
                       or any(r[k]['resetsAt']<=now for k in ('fiveHour','week')) for r in selected)):
            result['reason']='provider_windows_unknown_or_current_disabled';return result,unknown
        result.update(status='known',reason=None)
        return result,{'status':'known','identityDomain':DOMAIN,'observedAt':now,
                       'currentAccountId':current,'accounts':[
                           {'id':r['id'],**{k:{f:r[k][f] for f in ('usedPercent','resetsAt')} for k in ('fiveHour','week')}}
                           for r in selected]}
    except Exception:
        result['reason']='provider_catalog_unavailable_or_changed'
        # Earlier per-provider observations remain diagnostics, not selectable.
        return result,unknown
