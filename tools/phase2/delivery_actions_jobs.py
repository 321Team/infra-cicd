"""Pure native Actions required-job/step validator. No I/O or authority."""
import re

_SHA = re.compile(r'[0-9a-f]{40}')


def _fail():
    raise ValueError('ACTIONS_JOBS_INVALID') from None


def _pint(v):
    return type(v) is int and v > 0


def _text(v):
    return (type(v) is str and 0 < len(v) <= 512
            and v.strip() == v
            and not any(ord(c) < 32 or 127 <= ord(c) < 160 for c in v))


def _done(o):
    return o.get('status') == 'completed' and o.get('conclusion') == 'success'


def check_jobs(payload, run_id, workflow_sha, gates):
    try:
        return _check(payload, run_id, workflow_sha, gates)
    except ValueError:
        raise ValueError('ACTIONS_JOBS_INVALID') from None
    except Exception:
        raise ValueError('ACTIONS_JOBS_INVALID') from None


def _check(payload, run_id, workflow_sha, gates):
    if not _pint(run_id) or type(workflow_sha) is not str or not _SHA.fullmatch(workflow_sha):
        _fail()
    if type(gates) is not tuple or not 1 <= len(gates) <= 100:
        _fail()
    seen_g = set()
    for g in gates:
        if type(g) is not tuple or len(g) != 3:
            _fail()
        gid, gname, gsteps = g
        if not _pint(gid) or gid in seen_g or not _text(gname):
            _fail()
        seen_g.add(gid)
        if type(gsteps) is not tuple or not 1 <= len(gsteps) <= 512:
            _fail()
        seen_s = set()
        for s in gsteps:
            if type(s) is not tuple or len(s) != 2 or not _pint(s[0]) or not _text(s[1]) or s[0] in seen_s:
                _fail()
            seen_s.add(s[0])
    if type(payload) is not dict:
        _fail()
    jobs, total = payload.get('jobs'), payload.get('total_count')
    if type(jobs) is not list or type(total) is not int or not 1 <= total <= 100 or total != len(jobs):
        _fail()
    ids = set()
    for j in jobs:
        if type(j) is not dict or not _pint(j.get('id')) or j['id'] in ids:
            _fail()
        ids.add(j['id'])
    out = []
    for gid, gname, gsteps in gates:
        hits = [j for j in jobs if j['id'] == gid]
        if len(hits) != 1:
            _fail()
        j = hits[0]
        if (not _text(j.get('name')) or j['name'] != gname
                or not _pint(j.get('run_id')) or j['run_id'] != run_id
                or j.get('head_sha') != workflow_sha or not _done(j)):
            _fail()
        steps = j.get('steps')
        if type(steps) is not list or not 1 <= len(steps) <= 512:
            _fail()
        nums = set()
        for s in steps:
            if type(s) is not dict or not _pint(s.get('number')) or s['number'] in nums:
                _fail()
            nums.add(s['number'])
        for no, nm in gsteps:
            m = [s for s in steps if s['number'] == no and s.get('name') == nm]
            if len(m) != 1 or not _text(m[0]['name']) or not _done(m[0]):
                _fail()
        out.append((gid, tuple((no, nm) for no, nm in gsteps)))
    return tuple(out)
