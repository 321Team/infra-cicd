"""AC08 diagnostic only: one valid GET, one expired GET, one fresh-token GET.

No rollout client and no change to Credential's production expiry guard.
Endpoint stdout is a PRIVATE SSH pipe. Only coordinate() returns public receipts.
"""
import datetime as dt
import email.utils
import hashlib
import http.client
import math
import os
from pathlib import Path
import re
import socket
import stat
import time

import deploy_native_probe as n

NAME = 'transaction-api'
PATH = '/apis/apps/v1/namespaces/fds-app/deployments/' + NAME
GRACE = 75  # Beyond JWT expiry; actual server Date and 401 still required.
RESERVE = 90
HEARTBEAT = 15
MAX_CLOCK_DRIFT = 5
FILES = {'deploy_token_lifecycle.py', 'deploy_native_probe.py', 'deploy_cp1_access.py',
         'deploy_binding.py', 'deploy_credential.py', 'deploy_transport.py',
         'deploy_adapter.py', 'delivery_gate.py'}
need = n.need


def harden_process():
    """This process only: disable core dumps and ptrace-style memory access."""
    import ctypes
    import resource
    need(os.name == 'posix', 'POSIX_HOST_REQUIRED')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    libc = ctypes.CDLL(None, use_errno=True)
    need(libc.prctl(4, 0, 0, 0, 0) == 0, 'PROCESS_DUMP_GUARD_FAILED')  # PR_SET_DUMPABLE


class Clock:
    def __init__(self, now=n.utcnow, clock=time.monotonic):
        self.now, self.clock = now, clock
        self.start_wall, self.start_mono = now(), clock()
        self.last = self.start_mono
        self.point()

    def point(self):
        wall, mono = self.now(), self.clock()
        need(isinstance(wall, dt.datetime) and wall.utcoffset() == dt.timedelta(0)
             and type(mono) in (int, float) and math.isfinite(mono)
             and mono >= self.last, 'LIFECYCLE_CLOCK_INVALID')
        need(abs((wall - self.start_wall).total_seconds() - (mono - self.start_mono))
             <= MAX_CLOCK_DRIFT, 'LIFECYCLE_CLOCK_DRIFT')
        self.last = mono
        return wall, mono


def source_snapshot(workdir):
    """Read only the newly staged public source, never credential files."""
    root = Path(workdir)
    info = root.lstat()
    need(root.parent == Path.home() and root.name.startswith('FDS_NativeProbeSource.')
         and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
         and stat.S_IMODE(info.st_mode) == 0o700, 'SOURCE_DIRECTORY_INVALID')
    need({p.name for p in root.iterdir()} == FILES, 'SOURCE_DIRECTORY_EXTRA_FILE')
    result = {}
    for name in sorted(FILES):
        path = root / name
        info = path.lstat()
        need(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600,
             'SOURCE_MODE_INVALID')
        result[name] = hashlib.sha256(n.a.read_checked(path)).hexdigest()
    return result


