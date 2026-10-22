"""Closed T4-B aggregate report validation. Standard library only."""
import re
from quality_handoff import ALLOWED, require, count, number, no_private_rows

CHECKS={'reference','candidate','restart','rollback','final'}
RELEASE=re.compile(r'h-[a-f0-9]{64}$')
HASH=re.compile(r'[a-f0-9]{64}$')


def validate_serving(report,result,environment,names):
    try:
        require(set(names)<={'manifest.json',*ALLOWED})
        require({'result.json','environment.json','commit.txt','job.log'}<=set(names))
        require(report['run_id'].startswith('serving-') and report['result']==result)
        require(report['source_clean'] is True and report['source_commit']==environment['source_commit'])
        require(environment['run_id']==report['run_id'])
        no_private_rows(result)
        v2=result['protocol']=='history-serving-v2'
        require(set(result)=={'stage','protocol','status','source_tree_sha256','sources','checks','resources',
            'final_release_id','activated','test_evaluated','quality_accepted','training_performed','duration_seconds'}|({'host_checks'} if v2 else set()))
        require(result['stage']=='T4-B' and result['protocol'] in ('history-serving-v1','history-serving-v2') and result['status']=='passed')
        require(HASH.fullmatch(result['source_tree_sha256']) and result['source_tree_sha256']==environment['source_tree_sha256'])
        require(result['activated'] is True)
        require(all(result[k] is False for k in ('test_evaluated','quality_accepted','training_performed')))
        sources=result['sources'];require(set(sources)=={'reference','candidate'})
        for source in sources.values():
            require(set(source)=={'run_id','archive_sha256','release_id','manifest_sha256','dataset'})
            require(source['run_id'].startswith('release-') and RELEASE.fullmatch(source['release_id']))
            require(HASH.fullmatch(source['archive_sha256']) and HASH.fullmatch(source['manifest_sha256']))
            require(source['dataset'] in ('synthetic','hm'))
        require(sources['reference']['release_id']!=sources['candidate']['release_id'])
        require(result['final_release_id']==sources['candidate']['release_id'])
        require(set(result['checks'])==CHECKS)
        for key,check in result['checks'].items():
            require(set(check)=={'release_id','source_tree_sha256','sampled_buyers','checked_requests','filtered_cases',
                'http_parity','response_sha256','catalog_size','past_buyers','request_seconds_mean','request_seconds_max','load_benchmark'}|({'host_reference_sha256'} if v2 else set()))
            if v2:require(HASH.fullmatch(check['host_reference_sha256']))
            expected=sources['reference' if key in ('reference','rollback') else 'candidate']['release_id']
            require(check['release_id']==expected and check['source_tree_sha256']==result['source_tree_sha256'])
            require(check['http_parity'] is True and check['load_benchmark'] is False)
            require(count(check['sampled_buyers'],2) and check['sampled_buyers']<=257)
            require(count(check['checked_requests'],check['sampled_buyers']) and count(check['filtered_cases'],1))
            require(HASH.fullmatch(check['response_sha256']))
            require(count(check['catalog_size'],1) and count(check['past_buyers'],1))
            require(number(check['request_seconds_mean']) and number(check['request_seconds_max'],check['request_seconds_mean']))
        for a,b in [('reference','rollback'),('candidate','restart'),('candidate','final')]:
            require(all(result['checks'][a][field]==result['checks'][b][field]
                for field in ('response_sha256','sampled_buyers','checked_requests','filtered_cases','catalog_size','past_buyers')))
        require(set(result['resources'])=={'before_restart','after_restart'})
        for resource in result['resources'].values():
            require(set(resource)=={'image_id','memory_bytes','nano_cpus','cgroup_peak_bytes','swap_peak_bytes','oom_killed','running','threads'})
            require(re.fullmatch(r'sha256:[a-f0-9]{64}',resource['image_id']))
            require(resource['memory_bytes']==4*2**30 and resource['nano_cpus']==2*10**9 and resource['threads']==2)
            require(count(resource['cgroup_peak_bytes'],1) and resource['cgroup_peak_bytes']<resource['memory_bytes'])
            require(resource['swap_peak_bytes']==0 and resource['oom_killed'] is False and resource['running'] is True)
        require(result['resources']['before_restart']['image_id']==result['resources']['after_restart']['image_id'])
        if v2:
            require(set(result['host_checks'])==CHECKS)
            ports=set()
            for key,host in result['host_checks'].items():
                require(set(host)=={'release_id','source_tree_sha256','host','port','binding_verified','health_live',
                    'health_ready','checked_requests','http_parity','response_sha256','request_seconds_mean','request_seconds_max','load_benchmark'})
                reference=result['checks'][key]
                require(host['release_id']==reference['release_id'] and host['source_tree_sha256']==result['source_tree_sha256'])
                require(host['host']=='127.0.0.1' and count(host['port'],1024) and host['port']<=65535)
                ports.add(host['port'])
                require(all(host[k] is True for k in ('binding_verified','health_live','health_ready','http_parity')))
                require(host['checked_requests']==2 and host['load_benchmark'] is False)
                require(host['response_sha256']==reference['host_reference_sha256'])
                require(number(host['request_seconds_mean']) and number(host['request_seconds_max'],host['request_seconds_mean']))
            require(len(ports)==1)
            for a,b in [('reference','rollback'),('candidate','restart'),('candidate','final')]:
                require(result['host_checks'][a]['response_sha256']==result['host_checks'][b]['response_sha256'])
        require(number(result['duration_seconds'],0,7200))
    except (KeyError,TypeError,AttributeError) as error:
        raise ValueError('Malformed serving report') from error
