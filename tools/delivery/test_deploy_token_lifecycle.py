"""Synthetic JWT/HTTP/time/protocol only: no SSH, token issuance or server."""
import copy
import datetime as dt
import email.utils
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import deploy_token_lifecycle as life
import transfer_native_probe as bridge
from test_deploy_binding import NOW
from test_deploy_credential import SA_UID, fixture, jwt
from test_deploy_native_probe import FakeChannel

n=life.n
DEPLOY_UID='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'


def candidate(now=NOW, uid=SA_UID):
    claims,_,review=fixture()
    claims.update(iat=int(now.timestamp())-10,nbf=int(now.timestamp())-10,
                  exp=int(now.timestamp())+890)
    claims['kubernetes.io']['serviceaccount']['uid']=uid
    token=jwt(claims);review['spec']['token']=token;review['status']['user']['uid']=uid
    stamp=dt.datetime.fromtimestamp(claims['exp']-1,dt.timezone.utc).isoformat()
    message={'stage':'candidate','token':token,'sa_uid':uid,'status_expires_at':stamp}
    cred=n.c.Credential(token,uid,Mock(review=Mock(return_value=review)),now=lambda:now,clock=lambda:0)
    meta=cred.observation();meta['token_request_expires_at']=stamp
    return message,review,meta


def row(phase,now=NOW):
    return {'phase':phase,'method':'GET','path':life.PATH,'http_status':401 if phase=='expired' else 200,
            'server_date':now.isoformat(),'observed_at':now.isoformat(),
            'deployment_uid':None if phase=='expired' else DEPLOY_UID}