class LifecycleProbe:
    """Retain the old bearer ONLY for one delayed, fixed-path diagnostic GET."""
    def __init__(self, ca_path, ca_hash, window, *, now=n.utcnow,
                 clock=time.monotonic, sleep=time.sleep):
        self.ca_path, self.ca_hash, self.window = ca_path, ca_hash, window
        self.ca_raw, self.context = n.a.ca_checked(ca_path, ca_hash)
        self.time = Clock(now, clock)
        self.sleep, self.state = sleep, 'NEW'
        self._token = self._credential = None
        self.uid = self.deployment_uid = None
        self.exp = self.exp_mono = None
        self.first = self.second = None

    def close(self):
        if self._credential is not None:
            self._credential.close()
        self._token = self._credential = None
        self.state = 'CLOSED'

    def accept(self, message, reviewer, recovery=False):
        need(self.state == ('EXPIRED_OBSERVED' if recovery else 'NEW'), 'LIFECYCLE_ORDER')
        need(type(message) is dict and set(message) == {'stage', 'token', 'sa_uid', 'status_expires_at'}
             and message['stage'] == 'candidate', 'RUNNER_CANDIDATE_INVALID')
        token, uid = message['token'], message['sa_uid']
        claims = n.c.claims_checked(token, uid)
        status = n.expiry(message['status_expires_at'])
        wall, mono = self.time.point()
        need(claims['iat'] < status.timestamp() <= claims['exp'], 'TOKEN_REQUEST_EXPIRY_INVALID')
        need((status - wall).total_seconds() > 30, 'CANDIDATE_WINDOW_TOO_SHORT')
        if recovery:
            need(uid == self.uid and claims['iat'] > self.exp.timestamp(), 'RECOVERY_IDENTITY_INVALID')
        else:
            need(self.window.remaining() > claims['exp'] - wall.timestamp() + GRACE + RESERVE,
                 'EXPIRY_WINDOW_TOO_SHORT')
            self.uid = uid
            self.exp = dt.datetime.fromtimestamp(claims['exp'], dt.timezone.utc)
            self.exp_mono = mono + (self.exp - wall).total_seconds()
        credential = n.c.Credential(token, uid, reviewer, now=self.time.now,
                                    clock=self.time.clock,
                                    review_timeout=min(10, self.window.remaining(),
                                                       (status-wall).total_seconds()))
        self._token, self._credential = token, credential
        self.status = status
        self.status_mono = mono + (status-wall).total_seconds()
        metadata = credential.observation()
        metadata['token_request_expires_at'] = status.isoformat()
        self.state = 'RECOVERY_READY' if recovery else 'FIRST_READY'
        if recovery:
            self.second = metadata
        else:
            self.first = metadata
        return metadata

    def budget(self, *, expired=False):
        wall, mono = self.time.point()
        left = self.window.remaining()
        if expired:
            need(wall >= self.exp + dt.timedelta(seconds=GRACE)
                 and mono >= self.exp_mono + GRACE, 'EXPIRY_WAIT_INCOMPLETE')
        else:
            left = min(left, self._credential.remaining(), self.status_mono-mono,
                       (self.status-wall).total_seconds())
        need(left > 0, 'LIFECYCLE_WINDOW_EXPIRED')
        return left

    def request(self, phase):
        """Raw TLS GET; no redirects, proxy, configurable URL or retries."""
        need(phase in ('valid', 'expired', 'recovery'), 'LIFECYCLE_PHASE_INVALID')
        need(self.state == {'valid':'FIRST_READY', 'expired':'WAIT_COMPLETE',
                            'recovery':'RECOVERY_READY'}[phase], 'LIFECYCLE_ORDER')
        self.state = phase.upper() + '_ATTEMPTED'  # Attempt consumed even on ambiguous I/O.
        raw = secured = response = header = data = None
        try:
            expired = phase == 'expired'
            deadline = self.time.clock() + min(10, self.budget(expired=expired))
            def budget():
                left = min(deadline-self.time.clock(), self.budget(expired=expired))
                need(left > 0, 'LIFECYCLE_REQUEST_TIMEOUT')
                return left
            need(n.a.read_checked(self.ca_path) == self.ca_raw, 'RUNNER_CA_CHANGED')
            need(self._token is not None, 'CREDENTIAL_UNAVAILABLE')
            if not expired:
                need(self._credential.token() == self._token, 'CREDENTIAL_BINDING_INVALID')
            header = ('GET ' + PATH + ' HTTP/1.1\r\nHost: ' + n.t.HOST + ':' + str(n.t.PORT)
                      + '\r\nAuthorization: Bearer ' + self._token
                      + '\r\nAccept: application/json\r\nAccept-Encoding: identity'
                      + '\r\nConnection: close\r\nContent-Length: 0\r\n\r\n').encode('ascii')
            raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw.settimeout(budget());raw.connect((n.t.HOST, n.t.PORT))
            secured = self.context.wrap_socket(raw, server_hostname=n.t.HOST, do_handshake_on_connect=False)
            secured.settimeout(budget());secured.do_handshake()
            secured.settimeout(budget());secured.sendall(header)
            response = http.client.HTTPResponse(n.t.ResponseSocket(secured, budget), method='GET')
            response.begin();budget()
            lengths, transfers = response.headers.get_all('Content-Length', []), response.headers.get_all('Transfer-Encoding', [])
            need(len(lengths)<=1 and len(transfers)<=1 and not (lengths and transfers)
                 and (not transfers or transfers[0].lower()=='chunked')
                 and (not lengths or re.fullmatch('[0-9]+', lengths[0].strip()))
                 and response.getheader('Content-Encoding','identity').lower()=='identity', 'HTTP_FRAMING_INVALID')
            dates = response.headers.get_all('Date', [])
            need(len(dates)==1 and len(dates[0])<=60, 'SERVER_DATE_REQUIRED')
            server_time = email.utils.parsedate_to_datetime(dates[0])
            wall, _ = self.time.point()
            need(server_time.utcoffset()==dt.timedelta(0)
                 and abs((server_time-wall).total_seconds())<=10, 'SERVER_CLOCK_UNCONFIRMED')
            data = response.read(n.t.MAX_RESPONSE+1);budget()
            need(len(data)<=n.t.MAX_RESPONSE and (response.length is None or response.length==0), 'HTTP_BODY_INVALID')
            obj = n.b.read(data)
            expected = 401 if expired else 200
            need(response.status == expected, 'EXPECTED_HTTP_STATUS_NOT_OBSERVED')
            if expired:
                need(server_time >= self.exp + dt.timedelta(seconds=60), 'SERVER_EXPIRY_UNCONFIRMED')
                need(obj.get('apiVersion')=='v1' and obj.get('kind')=='Status'
                     and obj.get('status')=='Failure' and obj.get('reason')=='Unauthorized'
                     and type(obj.get('code')) is int and obj['code']==401, 'UNAUTHORIZED_BODY_INVALID')
            else:
                meta = obj.get('metadata', {})
                need(obj.get('apiVersion')=='apps/v1' and obj.get('kind')=='Deployment'
                     and meta.get('namespace')=='fds-app' and meta.get('name')==NAME
                     and type(meta.get('uid')) is str and n.c.UID.fullmatch(meta['uid'])
                     and not meta.get('deletionTimestamp'), 'DEPLOYMENT_IDENTITY_INVALID')
                if phase=='valid':self.deployment_uid=meta['uid']
                else:need(meta['uid']==self.deployment_uid, 'DEPLOYMENT_CHANGED')
            need(n.a.read_checked(self.ca_path)==self.ca_raw, 'RUNNER_CA_CHANGED');budget()
            row={'phase':phase,'method':'GET','path':PATH,'http_status':expected,
                 'server_date':server_time.isoformat(),'observed_at':self.time.now().isoformat(),
                 'deployment_uid':None if expired else self.deployment_uid}
            self.state={'valid':'VALID_OBSERVED','expired':'EXPIRED_OBSERVED','recovery':'RECOVERED'}[phase]
            # Preserve only the diagnostic bearer while waiting for expiry.
            if self._credential is not None:self._credential.close()
            if phase!='valid':self._token=self._credential=None
            return row
        except Exception:
            self.close()
            raise n.ProbeError('LIFECYCLE_GET_NOT_CONFIRMED') from None
        finally:
            header=data=None
            close_failed=False
            for item in (response,secured,raw):
                if item is not None:
                    try:item.close()
                    except Exception:close_failed=True
            if close_failed:
                self.close()
                raise n.ProbeError('LIFECYCLE_SOCKET_CLEANUP_UNCONFIRMED') from None

    def wait(self, channel):
        need(self.state=='VALID_OBSERVED', 'LIFECYCLE_ORDER')
        while True:
            wall, mono = self.time.point()
            left=max((self.exp-wall).total_seconds(),self.exp_mono-mono)+GRACE
            need(self.window.remaining()>max(0,left)+RESERVE, 'EXPIRY_WINDOW_TOO_SHORT')
            if left<=0:break
            # Broker loss causes EOF/timeout, not an orphaned credential holder.
            channel.send({'stage':'waiting','jwt_expires_at':self.exp.isoformat()},10)
            need(channel.recv(30)=={'action':'continue'}, 'WAIT_ACK_INVALID')
            self.sleep(min(HEARTBEAT,left))
        self.state='WAIT_COMPLETE'


