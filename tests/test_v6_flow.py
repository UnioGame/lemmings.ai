from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/lemmings/scripts'))

from lemmings.contracts import read_object, write_object
from lemmings.flow import advance_task, finish_phase, finish_task, replan_flow, start_flow, submit_flow
from lemmings.migration import apply_migration, propose_migration
from lemmings.task_workflow import TaskBriefError, prepare_task
from lemmings.invocations import _review_lane, record_invocation


def init_repo(path:Path)->None:
    subprocess.run(['git','init','-q'],cwd=path,check=True)
    subprocess.run(['git','config','user.email','tests@example.invalid'],cwd=path,check=True)
    subprocess.run(['git','config','user.name','Tests'],cwd=path,check=True)
    (path/'owned.txt').write_text('base\n',encoding='utf-8')
    subprocess.run(['git','add','owned.txt'],cwd=path,check=True)
    subprocess.run(['git','commit','-qm','base'],cwd=path,check=True)


def profile()->dict:
    return json.loads((ROOT/'skills/lemmings/defaults.json').read_text(encoding='utf-8'))


def brief(*,accounting='invocation-v1',capabilities=None,task_id='T1')->dict:
    return {'schemaVersion':1,'taskId':task_id,'goal':'change owned file','acceptance':['owned behavior passes'],'dependencies':[],'risks':[],
            'ownership':{'owned':['owned.txt'],'shared':[],'forbidden':[]},'workingSet':[{'ref':'owned.txt','purpose':'implementation target'}],
            'validation':{'riskToTest':[],'commands':['git diff --check'],'allowedOutputs':[]},
            'managerDecision':{'requestedMode':'standard','resolvedMode':'standard','riskClass':'medium','modeReasons':['workerRequired'],'workerRequired':True,'reviewRequired':True,'planReviewRequired':False,'reviewPolicy':'single','workspace':{'policy':'current','backend':'current','reason':'one writer'},'roleAssignments':{'worker':'native::current-host/default','reviewer':'native::current-host/default'},'accountingMode':accounting,'hostCapabilities':capabilities or {}}}


def strict_brief(task_id='S1')->dict:
    value=brief(task_id=task_id)
    value['managerDecision'].update(requestedMode='auto',resolvedMode='strict',riskClass='high',modeReasons=['highRisk'],workspace={'policy':'isolated','backend':'code-worktree','reason':'phase writer','workspaceId':'ws-'+task_id,'managedBy':'external','lifetime':'phase','estimatedGiB':0,'approval':'not-required'})
    return value

