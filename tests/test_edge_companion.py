"""Window-transition regressions without creating a window or moving input."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openassist.edge_companion import EdgeCompanion


class DraftText:
    """Text storage with Tk's disabled widgets ignoring insert/delete."""

    def __init__(self, value=""):
        self.value = value
        self.state = "normal"

    def get(self, *_):
        return self.value

    def configure(self, *, state):
        self.state = state

    def delete(self, *_):
        if self.state == "normal":
            self.value = ""

    def insert(self, _, value):
        if self.state == "normal":
            self.value = value


class EdgeTransitionTests(unittest.TestCase):
    def edge(self, *, phase="executing", mode="workspace"):
        edge = EdgeCompanion.__new__(EdgeCompanion)
        root = Mock()
        root.state.return_value = "normal"
        root.attributes.return_value = False
        root.geometry.return_value = "810x1000+100+80"
        root.minsize.return_value = (450, 650)
        root.winfo_width.return_value = 408
        root.winfo_height.return_value = 180
        root.winfo_x.return_value = 1500
        root.winfo_y.return_value = 220
        root.winfo_pointerxy.return_value = (-10000, -10000)
        session = Mock()
        session.work_area.return_value = (0, 0, 1920, 1080)
        session.is_minimized.return_value = False
        composer = Mock()
        composer.get.return_value = ""
        edge.app = SimpleNamespace(root=root, session=session, scale=1.0, _phase=phase,
                                   composer=composer, main=Mock(), controller=Mock(),
                                   _end_composing=Mock(), _begin_composing=Mock(),
                                   _update_controls=Mock(), _reduced=Mock(get=Mock(return_value=False)))
        edge.root = root
        edge.mode = mode
        edge._animation = None
        edge._keep_for_keyboard = False
        edge._surface_bounds = None
        edge._visible_rect = None
        edge._activity_timer = None
        edge._activity_bars = []
        edge._activity_frame = 0
        edge._workspace = "810x1000+100+80"
        edge._workspace_state = "normal"
        edge._workspace_fullscreen = False
        edge._workspace_minsize = (450, 650)
        edge._interaction_return = False
        edge._started = 0
        for name in ("frame", "panel", "canvas", "composer", "minimize_button"):
            setattr(edge, name, Mock())
        edge.composer.get.return_value = ""
        edge._animate_to = Mock()
        edge.draw = Mock()
        edge.set_ready = Mock()
        return edge

    def test_expanded_minus_minimizes_instead_of_collapsing(self):
        edge = self.edge(mode="expanded")
        edge.canvas.winfo_width.return_value = 370
        edge.collapse = Mock()
        edge._header_click(SimpleNamespace(x=348, y=27))
        edge.app._end_composing.assert_called_once()
        edge.app.session.minimize.assert_called_once()
        edge.collapse.assert_not_called()

    def test_minimized_companion_does_not_react_to_pointer(self):
        edge = self.edge(mode="expanded")
        edge.app.session.is_minimized.return_value = True
        edge.poll()
        edge.root.winfo_pointerxy.assert_not_called()

    def test_working_minimize_hover_yields_input_only_over_button(self):
        edge = self.edge(mode="expanded")
        edge.app.demo = False
        edge.app.session.last_human_pointer = 100
        edge._ready = False
        edge._compose_since = None
        edge._visible_rect = (370, 340, 1500, 150)
        edge.compose_frame = Mock()
        edge.compose_frame.winfo_rootx.return_value = 1518
        edge.compose_frame.winfo_rooty.return_value = 330
        edge.compose_frame.winfo_width.return_value = 330
        edge.root.winfo_pointerxy.return_value = (1525, 177)
        with patch("openassist.edge_companion.time.monotonic", return_value=100):
            edge.poll()
        edge.app._begin_composing.assert_not_called()
        edge.root.winfo_pointerxy.return_value = (1848, 177)
        with patch("openassist.edge_companion.time.monotonic", return_value=100):
            edge.poll()
        edge.minimize_button.state.assert_called_with(["active"])
        edge.app._begin_composing.assert_not_called()
        with patch("openassist.edge_companion.time.monotonic", return_value=100.2):
            edge.poll()
        edge.app._begin_composing.assert_called_once()

    def test_hover_responds_on_first_poll_and_manual_close_requires_reentry(self):
        edge = self.edge(mode="compact")
        edge.app.demo = True
        edge._hover_blocked = False
        edge._visible_rect = (272, 120, 1640, 150)
        edge.root.winfo_pointerxy.return_value = (1700, 180)
        edge.expand = Mock()
        edge.poll()
        edge.expand.assert_called_once()
        edge.expand.reset_mock()
        edge.mode = "expanded"
        edge.collapse()
        edge.poll()
        edge.expand.assert_not_called()
        edge.root.winfo_pointerxy.return_value = (100, 100)
        edge.poll()
        edge.root.winfo_pointerxy.return_value = (1700, 180)
        edge.poll()
        edge.expand.assert_called_once()

    def test_leave_collapses_on_first_poll_but_preserves_unsent_draft(self):
        for draft in ("", "Keep these files"):
            edge = self.edge(mode="expanded")
            edge.app.demo = True
            edge._visible_rect = (370, 340, 1542, 150)
            edge._compose_since = None
            edge._ready = True
            edge.compose_frame = Mock()
            edge.compose_frame.winfo_rootx.return_value = 1560
            edge.compose_frame.winfo_rooty.return_value = 300
            edge.compose_frame.winfo_width.return_value = 330
            edge.composer.get.return_value = draft
            edge.collapse = Mock()
            edge.poll()
            self.assertEqual(edge.collapse.call_count, 0 if draft else 1)

    def test_sent_supplement_is_cleared_before_the_composer_is_disabled(self):
        edge = self.edge(mode="expanded")
        edge._ready = True
        edge.composer = DraftText("  Keep the originals.\n")
        edge.app.controller.mode = "desktop"

        def send(text):
            self.assertEqual(text, "Keep the originals.")
            self.assertEqual(edge.composer.value, "")
            edge.resume_working()
            edge._ready = False
            edge.composer.configure(state="disabled")
            return True

        edge.app._send_text = send
        self.assertEqual(edge.submit(), "break")
        self.assertEqual(edge.composer.value, "")
        self.assertEqual(edge.composer.state, "disabled")
        self.assertEqual(edge.mode, "compact")

    def test_failed_or_raised_send_restores_exact_draft_with_current_input_gate(self):
        for raises in (False, True):
            with self.subTest(raises=raises):
                edge = self.edge(mode="expanded")
                edge._ready = True
                raw = "  Unsaved draft.\n"
                edge.composer = DraftText(raw)

                def send(_):
                    edge._ready = False
                    edge.composer.configure(state="disabled")
                    if raises:
                        raise RuntimeError("Stopped while sending")
                    return False

                edge.app._send_text = send
                if raises:
                    with self.assertRaises(RuntimeError):
                        edge.submit()
                else:
                    self.assertEqual(edge.submit(), "break")
                self.assertEqual(edge.composer.value, raw)
                self.assertEqual(edge.composer.state, "disabled")
                self.assertEqual(edge.mode, "expanded")

    def test_manual_dock_preserves_an_unsent_workspace_draft(self):
        edge = self.edge()
        edge.app.composer = DraftText("Unsent clarification")
        edge.composer = DraftText()
        edge.dock()
        self.assertEqual(edge.app.composer.value, "")
        self.assertEqual(edge.composer.value, "Unsent clarification")

    def test_unanswered_question_cannot_be_dismissed_by_manual_dock(self):
        for phase in ("waiting_user", "waiting_confirmation"):
            with self.subTest(phase=phase):
                edge = self.edge(phase=phase)
                edge.dock()
                self.assertEqual(edge.mode, "workspace")
                edge.root.overrideredirect.assert_not_called()

    def test_worker_resume_docks_while_ui_still_has_waiting_state(self):
        # _input sends the working handshake before its observing state event.
        # The former guard silently left a full-size click-through window here.
        for phase in ("waiting_user", "waiting_confirmation"):
            with self.subTest(phase=phase):
                edge = self.edge(phase=phase, mode="compact")
                edge.open_workspace(interaction=True)
                self.assertEqual(edge.mode, "workspace")
                edge.app.session.passthrough.assert_called_with(False)
                edge.resume_working(dock=False)  # Return even if auto-dock is off.
                self.assertEqual(edge.mode, "compact")
                self.assertFalse(edge._interaction_return)
                edge.set_ready.assert_called_with(False)

    def test_resume_respects_workspace_preference_without_prior_edge(self):
        edge = self.edge()
        edge.resume_working(dock=False)
        self.assertEqual(edge.mode, "workspace")
        edge.root.overrideredirect.assert_not_called()

    def test_resume_does_not_recursively_release_an_edit_lease(self):
        edge = self.edge(mode="expanded")
        edge.resume_working()
        self.assertEqual(edge.mode, "compact")
        edge.app._end_composing.assert_not_called()
        edge.mode = "expanded"
        edge.collapse()
        edge.app._end_composing.assert_called_once()

    def test_maximized_fullscreen_workspace_state_survives_dock_and_return(self):
        edge = self.edge()
        edge.root.state.return_value = "zoomed"
        edge.root.attributes.return_value = True
        edge.dock()
        edge.root.state.assert_called_with("normal")
        edge.root.attributes.assert_called_with("-fullscreen", False)
        edge.open_workspace(interaction=True)
        edge.root.state.assert_called_with("zoomed")
        edge.root.attributes.assert_called_with("-fullscreen", True)
        edge.root.geometry.assert_called_with("810x1000+100+80")

    def test_dock_restores_the_screen_clamped_workspace_minimum(self):
        edge = self.edge()
        edge.app.scale = 2.0
        edge.root.minsize.return_value = (760, 580)
        edge.dock()
        edge.root.minsize.assert_called_with(1, 1)
        edge.open_workspace(interaction=True)
        # Do not restore 900x1300 merely because this display has 200% scaling.
        edge.root.minsize.assert_called_with(760, 580)

    def test_animation_frame_commits_one_complete_native_surface(self):
        edge = self.edge(mode="compact")
        edge._set_rect((408, 180, -430, 220))
        edge.root.geometry.assert_not_called()
        edge.root.update_idletasks.assert_not_called()
        edge.app.session.resize_surface.assert_called_once_with((408, 180, -430, 220), 16)

    def test_new_edge_request_stays_compact_while_intent_is_still_unknown(self):
        edge = self.edge(phase="idle", mode="expanded")
        edge._ready = True
        edge.composer = DraftText("Organize these files")
        edge.app.controller.mode = "conversation"  # Classification has not completed.
        edge.app._send_text = Mock(return_value=True)
        edge.open_workspace = Mock()
        edge.submit()
        self.assertEqual(edge.mode, "compact")
        self.assertEqual(edge.composer.value, "")
        edge.open_workspace.assert_not_called()

    def test_edge_tween_preserves_exact_right_anchor_at_every_frame(self):
        edge = self.edge(mode="expanded")
        edge.app.session.window_rect.return_value = (408, 180, 1510, 220)
        edge._set_rect = Mock()
        edge._prepare_surface = Mock()
        callbacks = []
        edge.root.after.side_effect = lambda delay, callback: callbacks.append(callback) or "timer"
        clock = [10.0]
        with patch("openassist.edge_companion.time.monotonic", side_effect=lambda: clock[0]):
            EdgeCompanion._animate_to(edge, (555, 510, 1365, 220), anchor_right=True)
            for stamp in (10.037, 10.092, 10.153, 10.218, 10.277, 10.321):
                clock[0] = stamp
                callbacks.pop(0)()
        frames = [call.args[0] for call in edge._set_rect.call_args_list]
        self.assertGreater(len({frame[0] for frame in frames}), 5)
        self.assertTrue(all(frame[0] + frame[2] == 1920 for frame in frames))
        self.assertEqual(tuple(frames[-1]), (555, 510, 1365, 220))

    def test_reveal_changes_only_clip_region_not_native_size_or_child_layout(self):
        edge = self.edge(mode="expanded")
        edge._surface_bounds = (370, 340, -378, 150)
        edge._set_rect((310, 220, -318, 150))
        edge.app.session.resize_surface.assert_not_called()
        edge.root.geometry.assert_not_called()
        edge.root.update_idletasks.assert_not_called()
        edge.app.session.round_corners.assert_called_once_with(16, size=(310, 220), offset=(60, 0), redraw=False)
        edge.app.session.repaint_surface.assert_called_once_with(exposed=True)
        self.assertEqual(edge.visible_rect(), (310, 220, -318, 150))

    def test_short_display_shares_top_and_right_between_visible_states(self):
        edge = self.edge(mode="compact")
        edge.app.session.work_area.return_value = (-640, 0, 0, 420)
        compact, expanded = edge._rect(False), edge._rect(True)
        self.assertEqual(compact[3], expanded[3])
        self.assertEqual(compact[2] + compact[0], expanded[2] + expanded[0])
        self.assertGreaterEqual(compact[3], 0)
        self.assertLessEqual(expanded[3] + expanded[1], 420)

    def test_interrupted_reveal_starts_from_visible_region_not_full_surface(self):
        edge = self.edge(mode="compact")
        edge._surface_bounds = (370, 340, 1542, 150)
        edge._visible_rect = (320, 230, 1592, 150)
        edge._set_rect = Mock()
        edge._prepare_surface = Mock()
        with patch("openassist.edge_companion.time.monotonic", return_value=10):
            EdgeCompanion._animate_to(edge, (272, 120, 1640, 150), anchor_right=True)
        self.assertEqual(tuple(edge._set_rect.call_args.args[0]), (320, 230, 1592, 150))
        edge.app.session.window_rect.assert_not_called()

    def test_activity_moves_between_sub_200ms_frames_without_rebuilding_canvas(self):
        edge = self.edge(mode="expanded")
        edge._activity_bars = [(1, 340, 84), (2, 347, 84)]
        with patch("openassist.edge_companion.time.monotonic", return_value=1.0):
            edge._draw_activity()
        first = edge.canvas.coords.call_args_list[-2:]
        with patch("openassist.edge_companion.time.monotonic", return_value=1.016):
            edge._draw_activity()
        self.assertNotEqual(first, edge.canvas.coords.call_args_list[-2:])
        edge.canvas.delete.assert_not_called()
        self.assertEqual(edge._activity_frame, 2)

    def test_reduced_motion_and_workspace_cancel_activity_timer(self):
        edge = self.edge(mode="expanded")
        edge._activity_bars = [(1, 340, 84)]
        edge._activity_timer = "timer-1"
        edge.app._reduced.get.return_value = True
        edge._sync_activity()
        edge.root.after_cancel.assert_called_with("timer-1")
        self.assertIsNone(edge._activity_timer)
        edge._activity_timer = "timer-2"
        edge.app._reduced.get.return_value = False
        edge.mode = "workspace"
        edge._sync_activity()
        edge.root.after_cancel.assert_called_with("timer-2")
        self.assertIsNone(edge._activity_timer)


if __name__ == "__main__":
    unittest.main()