class ClockTests(unittest.TestCase):
    def test_forward_progress_accepts_real_elapsed_time(self):
        obj=life.Clock(lambda:NOW,lambda:0)
        obj.now=lambda:NOW+dt.timedelta(seconds=900);obj.clock=lambda:900
        self.assertEqual(obj.point()[1],900)

    def test_wall_forward_backward_and_monotonic_reversal_rejected(self):
        for wall,mono in [(NOW+dt.timedelta(seconds=60),0),(NOW-dt.timedelta(seconds=60),0),(NOW,-1),(NOW,float('nan'))]:
            obj=life.Clock(lambda:NOW,lambda:0);obj.now=lambda:wall;obj.clock=lambda:mono
            with self.subTest(wall=wall,mono=mono),self.assertRaises(n.ProbeError):obj.point()


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.wall,self.mono=NOW,0.0
        self.window=Mock(remaining=Mock(return_value=1800))
        self.context=Mock()
        with patch.object(n.a,'ca_checked',return_value=(b'CA',self.context)):
            self.probe=life.LifecycleProbe('/synthetic',bridge.CA_SHA,self.window,
                now=lambda:self.wall,clock=lambda:self.mono,sleep=self.advance)

    def advance(self,seconds):
        self.wall+=dt.timedelta(seconds=seconds);self.mono+=seconds

    def accept(self,recovery=False,message=None):
        message,review,_=candidate(self.wall) if message is None else message
        self.probe.accept(message,Mock(review=Mock(return_value=review)),recovery=recovery)
        return message

    def response(self,phase='valid',status=None,body=None):
        raw,secured,response=Mock(),Mock(),Mock()
        self.context.wrap_socket.return_value=secured
        response.length=0;response.status=(401 if phase=='expired' else 200) if status is None else status
        dates=[email.utils.format_datetime(self.wall,usegmt=True)]
        response.headers.get_all.side_effect=lambda key,default=None:dates if key=='Date' else []
        response.getheader.side_effect=lambda key,default=None:default
        obj=({'apiVersion':'v1','kind':'Status','status':'Failure','reason':'Unauthorized','code':401,'message':'PRIVATE_ERROR'}
             if phase=='expired' else {'apiVersion':'apps/v1','kind':'Deployment','metadata':{
                 'name':'transaction-api','namespace':'fds-app','uid':DEPLOY_UID},'spec':{'env':'PRIVATE_ENV'}})
        response.read.return_value=n.encode(obj if body is None else body)
        return raw,secured,response

    def request(self,phase='valid',triple=None):
        raw,secured,response=triple or self.response(phase)
        with patch.object(n.a,'read_checked',return_value=b'CA'), \
             patch.object(life.socket,'socket',return_value=raw) as socket_call, \
             patch.object(life.http.client,'HTTPResponse',return_value=response):
            value=self.probe.request(phase)
        return value,(raw,secured,response,socket_call)

    def expired_ready(self):
        self.accept();self.request()
        channel=FakeChannel([{'action':'continue'}]*70)
        self.probe.wait(channel)
        return channel

    def test_three_real_get_calls_200_401_200_with_same_path_and_uid(self):
        first=self.accept();valid,sockets=self.request()
        self.assertEqual(valid['http_status'],200)
        self.assertIsNotNone(self.probe._token)
        self.assertTrue(self.probe._credential.closed)
        self.assertNotIn('PRIVATE_',json.dumps(valid))
        channel=FakeChannel([{'action':'continue'}]*70)
        self.probe.wait(channel)
        self.assertGreaterEqual(self.mono,965)
        denied,_=self.request('expired')
        self.assertEqual(denied['http_status'],401)
        self.assertIsNone(self.probe._token)
        second=self.accept(recovery=True)
        self.assertNotEqual(first['token'],second['token'])
        recovered,_=self.request('recovery')
        self.assertEqual(recovered['deployment_uid'],valid['deployment_uid'])
        self.assertIsNone(self.probe._token)
        raw,secured,response,socket_call=sockets
        raw.connect.assert_called_once_with(('10.1.93.109',6443))
        self.assertEqual(self.context.wrap_socket.call_args.kwargs['server_hostname'],'10.1.93.109')
        self.assertTrue(secured.sendall.call_args.args[0].startswith(('GET '+life.PATH+' HTTP/1.1').encode()))
        self.assertEqual(socket_call.call_count,1)
        for item in (raw,secured,response):item.close.assert_called_once()

    def test_short_window_does_not_accept_or_request_a_token_review(self):
        self.window.remaining.return_value=1000
        message,review,_=candidate();reviewer=Mock()
        with self.assertRaisesRegex(n.ProbeError,'EXPIRY_WINDOW_TOO_SHORT'):
            self.probe.accept(message,reviewer)
        reviewer.review.assert_not_called()

    def test_old_credential_still_refuses_expired_use(self):
        self.accept();cred=self.probe._credential;self.advance(1000)
        with self.assertRaises(n.c.CredentialError):cred.token()

    def test_expired_request_cannot_bypass_wait_or_call_arbitrary_phase(self):
        self.accept()
        for phase in ('expired','recovery','PATCH','exec'):
            with self.subTest(phase=phase),self.assertRaises(n.ProbeError):self.probe.request(phase)

    def test_no_second_or_third_valid_get(self):
        self.accept();self.request()
        with self.assertRaises(n.ProbeError):self.probe.request('valid')
        with self.assertRaises(n.ProbeError):self.accept()

    def test_401_must_be_a_live_response_not_timeout_403_200_or_redirect(self):
        for status in (200,302,403,429,500):
            self.setUp();self.expired_ready()
            with self.subTest(status=status),self.assertRaisesRegex(n.ProbeError,'LIFECYCLE_GET_NOT_CONFIRMED'):
                self.request('expired',self.response('expired',status=status))
            self.assertIsNone(self.probe._token)
            with self.assertRaises(n.ProbeError):self.probe.request('expired')

    def test_bad_401_body_and_auth_body_echo_never_count_as_expiry(self):
        for body in ({}, {'apiVersion':'v1','kind':'Status','code':403,'reason':'Forbidden','status':'Failure'},
                     {'apiVersion':'v1','kind':'Status','code':True,'reason':'Unauthorized','status':'Failure'}):
            self.setUp();self.expired_ready()
            with self.subTest(body=body),self.assertRaises(n.ProbeError):self.request('expired',self.response('expired',body=body))

    def test_tls_disconnect_and_framing_errors_drop_token_without_retry(self):
        for mode in ('tls','send','oversize','two-lengths','encoding','date-missing','date-future','ca-change'):
            self.setUp();self.accept();raw,secured,response=self.response()
            if mode=='tls':secured.do_handshake.side_effect=RuntimeError('PRIVATE_TOKEN')
            if mode=='send':secured.sendall.side_effect=RuntimeError('PRIVATE_TOKEN')
            if mode=='oversize':response.read.return_value=b'x'*(n.t.MAX_RESPONSE+1)
            if mode=='two-lengths':response.headers.get_all.side_effect=lambda key,default=None:['1','1'] if key=='Content-Length' else []
            if mode=='encoding':response.getheader.side_effect=lambda key,default=None:'gzip'
            if mode=='date-missing':response.headers.get_all.side_effect=lambda key,default=None:[]
            if mode=='date-future':response.headers.get_all.side_effect=lambda key,default=None:[email.utils.format_datetime(NOW+dt.timedelta(seconds=60),usegmt=True)] if key=='Date' else []
            with self.subTest(mode=mode),patch.object(n.a,'read_checked',return_value=b'WRONG' if mode=='ca-change' else b'CA'), \
                 patch.object(life.socket,'socket',return_value=raw) as call, \
                 patch.object(life.http.client,'HTTPResponse',return_value=response):
                with self.assertRaisesRegex(n.ProbeError,'^LIFECYCLE_GET_NOT_CONFIRMED$'):self.probe.request('valid')
                count=call.call_count
                with self.assertRaises(n.ProbeError):self.probe.request('valid')
                self.assertEqual(call.call_count,count)
                self.assertIsNone(self.probe._token)

    def test_same_or_changed_sa_new_candidate_rejected_before_review(self):
        old=candidate();self.expired_ready();self.request('expired')
        for value in (old,candidate(self.wall,'bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee')):
            with self.subTest(uid=value[0]['sa_uid']),self.assertRaises(n.ProbeError):self.accept(True,value)

    def test_recovery_deployment_uid_change_stops(self):
        self.expired_ready();self.request('expired');self.accept(True)
        body={'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':life.NAME,'namespace':'fds-app','uid':SA_UID}}
        with self.assertRaises(n.ProbeError):self.request('recovery',self.response('recovery',body=body))

    def test_socket_cleanup_failure_is_not_reported_as_success(self):
        self.accept();triple=self.response();triple[1].close.side_effect=OSError('PRIVATE')
        with self.assertRaisesRegex(n.ProbeError,'^LIFECYCLE_SOCKET_CLEANUP_UNCONFIRMED$'):self.request(triple=triple)
        self.assertIsNone(self.probe._token)

    def test_wait_parent_disconnect_wrong_ack_or_window_close_stops(self):
        for channel in (FakeChannel([]),FakeChannel([{'action':'issue-again'}])):
            self.setUp();self.accept();self.request()
            with self.assertRaises((RuntimeError,n.ProbeError)):self.probe.wait(channel)
        self.setUp();self.accept();self.request();self.window.remaining.return_value=100
        with self.assertRaises(n.ProbeError):self.probe.wait(FakeChannel([]))

    def test_wall_jump_during_wait_fails_instead_of_shortening_wait(self):
        self.accept();self.request();self.wall+=dt.timedelta(seconds=1000)
        with self.assertRaisesRegex(n.ProbeError,'LIFECYCLE_CLOCK_DRIFT'):self.probe.wait(FakeChannel([]))


