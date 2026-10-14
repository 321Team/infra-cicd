"""Synthetic GET receipts only; no Kubernetes access or Runtime assertion."""
import copy
import unittest

import compare_scan_observations as compare


def snapshot(at):
    queries = []
    for kind, api in [('deployments','/apis/apps/v1'),('pods','/api/v1'),
                      ('services','/api/v1'),('endpointslices','/apis/discovery.k8s.io/v1')]:
        queries.append({'kind':kind,'rc':0,'at_utc':at,'raw_stdout_sha256':'a'*64,'items':[],
                        'argv':['/usr/bin/kubectl','--kubeconfig=/etc/kubernetes/admin.conf',
                                '--request-timeout=10s','get','--raw='+api+'/namespaces/fds-app/'+kind]})
    for name in compare.SERVICES:
        c = {'name':name,'image':'10.1.93.54/fds/'+name+'@sha256:'+'d'*64,'imagePullPolicy':'IfNotPresent'}
        queries[0]['items'].append({'metadata':{'name':name,'namespace':'fds-app','uid':name,'generation':1,'deletionTimestamp':None},
                                  'replicas':3,'spec_sha256':'b'*64,'containers':[c],
                                  'status':{'observedGeneration':1,'readyReplicas':3,'updatedReplicas':3,'availableReplicas':3}})
        for index in range(3):
            pod = name+'-rs-'+str(index)
            queries[1]['items'].append({'metadata':{'name':pod,'namespace':'fds-app','uid':pod,'deletionTimestamp':None},
                                      'node':'node'+str(index),'phase':'Running','containers':[copy.deepcopy(c)],
                                      'owners':[{'kind':'ReplicaSet','name':name+'-rs','uid':name+'-rs','controller':True}],
                                      'statuses':[{'name':name,'ready':True,'started':True,'restartCount':0,
                                                   'imageID':c['image'],'containerID':'containerd://'+pod}]})
    return {'host':'k8s-control01','mutation':False,'secret_or_env_values_collected':False,
            'started_at_utc':at,'finished_at_utc':at,'queries':queries}


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.before = snapshot('2026-10-14T06:00:00Z')
        self.after = snapshot('2026-10-14T06:04:00Z')
        self.result = {'context':{'run_id':'123','workflow_sha':'a'*40,'attempt':1,
                                 'negative_case':'vulnerability','purpose':'SCAN_NEGATIVE_NOT_DELIVERABLE'},
                       'case':'vulnerability','SCAN_NEGATIVE_WORKFLOW':'FAIL',
                       'NEGATIVE_CASE':'EXPECTED_FAILURE_OBSERVED','PUSH':'NOT_EXECUTED','DEPLOY':'NOT_EXECUTED',
                       'started_at':'2026-10-14T06:01:00Z','finished_at':'2026-10-14T06:03:00Z'}

    def execute(self):
        return compare.compare(self.before,self.after,self.result,'123','a'*40,'vulnerability')

    def test_same_fresh_identity_is_observed_unchanged(self):
        self.assertEqual(self.execute()['IMAGE_PRESERVATION'],'OBSERVED_UNCHANGED')

    def test_stale_wrong_run_wrong_case_or_unconfirmed_failure_rejected(self):
        for field, value in [('started_at','2026-10-14T07:00:00Z'),
                             ('case','scanner-error'),('NEGATIVE_CASE','NOT_CONFIRMED'),('DEPLOY','EXECUTED')]:
            with self.subTest(field=field):
                original = copy.deepcopy(self.result)
                self.result[field] = value
                with self.assertRaises(ValueError): self.execute()
                self.result = original
        self.result['context']['run_id']='124'
        with self.assertRaises(ValueError): self.execute()

    def test_image_uid_generation_restart_or_container_drift_rejected(self):
        for kind, group, field, value in [(0,'metadata','uid','other'),(0,'metadata','generation',2),
                                           (0,None,'spec_sha256','e'*64),(1,'statuses','restartCount',1),
                                           (1,'statuses','imageID','different'),(1,'statuses','containerID','other')]:
            with self.subTest(field=field):
                self.after=snapshot('2026-10-14T06:04:00Z')
                item=self.after['queries'][kind]['items'][0]
                target=item if group is None else item[group][0] if group=='statuses' else item[group]
                target[field]=value
                with self.assertRaises(ValueError): self.execute()

    def test_empty_duplicate_or_unready_observations_cannot_pass(self):
        for fault in ('empty','duplicate','unready','missing_pod','failed_get','wrong_time'):
            with self.subTest(fault=fault):
                self.after=snapshot('2026-10-14T06:04:00Z')
                q=self.after['queries']
                if fault=='empty': q[0]['items']=[]
                elif fault=='duplicate': q[0]['items'].append(copy.deepcopy(q[0]['items'][0]))
                elif fault=='unready': q[1]['items'][0]['statuses'][0]['ready']=False
                elif fault=='missing_pod': q[1]['items'].pop()
                elif fault=='failed_get': q[0]['rc']=1
                elif fault=='wrong_time': q[0]['at_utc']='2026-10-13T06:04:00Z'
                with self.assertRaises(ValueError): self.execute()


if __name__=='__main__':
    unittest.main()
