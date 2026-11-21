import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mirally.framework.desktop_controls import (
    DesktopControlError, prepare_operation, resolve_target, scan_controls, snapshot_record,
)
from mirally.framework.desktop_observation import _control_window
from mirally.platform.window_identity import MIRALLY_WINDOW_PROPERTY, is_mirally_window


AUTO = SimpleNamespace(PropertyId=SimpleNamespace(IsPasswordProperty=1),
                       PatternId=SimpleNamespace(InvokePattern=2, ValuePattern=3))
BOUNDS = {'left': -100, 'top': 0, 'right': 200, 'bottom': 100}


class Control:
    ProcessId = 42
    NativeWindowHandle = 0
    AutomationId = 'test'
    ClassName = 'fixture'
    ControlType = 50000
    BoundingRectangle = SimpleNamespace(left=-500, top=10, right=15, bottom=25)
    IsEnabled = True
    IsOffscreen = False

    def __init__(self, identity, *, name='', role='ButtonControl', password=False, property_supported=True):
        self.identity = identity
        self.name = name
        self.ControlTypeName = role
        self.IsPassword = password
        self.property_supported = property_supported
        self.patterns = {}
        self.parent = None
        self.children = []
        self.visited = 0

    @property
    def Name(self):
        self.visited += 1
        return self.name

    def append(self, *children):
        for control in children:
            control.parent = self
        self.children.extend(children)

    def GetRuntimeId(self):
        return (42, self.identity)

    def GetFirstChildControl(self):
        return self.children[0] if self.children else None

    def GetNextSiblingControl(self):
        siblings = self.parent.children
        next_index = siblings.index(self) + 1
        return siblings[next_index] if next_index < len(siblings) else None

    def GetParentControl(self):
        return self.parent

    def GetPropertyValueEx(self, _property, _ignore_default):
        return self.IsPassword if self.property_supported else None

    def GetPattern(self, pattern_id):
        return self.patterns.get(pattern_id)


class DesktopControlsTests(unittest.TestCase):
    def test_mirally_and_owned_dialogs_are_outside_semantic_automation(self):
        user = MagicMock()
        user.GetPropW.side_effect = lambda hwnd, name: bool(hwnd == 12 and name == MIRALLY_WINDOW_PROPERTY)
        user.GetAncestor.side_effect = lambda hwnd, relationship: 12 if hwnd == 13 and relationship == 3 else hwnd
        self.assertTrue(is_mirally_window(user, 12))
        self.assertTrue(is_mirally_window(user, 13))
        self.assertFalse(is_mirally_window(user, 14))
        with patch('mirally.framework.desktop_observation.window_record') as read:
            with self.assertRaises(DesktopControlError):
                _control_window(user, 12, 20)
            with self.assertRaises(DesktopControlError):
                _control_window(user, 13, 20)
            read.assert_not_called()

    def test_password_subtrees_are_masked_without_reading_names_or_values(self):
        root = Control(0)
        ordinary = Control(1, name='操作')
        password = Control(2, name='proof secret label never read', password=True)
        child = Control(3, name='proof secret descendant never read')
        ambiguous = Control(4, name='unknown password', role='EditControl', property_supported=False)
        password.append(child)
        ordinary.patterns[2] = SimpleNamespace(Invoke=lambda **_: True)
        root.append(ordinary, password, ambiguous)

        scan = scan_controls(root, AUTO, BOUNDS)
        snapshot = snapshot_record({'bounds': BOUNDS}, scan)
        self.assertEqual([(t['id'], t['name'], t['actions']) for t in snapshot['targets']], [(1, '操作', ['invoke'])])
        self.assertEqual(snapshot['redactions'], [{'left': -100, 'top': 10, 'right': 15, 'bottom': 25}] * 2)
        self.assertTrue(snapshot['image_safe'])
        self.assertEqual((password.visited, child.visited, ambiguous.visited), (0, 0, 0))

    def test_sibling_traversal_stops_at_visit_budget(self):
        root = Control(0)
        children = [Control(index, name=str(index)) for index in range(1, 21)]
        root.append(*children)
        with patch('mirally.framework.desktop_controls.MAX_NODES', 4):
            scan = scan_controls(root, AUTO, BOUNDS)
        self.assertEqual(len(scan.targets), 4)
        self.assertEqual(sum(control.visited for control in children), 4)
        self.assertTrue(scan.truncated)
        self.assertFalse(scan.image_safe)

    def test_value_writes_are_limited_to_writable_editable_text_roles(self):
        root = Control(0)
        caption = Control(1, name='Caption', role='TitleBarControl')
        close = Control(2, name='Close')
        button = Control(3, name='Button')
        edit = Control(4, name='Search', role='EditControl')
        readonly = Control(5, name='Read only', role='EditControl')
        for control in (caption, button, edit):
            control.patterns[3] = SimpleNamespace(IsReadOnly=False)
        close.patterns[2] = SimpleNamespace(Invoke=lambda **_: True)
        readonly.patterns[3] = SimpleNamespace(IsReadOnly=True)
        caption.append(close)
        root.append(caption, button, edit, readonly)
        result = scan_controls(root, AUTO, BOUNDS)
        self.assertEqual([(target.record['name'], target.record['actions']) for target in result.targets],
                         [('Button', []), ('Search', ['set_value']), ('Read only', [])])
        self.assertEqual(close.visited, 0)

    def test_disjoint_or_duplicate_controls_never_resolve(self):
        root = Control(0)
        first, duplicate = Control(1, name='one'), Control(1, name='two')
        first.patterns[2] = duplicate.patterns[2] = SimpleNamespace(Invoke=lambda **_: True)
        root.append(first)
        before = scan_controls(root, AUTO, BOUNDS).targets[0].record
        root.append(duplicate)

        scan = scan_controls(root, AUTO, BOUNDS)
        self.assertEqual(scan.targets, [])
        with self.assertRaises(DesktopControlError):
            resolve_target(scan, before, 'invoke')

    def test_stale_names_and_reparenting_reject_before_dispatch(self):
        root, branch = Control(0), Control(5, role='PaneControl')
        button = Control(1, name='打开')
        button.patterns[2] = SimpleNamespace(Invoke=lambda **_: self.fail('must not invoke'))
        root.append(button, branch)
        target = scan_controls(root, AUTO, BOUNDS).targets[0]
        button.name = '关闭'
        with self.assertRaises(DesktopControlError):
            resolve_target(scan_controls(root, AUTO, BOUNDS), target.record, 'invoke')
        button.name = '打开'
        root.children.remove(button)
        branch.append(button)
        with self.assertRaises(DesktopControlError):
            prepare_operation(AUTO, target, 'invoke', None, BOUNDS)


if __name__ == '__main__':
    unittest.main()