class PublicReceiptTests(unittest.TestCase):
    def test_arbitrary_fields_token_or_wrong_verb_status_uid_rejected(self):
        for mutate in (lambda o:o.update(token='PRIVATE'),lambda o:o.update(method='PATCH'),
                       lambda o:o.update(http_status=True),lambda o:o.update(deployment_uid='PRIVATE')):
            value=row('valid');mutate(value)
            with self.subTest(mutate=mutate),self.assertRaises(n.ProbeError):life.observation_checked(value,'valid')

    def test_expired_server_date_must_be_beyond_jwt_not_just_status_expiry(self):
        value=row('expired',NOW+dt.timedelta(seconds=59))
        with self.assertRaises(n.ProbeError):life.observation_checked(value,'expired',expires=NOW.isoformat())
        value=row('expired',NOW+dt.timedelta(seconds=75))
        self.assertEqual(life.observation_checked(value,'expired',expires=NOW.isoformat()),value)


class FakeSession:
    def __init__(self,role,messages,uid=SA_UID):
        self.ready={'stage':'ready','role':role,'source_sha':'a'*40,'workdir':'/synthetic',
                    'ca_sha256':bridge.CA_SHA,'sa_uid':uid if role=='cp1' else None}
        self.messages=copy.deepcopy(messages);self.sent=[];self.finished=self.closed=False
    def recv(self,timeout=10):
        if not self.messages:raise RuntimeError('PRIVATE_TOKEN_IN_ERROR')
        return self.messages.pop(0)
    def send(self,obj,timeout=10):self.sent.append(copy.deepcopy(obj))
    def finish(self):self.finished=True
    def close(self):self.closed=True


