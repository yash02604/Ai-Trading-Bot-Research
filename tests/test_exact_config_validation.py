"""Offline-only capability, exact canonical values and state isolation."""
import importlib.util
import json
from pathlib import Path
import pytest

def api():
    assert importlib.util.find_spec('app.research.exact_config_validation'), 'dedicated offline validation entry required'
    from app.research import exact_config_validation as module
    return module

@pytest.fixture(scope='module')
def validation(tmp_path_factory):
    module=api()
    return module.run_validation(tmp_path_factory.mktemp('exact-offline')/'OFFLINE_VALIDATION_ONLY')

def test_dedicated_entry_point_exists():
    import ast
    module = api()
    todo = ['app.research.exact_config_validation']; seen = set()
    forbidden = {'place_order', 'modify_order', 'cancel_order'}
    while todo:
        name = todo.pop()
        path = module.ROOT.joinpath(*name.split('.')).with_suffix('.py')
        if name in seen or not path.is_file(): continue
        seen.add(name)
        for node in ast.walk(ast.parse(path.read_bytes())):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name not in forbidden
            if isinstance(node, ast.Attribute): assert node.attr not in forbidden
            if isinstance(node, ast.Import): todo.extend(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                assert 'broker' not in node.module.lower()
                todo.append(node.module)
                todo.extend(node.module + '.' + alias.name for alias in node.names)
    assert 'app.research.causal_forward_paper_v2' in seen
    assert 'app.research.scheduled_forward_paper' in seen

@pytest.mark.parametrize('name,state',[
    ('no_opportunity','NO_OPPORTUNITY'),('one_candidate','COMPLETED'),('stale_quote','NO_OPPORTUNITY'),
    ('candidate_expiry','NO_OPPORTUNITY'),('provider_failure','NO_OPPORTUNITY'),('market_failure','NO_OPPORTUNITY'),
    ('provider_exhaustion','COMPLETED'),('market_exhaustion','FAILED_CLOSED'),('observer_failure','COMPLETED'),
    ('recovery','COMPLETED'),('retry','COMPLETED'),('serial_slow','NO_OPPORTUNITY')])
def test_actual_exact_config_scenarios(validation,name,state):
    case=validation['scenarios'][name]
    assert case['status']==state
    assert case['validation_kind']=='OFFLINE_VALIDATION_ONLY'
    assert case['canonical_config_identity']==validation['canonical_config_identity']

def test_complete_day_and_trade_request_graph(validation):
    assert validation['scenarios']['no_opportunity']['request_counts']=={'market_data_requests':1008,'provider_requests':36}
    assert validation['scenarios']['one_candidate']['request_counts']=={'market_data_requests':32,'provider_requests':1}
    assert validation['scenarios']['one_candidate']['settled_trades']==1
    assert validation['scenarios']['one_candidate']['selected_symbol']=='RELIANCE'

def test_exact_cap_and_recovery(validation):
    assert validation['scenarios']['provider_exhaustion']['request_counts']=={'market_data_requests':32,'provider_requests':36}
    case=validation['scenarios']['market_exhaustion']
    assert case['request_counts']['market_data_requests']==1100 and case['settled_trades']==0
    assert case['account']['reserved_cash']>0 and case['account']['open_positions']==1
    assert validation['scenarios']['recovery']['recovery_no_reacquisition'] is True
    assert validation['scenarios']['recovery']['settled_trades']==1

def test_observer_and_retry(validation):
    assert validation['scenarios']['observer_failure']['account']==validation['scenarios']['one_candidate']['account']
    assert 'OBSERVER_UNAVAILABLE' in validation['scenarios']['observer_failure']['warnings']
    retry=validation['scenarios']['retry']
    assert retry['request_counts']=={'market_data_requests':32,'provider_requests':1}
    assert retry['physical_attempts']==35

def test_validation_cannot_authorize_production(validation):
    assert validation['identity_status']=='IDENTITY_MATCHES_PENDING_APPROVAL'
    assert validation['production_preflight_before']['status']=='NO_GO'
    assert validation['production_preflight_after']['status']=='NO_GO'
    assert validation['production_artifacts_unchanged'] is True
    assert validation['production_modules_unchanged'] is True
    assert validation['storage_binding_is_production_incompatible'] is True
    assert validation['network_guard_checks']=='PASS'
    assert len(validation['guard_checks']) == 8
    assert set(validation['guard_checks'].values()) == {'BLOCKED'}
    assert validation['approval_sha256_before'] == validation['approval_sha256_after']
    assert validation['secret_marker_leaks']==[]
    assert validation['live_feasibility']=='LIVE FEASIBILITY UNPROVEN'

def test_output_refuses_production_or_existing_root(tmp_path):
    module=api()
    for path in (module.ROOT/'logs/forward-paper-v2/control',module.ROOT/'app',tmp_path):
        with pytest.raises(ValueError):module.run_validation(path,scenarios=('one_candidate',))

def test_invalid_scenario_and_foreign_config_rejected(tmp_path):
    module=api()
    with pytest.raises(ValueError):module.run_validation(tmp_path/'new',scenarios=('LIVE',))
    with pytest.raises(ValueError):module.run_validation(tmp_path/'new',config_path=tmp_path/'other.json')

def test_validation_artifacts_are_separate(validation):
    assert validation['schema_version']=='offline-exact-config-validation-v1'
    assert validation['validation_kind']=='OFFLINE_VALIDATION_ONLY'
    assert validation['mode']=='OFFLINE_VALIDATION_ONLY'
    for artifact in validation['artifacts']:
        assert artifact['validation_kind']=='OFFLINE_VALIDATION_ONLY'
        assert artifact['production_approval_eligible'] is False


def test_dependency_and_manifest_identities(validation):
    from app.research import v2_transport_contract as contract
    assert len(validation['dependency_hashes']) == 26
    assert contract.digest(validation['dependency_hashes']) == validation['source_identity']
    assert validation['manifest_sha256'] == validation['dependency_hashes']['data/audits/nse_cm_session_manifest_v3.json']

def test_validation_report_cannot_be_a_production_approval(validation,tmp_path):
    from app.research import v2_transport_contract as contract
    module=api();original=module.CANONICAL_CONFIG.read_bytes()
    cfg=json.loads(original)
    # Negative parser probe only, never passed to the validation lifecycle.
    for key in ('market_data_enabled','provider_enabled','production_start_enabled'):cfg['transport'][key]=True
    path=tmp_path/'not-an-approval.json';path.write_text(json.dumps(validation))
    cfg['transport']['review_approval']=str(path)
    result=contract.readiness(cfg,'2026-11-16T12:10:00+05:30')
    assert result['status']=='NO_GO' and result['reason_codes']==['TRANSPORT_REVIEW_REQUIRED']
    assert module.CANONICAL_CONFIG.read_bytes()==original

def test_worker_rejects_direct_production_output():
    module=api()
    with pytest.raises(ValueError):module._worker_entry(module.ROOT/'logs/forward-paper-v2/control',('one_candidate',))

def test_no_generic_bypass_or_transport_injection(tmp_path):
    module=api()
    for field in ('skip_approval','ignore_preflight','transport','force'):
        with pytest.raises(TypeError):module.run_validation(tmp_path/'new',**{field:True})