def endpoint(role, source_sha, workdir, ca_path, ca_hash, authorization_expires):
    harden_process()
    if role=='cp1':
        return n.endpoint(role,source_sha,workdir,ca_path,ca_hash,authorization_expires)
    n.a.host_checked('runner');n.private_channel_checked()
    channel=n.Channel(0,1)
    probe=message=None
    try:
        before=source_snapshot(workdir)
        n.a.ca_checked(ca_path,ca_hash)
        channel.send({'stage':'ready','role':'runner','source_sha':source_sha,'workdir':workdir,
                      'ca_sha256':ca_hash,'sa_uid':None})
        message=channel.recv(60)
        if message=={'action':'stop'}:
            channel.send({'stage':'done','token_issuance':'NOT_ATTEMPTED','local_credential_cleanup':'NO_CREDENTIAL'})
            return
        need(authorization_expires is not None, 'EXPLICIT_ISSUANCE_WINDOW_REQUIRED')
        window=n.Window(authorization_expires)
        probe=LifecycleProbe(ca_path,ca_hash,window)
        first=probe.accept(message,n.PipeReviewer(channel));message=None
        valid=probe.request('valid')
        channel.send({'stage':'valid','observation':valid,'credential':first})
        need(channel.recv(30)=={'action':'wait-for-expiry'}, 'LIFECYCLE_ORDER')
        probe.wait(channel)
        denied=probe.request('expired')
        channel.send({'stage':'expired','observation':denied,'old_reference_cleanup':'LOCAL_REFERENCE_DROPPED'})
        message=channel.recv(min(120,window.remaining()))
        second=probe.accept(message,n.PipeReviewer(channel),recovery=True);message=None
        recovered=probe.request('recovery')
        probe.close()
        need(source_snapshot(workdir)==before, 'SOURCE_DIRECTORY_CHANGED')
        for credential in (first,second):credential['local_credential_cleanup']='LOCAL_REFERENCE_DROPPED'
        channel.send({'stage':'complete','observations':[valid,denied,recovered],
                      'credentials':[first,second],'local_credential_cleanup':'LOCAL_REFERENCE_DROPPED',
                      'source_directory':'UNCHANGED_PUBLIC_SOURCE_ONLY'})
    except Exception:
        try:channel.send({'stage':'error','code':'TOKEN_LIFECYCLE_UNCONFIRMED'})
        except Exception:pass
    finally:
        if probe is not None:probe.close()
        message=None;channel.clear()