def sessions():
    c1,r1,m1=candidate();c2,r2,m2=candidate(NOW+dt.timedelta(seconds=1000))
    done1={**m1,'local_credential_cleanup':'LOCAL_REFERENCE_DROPPED'}
    done2={**m2,'local_credential_cleanup':'LOCAL_REFERENCE_DROPPED'}
    observations=[row('valid'),row('expired',NOW+dt.timedelta(seconds=965)),row('recovery',NOW+dt.timedelta(seconds=1000))]
    def cp(c,r,m):return FakeSession('cp1',[c,{'stage':'review-result','response':r},
        {'stage':'done','token_issuance':'ISSUED_AND_CP1_REVIEWED','credential':m}])
    def review(c):return {'stage':'review','token':c['token'],'audience':n.b.AUDIENCE,'timeout':10}
    runner=FakeSession('runner',[review(c1),{'stage':'valid','observation':observations[0],'credential':m1},
        {'stage':'waiting','jwt_expires_at':m1['expires_at']},
        {'stage':'expired','observation':observations[1],'old_reference_cleanup':'LOCAL_REFERENCE_DROPPED'},review(c2),
        {'stage':'complete','observations':observations,'credentials':[done1,done2],
         'local_credential_cleanup':'LOCAL_REFERENCE_DROPPED','source_directory':'UNCHANGED_PUBLIC_SOURCE_ONLY'}])
    return [cp(c1,r1,done1),runner,cp(c2,r2,done2)]