class AccountingAndFlowTests(unittest.TestCase):
    def test_default_is_invocation_accounting(self):
        self.assertEqual('invocation-v1',profile()['accountingMode'])

    def test_host_v1_capability_gate_precedes_task_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);init_repo(repo);target=repo/'task.json'
            with self.assertRaisesRegex(TaskBriefError,'trusted usageAccounting'):
                prepare_task(repo,target,brief(accounting='host-v1'),profile=profile())
            self.assertFalse(target.exists())
            accepted=brief(accounting='host-v1',capabilities={'native':{'usageAccounting':True}})
            prepare_task(repo,target,accepted,profile=profile())
            task=read_object(target)
            self.assertEqual('host-v1',task['budget']['policy']['accountingMode'])
            self.assertTrue(task['accountingCapabilities']['hosts']['native']['usageAccounting'])

    def test_host_v1_cross_review_gates_both_reviewer_hosts(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);init_repo(repo);target=repo/'task.json';value=brief(accounting='host-v1',capabilities={'native':{'usageAccounting':True}});value['managerDecision']['reviewPolicy']='cross';value['managerDecision']['reviewerRecovery']={'hostId':'other','providerId':'p','modelId':'reviewer'}
            with self.assertRaisesRegex(TaskBriefError,'other'):
                prepare_task(repo,target,value,profile=profile())
            self.assertFalse(target.exists())

    def test_flow_start_reserves_one_owner_bound_worker_and_replays(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);init_repo(repo);source=repo/'brief.json';owner=repo/'task.json';source.write_text(json.dumps(brief()),encoding='utf-8')
            first=start_flow(repo,source,owner,profile())
            self.assertEqual('Active',first['status']);self.assertEqual('dispatch-worker',first['actions'][0]['type'])
            invocation=first['actions'][0]['invocation'];self.assertEqual(('task','T1'),(invocation['ownerKind'],invocation['ownerId']))
            from lemmings.flow import status_flow
            again=status_flow(repo,owner,profile())
            self.assertEqual(invocation['invocationId'],again['actions'][0]['invocationId'])
            self.assertEqual(1,len(read_object(owner)['execution']['invocations']))

    def test_simple_manager_candidate_reaches_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);init_repo(repo);source=repo/'brief.json';owner=repo/'task.json';value=brief();value['managerDecision'].update(requestedMode='auto',resolvedMode='simple',riskClass='low',modeReasons=['single-domain-low-risk'],workerRequired=False,reviewRequired=False);source.write_text(json.dumps(value),encoding='utf-8');subprocess.run(['git','add','brief.json'],cwd=repo,check=True);subprocess.run(['git','commit','-qm','brief'],cwd=repo,check=True)
            started=start_flow(repo,source,owner,profile());self.assertEqual('manager-implement',started['actions'][0]['type'])
            head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip();advanced=advance_task(repo,owner,profile(),candidate_head=head)
            self.assertIn('status',advanced,advanced);self.assertEqual('Accepted',advanced['status']);self.assertTrue(read_object(owner)['execution']['candidateReadiness']['managerCandidate'])

    def test_cross_review_primary_lane_and_capability_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);init_repo(repo);owner=repo/'task.json';value=brief();value['managerDecision']['reviewPolicy']='cross';value['managerDecision']['reviewerRecovery']={'hostId':'other','providerId':'p','modelId':'r'};prepare_task(repo,owner,value,profile=profile());task=read_object(owner)
            self.assertEqual('native::current-host/default',_review_lane(task,profile(),explicit='native::current-host/default'))
            task['budget']['policy']['accountingMode']='host-v1';task['accountingCapabilities']={'hosts':{'native':{'usageAccounting':True}},'digest':'tampered'};write_object(owner,task)
            with self.assertRaisesRegex(ValueError,'snapshot digest'):record_invocation(repo,owner,profile(),'worker',1,task['revision'])

    def test_phase_failure_creates_no_children_and_finish_checks_ancestry(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);init_repo(repo);task=brief(task_id='A');phase={'schemaVersion':1,'phaseId':'P1','validation':{'commands':['git diff --check']},'managerDecision':{'roleAssignments':{'reviewer':{'hostId':'native','providerId':'p','modelId':'r'}},'accountingMode':'host-v1','hostCapabilities':{}},'tasks':[task]};source=repo/'p.json';source.write_text(json.dumps(phase),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'usageAccounting'):start_flow(repo,source,repo/'phase.json',profile())
            self.assertFalse((repo/'tasks').exists())
            phase['managerDecision']['accountingMode']='invocation-v1';phase['tasks']=[strict_brief(task_id='A')];source.write_text(json.dumps(phase),encoding='utf-8');started=start_flow(repo,source,repo/'phase.json',profile())
            saved=read_object(repo/'phase.json');saved['execution']['phaseGate']={'status':'Accepted'};write_object(repo/'phase.json',saved)
            child=repo/saved['taskRefs'][0];child_value=read_object(child);child_value['state']='Integrated';child_value['close']['mergeCommit']='deadbeef';write_object(child,child_value)
            with self.assertRaisesRegex(ValueError,'contain every child'):finish_phase(repo,repo/'phase.json')

    def test_replan_rejects_terminal_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);init_repo(repo);owner=repo/'task.json';prepare_task(repo,owner,brief(),profile=profile());task=read_object(owner);task['state']='Integrated';write_object(owner,task)
            with self.assertRaisesRegex(ValueError,'Replan Required'):replan_flow(repo,owner,{'goal':'new'},profile())

    def test_integration_failure_opens_bounded_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);init_repo(repo);owner=repo/'task.json';value=brief();value['ownership']['owned'].append('ok.txt');value['validation']['allowedOutputs']=['reviews/**'];value['validation']['commands']=["python -c \"import pathlib,sys;sys.exit(0 if pathlib.Path('ok.txt').exists() else 1)\""];prepare_task(repo,owner,value,profile=profile());task=read_object(owner);head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip();task['state']='Accepted';task['commits']['candidate']=head;write_object(owner,task)
            result=finish_task(repo,owner);stored=read_object(owner);self.assertEqual('Repair',stored['state']);self.assertEqual('delta',stored['reviewSpec']['mode']);self.assertEqual(1,stored['budget']['usage']['repairCycles']);self.assertFalse(result['ok']);self.assertTrue((repo/stored['reviewSpec']['previousReviewRef']).is_file())
            dispatched=advance_task(repo,owner,profile());worker=dispatched['actions'][0]['invocation'];(repo/'ok.txt').write_text('fixed\n',encoding='utf-8');subprocess.run(['git','add','ok.txt'],cwd=repo,check=True);subprocess.run(['git','commit','-qm','integration repair'],cwd=repo,check=True);fixed=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
            candidate=submit_flow(repo,owner,profile(),worker['invocationId'],{'status':'succeeded','candidateHead':fixed,'acceptanceEvidence':['integration fixed'],'validationEvidence':[]});reviewer=candidate['actions'][0]['invocation'];finding_id=read_object(owner)['execution']['activeRepair']['targetFindingIds'][0]
            accepted=submit_flow(repo,owner,profile(),reviewer['invocationId'],{'status':'succeeded','candidateHead':fixed,'acceptanceEvidence':['integration check inspected'],'validationEvidence':[],'findings':[],'blockers':[],'remainingRisks':[],'findingDispositions':{finding_id:'resolved'},'verdict':'Accepted'})
            self.assertEqual('Accepted',accepted['status'])
    def test_phase_rejects_standard_child_before_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);init_repo(repo);phase={'schemaVersion':1,'phaseId':'P1','validation':{'commands':['git diff --check']},'managerDecision':{'roleAssignments':{'reviewer':{'hostId':'native','providerId':'p','modelId':'r'}},'accountingMode':'invocation-v1','hostCapabilities':{}},'tasks':[brief(task_id='A')]};source=repo/'p.json';source.write_text(json.dumps(phase),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Strict mode'):start_flow(repo,source,repo/'phase.json',profile())
            self.assertFalse((repo/'tasks').exists());self.assertFalse((repo/'phase.json').exists())

    def test_replan_scope_invalidates_effective_config_and_recomputes_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            from lemmings.effective import digest
            repo=Path(temp);init_repo(repo);owner=repo/'task.json';prepare_task(repo,owner,brief(),profile=profile());task=read_object(owner);effective={'schemaVersion':5,'profile':{'name':'x','roleRoutes':{},'sources':{}},'rules':{'projects':[],'ruleRefs':[],'digest':'x'}};effective['digest']=digest(effective);task['effectiveConfig']=effective;task['state']='Replan Required';budget=json.loads(json.dumps(task['budget']));write_object(owner,task)
            result=replan_flow(repo,owner,{'requestedMode':'auto','riskClass':'high','ownership':{'owned':['owned.txt'],'shared':['contract.json'],'forbidden':[]},'workspace':{'policy':'isolated','backend':'code-worktree','reason':'shared contract','workspaceId':'ws-replan','managedBy':'external','lifetime':'task','estimatedGiB':0,'approval':'not-required'}},profile());stored=read_object(owner)
            self.assertEqual('Ready',result['status']);self.assertEqual('strict',stored['resolvedMode']);self.assertNotIn('effectiveConfig',stored);self.assertEqual(budget,stored['budget'])
    def test_changes_requested_opens_one_repair_and_dispatches_delta_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=root/'repo';repo.mkdir();init_repo(repo);source=root/'brief.json';owner=repo/'task.json';source.write_text(json.dumps(brief()),encoding='utf-8')
            started=start_flow(repo,source,owner,profile());worker=started['actions'][0]['invocation']
            (repo/'owned.txt').write_text('candidate\n',encoding='utf-8');subprocess.run(['git','commit','-qam','candidate'],cwd=repo,check=True);head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
            worker_result={'status':'succeeded','candidateHead':head,'acceptanceEvidence':['behavior checked'],'validationEvidence':[]}
            candidate=submit_flow(repo,owner,profile(),worker['invocationId'],worker_result);self.assertEqual('dispatch-reviewer',candidate['actions'][0]['type']);reviewer=candidate['actions'][0]['invocation']
            review_result={'status':'succeeded','candidateHead':head,'acceptanceEvidence':['criteria inspected'],'validationEvidence':[],'findings':[{'findingId':'F1','priority':'P1','origin':'implementation','summary':'boundary fails'}],'blockers':['F1'],'remainingRisks':[],'verdict':'ChangesRequested'}
            repair=submit_flow(repo,owner,profile(),reviewer['invocationId'],review_result)
            self.assertEqual('Repair',repair['status']);self.assertEqual('dispatch-worker',repair['actions'][0]['type'])
            stored=read_object(owner);self.assertEqual(['F1'],stored['execution']['activeRepair']['targetFindingIds']);self.assertEqual(1,len(stored['execution']['repairHistory']))
            repair_worker=repair['actions'][0]['invocation'];(repo/'owned.txt').write_text('fixed\n',encoding='utf-8');subprocess.run(['git','commit','-qam','repair'],cwd=repo,check=True);fixed=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
            repaired=submit_flow(repo,owner,profile(),repair_worker['invocationId'],{'status':'succeeded','candidateHead':fixed,'acceptanceEvidence':['boundary fixed'],'validationEvidence':[]})
            self.assertEqual('dispatch-reviewer',repaired['actions'][0]['type']);delta=repaired['actions'][0]['invocation']['reviewSpec'];self.assertEqual('delta',delta['mode']);self.assertEqual(['F1'],delta['findingIds'])

    def test_phase_projects_wave_and_serializes_overlapping_strict_writers(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);init_repo(repo);phase={'schemaVersion':1,'phaseId':'P1','contracts':[],'maxConcurrentWriters':2,'validation':{'commands':['git diff --check']},'managerDecision':{'roleAssignments':{'reviewer':{'hostId':'native','providerId':'p','modelId':'r'}},'accountingMode':'invocation-v1','hostCapabilities':{}},'tasks':[strict_brief('A'),strict_brief('B')]};source=repo/'phase-brief.json';owner=repo/'phase.json';source.write_text(json.dumps(phase),encoding='utf-8')
            started=start_flow(repo,source,owner,profile());advanced=submit_flow(repo,owner,profile(),started['actions'][0]['invocationId'],{'verdict':'Accepted','findings':[]})
            workers=[item for item in advanced['actions'] if item['type']=='dispatch-worker'];self.assertEqual(1,len(workers),advanced)
            saved=read_object(owner);states=[read_object(repo/ref)['state'] for ref in saved['taskRefs']];self.assertEqual(['Active','Ready'],states)
    def test_phase_gate_then_dependency_ready_wave(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);init_repo(repo);task=strict_brief(task_id='A');task['managerDecision']['reviewRequired']=False
            phase={'schemaVersion':1,'phaseId':'P1','contracts':[],'maxConcurrentWriters':1,'validation':{'commands':['git diff --check']},'managerDecision':{'roleAssignments':{'reviewer':{'hostId':'native','providerId':'current-host','modelId':'default'}},'accountingMode':'invocation-v1','hostCapabilities':{}},'tasks':[task]}
            source=repo/'phase-brief.json';owner=repo/'phase.json';source.write_text(json.dumps(phase),encoding='utf-8')
            started=start_flow(repo,source,owner,profile());self.assertEqual('dispatch-reviewer',started['actions'][0]['type'])
            result={'verdict':'Accepted','findings':[]}
            advanced=submit_flow(repo,owner,profile(),started['actions'][0]['invocationId'],result)
            self.assertEqual('dispatch-worker',advanced['actions'][0]['type']);self.assertTrue(advanced['actions'][0]['owner'].endswith('A.task.json'))


class MigrationTests(unittest.TestCase):
    def test_digest_confirmed_migration_preserves_budget_locks_and_review_graph(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);init_repo(repo)
            task=json.loads((ROOT/'skills/lemmings/templates/task.json').read_text(encoding='utf-8'));task['schemaVersion']=4;task['budget']=json.loads(json.dumps(__import__('lemmings.budget',fromlist=['new_task_budget']).new_task_budget({},'host-v1')));task['budget']['lockedRoles']=['worker'];task['reviewRef']='reviews/r.json';task['reviewHistory']=['reviews/r.json']
            review=json.loads((ROOT/'skills/lemmings/templates/review.json').read_text(encoding='utf-8'));review['schemaVersion']=4;review['subject']['taskId']=task['taskId']
            (repo/'reviews').mkdir();(repo/'reviews/r.json').write_text(json.dumps(review),encoding='utf-8');owner=repo/'task.json';owner.write_text(json.dumps(task),encoding='utf-8')
            proposal=propose_migration(repo,owner);out=repo/'migrated';result=apply_migration(repo,proposal,proposal['digest'],out)
            migrated=read_object(out/'task.json');self.assertEqual(5,migrated['schemaVersion']);self.assertEqual(['worker'],migrated['budget']['lockedRoles']);self.assertTrue((out/'reviews/r.json').is_file());self.assertEqual('migrated/reviews/r.json',migrated['reviewRef']);self.assertFalse(result['markerSwitched'])

    def test_apply_rejects_redigested_truncated_graph(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);init_repo(repo);task=json.loads((ROOT/'skills/lemmings/templates/task.json').read_text(encoding='utf-8'));task['schemaVersion']=4;task['reviewRef']='review.json';task['reviewHistory']=['review.json'];review=json.loads((ROOT/'skills/lemmings/templates/review.json').read_text(encoding='utf-8'));review['schemaVersion']=4;(repo/'task.json').write_text(json.dumps(task),encoding='utf-8');(repo/'review.json').write_text(json.dumps(review),encoding='utf-8')
            proposal=propose_migration(repo,repo/'task.json');proposal['entries']=[item for item in proposal['entries'] if item['kind']=='task'];proposal['digest']=__import__('hashlib').sha256(json.dumps({k:v for k,v in proposal.items() if k!='digest'},sort_keys=True,separators=(',',':')).encode()).hexdigest()
            with self.assertRaisesRegex(ValueError,'complete canonical'):apply_migration(repo,proposal,proposal['digest'],repo/'migrated')
            self.assertFalse((repo/'migrated').exists())
    def test_mixed_graph_and_active_reservation_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);init_repo(repo);task=json.loads((ROOT/'skills/lemmings/templates/task.json').read_text(encoding='utf-8'));task['schemaVersion']=4;task['reviewRef']='review.json';task['reviewHistory']=['review.json'];(repo/'task.json').write_text(json.dumps(task),encoding='utf-8');(repo/'review.json').write_text(json.dumps({'schemaVersion':5,'reviewId':'R'}),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'mixed-version'):propose_migration(repo,repo/'task.json')
            task['reviewRef']=None;task['reviewHistory']=[];task['budget']={'reservations':[{'invocationId':'x'}]};(repo/'task.json').write_text(json.dumps(task),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'reservation'):propose_migration(repo,repo/'task.json')


if __name__=='__main__':unittest.main()