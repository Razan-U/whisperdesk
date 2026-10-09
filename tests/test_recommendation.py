from whisperdesk.recommendation import recommend, recommendation_text, apply_recommendation


GB = 1024 ** 3


def hw(cpu=8, ram=16, cuda=False, vram=None):
    gpus = []
    if vram is not None:
        gpus = [{'name': 'NVIDIA Test', 'vram_bytes': vram * GB}]
    return {
        'cpu_logical': cpu,
        'ram_bytes': ram * GB if ram is not None else None,
        'gpus': gpus,
        'cuda_count': 1 if cuda else 0,
        'cuda_available': cuda,
    }


def test_cuda_recommends_turbo_auto_for_normal_workload():
    item = recommend(hw(cpu=12, ram=32, cuda=True, vram=12), 45 * 60)
    assert item['model'] == 'turbo'
    assert item['device'] == 'auto'
    assert 'CUDA доступна' in item['reason']


def test_low_vram_cuda_recommends_small_auto():
    item = recommend(hw(cpu=8, ram=16, cuda=True, vram=3), 30 * 60)
    assert item['model'] == 'small'
    assert item['device'] == 'auto'
    assert 'VRAM' in item['reason']


def test_weak_cpu_recommends_base_cpu():
    item = recommend(hw(cpu=4, ram=8, cuda=False), 30 * 60)
    assert item['model'] == 'base'
    assert item['device'] == 'cpu'


def test_balanced_cpu_recommends_small():
    item = recommend(hw(cpu=12, ram=16, cuda=False), 30 * 60)
    assert item['model'] == 'small'
    assert item['device'] == 'cpu'


def test_long_cpu_workload_recommends_base():
    item = recommend(hw(cpu=16, ram=32, cuda=False), 2 * 3600)
    assert item['model'] == 'base'
    assert item['device'] == 'cpu'


def test_fast_profile_can_promote_base_to_small():
    item = recommend(hw(cpu=8, ram=16, cuda=False), 30 * 60, profile='fast')
    assert item['model'] == 'small'


def test_recommendation_text_is_user_facing():
    text = recommendation_text({'model': 'turbo', 'device': 'auto', 'reason': 'Тестова причина.'})
    assert 'Прискорена велика · turbo + Авто' in text
    assert 'Тестова причина.' in text



def test_apply_recommendation_changes_runnable_new_tasks(tmp_path):
    tasks = [{
        'status': 'pending',
        'model': 'base',
        'device': 'cpu',
        'session': str(tmp_path / 'new.jsonl'),
    }]
    result = apply_recommendation(tasks, {'model': 'turbo', 'device': 'auto'})
    assert tasks[0]['model'] == 'turbo'
    assert tasks[0]['device'] == 'auto'
    assert result == {'changed': 1, 'model_locked': 0}


def test_apply_recommendation_preserves_model_for_resume_session(tmp_path):
    session = tmp_path / 'resume.jsonl'
    session.write_text('{}\n', encoding='utf-8')
    tasks = [{
        'status': 'interrupted',
        'model': 'base',
        'device': 'cpu',
        'session': str(session),
    }]
    result = apply_recommendation(tasks, {'model': 'turbo', 'device': 'auto'})
    assert tasks[0]['model'] == 'base'
    assert tasks[0]['device'] == 'auto'
    assert result == {'changed': 1, 'model_locked': 1}


def test_apply_recommendation_does_not_touch_completed_task(tmp_path):
    tasks = [{
        'status': 'done',
        'model': 'base',
        'device': 'cpu',
        'session': str(tmp_path / 'done.jsonl'),
    }]
    result = apply_recommendation(tasks, {'model': 'turbo', 'device': 'auto'})
    assert tasks[0]['model'] == 'base'
    assert tasks[0]['device'] == 'cpu'
    assert result == {'changed': 0, 'model_locked': 0}