class CoordinatorTests(unittest.TestCase):
    def run_case(self,items,execute=True,remaining=1800):
        factory=Mock(side_effect=items)
        with patch.object(life,'harden_process'),patch.object(n,'Window',return_value=Mock(remaining=Mock(return_value=remaining))):
            result=life.coordinate(bridge,b'public','b'*64,'a'*40,execute=execute,authorization=NOW.isoformat() if execute else None,
                                  cp1_ca='/public-cp1',runner_ca='/public-runner',session_factory=factory)
        return result,factory

    def test_two_one_shot_cp1_sessions_only_after_expired_server_receipt(self):
        items=sessions();result,factory=self.run_case(items)
        self.assertEqual(result['TOKEN_LIFECYCLE'],'PASS')
        self.assertEqual(result['TOKEN_REQUEST_COMMANDS'],2)
        self.assertEqual([x['http_status'] for x in result['observations']],[200,401,200])
        self.assertEqual(factory.call_count,3)
        self.assertTrue(all(x.closed and x.finished for x in items))
        self.assertEqual([x for x in items[0].sent if x.get('action')=='issue-once'],[{'action':'issue-once'}])
        self.assertEqual([x for x in items[2].sent if x.get('action')=='issue-once'],[{'action':'issue-once'}])
        self.assertNotIn(candidate()[0]['token'],json.dumps(result))
        self.assertEqual(result['GLOBAL_LOG_ARTIFACT_RESIDUE'],'REQUIRES_POSTCOLLECTION')

    def test_default_preflight_never_sends_issue(self):
        done={'stage':'done','token_issuance':'NOT_ATTEMPTED','local_credential_cleanup':'NO_CREDENTIAL'}
        items=[FakeSession('cp1',[done]),FakeSession('runner',[done])]
        result,factory=self.run_case(items,False)
        self.assertEqual(result['PREFLIGHT'],'PASS');self.assertEqual(result['TOKEN_REQUEST_COMMANDS'],0)
        self.assertTrue(all(s.sent==[{'action':'stop'}] for s in items))

    def test_missing_401_or_wrong_uid_never_starts_recovery_issuance(self):
        for mode in ('403','early-date','missing','extra-field','wrong-wait','cp1-cleanup'):
            items=sessions()
            if mode=='403':items[1].messages[3]['observation']['http_status']=403
            if mode=='early-date':items[1].messages[3]['observation']=row('expired',NOW)
            if mode=='missing':items[1].messages=items[1].messages[:3]
            if mode=='extra-field':items[1].messages[3]['token']='PRIVATE'
            if mode=='wrong-wait':items[1].messages[2]['jwt_expires_at']=NOW.isoformat()
            if mode=='cp1-cleanup':items[0].messages[-1]['token_issuance']='NOT_ATTEMPTED'
            result,factory=self.run_case(items)
            with self.subTest(mode=mode):
                self.assertEqual(result['TOKEN_LIFECYCLE'],'NOT_CONFIRMED')
                self.assertEqual(result['TOKEN_REQUEST_COMMANDS_ATTEMPTED'],1)
                self.assertEqual(factory.call_count,2)
                self.assertFalse(items[2].sent)
                self.assertNotIn('PRIVATE',json.dumps(result))

    def test_second_sa_change_stops_before_second_command(self):
        items=sessions();items[2].ready['sa_uid']=DEPLOY_UID
        result,_=self.run_case(items)
        self.assertEqual(result['TOKEN_REQUEST_COMMANDS_ATTEMPTED'],1)
        self.assertFalse(items[2].sent)

    def test_second_failure_is_not_retried_and_all_sessions_close(self):
        items=sessions();items[2].messages=[]
        result,factory=self.run_case(items)
        self.assertEqual(result['TOKEN_LIFECYCLE'],'NOT_CONFIRMED')
        self.assertEqual(result['TOKEN_REQUEST_COMMANDS_ATTEMPTED'],2)
        self.assertEqual(factory.call_count,3)
        self.assertTrue(all(s.closed for s in items))

    def test_forged_review_response_or_old_second_token_rejected(self):
        for mode in ('review','old','final-observation','final-credential','extra-result'):
            items=sessions()
            if mode=='review':items[0].messages[1]['response']['spec']['token']='PRIVATE'
            if mode=='old':items[2].messages[0]=copy.deepcopy(items[0].messages[0])
            if mode=='final-observation':items[1].messages[-1]['observations'][2]['deployment_uid']=SA_UID
            if mode=='final-credential':items[1].messages[-1]['credentials'][0]['expires_at']=NOW.isoformat()
            if mode=='extra-result':items[1].messages[-1]['token']='PRIVATE'
            result,_=self.run_case(items)
            with self.subTest(mode=mode):
                self.assertEqual(result['TOKEN_LIFECYCLE'],'NOT_CONFIRMED')
                self.assertNotIn('PRIVATE',json.dumps(result))

    def test_insufficient_window_starts_no_endpoint_and_issues_zero(self):
        result,factory=self.run_case([],remaining=1000)
        self.assertEqual(result['TOKEN_REQUEST_COMMANDS_ATTEMPTED'],0)
        factory.assert_not_called()


