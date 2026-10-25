"""Slow-verifier regression with synthetic time, no live credential/network."""
import unittest
from unittest.mock import patch
import delivery_adapter as core
import delivery_session as session
from test_delivery_operational import ContractHarness,validator

class LocalWindowTests(unittest.TestCase):
    def execute(self,f,enabled):
        h=f.h
        return session.run_session(h.binding,h.render,h.contract,validator,root='UNUSED_FAKE_ROOT',providers=f.providers,credential=f.credential,sender_factory=f.sender,budget_seconds=600,acquire=f.storage.acquire,journal_factory=f.storage.journal,wall=lambda:h.now,monotonic=lambda:h.mono,sleep=h.sleep,finalize=True,local_time_checks=enabled)
    def test_slow_fresh_checks_reproduce_failure_and_local_dedup_preserves_api_checks(self):
        old=ContractHarness()
        old.providers.before=lambda role,r: old.h.sleep(60) if role=='approval' else None
        r=self.execute(old,False)
        self.assertEqual('FIRST_WRITE_WINDOW_TOO_SHORT',r['receipt']['error_code'])
        self.assertEqual([],old.h.requests)
        f=ContractHarness();events=[]
        def before(role,r):
            if role=='approval':f.h.sleep(60);events.append('verify')
        f.providers.before=before
        f.h.after_send=lambda req,obj:events.append(req.method)
        r=self.execute(f,True)
        self.assertEqual('SESSION_COMPLETED',r['status'],r)
        self.assertEqual(['PATCH']*6+['GET']*2,[q.method for q in f.h.requests])
        for i,event in enumerate(events):
            if event in ('GET','PATCH'):self.assertEqual('verify',events[i-1])
        self.assertEqual(1,f.storage.complete_calls)
    def test_withdrawal_still_denies_before_first_api(self):
        f=ContractHarness();seen=[0]
        def before(role,r):
            if role=='approval':
                seen[0]+=1
                if seen[0]==3:raise RuntimeError('SYNTHETIC_WITHDRAWN')
        f.providers.before=before
        r=self.execute(f,True)
        self.assertEqual([],f.h.requests)
        self.assertEqual('AUTHORIZATION_DENIED',r['receipt']['error_code'])
    def test_initial_lease_cannot_authorize_network_without_fresh_callback(self):
        from test_delivery_adapter import Harness
        h=Harness();calls=[]
        def deny(b):calls.append('verify');raise RuntimeError('SYNTHETIC_DENY')
        adapter=core.Adapter(authorize=deny,send=h.send,checkpoint=h.checkpoint,wall=lambda:h.now,monotonic=lambda:h.mono,sleep=h.sleep,local_time_checks=True,initial_lease=h.lease)
        r=adapter.run(h.binding,h.render,h.contract,validator)
        self.assertEqual('AUTHORIZATION_DENIED',r['error_code'])
        self.assertEqual(['verify'],calls);self.assertEqual([],h.requests)
    def test_withdrawal_after_one_ack_blocks_second_patch_and_keeps_fence(self):
        f=ContractHarness();seen=[0]
        def before(role,r):
            if role=='approval':
                seen[0]+=1
                if seen[0]==4:raise RuntimeError('SYNTHETIC_WITHDRAWN_AFTER_ACK')
        f.providers.before=before
        r=self.execute(f,True)
        self.assertEqual(1,len(f.h.requests))
        self.assertEqual('PATCH',f.h.requests[0].method)
        self.assertEqual(1,len(r['receipt']['acknowledged']))
        self.assertEqual('AUTHORIZATION_DENIED',r['receipt']['error_code'])
        self.assertEqual('RETAINED',r['fence'])
        self.assertEqual(0,f.storage.complete_calls)