def observation_checked(obj, phase, *, uid=None, expires=None):
    need(type(obj) is dict and set(obj)=={'phase','method','path','http_status','server_date','observed_at','deployment_uid'}, 'OBSERVATION_SHAPE')
    need(obj['phase']==phase and obj['method']=='GET' and obj['path']==PATH
         and type(obj['http_status']) is int and obj['http_status']==(401 if phase=='expired' else 200), 'OBSERVATION_STATUS')
    server,observed=n.expiry(obj['server_date']),n.expiry(obj['observed_at'])
    need(abs((server-observed).total_seconds())<=20,'OBSERVATION_CLOCK')
    if phase=='expired':
        need(obj['deployment_uid'] is None and expires is not None
             and server>=n.expiry(expires)+dt.timedelta(seconds=60), 'OBSERVATION_EXPIRY')
    else:
        need(type(obj['deployment_uid']) is str and n.c.UID.fullmatch(obj['deployment_uid'])
             and (uid is None or obj['deployment_uid']==uid), 'OBSERVATION_UID')
    return dict(obj)


def credential_checked(bridge, obj, uid, *, retained=False):
    need(type(obj) is dict and obj.get('service_account_uid')==uid,'CREDENTIAL_UID')
    value=dict(obj)
    if retained:
        need(value.get('local_credential_cleanup')=='NOT_PERFORMED','CREDENTIAL_STATE')
        value['local_credential_cleanup']='LOCAL_REFERENCE_DROPPED'
    result=bridge.safe_credential(value)
    need(0 < (n.expiry(result['expires_at'])-n.expiry(result['issued_at'])).total_seconds()
         <= n.TTL, 'CREDENTIAL_RECEIPT_TTL')
    if retained:result['local_credential_cleanup']='RETAINED_FOR_EXPIRY_DIAGNOSTIC'
    return result