class ModeTests(unittest.TestCase):
    def test_cli_rejects_missing_or_invalid_explicit_limit_before_staging(self):
        base=['transfer_native_probe.py','--bundle','/public.json',
              '--bundle-sha256','b'*64,'--source-sha','a'*40]
        for extra in (
            ['--token-lifecycle','--execute','--authorization-expires',NOW.isoformat()],
            ['--token-lifecycle','--execute','--max-token-requests','1','--authorization-expires',NOW.isoformat()],
            ['--token-lifecycle','--max-token-requests','3'],
            ['--max-token-requests','2'],
            ['--token-lifecycle','--execute','--max-token-requests','2'],
        ):
            with self.subTest(extra=extra),patch.object(sys,'argv',base+extra), \
                 patch.object(bridge,'loader') as load,patch.object(sys,'stderr'):
                self.assertEqual(bridge.main(),64)
                load.assert_not_called()

    def test_mode_must_be_boolean_and_native_bootstrap_remains_separate(self):
        for value in ('true',1,None):
            with self.assertRaises(bridge.TransferError):bridge.bootstrap(value)
        native=bridge.bootstrap();lifecycle=bridge.bootstrap(True)
        self.assertNotIn('deploy_token_lifecycle',native)
        self.assertIn("SCHEMA = 'P1-CICD-token-lifecycle-source-1'",lifecycle)
        self.assertIn("FILES = FILES | {'deploy_token_lifecycle.py'}",lifecycle)


class EndpointTests(unittest.TestCase):
    def test_complete_endpoint_orders_three_gets_and_drops_all_references(self):
        channel=FakeChannel([candidate()[0],{'action':'wait-for-expiry'},candidate(NOW+dt.timedelta(seconds=1000))[0]])
        probe=Mock()
        probe.accept.side_effect=[candidate()[2],candidate(NOW+dt.timedelta(seconds=1000))[2]]
        probe.request.side_effect=[row('valid'),row('expired',NOW+dt.timedelta(seconds=965)),row('recovery',NOW+dt.timedelta(seconds=1000))]
        with patch.object(life,'harden_process'),patch.object(n.a,'host_checked'),patch.object(n,'private_channel_checked'), \
             patch.object(n.a,'ca_checked'),patch.object(n,'Channel',return_value=channel), \
             patch.object(n,'Window',return_value=Mock(remaining=Mock(return_value=1800))),patch.object(life,'source_snapshot',return_value={}), \
             patch.object(life,'LifecycleProbe',return_value=probe):
            life.endpoint('runner','a'*40,'/source','/ca','b'*64,NOW.isoformat())
        self.assertEqual([call.args[0] for call in probe.request.call_args_list],['valid','expired','recovery'])
        self.assertEqual(channel.sent[-1]['stage'],'complete')
        self.assertEqual(channel.sent[-1]['local_credential_cleanup'],'LOCAL_REFERENCE_DROPPED')
        self.assertTrue(channel.cleared);self.assertEqual(probe.close.call_count,2)
        self.assertNotIn(candidate()[0]['token'],json.dumps(channel.sent))

    def test_endpoint_wait_failure_always_closes_holder_and_sanitizes_exception(self):
        channel=FakeChannel([candidate()[0],{'action':'wait-for-expiry'}]);probe=Mock()
        probe.accept.return_value=candidate()[2];probe.request.return_value=row('valid')
        probe.wait.side_effect=RuntimeError('PRIVATE_TOKEN')
        with patch.object(life,'harden_process'),patch.object(n.a,'host_checked'),patch.object(n,'private_channel_checked'), \
             patch.object(n.a,'ca_checked'),patch.object(n,'Channel',return_value=channel), \
             patch.object(n,'Window',return_value=Mock(remaining=Mock(return_value=1800))),patch.object(life,'source_snapshot',return_value={}), \
             patch.object(life,'LifecycleProbe',return_value=probe):
            life.endpoint('runner','a'*40,'/source','/ca','b'*64,NOW.isoformat())
        probe.close.assert_called_once();self.assertTrue(channel.cleared)
        self.assertEqual(channel.sent[-1],{'stage':'error','code':'TOKEN_LIFECYCLE_UNCONFIRMED'})
        self.assertEqual(probe.accept.call_count,1)
        self.assertNotIn('PRIVATE_TOKEN',json.dumps(channel.sent))

    def test_cp1_delegates_unchanged_one_issue_protocol_after_process_guard(self):
        with patch.object(life,'harden_process') as guard,patch.object(n,'endpoint') as endpoint:
            life.endpoint('cp1','a'*40,'/source','/ca','b'*64,None)
        guard.assert_called_once();endpoint.assert_called_once_with('cp1','a'*40,'/source','/ca','b'*64,None)

    def test_runner_preflight_no_credential_and_source_read_only(self):
        channel=FakeChannel([{'action':'stop'}])
        with patch.object(life,'harden_process'),patch.object(n.a,'host_checked'),patch.object(n,'private_channel_checked'), \
             patch.object(n.a,'ca_checked'),patch.object(n,'Channel',return_value=channel), \
             patch.object(life,'source_snapshot',return_value={}),patch.object(life,'LifecycleProbe') as probe:
            life.endpoint('runner','a'*40,'/source','/ca','b'*64,None)
        probe.assert_not_called();self.assertTrue(channel.cleared)
        self.assertEqual(channel.sent[-1]['token_issuance'],'NOT_ATTEMPTED')

    def test_runner_missing_authorization_drops_private_frame_and_returns_static_error(self):
        channel=FakeChannel([candidate()[0]])
        with patch.object(life,'harden_process'),patch.object(n.a,'host_checked'),patch.object(n,'private_channel_checked'), \
             patch.object(n.a,'ca_checked'),patch.object(n,'Channel',return_value=channel), \
             patch.object(life,'source_snapshot',return_value={}),patch.object(life,'LifecycleProbe') as probe:
            life.endpoint('runner','a'*40,'/source','/ca','b'*64,None)
        probe.assert_not_called();self.assertTrue(channel.cleared)
        self.assertEqual(channel.sent[-1],{'stage':'error','code':'TOKEN_LIFECYCLE_UNCONFIRMED'})


