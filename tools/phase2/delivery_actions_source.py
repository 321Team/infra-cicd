"""Native GitHub run/attempt/job/step observations, not image/GO attestation.

Only explicit reviewed code can select run pins and their existing producer
references. Workflow repository SHA is NOT the application's source SHA.
All reads reuse bounded GET-only public/private source transport. No dispatch,
rerun, log download, credential discovery, polling, cache or status write.
"""
from dataclasses import dataclass, field
import re
import time

import delivery_adapter as core
from delivery_adopted_authority import text
from delivery_github_source import (RepositoryPin, GitHubOriginalAdapter,
    repository_checked, positive, SHA1)
from delivery_private_source import PrivateGitHubOriginalAdapter
from delivery_object_reader import BearerCredential
from delivery_verifier import ReadRequest, Progress, ProducerEvidence
from delivery_actions_jobs import check_jobs

STAGES=('test','build','scan','push')


class ActionsSourceError(RuntimeError):
    """Fixed public codes only, never raw API/credentials/arbitrary messages."""


def need(ok,code):
    if not ok:raise ActionsSourceError(code)


@dataclass(frozen=True)
class RunPin:
    repository: RepositoryPin
    run_id: int
    attempt: int
    workflow_id: int
    workflow_commit: str
    workflow_path: str
    event: str
    branch: str


@dataclass(frozen=True)
class StagePin:
    stage: str
    producer_reference: str = field(repr=False)
    run: RunPin
    job_id: int
    job_name: str = field(repr=False)
    steps: tuple = field(repr=False)  # ((native step number, exact name), ...)


@dataclass(frozen=True)
class ActionsObservation:
    request: ReadRequest = field(repr=False)
    stages: tuple
    receipts: tuple = field(repr=False)
    execution_authorized: bool = field(default=False,init=False)
    app_lineage_authenticated: bool = field(default=False,init=False)


def policy_checked(pins):
    need(type(pins) is tuple and len(pins)==4
         and all(type(p) is StagePin for p in pins)
         and tuple(p.stage for p in pins)==STAGES,'ACTIONS_POLICY_INVALID')
    repos=set()
    for p in pins:
        run=p.run
        need(type(run) is RunPin,'ACTIONS_POLICY_INVALID')
        repository_checked(run.repository);repos.add(run.repository)
        need(all(positive(x) for x in (run.run_id,run.attempt,run.workflow_id,p.job_id))
             and type(run.workflow_commit) is str and SHA1.fullmatch(run.workflow_commit)
             and type(run.workflow_path) is str and re.fullmatch(
                 r'\.github/workflows/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.ya?ml',run.workflow_path)
             and text(run.event,64) and text(run.branch,256)
             and text(p.producer_reference,2048) and text(p.job_name,512)
             and type(p.steps) is tuple and 0<len(p.steps)<=512,
             'ACTIONS_POLICY_INVALID')
        numbers=set()
        for step in p.steps:
            need(type(step) is tuple and len(step)==2 and positive(step[0])
                 and text(step[1],512) and step[0] not in numbers,'ACTIONS_POLICY_INVALID')
            numbers.add(step[0])
    need(len(repos)==1,'ACTIONS_REPOSITORY_SCOPE_MISMATCH')
    # One run ID cannot silently mean two attempts/workflow revisions/jobs.
    grouped={};jobs={}
    for p in pins:
        if p.run.run_id in grouped:
            need(grouped[p.run.run_id]==p.run,'ACTIONS_RUN_POLICY_CONFLICT')
        grouped[p.run.run_id]=p.run
        key=(p.run,p.job_id)
        if key not in jobs:jobs[key]=(p.job_name,{})
        name,steps=jobs[key]
        need(name==p.job_name,'ACTIONS_JOB_POLICY_CONFLICT')
        for number,step_name in p.steps:
            need(number not in steps or steps[number]==step_name,'ACTIONS_STEP_POLICY_CONFLICT')
            steps[number]=step_name


