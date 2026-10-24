import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from mac24_ui_inventory import verify_stable, verify_services


def rows():
    return {name: dict(container_id=name, image_id='sha256:old', started_at='time',
        restart_count=0, running=True, health='healthy', memory_bytes=512,
        nano_cpus=1000, project='original', service=name)
        for name in ('api', 'postgres', 'ui')}


class UiInventory(unittest.TestCase):
    def test_detects_restart_replacement_and_resource_changes(self):
        before = rows()
        for key, value in [('started_at', 'later'), ('restart_count', 1),
                           ('image_id', 'sha256:new'), ('memory_bytes', 1024)]:
            after = copy.deepcopy(before)
            after['postgres'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify_stable(before, after)
        after = rows(); after.pop('ui')
        with self.assertRaises(ValueError):verify_stable(before, after)
        verify_stable(before, copy.deepcopy(before))

    def test_other_projects_cannot_substitute_missing_original(self):
        data = rows(); data['ui']['project'] = 'restored'
        with self.assertRaises(ValueError):verify_services(data, 'original')

    def test_unhealthy_or_duplicate_service_is_rejected(self):
        data = rows(); data['ui']['health'] = 'starting'
        with self.assertRaises(ValueError):verify_services(data, 'original')
        data = rows(); data['ui-copy'] = dict(data['ui'], container_id='ui-copy')
        with self.assertRaises(ValueError):verify_services(data, 'original')

    def test_returns_exact_original_ids(self):
        self.assertEqual(verify_services(rows(), 'original'),
                         {'api': 'api', 'postgres': 'postgres', 'ui': 'ui'})