@unittest.skipUnless(os.name=='posix','POSIX protected staging')
class LifecycleBundleTests(unittest.TestCase):
    def test_child_process_core_and_dumpability_are_disabled(self):
        program=('import ctypes,resource;import deploy_token_lifecycle as l;'
                 'l.harden_process();assert resource.getrlimit(resource.RLIMIT_CORE)==(0,0);'
                 'assert ctypes.CDLL(None).prctl(3,0,0,0,0)==0')
        p=subprocess.run([sys.executable,'-B','-c',program],cwd=Path(__file__).parent,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr.decode())

    def test_new_bundle_schema_and_eight_exact_files_do_not_relax_native_loader(self):
        files={name:{'content':'SYNTHETIC = True\n','git_blob':hashlib.sha1(b'blob 17\0SYNTHETIC = True\n').hexdigest()} for name in life.FILES}
        # Compute blob from actual length to keep this a source binding fixture.
        for item in files.values():
            raw=item['content'].encode();item['git_blob']=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        raw=n.encode({'schema':'P1-CICD-token-lifecycle-source-1','source_sha':'a'*40,'files':files})+b'\n'
        digest=hashlib.sha256(raw).hexdigest()
        checks=bridge.loader(True);checks['identity']=lambda role:None
        root=checks['stage'](raw,digest,'a'*40,'runner')
        try:
            self.assertEqual(set(p.name for p in root.iterdir()),life.FILES)
            self.assertEqual(set(life.source_snapshot(root)),life.FILES)
            extra=root/'unexpected.log';extra.write_bytes(b'public');extra.chmod(0o600)
            with self.assertRaises(n.ProbeError):life.source_snapshot(root)
        finally:
            for p in root.iterdir():p.unlink()
            root.rmdir()
        normal=bridge.loader();normal['identity']=lambda role:None
        with self.assertRaises(ValueError):normal['stage'](raw,digest,'a'*40,'runner')


if __name__=='__main__':unittest.main()
