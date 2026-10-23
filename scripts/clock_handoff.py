"""Strict aggregate-only schema for a read-only follow-up, including missing logs."""
from collections import Counter
import json
import re
from events_clock import analyze_timing, utc, number, require, MAX_EVENTS

PROTOCOL = 'events-clock-v1'


def count(value):
    return type(value) is int and value >= 0


def complete(power):
    return (power['collection_status'] == 'complete' and power['log_brackets_window']
            and power['malformed_timestamp_rows'] == 0 and not power['events_truncated'])


def validate_clock(report, result, env, names):
    try:
        needed = {'manifest.json', 'result.json', 'environment.json', 'commit.txt', 'job.log'}
        require(needed <= set(names) <= needed | {'summary.md'})
        require(report['source_clean'] is True and report['result'] == result)
        require(report['kind'] == 'events-handoff' and report['context'] == 'host-read-only')
        require(report['status'] == result['status'] and report['status'] in ('passed','failed'))
        require(set(env) == {'run_id','source_commit','runner_tree_sha256'})
        require(report['run_id'] == env['run_id'] and report['source_commit'] == env['source_commit'])
        require(re.fullmatch('[a-f0-9]{40}', env['source_commit']) is not None)
        require(re.fullmatch('[a-f0-9]{64}', env['runner_tree_sha256']) is not None)
        require(set(result) == {'stage','protocol','status','clock_diagnostic','training_performed','test_evaluated','t5_closed'})
        require(result['stage'] == 'T5' and result['protocol'] == PROTOCOL)
        require(all(result[k] is False for k in ('training_performed','test_evaluated','t5_closed')))
        d = result['clock_diagnostic']
        require(set(d) == {'source','host','timing','power','complete'})
        require(type(d['complete']) is bool)
        source = d['source']
        require(set(source) == {'run_id','machine_id','source_commit','archive_sha256','package_id','status','protocol'})
        require(source['machine_id'] == report['machine_id'])
        require(re.fullmatch('[a-z][a-z0-9_-]{0,31}', source['machine_id']) is not None)
        require(isinstance(source['run_id'],str) and re.fullmatch('events-[a-zA-Z0-9_-]{1,150}',source['run_id']))
        require(source['protocol'] == 'events-memory-v1' and source['status'] in ('passed','failed'))
        for k, size in (('source_commit',40),('archive_sha256',64),('package_id',64)):
            require(re.fullmatch('[a-f0-9]{'+str(size)+'}',source[k]) is not None)
        timing = d['timing']
        expected = analyze_timing(timing['started_at'], timing['finished_at'], timing['monotonic_duration_seconds'], timing['snapshots'])
        require(json.dumps(timing,sort_keys=True) == json.dumps(expected,sort_keys=True))
        host = d['host']
        require(set(host) == {'system','system_version','machine','python','clocks'})
        for k in ('system','system_version','machine','python'):
            require(isinstance(host[k],str) and re.fullmatch('[A-Za-z0-9_.+ -]{1,100}',host[k]))
        require(set(host['clocks']) == {'time','monotonic','perf_counter'})
        for clock in host['clocks'].values():
            require(set(clock) == {'implementation','monotonic','adjustable','resolution'})
            require(isinstance(clock['implementation'],str) and re.fullmatch('[A-Za-z0-9_ .(),+-]{1,120}',clock['implementation']))
            require(type(clock['monotonic']) is bool and type(clock['adjustable']) is bool)
            require(number(clock['resolution']) and 0 < clock['resolution'] <= 1)
        power = d['power']
        require(set(power) == {'collection_status','log_brackets_window','window_timestamped_rows',
                'unrecognized_window_rows','malformed_timestamp_rows','counts','events','events_truncated'})
        require(power['collection_status'] in ('complete','command_unavailable','command_failed','timeout','size_limit','read_error','unsupported_platform'))
        require(type(power['log_brackets_window']) is bool and type(power['events_truncated']) is bool)
        for k in ('window_timestamped_rows','unrecognized_window_rows','malformed_timestamp_rows'):
            require(count(power[k]))
        require(set(power['counts']) == {'sleep','wake','darkwake'} and all(count(n) for n in power['counts'].values()))
        total = sum(power['counts'].values())
        require(total + power['unrecognized_window_rows'] == power['window_timestamped_rows'])
        require(power['events_truncated'] == (total > MAX_EVENTS))
        events = power['events']
        require(isinstance(events,list) and len(events) == min(total,MAX_EVENTS))
        require(events == sorted(events,key=lambda e:(e['at'],e['kind'])))
        for event in events:
            require(set(event) == {'at','kind'} and event['kind'] in power['counts'])
            require(utc(timing['started_at']) <= utc(event['at']) <= utc(timing['finished_at']))
        seen = Counter(e['kind'] for e in events)
        require(all(seen[k] <= v for k,v in power['counts'].items()))
        if not power['events_truncated']:
            require(all(seen[k] == v for k,v in power['counts'].items()))
        require(d['complete'] == complete(power))
        require(result['status'] == ('passed' if d['complete'] else 'failed'))
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('Malformed clock follow-up') from error

