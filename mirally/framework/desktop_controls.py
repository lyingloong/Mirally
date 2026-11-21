"""Bounded, task-scoped Windows UI Automation observations and actions."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, deque
from dataclasses import dataclass


MAX_NODES = 500
MAX_TARGETS = 80
MAX_DEPTH = 20
SCAN_SECONDS = 3.0
WRITABLE_ROLES = {'EditControl', 'ComboBoxControl'}
LABELLED_ROLES = {
    'ButtonControl', 'CheckBoxControl', 'ComboBoxControl', 'EditControl', 'HyperlinkControl',
    'ListItemControl', 'MenuItemControl', 'RadioButtonControl', 'SliderControl', 'SpinnerControl',
    'TabItemControl', 'TreeItemControl',
}


class DesktopControlError(RuntimeError):
    """A user-facing rejection with no action issued to the provider."""


@dataclass
class Target:
    control: object
    record: dict
    lineage: tuple


@dataclass
class ControlScan:
    targets: list[Target]
    redactions: list[dict]
    truncated: bool
    image_safe: bool


def _metadata(control, name, default=None):
    try:
        return getattr(control, name)
    except Exception:
        # Providers can destroy or detach controls while their tree is read.
        return default


def _flag(value):
    return bool(value) if type(value) in (bool, int) and value in (0, 1) else None


def _protected(control, auto):
    try:
        return _flag(control.GetPropertyValueEx(auto.PropertyId.IsPasswordProperty, True))
    except Exception:
        return None


def _runtime_id(control):
    try:
        value = tuple(control.GetRuntimeId() or ())
        if 0 < len(value) <= 64 and all(type(part) is int for part in value):
            return value
    except Exception:
        pass
    return None


def _bounds(control, window_bounds):
    rect = _metadata(control, 'BoundingRectangle')
    if rect is None:
        return None
    bounds = {
        'left': max(rect.left, window_bounds['left']),
        'top': max(rect.top, window_bounds['top']),
        'right': min(rect.right, window_bounds['right']),
        'bottom': min(rect.bottom, window_bounds['bottom']),
    }
    if bounds['left'] < bounds['right'] and bounds['top'] < bounds['bottom']:
        return bounds
    return None


def _patterns(control, pattern_ids, *, writable):
    patterns = {}
    invoke = control.GetPattern(pattern_ids.InvokePattern)
    if invoke is not None:
        patterns['invoke'] = invoke
    if writable:
        value = control.GetPattern(pattern_ids.ValuePattern)
        if value is not None and _flag(_metadata(value, 'IsReadOnly')) is False:
            patterns['set_value'] = value
    return patterns


def _identity(control, name, runtime_id, lineage):
    process = int(control.ProcessId)
    control_type = int(control.ControlType)
    if process <= 0 or control_type <= 0:
        raise ValueError('incomplete UIA identity')
    fields = [process, control_type, int(control.NativeWindowHandle or 0),
              control.AutomationId, control.ClassName, name, lineage]
    fingerprint = hashlib.sha256(json.dumps(fields, ensure_ascii=True).encode('utf-8')).hexdigest()
    return {'runtime_id': list(runtime_id), 'fingerprint': fingerprint}


def scan_controls(root, auto, window_bounds, *, deadline=None):
    """Collect a single window's labels and patterns without reading any control value."""
    if deadline is None:
        deadline = time.monotonic() + SCAN_SECONDS
    root_id = _runtime_id(root)
    if root_id is None or _flag(_metadata(root, 'IsOffscreen')) is not False:
        raise DesktopControlError('无法确认目标窗口的辅助功能树。请重新观察窗口。')

    counts = Counter({root_id: 1})
    candidates = []
    redactions = []
    complete = True
    image_safe = True
    # Keep one cursor per visited sibling chain. The budget also stops traversal
    # when a provider has thousands of siblings, without materializing a subtree.
    pending = deque([(root.GetFirstChildControl, (root_id,))])
    nodes = 0
    while pending:
        if nodes >= MAX_NODES or time.monotonic() >= deadline:
            complete = False
            break
        next_node, lineage = pending.popleft()
        try:
            control = next_node()
            if control is None:
                continue
            nodes += 1
            pending.append((control.GetNextSiblingControl, lineage))
            runtime_id = _runtime_id(control)
            if runtime_id is not None:
                counts[runtime_id] += 1
                if runtime_id in lineage:
                    complete = False
                    continue
            offscreen = _flag(_metadata(control, 'IsOffscreen'))
            if offscreen is True:
                continue
            if offscreen is None:
                image_safe = False
                continue
            kind = _metadata(control, 'ControlTypeName', '')
            if kind == 'TitleBarControl':
                continue
            if _flag(_metadata(control, 'IsPassword')) is not False:
                rect = _bounds(control, window_bounds)
                if rect is None:
                    image_safe = False
                else:
                    redactions.append(rect)
                continue
            writable = kind in WRITABLE_ROLES
            privacy = _protected(control, auto) if writable else False
            if privacy is not False:
                rect = _bounds(control, window_bounds)
                if rect is None:
                    image_safe = False
                else:
                    redactions.append(rect)
                continue
            if len(lineage) >= MAX_DEPTH:
                if control.GetFirstChildControl() is not None:
                    complete = False
            else:
                pending.append((control.GetFirstChildControl, (*lineage, runtime_id)))

            if _flag(_metadata(control, 'IsEnabled')) is not True:
                continue
            bounds = _bounds(control, window_bounds)
            if bounds is None:
                continue
            if runtime_id is None or None in lineage:
                continue
            try:
                patterns = _patterns(control, auto.PatternId, writable=writable)
                if not patterns and kind not in LABELLED_ROLES:
                    continue
                name = control.Name
                if not isinstance(name, str) or not isinstance(kind, str) or (not name.strip() and not patterns):
                    continue
                locator = _identity(control, name, runtime_id, lineage)
            except Exception:
                # An unavailable action pattern or label does not hide descendants.
                continue
            record = {'name': name.strip()[:160], 'type': kind[:80], 'bounds': bounds,
                      'actions': list(patterns), 'identity': locator}
            candidates.append(Target(control, record, lineage))
        except Exception:
            # A disappearing sub-tree is an incomplete observation, not permission
            # to claim we inspected its contents or safely masked its pixels.
            complete = False

    visible = [target for target in candidates if counts[tuple(target.record['identity']['runtime_id'])] == 1]
    return ControlScan(visible, redactions, not complete or len(visible) > MAX_TARGETS,
                       complete and image_safe)