def coordinate(bridge, bundle, digest, source, *, execute, authorization,
               cp1_ca, runner_ca, cp1_key=None, runner_key=None, session_factory=None):
    """Two independently one-shot CP1 sessions. No second issue before live 401."""
    harden_process()
    factory=session_factory or bridge.Session
    cp1=runner=candidate=request=response=None
    attempted=0
    checkpoints=[];credentials=[];cp1_receipts=[];sources=[]
    try:
        need(execute or authorization is None, 'PREFLIGHT_AUTHORIZATION_UNEXPECTED')
        window=n.Window(authorization) if execute else None
        if execute:need(window.remaining()>n.TTL+GRACE+RESERVE,'EXPIRY_WINDOW_TOO_SHORT')
        def connect(role):
            session=factory(role,bundle,digest,source,cp1_ca if role=='cp1' else runner_ca,
                            authorization if execute else None,n.Channel,
                            key=cp1_key if role=='cp1' else runner_key,lifecycle=True)
            return session
        cp1=connect('cp1');runner=connect('runner')
        sources.extend([cp1.ready,runner.ready]);uid=cp1.ready['sa_uid']
        if not execute:
            for session in (runner,cp1):
                session.send({'action':'stop'})
                done=session.recv()
                need(done=={'stage':'done','token_issuance':'NOT_ATTEMPTED','local_credential_cleanup':'NO_CREDENTIAL'},'PREFLIGHT_CLEANUP')
                session.finish()
            return {'PREFLIGHT':'PASS','TOKEN_REQUEST_COMMANDS':0,'TOKEN_LIFECYCLE':'NOT_EXECUTED',
                    'sources':sources,'DEPLOY':'NOT_EXECUTED','DELIVERY_GATE':'NOT_VERIFIED'}
        for index in (0,1):
            if index:
                cp1=connect('cp1');sources.append(cp1.ready)
                need(cp1.ready['sa_uid']==uid,'RECOVERY_SA_CHANGED')
            attempted+=1
            cp1.send({'action':'issue-once'},min(10,window.remaining()))
            candidate=cp1.recv(min(30,window.remaining()))
            need(type(candidate) is dict and set(candidate)=={'stage','token','sa_uid','status_expires_at'}
                 and candidate['stage']=='candidate' and candidate['sa_uid']==uid,'CANDIDATE_BINDING')
            claims=n.c.claims_checked(candidate['token'],uid)
            status=n.expiry(candidate['status_expires_at'])
            need(claims['iat']<status.timestamp()<=claims['exp'],'CANDIDATE_EXPIRY')
            candidate_times={
                'issued_at':dt.datetime.fromtimestamp(claims['iat'],dt.timezone.utc).isoformat(),
                'expires_at':dt.datetime.fromtimestamp(claims['exp'],dt.timezone.utc).isoformat(),
                'token_request_expires_at':candidate['status_expires_at'],
            }
            if index:need(claims['iat']>n.expiry(credentials[0]['expires_at']).timestamp(),'RECOVERY_TOKEN_NOT_NEW')
            runner.send(candidate,min(10,window.remaining()))
            request=runner.recv(min(10,window.remaining()))
            need(type(request) is dict and set(request)=={'stage','token','audience','timeout'}
                 and request['stage']=='review' and request['token']==candidate['token']
                 and request['audience']==n.b.AUDIENCE,'REVIEW_RELAY_BINDING')
            timeout=n.a.timeout_checked(request['timeout']);start=time.monotonic()
            cp1.send(request,min(timeout,window.remaining()))
            response=cp1.recv(min(timeout,window.remaining()))
            need(type(response) is dict and set(response)=={'stage','response'} and response['stage']=='review-result','REVIEW_RELAY_RESPONSE')
            n.c.review_checked(response['response'],candidate['token'],uid)
            left=timeout-(time.monotonic()-start);need(left>0,'REVIEW_RELAY_TIMEOUT')
            runner.send(response,min(left,window.remaining()))
            native_expiry=candidate['status_expires_at']
            candidate=request=response=None
            result=runner.recv(min(30,window.remaining()))
            if index==0:
                need(type(result) is dict and set(result)=={'stage','observation','credential'} and result['stage']=='valid','VALID_RESULT_INVALID')
                first=credential_checked(bridge,result['credential'],uid,retained=True)
                need(first['token_request_expires_at']==native_expiry,'FIRST_CREDENTIAL_MISMATCH')
                need(all(first[k]==v for k,v in candidate_times.items()),'FIRST_JWT_RECEIPT_MISMATCH')
                checkpoints.append(observation_checked(result['observation'],'valid'))
                credentials.append(first)
            else:
                need(type(result) is dict and set(result)=={'stage','observations','credentials','local_credential_cleanup','source_directory'}
                     and result['stage']=='complete' and result['local_credential_cleanup']=='LOCAL_REFERENCE_DROPPED'
                     and result['source_directory']=='UNCHANGED_PUBLIC_SOURCE_ONLY'
                     and type(result['observations']) is list and len(result['observations'])==3
                     and result['observations'][:2]==checkpoints
                     and type(result['credentials']) is list and len(result['credentials'])==2,'FINAL_RESULT_INVALID')
                final_first=credential_checked(bridge,result['credentials'][0],uid)
                expected_first={**credentials[0],'local_credential_cleanup':'LOCAL_REFERENCE_DROPPED'}
                need(final_first==expected_first,'OLD_CREDENTIAL_CHANGED')
                fresh=credential_checked(bridge,result['credentials'][1],uid)
                need(fresh['token_request_expires_at']==native_expiry and n.expiry(fresh['issued_at'])>n.expiry(first['expires_at']),'NEW_CREDENTIAL_MISMATCH')
                need(all(fresh[k]==v for k,v in candidate_times.items()),'SECOND_JWT_RECEIPT_MISMATCH')
                credentials=[final_first,fresh]
                checkpoints.append(observation_checked(result['observations'][2],'recovery',uid=checkpoints[0]['deployment_uid']))
            cp1.send({'action':'finish'},min(10,window.remaining()))
            done=cp1.recv(min(10,window.remaining()))
            need(type(done) is dict and set(done)=={'stage','token_issuance','credential'} and done['stage']=='done'
                 and done['token_issuance']=='ISSUED_AND_CP1_REVIEWED','CP1_CLEANUP_UNCONFIRMED')
            meta=credential_checked(bridge,done['credential'],uid)
            for key in ('issued_at','expires_at','token_request_expires_at'):
                need(meta[key]==credentials[index][key],'CP1_RECEIPT_MISMATCH')
            cp1_receipts.append(meta);cp1.finish();cp1.close();cp1=None
            if index==0:
                runner.send({'action':'wait-for-expiry'},min(10,window.remaining()))
                while True:
                    frame=runner.recv(min(30,window.remaining()))
                    if type(frame) is dict and frame.get('stage')=='waiting':
                        need(set(frame)=={'stage','jwt_expires_at'} and frame['jwt_expires_at']==first['expires_at'],'WAIT_FRAME_INVALID')
                        runner.send({'action':'continue'},min(10,window.remaining()))
                        continue
                    need(type(frame) is dict and set(frame)=={'stage','observation','old_reference_cleanup'}
                         and frame['stage']=='expired' and frame['old_reference_cleanup']=='LOCAL_REFERENCE_DROPPED','EXPIRY_RESULT_INVALID')
                    checkpoints.append(observation_checked(frame['observation'],'expired',expires=first['expires_at']))
                    break
        runner.finish()
        result={'PREFLIGHT':'PASS','TOKEN_LIFECYCLE':'PASS','TOKEN_REQUEST_COMMANDS':attempted,
                'observations':checkpoints,'credentials':credentials,'cp1_credentials':cp1_receipts,
                'sources':sources,'ENDPOINT_CLEANUP':'ACK_AND_PROCESS_EXIT',
                'SOURCE_DIRECTORY':'RUNNER_UNCHANGED_PUBLIC_SOURCE_ONLY',
                'ANSIBLE_INPUT_REFERENCE_CLEANUP':'LOCAL_REFERENCE_DROPPED',
                'server_revocation':'NOT_PERFORMED','DEPLOY':'NOT_EXECUTED','DELIVERY_GATE':'NOT_VERIFIED',
                'GLOBAL_LOG_ARTIFACT_RESIDUE':'REQUIRES_POSTCOLLECTION'}
        # Sanitized schemas above; a second guard never prints an accidental bearer.
        need(not re.search(rb'eyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.',n.encode(result)), 'PUBLIC_SECRET_PATTERN')
        return result
    except Exception:
        return {'TOKEN_LIFECYCLE':'NOT_CONFIRMED','TOKEN_REQUEST_COMMANDS_ATTEMPTED':attempted,
                'error_code':'TOKEN_LIFECYCLE_UNCONFIRMED','ENDPOINT_CLEANUP':'NOT_CONFIRMED',
                'completed_http_statuses':[x['http_status'] for x in checkpoints],
                'DEPLOY':'NOT_EXECUTED','DELIVERY_GATE':'NOT_VERIFIED','server_revocation':'NOT_PERFORMED'}
    finally:
        candidate=request=response=None
        for session in (runner,cp1):
            if session is not None:
                try:session.close()
                except Exception:pass