class ActionsRunSource:
    def __init__(self,*,reader,pins,binding,credential,progress,monotonic=time.monotonic):
        try:
            policy_checked(pins);core.binding_checked(binding)
            need(type(reader) in (GitHubOriginalAdapter,PrivateGitHubOriginalAdapter)
                 and type(credential) is BearerCredential and callable(progress)
                 and callable(monotonic),'ACTIONS_CONFIGURATION_INVALID')
            self.reader,self.pins,self.binding=reader,pins,binding
            self.credential,self.progress,self.monotonic=credential,progress,monotonic
            self.last=None;self.closed=False
        except ActionsSourceError:raise
        except Exception:raise ActionsSourceError('ACTIONS_CONFIGURATION_INVALID') from None

    def check(self,r):
        need(not self.closed and type(r) is ReadRequest and r.binding==self.binding
             and r.credential is self.credential and type(r.progress) is Progress
             and self.progress()==r.progress,'ACTIONS_REQUEST_INVALID')
        now=self.monotonic()
        need(core.finite(now) and (self.last is None or now>=self.last)
             and core.finite(r.deadline) and now<r.deadline,'ACTIONS_DEADLINE_EXCEEDED')
        self.last=now

    @staticmethod
    def run_checked(value,pin):
        need(type(value) is dict and all(type(value.get(k)) is int for k in
             ('id','run_attempt','workflow_id')),'ACTIONS_RUN_INVALID')
        need((value['id'],value['run_attempt'],value['workflow_id'],value.get('head_sha'),
              value.get('path'),value.get('event'),value.get('head_branch')) ==
             (pin.run_id,pin.attempt,pin.workflow_id,pin.workflow_commit,
              pin.workflow_path,pin.event,pin.branch),'ACTIONS_RUN_PIN_MISMATCH')
        for key in ('repository','head_repository'):
            repo=value.get(key)
            need(type(repo) is dict and type(repo.get('id')) is int
                 and repo['id']==pin.repository.repository_id,'ACTIONS_RUN_REPOSITORY_MISMATCH')
        need(value.get('status')=='completed' and value.get('conclusion')=='success',
             'ACTIONS_RUN_NOT_SUCCESSFUL')
        return core.encoded({k:value.get(k) for k in ('id','run_attempt','workflow_id','head_sha',
            'path','event','head_branch','status','conclusion')})

    def inspect(self,request):
        try:
            self.check(request);receipts=[];observed=[]
            runs=tuple(dict.fromkeys(p.run for p in self.pins))
            for pin in runs:
                prefix=self.reader.repository(request,pin.repository,receipts)
                self.check(request)
                def read(path):
                    self.check(request)
                    value=self.reader.read(request,path,receipts)
                    self.check(request)
                    return value
                latest=prefix+f'/actions/runs/{pin.run_id}'
                attempt=latest+f'/attempts/{pin.attempt}'
                before=self.run_checked(read(latest),pin)
                need(before==self.run_checked(read(attempt),pin),'ACTIONS_ATTEMPT_CHANGED')
                gates={}
                for p in self.pins:
                    if p.run!=pin:continue
                    if p.job_id not in gates:gates[p.job_id]=(p.job_name,{})
                    name,steps=gates[p.job_id]
                    need(name==p.job_name,'ACTIONS_JOB_POLICY_CONFLICT')
                    for number,step_name in p.steps:
                        need(number not in steps or steps[number]==step_name,'ACTIONS_STEP_POLICY_CONFLICT')
                        steps[number]=step_name
                checked=check_jobs(read(attempt+'/jobs?per_page=100&page=1'),pin.run_id,
                    pin.workflow_commit,tuple((job,name,tuple(sorted(steps.items())))
                        for job,(name,steps) in gates.items()))
                need(before==self.run_checked(read(latest),pin),'ACTIONS_ATTEMPT_CHANGED')
                # Bounded four-stage policy: at most 4 runs x 5 response bodies.
                need(len(receipts)<=20 and all(type(raw) is bytes and len(raw)<=1024*1024 for raw in receipts),
                     'ACTIONS_RECEIPT_LIMIT')
                observed.append((pin.run_id,pin.attempt,pin.workflow_commit,checked))
            self.check(request)
            return ActionsObservation(request,tuple(observed),tuple(receipts))
        except ActionsSourceError:raise
        except Exception:raise ActionsSourceError('ACTIONS_SOURCE_UNVERIFIED') from None

    def close(self):
        self.closed=True
        self.reader=self.pins=self.credential=self.progress=None


class ActionsCheckedProducer:
    """Native successful steps plus existing original lineage/adoption, not a GO."""
    def __init__(self,*,source,gate,references):
        need(callable(source) and type(gate) is ActionsRunSource
             and type(references) is tuple and len(references)==4
             and tuple(p.producer_reference for p in gate.pins)==references,
             'ACTIONS_PRODUCER_REFERENCE_MISMATCH')
        self.source,self.gate,self.observation=source,gate,None

    def __call__(self,request):
        try:
            need(self.source is not None,'ACTIONS_PRODUCER_CLOSED')
            before=self.source(request)
            need(type(before) is ProducerEvidence and before.request is request
                 and before.binding==request.binding,'ACTIONS_PRODUCER_INVALID')
            observation=self.gate.inspect(request)
            after=self.source(request)
            self.gate.check(request)
            need(before==after,'ACTIONS_PRODUCER_CHANGED')
            self.observation=observation
            return after
        except ActionsSourceError:raise
        except Exception:raise ActionsSourceError('ACTIONS_PRODUCER_UNVERIFIED') from None

    def close(self):
        # Original source and gate are separately owned by the factory bundle.
        self.source=self.gate=self.observation=None