def snapshot_record(window, scan):
    targets = [dict(target.record, id=index)
               for index, target in enumerate(scan.targets[:MAX_TARGETS], 1)]
    return {'window': window, 'targets': targets, 'truncated': scan.truncated,
            'redactions': scan.redactions, 'image_safe': scan.image_safe, 'untrusted_content': True}


def resolve_target(scan, expected, operation):
    locator = expected.get('identity') if isinstance(expected, dict) else None
    if not isinstance(locator, dict):
        raise DesktopControlError('控件编号已失效。请重新观察窗口。')
    for target in scan.targets:
        if target.record['identity'] == locator and operation in target.record['actions']:
            return target
    raise DesktopControlError('控件已变化、不可用或匹配不唯一。请重新观察窗口。')


def prepare_operation(auto, target, operation, text, window_bounds):
    """Refresh the resolved control's exact identity and capability before dispatch."""
    control = target.control
    try:
        ancestor = control
        for expected_id in reversed(target.lineage):
            ancestor = ancestor.GetParentControl()
            if (_runtime_id(ancestor) != expected_id or _flag(ancestor.IsPassword) is not False
                    or _flag(ancestor.IsOffscreen) is not False):
                raise DesktopControlError('控件已离开原来的位置。请重新观察窗口。')
        locator = _identity(control, control.Name, _runtime_id(control), target.lineage)
        writable = control.ControlTypeName in WRITABLE_ROLES
        allowed = _flag(control.IsPassword) is False and (not writable or _protected(control, auto) is False)
        if (locator == target.record['identity'] and allowed and _flag(control.IsOffscreen) is False
                and _flag(control.IsEnabled) is True and _bounds(control, window_bounds) is not None):
            pattern = _patterns(control, auto.PatternId, writable=writable).get(operation)
            if pattern is not None:
                return pattern.Invoke if operation == 'invoke' else lambda **kw: pattern.SetValue(text, **kw)
    except DesktopControlError:
        raise
    except Exception as error:
        raise DesktopControlError('无法再次确认控件。请重新观察窗口。') from error
    raise DesktopControlError('控件已变化或不支持这个动作。请重新观察窗口。')


def read_verified_value(auto, target, window_bounds):
    """Return fresh ValuePattern text to the caller for comparison inside the worker."""
    control = target.control
    if (_flag(control.IsPassword) is False and _protected(control, auto) is False
            and _flag(control.IsOffscreen) is False and _bounds(control, window_bounds) is not None):
        pattern = control.GetPattern(auto.PatternId.ValuePattern)
        if pattern is not None:
            return pattern.Value
    return None
