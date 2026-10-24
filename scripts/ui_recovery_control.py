"""Resource admission and exact-service lifecycle for UI recovery preparation."""
G = 2**30
STOP_ORDER = {'airflow-scheduler':0, 'airflow-dag-processor':1, 'airflow-api':2,
              'api':3, 'mlflow':4, 'postgres':5}
START_ORDER = {'postgres':0, 'api':1, 'mlflow':2, 'airflow-api':3,
               'airflow-dag-processor':4, 'airflow-scheduler':5}
IDENTITY = ('container_id','image_id','project','service','memory_bytes','nano_cpus')


def require(value, message):
    if not value:raise ValueError(message)


def identity(before, after):
    require(all(before[k] == after[k] for k in IDENTITY), 'Container identity or limits changed')


def select_targets(rows, project, restored_postgres):
    restored = rows.get(restored_postgres, {})
    other = restored.get('project')
    require(other and other != project and restored.get('service') == 'postgres', 'Restored T7 database differs')
    expected = {(project,s) for s in ('api','postgres','ui','mlflow','airflow-api','airflow-scheduler','airflow-dag-processor')} | {(other,s) for s in ('api','postgres')}
    active = [r for r in rows.values() if r['running']]
    require(len(active)==9 and {(r['project'],r['service']) for r in active}==expected, 'Unexpected running inventory')
    require(all(r['health']=='healthy' for r in active), 'Unhealthy service')
    targets = [r for r in active if r['project']==other or r['service'] not in ('api','postgres','ui')]
    return sorted(targets, key=lambda r:STOP_ORDER[r['service']])


def admit(rows, memory, extra):
    active = [r for r in rows.values() if r['running']]
    require(all(type(r['memory_bytes']) is int and r['memory_bytes']>0 for r in active), 'Unbounded active memory')
    required = sum(r['memory_bytes'] for r in active) + extra + G
    require(required<=memory, 'Insufficient capacity with 1 GiB margin')
    return required


def restored_services(rows, inspect, start, wait):
    """Attempt every saved auxiliary even if an earlier recovery fails."""
    result = []
    for row in sorted(rows,key=lambda r:START_ORDER[r['service']]):
        item = dict(role=row['service'],restored=False)
        try:
            current=inspect(row['container_id']);identity(row,current)
            if not current['running']:start(row['container_id'])
            current=wait(row['container_id']);identity(row,current)
            require(current['running'] and current['health']=='healthy', 'Auxiliary not healthy')
            item['restored']=True
        except Exception as error:item['error_type']=type(error).__name__
        result.append(item)
    return result
