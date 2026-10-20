"""Bounded CPU training with durable model/optimizer/RNG/cursor checkpoints."""
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import time

import numpy as np
import torch
from torch.nn import functional as F

from hm_recommender.contracts import TrainingResult
from hm_recommender.data.io import atomic_json, locked, sha256
from hm_recommender.model.inputs import Inputs, TrainConfig, ids, lookup, numeric
from hm_recommender.model.two_tower import TwoTower


def telemetry():
    report = {'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1024 if os.name == 'posix' and __import__('sys').platform != 'darwin' else 1)}
    for name in ['memory.current', 'memory.peak', 'memory.swap.current', 'memory.swap.peak']:
        path = Path('/sys/fs/cgroup') / name
        if path.exists():
            try:
                report[name] = int(path.read_text())
            except ValueError:
                pass
    for name in ['memory.events', 'memory.stat']:
        path = Path('/sys/fs/cgroup') / name
        if path.exists():
            report[name] = {key: int(value) for key, value in (line.split() for line in path.read_text().splitlines())}
    return report


def contains(sorted_values, queries):
    positions = np.searchsorted(sorted_values, queries)
    return (positions < len(sorted_values)) & (sorted_values[np.minimum(positions, len(sorted_values) - 1)] == queries)


def sample_negatives(rng, users, candidates, positives, n_items, count):
    if not len(candidates):
        raise ValueError('No negative candidates available before this week')
    result = rng.choice(candidates, (len(users), count))
    for _ in range(32):
        invalid = contains(positives, users[:, None] * n_items + result)
        if not invalid.any():
            return result
        result[invalid] = rng.choice(candidates, invalid.sum())
    # Rare saturated baskets: resolve exactly, never train against a true positive.
    for row in np.flatnonzero(invalid.any(axis=1)):
        available = candidates[~contains(positives, users[row] * n_items + candidates)]
        if not len(available):
            raise ValueError('A weekly basket contains the entire available catalog; no valid negative')
        result[row] = rng.choice(available, count)
    return result


def checkpoint(directory, payload):
    name = 'checkpoint-' + str(payload['step']) + '-' + str(time.time_ns()) + '.pt'
    path = directory / name
    partial = path.with_suffix('.partial')
    with partial.open('wb') as stream:
        torch.save(payload, stream)
        stream.flush(); os.fsync(stream.fileno())
    partial.replace(path)
    atomic_json(directory / 'latest.json', {'path': name, 'sha256': sha256(path), 'step': payload['step'],
        'rows_consumed': payload['rows'], 'cursor': payload['cursor'], 'duration_seconds': payload['seconds']})
    generations = sorted(directory.glob('checkpoint-*.pt'), key=lambda p: int(p.stem.split('-')[-1]), reverse=True)
    for old in generations[2:]:
        old.unlink()
    return path


def load_checkpoint(directory):
    from hm_recommender.data.io import safe_file
    pointer = json.loads((directory / 'latest.json').read_text())
    path = safe_file(directory, pointer['path'])
    if sha256(path) != pointer['sha256']:
        raise ValueError('Checkpoint checksum mismatch')
    return torch.load(path, weights_only=True, map_location='cpu')


def train(features, config, output_dir, *, resume=False, stop_after_steps=None):
    settings = TrainConfig.model_validate(config)
    output_dir = Path(output_dir)
    with locked(output_dir):
        inputs = Inputs(features, settings)
        return train_inputs(inputs, output_dir, resume=resume, stop_after_steps=stop_after_steps)


def train_inputs(inputs, output_dir, *, resume=False, stop_after_steps=None):
    config = inputs.config
    if shutil.disk_usage(output_dir).free < config.min_free_bytes:
        raise ValueError('Insufficient free disk for checkpoints')
    identity_file = output_dir / 'inputs.json'
    if identity_file.exists() and json.loads(identity_file.read_text()) != inputs.identity:
        raise ValueError('Inputs or training configuration changed; use a new experiment')
    if (output_dir / 'latest.json').exists() and not resume:
        raise ValueError('Checkpoint exists; explicitly resume or choose a new experiment')
    atomic_json(identity_file, inputs.identity)
    torch.manual_seed(config.seed)
    torch.use_deterministic_algorithms(True)
    rng = np.random.default_rng(config.seed)
    model = TwoTower(len(inputs.users) + 1, inputs.item_sizes)
    optimizer = torch.optim.Adagrad(model.parameters(), lr=config.learning_rate, foreach=False, fused=False)
    cursor = [0, 0, 0]
    step = rows = 0
    elapsed = 0.0
    loss_sum = 0.0
    if resume:
        state = load_checkpoint(output_dir)
        if state['identity'] != inputs.identity:
            raise ValueError('Checkpoint identity mismatch')
        model.load_state_dict(state['model'])
        optimizer.load_state_dict(state['optimizer'])
        torch.set_rng_state(state['torch_rng'])
        rng.bit_generator.state = json.loads(state['numpy_rng'])
        cursor, step, rows, elapsed, loss_sum = state['cursor'], state['step'], state['rows'], state['seconds'], state['loss_sum']
    interrupted = [False]
    def stop(*_):
        interrupted[0] = True
    handlers = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    started = time.monotonic()
    initial_step = step
    def persist():
        seconds = elapsed + time.monotonic() - started
        path = checkpoint(output_dir, {'identity': inputs.identity, 'settings': model.settings,
            'model': model.state_dict(), 'optimizer': optimizer.state_dict(), 'torch_rng': torch.get_rng_state(),
            'numpy_rng': json.dumps(rng.bit_generator.state), 'cursor': cursor, 'step': step,
            'rows': rows, 'seconds': seconds, 'loss_sum': loss_sum})
        atomic_json(output_dir / 'progress.json', {'status': 'interrupted' if interrupted[0] else 'running',
            'cursor': cursor, 'step': step, 'rows_consumed': rows, 'duration_seconds': seconds,
            'checkpoint': path.name, 'resources': telemetry()})
        return path
    model.train()
    if not resume:
        persist()
    try:
        for epoch in range(cursor[0], config.epochs):
            first_week = cursor[1] if epoch == cursor[0] else 0
            for week_index in range(first_week, len(inputs.train_files)):
                frame = inputs.week(inputs.train_files[week_index])
                if not len(frame):
                    cursor = [epoch, week_index + 1, 0]
                    continue
                week = frame['week_start'][0]
                users = lookup(inputs.users, ids(frame['customer_id']))
                items = np.asarray([inputs.article_map[v] for v in frame['article_id']], dtype=np.int64)
                history = numeric(frame)
                positive_pairs = np.unique(users * len(inputs.article_ids) + items)
                candidates = np.flatnonzero(inputs.first_seen < np.datetime64(week))
                order = np.random.default_rng(np.random.SeedSequence([config.seed, epoch, week_index])).permutation(len(frame))
                begin = cursor[2] if cursor[:2] == [epoch, week_index] else 0
                for offset in range(begin, len(order), config.batch_size):
                    if interrupted[0]:
                        break
                    batch = order[offset:offset + config.batch_size]
                    neg = sample_negatives(rng, users[batch], candidates, positive_pairs, len(inputs.article_ids), config.negatives)
                    candidate_items = np.concatenate([items[batch, None], neg], axis=1)
                    user_vectors = model.encode_user(torch.from_numpy(users[batch]), torch.from_numpy(history[batch]))
                    item_vectors = model.encode_item(torch.from_numpy(inputs.attributes[candidate_items]))
                    scores = (user_vectors[:, None, :] * item_vectors).sum(-1) / config.temperature
                    loss = F.cross_entropy(scores, torch.zeros(len(batch), dtype=torch.long))
                    if not torch.isfinite(loss):
                        raise ValueError('Non-finite loss')
                    optimizer.zero_grad(set_to_none=True)
                    loss.backward(); optimizer.step()
                    rows += len(batch); step += 1; loss_sum += loss.item() * len(batch)
                    cursor = [epoch, week_index, offset + len(batch)]
                    if step % config.checkpoint_steps == 0:
                        if shutil.disk_usage(output_dir).free < config.min_free_bytes:
                            raise ValueError('Disk reserve exhausted')
                        persist()
                    if step % 10 == 0:
                        atomic_json(output_dir / 'progress.json', {'status': 'running', 'cursor': cursor,
                            'step': step, 'rows_consumed': rows, 'duration_seconds': elapsed + time.monotonic() - started,
                            'resources': telemetry()})
                    if stop_after_steps is not None and step - initial_step >= stop_after_steps:
                        interrupted[0] = True
                if interrupted[0]:
                    break
                cursor = [epoch, week_index + 1, 0]
            if interrupted[0]:
                break
            cursor = [epoch + 1, 0, 0]
        path = persist()
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    complete = cursor[0] == config.epochs
    result = TrainingResult(run_id=output_dir.name, checkpoint_path=path.name,
        config=config.model_dump(), seed=config.seed, epochs=cursor[0], rows_consumed=rows,
        duration_seconds=elapsed + time.monotonic() - started, peak_memory_bytes=telemetry()['peak_rss_bytes'])
    payload = result.model_dump(mode='json')
    payload.update(status='passed' if complete else 'interrupted', step=step,
                   mean_training_loss=loss_sum / rows if rows else None,
                   examples_per_second=rows / max(result.duration_seconds, .001), resources=telemetry(),
                   full_dataset=not config.max_users, eligible_train_labels=inputs.manifest['counts']['eligible_train_labels'],
                   users=len(inputs.users), input_identity=inputs.identity,
                   disk_bytes=sum(p.stat().st_size for p in output_dir.glob('*') if p.is_file()))
    if complete and not config.max_users and rows != inputs.manifest['counts']['eligible_train_labels'] * config.epochs:
        raise ValueError('Full epoch row reconciliation failed')
    atomic_json(output_dir / 'training.json', payload)
    atomic_json(output_dir / 'progress.json', {'status': payload['status'], 'step': step, 'rows_consumed': rows,
                                               'duration_seconds': result.duration_seconds, 'resources': telemetry()})
    return result
