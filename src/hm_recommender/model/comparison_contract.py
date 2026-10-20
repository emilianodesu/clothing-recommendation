"""Dependency-free contract shared by the host launcher and comparison worker."""


def validate_comparison_sources(candidate, reference):
    left, right = candidate['config'], reference['config']
    expected_reference = (1, 2) if right.get('demo') else (1,)
    if left.get('epochs') != 3 or right.get('epochs') not in expected_reference:
        raise ValueError('Comparison requires three epochs versus one (demo may use two)')
    if {k: v for k, v in left.items() if k != 'epochs'} != {k: v for k, v in right.items() if k != 'epochs'}:
        raise ValueError('Only epochs may differ between training configurations')
    if candidate['run_id'] == reference['run_id'] or candidate['uv_lock_sha256'] != reference['uv_lock_sha256']:
        raise ValueError('Comparison source identity or dependency lock mismatch')
    for source in (candidate, reference):
        if source['input_identity'].get('config') != source['config']:
            raise ValueError('Source config and input identity disagree')
    if ({k: v for k, v in candidate['input_identity'].items() if k not in ('config', 'sha256')} !=
            {k: v for k, v in reference['input_identity'].items() if k not in ('config', 'sha256')}):
        raise ValueError('Comparison requires identical model inputs and dataset')
    original_rows = reference['rows_consumed']
    if original_rows <= 0 or original_rows % right['epochs'] or candidate['rows_consumed'] != original_rows // right['epochs'] * 3:
        raise ValueError('Comparison epoch row reconciliation failed')
